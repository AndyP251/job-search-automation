#!/usr/bin/env python3
"""
Web UI for the Job Board Scraper
=================================
Run:   python3 scraper_ui.py
Open:  http://localhost:5050

Uses Flask + SSE for real-time progress streaming.
Requires: pip install flask
"""

import json
import queue
import threading
import datetime
from pathlib import Path

from flask import Flask, render_template_string, Response, request, jsonify, send_file

# ── Import scraper ─────────────────────────────────────────────────────────────
import job_scraper as scraper

app = Flask(__name__)

# ── Shared state ───────────────────────────────────────────────────────────────
_current_run: dict = {"running": False, "thread": None}
_event_queues: list[queue.Queue] = []   # one per SSE client


def _broadcast(data: dict):
    """Send an event to all connected SSE clients."""
    msg = json.dumps(data, ensure_ascii=False)
    dead = []
    for q in _event_queues:
        try:
            q.put_nowait(msg)
        except queue.Full:
            dead.append(q)
    for q in dead:
        _event_queues.remove(q)


def _run_in_thread(sources: set[str], roles: list[str], locations: list[str],
                   timeout: int, days: int):
    """Worker thread that runs the scraper and broadcasts events."""
    try:
        for event in scraper.run_scraper(
            active_sources=sources,
            roles=roles,
            locations=locations,
            timeout_s=timeout,
            days=days,
        ):
            _broadcast(event)
    except Exception as exc:
        _broadcast({"event": "error", "source": "system", "message": str(exc)})
        _broadcast({"event": "done", "jobs": [], "md_path": "", "total": 0})
    finally:
        _current_run["running"] = False


# ══════════════════════════════════════════════════════════════════════════════
#  API ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/api/sources")
def api_sources():
    """Return available sources and their type."""
    out = []
    for key in scraper.ALL_SOURCES:
        entry = scraper.SOURCE_REGISTRY.get(key)
        out.append({
            "key": key,
            "needs_browser": entry[1] if entry else True,
            "label": scraper.INVESTOR_MAP.get(
                key.replace("ventures", " Ventures").replace("partners", " Partners").title(),
                key.title()
            ),
        })
    return jsonify(out)


@app.route("/api/start", methods=["POST"])
def api_start():
    """Start a scraper run."""
    if _current_run["running"]:
        return jsonify({"error": "Already running"}), 409

    body = request.get_json(force=True, silent=True) or {}
    sources = set(body.get("sources", scraper.ALL_SOURCES))
    roles = body.get("roles", list(scraper.DEFAULT_ROLES))
    locations = body.get("locations", list(scraper.DEFAULT_LOCATIONS))
    timeout = int(body.get("timeout", 12))
    days = int(body.get("days", 2))

    _current_run["running"] = True
    t = threading.Thread(
        target=_run_in_thread,
        args=(sources, roles, locations, timeout, days),
        daemon=True,
    )
    t.start()
    _current_run["thread"] = t

    return jsonify({"status": "started"})


@app.route("/api/stream")
def api_stream():
    """SSE endpoint — streams scraper progress events."""
    q: queue.Queue = queue.Queue(maxsize=500)
    _event_queues.append(q)

    def generate():
        try:
            while True:
                try:
                    msg = q.get(timeout=30)
                    yield f"data: {msg}\n\n"
                    # Check for done event
                    data = json.loads(msg)
                    if data.get("event") == "done":
                        break
                except queue.Empty:
                    yield f"data: {json.dumps({'event': 'heartbeat'})}\n\n"
        finally:
            if q in _event_queues:
                _event_queues.remove(q)

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/api/results")
def api_results():
    """Return the latest JSON results file."""
    today = datetime.date.today().isoformat()
    p = Path(f"job_listings_{today}.json")
    if p.exists():
        return jsonify(json.loads(p.read_text()))
    return jsonify([])


@app.route("/api/download")
def api_download():
    """Download the latest .md report."""
    today = datetime.date.today().isoformat()
    p = Path(f"job_listings_{today}.md")
    if p.exists():
        return send_file(p.resolve(), as_attachment=True)
    return "No report found", 404


# ══════════════════════════════════════════════════════════════════════════════
#  FRONTEND (single HTML page, no build step)
# ══════════════════════════════════════════════════════════════════════════════

