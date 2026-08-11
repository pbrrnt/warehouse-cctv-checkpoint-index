/*
หน้าเว็บค้นหา — เรียก services/api/ ตรง ๆ (ไม่มี build step ไม่มี framework
ตั้งใจให้ deploy ง่ายที่สุด แค่ static file เสิร์ฟผ่าน nginx ก็พอ)

★ ถ้าเรียก API จริงไม่ได้ (ยังไม่ได้ตั้งเซิร์ฟเวอร์/ไม่มี key/เครือข่ายพัง)
จะสลับเป็น "โหมดตัวอย่าง" อัตโนมัติ โชว์ข้อมูลสมมติแทน — ไว้ดูหน้าตา UI ได้
โดยไม่ต้องมี Postgres/API รันจริง (ดู DEMO_* ด้านล่าง) มี badge เตือนชัดเจน
ตอนอยู่โหมดนี้ กันสับสนว่าเป็นข้อมูลจริง
*/

const API_BASE = "/api"; // nginx.conf proxy /api/ -> search-api (ตัด prefix ออกก่อนส่งต่อ)
const API_KEY_STORAGE_KEY = "cctv_index_api_key";

const CAMERA_NAME_TH = {}; // เติมจากผลลัพธ์จริงที่เจอ (camera.id -> name) ใช้ dropdown filter

let demoMode = false;

function getApiKey() {
    return localStorage.getItem(API_KEY_STORAGE_KEY) || "";
}

// เวอร์ชัน JS ของ services/api/registry.py::normalize_registry_plate (แปลง
// เลขไทย -> อารบิก, ตัดช่องว่าง) ใช้เฉพาะตอน "โหมดตัวอย่าง" (client-side
// ล้วน ไม่มี backend จริงให้ normalize ให้) เพื่อให้พฤติกรรมตรงกับของจริง
// ไม่งั้นทะเบียนที่พิมพ์ด้วยเลขไทย/มีช่องว่างจะไม่ match กับที่ OCR อ่านได้
function normalizePlateClientSide(raw) {
    const thaiDigits = "๐๑๒๓๔๕๖๗๘๙";
    let result = "";
    for (const ch of raw) {
        if (ch === " " || ch === "\t") continue;
        const idx = thaiDigits.indexOf(ch);
        result += idx >= 0 ? String(idx) : ch;
    }
    return result;
}

async function apiGet(path, params) {
    const url = new URL(API_BASE + path, window.location.origin);
    for (const [k, v] of Object.entries(params || {})) {
        if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, v);
    }
    const resp = await fetch(url, {
        headers: { "X-API-Key": getApiKey() },
    });
    if (!resp.ok) throw new Error(`API ${resp.status}`);
    return resp.json();
}

async function apiPut(path, body) {
    const resp = await fetch(new URL(API_BASE + path, window.location.origin), {
        method: "PUT",
        headers: { "X-API-Key": getApiKey(), "Content-Type": "application/json" },
        body: JSON.stringify(body),
    });
    if (!resp.ok) throw new Error(`API ${resp.status}`);
    return resp.json();
}

async function apiDelete(path) {
    const resp = await fetch(new URL(API_BASE + path, window.location.origin), {
        method: "DELETE",
        headers: { "X-API-Key": getApiKey() },
    });
    if (!resp.ok && resp.status !== 404) throw new Error(`API ${resp.status}`);
}

// ============================================================
//  โหมดตัวอย่าง — ข้อมูลสมมติหน้าตาเหมือนของจริง (ดู docs/08-nvr-integration.md หัวข้อ 6)
// ============================================================

const DEMO_CAMERAS = {
    "WH01-GATE-LPR": { id: "WH01-GATE-LPR", name: "ประตูใหญ่ - อ่านป้าย", nvr_channel: 3 },
    "WH01-GATE-OVERVIEW": { id: "WH01-GATE-OVERVIEW", name: "ประตูใหญ่ - ภาพรวม", nvr_channel: 4 },
    "WH01-DOOR-FACE": { id: "WH01-DOOR-FACE", name: "ประตูคน", nvr_channel: 7 },
};

