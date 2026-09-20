#!/usr/bin/env python3
"""Optional lightweight web UI for Radarr Smart Optimizer.

Standard library only. The optimizer remains fully usable without this file.
"""

import html
import json
import os
import re
import signal
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OPTIMIZER = os.environ.get("RADARR_OPTIMIZER_SCRIPT", os.path.join(BASE_DIR, "radarr-smart-optimizer.py"))
STATE_FILE = os.environ.get("RADARR_OPTIMIZER_STATE", os.path.join(BASE_DIR, "radarr-smart-optimizer-state.json"))
RADARR_URL = os.environ.get("RADARR_URL", "http://127.0.0.1:7878").rstrip("/")
SONARR_URL = os.environ.get("SONARR_URL", "http://127.0.0.1:8989").rstrip("/")
SONARR_KEY = os.environ.get("SONARR_KEY", "").strip()
SONARR_STATE_FILE = os.environ.get("SONARR_OPTIMIZER_STATE", os.path.join(BASE_DIR, "sonarr-smart-optimizer-state.json"))
API_KEY = os.environ.get("RADARR_KEY", "").strip()
HOST = os.environ.get("SMART_UI_HOST", os.environ.get("RADARR_UI_HOST", "127.0.0.1"))
PORT = int(os.environ.get("SMART_UI_PORT", os.environ.get("RADARR_UI_PORT", "8788")))
ENABLE_ACTIONS = os.environ.get("SMART_UI_ENABLE_ACTIONS", "1").lower() in ("1", "true", "yes")
HISTORY_PAGES = max(1, min(20, int(os.environ.get("RADARR_UI_HISTORY_PAGES", "5"))))
MAX_OUTPUT = 50000
CONTROL_FILE = os.environ.get("SMART_OPTIMIZER_CONTROL", os.path.join(BASE_DIR, "smart-optimizer-control.json"))
SONARR_OPTIMIZER = os.environ.get("SONARR_OPTIMIZER_SCRIPT", os.path.join(BASE_DIR, "sonarr-smart-optimizer.py"))
RADARR_BASE_BUDGET = int(os.environ.get("RADARR_DAILY_SEARCH_BUDGET", "400"))
SONARR_BASE_BUDGET = int(os.environ.get("SONARR_DAILY_SEARCH_BUDGET", "400"))
MAX_MANUAL = max(1, int(os.environ.get("SMART_UI_MAX_MANUAL_SEARCHES", "10000")))

