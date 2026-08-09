#!/usr/bin/env python3
"""
แปลงไฟล์ CSV เป็น HTML แบบอ่านง่ายและ interactive (Search, Sort, Pagination)
ป้องกันปัญหาคำภาษาอังกฤษโดนหักกลางตัวอักษร (เช่น false, true)
"""

import csv
import html
import json
from pathlib import Path

CSS = r"""
:root {
  --ground:#F5F4F2; --surface:#FFFFFF; --surface-2:#EEECE8;
  --ink:#191714; --ink-2:#57524B; --ink-3:#8A837A;
  --rule:#DEDAD4; --rule-strong:#C4BEB5;
  --accent:#A66A00; --accent-ink:#7A4E00; --accent-wash:#F8F0DE;
  --pass:#216B45; --pass-wash:#E4F0E9; 
  --warn:#9E6B00; --warn-wash:#FCF4E2;
  --fail:#A32219; --fail-wash:#F8E6E4;
  --shadow:0 1px 2px rgba(25,23,20,.05), 0 8px 24px -16px rgba(25,23,20,.28);
  --font-th:"Leelawadee UI","Noto Sans Thai","Thonburi","Sarabun","Segoe UI",Tahoma,system-ui,sans-serif;
  --font-mono:ui-monospace,"SF Mono","Cascadia Mono","Segoe UI Mono","Roboto Mono",Menlo,Consolas,monospace;
}
@media (prefers-color-scheme: dark) {
  :root {
    --ground:#141210; --surface:#1C1917; --surface-2:#24201C;
    --ink:#EFEAE2; --ink-2:#ADA49A; --ink-3:#7C736A;
    --rule:#332D27; --rule-strong:#4A423A;
    --accent:#E0A648; --accent-ink:#EFC077; --accent-wash:#2C2313;
    --pass:#5FBF8C; --pass-wash:#16301F; 
    --warn:#F1B848; --warn-wash:#2D2311;
    --fail:#E5796C; --fail-wash:#331A17;
    --shadow:0 1px 2px rgba(0,0,0,.4), 0 8px 24px -16px rgba(0,0,0,.7);
  }
}

* { box-sizing:border-box; }
body {
  margin:0; background:var(--ground); color:var(--ink);
  font-family:var(--font-th); font-size:15px; line-height:1.6;
  -webkit-font-smoothing:antialiased;
}
.container {
  max-width: 1400px; margin: 0 auto; padding: 2rem 1.5rem 4rem;
}
.masthead {
  border-bottom: 1px solid var(--rule); padding-bottom: 1.5rem; margin-bottom: 2rem;
}
.masthead__kicker {
  font-family: var(--font-mono); font-size: .75rem; letter-spacing: .1em;
  text-transform: uppercase; color: var(--ink-3); margin-bottom: .5rem;
}
.masthead h1 {
  margin: 0; font-size: 1.8rem; font-weight: 700; color: var(--ink);
}

.controls {
  display: flex; gap: 1rem; margin-bottom: 1.2rem; flex-wrap: wrap; align-items: center; justify-content: space-between;
}
.search-box {
  flex: 1; min-width: 250px;
}
.search-box input {
  width: 100%; padding: .55rem .85rem; font-family: var(--font-th); font-size: .95rem;
  background: var(--surface); color: var(--ink); border: 1px solid var(--rule-strong);
  border-radius: 4px; outline: none; transition: border-color .15s;
}
.search-box input:focus { border-color: var(--accent); }

.meta-info {
  font-size: .85rem; color: var(--ink-2); font-family: var(--font-mono);
}

.table-wrapper {
  overflow-x: auto; background: var(--surface); border: 1px solid var(--rule);
  border-radius: 4px; box-shadow: var(--shadow);
}

table {
  width: 100%; border-collapse: collapse; font-size: .92rem; text-align: left;
  table-layout: auto;
}

th {
  background: var(--surface-2); color: var(--ink-2); font-weight: 700; font-size: .8rem;
  padding: .75rem .9rem; border-bottom: 1px solid var(--rule-strong);
  cursor: pointer; user-select: none; white-space: nowrap;
}
th:hover { color: var(--accent-ink); }
th .sort-icon { margin-left: 4px; opacity: .4; font-size: .75rem; }
th.asc .sort-icon::after { content: "▲"; opacity: 1; }
th.desc .sort-icon::after { content: "▼"; opacity: 1; }
th:not(.asc):not(.desc) .sort-icon::after { content: "⇅"; }

td {
  padding: .65rem .9rem; border-bottom: 1px solid var(--rule); vertical-align: middle;
  line-height: 1.5; white-space: normal;
  /* ป้องกันการตัดคำกลางตัวอักษรภาษาอังกฤษ */
  word-break: normal;
  overflow-wrap: break-word;
}
tr:last-child td { border-bottom: 0; }
tr:hover td { background: color-mix(in srgb, var(--surface-2) 40%, transparent); }

/* Badge ป้องกันไม่ให้ขึ้นบรรทัดใหม่ หรือตัดคำในตัวมันเองเด็ดขาด */
.badge {
  display: inline-block; padding: .2rem .6rem; border-radius: 3px;
  font-size: .78rem; font-weight: 600; font-family: var(--font-mono);
  white-space: nowrap !important; word-break: keep-all !important;
  text-transform: uppercase; letter-spacing: 0.03em; text-align: center;
}
.badge-error, .badge-fail, .badge-false { background: var(--fail-wash); color: var(--fail); border: 1px solid color-mix(in srgb, var(--fail) 30%, transparent); }
.badge-warn, .badge-warning { background: var(--warn-wash); color: var(--warn); border: 1px solid color-mix(in srgb, var(--warn) 30%, transparent); }
.badge-success, .badge-pass, .badge-active, .badge-ok, .badge-true { background: var(--pass-wash); color: var(--pass); border: 1px solid color-mix(in srgb, var(--pass) 30%, transparent); }
.badge-info { background: var(--accent-wash); color: var(--accent-ink); border: 1px solid color-mix(in srgb, var(--accent) 30%, transparent); }

.code-text {
  font-family: var(--font-mono); font-size: .88em; white-space: nowrap !important; word-break: keep-all !important;
}

.pagination {
  display: flex; gap: .5rem; align-items: center; justify-content: flex-end; margin-top: 1.2rem;
}
.pagination button {
  background: var(--surface); border: 1px solid var(--rule); color: var(--ink);
  padding: .35rem .75rem; border-radius: 3px; cursor: pointer; font-family: var(--font-th);
}
.pagination button:disabled { opacity: .4; cursor: not-allowed; }
.pagination button:hover:not(:disabled) { border-color: var(--rule-strong); background: var(--surface-2); }
.page-num { font-size: .85rem; color: var(--ink-2); font-family: var(--font-mono); }
"""

