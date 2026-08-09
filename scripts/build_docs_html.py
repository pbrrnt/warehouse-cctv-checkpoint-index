#!/usr/bin/env python3
"""
แปลงเอกสาร Markdown ใน docs/ เป็น HTML แบบอ่านง่าย ลง docs/html/

- ใช้ดีไซน์เดียวกับ docs/10-chokepoint-design.html (ทำมือ)
- ไฟล์ HTML แต่ละไฟล์ self-contained (CSS ฝังในตัว) คัดลอกไปไหนก็อ่านได้
- เช็กลิสต์ - [ ] กลายเป็นช่องติ๊กจริง จำสถานะไว้ใน localStorage
- ลิงก์ .md ถูกเขียนใหม่เป็น .html
- 10-chokepoint-design.html คัดลอกจากฉบับทำมือ ไม่ generate ทับ

รัน:  python scripts/build_docs_html.py
"""

import html
import re
import shutil
from pathlib import Path

from markdown_it import MarkdownIt

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
OUT = DOCS / "html"

# เล่มที่ทำมือไว้แล้ว ไม่ generate ทับ
HANDMADE = {"10-chokepoint-design"}

# หัวข้อย่อยสำหรับหน้า index
BLURB = {
    "00-decisions": "บันทึกการตัดสินใจ (ADR) และสิ่งที่ยังไม่ตัดสินใจ",
    "01-architecture": "สถาปัตยกรรมระบบและเหตุผลของแต่ละทางเลือก",
    "02-hardware-sizing": "เลือกเครื่อง และสูตรคำนวณพื้นที่จัดเก็บ",
    "03-camera-onboarding": "เทคนิคการต่อกล้อง Dahua/ONVIF และกับดักที่พบบ่อย",
    "04-data-model": "โครงสร้างฐานข้อมูล index และคิวรีตัวอย่าง",
    "05-thai-lpr": "ป้ายทะเบียนไทย รูปแบบ ตารางอักษรสับสน 77 จังหวัด",
    "06-pdpa-compliance": "หน้าที่ตามกฎหมาย และการควบคุมทางเทคนิค",
    "07-operations": "เฝ้าระวัง สำรองข้อมูล และแก้ปัญหา",
    "08-nvr-integration": "การซิงก์เวลา และขั้นตอนของเจ้าหน้าที่",
    "09-multi-site-runbook": "เช็กลิสต์เปิดโกดังใหม่ตั้งแต่สำรวจถึงส่งมอบ",
    "10-chokepoint-design": "สเปกกล้อง ไฟส่องสว่าง และเกณฑ์ตรวจรับงานติดตั้ง",
}

# เล่มที่สำคัญเป็นพิเศษ เน้นในหน้า index
KEY_DOCS = {"05-thai-lpr", "08-nvr-integration", "09-multi-site-runbook", "10-chokepoint-design"}


