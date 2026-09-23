#!/usr/bin/env python3
import hashlib
import http.cookiejar
import json
import os
import re
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path

RAD=Path("/volume1/WDBLACK/radarr-smart-optimizer.py")
UI=Path("/volume1/WDBLACK/ContainerConfigs/Smart-Optimizer-UI/smart-optimizer-ui.py")
CONTROL=Path("/volume1/WDBLACK/ContainerConfigs/Smart-Optimizer-UI/smart-optimizer-control.json")

EXPECTED_RAD="6594cbb61efb9cf1d62b798a7847c39bee86e64c8f03e16ed3517ab10edee85c"
EXPECTED_UI="0da64f80fbfeb809b551f0e3ed915e79306d4e69fb0096deb490f70e6c8fae67"

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

print("===== RADARR SIZE REGRESSION AUDIT =====")
for label,path,expected in (
    ("Radarr optimizer",RAD,EXPECTED_RAD),
    ("Smart Optimizer UI",UI,EXPECTED_UI),
):
    actual=sha(path)
    print(label,actual)
    if actual != expected:
        raise SystemExit("STOP: live source hash mismatch for "+str(path))

# Verify UI is stopped before investigating the regression.
p=subprocess.run(
    ["docker","inspect","-f","{{.State.Running}}","smart-optimizer-ui"],
    capture_output=True,
    text=True,
)
if p.returncode != 0 or p.stdout.strip().lower() != "false":
    raise SystemExit("STOP: smart-optimizer-ui must be stopped for this audit")
print("UI: STOPPED")

# Radarr API key, kept private.
p=subprocess.run(
    ["docker","exec","Radarr-latest-SSD","cat","/config/config.xml"],
    capture_output=True,
    text=True,
)
if p.returncode != 0:
    raise SystemExit("STOP: cannot read Radarr config")
m=re.search(r"<ApiKey>([^<]+)</ApiKey>",p.stdout)
if not m:
    raise SystemExit("STOP: Radarr API key not found")
rad_key=m.group(1).strip()
RAD_URL="http://127.0.0.1:7272/api/v3"

def rget(path):
    req=urllib.request.Request(
        RAD_URL+path,
        headers={"X-Api-Key":rad_key,"Accept":"application/json"},
    )
    with urllib.request.urlopen(req,timeout=30) as r:
        return json.load(r)

def queue_all():
    out=[]
    page=1
    while True:
        data=rget(
            "/queue?page=%d&pageSize=250"
            "&includeUnknownMovieItems=true"
            % page
        ) or {}
        batch=data.get("records") or []
        out.extend(batch)
        total=int(data.get("totalRecords") or len(out))
        if not batch or len(out)>=total or len(batch)<250:
            break
        page+=1
    return out

def history_all():
    out=[]
    page=1
    while page<=10:
        data=rget(
            "/history?page=%d&pageSize=250"
            "&sortKey=date&sortDirection=descending"
            "&includeMovie=true"
            % page
        ) or {}
        batch=data.get("records") or []
        out.extend(batch)
        total=int(data.get("totalRecords") or len(out))
        if not batch or len(out)>=total or len(batch)<250:
            break
        page+=1
    return out

movies=rget("/movie") or []
qrows=queue_all()
history=history_all()

targets=("the book thief","the dark knight rises")

print()
print("===== RADARR TARGETS =====")
target_ids=set()

for wanted in targets:
    found=[
        m for m in movies
        if wanted in str(m.get("title") or "").lower()
    ]
    if not found:
        print(wanted.upper(),": ABSENT FROM /movie")
        continue

    for movie in found:
        mid=int(movie.get("id") or 0)
        target_ids.add(mid)
        mf=rget("/moviefile?movieId=%d" % mid) or []
        print(
            "%s | id=%d | monitored=%s | hasFile=%s | path=%s"
            % (
                movie.get("title"),
                mid,
                movie.get("monitored"),
                movie.get("hasFile"),
                movie.get("path"),
            )
        )
        if not mf:
            print("  registered file: NONE")
        for f in mf:
            size=int(f.get("size") or 0)
            quality=((f.get("quality") or {}).get("quality") or {}).get("name")
            print(
                "  file id=%s | %.2f GiB | quality=%s | %s"
                % (
                    f.get("id"),
                    size/(1024**3),
                    quality,
                    f.get("relativePath") or f.get("path") or "",
                )
            )

