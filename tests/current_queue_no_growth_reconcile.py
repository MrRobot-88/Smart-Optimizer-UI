#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

RAD=Path("/volume1/WDBLACK/radarr-smart-optimizer.py")
SON=Path("/volume1/WDBLACK/sonarr-smart-optimizer.py")
UI=Path("/volume1/WDBLACK/ContainerConfigs/Smart-Optimizer-UI/smart-optimizer-ui.py")
RS=Path("/volume1/WDBLACK/radarr-smart-optimizer-state.json")
SS=Path("/volume1/WDBLACK/sonarr-smart-optimizer-state.json")
CONTROL=Path("/volume1/WDBLACK/ContainerConfigs/Smart-Optimizer-UI/smart-optimizer-control.json")
BACKUP_ROOT=Path("/volume1/WDBLACK/ContainerConfigs/Smart-Optimizer-UI")

EXPECTED={
    RAD:"c9e0d6d52cd468ba080378aabae2b3377c06e93e0eaf691ef50beb59c9e6c5bb",
    SON:"490ba5af4f46bfa713cf627908e6c9f376b2a50121c16a0a8ee67034487e607f",
    UI:"28d014e0115334c43dff0801b3dd771c1919301e5be1d6468d6b3e7d1df8b41d",
}

parser=argparse.ArgumentParser()
parser.add_argument(
    "--apply",
    action="store_true",
    help="blocklist/remove current queue violations and re-search through Smart Optimizer",
)
args=parser.parse_args()

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

for path,expected in EXPECTED.items():
    actual=sha(path)
    if actual != expected:
        raise SystemExit(
            "STOP: source hash mismatch for %s: %s"
            % (path,actual)
        )

p=subprocess.run(
    ["docker","inspect","-f","{{.State.Running}}","smart-optimizer-ui"],
    capture_output=True,
    text=True,
)
if p.returncode != 0 or p.stdout.strip().lower() != "false":
    raise SystemExit("STOP: smart-optimizer-ui must remain STOPPED")

def load_json(path):
    try:
        with open(path,encoding="utf-8") as f:
            x=json.load(f)
            return x if isinstance(x,dict) else {}
    except Exception:
        return {}