JS = r"""
(function() {
  var headers = DATA.headers;
  var rows = DATA.rows;
  var filteredRows = rows.slice();
  var sortCol = -1;
  var sortAsc = true;
  var currentPage = 1;
  var pageSize = 25;

  var searchInput = document.getElementById('searchInput');
  var tableBody = document.getElementById('tableBody');
  var recordCount = document.getElementById('recordCount');
  var prevBtn = document.getElementById('prevBtn');
  var nextBtn = document.getElementById('nextBtn');
  var pageInfo = document.getElementById('pageInfo');

  function isIpAddress(str) {
    return /^(\d{1,3}\.){3}\d{1,3}$/.test(str);
  }

  function renderCellContent(val) {
    var str = String(val).trim();
    var lower = str.toLowerCase();

    // กรณี OK / PASS / TRUE / SUCCESS / ACTIVE
    if (lower === 'ok' || lower === 'pass' || lower === 'passed' || lower === 'success' || lower === 'active' || lower === 'true') {
      return '<span class="badge badge-ok">' + escapeHtml(str) + '</span>';
    }
    // กรณี ERROR / FAIL / FALSE
    if (lower === 'error' || lower === 'failed' || lower === 'fail' || lower === 'false') {
      return '<span class="badge badge-false">' + escapeHtml(str) + '</span>';
    }
    // กรณี WARNING / WARN
    if (lower === 'warning' || lower === 'warn') {
      return '<span class="badge badge-warn">' + escapeHtml(str) + '</span>';
    }
    // กรณี IP Address
    if (isIpAddress(str)) {
      return '<span class="code-text">' + escapeHtml(str) + '</span>';
    }

    return escapeHtml(str);
  }

  function renderTable() {
    var start = (currentPage - 1) * pageSize;
    var end = start + pageSize;
    var pageData = filteredRows.slice(start, end);

    var html = '';
    if (pageData.length === 0) {
      html = '<tr><td colspan="' + headers.length + '" style="text-align:center; padding: 2rem; color: var(--ink-3);">ไม่พบข้อมูล</td></tr>';
    } else {
      pageData.forEach(function(row) {
        html += '<tr>';
        row.forEach(function(cell) {
          html += '<td>' + renderCellContent(cell) + '</td>';
        });
        html += '</tr>';
      });
    }
    tableBody.innerHTML = html;

    var totalPages = Math.ceil(filteredRows.length / pageSize) || 1;
    recordCount.textContent = 'แสดง ' + filteredRows.length + ' จากทั้งหมด ' + rows.length + ' รายการ';
    pageInfo.textContent = 'หน้า ' + currentPage + ' / ' + totalPages;
    prevBtn.disabled = currentPage === 1;
    nextBtn.disabled = currentPage >= totalPages;
  }

  function escapeHtml(text) {
    var div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }

  function filterData() {
    var query = searchInput.value.toLowerCase().trim();
    if (!query) {
      filteredRows = rows.slice();
    } else {
      filteredRows = rows.filter(function(row) {
        return row.some(function(cell) {
          return String(cell).toLowerCase().indexOf(query) !== -1;
        });
      });
    }
    currentPage = 1;
    sortData();
  }

  function sortData() {
    if (sortCol !== -1) {
      filteredRows.sort(function(a, b) {
        var valA = a[sortCol] || '';
        var valB = b[sortCol] || '';
        var numA = parseFloat(valA), numB = parseFloat(valB);
        if (!isNaN(numA) && !isNaN(numB)) {
          return sortAsc ? numA - numB : numB - numA;
        }
        return sortAsc ? valA.localeCompare(valB, 'th') : valB.localeCompare(valA, 'th');
      });
    }
    renderTable();
  }

  searchInput.addEventListener('input', filterData);

  document.querySelectorAll('th').forEach(function(th, idx) {
    th.addEventListener('click', function() {
      if (sortCol === idx) {
        sortAsc = !sortAsc;
      } else {
        sortCol = idx;
        sortAsc = true;
      }
      document.querySelectorAll('th').forEach(function(h) { h.className = ''; });
      th.className = sortAsc ? 'asc' : 'desc';
      sortData();
    });
  });

  prevBtn.addEventListener('click', function() {
    if (currentPage > 1) { currentPage--; renderTable(); }
  });

  nextBtn.addEventListener('click', function() {
    var totalPages = Math.ceil(filteredRows.length / pageSize);
    if (currentPage < totalPages) { currentPage++; renderTable(); }
  });

  renderTable();
})();
"""