def load_controls():
    try:
        with open(CONTROL_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def save_controls(data):
    os.makedirs(os.path.dirname(CONTROL_FILE), exist_ok=True)
    # CONTROL_FILE may be bind-mounted as a single file in Docker.
    # Replacing the inode fails with EBUSY on such mounts, so update it in place.
    with open(CONTROL_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())

def app_controls(app):
    data = load_controls()
    c = data.get(app, {})
    today = time.strftime("%Y-%m-%d")
    return float(c.get("min_saving_percent", 5.0)), float(c.get("max_saving_percent", 50.0)), int((c.get("daily_extra") or {}).get(today, 0))

def update_saving_window(app, minimum, maximum):
    if not (0 <= minimum <= maximum <= 100):
        raise ValueError("Use 0-100%, and minimum cannot be greater than maximum.")
    data = load_controls(); c = data.setdefault(app, {})
    c["min_saving_percent"] = minimum; c["max_saving_percent"] = maximum
    save_controls(data)

def add_daily_extra(app, amount=50):
    data = load_controls(); c = data.setdefault(app, {}); extras = c.setdefault("daily_extra", {})
    today = time.strftime("%Y-%m-%d")
    extras[today] = int(extras.get(today, 0)) + amount
    # Old overrides are irrelevant; prune them so the file stays tiny.
    c["daily_extra"] = {today: extras[today]}
    save_controls(data)
    return extras[today]

job_lock = threading.Lock()
jobs = {a: {"running": False, "requested": 0, "start": 0, "proc": None, "stopped": False, "started": None, "finished": None, "output": "", "current": "", "last": "", "display_searched": 0, "display_item": "", "returncode": None} for a in ("radarr", "sonarr")}


def radarr_request(path, method="GET", payload=None):
    if not API_KEY:
        raise RuntimeError("RADARR_KEY is not configured")
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        RADARR_URL + "/api/v3" + path,
        data=data,
        method=method,
        headers={"X-Api-Key": API_KEY, "Accept": "application/json",
                 "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        raw = response.read().decode("utf-8")
        return json.loads(raw) if raw else {}

def radarr_get(path):
    if not API_KEY:
        raise RuntimeError("RADARR_KEY is not configured")
    return radarr_request(path)


def sonarr_get(path):
    if not SONARR_KEY:
        raise RuntimeError("SONARR_KEY is not configured")
    req = urllib.request.Request(
        SONARR_URL + "/api/v3" + path,
        headers={"X-Api-Key": SONARR_KEY, "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        raw = response.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def load_sonarr_state():
    try:
        with open(SONARR_STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"daily": {}, "episodes": {}}


def sonarr_history_records():
    records = []
    for page_num in range(1, HISTORY_PAGES + 1):
        data = sonarr_get("/history?page=%d&pageSize=100&sortKey=date&sortDirection=descending" % page_num)
        batch = data.get("records", [])
        records.extend(batch)
        if len(batch) < 100:
            break
    return records


def sonarr_queue_records():
    data = sonarr_get("/queue?page=1&pageSize=100&sortKey=timeleft&sortDirection=ascending")
    return data.get("records", [])


def sonarr_completed_upgrades(records):
    pending = {}
    upgrades = []
    for event in reversed(records):
        episode_id = event.get("episodeId")
        etype = event.get("eventType")
        data = event.get("data") or {}
        if etype == "episodeFileDeleted" and data.get("reason") == "Upgrade":
            try:
                pending[episode_id] = int(data.get("size") or 0)
            except (TypeError, ValueError):
                pass
        elif etype == "downloadFolderImported" and episode_id in pending:
            try:
                new_size = int(data.get("size") or 0)
            except (TypeError, ValueError):
                new_size = 0
            old_size = pending.pop(episode_id)
            if old_size > 0 and new_size > 0:
                upgrades.append({
                    "date": event.get("date") or "",
                    "title": event.get("sourceTitle") or ("Episode ID %s" % episode_id),
                    "old": old_size, "new": new_size, "saved": old_size - new_size,
                })
    return sorted(upgrades, key=lambda x: x["date"], reverse=True)


def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"daily": {}, "movies": {}}


def history_records():
    records = []
    for page in range(1, HISTORY_PAGES + 1):
        data = radarr_get("/history?page=%d&pageSize=100&sortKey=date&sortDirection=descending" % page)
        batch = data.get("records", [])
        records.extend(batch)
        if len(batch) < 100:
            break
    return records


def queue_records():
    """Return current Radarr download queue rows for the dashboard."""
    data = radarr_get("/queue?page=1&pageSize=100&sortKey=timeleft&sortDirection=ascending")
    return data.get("records", [])


def queue_health(rows):
    """Classify queue rows conservatively from Radarr's own status fields."""
    result = []
    for row in rows:
        status = str(row.get("status") or "").lower()
        tracked = str(row.get("trackedDownloadStatus") or "").lower()
        messages = row.get("statusMessages") or []
        message_parts = []
        for m in messages:
            if not isinstance(m, dict):
                continue
            title = str(m.get("title") or "")
            details = m.get("messages") or []
            if isinstance(details, list):
                detail_text = " ".join(
                    str(x.get("message") or "") if isinstance(x, dict) else str(x)
                    for x in details
                )
            elif isinstance(details, dict):
                detail_text = str(details.get("message") or details)
            else:
                detail_text = str(details)
            message_parts.append((title + " " + detail_text).strip())
        message_text = " ".join(x for x in message_parts if x).strip()
        size = float(row.get("size") or 0)
        left = float(row.get("sizeleft") or 0)
        progress = max(0.0, min(100.0, ((size - left) / size * 100.0) if size else 0.0))
        attention = tracked in ("warning", "error") or status in ("warning", "failed")
        lower_message = message_text.lower()
        if "not an upgrade for existing movie file" in lower_message:
            health = "Optimizer import blocked"
            health_kind = "optimizer_blocked"
        elif tracked == "error" or status == "failed":
            health = "Failed"
            health_kind = "failed"
        elif tracked == "warning" or status == "warning":
            health = "Needs attention"
            health_kind = "warning"
        else:
            health = row.get("status") or row.get("trackedDownloadStatus") or "unknown"
            health_kind = "normal"
        result.append({
            "title": row.get("title") or ("Movie ID %s" % row.get("movieId")),
            "movie_id": row.get("movieId"),
            "status": row.get("status") or row.get("trackedDownloadStatus") or "unknown",
            "tracked": row.get("trackedDownloadStatus") or "",
            "progress": progress,
            "timeleft": row.get("timeleft") or "—",
            "message": message_text,
            "attention": attention,
            "health": health,
            "health_kind": health_kind,
            "queue_id": row.get("id"),
        })
    return result


def repair_import(queue_id):
    """Reprocess one completed Radarr queue item using COPY mode.

    This is intentionally user-triggered. It only handles the specific
    quality-hierarchy rejection the optimizer understands; all other queue
    failures remain untouched.
    """
    rows = queue_records()
    row = next((x for x in rows if str(x.get("id")) == str(queue_id)), None)
    if not row:
        raise RuntimeError("Queue item is no longer present")

    classified = queue_health([row])[0]
    if classified.get("health_kind") != "optimizer_blocked":
        raise RuntimeError("This item is not an optimizer import block")
    if str(row.get("status") or "").lower() != "completed":
        raise RuntimeError("Download is not completed")
    if str(row.get("trackedDownloadState") or "").lower() != "importpending":
        raise RuntimeError("Download is not waiting for import")
    if int(row.get("sizeleft") or 0) != 0:
        raise RuntimeError("Download still has data remaining")

    movie_id = int(row.get("movieId"))
    movie = radarr_get("/movie/%d" % movie_id)
    old_file = movie.get("movieFile") or {}
    old_size = int(old_file.get("size") or 0)
    new_size = int(row.get("size") or 0)
    old_res = (((old_file.get("quality") or {}).get("quality") or {}).get("resolution") or 0)
    new_res = (((row.get("quality") or {}).get("quality") or {}).get("resolution") or 0)
    if not old_size or not new_size or not old_res or not new_res:
        raise RuntimeError("Cannot safely compare current and downloaded file")
    if int(new_res) < int(old_res):
        raise RuntimeError("Refusing resolution downgrade")
    if int(new_res) == int(old_res) and new_size >= old_size:
        raise RuntimeError("Refusing same-resolution replacement that is not smaller")

    download_id = str(row.get("downloadId") or "")
    if not download_id:
        raise RuntimeError("Queue item has no downloadId")

    items = radarr_get("/manualimport?downloadId=%s&movieId=%d&filterExistingFiles=true" %
                       (urllib.parse.quote(download_id), movie_id))
    usable = []
    for item in items if isinstance(items, list) else []:
        rejections = item.get("rejections") or []
        reasons = " ".join(str((r.get("reason") or r.get("message") or "")) if isinstance(r, dict) else str(r)
                           for r in rejections).lower()
        # Only override Radarr's source-quality hierarchy. Anything else stays blocked.
        bad = [r for r in rejections if "not an upgrade for existing movie file" not in
               str((r.get("reason") or r.get("message") or "")) .lower()]
        if not bad:
            usable.append(item)
    if len(usable) != 1:
        raise RuntimeError("Expected exactly one safely reprocessable video file, found %d" % len(usable))

    item = usable[0]
    payload = {
        "name": "ManualImport",
        "files": [{
            "path": item.get("path"),
            "folderName": item.get("folderName"),
            "quality": item.get("quality"),
            "languages": item.get("languages") or row.get("languages") or [],
            "releaseGroup": item.get("releaseGroup"),
            "indexerFlags": item.get("indexerFlags") or 0,
            "downloadId": download_id,
            "movieId": movie_id,
        }],
        "importMode": 2
    }
    if not payload["files"][0]["path"]:
        raise RuntimeError("Radarr did not return an importable file path")
    return radarr_request("/command", method="POST", payload=payload)


def completed_upgrades(records):
    """Pair Upgrade deletion -> subsequent import for the same movie.

    This reports observed Radarr history, not predicted optimizer savings.
    """
    pending = {}
    upgrades = []
    # API records are newest first; process oldest first.
    for event in reversed(records):
        movie_id = event.get("movieId")
        etype = event.get("eventType")
        data = event.get("data") or {}
        if etype == "movieFileDeleted" and data.get("reason") == "Upgrade":
            try:
                pending[movie_id] = {
                    "old": int(data.get("size") or 0),
                    "deleted": event.get("date"),
                    "old_path": event.get("sourceTitle") or "",
                }
            except (TypeError, ValueError):
                pass
        elif etype == "downloadFolderImported" and movie_id in pending:
            try:
                new_size = int(data.get("size") or 0)
            except (TypeError, ValueError):
                new_size = 0
            old = pending.pop(movie_id)
            if old["old"] > 0 and new_size > 0:
                upgrades.append({
                    "movie_id": movie_id,
                    "date": event.get("date") or "",
                    "title": event.get("sourceTitle") or ("Movie ID %s" % movie_id),
                    "old": old["old"],
                    "new": new_size,
                    "saved": old["old"] - new_size,
                })
    return sorted(upgrades, key=lambda x: x["date"], reverse=True)


def gib(n):
    return n / (1024.0 ** 3)


def search_count(app):
    state = load_state() if app == "radarr" else load_sonarr_state()
    today = time.strftime("%Y-%m-%d")
    return int((state.get("daily") or {}).get(today, {}).get("searches", 0))

def manual_status(app):
    with job_lock:
        snap = dict(jobs[app])
    searched = max(0, search_count(app) - int(snap.get("start") or 0)) if snap.get("started") else 0
    requested = int(snap.get("requested") or 0)
    if not snap.get("started"):
        state, detail = "idle", ""
    elif snap.get("running"):
        state, detail = ("stopping" if snap.get("stopped") else "running"), ""
    elif snap.get("stopped"):
        state, detail = "stopped", ""
    elif snap.get("returncode") not in (None, 0):
        state, detail = "failed", "Optimizer exited with code %s" % snap.get("returncode")
    else:
        state = "finished"
        detail = "No eligible items" if requested and searched == 0 else ""
    display_searched = int(snap.get("display_searched") or 0)
    display_item = str(snap.get("display_item") or "")
    if not snap.get("running") and searched > display_searched:
        display_searched = searched
        display_item = str(snap.get("last") or display_item)
    return {"state": state, "searched": display_searched if snap.get("started") else searched,
            "requested": requested, "running": bool(snap.get("running")), "detail": detail,
            "current": str(snap.get("current") or "") if snap.get("running") else "",
            "last": display_item}

def run_optimizer(live, app="radarr", searches_per_run=None, daily_extra=0):
    with job_lock:
        other = "sonarr" if app == "radarr" else "radarr"
        if jobs[app]["running"] or jobs[other]["running"]: return False
        start = search_count(app)
        if daily_extra: add_daily_extra(app, daily_extra)
        jobs[app].update(running=True, requested=int(searches_per_run or 0), start=start, proc=None, stopped=False, started=time.time(), finished=None, output="", current="", last="", display_searched=0, display_item="", returncode=None)
    def worker():
        script = OPTIMIZER if app == "radarr" else SONARR_OPTIMIZER
        cmd = ["python3", "-u", script] + (["--live"] if live else [])
        env = os.environ.copy(); env["SMART_OPTIMIZER_CONTROL"] = CONTROL_FILE
        if searches_per_run: env["RADARR_SEARCHES_PER_RUN" if app == "radarr" else "SONARR_SEARCHES_PER_RUN"] = str(searches_per_run)
        output = ""
        returncode = None
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env, start_new_session=True)
            with job_lock:
                jobs[app]["proc"] = proc; stop_now = jobs[app]["stopped"]
            if stop_now: os.killpg(proc.pid, signal.SIGTERM)
            chunks = []
            for line in proc.stdout:
                chunks.append(line)
                if len(chunks) > 2000:
                    chunks = chunks[-1000:]
                clean = line.strip()
                # Sonarr item lines look like: 20. 'Allo 'Allo! S04E01
                # Avoid whitespace-regex escaping issues: locate the SxxExx token,
                # then derive the series title from the text before it.
                match = re.search(r"S([0-9]{2})E([0-9]{2})", clean)
                if app == "sonarr" and match and ". " in clean[:match.start()]:
                    before = clean[:match.start()].strip()
                    title = before.split(". ", 1)[1].strip()
                    current = "%s · S%sE%s" % (title, match.group(1), match.group(2))
                    with job_lock:
                        jobs[app]["current"] = current
                        jobs[app]["last"] = current
                elif app == "radarr" and "NOW CHECKING:" in clean:
                    current = clean.split("NOW CHECKING:", 1)[1].strip()
                    with job_lock:
                        jobs[app]["current"] = current
                        jobs[app]["last"] = current
                # Pair completed-search count with the episode that produced it.
                # Parse without regex so this cannot fail because of escaping.
                if "SEARCH PROGRESS:" in clean:
                    try:
                        done_text = clean.split("SEARCH PROGRESS:", 1)[1].strip().split("/", 1)[0].strip()
                        done = int(done_text)
                        with job_lock:
                            jobs[app]["display_searched"] = done
                            jobs[app]["display_item"] = str(jobs[app].get("current") or jobs[app].get("last") or "")
                    except (ValueError, IndexError):
                        pass
                with job_lock:
                    jobs[app]["output"] = "".join(chunks)[-MAX_OUTPUT:]
            proc.wait()
            output = "".join(chunks)
            returncode = proc.returncode
        except Exception as exc:
            output = "ERROR: %s" % exc
            returncode = -1
        with job_lock: jobs[app].update(running=False, finished=time.time(), proc=None, output=(output or "")[-MAX_OUTPUT:], returncode=returncode)
    threading.Thread(target=worker, daemon=True).start(); return True

def stop_optimizer(app):
    with job_lock:
        item = jobs[app]
        if not item["running"]: return False
        item["stopped"] = True; proc = item.get("proc")
    if proc and proc.poll() is None:
        try: os.killpg(proc.pid, signal.SIGTERM)
        except Exception:
            try: proc.terminate()
            except Exception: pass
    return True


CSS = """
*{box-sizing:border-box}
:root{color-scheme:dark;--bg:#0b0e13;--panel:#121720;--panel2:#161c26;--line:#242b36;--text:#f3f4f6;--muted:#8993a4;--accent:#7dd3fc;--accent2:#a78bfa;--good:#86efac;--bad:#fda4af;--warn:#fde68a}
html,body{margin:0;min-height:100%;background:var(--bg);color:var(--text)}
body{font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:radial-gradient(circle at 18% -10%,rgba(59,130,246,.10),transparent 32rem),radial-gradient(circle at 90% 0%,rgba(168,85,247,.08),transparent 28rem),var(--bg)}
a{color:inherit}
.shell{max-width:1220px;margin:0 auto;padding:30px 28px 54px}
.topbar{display:flex;align-items:center;justify-content:space-between;gap:20px;margin-bottom:28px}
.brand{display:flex;align-items:center;gap:13px}.mark{width:42px;height:42px;border-radius:12px;display:grid;place-items:center;font-size:20px;font-weight:800;background:linear-gradient(145deg,#2563eb,#7c3aed);box-shadow:inset 0 1px rgba(255,255,255,.22),0 8px 24px rgba(37,99,235,.18)}
.brandcopy h1{margin:0;font-size:1.15rem;letter-spacing:-.025em}.brandcopy div{font-size:.76rem;color:var(--muted);margin-top:2px}
.nav{display:flex;align-items:center;gap:8px}.appswitch{display:flex;gap:6px;padding:4px;border:1px solid var(--line);background:#0e131a;border-radius:11px}.appswitch a{text-decoration:none;padding:7px 12px;border-radius:8px;color:#8f9bad;font-size:.75rem;font-weight:700}.appswitch a:hover{color:#fff;background:#17202c}.appswitch a.active{color:#fff;background:#1d2939}.homewrap{min-height:70vh;display:grid;place-items:center}.homecard{text-align:center;max-width:720px}.homecard h1{font-size:2.25rem;margin:0 0 8px;letter-spacing:-.05em}.homecard p{color:var(--muted);margin:0 0 28px}.chooser{display:grid;grid-template-columns:1fr 1fr;gap:14px}.choice{text-decoration:none;text-align:left;padding:24px;border-radius:16px;border:1px solid var(--line);background:linear-gradient(180deg,var(--panel2),var(--panel));transition:.15s}.choice:hover{transform:translateY(-2px);border-color:#3b4758}.choice b{display:block;font-size:1.15rem;margin-bottom:6px}.choice span{font-size:.78rem;color:var(--muted)}.navchip,.status{height:34px;display:inline-flex;align-items:center;gap:8px;padding:0 11px;border-radius:9px;border:1px solid var(--line);background:#10151d;color:#b8c0cc;font-size:.76rem}
.dot{width:7px;height:7px;border-radius:999px;background:var(--good);box-shadow:0 0 10px rgba(134,239,172,.55)}
.hero{display:flex;justify-content:space-between;align-items:flex-end;gap:24px;margin-bottom:18px}
.hero h2{font-size:1.75rem;line-height:1.1;letter-spacing:-.04em;margin:0 0 7px}.hero p{margin:0;color:var(--muted);font-size:.86rem}
.actions{display:flex;gap:8px;flex-wrap:wrap}.actions form{margin:0}
button{height:36px;padding:0 13px;border-radius:9px;border:1px solid #334155;background:#172033;color:#e5e7eb;font-weight:700;font-size:.78rem;cursor:pointer}
button:hover:not(:disabled){background:#1d2940}button.live{background:#2a1720;border-color:#5f2437;color:#fecdd3}button.live:hover:not(:disabled){background:#351b27}button:disabled{opacity:.38;cursor:not-allowed}
.notice{margin:0 0 16px;padding:10px 12px;border-radius:10px;border:1px solid var(--line);background:#10151d;color:var(--muted);font-size:.78rem}.notice.bad{color:var(--bad)}
.grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-bottom:16px}
.stat{background:linear-gradient(180deg,var(--panel2),var(--panel));border:1px solid var(--line);border-radius:13px;padding:16px;min-height:120px}
.stathead{display:flex;align-items:center;justify-content:space-between;gap:12px;color:#aab3c1;font-size:.75rem}.stathead span:first-child{display:flex;align-items:center;gap:8px}.mini{width:24px;height:24px;border-radius:7px;display:grid;place-items:center;background:#0f141c;border:1px solid #222a35;color:#cbd5e1;font-size:.74rem}
.value{font-size:1.85rem;font-weight:760;letter-spacing:-.045em;margin-top:18px}.good{color:var(--good)}.bad{color:var(--bad)}.muted{color:var(--muted)}.sub{font-size:.73rem;color:var(--muted);margin-top:6px}
.layout{display:grid;grid-template-columns:minmax(0,1.7fr) minmax(280px,.8fr);gap:16px}
.panel{background:linear-gradient(180deg,#141a23,#10151c);border:1px solid var(--line);border-radius:13px;overflow:hidden}
.panel+.panel{margin-top:16px}.layout .panel+.panel{margin-top:0}
.panelhead{display:flex;justify-content:space-between;align-items:flex-start;gap:14px;padding:16px 17px 13px;border-bottom:1px solid var(--line)}.panelhead h3{font-size:.9rem;margin:0;letter-spacing:-.015em}.panelhead p{font-size:.73rem;color:var(--muted);margin:4px 0 0}.badge{font-size:.64rem;padding:5px 7px;border-radius:999px;border:1px solid #2a3340;color:#93a4b8;background:#0e131a;white-space:nowrap}
table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:12px 17px;border-bottom:1px solid #1f2630;font-size:.79rem}th{font-size:.62rem;color:#667085;text-transform:uppercase;letter-spacing:.09em;background:#0f141b}tr:last-child td{border-bottom:0}td:first-child{max-width:520px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.sidecontent{padding:16px}.metricline{display:flex;align-items:center;justify-content:space-between;padding:11px 0;border-bottom:1px solid #202731;font-size:.78rem}.metricline:last-child{border-bottom:0}.metricline span:first-child{color:var(--muted)}.metricline b{font-size:.8rem}
pre{margin:0;white-space:pre-wrap;word-break:break-word;max-height:305px;overflow:auto;background:#0c1117;padding:15px 17px;color:#bbc5d3;font:11.5px/1.55 ui-monospace,SFMono-Regular,Menlo,monospace}
.footer{padding-top:22px;text-align:center;font-size:.68rem;color:#4c5667}
.toolbar{display:flex;gap:10px;align-items:center;margin-bottom:16px}.searchbox{position:relative;flex:1}.searchbox input{width:100%;height:40px;border-radius:10px;border:1px solid var(--line);background:#0f141b;color:var(--text);padding:0 14px 0 38px;outline:none;font-size:.8rem}.searchbox input:focus{border-color:#3b82f6;box-shadow:0 0 0 3px rgba(59,130,246,.10)}.searchicon{position:absolute;left:13px;top:10px;color:#64748b}.queueitem{padding:14px 17px;border-bottom:1px solid #1f2630}.queueitem.extra{display:none}.queueitem:last-child{border-bottom:0}.qtop{display:flex;justify-content:space-between;gap:12px;align-items:center}.qtitle{font-size:.8rem;font-weight:650;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.qmeta{font-size:.7rem;color:var(--muted);margin-top:5px}.progress{height:5px;background:#0b1016;border-radius:99px;overflow:hidden;margin-top:10px}.progress span{display:block;height:100%;background:linear-gradient(90deg,#3b82f6,#8b5cf6);border-radius:99px}.attention{color:var(--warn)}.empty{padding:22px 17px;color:var(--muted);font-size:.78rem}.expandbar{width:100%;height:38px;border:0;border-top:1px solid var(--line);border-radius:0;background:#10161e;color:#9aa6b7;font-size:.74rem;box-shadow:none}.expandbar:hover:not(:disabled){transform:none;background:#151c26;color:#e5e7eb}.controlbar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:0 0 16px}.controlbox{display:flex;gap:7px;align-items:center;padding:7px 9px;border:1px solid var(--line);border-radius:10px;background:#10151d}.controlbox label{font-size:.7rem;color:var(--muted)}.controlbox input{width:62px;height:32px;border:1px solid #303947;border-radius:7px;background:#0b1016;color:var(--text);padding:0 8px}.controlbox button{height:32px}.sectiontabs{display:flex;gap:5px;margin-bottom:12px}.tab{font-size:.72rem;padding:6px 9px;border-radius:8px;background:#10151d;border:1px solid var(--line);color:#8e99aa}.tab.active{color:#e5e7eb;background:#17202c}.kpi{font-size:.66rem;color:#667085;text-transform:uppercase;letter-spacing:.08em}
.manualstate{font-size:.78rem;font-weight:700;color:#dbeafe}.stopbtn{border-color:#6b2635;color:#fecdd3}.hint{font-size:.7rem;color:var(--muted)}
.grid.five{grid-template-columns:repeat(5,minmax(0,1fr))}
.dashboard2{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:14px;align-items:start}
.dashboard2 .panel{margin-bottom:0;align-self:start}
.topbar.compact{margin-bottom:16px}
.controlbar.primary{margin-bottom:8px}
.manualstate{display:flex;flex-direction:column;align-items:flex-start;justify-content:center;gap:4px;min-width:260px;line-height:1.25}.runmain{display:block;font-weight:700;white-space:nowrap}.runitem{display:block;font-size:.72rem;color:var(--muted);font-weight:500;white-space:nowrap}.ajaxmsg{font-size:.72rem;color:var(--muted)}.queueitem.extra,.changeextra{display:none}.changes{width:100%;table-layout:fixed}.changes .releasecol{width:45%}.changes .sizecol{width:18%}.changes .changecol{width:19%}.changes th,.changes td{overflow:hidden;text-overflow:ellipsis}.changes th:not(:first-child),.changes td:not(:first-child){white-space:nowrap;text-align:right}.changes td:first-child{white-space:nowrap}
@media(max-width:1100px){.grid.five{grid-template-columns:repeat(3,1fr)}}
@media(max-width:900px){.dashboard2{grid-template-columns:1fr}.grid.five{grid-template-columns:repeat(2,1fr)}}
@media(max-width:900px){.grid{grid-template-columns:repeat(2,1fr)}.layout{grid-template-columns:1fr}.hero{align-items:flex-start;flex-direction:column}.topbar{align-items:flex-start;flex-direction:column}.nav{width:100%;justify-content:space-between}}
@media(max-width:520px){.shell{padding:22px 14px 40px}.grid{grid-template-columns:1fr}.hero h2{font-size:1.45rem}th,td{padding:11px 12px}}
"""

AJAX_SCRIPT = """<script>
(function(){
 const form=document.querySelector('.manualform'); if(!form)return;
 const app=form.querySelector('input[name="app"]').value;
 const state=document.getElementById('runstate-'+app);
 async function refresh(){
  try{const r=await fetch('/status?app='+app,{cache:'no-store'});const x=await r.json();
   const word=x.state.charAt(0).toUpperCase()+x.state.slice(1);
   const main=x.requested?(word+' · '+x.searched+' / '+x.requested+' searched'+(x.detail?' · '+x.detail:'')):'Idle';
   const now=x.running&&x.current?('Now checking: '+x.current):'';
   const last=!x.running&&x.last?('Last searched: '+x.last):'';
   const esc=s=>s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
   state.innerHTML='<span class="runmain">'+esc(main)+'</span>'
     +(now?'<span class="runitem">'+esc(now)+'</span>':'')
     +(last?'<span class="runitem">'+esc(last)+'</span>':'');
   form.querySelector('button:not(.stopbtn)').disabled=!!x.running;
   form.querySelector('.stopbtn').disabled=!x.running;
  }catch(e){}
 }
 form.addEventListener('submit',async function(e){
  e.preventDefault(); const submit=e.submitter; const target=(submit&&submit.classList.contains('stopbtn'))?'/stop':'/manual-search';
  try{const r=await fetch(target,{method:'POST',body:new URLSearchParams(new FormData(form)),headers:{'Content-Type':'application/x-www-form-urlencoded'}});
   if(!r.ok){state.textContent='Error · '+r.status; return;} await refresh();
  }catch(e){state.textContent='Connection error';}
 });
 refresh(); setInterval(refresh,2000);
})();
</script>"""

def page():
    state = load_state()
    today = time.strftime("%Y-%m-%d")
    used = int((state.get("daily") or {}).get(today, {}).get("searches", 0))
    rule_min, rule_max, extra_today = app_controls("radarr")
    error = ""
    try:
        records = history_records()
        upgrades = completed_upgrades(records)
        queue = queue_health(queue_records())
    except Exception as exc:
        upgrades, queue = [], []
        error = str(exc)
    saved = sum(x["saved"] for x in upgrades)
    positive = sum(1 for x in upgrades if x["saved"] > 0)
    total_before = sum(x["old"] for x in upgrades)
    reduction_pct = (saved / total_before * 100.0) if total_before else 0.0
    attention = [x for x in queue if x["attention"] and x.get("health_kind") != "optimizer_blocked"]
    optimizer_blocked = [x for x in queue if x.get("health_kind") == "optimizer_blocked"]
    last_date = upgrades[0]["date"][:10] if upgrades else "—"
    with job_lock:
        snap = dict(jobs["radarr"])

    rows = ""
    for i, x in enumerate(upgrades[:15]):
        extra = " changeextra" if i >= 5 else ""
        delta = gib(x["saved"])
        cls = "good" if delta >= 0 else "bad"
        rows += "<tr class='filterrow%s' data-search='%s'><td>%s</td><td>%.2f GiB</td><td>%.2f GiB</td><td class='%s'>%+.2f GiB</td></tr>" % (
            extra, html.escape(x["title"].lower(), quote=True), html.escape(x["title"]), gib(x["old"]), gib(x["new"]), cls, delta)
    if not rows:
        rows = "<tr><td colspan='4' class='muted'>No completed upgrade pairs found in the loaded history window.</td></tr>"
    elif len(upgrades) > 5:
        rows += "<tr id='changesExpandRow'><td colspan='4'><button type='button' class='expandbar' id='changesExpand' onclick='toggleChanges()'>Show %d more changes ↓</button></td></tr>" % (min(len(upgrades), 15) - 5)

    qrows = ""
    visible_queue = queue[:4]
    for i, x in enumerate(queue[:15]):
        cls = "attention" if x["attention"] else ""
        note = x["message"] or ("Time left: %s" % x["timeleft"])
        extra = " extra" if i >= 4 else ""
        qrows += """<div class="queueitem filterrow%s" data-search="%s"><div class="qtop"><div class="qtitle">%s</div><div class="%s">%s</div></div><div class="qmeta">%.1f%% · %s</div><div class="progress"><span style="width:%.1f%%"></span></div></div>""" % (
            extra, html.escape(x["title"].lower(), quote=True), html.escape(x["title"]), cls,
            html.escape(str(x.get("health") or x["status"])),
            x["progress"], html.escape(note) + ("""<form method="post" action="/repair-import" style="margin-top:9px"><input type="hidden" name="queue_id" value="%s"><button class="live" type="submit">Try safe import</button></form>""" % html.escape(str(x.get("queue_id") or ""), quote=True) if x.get("health_kind") == "optimizer_blocked" else ""), x["progress"])
    if not qrows:
        qrows = "<div class='empty'>Nothing is currently in Radarr's download queue.</div>"
    elif len(queue) > 4:
        qrows += """<button type="button" class="expandbar" id="queueExpand" onclick="toggleQueue()">Show %d more downloads ↓</button>""" % (min(len(queue), 15) - 4)

    runstat = manual_status("radarr")
    runlabel = "Idle" if not runstat["requested"] else ("%s%s · %d / %d searched" % (runstat["state"].capitalize(), (" · Searching " + runstat["current"]) if runstat.get("running") and runstat.get("current") else "", runstat["searched"], runstat["requested"]))
    actions = """<div class="controlbar primary"><form class="controlbox manualform" method="post" action="/manual-search"><input type="hidden" name="app" value="radarr"><label>Manual search</label><input name="count" type="number" min="1" max="%d" value="50"><button %s>Search</button><button class="stopbtn" formaction="/stop" %s>STOP</button></form><span id="runstate-radarr" class="manualstate">%s</span></div>
<form class="controlbar" method="post" action="/settings"><input type="hidden" name="app" value="radarr"><div class="controlbox"><label>Downsize</label><input name="min" type="number" min="0" max="100" step="0.1" value="%.1f"><span>–</span><input name="max" type="number" min="0" max="100" step="0.1" value="%.1f"><span>%%</span><button type="submit">Apply</button></div><span class="badge">%d/%d searches · +%d today</span></form>""" % (MAX_MANUAL, "disabled" if runstat["running"] else "", "" if runstat["running"] else "disabled", html.escape(runlabel), rule_min, rule_max, used, RADARR_BASE_BUDGET + extra_today, extra_today)
    output = html.escape(snap.get("output") or "No UI-started run yet.")
    status = runlabel
    warning = "" if ENABLE_ACTIONS else "<div class='notice'>Read-only mode is active. Smart retry controls will only be enabled after we validate queue detection and candidate selection.</div>"
    err = ("<div class='notice bad'>Radarr API error: %s</div>" % html.escape(error)) if error else ""

    return """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="theme-color" content="#0b0e13"><title>Smart Optimizer UI · Radarr</title><style>%s</style></head>
<body><div class="shell">
<div class="topbar compact"><div class="brand"><div class="brandcopy"><h1>Radarr Optimizer</h1><div>Find smaller releases for your movies while keeping quality.</div></div></div><div class="nav"><div class="appswitch"><a class="active" href="/radarr">Radarr</a><a href="/sonarr">Sonarr</a></div><span class="status"><span class="dot"></span>%s</span></div></div>
%s
%s%s
<div class="toolbar"><div class="searchbox"><span class="searchicon">⌕</span><input id="librarySearch" autocomplete="off" placeholder="Search releases and current downloads…"></div></div>
<div class="grid five">
<div class="stat"><div class="stathead"><span><span class="mini">↘</span>Storage saved</span></div><div class="value %s">%+.2f GiB</div><div class="sub">Observed across loaded upgrade history</div></div>
<div class="stat"><div class="stathead"><span><span class="mini">✓</span>Space reductions</span></div><div class="value">%d</div><div class="sub">Observed upgrades that ended smaller</div></div>
<div class="stat"><div class="stathead"><span><span class="mini">↓</span>Active downloads</span></div><div class="value">%d</div><div class="sub">%d need attention · %d optimizer import blocked</div></div>
<div class="stat"><div class="stathead"><span><span class="mini">⌕</span>Searches today</span></div><div class="value">%d</div><div class="sub">Optimizer state counter</div></div>
<div class="stat"><div class="stathead"><span><span class="mini">◷</span>Temporary extra today</span></div><div class="value">+%d</div><div class="sub">Resets tomorrow</div></div>
</div>
<div class="layout"><div>
<div class="panel"><div class="panelhead"><div><h3>Recent file changes</h3><p>Observed Radarr upgrade pairs. These are not all necessarily optimizer-triggered.</p></div><a class="badge" href="/radarr/history">HISTORY</a></div>
<table class="changes"><colgroup><col class="releasecol"><col class="sizecol"><col class="sizecol"><col class="changecol"></colgroup><thead><tr><th>Release</th><th>Before</th><th>After</th><th>Change</th></tr></thead><tbody>%s</tbody></table></div>
<div class="panel"><div class="panelhead"><div><h3>Optimizer activity</h3><p>Output from runs started through this dashboard.</p></div><span class="badge">ACTIVITY</span></div><pre>%s</pre></div>
</div>
<div>
<div class="panel"><div class="panelhead"><div><h3>Download radar</h3><p>Live Radarr queue with problem jobs surfaced automatically.</p></div><span class="badge">%d ACTIVE</span></div>%s</div>
<div class="panel"><div class="panelhead"><div><h3>Optimizer intelligence</h3><p>Useful context without pretending Radarr history equals optimizer success.</p></div><span class="badge">SUMMARY</span></div><div class="sidecontent">
<div class="metricline"><span>Observed net reduction</span><b class="%s">%.1f%%</b></div>
<div class="metricline"><span>Smaller replacements</span><b>%d</b></div>
<div class="metricline"><span>Downloads needing attention</span><b class="%s">%d</b></div>
<div class="metricline"><span>Optimizer imports blocked</span><b class="%s">%d</b></div>
<div class="metricline"><span>Last observed upgrade</span><b>%s</b></div>
<div class="metricline"><span>Engine</span><b>%s</b></div>
<div class="metricline"><span>UI mode</span><b>%s</b></div>
</div></div></div></div>
<div class="footer">Radarr Smart Optimizer · storage intelligence, not another Radarr replacement</div>
</div>
<script>
let queueOpen=false;
function toggleQueue(){const b=document.getElementById('queueExpand');if(queueOpen){location.href='/radarr/history';return;}queueOpen=true;document.querySelectorAll('.queueitem.extra').forEach(el=>el.style.display='block');if(b)b.textContent='History →';}
let changesOpen=false;function toggleChanges(){const b=document.getElementById('changesExpand');if(changesOpen){location.href='/radarr/history';return;}changesOpen=true;document.querySelectorAll('.changeextra').forEach(el=>el.style.display='table-row');if(b)b.textContent='History →';}
const box=document.getElementById('librarySearch');
box.addEventListener('input',()=>{const q=box.value.trim().toLowerCase();document.querySelectorAll('.filterrow').forEach(el=>{const match=!q||((el.dataset.search||'').includes(q));if(el.classList.contains('extra')&&!queueOpen&&!q){el.style.display='none';}else{el.style.display=match?'':'none';}});});
</script><script>
(function(){var el=document.querySelector('[id^="runstate-"]');if(!el)return;var app=el.id.replace('runstate-','');async function tick(){try{var r=await fetch('/status?app='+app,{cache:'no-store'});var x=await r.json();el.textContent=x.requested?(x.state.charAt(0).toUpperCase()+x.state.slice(1)+' · '+x.searched+' / '+x.requested+' searched'):'Idle';}catch(e){}}tick();setInterval(tick,10000);})();
</script>%s</body></html>""" % (
        CSS, html.escape(status), actions, warning, err,
        "good" if saved >= 0 else "bad", gib(saved), positive,
        len(queue), len(attention), len(optimizer_blocked), used, extra_today, rows, output, len(queue), qrows,
        "good" if reduction_pct >= 0 else "bad", reduction_pct, positive,
        "bad" if attention else "good", len(attention),
        "bad" if optimizer_blocked else "good", len(optimizer_blocked), html.escape(last_date),
        html.escape(status), "Actions enabled" if ENABLE_ACTIONS else "Read-only", AJAX_SCRIPT)



def home_page():
    return """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Smart Optimizer</title><style>%s</style></head><body><div class="shell homewrap"><div class="homecard"><div class="brand" style="justify-content:center;margin-bottom:24px"><div class="mark">S</div></div><h1>Smart Optimizer</h1><p>Choose the library you want to inspect.</p><div class="chooser"><a class="choice" href="/radarr"><b>Radarr →</b><span>Movies · storage savings · download radar</span></a><a class="choice" href="/sonarr"><b>Sonarr →</b><span>Episodes · storage savings · download radar</span></a></div></div></div></body></html>""" % CSS


def history_page(app):
    try:
        if app == "radarr":
            upgrades = completed_upgrades(history_records())
            title, noun = "Radarr history", "movies"
        else:
            upgrades = sonarr_completed_upgrades(sonarr_history_records())
            title, noun = "Sonarr history", "episodes"
        rows = ""
        for x in upgrades:
            delta = gib(x["saved"])
            cls = "good" if delta >= 0 else "bad"
            rows += "<tr><td>%s</td><td>%.2f GiB</td><td>%.2f GiB</td><td class='%s'>%+.2f GiB</td><td>%s</td></tr>" % (
                html.escape(x["title"]), gib(x["old"]), gib(x["new"]), cls, delta, html.escape((x.get("date") or "")[:19].replace("T", " ")))
        if not rows:
            rows = "<tr><td colspan='5' class='muted'>No completed upgrade pairs found in the loaded history window.</td></tr>"
        err = ""
    except Exception as exc:
        rows = ""
        err = "<div class='notice bad'>%s API error: %s</div>" % (app.capitalize(), html.escape(str(exc)))
        title, noun = app.capitalize() + " history", "items"
    return """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Smart Optimizer UI · %s</title><style>%s</style></head><body><div class="shell">
<div class="topbar compact"><div class="brand"><div class="brandcopy"><h1>%s</h1><div>Complete observed upgrade history available in the loaded API history window.</div></div></div><div class="nav"><a class="badge" href="/%s">← Back to dashboard</a></div></div>
%s
<div class="panel"><div class="panelhead"><div><h3>All recent observed changes</h3><p>Size changes for %s returned by the configured history window.</p></div><span class="badge">HISTORY</span></div>
<table><thead><tr><th>Release</th><th>Before</th><th>After</th><th>Change</th><th>Date</th></tr></thead><tbody>%s</tbody></table></div>
</div></body></html>""" % (html.escape(title), CSS, html.escape(title), app, err, noun, rows)


def sonarr_page():
    state = load_sonarr_state()
    today = time.strftime("%Y-%m-%d")
    used = int((state.get("daily") or {}).get(today, {}).get("searches", 0))
    rule_min, rule_max, extra_today = app_controls("sonarr")
    error = ""
    try:
        records = sonarr_history_records()
        upgrades = sonarr_completed_upgrades(records)
        queue = sonarr_queue_records()
    except Exception as exc:
        upgrades, queue = [], []
        error = str(exc)
    saved = sum(x["saved"] for x in upgrades)
    positive = sum(1 for x in upgrades if x["saved"] > 0)
    total_before = sum(x["old"] for x in upgrades)
    reduction_pct = (saved / total_before * 100.0) if total_before else 0.0
    last_date = upgrades[0]["date"][:10] if upgrades else "—"
    rows = ""
    for i, x in enumerate(upgrades[:15]):
        delta = gib(x["saved"])
        cls = "good" if delta >= 0 else "bad"
        extra = " class='changeextra'" if i >= 5 else ""
        rows += "<tr%s><td>%s</td><td>%.2f GiB</td><td>%.2f GiB</td><td class='%s'>%+.2f GiB</td></tr>" % (
            extra, html.escape(x["title"]), gib(x["old"]), gib(x["new"]), cls, delta)
    if not rows:
        rows = "<tr><td colspan='4' class='muted'>No completed episode upgrade pairs found in the loaded history window.</td></tr>"
    elif len(upgrades) > 5:
        rows += "<tr id='changesExpandRow'><td colspan='4'><button type='button' class='expandbar' id='changesExpand' onclick='toggleChanges()'>Show %d more changes ↓</button></td></tr>" % (min(len(upgrades), 15) - 5)
    qrows = ""
    for i, x in enumerate(queue[:15]):
        size = float(x.get("size") or 0); left = float(x.get("sizeleft") or 0)
        progress = max(0.0, min(100.0, ((size-left)/size*100.0) if size else 0.0))
        title = x.get("title") or ("Episode ID %s" % x.get("episodeId"))
        status = x.get("status") or x.get("trackedDownloadStatus") or "unknown"
        extra = " extra" if i >= 4 else ""
        qrows += "<div class='queueitem%s'><div class='qtop'><div class='qtitle'>%s</div><div>%s</div></div><div class='qmeta'>%.1f%%</div><div class='progress'><span style='width:%.1f%%'></span></div></div>" % (
            extra, html.escape(str(title)), html.escape(str(status)), progress, progress)
    if not qrows:
        qrows = "<div class='empty'>Nothing is currently in Sonarr's download queue.</div>"
    elif len(queue) > 4:
        qrows += "<button type='button' class='expandbar' id='queueExpand' onclick='toggleQueue()'>Show %d more downloads ↓</button>" % (min(len(queue), 15) - 4)
    runstat = manual_status("sonarr")
    runlabel = "Idle" if not runstat["requested"] else ("%s · %d / %d searched" % (runstat["state"].capitalize(), runstat["searched"], runstat["requested"]))
    if runstat.get("running") and runstat.get("current"):
        runlabel += "<span class='runitem'>Now checking: %s</span>" % html.escape(runstat["current"])
    elif not runstat.get("running") and runstat.get("last"):
        runlabel += "<span class='runitem'>Last searched: %s</span>" % html.escape(runstat["last"])
    son_actions = """<div class="controlbar primary"><form class="controlbox manualform" method="post" action="/manual-search"><input type="hidden" name="app" value="sonarr"><label>Manual search</label><input name="count" type="number" min="1" max="%d" value="50"><button %s>Search</button><button class="stopbtn" formaction="/stop" %s>STOP</button></form><span id="runstate-sonarr" class="manualstate">%s</span></div>
<form class="controlbar" method="post" action="/settings"><input type="hidden" name="app" value="sonarr"><div class="controlbox"><label>Downsize</label><input name="min" type="number" min="0" max="100" step="0.1" value="%.1f"><span>–</span><input name="max" type="number" min="0" max="100" step="0.1" value="%.1f"><span>%%</span><button type="submit">Apply</button></div><span class="badge">%d/%d searches · +%d today</span><span class="badge">UHD 1080→2160 exception unchanged</span></form>""" % (MAX_MANUAL, "disabled" if runstat["running"] else "", "" if runstat["running"] else "disabled", runlabel, rule_min, rule_max, used, SONARR_BASE_BUDGET + extra_today, extra_today)
    err = ("<div class='notice bad'>Sonarr API error: %s</div>" % html.escape(error)) if error else ""
    return """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Smart Optimizer UI · Sonarr</title><style>%s</style></head><body><div class="shell">
<div class="topbar compact"><div class="brand"><div class="brandcopy"><h1>Sonarr Optimizer</h1><div>Find smaller releases for your episodes while keeping quality.</div></div></div><div class="nav"><div class="appswitch"><a href="/radarr">Radarr</a><a class="active" href="/sonarr">Sonarr</a></div></div></div>
%s%s
<div class="grid five">
<div class="stat"><div class="stathead"><span><span class="mini">↘</span>Storage saved</span></div><div class="value %s">%+.2f GiB</div><div class="sub">Observed across loaded upgrade history</div></div>
<div class="stat"><div class="stathead"><span><span class="mini">✓</span>Reductions</span></div><div class="value">%d</div><div class="sub">Observed upgrades that ended smaller</div></div>
<div class="stat"><div class="stathead"><span><span class="mini">↓</span>Active downloads</span></div><div class="value">%d</div><div class="sub">Live Sonarr queue</div></div>
<div class="stat"><div class="stathead"><span><span class="mini">⌕</span>Searches today</span></div><div class="value">%d</div><div class="sub">Optimizer state counter</div></div>
<div class="stat"><div class="stathead"><span><span class="mini">◷</span>Temporary extra today</span></div><div class="value">+%d</div><div class="sub">Resets tomorrow</div></div>
</div>
<div class="layout"><div>
<div class="panel"><div class="panelhead"><div><h3>Recent changes</h3><p>Latest optimized episodes and their size changes.</p></div><a class="badge" href="/sonarr/history">HISTORY</a></div><table><thead><tr><th>Release</th><th>Before</th><th>After</th><th>Change</th></tr></thead><tbody>%s</tbody></table></div>
<div class="panel"><div class="panelhead"><div><h3>Settings &amp; status</h3><p>Current configuration and system status.</p></div><span class="badge">CONFIG</span></div><div class="sidecontent">
<div class="metricline"><span>Downsize range</span><b>%.1f – %.1f%%</b></div>
<div class="metricline"><span>UHD (1080 → 2160) exception</span><b>Enabled (unchanged)</b></div>
<div class="metricline"><span>Daily search budget</span><b>%d</b></div>
<div class="metricline"><span>Temporary extra searches</span><b>%d</b></div>
<div class="metricline"><span>Status</span><b class="good">Ready</b></div>
</div></div>
</div>
<div>
<div class="panel"><div class="panelhead"><div><h3>Download radar</h3><p>Live Sonarr queue.</p></div><span class="badge">%d ACTIVE</span></div>%s</div>
<div class="panel"><div class="panelhead"><div><h3>Optimizer intelligence</h3><p>Useful context without pretending Sonarr history equals optimizer success.</p></div><span class="badge">SUMMARY</span></div><div class="sidecontent">
<div class="metricline"><span>Observed net reduction</span><b class="%s">%.1f%%</b></div>
<div class="metricline"><span>Total smaller replacements</span><b>%d</b></div>
<div class="metricline"><span>Active downloads</span><b>%d</b></div>
<div class="metricline"><span>Last observed upgrade</span><b>%s</b></div>
</div></div>
</div></div>
<div class="footer"><a href="/">Smart Optimizer</a> · Sonarr dashboard</div></div>
<script>
let queueOpen=false;function toggleQueue(){const b=document.getElementById('queueExpand');if(queueOpen){location.href='/sonarr/history';return;}queueOpen=true;document.querySelectorAll('.queueitem.extra').forEach(el=>el.style.display='block');if(b)b.textContent='History →';}
let changesOpen=false;function toggleChanges(){const b=document.getElementById('changesExpand');if(changesOpen){location.href='/sonarr/history';return;}changesOpen=true;document.querySelectorAll('.changeextra').forEach(el=>el.style.display='table-row');if(b)b.textContent='History →';}
</script>
%s
</body></html>""" % (
        CSS, son_actions, err, "good" if saved >= 0 else "bad", gib(saved), positive, len(queue), used, extra_today,
        rows, rule_min, rule_max, SONARR_BASE_BUDGET, extra_today, len(queue), qrows,
        "good" if reduction_pct >= 0 else "bad", reduction_pct, positive, len(queue),
        html.escape(last_date), AJAX_SCRIPT)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path == "/status":
            app = (urllib.parse.parse_qs(parsed.query).get("app") or [""])[0]
            if app not in ("radarr", "sonarr"):
                self.send_error(400); return
            body = json.dumps(manual_status(app)).encode("utf-8")
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(body); return
        if path == "/":
            rendered = home_page()
        elif path == "/radarr":
            rendered = page()
        elif path == "/sonarr":
            rendered = sonarr_page()
        elif path == "/radarr/history":
            rendered = history_page("radarr")
        elif path == "/sonarr/history":
            rendered = history_page("sonarr")
        else:
            self.send_error(404); return
        body = rendered.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = min(int(self.headers.get("Content-Length", "0")), 4096)
        form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
        if self.path == "/settings":
            if not ENABLE_ACTIONS:
                self.send_error(403); return
            app = (form.get("app") or [""])[0]
            if app not in ("radarr", "sonarr"):
                self.send_error(400); return
            try:
                update_saving_window(app, float((form.get("min") or [""])[0]), float((form.get("max") or [""])[0]))
            except Exception as exc:
                self.send_error(400, str(exc)); return
            self.send_response(303); self.send_header("Location", "/" + app); self.end_headers(); return
        if self.path == "/manual-search":
            if not ENABLE_ACTIONS: self.send_error(403); return
            app = (form.get("app") or [""])[0]
            try:
                count = int((form.get("count") or [""])[0])
                if app not in ("radarr", "sonarr") or not 1 <= count <= MAX_MANUAL: raise ValueError()
            except Exception:
                self.send_error(400, "Invalid manual search amount"); return
            if not run_optimizer(True, app, count, daily_extra=count):
                self.send_error(409, "Another UI optimizer run is already active"); return
            self.send_response(303); self.send_header("Location", "/" + app); self.end_headers(); return
        if self.path == "/stop":
            if not ENABLE_ACTIONS: self.send_error(403); return
            app = (form.get("app") or [""])[0]
            if app not in ("radarr", "sonarr"): self.send_error(400); return
            stop_optimizer(app)
            self.send_response(303); self.send_header("Location", "/" + app); self.end_headers(); return
        if self.path == "/repair-import":
            if not ENABLE_ACTIONS: self.send_error(403); return
            try:
                repair_import((form.get("queue_id") or [""])[0])
            except Exception as exc:
                print("[ui] safe import failed:", exc)
                self.send_error(409, str(exc)); return
            self.send_response(303); self.send_header("Location", "/radarr"); self.end_headers(); return
        if self.path != "/run" or not ENABLE_ACTIONS:
            self.send_error(403); return
        mode = (form.get("mode") or [""])[0]
        if mode not in ("dry", "live"):
            self.send_error(400); return
        run_optimizer(mode == "live")
        self.send_response(303)
        self.send_header("Location", "/radarr")
        self.end_headers()

    def log_message(self, fmt, *args):
        print("[ui] " + fmt % args)


if __name__ == "__main__":
    print("Radarr Smart Optimizer UI")
    print("Listening on http://%s:%d" % (HOST, PORT))
    print("Actions:", "ENABLED" if ENABLE_ACTIONS else "disabled (read-only)")
    if HOST not in ("127.0.0.1", "localhost", "::1"):
        print("WARNING: UI has no built-in authentication; expose only on a trusted LAN/reverse proxy.")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