print()
print("===== RELEVANT RADARR QUEUE =====")
for row in qrows:
    title=str(row.get("title") or row.get("movie",{}).get("title") or "")
    mid=int(row.get("movieId") or 0)
    if (
        mid in target_ids
        or any(x in title.lower() for x in targets)
    ):
        size=int(row.get("size") or 0)
        print(
            "queue=%s | movie=%s | status=%s | %.2f GiB | hash=%s... | %s"
            % (
                row.get("id"),
                mid,
                row.get("status"),
                size/(1024**3) if size else 0,
                str(row.get("downloadId") or "")[:10],
                title,
            )
        )

print()
print("===== RELEVANT RADARR HISTORY =====")
for ev in history:
    mid=int(ev.get("movieId") or 0)
    source=str(ev.get("sourceTitle") or "")
    movie_title=str((ev.get("movie") or {}).get("title") or "")
    hay=(source+" "+movie_title).lower()
    if mid not in target_ids and not any(x in hay for x in targets):
        continue

    data=ev.get("data") or {}
    print(
        "%s | movie=%s | %-24s | reason=%s | %.80s"
        % (
            ev.get("date") or "",
            mid,
            ev.get("eventType") or "",
            data.get("reason") or "",
            source,
        )
    )

# Deluge read-only status for matching names.
with open(CONTROL,encoding="utf-8") as f:
    dc=(json.load(f).get("deluge") or {})

endpoint="%s://%s:%d/json" % (
    dc.get("scheme") or "http",
    dc.get("host"),
    int(dc.get("port") or 8112),
)
jar=http.cookiejar.CookieJar()
opener=urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(jar)
)
rpc_id=0

def rpc(method,params=None):
    global rpc_id
    rpc_id+=1
    req=urllib.request.Request(
        endpoint,
        data=json.dumps({
            "method":method,
            "params":params or [],
            "id":rpc_id,
        }).encode(),
        headers={"Content-Type":"application/json"},
    )
    with opener.open(req,timeout=20) as r:
        obj=json.load(r)
    if obj.get("error"):
        raise RuntimeError(obj["error"])
    return obj.get("result")

rpc("auth.login",[dc.get("password")])
torrents=rpc(
    "core.get_torrents_status",
    [{},[
        "name","label","state","progress",
        "total_size","tracker","trackers"
    ]]
) or {}

print()
print("===== RELEVANT DELUGE =====")
for h,t in torrents.items():
    name=str((t or {}).get("name") or "")
    if not any(x in name.lower() for x in targets):
        continue
    tracker=str((t or {}).get("tracker") or "")
    host=urllib.parse.urlparse(tracker).hostname or ""
    size=int((t or {}).get("total_size") or 0)
    print(
        "%s... | %-11s | %6.2f%% | %.2f GiB | label=%s | tracker=%s | %s"
        % (
            str(h)[:10],
            (t or {}).get("state") or "",
            float((t or {}).get("progress") or 0),
            size/(1024**3) if size else 0,
            (t or {}).get("label") or "NONE",
            host,
            name,
        )
    )

def anchors(path,patterns,window=10):
    lines=path.read_text(encoding="utf-8").splitlines()
    hits=set()
    for i,line in enumerate(lines):
        if any(p.lower() in line.lower() for p in patterns):
            for n in range(max(0,i-window),min(len(lines),i+window+1)):
                hits.add(n)
    print()
    print("-----",path.name,"-----")
    last=-2
    for n in sorted(hits):
        if n>last+1:
            print("...")
        print("%5d: %s" % (n+1,lines[n]))
        last=n

print()
print("===== SOURCE ANCHORS: SIZE POLICY =====")
anchors(
    RAD,
    [
        "def evaluate_release",
        "current_size",
        "candidate_size",
        "saving_percent",
        "candidate_resolution",
        "current_resolution",
        "1080",
        "2160",
        "def choose_best",
    ],
    8,
)

print()
print("===== SOURCE ANCHORS: WATCHDOG RESEARCH =====")
anchors(
    UI,
    [
        "def _dead_watchdog_native_search",
        "MoviesSearch",
        "EpisodeSearch",
        "CLEANED + RESEARCH",
    ],
    8,
)

print()
print("========================================")
print("SIZE REGRESSION AUDIT COMPLETE")
print("Nothing modified")
print("========================================")