// ★ let ไม่ใช่ const — โหมดตัวอย่างให้แก้ทะเบียนที่รู้จักได้จริง (เพิ่ม/ลบ)
// เพื่อให้ดูหน้าตา UI ตอนโต้ตอบได้ครบ ไม่ใช่แค่ static list
let demoRegistry = [
    { plate_norm: "1กก1234", owner_label: "รถส่งของบริษัท ก", notes: "มาส่งของทุกวันจันทร์", watch: false },
    { plate_norm: "9ขค5678", owner_label: null, notes: null, watch: true },
];

function demoRegistryLookup(plateNorm) {
    return demoRegistry.find((e) => e.plate_norm === plateNorm) || null;
}

function demoPlateResults(q) {
    const base = [
        { plate_norm: "1กก1234", raw_text: "1กก 1234", province: "กรุงเทพมหานคร", ocr_conf: 0.96, match_score: 1.0, camera: DEMO_CAMERAS["WH01-GATE-LPR"], direction: "in", local_time: "2026-08-09T14:32:07+07:00", nvr_playback_time: "2026-08-09T14:31:59+07:00", event_id: "demo-1" },
        { plate_norm: "1ถก1234", raw_text: "1ถก 1234", province: "นนทบุรี", ocr_conf: 0.89, match_score: 0.82, camera: DEMO_CAMERAS["WH01-GATE-LPR"], direction: "out", local_time: "2026-08-09T11:05:41+07:00", nvr_playback_time: "2026-08-09T11:05:33+07:00", event_id: "demo-2" },
        { plate_norm: "9ขค5678", raw_text: "9ขค 5678", province: "ปทุมธานี", ocr_conf: 0.74, match_score: 0.55, camera: DEMO_CAMERAS["WH01-GATE-LPR"], direction: "in", local_time: "2026-08-08T09:14:02+07:00", nvr_playback_time: "2026-08-08T09:13:54+07:00", event_id: "demo-3" },
    ];
    // จำลอง LEFT JOIN plate_registry ที่ API จริงทำ (ดู services/api/search.py)
    const withRegistry = base.map((r) => {
        const entry = demoRegistryLookup(r.plate_norm);
        return { ...r, owner_label: entry ? entry.owner_label : null, watch: entry ? entry.watch : false };
    });
    if (!q) return withRegistry;
    return withRegistry.filter((r) => r.plate_norm.includes(q) || q.includes(r.plate_norm.slice(0, 2)));
}

function demoVehicleAttrsFor(eventId) {
    const map = {
        "demo-1": { vehicle_type: "รถกระบะ", color: "ขาว" },
        "demo-2": { vehicle_type: "รถเก๋ง", color: "ดำ" },
        "demo-3": { vehicle_type: "รถบรรทุก", color: "แดง" },
    };
    return map[eventId] || null;
}

function demoNowResults() {
    return [
        { plate_norm: "1กก1234", province: "กรุงเทพมหานคร", camera: DEMO_CAMERAS["WH01-GATE-LPR"], local_time: "2026-08-09T14:32:07+07:00" },
        { plate_norm: "5จฉ9012", province: "สมุทรปราการ", camera: DEMO_CAMERAS["WH01-GATE-LPR"], local_time: "2026-08-09T13:50:12+07:00" },
    ];
}

// ============================================================
//  แปลงเวลา/เลข confidence ให้อ่านง่าย
// ============================================================

