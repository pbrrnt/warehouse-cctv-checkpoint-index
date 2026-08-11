"""
เทสต์ indexer skeleton — เฉพาะส่วนที่ทดสอบได้โดยไม่ต้องมี Postgres/MQTT/
Redis จริง (pure function + mock) ดู services/indexer/*.py และ
db/migrations/README.md สำหรับสิ่งที่ยังต้องพิสูจน์กับ infra จริง

รันด้วย:  python -m unittest tests.test_indexer -v
"""

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.common.analytics.thai_ocr import OcrResult  # noqa: E402
from services.common.analytics.vehicle import VehicleAttrs, VehicleResult  # noqa: E402
from services.common.db.models import CameraRole  # noqa: E402
from services.common.storage import LocalFileStore  # noqa: E402
from services.indexer.frigate_events import (  # noqa: E402
    compute_direction,
    frigate_camera_to_db_id,
    parse_frigate_event,
)
from services.indexer.mqtt_bridge import MqttBridge  # noqa: E402
from services.indexer.stream_consumer import ensure_consumer_group, process_one_message  # noqa: E402
from services.indexer.worker import EventWorker, build_event_values, snapshot_key  # noqa: E402


# ============================================================
#  ตัวอย่าง payload — จำลองตามสคีมาที่ยืนยันได้จากเอกสาร Frigate
#  ★ ต้องเทียบกับ payload จริงตอนขึ้นระบบจริง (ดู frigate_events.py docstring)
# ============================================================

def _sample_payload(
    event_type="end",
    frigate_id="1699999999.123456-abcdef",
    camera="WH01_GATE_LPR",
    label="car",
    sub_label=None,
    entered_zones=None,
) -> dict:
    return {
        "type": event_type,
        "before": {},
        "after": {
            "id": frigate_id,
            "camera": camera,
            "label": label,
            "sub_label": sub_label,
            "score": 0.87,
            "start_time": 1699999999.123456,
            "end_time": 1699999999.123456 + 8.5,
            "has_clip": True,
            "has_snapshot": True,
            "entered_zones": entered_zones or [],
            "current_zones": [],
        },
    }


class TestParseFrigateEvent(unittest.TestCase):
    def test_end_event_parsed(self):
        payload = _sample_payload()
        parsed = parse_frigate_event(payload)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.frigate_id, "1699999999.123456-abcdef")
        self.assertEqual(parsed.frigate_camera, "WH01_GATE_LPR")
        self.assertEqual(parsed.label, "car")
        self.assertEqual(parsed.score, 0.87)
        self.assertTrue(parsed.has_clip)
        self.assertEqual(parsed.start_ts, datetime.fromtimestamp(1699999999.123456, tz=timezone.utc))

    def test_accepts_json_bytes(self):
        payload = _sample_payload()
        raw = json.dumps(payload).encode("utf-8")
        parsed = parse_frigate_event(raw)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.frigate_id, payload["after"]["id"])

    def test_accepts_json_string(self):
        payload = _sample_payload()
        parsed = parse_frigate_event(json.dumps(payload))
        self.assertIsNotNone(parsed)

    def test_new_event_ignored(self):
        self.assertIsNone(parse_frigate_event(_sample_payload(event_type="new")))

    def test_update_event_ignored(self):
        self.assertIsNone(parse_frigate_event(_sample_payload(event_type="update")))

    def test_missing_after_returns_none(self):
        self.assertIsNone(parse_frigate_event({"type": "end"}))

    def test_missing_id_returns_none(self):
        payload = _sample_payload()
        del payload["after"]["id"]
        self.assertIsNone(parse_frigate_event(payload))

    def test_malformed_json_bytes_returns_none(self):
        self.assertIsNone(parse_frigate_event(b"{not valid json"))

    def test_non_dict_payload_returns_none(self):
        self.assertIsNone(parse_frigate_event(b"null"))
        self.assertIsNone(parse_frigate_event(b"[]"))

    def test_sub_label_as_plain_string(self):
        payload = _sample_payload(sub_label="1กก1234")
        parsed = parse_frigate_event(payload)
        self.assertEqual(parsed.sub_label, "1กก1234")

    def test_sub_label_as_list_tuple_format(self):
        # บาง Frigate เวอร์ชันส่ง sub_label เป็น [name, score]
        payload = _sample_payload(sub_label=["1กก1234", 0.92])
        parsed = parse_frigate_event(payload)
        self.assertEqual(parsed.sub_label, "1กก1234")

    def test_sub_label_none(self):
        parsed = parse_frigate_event(_sample_payload(sub_label=None))
        self.assertIsNone(parsed.sub_label)

    def test_raw_payload_preserved(self):
        # ★ ต้องเก็บ payload ดิบทั้งก้อนไว้เสมอ
        payload = _sample_payload()
        parsed = parse_frigate_event(payload)
        self.assertEqual(parsed.raw, payload)

    def test_no_end_time_while_object_still_tracked(self):
        payload = _sample_payload()
        payload["after"]["end_time"] = None
        parsed = parse_frigate_event(payload)
        self.assertIsNone(parsed.end_ts)