# ============================================================
#  CSS — ชุดเดียวกับเล่ม 10
# ============================================================
CSS = r"""
:root {
  --ground:#F5F4F2; --surface:#FFFFFF; --surface-2:#EEECE8;
  --ink:#191714; --ink-2:#57524B; --ink-3:#8A837A;
  --rule:#DEDAD4; --rule-strong:#C4BEB5;
  --accent:#A66A00; --accent-ink:#7A4E00; --accent-wash:#F8F0DE;
  --pass:#216B45; --pass-wash:#E4F0E9; --fail:#A32219; --fail-wash:#F8E6E4;
  --shadow:0 1px 2px rgba(25,23,20,.05), 0 8px 24px -16px rgba(25,23,20,.28);
  --font-th:"Leelawadee UI","Noto Sans Thai","Thonburi","Sarabun","Segoe UI",Tahoma,system-ui,sans-serif;
  --font-mono:ui-monospace,"SF Mono","Cascadia Mono","Segoe UI Mono","Roboto Mono",Menlo,Consolas,monospace;
  --measure:70ch; --rail:16rem;
}
@media (prefers-color-scheme: dark) {
  :root {
    --ground:#141210; --surface:#1C1917; --surface-2:#24201C;
    --ink:#EFEAE2; --ink-2:#ADA49A; --ink-3:#7C736A;
    --rule:#332D27; --rule-strong:#4A423A;
    --accent:#E0A648; --accent-ink:#EFC077; --accent-wash:#2C2313;
    --pass:#5FBF8C; --pass-wash:#16301F; --fail:#E5796C; --fail-wash:#331A17;
    --shadow:0 1px 2px rgba(0,0,0,.4), 0 8px 24px -16px rgba(0,0,0,.7);
  }
}
:root[data-theme="dark"] {
  --ground:#141210; --surface:#1C1917; --surface-2:#24201C;
  --ink:#EFEAE2; --ink-2:#ADA49A; --ink-3:#7C736A;
  --rule:#332D27; --rule-strong:#4A423A;
  --accent:#E0A648; --accent-ink:#EFC077; --accent-wash:#2C2313;
  --pass:#5FBF8C; --pass-wash:#16301F; --fail:#E5796C; --fail-wash:#331A17;
  --shadow:0 1px 2px rgba(0,0,0,.4), 0 8px 24px -16px rgba(0,0,0,.7);
}
:root[data-theme="light"] {
  --ground:#F5F4F2; --surface:#FFFFFF; --surface-2:#EEECE8;
  --ink:#191714; --ink-2:#57524B; --ink-3:#8A837A;
  --rule:#DEDAD4; --rule-strong:#C4BEB5;
  --accent:#A66A00; --accent-ink:#7A4E00; --accent-wash:#F8F0DE;
  --pass:#216B45; --pass-wash:#E4F0E9; --fail:#A32219; --fail-wash:#F8E6E4;
  --shadow:0 1px 2px rgba(25,23,20,.05), 0 8px 24px -16px rgba(25,23,20,.28);
}

* { box-sizing:border-box; }
body {
  margin:0; background:var(--ground); color:var(--ink);
  font-family:var(--font-th); font-size:16.5px; line-height:1.85;
  -webkit-font-smoothing:antialiased; text-rendering:optimizeLegibility;
}
:where(a){ color:var(--accent-ink); text-decoration-color:color-mix(in srgb, var(--accent) 40%, transparent); text-underline-offset:.22em; }
:where(a):hover{ text-decoration-color:var(--accent); }
:where(a,button,input,summary):focus-visible{ outline:2px solid var(--accent); outline-offset:3px; border-radius:3px; }

.page{ display:grid; grid-template-columns:var(--rail) minmax(0,1fr); gap:3.5rem;
       max-width:78rem; margin:0 auto; padding:0 2rem 6rem; align-items:start; }
.main{ min-width:0; max-width:var(--measure); }
@media (max-width:60rem){ .page{ grid-template-columns:minmax(0,1fr); gap:0; padding:0 1.15rem 4rem; } .main{ max-width:none; } }

.masthead{ grid-column:1/-1; border-bottom:1px solid var(--rule);
           padding:3.5rem 0 2rem; margin-bottom:2.5rem; display:flex; flex-direction:column; gap:.9rem; }
.masthead__kicker{ font-family:var(--font-mono); font-size:.72rem; letter-spacing:.14em;
                   text-transform:uppercase; color:var(--ink-3); display:flex; flex-wrap:wrap; gap:.55rem .9rem; align-items:center; }
.masthead__kicker b{ color:var(--accent-ink); font-weight:600; }
.masthead__kicker a{ color:var(--ink-3); text-decoration:none; }
.masthead__kicker a:hover{ color:var(--accent-ink); }
.masthead h1{ margin:0; font-size:clamp(1.8rem,1.2rem + 2.2vw,2.5rem); line-height:1.35;
              font-weight:700; letter-spacing:-.005em; text-wrap:balance; }
.masthead__sub{ margin:0; max-width:56ch; color:var(--ink-2); font-size:1.02rem; }

.toc{ position:sticky; top:2rem; font-size:.875rem; line-height:1.6;
      border-left:1px solid var(--rule); padding-left:1.1rem; }
.toc__label{ font-family:var(--font-mono); font-size:.68rem; letter-spacing:.14em;
             text-transform:uppercase; color:var(--ink-3); margin-bottom:.85rem; }
.toc ol{ list-style:none; margin:0; padding:0; display:flex; flex-direction:column; gap:.1rem; }
.toc li{ margin:0; }
.toc a{ display:block; padding:.3rem 0; color:var(--ink-2); text-decoration:none; border-radius:2px; }
.toc a:hover{ color:var(--ink); }
.toc a.is-active{ color:var(--accent-ink); font-weight:600; }
@media (max-width:60rem){
  .toc{ position:static; border-left:0; border:1px solid var(--rule); border-radius:3px;
        background:var(--surface); padding:1rem 1.1rem; margin-bottom:2.5rem; }
  .toc ol{ columns:2; column-gap:1.5rem; }
  .toc li{ break-inside:avoid; }
}

h2{ margin:3.4rem 0 1.5rem; padding-bottom:.55rem; border-bottom:2px solid var(--ink);
    font-size:clamp(1.25rem,1.1rem + .7vw,1.5rem); line-height:1.4; font-weight:700;
    text-wrap:balance; scroll-margin-top:1.5rem; }
.main > h2:first-child{ margin-top:0; }
h3{ margin:2.6rem 0 .9rem; font-size:1.06rem; font-weight:700; line-height:1.5; scroll-margin-top:1.5rem; }
h4{ margin:1.9rem 0 .6rem; font-size:.95rem; font-weight:700; color:var(--ink-2); }
p{ margin:0 0 1rem; }
strong{ font-weight:700; }
ul,ol{ margin:0 0 1.1rem; padding-left:1.35rem; }
li{ margin-bottom:.4rem; }
li:last-child{ margin-bottom:0; }
li > ul, li > ol { margin-top:.4rem; }
hr{ border:0; border-top:1px solid var(--rule); margin:2.5rem 0; }

/* ★ ในต้นฉบับ = ระดับความสำคัญ */
.star{ color:var(--accent); font-size:.9em; }

blockquote{ margin:1.4rem 0; padding:.1rem 0 .1rem 1.1rem;
            border-left:3px solid var(--rule-strong); color:var(--ink-2); font-size:.96rem; }
blockquote strong{ color:var(--ink); }
blockquote > :last-child{ margin-bottom:0; }
/* blockquote ที่ขึ้นต้นด้วย ★ = คำเตือนสำคัญ */
blockquote.is-flag{ background:var(--surface); border:1px solid var(--rule);
                    border-left:4px solid var(--accent); border-radius:2px;
                    padding:1.05rem 1.25rem; box-shadow:var(--shadow); color:var(--ink); }

.tw{ overflow-x:auto; margin:1.4rem 0; border:1px solid var(--rule); border-radius:3px;
     background:var(--surface); -webkit-overflow-scrolling:touch; }
table{ border-collapse:collapse; width:100%; font-size:.93rem; line-height:1.65;
       font-variant-numeric:tabular-nums; }
thead th{ background:var(--surface-2); text-align:left; font-weight:700; font-size:.8rem;
          color:var(--ink-2); padding:.6rem .85rem; border-bottom:1px solid var(--rule-strong); white-space:nowrap; }
td{ padding:.62rem .85rem; border-bottom:1px solid var(--rule); vertical-align:top; text-align:left; }
tbody tr:last-child td{ border-bottom:0; }
td:empty{ min-width:4rem; }

code{ font-family:var(--font-mono); font-size:.875em; background:var(--surface-2);
      padding:.1em .38em; border-radius:2px; }
pre{ font-family:var(--font-mono); font-size:.85rem; line-height:1.7; background:var(--surface);
     border:1px solid var(--rule); border-radius:3px; padding:1rem 1.15rem; margin:1.3rem 0;
     overflow-x:auto; color:var(--ink); font-variant-numeric:tabular-nums; }
pre code{ background:none; padding:0; font-size:1em; white-space:pre; }

/* เช็กลิสต์ติ๊กได้ จำสถานะไว้ — ใช้ li.chk เพื่อรองรับรายการซ้อนชั้น */
ul:has(> li.chk){ list-style:none; margin:1.1rem 0 1.5rem; padding:0; }
li.chk > ul{ margin:.3rem 0 .5rem 1.8rem; }
li.chk{ margin:0; }
li.chk > label{ display:flex; gap:.7rem; align-items:flex-start; padding:.5rem .7rem;
                border-radius:3px; cursor:pointer; transition:background .12s ease; }
li.chk > label:hover{ background:var(--surface-2); }
li.chk input{ appearance:none; flex:0 0 auto; width:1.05rem; height:1.05rem; margin:.38rem 0 0;
              border:1.5px solid var(--rule-strong); border-radius:2px; background:var(--surface);
              cursor:pointer; position:relative; }
li.chk input:checked{ background:var(--pass); border-color:var(--pass); }
li.chk input:checked::after{ content:""; position:absolute; left:5px; top:1px; width:4px; height:9px;
                             border:solid var(--surface); border-width:0 2px 2px 0; transform:rotate(42deg); }
li.chk input:checked + span{ color:var(--ink-3); text-decoration:line-through; text-decoration-thickness:1px; }
li.chk > label > span{ flex:1; }
.checks-reset{ font-family:var(--font-mono); font-size:.72rem; letter-spacing:.06em; background:none;
               border:1px solid var(--rule); border-radius:2px; color:var(--ink-3);
               padding:.3rem .65rem; cursor:pointer; margin:.4rem 0 1.5rem; display:inline-block; }
.checks-reset:hover{ color:var(--ink); border-color:var(--rule-strong); }

/* หน้า index */
.cards{ list-style:none; padding:0; margin:0; display:grid; gap:.65rem; }
.cards li{ margin:0; }
.cards a{ display:flex; gap:1rem; align-items:baseline; text-decoration:none; color:inherit;
          background:var(--surface); border:1px solid var(--rule); border-radius:3px;
          padding:.9rem 1.1rem; transition:border-color .12s ease, transform .12s ease; }
.cards a:hover{ border-color:var(--accent); transform:translateX(2px); }
.cards .n{ font-family:var(--font-mono); font-size:.85rem; color:var(--ink-3); font-variant-numeric:tabular-nums; }
.cards .t{ font-weight:700; display:block; margin-bottom:.1rem; }
.cards .d{ color:var(--ink-2); font-size:.92rem; display:block; line-height:1.6; }
.cards .is-key .t{ color:var(--accent-ink); }
.cards a.is-key{ border-left:3px solid var(--accent); }

@media print{
  :root{ --ground:#fff; --surface:#fff; --surface-2:#f2f2f2; --ink:#000; --ink-2:#333; --ink-3:#666;
         --rule:#bbb; --rule-strong:#888; --accent:#000; --accent-ink:#000; --accent-wash:#f4f4f4; --shadow:none; }
  body{ font-size:10.5pt; line-height:1.65; }
  .toc,.checks-reset{ display:none; }
  .page{ display:block; max-width:none; padding:0; }
  h2,h3,h4{ page-break-after:avoid; }
  .tw,pre,blockquote{ page-break-inside:avoid; }
  a{ text-decoration:none; color:#000; }
}
@media (prefers-reduced-motion: reduce){ *{ animation:none !important; transition:none !important; scroll-behavior:auto !important; } }
html{ scroll-behavior:smooth; }
"""