function formatThaiDateTime(iso) {
    const d = new Date(iso);
    const pad = (n) => String(n).padStart(2, "0");
    return `${pad(d.getDate())}/${pad(d.getMonth() + 1)}/${d.getFullYear()}  ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function confidenceClass(v) {
    if (v >= 0.85) return "";
    if (v >= 0.6) return "mid";
    return "low";
}

const DIRECTION_TH = { in: "เข้า", out: "ออก" };

// ============================================================
//  render
// ============================================================

function cameraChip(camera) {
    if (!camera) return "";
    const ch = camera.nvr_channel != null ? ` (NVR ช่อง ${camera.nvr_channel})` : "";
    return `${camera.name}${ch}`;
}

function copyButton(nvrTime) {
    const id = "copy-" + Math.random().toString(36).slice(2);
    return `<button class="copy-btn" id="${id}" data-copy="${nvrTime}">คัดลอกเวลาไปเปิด NVR</button>`;
}

function renderPlateCard(r) {
    const confPct = r.ocr_conf != null ? Math.round(r.ocr_conf * 100) : null;
    const attrs = demoMode ? demoVehicleAttrsFor(r.event_id) : null; // ยังไม่ join รถจริง (vehicles.plate_id ว่าง — ดู ADR ที่เกี่ยวข้อง)
    const dirBadge = r.direction
        ? `<span class="direction-badge ${r.direction}">${DIRECTION_TH[r.direction] || r.direction}</span>`
        : "";
    const ownerTag = r.owner_label ? `<span class="owner-tag">🏷 ${r.owner_label}</span>` : "";
    const watchBadge = r.watch ? `<span class="watch-badge">⚠ เฝ้าระวัง</span>` : "";
    return `
    <div class="card">
        <div class="thumb">🚗</div>
        <div class="body">
            <div class="headline">
                <span class="plate">${r.plate_norm || r.raw_text || "(อ่านไม่ออก)"}</span>
                <span class="province">${r.province || ""}</span>
                ${confPct != null ? `<span class="confidence ${confidenceClass(r.ocr_conf)}">ความมั่นใจ ${confPct}%</span>` : ""}
            </div>
            ${attrs ? `<div class="attrs">${attrs.vehicle_type} สี${attrs.color}</div>` : ""}
            <div class="meta">
                <span>${cameraChip(r.camera)}</span>
                ${dirBadge}
                ${ownerTag}
                ${watchBadge}
            </div>
            <div class="timerow">
                <time>${formatThaiDateTime(r.local_time)}</time>
                ${copyButton(r.nvr_playback_time)}
            </div>
        </div>
    </div>`;
}

function renderNowCard(r) {
    return `
    <div class="card">
        <div class="thumb">🚗</div>
        <div class="body">
            <div class="headline">
                <span class="plate">${r.plate_norm}</span>
                <span class="province">${r.province || ""}</span>
            </div>
            <div class="meta"><span>${cameraChip(r.camera)}</span></div>
            <div class="timerow"><time>เข้าเมื่อ ${formatThaiDateTime(r.local_time)}</time></div>
        </div>
    </div>`;
}

function bindCopyButtons(container) {
    container.querySelectorAll(".copy-btn").forEach((btn) => {
        btn.addEventListener("click", async () => {
            const text = btn.getAttribute("data-copy");
            try {
                await navigator.clipboard.writeText(text);
            } catch (e) {
                // clipboard API อาจใช้ไม่ได้ใน context ที่ไม่ใช่ https/localhost — เงียบไว้ ปุ่มยัง feedback ให้เห็นว่า "กด" แล้ว
            }
            btn.textContent = "คัดลอกแล้ว";
            btn.classList.add("copied");
            setTimeout(() => {
                btn.textContent = "คัดลอกเวลาไปเปิด NVR";
                btn.classList.remove("copied");
            }, 1500);
        });
    });
}

function renderRegistryRow(entry) {
    const watchBadge = entry.watch ? `<span class="watch-badge">⚠ เฝ้าระวัง</span>` : "";
    return `
    <div class="registry-row" data-plate="${entry.plate_norm}">
        <span class="reg-plate">${entry.plate_norm}</span>
        <span class="reg-owner">${entry.owner_label || "(ยังไม่ตั้งชื่อ)"}</span>
        <span class="reg-notes">${entry.notes || ""}</span>
        ${watchBadge}
        <button class="icon-btn edit-btn" data-plate="${entry.plate_norm}">แก้ไข</button>
        <button class="icon-btn danger delete-btn" data-plate="${entry.plate_norm}">ลบ</button>
    </div>`;
}

function bindRegistryRowButtons(container, entries) {
    container.querySelectorAll(".edit-btn").forEach((btn) => {
        btn.addEventListener("click", () => {
            const entry = entries.find((e) => e.plate_norm === btn.dataset.plate);
            if (!entry) return;
            document.getElementById("regPlate").value = entry.plate_norm;
            document.getElementById("regOwner").value = entry.owner_label || "";
            document.getElementById("regNotes").value = entry.notes || "";
            document.getElementById("regWatch").checked = !!entry.watch;
            document.getElementById("regPlate").scrollIntoView({ behavior: "smooth", block: "center" });
        });
    });
    container.querySelectorAll(".delete-btn").forEach((btn) => {
        btn.addEventListener("click", async () => {
            if (!confirm(`ลบทะเบียน ${btn.dataset.plate} ออกจากทะเบียนที่รู้จักหรือไม่?`)) return;
            try {
                await apiDelete(`/registry/${encodeURIComponent(btn.dataset.plate)}`);
            } catch (e) {
                showDemoBadge();
                demoRegistry = demoRegistry.filter((x) => x.plate_norm !== btn.dataset.plate);
            }
            loadRegistry();
        });
    });
}

async function loadRegistry() {
    let entries;
    try {
        entries = await apiGet("/registry", {});
    } catch (e) {
        showDemoBadge();
        entries = demoRegistry;
    }
    const container = document.getElementById("registryList");
    const countEl = document.getElementById("registryCount");
    if (entries.length === 0) {
        container.innerHTML = `<div class="empty-state">ยังไม่มีทะเบียนที่บันทึกไว้</div>`;
        countEl.textContent = "";
        return;
    }
    countEl.textContent = `${entries.length} รายการ`;
    container.innerHTML = entries.map(renderRegistryRow).join("");
    bindRegistryRowButtons(container, entries);
}

async function saveRegistryEntry() {
    const plateRaw = document.getElementById("regPlate").value.trim();
    if (!plateRaw) {
        alert("กรุณาพิมพ์ทะเบียนก่อนบันทึก");
        return;
    }
    const body = {
        owner_label: document.getElementById("regOwner").value.trim() || null,
        notes: document.getElementById("regNotes").value.trim() || null,
        watch: document.getElementById("regWatch").checked,
    };

    try {
        // API จริงจะ normalize ทะเบียนให้เอง (services/api/registry.py) — ส่ง raw ไปได้
        await apiPut(`/registry/${encodeURIComponent(plateRaw)}`, body);
    } catch (e) {
        showDemoBadge();
        // โหมดตัวอย่างไม่มี backend ให้ normalize เลย ทำเองฝั่ง client แทน
        // ให้พฤติกรรมตรงกับของจริง (ดู normalizePlateClientSide ด้านบน)
        const plateNorm = normalizePlateClientSide(plateRaw);
        const existing = demoRegistry.find((x) => x.plate_norm === plateNorm);
        if (existing) {
            Object.assign(existing, body);
        } else {
            demoRegistry.push({ plate_norm: plateNorm, ...body });
        }
    }

    document.getElementById("regPlate").value = "";
    document.getElementById("regOwner").value = "";
    document.getElementById("regNotes").value = "";
    document.getElementById("regWatch").checked = false;
    loadRegistry();
}

function showDemoBadge() {
    demoMode = true;
    document.getElementById("demoBadge").hidden = false;
}

function updateKeyStatus() {
    const status = document.getElementById("keyStatus");
    status.textContent = getApiKey() ? "ตั้งค่าไว้แล้ว" : "ยังไม่ได้ตั้งค่า";
    status.style.color = getApiKey() ? "var(--ok)" : "var(--text-dim)";
}

function toggleKeyPanel() {
    const panel = document.getElementById("keyPanel");
    panel.hidden = !panel.hidden;
    if (!panel.hidden) {
        document.getElementById("keyInput").value = getApiKey();
        updateKeyStatus();
    }
}

function saveApiKey() {
    const key = document.getElementById("keyInput").value.trim();
    if (key) {
        localStorage.setItem(API_KEY_STORAGE_KEY, key);
    } else {
        localStorage.removeItem(API_KEY_STORAGE_KEY);
    }
    updateKeyStatus();
    document.getElementById("keyPanel").hidden = true;
}

// ============================================================
//  actions
// ============================================================

async function runSearch() {
    const q = document.getElementById("plateQuery").value.trim();
    const params = {
        q,
        camera_id: document.getElementById("filterCamera").value,
        vehicle_type: document.getElementById("filterVehicleType").value,
        color: document.getElementById("filterColor").value,
        direction: document.getElementById("filterDirection").value,
        from_ts: document.getElementById("filterFrom").value,
        to_ts: document.getElementById("filterTo").value,
    };

    let results;
    if (!q) {
        // หน้าเพิ่งเปิด ยังไม่ได้พิมพ์อะไร — โชว์ตัวอย่างผลลัพธ์ไปก่อนเลย
        // ไม่ต้องรอผู้ใช้พิมพ์ก่อนถึงจะเห็นหน้าตา UI
        showDemoBadge();
        results = demoPlateResults(q);
    } else {
        try {
            results = await apiGet("/search/plates", params);
        } catch (e) {
            showDemoBadge();
            results = demoPlateResults(q);
        }
    }

    const container = document.getElementById("searchResults");
    const countEl = document.getElementById("searchResultCount");
    if (results.length === 0) {
        container.innerHTML = `<div class="empty-state">ไม่พบทะเบียนที่ตรงกับ "${q}" ลองพิมพ์สั้นลงหรือเช็คช่วงเวลา</div>`;
        countEl.textContent = "";
        return;
    }
    countEl.textContent = `พบ ${results.length} รายการ`;
    container.innerHTML = results.map(renderPlateCard).join("");
    bindCopyButtons(container);
}

async function loadNow() {
    let results;
    try {
        results = await apiGet("/now", {});
    } catch (e) {
        showDemoBadge();
        results = demoNowResults();
    }
    const container = document.getElementById("nowResults");
    const countEl = document.getElementById("nowResultCount");
    if (results.length === 0) {
        container.innerHTML = `<div class="empty-state">ไม่มีรถค้างอยู่ในระบบตอนนี้</div>`;
        countEl.textContent = "";
        return;
    }
    countEl.textContent = `${results.length} คัน`;
    container.innerHTML = results.map(renderNowCard).join("");
}

function switchTab(name) {
    document.querySelectorAll(".tab-btn").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
    document.getElementById("tab-search").hidden = name !== "search";
    document.getElementById("tab-now").hidden = name !== "now";
    document.getElementById("tab-registry").hidden = name !== "registry";
    if (name === "now") loadNow();
    if (name === "registry") loadRegistry();
}

// ============================================================
//  wire up
// ============================================================

document.getElementById("searchBtn").addEventListener("click", runSearch);
document.getElementById("plateQuery").addEventListener("keydown", (e) => {
    if (e.key === "Enter") runSearch();
});
document.querySelectorAll(".tab-btn").forEach((b) => {
    b.addEventListener("click", () => switchTab(b.dataset.tab));
});
document.getElementById("regSaveBtn").addEventListener("click", saveRegistryEntry);
document.getElementById("keySettingsBtn").addEventListener("click", toggleKeyPanel);
document.getElementById("keySaveBtn").addEventListener("click", saveApiKey);

// เปิดหน้ามาแสดงตัวอย่างผลค้นหาเลย (ไม่บังคับให้พิมพ์ก่อนถึงจะเห็นหน้าตา UI)
runSearch();