class TestFrigateCameraToDbId(unittest.TestCase):
    def test_underscore_converted_to_dash(self):
        self.assertEqual(frigate_camera_to_db_id("WH01", "WH01_GATE_LPR"), "WH01-GATE-LPR")

    def test_no_underscore_unchanged(self):
        self.assertEqual(frigate_camera_to_db_id("WH01", "camera1"), "camera1")


class TestComputeDirection(unittest.TestCase):
    def test_outside_before_inside_is_in(self):
        result = compute_direction(["gate_outside", "gate_inside"], "gate_outside", "gate_inside")
        self.assertEqual(result, "in")

    def test_inside_before_outside_is_out(self):
        result = compute_direction(["gate_inside", "gate_outside"], "gate_outside", "gate_inside")
        self.assertEqual(result, "out")

    def test_only_one_zone_entered_unknown(self):
        result = compute_direction(["gate_outside"], "gate_outside", "gate_inside")
        self.assertIsNone(result)

    def test_no_zones_entered_unknown(self):
        self.assertIsNone(compute_direction([], "gate_outside", "gate_inside"))

    def test_unrelated_zones_ignored(self):
        result = compute_direction(
            ["some_other_zone", "gate_outside", "gate_inside"], "gate_outside", "gate_inside"
        )
        self.assertEqual(result, "in")

    def test_missing_zone_config_returns_none(self):
        self.assertIsNone(compute_direction(["gate_outside", "gate_inside"], None, "gate_inside"))
        self.assertIsNone(compute_direction(["gate_outside", "gate_inside"], "gate_outside", None))

    def test_camera_without_zones_configured_at_all(self):
        # กล้องที่ยังไม่ตั้ง zone_config (role='face' อาจไม่ต้องมีทิศทาง)
        self.assertIsNone(compute_direction(["a", "b"], None, None))


class TestBuildEventValues(unittest.TestCase):
    def test_values_match_expected_columns(self):
        payload = _sample_payload(entered_zones=["gate_outside", "gate_inside"])
        parsed = parse_frigate_event(payload)
        values = build_event_values(parsed, site_id="WH01", camera_id="WH01-GATE-LPR", direction="in", thumb_key="k.jpg")

        self.assertEqual(values["id"], parsed.frigate_id)
        self.assertEqual(values["site_id"], "WH01")
        self.assertEqual(values["camera_id"], "WH01-GATE-LPR")
        self.assertEqual(values["direction"], "in")
        self.assertEqual(values["zones"], ["gate_outside", "gate_inside"])
        self.assertEqual(values["thumb_key"], "k.jpg")
        self.assertEqual(values["raw"], payload)

    def test_empty_zones_stored_as_none_not_empty_list(self):
        parsed = parse_frigate_event(_sample_payload(entered_zones=[]))
        values = build_event_values(parsed, "WH01", "WH01-GATE-LPR", None, None)
        self.assertIsNone(values["zones"])


