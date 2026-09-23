#!/usr/bin/env python3
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

MOVIE_ID=4658

RAD=Path("/volume1/WDBLACK/radarr-smart-optimizer.py")
STATE=Path("/volume1/WDBLACK/radarr-smart-optimizer-state.json")
CONTROL=Path("/volume1/WDBLACK/ContainerConfigs/Smart-Optimizer-UI/smart-optimizer-control.json")
BACKUP_ROOT=Path("/volume1/WDBLACK/ContainerConfigs/Smart-Optimizer-UI")

EXPECTED_RAD="c9e0d6d52cd468ba080378aabae2b3377c06e93e0eaf691ef50beb59c9e6c5bb"

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

actual=sha(RAD)
if actual != EXPECTED_RAD:
    raise SystemExit("STOP: Radarr optimizer source hash mismatch: "+actual)

p=subprocess.run(
    ["docker","inspect","-f","{{.State.Running}}","smart-optimizer-ui"],
    capture_output=True,
    text=True,
)
if p.returncode != 0 or p.stdout.strip().lower() != "false":
    raise SystemExit("STOP: smart-optimizer-ui must remain STOPPED")

def get_key():
    out=subprocess.run(
        ["docker","exec","Radarr-latest-SSD","cat","/config/config.xml"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    m=re.search(r"<ApiKey>([^<]+)</ApiKey>",out)
    if not m:
        raise RuntimeError("Radarr API key not found")
    return m.group(1).strip()

KEY=get_key()
BASE="http://127.0.0.1:7272/api/v3"

def api(method,path,payload=None):
    body=None
    headers={
        "X-Api-Key":KEY,
        "Accept":"application/json",
    }
    if payload is not None:
        body=json.dumps(payload).encode("utf-8")
        headers["Content-Type"]="application/json"
    req=urllib.request.Request(
        BASE+path,
        data=body,
        method=method,
        headers=headers,
    )
    with urllib.request.urlopen(req,timeout=60) as r:
        raw=r.read()
        return json.loads(raw.decode("utf-8")) if raw else {}

def queue_all():
    out=[]
    page=1
    while True:
        data=api(
            "GET",
            "/queue?page=%d&pageSize=250&includeUnknownMovieItems=true" % page,
        ) or {}
        batch=data.get("records") or []
        out.extend(batch)
        total=int(data.get("totalRecords") or len(out))
        if not batch or len(out)>=total or len(batch)<250:
            break
        page+=1
    return out

def queue_resolution(row):
    q=(row.get("quality") or {}).get("quality") or {}
    try:
        res=int(q.get("resolution") or 0)
        if res:
            return res
    except Exception:
        pass
    title=str(row.get("title") or "")
    m=re.search(r"(?i)(2160|1080|720|480)p?",title)
    return int(m.group(1)) if m else 0

with STATE.open(encoding="utf-8") as f:
    state=json.load(f)

key=str(MOVIE_ID)
txn=(state.get("pending_replacements") or {}).get(key) or {}
job=(state.get("tracker_jobs") or {}).get(key) or {}

owned_hash=str(
    job.get("download_id")
    or txn.get("download_id")
    or ""
).strip().upper()

if not owned_hash:
    raise SystemExit("STOP: no exact optimizer-owned hash for movie 4658")

movie=api("GET","/movie/%d" % MOVIE_ID) or {}
files=api("GET","/moviefile?movieId=%d" % MOVIE_ID) or []

if not bool(movie.get("hasFile")) or len(files)!=1:
    raise SystemExit("STOP: expected exactly one current registered movie file")

current_size=int(files[0].get("size") or 0)
if current_size<=0:
    raise SystemExit("STOP: current movie size unavailable")

rows=queue_all()
matches=[
    row for row in rows
    if int(row.get("movieId") or 0)==MOVIE_ID
    and str(row.get("downloadId") or "").strip().upper()==owned_hash
]

if len(matches)!=1:
    raise SystemExit(
        "STOP: exact optimizer-owned queue row not uniquely found"
    )

row=matches[0]
qid=int(row.get("id") or 0)
candidate_size=int(row.get("size") or 0)
candidate_res=queue_resolution(row)
title=str(row.get("title") or "")

violations=[]

if candidate_size > current_size:
    violations.append("candidate larger than current")

if candidate_res==1080 and candidate_size > 10*(1024**3):
    violations.append("1080p candidate above 10 GiB ceiling")

print("===== OPTIMIZER-OWNED QUEUE POLICY RECONCILE =====")
print("Movie:",movie.get("title"),"| id=",MOVIE_ID)
print("Current file: %.2f GiB" % (current_size/(1024**3)))
print("Queued file : %.2f GiB" % (candidate_size/(1024**3)))
print("Queued res  :",candidate_res or "unknown")
print("Queue title :",title)
print("Exact hash  :",owned_hash[:10]+"...")
print("Violations  :",", ".join(violations) if violations else "NONE")

if not violations:
    print("NO ACTION: exact optimizer-owned queue item satisfies current policy")
    raise SystemExit(0)

stamp=time.strftime("%Y%m%d-%H%M%S")
backup=BACKUP_ROOT/("pre-owned-queue-policy-reconcile-"+stamp)
backup.mkdir(parents=True,exist_ok=False)
(backup/STATE.name).write_bytes(STATE.read_bytes())

print("BACKUP:",backup)

# Revalidate immediately before deletion.
matches=[
    row for row in queue_all()
    if int(row.get("id") or 0)==qid
    and int(row.get("movieId") or 0)==MOVIE_ID
    and str(row.get("downloadId") or "").strip().upper()==owned_hash
]
if len(matches)!=1:
    raise RuntimeError("exact queue revalidation failed")

api(
    "DELETE",
    "/queue/%d?removeFromClient=true&blocklist=true" % qid,
)

changed=False

job=(state.get("tracker_jobs") or {}).get(key) or {}
if str(job.get("download_id") or "").strip().upper()==owned_hash:
    state.setdefault("tracker_jobs",{}).pop(key,None)
    changed=True

txn=(state.get("pending_replacements") or {}).get(key) or {}
if str(txn.get("download_id") or "").strip().upper()==owned_hash:
    state.setdefault("pending_replacements",{}).pop(key,None)
    changed=True

with STATE.open("w",encoding="utf-8") as f:
    json.dump(state,f,indent=2,sort_keys=True)
    f.flush()
    os.fsync(f.fileno())

print("REMOVED + BLOCKLISTED exact policy-violating optimizer torrent")
print("Cleared exact optimizer ownership state:", "YES" if changed else "NO")
print("Library movie file modified: NO")

env=os.environ.copy()
env.update({
    "RADARR_URL":"http://127.0.0.1:7272",
    "RADARR_KEY":KEY,
    "RADARR_OPTIMIZER_STATE":str(STATE),
    "SMART_OPTIMIZER_CONTROL":str(CONTROL),
    "SMART_OPTIMIZER_MOVIE_ID":str(MOVIE_ID),
    "SMART_OPTIMIZER_MANUAL_TARGET":"1",
    "SMART_OPTIMIZER_TARGET_GRABS":"1",
    "RADARR_SEARCHES_PER_RUN":"100",
})

p=subprocess.run(
    ["python3",str(RAD),"--live"],
    env=env,
    capture_output=True,
    text=True,
    timeout=240,
)

log=backup/"optimizer-retry.log"
log.write_text(
    (p.stdout or "")+"\n--- STDERR ---\n"+(p.stderr or ""),
    encoding="utf-8",
)

print("Optimizer retry exit:",p.returncode)

for line in (p.stdout or "").splitlines():
    low=line.lower()
    if any(token in low for token in (
        "absolute size rule",
        "1080p size ceiling",
        "size rule:",
        "ownership bound",
        "upgrade grabbed",
        "release sent",
        "keep current",
        "no qualifying",
        "grab error",
    )):
        print(line)

# Final exact movie queue policy audit.
bad=[]

for row in queue_all():
    if int(row.get("movieId") or 0)!=MOVIE_ID:
        continue

    cand=int(row.get("size") or 0)
    if cand<=0:
        continue

    res=queue_resolution(row)
    reasons=[]

    if cand>current_size:
        reasons.append("larger than current")

    if res==1080 and cand>10*(1024**3):
        reasons.append("1080p >10 GiB")

    if reasons:
        bad.append({
            "queue_id":row.get("id"),
            "hash":str(row.get("downloadId") or "")[:10],
            "size_gib":cand/(1024**3),
            "resolution":res,
            "reasons":reasons,
            "title":row.get("title"),
        })

print()
print("Remaining policy-violating queue rows for movie 4658:",len(bad))

for x in bad:
    print("BAD:",x)

if bad:
    raise SystemExit("STOP: optimizer retry produced a policy violation")

print("FINAL OWNED QUEUE POLICY AUDIT: PASS")
print("UI remains STOPPED")
print("================================================")