def container_api_key(kind):
    needle="/"+kind.lower()+":"
    names=subprocess.run(
        ["docker","ps","-a","--format","{{.Names}}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()

    # Prefer container names containing Radarr/Sonarr.
    ordered=sorted(
        names,
        key=lambda n:(
            kind.lower() not in n.lower(),
            n.lower(),
        ),
    )

    for name in ordered:
        try:
            out=subprocess.run(
                ["docker","exec",name,"cat","/config/config.xml"],
                capture_output=True,
                text=True,
                timeout=15,
            )
        except Exception:
            continue
        if out.returncode != 0:
            continue
        m=re.search(r"<ApiKey>([^<]+)</ApiKey>",out.stdout)
        if m:
            return m.group(1).strip(),name

    raise RuntimeError("could not locate %s API key" % kind)

RAD_KEY,RAD_CONTAINER=container_api_key("Radarr")
SON_KEY,SON_CONTAINER=container_api_key("Sonarr")

def api(base,key,method,path,payload=None):
    data=None
    headers={
        "X-Api-Key":key,
        "Accept":"application/json",
    }
    if payload is not None:
        data=json.dumps(payload).encode()
        headers["Content-Type"]="application/json"

    req=urllib.request.Request(
        base+"/api/v3"+path,
        data=data,
        method=method,
        headers=headers,
    )
    with urllib.request.urlopen(req,timeout=60) as r:
        raw=r.read()
        return json.loads(raw.decode()) if raw else {}

RAD_URL="http://127.0.0.1:7272"
SON_URL="http://127.0.0.1:8989"

def rget(path):
    return api(RAD_URL,RAD_KEY,"GET",path)

def sget(path):
    return api(SON_URL,SON_KEY,"GET",path)

def rdelete(path):
    return api(RAD_URL,RAD_KEY,"DELETE",path)

def sdelete(path):
    return api(SON_URL,SON_KEY,"DELETE",path)

def queue_all(getter,unknown_arg):
    out=[]
    page=1
    while True:
        data=getter(
            "/queue?page=%d&pageSize=250&%s=true"
            % (page,unknown_arg)
        ) or {}
        batch=data.get("records") or []
        out.extend(batch)
        total=int(data.get("totalRecords") or len(out))
        if not batch or len(out)>=total or len(batch)<250:
            break
        page+=1
    return out

rad_state=load_json(RS)
son_state=load_json(SS)

owned_hashes=set()
for state in (rad_state,son_state):
    for job in (state.get("tracker_jobs") or {}).values():
        h=str((job or {}).get("download_id") or "").strip().upper()
        if h:
            owned_hashes.add(h)
    for txn in (state.get("pending_replacements") or {}).values():
        h=str((txn or {}).get("download_id") or "").strip().upper()
        if h:
            owned_hashes.add(h)

def nested_resolution(row):
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

def rad_current(movie_id):
    movie=rget("/movie/%d" % movie_id) or {}
    if not bool(movie.get("hasFile")):
        return movie,None
    files=rget("/moviefile?movieId=%d" % movie_id) or []
    if len(files) != 1:
        return movie,None
    return movie,files[0]

def son_current(episode_id):
    ep=sget("/episode/%d" % episode_id) or {}
    efid=int(ep.get("episodeFileId") or 0)
    if not bool(ep.get("hasFile")) or efid <= 0:
        return ep,None
    ef=sget("/episodefile/%d" % efid) or {}
    return ep,ef

violations=[]
skipped_owned=[]
missing_baseline=[]

for app,rows in (
    ("radarr",queue_all(rget,"includeUnknownMovieItems")),
    ("sonarr",queue_all(sget,"includeUnknownSeriesItems")),
):
    for row in rows:
        qid=int(row.get("id") or 0)
        h=str(row.get("downloadId") or "").strip().upper()
        cand=int(row.get("size") or 0)
        if not qid or not h or cand <= 0:
            continue

        if app=="radarr":
            media_id=int(row.get("movieId") or 0)
            if media_id <= 0:
                continue
            media,current=rad_current(media_id)
            title=str(media.get("title") or row.get("title") or media_id)
        else:
            media_id=int(row.get("episodeId") or 0)
            if media_id <= 0:
                continue
            media,current=son_current(media_id)
            series_title=str((media.get("series") or {}).get("title") or "")
            title=(
                "%s S%02dE%02d"
                % (
                    series_title or "Episode",
                    int(media.get("seasonNumber") or 0),
                    int(media.get("episodeNumber") or 0),
                )
            )

        if not current:
            missing_baseline.append((app,media_id,qid,h,title,cand))
            continue

        old=int(current.get("size") or 0)
        if old <= 0:
            continue

        reason=None

        if cand > old:
            reason="candidate larger than current"

        if (
            app=="radarr"
            and nested_resolution(row)==1080
            and cand > 10*(1024**3)
        ):
            reason=(
                reason+" + 1080p >10 GiB"
                if reason
                else "1080p >10 GiB"
            )

        if not reason:
            continue

        item={
            "app":app,
            "media_id":media_id,
            "queue_id":qid,
            "download_id":h,
            "title":title,
            "candidate_size":cand,
            "current_size":old,
            "reason":reason,
            "owned":h in owned_hashes,
        }

        if item["owned"]:
            skipped_owned.append(item)
        else:
            violations.append(item)

print("===== CURRENT QUEUE NO-GROWTH RECONCILE =====")
print("Mode:","APPLY" if args.apply else "AUDIT ONLY")
print("UI: STOPPED")
print("Policy violations eligible:",len(violations))
print("Optimizer-owned violations skipped:",len(skipped_owned))
print("Missing-file queue rows (no size baseline):",len(missing_baseline))
print()

for x in violations:
    print(
        "VIOLATION %-6s id=%-7s | current=%6.2f GiB | queued=%6.2f GiB | %s | %s"
        % (
            x["app"],
            x["media_id"],
            x["current_size"]/(1024**3),
            x["candidate_size"]/(1024**3),
            x["reason"],
            x["title"],
        )
    )

for x in skipped_owned:
    print(
        "SKIP OWNED %-6s id=%s | %s"
        % (x["app"],x["media_id"],x["title"])
    )

if not args.apply:
    print()
    print("AUDIT COMPLETE -- nothing modified")
    raise SystemExit(0)

if skipped_owned:
    raise SystemExit(
        "STOP: optimizer-owned size violations require transaction-specific handling"
    )

stamp=time.strftime("%Y%m%d-%H%M%S")
backup=BACKUP_ROOT/("pre-current-queue-no-growth-"+stamp)
backup.mkdir(parents=True,exist_ok=False)

for src in (RS,SS):
    if src.exists():
        (backup/src.name).write_bytes(src.read_bytes())

report=backup/"removed-queue-items.json"
report.write_text(
    json.dumps(violations,indent=2,sort_keys=True)+"\n",
    encoding="utf-8",
)
print("BACKUP:",backup)

removed=[]

for x in violations:
    app=x["app"]
    qid=x["queue_id"]
    h=x["download_id"]

    # Revalidate exact queue identity immediately before deletion.
    rows=queue_all(
        rget if app=="radarr" else sget,
        "includeUnknownMovieItems" if app=="radarr" else "includeUnknownSeriesItems",
    )
    matches=[
        row for row in rows
        if int(row.get("id") or 0)==qid
        and str(row.get("downloadId") or "").strip().upper()==h
    ]
    if len(matches) != 1:
        raise RuntimeError(
            "exact queue revalidation failed for %s/%s"
            % (app,qid)
        )

    path=(
        "/queue/%d?removeFromClient=true&blocklist=true"
        % qid
    )

    if app=="radarr":
        rdelete(path)
    else:
        sdelete(path)

    removed.append(x)
    print(
        "REMOVED + BLOCKLISTED:",
        app,
        x["media_id"],
        h[:10]+"...",
        x["title"],
    )

# Verify exact hashes left Arr queue.
for x in removed:
    rows=queue_all(
        rget if x["app"]=="radarr" else sget,
        "includeUnknownMovieItems" if x["app"]=="radarr" else "includeUnknownSeriesItems",
    )
    if any(
        str(row.get("downloadId") or "").strip().upper()==x["download_id"]
        for row in rows
    ):
        raise RuntimeError(
            "removed hash still present in %s queue: %s"
            % (x["app"],x["download_id"])
        )

print()
print("Exact invalid queue rows removed:",len(removed))
print("Library files deleted: NO")
print("Replacement searches launched in this script: NO")
print("============================================")