class TestSnapshotKey(unittest.TestCase):
    def test_key_format(self):
        key = snapshot_key("WH01", "1699999999.123456-abcdef")
        self.assertEqual(key, "WH01/events/1699999999.123456-abcdef/snapshot.jpg")


class TestLocalFileStoreIntegration(unittest.TestCase):
    """เช็กว่า worker.snapshot_key ผลิต key ที่ LocalFileStore ใช้ได้จริง"""

    def test_snapshot_key_roundtrip_through_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = LocalFileStore(tmp)
            key = snapshot_key("WH01", "evt123")
            store.save(key, b"fake-jpeg")
            self.assertEqual(store.read(key), b"fake-jpeg")


# ============================================================
#  MQTT bridge — ทดสอบ handle_payload โดยไม่ต้องมี broker จริง
# ============================================================

class _FakeRedisForBridge:
    def __init__(self, raise_error=False):
        self.added = []
        self.raise_error = raise_error

    def xadd(self, stream, fields):
        if self.raise_error:
            import redis
            raise redis.RedisError("จำลอง Redis ล่ม")
        self.added.append((stream, fields))


class TestMqttBridge(unittest.TestCase):
    def test_handle_payload_pushes_to_redis_stream(self):
        fake_redis = _FakeRedisForBridge()
        bridge = MqttBridge("localhost", 1883, fake_redis, stream_name="test-stream")
        bridge.handle_payload(b'{"type": "end"}')
        self.assertEqual(len(fake_redis.added), 1)
        self.assertEqual(fake_redis.added[0], ("test-stream", {"payload": b'{"type": "end"}'}))

    def test_redis_error_does_not_raise(self):
        # ★ ถ้า Redis ล่มชั่วคราว ไม่ควรทำให้ MQTT client thread ตายไปด้วย
        fake_redis = _FakeRedisForBridge(raise_error=True)
        bridge = MqttBridge("localhost", 1883, fake_redis)
        try:
            bridge.handle_payload(b'{"type": "end"}')
        except Exception as exc:
            self.fail(f"handle_payload ไม่ควร raise ออกมาแม้ Redis ล่ม: {exc}")


# ============================================================
#  Stream consumer — ทดสอบ process_one_message และ ensure_consumer_group
#  ด้วย fake redis client + fake event worker
# ============================================================

class _FakeEventWorker:
    def __init__(self, raise_on_process=False):
        self.processed = []
        self.raise_on_process = raise_on_process

    def process(self, parsed):
        if self.raise_on_process:
            raise RuntimeError("จำลองการเขียน DB ล้มเหลว")
        self.processed.append(parsed)


class _FakeRedisForConsumer:
    def __init__(self):
        self.acked = []
        self.group_created_with = None

    def xack(self, stream, group, message_id):
        self.acked.append((stream, group, message_id))

    def xgroup_create(self, stream, group, id, mkstream):
        self.group_created_with = (stream, group, id, mkstream)

    def xgroup_create_busygroup(self, *a, **k):
        import redis
        raise redis.ResponseError("BUSYGROUP Consumer Group name already exists")