def convert_csv_to_html(csv_path: Path, output_html_path: Path):
    headers = []
    rows = []
    
    with open(csv_path, mode='r', encoding='utf-8') as f:
        reader = csv.reader(f)
        try:
            headers = next(reader)
        except StopIteration:
            headers = []
        for row in reader:
            rows.append(row)

    title = csv_path.stem
    th_elements = "".join([f'<th>{html.escape(h)}<span class="sort-icon"></span></th>' for h in headers])
    data_json = json.dumps({"headers": headers, "rows": rows}, ensure_ascii=False)

    page_html = f"""<!doctype html>
<html lang="th">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} — CSV Viewer</title>
<style>{CSS}</style>
</head>
<body>
<div class="container">
  <header class="masthead">
    <div class="masthead__kicker">รายงานสรุปข้อมูล CSV</div>
    <h1>{html.escape(title)}</h1>
  </header>

  <div class="controls">
    <div class="search-box">
      <input type="text" id="searchInput" placeholder="พิมพ์เพื่อค้นหาข้อมูล...">
    </div>
    <div class="meta-info" id="recordCount"></div>
  </div>

  <div class="table-wrapper">
    <table>
      <thead>
        <tr>{th_elements}</tr>
      </thead>
      <tbody id="tableBody"></tbody>
    </table>
  </div>

  <div class="pagination">
    <button id="prevBtn" type="button">◄ ก่อนหน้า</button>
    <span class="page-num" id="pageInfo"></span>
    <button id="nextBtn" type="button">ถัดไป ►</button>
  </div>
</div>

<script>
var DATA = {data_json};
{JS}
</script>
</body>
</html>
"""

    output_html_path.write_text(page_html, encoding="utf-8")
    print(f"แปลงไฟล์สำเร็จ: {output_html_path}")

if __name__ == "__main__":
    convert_csv_to_html(Path("result.csv"), Path("result.html"))