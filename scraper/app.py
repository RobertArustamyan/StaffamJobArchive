import html as html_lib
import json
import os
import subprocess
import sys

from flask import Flask, redirect, render_template_string, request, url_for

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
DATA_DIR    = os.path.join(BASE_DIR, "data")
JOBS_PATH   = os.path.join(DATA_DIR, "jobs.json")
LOG_PATH    = os.path.join(DATA_DIR, "runs.log")
PID_PATH    = os.path.join(DATA_DIR, ".scraper.pid")

# Fields shown as table columns (description is always in the expandable row)
DISPLAY_FIELDS = [
    "title", "company", "location", "job_type",
    "employment_term", "category", "deadline", "url", "scraped_date",
]

ALL_CONFIG_FIELDS = [
    "title", "company", "location", "job_type",
    "employment_term", "category", "deadline", "url", "description",
]

DEFAULT_CONFIG = {"url": "https://staff.am/en/jobs", "fields": [], "keywords": ""}

app = Flask(__name__)


# ── helpers ───────────────────────────────────────────────────────────────────

def ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "w") as f:
            json.dump(DEFAULT_CONFIG, f, indent=2)
    if not os.path.exists(LOG_PATH):
        open(LOG_PATH, "w").close()


def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def save_config(cfg):
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)


def load_jobs():
    if not os.path.exists(JOBS_PATH):
        return []
    try:
        with open(JOBS_PATH) as f:
            return json.load(f)
    except Exception:
        return []


def is_scraper_running():
    if not os.path.exists(PID_PATH):
        return False
    try:
        with open(PID_PATH) as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)
        return True
    except Exception:
        try: os.remove(PID_PATH)
        except Exception: pass
        return False


# ── template ──────────────────────────────────────────────────────────────────

PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Job Scraper</title>
{% if refresh %}<meta http-equiv="refresh" content="5">{% endif %}
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: system-ui, sans-serif; background: #f0f2f5; color: #222; }