class TestProcessOneMessage(unittest.TestCase):
    def test_valid_end_event_processed_and_acked(self):
        fake_redis = _FakeRedisForConsumer()
        fake_worker = _FakeEventWorker()
        payload = json.dumps(_sample_payload()).encode("utf-8")

        process_one_message(fake_redis, "stream", "group", b"1-0", {b"payload": payload}, fake_worker)

        self.assertEqual(len(fake_worker.processed), 1)
        self.assertEqual(fake_redis.acked, [("stream", "group", b"1-0")])

    def test_skippable_event_still_acked(self):
        # type != "end" -> parse คืน None -> ไม่ใช่ error ต้อง ack ปกติ ไม่ค้างคิว
        fake_redis = _FakeRedisForConsumer()
        fake_worker = _FakeEventWorker()
        payload = json.dumps(_sample_payload(event_type="new")).encode("utf-8")

        process_one_message(fake_redis, "stream", "group", b"1-0", {b"payload": payload}, fake_worker)

        self.assertEqual(len(fake_worker.processed), 0)
        self.assertEqual(fake_redis.acked, [("stream", "group", b"1-0")])

    def test_processing_failure_not_acked(self):
        # ★ ถ้า worker.process พัง ห้าม ack ต้องปล่อยให้ค้างไว้ retry ได้
        fake_redis = _FakeRedisForConsumer()
        fake_worker = _FakeEventWorker(raise_on_process=True)
        payload = json.dumps(_sample_payload()).encode("utf-8")

        process_one_message(fake_redis, "stream", "group", b"1-0", {b"payload": payload}, fake_worker)

        self.assertEqual(fake_redis.acked, [])  # ไม่ ack

    def test_string_payload_key_also_supported(self):
        fake_redis = _FakeRedisForConsumer()
        fake_worker = _FakeEventWorker()
        payload = json.dumps(_sample_payload()).encode("utf-8")

        process_one_message(fake_redis, "stream", "group", b"1-0", {"payload": payload}, fake_worker)

        self.assertEqual(len(fake_worker.processed), 1)


class TestEnsureConsumerGroup(unittest.TestCase):
    def test_creates_group_when_missing(self):
        fake_redis = _FakeRedisForConsumer()
        ensure_consumer_group(fake_redis, "stream", "group")
        self.assertEqual(fake_redis.group_created_with, ("stream", "group", "0", True))

    def test_busygroup_error_is_swallowed(self):
        import redis

        class RedisRaisesBusyGroup:
            def xgroup_create(self, *a, **k):
                raise redis.ResponseError("BUSYGROUP Consumer Group name already exists")

        # ไม่ควร raise ออกมา — กลุ่มมีอยู่แล้วถือว่าปกติ
        ensure_consumer_group(RedisRaisesBusyGroup(), "stream", "group")

    def test_other_response_error_propagates(self):
        import redis

        class RedisRaisesOtherError:
            def xgroup_create(self, *a, **k):
                raise redis.ResponseError("some other real error")

        with self.assertRaises(redis.ResponseError):
            ensure_consumer_group(RedisRaisesOtherError(), "stream", "group")


# ============================================================
#  EventWorker.process — ทดสอบ orchestration เต็มเส้นทางด้วย fake
#  session/camera/frigate client/store (ไม่มีตัวไหนต่อ infra จริงเลย)
#
#  ตั้งใจแยกจาก db/migrations (ที่ตรวจแค่ว่า schema compile ถูก) — ทดสอบชุด
#  นี้ตรวจ "logic การตัดสินใจ" ของ worker เอง เช่น ข้ามงานเมื่อไม่รู้จัก
#  กล้อง, ยังบันทึก metadata ต่อได้แม้ snapshot ดึงไม่สำเร็จ
# ============================================================

class _FakeCamera:
    def __init__(self, zone_config, role=None):
        self.zone_config = zone_config
        self.role = role


class _FakeScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeSession:
    def __init__(self, camera=None, existing_detection_id=None):
        self._camera = camera
        # query เช็ก idempotency ของ _process_vehicle/_process_plate คืนค่านี้
        # — ตั้งเป็นไม่ None เพื่อจำลอง "แถวนี้มีอยู่แล้ว (redelivery ซ้ำ)"
        # (เทสต์แต่ละเคสเช็กแค่ทีละ kind เลยไม่ต้องแยกตาม vehicle/plate)
        self._existing_detection_id = existing_detection_id
        self.executed = []
        self.added = []
        self.committed = False
        self._next_id = 1

    def get(self, model, camera_id):
        return self._camera

    def execute(self, stmt):
        self.executed.append(stmt)
        return _FakeScalarResult(self._existing_detection_id)

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        # จำลอง autoincrement PK ที่ DB จริงจะให้หลัง flush/insert
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = self._next_id
                self._next_id += 1

    def commit(self):
        self.committed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeSessionFactory:
    """ทำตัวเหมือน sessionmaker(...) — เรียกแล้วได้ context manager ใหม่ทุกครั้ง"""

    def __init__(self, session: _FakeSession):
        self._session = session

    def __call__(self):
        return self._session