JS = r"""
(function () {
  "use strict";
  var KEY = "docs-checks-" + (document.body.getAttribute("data-doc") || "x");
  var boxes = Array.prototype.slice.call(document.querySelectorAll("li.chk input"));
  var saved = {};
  try { saved = JSON.parse(localStorage.getItem(KEY)) || {}; } catch (e) { saved = {}; }
  boxes.forEach(function (box, i) {
    if (saved[i]) box.checked = true;
    box.addEventListener("change", function () {
      saved[i] = box.checked;
      try { localStorage.setItem(KEY, JSON.stringify(saved)); } catch (e) {}
    });
  });
  var reset = document.querySelector(".checks-reset");
  if (reset) reset.addEventListener("click", function () {
    boxes.forEach(function (b) { b.checked = false; });
    saved = {};
    try { localStorage.removeItem(KEY); } catch (e) {}
  });

  var links = {};
  Array.prototype.forEach.call(document.querySelectorAll(".toc a"), function (a) {
    links[a.getAttribute("href").slice(1)] = a;
  });
  var heads = document.querySelectorAll(".main h2");
  if (!heads.length || !window.IntersectionObserver) return;
  var io = new IntersectionObserver(function (entries) {
    entries.forEach(function (e) {
      var link = links[e.target.id];
      if (!link || !e.isIntersecting) return;
      Object.keys(links).forEach(function (k) { links[k].classList.remove("is-active"); });
      link.classList.add("is-active");
    });
  }, { rootMargin: "-8% 0px -80% 0px" });
  Array.prototype.forEach.call(heads, function (h) { io.observe(h); });
})();
"""