header {
  background: #1a1a2e; color: #fff; padding: 12px 24px;
  display: flex; align-items: center; gap: 24px;
}
header h1 { font-size: 1.15rem; }
header a { color: #aad4f5; text-decoration: none; font-size: .9rem; }
header a:hover { text-decoration: underline; }

.content { padding: 20px 24px; max-width: 1600px; margin: 0 auto; }

/* banners */
.banner {
  padding: 10px 16px; border-radius: 6px; margin-bottom: 14px;
  display: flex; align-items: center; gap: 10px; font-size: .875rem;
}
.banner.info  { background: #d4edda; border: 1px solid #c3e6cb; color: #155724; }
.banner.warn  { background: #fff3cd; border: 1px solid #ffc107; color: #856404; }
.banner.error { background: #f8d7da; border: 1px solid #f5c6cb; color: #721c24; }
.spinner {
  width: 14px; height: 14px; border: 2px solid currentColor;
  border-top-color: transparent; border-radius: 50%;
  animation: spin .8s linear infinite; flex-shrink: 0;
}
@keyframes spin { to { transform: rotate(360deg); } }

/* toolbar */
.toolbar {
  display: flex; align-items: center; gap: 10px;
  flex-wrap: wrap; margin-bottom: 14px;
}
button, .btn {
  background: #1a1a2e; color: #fff; border: none; padding: 8px 16px;
  border-radius: 5px; cursor: pointer; font-size: .875rem; white-space: nowrap;
}
button:hover:not(:disabled) { background: #2e2e5e; }
button:disabled { background: #999; cursor: not-allowed; }
.btn-outline {
  background: transparent; color: #1a1a2e;
  border: 1.5px solid #1a1a2e; padding: 7px 15px;
}
.btn-outline:hover { background: #1a1a2e; color: #fff; }

/* column picker dropdown */
.col-picker-wrap { position: relative; }
.col-picker-panel {
  display: none; position: absolute; top: calc(100% + 6px); left: 0;
  background: #fff; border: 1px solid #ccc; border-radius: 6px;
  padding: 12px 16px; z-index: 100; box-shadow: 0 4px 12px rgba(0,0,0,.12);
  min-width: 220px;
}
.col-picker-panel.open { display: block; }
.col-picker-panel label {
  display: flex; align-items: center; gap: 8px;
  font-size: .875rem; padding: 4px 0; cursor: pointer;
}

/* date filter */
.filter-group { display: flex; align-items: center; gap: 8px; font-size: .875rem; }
.filter-group input[type=date] {
  padding: 7px 10px; border: 1.5px solid #ccc; border-radius: 5px; font-size: .875rem;
}
.filter-group button { padding: 7px 12px; background: #888; }

/* stats bar */
.stats { font-size: .85rem; color: #555; margin-bottom: 10px; }

/* table */
.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; background: #fff;
        box-shadow: 0 1px 4px rgba(0,0,0,.1); border-radius: 6px; overflow: hidden; }
thead th {
  background: #1a1a2e; color: #fff; padding: 10px 12px;
  text-align: left; font-size: .8rem; text-transform: uppercase;
  letter-spacing: .05em; white-space: nowrap; user-select: none;
}
thead th.sortable { cursor: pointer; }
thead th.sortable:hover { background: #2e2e5e; }
.sort-ind { margin-left: 5px; opacity: .6; font-size: .75rem; }

tbody tr.job-row {
  cursor: pointer; transition: background .15s;
  border-bottom: 1px solid #eee;
}
tbody tr.job-row:hover { background: #f0f4ff !important; }
tbody td {
  padding: 8px 12px; font-size: .85rem; vertical-align: middle;
  max-width: 260px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
tbody td a { color: #0066cc; }

/* expandable detail row */
tr.detail-row td {
  padding: 0; background: #f8f9ff;
  border-bottom: 2px solid #c5cae9;
  max-width: none; overflow: visible; white-space: normal;
}
.desc-inner {
  padding: 16px 20px; font-size: .85rem; line-height: 1.6;
  white-space: pre-wrap; word-break: break-word; max-width: 100%;
}
tr.job-row.expanded { background: #eef0ff !important; }

/* empty */
.empty { text-align: center; padding: 56px; color: #888; }
.empty a { color: #0066cc; }

/* config page */
.form-wrap { background: #fff; padding: 24px; border-radius: 8px;
             box-shadow: 0 1px 4px rgba(0,0,0,.1); max-width: 700px; }
.form-wrap label { display: block; margin-bottom: 6px; font-size: .9rem; font-weight: 500; }
.form-wrap input[type=text], .form-wrap input[type=url] {
  width: 100%; padding: 8px 10px; border: 1.5px solid #ccc;
  border-radius: 5px; font-size: .9rem; margin-bottom: 16px;
}
.checkbox-grid { display: flex; flex-wrap: wrap; gap: 10px 28px; margin-bottom: 16px; }
.checkbox-grid label {
  display: flex; align-items: center; gap: 6px;
  font-size: .9rem; font-weight: 400; cursor: pointer;
}
.note { font-size: .78rem; color: #b05000; }

pre { background: #1a1a2e; color: #cff; padding: 16px; border-radius: 6px;
      overflow-x: auto; font-size: .8rem; line-height: 1.5; }
h2 { margin-bottom: 16px; font-size: 1.05rem; }
</style>
</head>
<body>
<header>
  <h1>Job Scraper</h1>
  <a href="/">Jobs</a>
  <a href="/config">Config</a>
  <a href="/log">Run Log</a>
</header>
<div class="content">

{% if running %}
<div class="banner warn">
  <div class="spinner"></div>
  Scraping in progress — page refreshes every 5 s as new jobs arrive.
</div>
{% endif %}

{% if flash_msg %}
<div class="banner {{ 'error' if flash_error else 'info' }}">{{ flash_msg }}</div>
{% endif %}

{{ body | safe }}
</div>

<script>
/* ── column visibility ─────────────────────────────────────────────────── */
function toggleColPanel() {
  document.getElementById('colPanel').classList.toggle('open');
}
document.addEventListener('click', e => {
  const wrap = document.getElementById('colWrap');
  if (wrap && !wrap.contains(e.target))
    document.getElementById('colPanel').classList.remove('open');
});

function updateColumns() {
  document.querySelectorAll('.col-toggle').forEach(cb => {
    const f = cb.dataset.field;
    const show = cb.checked;
    document.querySelectorAll(`[data-field="${f}"]`).forEach(el => {
      el.style.display = show ? '' : 'none';
    });
    const th = document.querySelector(`th[data-field="${f}"]`);
    if (th) th.style.display = show ? '' : 'none';
    // Recalculate detail row colspan
    updateDetailColspan();
    saveColPrefs();
  });
}

function updateDetailColspan() {
  const total = document.querySelectorAll('thead th:not([style*="display: none"]):not([style*="display:none"])').length;
  document.querySelectorAll('tr.detail-row td').forEach(td => td.colSpan = total || 1);
}

function saveColPrefs() {
  const prefs = {};
  document.querySelectorAll('.col-toggle').forEach(cb => { prefs[cb.dataset.field] = cb.checked; });
  try { localStorage.setItem('staffScraperCols', JSON.stringify(prefs)); } catch(e){}
}

function loadColPrefs() {
  try {
    const saved = JSON.parse(localStorage.getItem('staffScraperCols') || 'null');
    if (!saved) return;
    document.querySelectorAll('.col-toggle').forEach(cb => {
      if (cb.dataset.field in saved) cb.checked = saved[cb.dataset.field];
    });
    updateColumns();
  } catch(e){}
}

/* ── row expand / collapse ─────────────────────────────────────────────── */
function toggleDetail(row, e) {
  if (e && e.target.tagName === 'A') return; // don't intercept link clicks
  const detail = row.nextElementSibling;
  if (!detail || !detail.classList.contains('detail-row')) return;
  const open = detail.style.display !== 'none';
  detail.style.display = open ? 'none' : '';
  row.classList.toggle('expanded', !open);
}

/* ── color by deadline ─────────────────────────────────────────────────── */
let colorActive = false;
function colorByDeadline() {
  colorActive = !colorActive;
  document.querySelectorAll('tr.job-row').forEach(row => {
    if (!colorActive) { row.style.background = ''; return; }
    const cell = row.querySelector('[data-field="deadline"]');
    const text = cell ? cell.textContent.trim() : '';
    if (!text || text === '—') return;
    const d = new Date(text);
    if (isNaN(d.getTime())) return;
    const today = new Date(); today.setHours(0,0,0,0);
    d.setHours(0,0,0,0);
    if (d < today)                      row.style.background = '#ffe0e0';
    else if (d.getTime() === today.getTime()) row.style.background = '#fff3cd';
    else                                row.style.background = '#e8f5e9';
  });
  document.getElementById('colorBtn').textContent =
    colorActive ? 'Remove Colors' : 'Color by Deadline';
}

/* ── sort ──────────────────────────────────────────────────────────────── */
const sortState = {};
function sortTable(field) {
  const tbody = document.querySelector('table tbody');
  if (!tbody) return;
  const pairs = [];
  let cur = tbody.firstElementChild;
  while (cur) {
    if (cur.classList.contains('job-row')) {
      const det = cur.nextElementSibling;
      pairs.push({ row: cur, detail: det && det.classList.contains('detail-row') ? det : null });
      cur = det && det.classList.contains('detail-row') ? det.nextElementSibling : cur.nextElementSibling;
    } else { cur = cur.nextElementSibling; }
  }
  const dir = sortState[field] === 'asc' ? -1 : 1;
  sortState[field] = dir === 1 ? 'asc' : 'desc';

  pairs.sort((a, b) => {
    const av = a.row.querySelector(`[data-field="${field}"]`)?.textContent.trim() || '';
    const bv = b.row.querySelector(`[data-field="${field}"]`)?.textContent.trim() || '';
    if (field === 'deadline') {
      const ad = av === '—' ? new Date(0) : new Date(av);
      const bd = bv === '—' ? new Date(0) : new Date(bv);
      if (isNaN(ad)) return dir; if (isNaN(bd)) return -dir;
      return dir * (ad - bd);
    }
    return dir * av.localeCompare(bv, undefined, {numeric: true});
  });

  pairs.forEach(p => {
    tbody.appendChild(p.row);
    if (p.detail) tbody.appendChild(p.detail);
  });

  document.querySelectorAll('th.sortable .sort-ind').forEach(el => el.textContent = ' ↕');
  const th = document.querySelector(`th[data-field="${field}"]`);
  if (th) th.querySelector('.sort-ind').textContent = sortState[field] === 'asc' ? ' ↑' : ' ↓';
}

/* ── filter by scraped date ────────────────────────────────────────────── */
function applyDateFilter() {
  const val = document.getElementById('dateFilter').value; // "2026-04-06" or ""
  let visible = 0;
  document.querySelectorAll('tr.job-row').forEach(row => {
    const cell = row.querySelector('[data-field="scraped_date"]');
    const date = cell ? cell.textContent.trim() : '';
    const show = !val || date === val;
    row.style.display = show ? '' : 'none';
    const det = row.nextElementSibling;
    if (det && det.classList.contains('detail-row')) det.style.display = 'none';
    if (show) visible++;
  });
  const stats = document.getElementById('visibleCount');
  if (stats) stats.textContent = val ? `Showing ${visible} of ${document.querySelectorAll('tr.job-row').length} jobs` : `Showing all ${document.querySelectorAll('tr.job-row').length} jobs`;
}

function clearDateFilter() {
  document.getElementById('dateFilter').value = '';
  applyDateFilter();
}

/* ── init ──────────────────────────────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', () => {
  loadColPrefs();
  updateDetailColspan();
});
</script>
</body>
</html>"""


def render_page(body, flash_msg="", flash_error=False, running=False):
    return render_template_string(
        PAGE, body=body,
        flash_msg=flash_msg, flash_error=flash_error,
        running=running, refresh=running,
    )


# ── routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    flash_msg   = request.args.get("msg", "")
    flash_error = request.args.get("err", "") == "1"
    running     = is_scraper_running()
    jobs        = load_jobs()
    jobs.sort(key=lambda j: j.get("scraped_at", ""), reverse=True)

    n_total = len(jobs)

    scrape_btn = (
        '<form method="post" action="/scrape" style="margin:0">'
        f'<button type="submit" {"disabled" if running else ""}>Run Scraper Now</button>'
        '</form>'
    )

    if not jobs:
        body = (
            f'<div class="toolbar">{scrape_btn}</div>'
            '<div class="empty"><p>No jobs scraped yet.</p>'
            '<p style="margin-top:10px"><a href="/config">Go to Config</a>'
            ' to set up and run the scraper.</p></div>'
        )
        return render_page(body, flash_msg=flash_msg,
                           flash_error=flash_error, running=running)

    # ── column picker checkboxes ──────────────────────────────────────────
    col_checks = ""
    for f in DISPLAY_FIELDS:
        label = f.replace("_", " ").title()
        col_checks += (
            f'<label>'
            f'<input type="checkbox" class="col-toggle" data-field="{f}" '
            f'checked onchange="updateColumns()"> {label}'
            f'</label>'
        )

    # ── table headers ────────────────────────────────────────────────────
    sortable = {"deadline", "scraped_date", "title", "company", "location"}
    ths = ""
    for f in DISPLAY_FIELDS:
        label = f.replace("_", " ").title()
        if f in sortable:
            ths += (
                f'<th class="sortable" data-field="{f}" '
                f'onclick="sortTable(\'{f}\')">'
                f'{label}<span class="sort-ind"> ↕</span></th>'
            )
        else:
            ths += f'<th data-field="{f}">{label}</th>'

    # ── table rows ────────────────────────────────────────────────────────
    rows_html = []
    for job in jobs:
        scraped_date = (job.get("scraped_at") or "")[:10]
        cells = []
        for f in DISPLAY_FIELDS:
            val = scraped_date if f == "scraped_date" else job.get(f)
            if val is None or val == "":
                cells.append(f'<td data-field="{f}">—</td>')
            elif f == "url":
                safe_url = html_lib.escape(str(val))
                cells.append(
                    f'<td data-field="{f}">'
                    f'<a href="{safe_url}" target="_blank" '
                    f'onclick="event.stopPropagation()">link</a></td>'
                )
            else:
                cells.append(
                    f'<td data-field="{f}">'
                    f'{html_lib.escape(str(val))}</td>'
                )

        desc_text = html_lib.escape(
            str(job.get("description") or "No description available.")
        )
        rows_html.append(
            '<tr class="job-row" onclick="toggleDetail(this,event)">'
            + "".join(cells)
            + "</tr>"
            + f'<tr class="detail-row" style="display:none">'
            f'<td colspan="{len(DISPLAY_FIELDS)}">'
            f'<div class="desc-inner">{desc_text}</div>'
            f'</td></tr>'
        )

    body = f"""
<div class="toolbar">
  {scrape_btn}
  <button id="colorBtn" class="btn-outline" onclick="colorByDeadline()">Color by Deadline</button>

  <div class="col-picker-wrap" id="colWrap">
    <button class="btn-outline" onclick="toggleColPanel()">Columns ▾</button>
    <div class="col-picker-panel" id="colPanel">
      <div class="checkbox-grid" style="flex-direction:column;gap:2px">
        {col_checks}
      </div>
    </div>
  </div>

  <div class="filter-group">
    <label for="dateFilter" style="white-space:nowrap;font-weight:500">Scraped date:</label>
    <input type="date" id="dateFilter" onchange="applyDateFilter()">
    <button onclick="clearDateFilter()" style="background:#888">Clear</button>
  </div>
</div>

<p class="stats" id="visibleCount">Showing all {n_total} jobs</p>

<div class="table-wrap">
<table>
  <thead><tr>{ths}</tr></thead>
  <tbody>{"".join(rows_html)}</tbody>
</table>
</div>"""

    return render_page(body, flash_msg=flash_msg,
                       flash_error=flash_error, running=running)


@app.route("/config", methods=["GET"])
def config_get():
    cfg    = load_config()
    active = set(cfg.get("fields", []))
    msg    = request.args.get("msg", "")

    checks = ""
    for f in ALL_CONFIG_FIELDS:
        checked = "checked" if f in active else ""
        note = (' <span class="note">(slow — visits each page)</span>'
                if f == "description" else "")
        checks += (
            f'<label>'
            f'<input type="checkbox" name="fields" value="{f}" {checked}>'
            f' {f}{note}</label>'
        )

    body = f"""
<div class="form-wrap">
  <h2>Scraper Configuration</h2>
  {"<div class='banner info' style='margin-bottom:12px'>" + msg + "</div>" if msg else ""}
  <form method="post" action="/config">
    <label>Target URL</label>
    <input type="url" name="url" value="{html_lib.escape(cfg.get('url',''))}">
    <label>Fields to extract</label>
    <div class="checkbox-grid">{checks}</div>
    <label>Keyword filter
      <span style="font-weight:400;color:#666">
        (comma-separated; leave blank for all)
      </span>
    </label>
    <input type="text" name="keywords"
           value="{html_lib.escape(cfg.get('keywords',''))}"
           placeholder="e.g. Python, Django">
    <button type="submit">Save Config</button>
  </form>
</div>"""
    return render_page(body)


@app.route("/config", methods=["POST"])
def config_post():
    save_config({
        "url":      request.form.get("url", "").strip(),
        "fields":   request.form.getlist("fields"),
        "keywords": request.form.get("keywords", "").strip(),
    })
    return redirect(url_for("config_get") + "?msg=Config+saved")


@app.route("/scrape", methods=["POST"])
def scrape():
    if is_scraper_running():
        return redirect(url_for("index") + "?msg=Scraper+already+running")
    subprocess.Popen(
        [sys.executable, os.path.join(BASE_DIR, "scraper.py")],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return redirect(url_for("index") + "?msg=Scraper+started+in+background")


@app.route("/log")
def log_view():
    lines = []
    if os.path.exists(LOG_PATH):
        with open(LOG_PATH) as f:
            lines = f.readlines()
    last30 = "".join(lines[-30:]) or "(no log entries yet)"
    body = f"<h2>Run Log (last 30 lines)</h2><pre>{html_lib.escape(last30)}</pre>"
    return render_page(body, running=is_scraper_running())


if __name__ == "__main__":
    ensure_data_dir()
    print("Open http://localhost:5000/config to configure")
    app.run(debug=False, port=5000)