HTML = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>🚀 Job Scraper Dashboard</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
:root {
  --bg:        #0c0e14;
  --bg2:       #151825;
  --bg3:       #1c2033;
  --border:    #262b40;
  --text:      #e4e6f0;
  --text2:     #8b90a8;
  --accent:    #6c63ff;
  --accent2:   #8b83ff;
  --green:     #2dd4a0;
  --red:       #f56565;
  --orange:    #f6ad55;
  --blue:      #63b3ed;
  --glass:     rgba(26, 31, 50, 0.7);
  --radius:    14px;
  --shadow:    0 8px 32px rgba(0,0,0,.4);
}
* { margin:0; padding:0; box-sizing:border-box; }
body {
  font-family: 'Inter', system-ui, -apple-system, sans-serif;
  background: var(--bg);
  color: var(--text);
  min-height: 100vh;
  overflow-x: hidden;
}

/* ── Animated gradient background ─────────────────────────────────────── */
body::before {
  content: "";
  position: fixed; inset: 0;
  background: radial-gradient(ellipse at 20% 0%, rgba(108,99,255,.12) 0%, transparent 50%),
              radial-gradient(ellipse at 80% 100%, rgba(45,212,160,.08) 0%, transparent 50%);
  pointer-events: none; z-index: 0;
}

/* ── Layout ───────────────────────────────────────────────────────────── */
.app { position: relative; z-index: 1; max-width: 1200px; margin: 0 auto; padding: 28px 20px 60px; }

/* ── Header ───────────────────────────────────────────────────────────── */
.header { text-align: center; margin-bottom: 36px; }
.header h1 { font-size: 2rem; font-weight: 800; letter-spacing: -.02em;
  background: linear-gradient(135deg, var(--accent2), var(--green));
  -webkit-background-clip: text; -webkit-text-fill-color: transparent;
}
.header p { color: var(--text2); margin-top: 6px; font-size: .95rem; }

/* ── Glass cards ──────────────────────────────────────────────────────── */
.card {
  background: var(--glass);
  backdrop-filter: blur(16px);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 24px;
  margin-bottom: 20px;
  box-shadow: var(--shadow);
}
.card-title {
  font-size: .85rem; font-weight: 600; text-transform: uppercase;
  letter-spacing: .08em; color: var(--text2); margin-bottom: 16px;
}

/* ── Sections grid ────────────────────────────────────────────────────── */
.grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
@media (max-width: 700px) { .grid2 { grid-template-columns: 1fr; } }

/* ── Source toggles ───────────────────────────────────────────────────── */
.sources-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(155px, 1fr)); gap: 8px; }
.src-toggle {
  display: flex; align-items: center; gap: 8px;
  padding: 8px 12px;
  background: var(--bg2); border: 1px solid var(--border);
  border-radius: 8px; cursor: pointer; transition: all .2s;
  font-size: .82rem;
}
.src-toggle:hover { border-color: var(--accent); background: var(--bg3); }
.src-toggle.active { border-color: var(--accent); background: rgba(108,99,255,.12); }
.src-toggle .dot {
  width: 10px; height: 10px; border-radius: 50%;
  border: 2px solid var(--border); transition: all .2s;
}
.src-toggle.active .dot { background: var(--accent); border-color: var(--accent); }
.src-toggle .type-badge {
  font-size: .65rem; padding: 1px 5px; border-radius: 4px; margin-left: auto;
  background: rgba(99,179,237,.15); color: var(--blue);
}
.src-toggle .type-badge.api { background: rgba(45,212,160,.12); color: var(--green); }