PAGE = """<!doctype html>
<html lang="th">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>{css}</style>
</head>
<body data-doc="{slug}">
<div class="page">
  <header class="masthead">
    <div class="masthead__kicker">
      {kicker}
    </div>
    <h1>{h1}</h1>
    {sub}
  </header>
  {toc}
  <main class="main">
{body}
  </main>
</div>
<script>{js}</script>
</body>
</html>
"""


def slugify(text: str) -> str:
    """ทำ id สำหรับหัวข้อ — คงอักษรไทยไว้ ตัดอักขระที่ใช้ใน id ไม่ได้"""
    t = re.sub(r"<[^>]+>", "", text)
    t = html.unescape(t).strip().lower()
    t = re.sub(r"[^\w฀-๿\-]+", "-", t)
    return re.sub(r"-{2,}", "-", t).strip("-") or "s"


def build_md() -> MarkdownIt:
    md = MarkdownIt("commonmark", {"html": False, "linkify": False})
    md.enable(["table", "strikethrough"])
    return md


def transform(rendered: str) -> tuple[str, list[tuple[str, str]]]:
    """ปรับ HTML ที่ได้จาก markdown-it ให้เข้ากับดีไซน์ และเก็บรายการหัวข้อไว้ทำสารบัญ"""

    # ลิงก์ .md -> .html
    rendered = re.sub(r'href="([^"]+?)\.md(#[^"]*)?"', r'href="\1.html\2"', rendered)

    # ตารางต้องเลื่อนแนวนอนได้เองบนจอแคบ ไม่ให้ทั้งหน้าเลื่อน
    rendered = rendered.replace("<table>", '<div class="tw"><table>')
    rendered = rendered.replace("</table>", "</table></div>")

    # ★ = ระดับความสำคัญ ไม่ใช่เครื่องประดับ
    rendered = rendered.replace("★", '<span class="star">★</span>')

    # blockquote ที่มี ★ หรือ "ห้าม" = คำเตือน ทำให้เด่นกว่าหมายเหตุทั่วไป
    def flag_quote(m: re.Match) -> str:
        inner = m.group(1)
        if 'class="star"' in inner or "ห้าม" in inner:
            return '<blockquote class="is-flag">' + inner + "</blockquote>"
        return m.group(0)

    rendered = re.sub(r"<blockquote>(.*?)</blockquote>", flag_quote, rendered, flags=re.S)

    # - [ ] -> ช่องติ๊กจริง ใช้ตอนเดินสำรวจหน้างาน
    #
    # group 2 ต้องหยุดก่อน <li> </li> และก่อน <ul> <ol> ของ list ย่อย
    # ไม่งั้น list ย่อยจะถูกดูดเข้าไปอยู่ใน <span> ของ label
    has_checks = "<li>[ ]" in rendered or "<li>[x]" in rendered

    def to_check(m: re.Match) -> str:
        checked = " checked" if m.group(1).lower() == "x" else ""
        return (
            f'<li class="chk"><label><input type="checkbox"{checked}>'
            f"<span>{m.group(2).rstrip()}</span></label>"
        )

    rendered = re.sub(
        r"<li>\[([ xX])\]\s*((?:(?!</?li[ >]|<[uo]l[ >]).)*)",
        to_check,
        rendered,
        flags=re.S,
    )

    # id ให้หัวข้อ + เก็บสารบัญจาก h2
    toc: list[tuple[str, str]] = []
    seen: dict[str, int] = {}

    def add_id(m: re.Match) -> str:
        level, inner = m.group(1), m.group(2)
        sid = slugify(inner)
        seen[sid] = seen.get(sid, 0) + 1
        if seen[sid] > 1:
            sid = f"{sid}-{seen[sid]}"
        if level == "2":
            plain = html.unescape(re.sub(r"<[^>]+>", "", inner)).strip()
            toc.append((sid, plain))
        return f'<h{level} id="{sid}">{inner}</h{level}>'

    rendered = re.sub(r"<h([234])>(.*?)</h\1>", add_id, rendered, flags=re.S)

    if has_checks:
        rendered += '\n<button class="checks-reset" type="button">ล้างเครื่องหมายทั้งหมด</button>\n'

    return rendered, toc