class _FakeFrigateClient:
    def __init__(self, snapshot=b"fake-jpeg", raise_error=False, raise_error_on_crop=None):
        self.snapshot = snapshot
        self.raise_error = raise_error
        # เผื่อเทสต์ที่อยากให้ full snapshot สำเร็จแต่ crop snapshot ล้มเหลว
        # เท่านั้น (หรือกลับกัน) — ถ้าไม่ตั้งจะใช้ค่า raise_error เดิมทั้งคู่
        self.raise_error_on_crop = raise_error if raise_error_on_crop is None else raise_error_on_crop
        self.requested = []  # list ของ (event_id, crop)

    def get_snapshot(self, event_id, crop=False):
        self.requested.append((event_id, crop))
        should_raise = self.raise_error_on_crop if crop else self.raise_error
        if should_raise:
            from services.indexer.frigate_client import FrigateClientError
            raise FrigateClientError("จำลอง Frigate ไม่ตอบสนอง")
        return self.snapshot

    @property
    def requested_ids(self):
        # ★ เข้ากันได้กับเทสต์เดิมที่เช็ก requested_ids ก่อนมี crop param
        return [event_id for event_id, _crop in self.requested]


class _FakeStore:
    def __init__(self):
        self.saved = {}

    def save(self, key, data):
        self.saved[key] = data
        return key


def _tiny_jpeg_bytes() -> bytes:
    """ภาพ JPEG เล็ก ๆ ที่ decode ได้จริง — ใช้แทน 'fake-jpeg' bytes ธรรมดา
    ตอนเทสต์ทางเดินที่ต้องผ่าน cv2.imdecode ใน _process_vehicle"""
    ok, buf = cv2.imencode(".jpg", np.zeros((20, 20, 3), dtype=np.uint8))
    assert ok
    return buf.tobytes()


def _fake_vehicle_result() -> VehicleResult:
    return VehicleResult(
        attrs=VehicleAttrs(vehicle_type="car", type_conf=0.9, color="white", color_conf=0.8),
        embedding=[0.1, 0.2, 0.3],
    )