/* ── Buttons ──────────────────────────────────────────────────────────── */
.btn-row { display: flex; gap: 12px; flex-wrap: wrap; margin-top: 16px; }
.btn {
  display: inline-flex; align-items: center; gap: 8px;
  padding: 12px 28px; border: none; border-radius: 10px;
  font-family: inherit; font-weight: 600; font-size: .95rem;
  cursor: pointer; transition: all .25s;
}
.btn-primary {
  background: linear-gradient(135deg, var(--accent), #8b5cf6);
  color: #fff; box-shadow: 0 4px 20px rgba(108,99,255,.35);
}
.btn-primary:hover:not(:disabled) { transform: translateY(-1px); box-shadow: 0 6px 28px rgba(108,99,255,.5); }
.btn-primary:disabled { opacity: .5; cursor: not-allowed; }
.btn-secondary { background: var(--bg3); color: var(--text); border: 1px solid var(--border); }
.btn-secondary:hover { border-color: var(--accent); }
.btn-sm { padding: 8px 16px; font-size: .82rem; }
.btn-green {
  background: linear-gradient(135deg, #2dd4a0, #27b88c);
  color: #000; font-weight: 700;
}

/* ── Input fields ─────────────────────────────────────────────────────── */
.field { margin-bottom: 14px; }
.field label { display: block; font-size: .8rem; color: var(--text2); margin-bottom: 5px; font-weight: 500; }
.field input, .field select {
  width: 100%; padding: 10px 14px;
  background: var(--bg); border: 1px solid var(--border); border-radius: 8px;
  color: var(--text); font-family: inherit; font-size: .88rem;
  transition: border .2s;
}
.field input:focus, .field select:focus { outline: none; border-color: var(--accent); }

/* ── Progress area ────────────────────────────────────────────────────── */
.progress-wrapper { display: none; }
.progress-wrapper.visible { display: block; }
.progress-bar-track {
  width: 100%; height: 8px; border-radius: 4px; background: var(--bg);
  overflow: hidden; margin-bottom: 14px;
}
.progress-bar-fill {
  height: 100%; border-radius: 4px;
  background: linear-gradient(90deg, var(--accent), var(--green));
  transition: width .5s cubic-bezier(.4,0,.2,1);
  width: 0%;
}
.progress-status {
  font-size: .85rem; color: var(--text2); margin-bottom: 6px;
}
.progress-counter {
  display: flex; gap: 24px; margin-bottom: 16px; flex-wrap: wrap;
}
.counter-box {
  display: flex; flex-direction: column; align-items: center;
  background: var(--bg); border: 1px solid var(--border); border-radius: 10px;
  padding: 12px 20px; min-width: 100px;
}
.counter-box .num { font-size: 1.6rem; font-weight: 800; color: var(--green); }
.counter-box .lbl { font-size: .7rem; color: var(--text2); text-transform: uppercase; letter-spacing: .06em; margin-top: 2px; }

/* ── Log feed ─────────────────────────────────────────────────────────── */
.log-feed {
  max-height: 240px; overflow-y: auto;
  background: var(--bg); border: 1px solid var(--border); border-radius: 10px;
  padding: 12px 14px; font-size: .8rem;
  scrollbar-width: thin; scrollbar-color: var(--border) transparent;
}
.log-line { padding: 3px 0; display: flex; gap: 10px; align-items: baseline; }
.log-line .ts { color: var(--text2); font-size: .72rem; white-space: nowrap; }
.log-line .msg { color: var(--text); }
.log-line.hit .msg { color: var(--green); }
.log-line.err .msg { color: var(--red); }
.log-line.info .msg { color: var(--blue); }

/* ── Results table ────────────────────────────────────────────────────── */
.results-area { display: none; }
.results-area.visible { display: block; }
.results-summary {
  display: flex; gap: 16px; align-items: center; margin-bottom: 16px; flex-wrap: wrap;
}
.results-summary .big { font-size: 1.4rem; font-weight: 800; color: var(--green); }
.results-table-wrap {
  overflow-x: auto; border: 1px solid var(--border); border-radius: 10px;
}
table {
  width: 100%; border-collapse: collapse; font-size: .82rem;
}
thead { background: var(--bg3); }
th {
  text-align: left; padding: 12px 14px; font-weight: 600; color: var(--text2);
  text-transform: uppercase; font-size: .72rem; letter-spacing: .05em;
  border-bottom: 1px solid var(--border);
}
td {
  padding: 10px 14px; border-bottom: 1px solid var(--border);
  vertical-align: top; max-width: 300px;
}
tr:hover { background: rgba(108,99,255,.04); }
a { color: var(--accent2); text-decoration: none; }
a:hover { text-decoration: underline; }
.badge {
  display: inline-block; font-size: .68rem; padding: 2px 7px;
  border-radius: 4px; font-weight: 600;
}
.badge-vc   { background: rgba(108,99,255,.15); color: var(--accent2); }
.badge-pe   { background: rgba(246,173,85,.15); color: var(--orange); }
.badge-loc  { background: rgba(45,212,160,.1);  color: var(--green); }

/* ── Filter bar ───────────────────────────────────────────────────────── */
.filter-bar { display: flex; gap: 10px; margin-bottom: 14px; flex-wrap: wrap; }
.filter-bar input {
  flex: 1; min-width: 180px; padding: 9px 14px;
  background: var(--bg); border: 1px solid var(--border); border-radius: 8px;
  color: var(--text); font-size: .85rem;
}
.filter-bar input:focus { outline: none; border-color: var(--accent); }
.filter-bar select {
  padding: 9px 14px; background: var(--bg); border: 1px solid var(--border);
  border-radius: 8px; color: var(--text); font-size: .85rem;
}

/* ── Spinner ──────────────────────────────────────────────────────────── */
@keyframes spin { to { transform: rotate(360deg); } }
.spinner {
  width: 16px; height: 16px; border: 2px solid var(--border);
  border-top-color: var(--accent); border-radius: 50%;
  animation: spin .7s linear infinite; display: inline-block;
}

/* ── Scrollbar ────────────────────────────────────────────────────────── */
::-webkit-scrollbar { width: 6px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }
</style>
</head>
<body>
<div class="app">

  <!-- ─── Header ──────────────────────────────────────────────────────── -->
  <div class="header">
    <h1>🚀 Job Scraper Dashboard</h1>
    <p>Find SDR · BDR · ADR roles across 22 job boards &amp; VC/PE portfolios</p>
  </div>

  <!-- ─── Config Card ─────────────────────────────────────────────────── -->
  <div class="grid2">
    <div class="card">
      <div class="card-title">⚙️ Settings</div>
      <div class="field">
        <label>Role Keywords (comma-separated)</label>
        <input id="inp-roles" type="text" value="SDR, BDR, ADR, Sales Development Representative, Business Development Representative, Account Development Representative" />
      </div>
      <div class="field">
        <label>Locations (comma-separated)</label>
        <input id="inp-locations" type="text" value="New York, NYC, Boston, Austin, Atlanta, Remote, Hybrid" />
      </div>
      <div class="field">
        <label>Per-page Timeout (seconds)</label>
        <input id="inp-timeout" type="number" value="12" min="5" max="60" style="max-width:120px" />
      </div>
      <div class="field">
        <label>Date Window (days) — only show jobs posted within</label>
        <input id="inp-days" type="number" value="2" min="1" max="30" style="max-width:120px" />
      </div>
    </div>

    <div class="card">
      <div class="card-title">📡 Sources <span id="src-count" style="color:var(--green)">(loading…)</span></div>
      <div class="btn-row" style="margin-bottom:12px; margin-top:0">
        <button class="btn btn-secondary btn-sm" onclick="toggleAllSources(true)">Select All</button>
        <button class="btn btn-secondary btn-sm" onclick="toggleAllSources(false)">Deselect All</button>
        <button class="btn btn-secondary btn-sm" onclick="toggleApiOnly()">⚡ API Only (fast)</button>
      </div>
      <div id="sources-grid" class="sources-grid"></div>
    </div>
  </div>

  <!-- ─── Run button ──────────────────────────────────────────────────── -->
  <div style="text-align:center; margin: 24px 0">
    <button id="btn-run" class="btn btn-primary" onclick="startScrape()" style="font-size:1.1rem; padding:16px 48px;">
      ▶ &nbsp;Run Scraper
    </button>
  </div>

  <!-- ─── Progress ────────────────────────────────────────────────────── -->
  <div id="progress-area" class="card progress-wrapper">
    <div class="card-title">📊 Progress</div>
    <div class="progress-counter">
      <div class="counter-box"><div class="num" id="cnt-sources">0</div><div class="lbl">Sources</div></div>
      <div class="counter-box"><div class="num" id="cnt-hits" style="color:var(--green)">0</div><div class="lbl">Hits</div></div>
      <div class="counter-box"><div class="num" id="cnt-errors" style="color:var(--red)">0</div><div class="lbl">Errors</div></div>
    </div>
    <div class="progress-status" id="progress-status"><span class="spinner"></span> &nbsp;Starting…</div>
    <div class="progress-bar-track"><div class="progress-bar-fill" id="progress-fill"></div></div>
    <div class="log-feed" id="log-feed"></div>
  </div>

  <!-- ─── Results ─────────────────────────────────────────────────────── -->
  <div id="results-area" class="card results-area">
    <div class="card-title">📋 Results</div>
    <div class="results-summary">
      <div class="big" id="res-total">0 listings</div>
      <button class="btn btn-green btn-sm" onclick="downloadMd()">⬇ Download .md</button>
    </div>
    <div class="filter-bar">
      <input id="flt-search" type="text" placeholder="Search title or company…" oninput="renderTable()" />
      <select id="flt-source" onchange="renderTable()"><option value="">All Sources</option></select>
      <select id="flt-location" onchange="renderTable()"><option value="">All Locations</option></select>
    </div>
    <div class="results-table-wrap">
      <table>
        <thead>
          <tr><th>#</th><th>Role</th><th>Company</th><th>Location</th><th>Source</th><th>Investor</th></tr>
        </thead>
        <tbody id="res-tbody"></tbody>
      </table>
    </div>
  </div>

</div>

<script>
// ── State ──────────────────────────────────────────────────────────────────
let allSources = [];
let selectedSources = new Set();
let jobs = [];
let errorCount = 0;

// ── Load sources ───────────────────────────────────────────────────────────
fetch("/api/sources").then(r => r.json()).then(data => {
  allSources = data;
  selectedSources = new Set(data.map(s => s.key));
  renderSources();
  document.getElementById("src-count").textContent = `(${data.length})`;
});

function renderSources() {
  const grid = document.getElementById("sources-grid");
  grid.innerHTML = allSources.map(s => {
    const active = selectedSources.has(s.key) ? " active" : "";
    const typeClass = s.needs_browser ? "" : " api";
    const typeLabel = s.needs_browser ? "🌐" : "⚡";
    return `<div class="src-toggle${active}" data-key="${s.key}" onclick="toggleSource('${s.key}')">
      <span class="dot"></span>
      <span>${s.key}</span>
      <span class="type-badge${typeClass}">${typeLabel}</span>
    </div>`;
  }).join("");
}

function toggleSource(key) {
  if (selectedSources.has(key)) selectedSources.delete(key);
  else selectedSources.add(key);
  renderSources();
}

function toggleAllSources(on) {
  if (on) selectedSources = new Set(allSources.map(s => s.key));
  else selectedSources.clear();
  renderSources();
}

function toggleApiOnly() {
  selectedSources = new Set(allSources.filter(s => !s.needs_browser).map(s => s.key));
  renderSources();
}

// ── Start scrape ───────────────────────────────────────────────────────────
function startScrape() {
  const btn = document.getElementById("btn-run");
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> &nbsp;Running…';

  errorCount = 0;
  jobs = [];

  // Show progress area
  const pa = document.getElementById("progress-area");
  pa.classList.add("visible");
  document.getElementById("results-area").classList.remove("visible");
  document.getElementById("log-feed").innerHTML = "";
  document.getElementById("cnt-sources").textContent = "0";
  document.getElementById("cnt-hits").textContent = "0";
  document.getElementById("cnt-errors").textContent = "0";
  document.getElementById("progress-fill").style.width = "0%";

  // Gather config
  const roles = document.getElementById("inp-roles").value.split(",").map(s => s.trim()).filter(Boolean);
  const locations = document.getElementById("inp-locations").value.split(",").map(s => s.trim()).filter(Boolean);
  const timeout = parseInt(document.getElementById("inp-timeout").value) || 12;
  const days = parseInt(document.getElementById("inp-days").value) || 2;
  const sources = Array.from(selectedSources);

  // POST start
  fetch("/api/start", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({ sources, roles, locations, timeout, days }),
  });

  // SSE stream
  const es = new EventSource("/api/stream");
  es.onmessage = (e) => {
    const d = JSON.parse(e.data);
    handleEvent(d, es, btn);
  };
  es.onerror = () => {
    es.close();
    btn.disabled = false;
    btn.innerHTML = "▶ &nbsp;Run Scraper";
  };
}

function handleEvent(d, es, btn) {
  const logFeed = document.getElementById("log-feed");
  const ts = new Date().toLocaleTimeString();

  if (d.event === "start") {
    addLog(logFeed, ts, `Starting scan of ${d.total_sources} sources…`, "info");
  }
  else if (d.event === "progress") {
    document.getElementById("cnt-sources").textContent = `${d.index}/${d.total}`;
    const pct = Math.round((d.index / d.total) * 100);
    document.getElementById("progress-fill").style.width = pct + "%";
    document.getElementById("progress-status").innerHTML =
      `<span class="spinner"></span> &nbsp;Scraping <b>${d.source}</b> (${d.index}/${d.total})`;
    addLog(logFeed, ts, `→ ${d.source}`, "info");
  }
  else if (d.event === "hits") {
    document.getElementById("cnt-hits").textContent = d.running_total;
    if (d.count > 0) {
      addLog(logFeed, ts, `✓ ${d.source}: ${d.count} hit(s) (total: ${d.running_total})`, "hit");
    } else {
      addLog(logFeed, ts, `· ${d.source}: 0 hits`, "");
    }
  }
  else if (d.event === "error") {
    errorCount++;
    document.getElementById("cnt-errors").textContent = errorCount;
    addLog(logFeed, ts, `✗ ${d.source}: ${d.message}`, "err");
  }
  else if (d.event === "done") {
    es.close();
    btn.disabled = false;
    btn.innerHTML = "▶ &nbsp;Run Scraper";
    document.getElementById("progress-status").innerHTML = `✅ Done! ${d.total} unique listings found.`;
    document.getElementById("progress-fill").style.width = "100%";
    addLog(logFeed, ts, `Done — ${d.total} listings saved.`, "hit");
    jobs = d.jobs || [];
    showResults();
  }
}

function addLog(feed, ts, msg, cls) {
  const div = document.createElement("div");
  div.className = "log-line" + (cls ? " " + cls : "");
  div.innerHTML = `<span class="ts">${ts}</span><span class="msg">${msg}</span>`;
  feed.appendChild(div);
  feed.scrollTop = feed.scrollHeight;
}

// ── Results ────────────────────────────────────────────────────────────────
function showResults() {
  if (jobs.length === 0) return;
  document.getElementById("results-area").classList.add("visible");
  document.getElementById("res-total").textContent = `${jobs.length} listings`;

  // Populate filter dropdowns
  const sources = [...new Set(jobs.map(j => j.source))].sort();
  const locs = [...new Set(jobs.map(j => j.location))].sort();
  const srcSel = document.getElementById("flt-source");
  srcSel.innerHTML = '<option value="">All Sources</option>' +
    sources.map(s => `<option value="${s}">${s}</option>`).join("");
  const locSel = document.getElementById("flt-location");
  locSel.innerHTML = '<option value="">All Locations</option>' +
    locs.map(l => `<option value="${l}">${l}</option>`).join("");

  renderTable();
}

function renderTable() {
  const q = document.getElementById("flt-search").value.toLowerCase();
  const src = document.getElementById("flt-source").value;
  const loc = document.getElementById("flt-location").value;

  let filtered = jobs.filter(j => {
    if (q && !j.title.toLowerCase().includes(q) && !j.company.toLowerCase().includes(q)) return false;
    if (src && j.source !== src) return false;
    if (loc && j.location !== loc) return false;
    return true;
  });

  const tbody = document.getElementById("res-tbody");
  tbody.innerHTML = filtered.map((j, i) => {
    const inv = j.investor
      ? `<span class="badge ${j.investor.includes("PE") ? "badge-pe" : "badge-vc"}">${j.investor}</span>`
      : "—";
    return `<tr>
      <td>${i+1}</td>
      <td><a href="${esc(j.url)}" target="_blank" rel="noopener">${esc(j.title)}</a></td>
      <td>${esc(j.company)}</td>
      <td><span class="badge badge-loc">${esc(j.location)}</span></td>
      <td>${esc(j.source)}</td>
      <td>${inv}</td>
    </tr>`;
  }).join("");
}

function esc(s) {
  const el = document.createElement("span");
  el.textContent = s || "";
  return el.innerHTML;
}

function downloadMd() {
  window.open("/api/download", "_blank");
}
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(HTML)


if __name__ == "__main__":
    print("\n🚀  Job Scraper Dashboard")
    print("   Open http://localhost:5050 in your browser\n")
    app.run(host="0.0.0.0", port=5050, debug=False, threaded=True)