def convert(path: Path) -> None:
    slug = path.stem
    raw = path.read_text(encoding="utf-8")

    # ดึง h1 และคำโปรยออกจากตัวเอกสาร ไปอยู่ใน masthead แทน
    lines = raw.split("\n")
    h1 = slug
    start = 0
    for i, line in enumerate(lines):
        if line.startswith("# "):
            h1 = line[2:].strip()
            start = i + 1
            break
    body_md = "\n".join(lines[start:]).lstrip("\n")

    # blockquote ก้อนแรกที่ติดหัวเรื่อง = คำโปรย
    sub = ""
    bq: list[str] = []
    rest = body_md.split("\n")
    while rest and rest[0].startswith(">"):
        bq.append(rest.pop(0).lstrip("> ").rstrip())
    if bq:
        body_md = "\n".join(rest).lstrip("\n")
        md_inline = build_md()
        # ต้องผ่าน transform ด้วย ไม่งั้นลิงก์ .md ในคำโปรยไม่ถูกเขียนใหม่
        sub_html, _ = transform(md_inline.render(" ".join(bq)).strip())
        sub_html = re.sub(r"^<p>|</p>$", "", sub_html.strip())
        sub = f'<p class="masthead__sub">{sub_html}</p>'

    md = build_md()
    body, toc = transform(md.render(body_md))

    toc_html = ""
    if len(toc) >= 3:
        items = "\n".join(
            f'      <li><a href="#{sid}">{html.escape(txt)}</a></li>' for sid, txt in toc
        )
        toc_html = (
            '<nav class="toc" aria-label="สารบัญ">\n'
            '    <div class="toc__label">สารบัญ</div>\n'
            f"    <ol>\n{items}\n    </ol>\n"
            "  </nav>"
        )

    num = slug.split("-")[0]
    kicker = (
        f"<span>เอกสาร {num}</span><span>·</span>"
        "<span>ระบบดัชนีสืบค้นภาพจากกล้องวงจรปิด</span><span>·</span>"
        '<a href="index.html"><b>สารบัญทั้งหมด</b></a>'
    )

    OUT.joinpath(f"{slug}.html").write_text(
        PAGE.format(
            title=f"{h1} — ระบบดัชนีสืบค้นภาพ",
            css=CSS,
            js=JS,
            slug=slug,
            kicker=kicker,
            h1=html.escape(h1.split("—")[-1].strip() if "—" in h1 else h1),
            sub=sub,
            toc=toc_html,
            body=body,
        ),
        encoding="utf-8",
    )
    print(f"  {slug}.html")