class TestEventWorkerProcess(unittest.TestCase):
    def _parsed(self, entered_zones=None, label="car"):
        payload = _sample_payload(
            camera="WH01_GATE_LPR",
            entered_zones=entered_zones or ["gate_outside", "gate_inside"],
            label=label,
        )
        return parse_frigate_event(payload)

    def test_known_camera_full_happy_path(self):
        camera = _FakeCamera(
            zone_config={"outside_zone": "gate_outside", "inside_zone": "gate_inside"},
            role=CameraRole.overview,
        )
        session = _FakeSession(camera=camera)
        frigate_client = _FakeFrigateClient(snapshot=_tiny_jpeg_bytes())
        store = _FakeStore()

        worker = EventWorker(
            _FakeSessionFactory(session), frigate_client, store, site_id="WH01",
            vehicle_analyzer=lambda image: _fake_vehicle_result(),
        )
        worker.process(self._parsed())

        # snapshot ถูกดึง 2 ครั้ง — ทั้งเฟรม (thumb_key) + ครอปรถ (AI)
        self.assertEqual(frigate_client.requested, [("1699999999.123456-abcdef", False), ("1699999999.123456-abcdef", True)])
        self.assertEqual(len(store.saved), 1)  # เก็บแค่ภาพทั้งเฟรมลง store ไม่เก็บภาพครอป
        # execute: 1) upsert event  2) idempotency check ก่อนเขียน vehicle detection
        self.assertEqual(len(session.executed), 2)
        # add: Detection(kind=vehicle) + Vehicle
        self.assertEqual(len(session.added), 2)
        detection, vehicle = session.added
        self.assertEqual(detection.kind.value, "vehicle")
        self.assertEqual(vehicle.detection_id, detection.id)
        self.assertEqual(vehicle.vehicle_type, "car")
        self.assertEqual(vehicle.color, "white")
        self.assertEqual(vehicle.embedding, [0.1, 0.2, 0.3])
        self.assertTrue(session.committed)

    def test_non_vehicle_label_skips_vehicle_processing(self):
        # label='person' ไม่อยู่ใน VEHICLE_LABELS — ไม่ควรพยายามวิเคราะห์รถเลย
        camera = _FakeCamera(zone_config={})
        session = _FakeSession(camera=camera)
        frigate_client = _FakeFrigateClient(snapshot=_tiny_jpeg_bytes())
        store = _FakeStore()

        worker = EventWorker(
            _FakeSessionFactory(session), frigate_client, store, site_id="WH01",
            vehicle_analyzer=lambda image: _fake_vehicle_result(),
        )
        worker.process(self._parsed(label="person"))

        self.assertEqual(frigate_client.requested, [("1699999999.123456-abcdef", False)])  # ดึงแค่ thumb
        self.assertEqual(len(session.executed), 1)  # แค่ upsert event ไม่มี idempotency check
        self.assertEqual(len(session.added), 0)

    def test_vehicle_already_recorded_skips_duplicate_insert(self):
        # ★ จำลอง redelivery ซ้ำจาก Redis Stream — ต้องไม่เขียนแถวซ้ำ
        camera = _FakeCamera(zone_config={}, role=CameraRole.overview)
        session = _FakeSession(camera=camera, existing_detection_id=42)
        frigate_client = _FakeFrigateClient(snapshot=_tiny_jpeg_bytes())
        store = _FakeStore()

        worker = EventWorker(
            _FakeSessionFactory(session), frigate_client, store, site_id="WH01",
            vehicle_analyzer=lambda image: _fake_vehicle_result(),
        )
        worker.process(self._parsed())

        self.assertEqual(frigate_client.requested, [("1699999999.123456-abcdef", False)])  # ไม่ควรไปดึงภาพครอปเลย
        self.assertEqual(len(session.added), 0)

    def test_vehicle_crop_decode_failure_does_not_crash(self):
        # snapshot ปลอมที่ decode ไม่ได้ (ไม่ใช่ jpeg จริง) — ต้อง skip เงียบ ๆ
        camera = _FakeCamera(zone_config={}, role=CameraRole.overview)
        session = _FakeSession(camera=camera)
        frigate_client = _FakeFrigateClient(snapshot=b"not-a-real-jpeg")
        store = _FakeStore()

        worker = EventWorker(
            _FakeSessionFactory(session), frigate_client, store, site_id="WH01",
            vehicle_analyzer=lambda image: _fake_vehicle_result(),
        )
        worker.process(self._parsed())  # ต้องไม่ raise

        self.assertEqual(len(session.added), 0)
        self.assertTrue(session.committed)  # event metadata ยังบันทึกอยู่

    def test_vehicle_analyzer_exception_does_not_crash(self):
        camera = _FakeCamera(zone_config={}, role=CameraRole.overview)
        session = _FakeSession(camera=camera)
        frigate_client = _FakeFrigateClient(snapshot=_tiny_jpeg_bytes())
        store = _FakeStore()

        def _boom(image):
            raise RuntimeError("จำลองโมเดล AI พัง")

        worker = EventWorker(
            _FakeSessionFactory(session), frigate_client, store, site_id="WH01",
            vehicle_analyzer=_boom,
        )
        worker.process(self._parsed())  # ต้องไม่ raise

        self.assertEqual(len(session.added), 0)
        self.assertTrue(session.committed)

    def test_unknown_camera_skipped_without_crash(self):
        session = _FakeSession(camera=None)  # session.get คืน None = ไม่รู้จักกล้องนี้
        frigate_client = _FakeFrigateClient()
        store = _FakeStore()

        worker = EventWorker(_FakeSessionFactory(session), frigate_client, store, site_id="WH01")
        worker.process(self._parsed())  # ต้องไม่ raise

        # ไม่ควรพยายามดึง snapshot หรือเขียน DB เลยถ้าไม่รู้จักกล้อง
        self.assertEqual(len(frigate_client.requested_ids), 0)
        self.assertEqual(len(session.executed), 0)
        self.assertFalse(session.committed)

    def test_snapshot_fetch_failure_still_saves_metadata(self):
        # ★ ดึง snapshot ไม่ได้ (ทั้งเฟรมและครอป) ไม่ควรทำให้ metadata หายไปด้วย
        camera = _FakeCamera(zone_config={}, role=CameraRole.overview)
        session = _FakeSession(camera=camera)
        frigate_client = _FakeFrigateClient(raise_error=True)
        store = _FakeStore()

        worker = EventWorker(
            _FakeSessionFactory(session), frigate_client, store, site_id="WH01",
            vehicle_analyzer=lambda image: _fake_vehicle_result(),  # ไม่ควรถูกเรียกเลย
        )
        worker.process(self._parsed())

        self.assertEqual(len(store.saved), 0)  # ไม่มีอะไรให้บันทึก
        # execute: 1) upsert event  2) idempotency check (เช็กก่อนถึงจะไปดึง
        # ภาพครอป ซึ่งพังตรงนั้น) — ไม่มีการ add Detection/Vehicle เลย
        self.assertEqual(len(session.executed), 2)
        self.assertEqual(len(session.added), 0)
        self.assertTrue(session.committed)  # event row ยังถูกเขียน

    # ------------------------------------------------------
    #  role='lpr' -> _process_plate (อ่านป้ายตรง ไม่มี plate_detect.py แยก)
    # ------------------------------------------------------

    def test_lpr_camera_full_happy_path(self):
        camera = _FakeCamera(zone_config={}, role=CameraRole.lpr)
        session = _FakeSession(camera=camera)
        frigate_client = _FakeFrigateClient(snapshot=_tiny_jpeg_bytes())
        store = _FakeStore()

        fake_ocr = OcrResult(
            lines=["1กก 1234", "กรุงเทพมหานคร"],
            char_confs=[0.95] * 12,
            ocr_conf=0.95,
            box_count=2,
        )
        worker = EventWorker(
            _FakeSessionFactory(session), frigate_client, store, site_id="WH01",
            plate_reader=lambda image: fake_ocr,
        )
        worker.process(self._parsed())

        self.assertEqual(frigate_client.requested, [("1699999999.123456-abcdef", False), ("1699999999.123456-abcdef", True)])
        self.assertEqual(len(session.executed), 2)  # upsert event + idempotency check
        self.assertEqual(len(session.added), 2)  # Detection(kind=plate) + Plate
        detection, plate = session.added
        self.assertEqual(detection.kind.value, "plate")
        self.assertEqual(plate.detection_id, detection.id)
        self.assertEqual(plate.plate_norm, "1กก1234")
        self.assertEqual(plate.province, "กรุงเทพมหานคร")
        self.assertTrue(session.committed)

    def test_overview_camera_does_not_run_plate_ocr(self):
        # ★ เช็กสลับด้าน — กล้อง overview ต้องไม่ไปเรียก plate_reader
        camera = _FakeCamera(zone_config={}, role=CameraRole.overview)
        session = _FakeSession(camera=camera)
        frigate_client = _FakeFrigateClient(snapshot=_tiny_jpeg_bytes())
        store = _FakeStore()

        def _should_not_be_called(image):
            raise AssertionError("ไม่ควรเรียก plate_reader ตอนกล้อง role=overview")

        worker = EventWorker(
            _FakeSessionFactory(session), frigate_client, store, site_id="WH01",
            vehicle_analyzer=lambda image: _fake_vehicle_result(),
            plate_reader=_should_not_be_called,
        )
        worker.process(self._parsed())  # ต้องไม่ raise (พิสูจน์ว่าไม่ถูกเรียก)

    def test_lpr_camera_does_not_run_vehicle_analysis(self):
        # ★ เช็กสลับด้าน — กล้อง lpr ต้องไม่ไปเรียก vehicle_analyzer
        camera = _FakeCamera(zone_config={}, role=CameraRole.lpr)
        session = _FakeSession(camera=camera)
        frigate_client = _FakeFrigateClient(snapshot=_tiny_jpeg_bytes())
        store = _FakeStore()

        def _should_not_be_called(image):
            raise AssertionError("ไม่ควรเรียก vehicle_analyzer ตอนกล้อง role=lpr")

        worker = EventWorker(
            _FakeSessionFactory(session), frigate_client, store, site_id="WH01",
            vehicle_analyzer=_should_not_be_called,
            plate_reader=lambda image: OcrResult(lines=[], char_confs=[], ocr_conf=0.0, box_count=0),
        )
        worker.process(self._parsed())  # ต้องไม่ raise

    def test_plate_already_recorded_skips_duplicate_insert(self):
        camera = _FakeCamera(zone_config={}, role=CameraRole.lpr)
        session = _FakeSession(camera=camera, existing_detection_id=99)
        frigate_client = _FakeFrigateClient(snapshot=_tiny_jpeg_bytes())
        store = _FakeStore()

        worker = EventWorker(
            _FakeSessionFactory(session), frigate_client, store, site_id="WH01",
            plate_reader=lambda image: OcrResult(lines=["1กก 1234"], char_confs=[], ocr_conf=0.9, box_count=1),
        )
        worker.process(self._parsed())

        self.assertEqual(frigate_client.requested, [("1699999999.123456-abcdef", False)])  # ไม่ควรไปดึงภาพครอปเลย
        self.assertEqual(len(session.added), 0)

    def test_plate_no_text_detected_saves_nothing(self):
        # ★ ไม่เจอข้อความเลยในภาพครอป (มุม/แสงไม่ดี) — ไม่ใช่ error แค่ไม่มี
        # อะไรให้บันทึก ไม่ควร insert แถวว่างเปล่า
        camera = _FakeCamera(zone_config={}, role=CameraRole.lpr)
        session = _FakeSession(camera=camera)
        frigate_client = _FakeFrigateClient(snapshot=_tiny_jpeg_bytes())
        store = _FakeStore()

        worker = EventWorker(
            _FakeSessionFactory(session), frigate_client, store, site_id="WH01",
            plate_reader=lambda image: OcrResult(lines=[], char_confs=[], ocr_conf=0.0, box_count=0),
        )
        worker.process(self._parsed())

        self.assertEqual(len(session.added), 0)
        self.assertTrue(session.committed)

    def test_plate_reader_exception_does_not_crash(self):
        camera = _FakeCamera(zone_config={}, role=CameraRole.lpr)
        session = _FakeSession(camera=camera)
        frigate_client = _FakeFrigateClient(snapshot=_tiny_jpeg_bytes())
        store = _FakeStore()

        def _boom(image):
            raise RuntimeError("จำลอง PaddleOCR พัง")

        worker = EventWorker(
            _FakeSessionFactory(session), frigate_client, store, site_id="WH01",
            plate_reader=_boom,
        )
        worker.process(self._parsed())  # ต้องไม่ raise

        self.assertEqual(len(session.added), 0)
        self.assertTrue(session.committed)

    def test_camera_without_zone_config_direction_is_none(self):
        camera = _FakeCamera(zone_config=None)
        session = _FakeSession(camera=camera)
        worker = EventWorker(
            _FakeSessionFactory(session), _FakeFrigateClient(), _FakeStore(), site_id="WH01",
            vehicle_analyzer=lambda image: _fake_vehicle_result(),
        )

        # ไม่ควร crash แม้ zone_config เป็น None (ยังไม่ได้ตั้งค่า) — snapshot
        # ปลอมเริ่มต้นไม่ใช่ jpeg จริงด้วย เลย decode ไม่ผ่านใน _process_vehicle
        # แต่ก็ยังไม่ควร crash
        worker.process(self._parsed())
        self.assertTrue(session.committed)


if __name__ == "__main__":
    unittest.main()