def build_index(slugs: list[str]) -> None:
    items = []
    for slug in slugs:
        num = slug.split("-")[0]
        title = BLURB.get(slug, "")
        name = slug.split("-", 1)[1].replace("-", " ")
        label = {
            "00-decisions": "บันทึกการตัดสินใจ",
            "01-architecture": "สถาปัตยกรรมระบบ",
            "02-hardware-sizing": "จัดขนาดเครื่องและพื้นที่",
            "03-camera-onboarding": "การนำกล้องเข้าระบบ",
            "04-data-model": "โครงสร้างฐานข้อมูล",
            "05-thai-lpr": "การอ่านป้ายทะเบียนไทย",
            "06-pdpa-compliance": "การปฏิบัติตาม PDPA",
            "07-operations": "การดูแลระบบ",
            "08-nvr-integration": "การเชื่อมต่อกับ NVR",
            "09-multi-site-runbook": "Runbook เปิดโกดังใหม่",
            "10-chokepoint-design": "การออกแบบจุดคอขวด",
        }.get(slug, name)
        key = " is-key" if slug in KEY_DOCS else ""
        items.append(
            f'      <li><a class="cards-a{key}" href="{slug}.html">'
            f'<span class="n">{num}</span>'
            f'<span><span class="t">{label}</span>'
            f'<span class="d">{title}</span></span></a></li>'
        )

    body = (
        '<ul class="cards">\n' + "\n".join(items) + "\n    </ul>\n"
        '<p style="margin-top:2rem;color:var(--ink-2);font-size:.95rem">'
        'เล่มที่ขีดเส้นสีไว้คือเล่มที่มีผลต่อความสำเร็จมากที่สุด '
        'ช่างติดตั้งอ่านเล่ม <a href="10-chokepoint-design.html">10</a> ก่อน</p>'
    )

    OUT.joinpath("index.html").write_text(
        PAGE.format(
            title="เอกสารระบบดัชนีสืบค้นภาพจากกล้องวงจรปิด",
            css=CSS + "\n.cards a.is-key{border-left:3px solid var(--accent);}",
            js=JS,
            slug="index",
            kicker="<span>ระบบดัชนีสืบค้นภาพจากกล้องวงจรปิด</span><span>·</span><b>เอกสารทั้งหมด</b>",
            h1="เอกสารโครงการ",
            sub='<p class="masthead__sub">ระบบช่วยจำกัดวงการค้นหาสำหรับโกดังที่มี NVR อยู่แล้ว '
            "วิเคราะห์ภาพจากกล้องจุดคอขวดเพื่อบอกว่าใครหรืออะไรผ่านเข้า–ออกเมื่อไร</p>",
            toc="",
            body=body,
        ),
        encoding="utf-8",
    )
    print("  index.html")


def main() -> None:
    OUT.mkdir(exist_ok=True)
    print(f"แปลงเอกสาร -> {OUT}")

    slugs = []
    for path in sorted(DOCS.glob("*.md")):
        slugs.append(path.stem)
        if path.stem in HANDMADE:
            src = DOCS / f"{path.stem}.html"
            if src.exists():
                shutil.copyfile(src, OUT / f"{path.stem}.html")
                print(f"  {path.stem}.html  (คัดลอกจากฉบับทำมือ)")
                continue
        convert(path)

    build_index(slugs)
    print(f"\nเสร็จ {len(slugs) + 1} ไฟล์")


if __name__ == "__main__":
    main()
