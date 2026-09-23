#!/usr/bin/env python3
import datetime
import http.cookiejar
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request

STATE="/volume1/WDBLACK/radarr-smart-optimizer-state.json"
RADARR_CFG="/volume1/WDBLACK/ContainerConfigs/Radarr/config.xml"
RADARR_DB="/volume1/WDBLACK/ContainerConfigs/Radarr/radarr.db"
CONTROL="/volume1/WDBLACK/ContainerConfigs/Smart-Optimizer-UI/smart-optimizer-control.json"
BACKUP_ROOT="/volume1/WDBLACK/ContainerConfigs/Smart-Optimizer-UI"
QUARANTINE="/volume1/WDBLACK/ContainerConfigs/Smart-Optimizer-UI/radarr-pending-quarantine.json"
BASE="http://127.0.0.1:7272/api/v3"

def stop(msg):
    raise SystemExit("STOP: "+msg)

def ensure_ui_stopped():
    p=subprocess.run(
        ["docker","inspect","-f","{{.State.Running}}","smart-optimizer-ui"],
        capture_output=True,
        text=True,
    )
    if p.returncode != 0:
        stop("cannot verify smart-optimizer-ui state")
    if p.stdout.strip().lower() != "false":
        stop("smart-optimizer-ui must remain stopped")

def make_backup():
    stamp=time.strftime("%Y%m%d-%H%M%S")
    dest=os.path.join(
        BACKUP_ROOT,
        "pre-aggressive-backlog-normalize-"+stamp,
    )
    os.makedirs(dest,exist_ok=False)

    shutil.copy2(
        STATE,
        os.path.join(dest,"radarr-smart-optimizer-state.json"),
    )

    if os.path.exists(QUARANTINE):
        shutil.copy2(
            QUARANTINE,
            os.path.join(dest,"radarr-pending-quarantine.json"),
        )

    src=sqlite3.connect(
        "file:"+RADARR_DB+"?mode=ro",
        uri=True,
    )
    dst=sqlite3.connect(
        os.path.join(dest,"radarr.db")
    )
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()

    return dest

cfg=open(RADARR_CFG,encoding="utf-8").read()
m=re.search(r"<ApiKey>(.*?)</ApiKey>",cfg,re.S)
if not m:
    stop("cannot read Radarr API key")
KEY=m.group(1).strip()

def radarr(path,method="GET",payload=None):
    data=None
    headers={
        "X-Api-Key":KEY,
        "Accept":"application/json",
    }

    if payload is not None:
        data=json.dumps(payload).encode("utf-8")
        headers["Content-Type"]="application/json"

    req=urllib.request.Request(
        BASE+path,
        data=data,
        method=method,
        headers=headers,
    )

    with urllib.request.urlopen(req,timeout=30) as r:
        body=r.read()
        return json.loads(body) if body else None

def all_pages(path,max_pages=50):
    result=[]
    page=1

    while page <= max_pages:
        sep="&" if "?" in path else "?"
        data=radarr(
            path+sep+"page=%d&pageSize=250" % page
        ) or {}

        rows=data.get("records") or []
        result.extend(rows)

        total=int(
            data.get("totalRecords")
            or len(result)
        )

        if not rows or len(result) >= total:
            break

        page += 1

    return result

def wait_command(command_id,timeout=180):
    deadline=time.time()+timeout

    while time.time() < deadline:
        cmd=radarr(
            "/command/%d" % int(command_id)
        ) or {}

        status=str(
            cmd.get("status") or ""
        ).lower()

        if status=="completed":
            return

        if status in ("failed","aborted"):
            raise RuntimeError(
                "Radarr command %d ended %s"
                % (command_id,status)
            )

        time.sleep(2)

    raise RuntimeError(
        "Radarr command %d timed out"
        % command_id
    )

def rescan_movie(movie_id):
    result=radarr(
        "/command",
        method="POST",
        payload={
            "name":"RescanMovie",
            "movieId":int(movie_id),
        },
    ) or {}

    cid=int(result.get("id") or 0)

    if not cid:
        raise RuntimeError(
            "Radarr returned no rescan command id"
        )

    wait_command(cid)

def physical_root(movie_path):
    quoted=urllib.parse.quote(
        movie_path,
        safe="",
    )

    media=radarr(
        "/filesystem/mediafiles?path="+quoted
    ) or []

    listing=radarr(
        "/filesystem?path="
        + quoted
        + "&includeFiles=true"
        + "&allowFoldersWithoutTrailingSlashes=true"
    ) or {}

    sizes={
        str(x.get("name") or ""):
            int(x.get("size") or 0)
        for x in listing.get("files") or []
    }

    result={}

    for item in media:
        rel=str(
            item.get("relativePath") or ""
        ).replace("\\","/").strip("/")

        if not rel or "/" in rel:
            continue

        result[rel]=sizes.get(rel,0)

    return result

def load_deluge():
    try:
        with open(CONTROL,encoding="utf-8") as f:
            controls=json.load(f)
    except Exception as exc:
        raise RuntimeError(
            "cannot read Smart Optimizer control file: %s"
            % exc
        )

    dcfg=controls.get("deluge") or {}

    scheme=str(
        dcfg.get("scheme") or "http"
    ).lower()

    host=str(
        dcfg.get("host") or ""
    ).strip()

    port=int(
        dcfg.get("port") or 8112
    )

    password=str(
        dcfg.get("password") or ""
    )

    if scheme not in ("http","https"):
        raise RuntimeError(
            "invalid saved Deluge scheme"
        )

    if not host or not password:
        raise RuntimeError(
            "Deluge connection is incomplete in Smart Optimizer controls"
        )

    endpoint="%s://%s:%d/json" % (
        scheme,
        host,
        port,
    )

    jar=http.cookiejar.CookieJar()
    opener=urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar)
    )

    rpc_id=0

    def rpc(method,params):
        nonlocal rpc_id
        rpc_id += 1

        body=json.dumps(
            {
                "method":method,
                "params":params,
                "id":rpc_id,
            }
        ).encode("utf-8")

        req=urllib.request.Request(
            endpoint,
            data=body,
            method="POST",
            headers={
                "Content-Type":"application/json",
                "Accept":"application/json",
            },
        )

        with opener.open(req,timeout=30) as r:
            obj=json.load(r)

        if obj.get("error"):
            raise RuntimeError(
                "%s: %s"
                % (method,obj.get("error"))
            )

        return obj.get("result")

    if rpc("auth.login",[password]) is not True:
        raise RuntimeError(
            "Deluge Web authentication failed"
        )

    if not bool(rpc("web.connected",[])):
        hosts=rpc("web.get_hosts",[]) or []

        if not hosts:
            raise RuntimeError(
                "Deluge Web has no daemon hosts"
            )

        if rpc(
            "web.connect",
            [str(hosts[0][0])],
        ) is not True:
            raise RuntimeError(
                "Deluge Web could not connect to daemon"
            )

    torrents=rpc(
        "core.get_torrents_status",
        [{},["name","state","progress"]],
    )

    if not isinstance(torrents,dict):
        raise RuntimeError(
            "Deluge returned invalid torrent listing"
        )

    out={}

    for h,data in torrents.items():
        out[str(h).strip().upper()]={
            "name":str(
                (data or {}).get("name") or ""
            ),
            "state":str(
                (data or {}).get("state") or ""
            ),
            "progress":float(
                (data or {}).get("progress") or 0
            ),
        }

    return out

def deluge_is_active(item):
    if not item:
        return False

    state=str(
        item.get("state") or ""
    ).lower()

    progress=float(
        item.get("progress") or 0
    )

    if state in (
        "downloading",
        "queued",
        "checking",
        "allocating",
        "moving",
    ):
        return True

    if progress < 99.9 and state not in (
        "seeding",
        "paused",
        "error",
    ):
        return True

    return False

def atomic_json(path,data):
    directory=os.path.dirname(path)

    fd,tmp=tempfile.mkstemp(
        prefix="."+os.path.basename(path)+".",
        suffix=".tmp",
        dir=directory,
    )

    try:
        with os.fdopen(
            fd,
            "w",
            encoding="utf-8",
        ) as f:
            json.dump(
                data,
                f,
                indent=2,
                sort_keys=True,
            )
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())

        if os.path.exists(path):
            st=os.stat(path)
            os.chmod(
                tmp,
                st.st_mode & 0o777,
            )

        os.replace(tmp,path)

        dfd=os.open(
            directory,
            os.O_DIRECTORY,
        )
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)

    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

def mark_closed(state,movie_id):
    pending=state.setdefault(
        "pending_replacements",
        {},
    )

    pending.pop(
        str(movie_id),
        None,
    )

    entry=state.setdefault(
        "movies",
        {},
    ).setdefault(
        str(movie_id),
        {},
    )

    entry["search_cycles"]=max(
        1,
        int(
            entry.get("search_cycles",0)
        ),
    )

    entry["auto_processed"]=True

    processed={
        int(x)
        for x in state.get(
            "auto_processed_movie_ids",
            [],
        )
        if str(x).isdigit()
    }

    processed.add(
        int(movie_id)
    )

    state[
        "auto_processed_movie_ids"
    ]=sorted(processed)

ensure_ui_stopped()
backup=make_backup()

print("BACKUP:",backup)
print()
print("Loading Radarr queue...")

queue=all_pages(
    "/queue?includeUnknownMovieItems=true"
)

queue_ids=set()

for item in queue:
    data=item.get("data") or {}

    did=str(
        item.get("downloadId")
        or data.get("downloadId")
        or ""
    ).strip().upper()

    if did:
        queue_ids.add(did)

print("Radarr queue rows:",len(queue))

print("Loading Deluge...")
deluge=load_deluge()
print("Deluge torrents:",len(deluge))

with open(STATE,encoding="utf-8") as f:
    original=json.load(f)

state=json.loads(
    json.dumps(original)
)

pending=dict(
    state.get("pending_replacements")
    or {}
)

try:
    with open(QUARANTINE,encoding="utf-8") as f:
        quarantine=json.load(f)
        if not isinstance(quarantine,dict):
            quarantine={}
except FileNotFoundError:
    quarantine={}

closed_clean=[]
closed_rescanned=[]
quarantined=[]
left_active=[]
left_blocked=[]

print()
print("===== AGGRESSIVE BACKLOG NORMALIZATION =====")

for key,txn in sorted(
    pending.items(),
    key=lambda x:int(x[0]),
):
    mid=int(key)

    if not isinstance(txn,dict):
        left_blocked.append(
            (mid,"invalid transaction")
        )
        continue

    did=str(
        txn.get("download_id") or ""
    ).strip().upper()

    if did and did in queue_ids:
        left_active.append(
            (mid,"Radarr queue")
        )
        continue

    ditem=deluge.get(did) if did else None

    if deluge_is_active(ditem):
        left_active.append(
            (
                mid,
                "Deluge %s %.1f%%"
                % (
                    ditem.get("state"),
                    ditem.get("progress"),
                ),
            )
        )
        continue

    try:
        movie=radarr(
            "/movie/%d" % mid
        ) or {}

        title=str(
            movie.get("title") or "UNKNOWN"
        )

        movie_path=str(
            movie.get("path") or ""
        )

        registered=radarr(
            "/moviefile?movieId=%d" % mid
        ) or []

        physical=physical_root(
            movie_path
        )

    except Exception as exc:
        left_blocked.append(
            (
                mid,
                "inspection failed: %s"
                % exc,
            )
        )
        continue

    if (
        len(registered)==1
        and len(physical)==1
    ):
        rf=registered[0]

        rname=str(
            rf.get("relativePath") or ""
        )

        rsize=int(
            rf.get("size") or 0
        )

        pname=next(
            iter(physical)
        )

        psize=int(
            physical[pname] or 0
        )

        if (
            rname==pname
            and rsize>0
            and rsize==psize
        ):
            mark_closed(
                state,
                mid,
            )

            closed_clean.append(
                (
                    mid,
                    title,
                    rsize,
                )
            )
            continue

        # Exactly one movie exists physically. Refresh stale Radarr
        # registration, then only close if Radarr converges exactly to disk.
        try:
            rescan_movie(mid)

            registered=radarr(
                "/moviefile?movieId=%d" % mid
            ) or []

            physical=physical_root(
                movie_path
            )

        except Exception as exc:
            left_blocked.append(
                (
                    mid,
                    "rescan failed: %s"
                    % exc,
                )
            )
            continue

        if (
            len(registered)==1
            and len(physical)==1
        ):
            rf=registered[0]

            rname=str(
                rf.get("relativePath") or ""
            )

            rsize=int(
                rf.get("size") or 0
            )

            pname=next(
                iter(physical)
            )

            psize=int(
                physical[pname] or 0
            )

            if (
                rname==pname
                and rsize>0
                and rsize==psize
            ):
                mark_closed(
                    state,
                    mid,
                )

                closed_rescanned.append(
                    (
                        mid,
                        title,
                        rsize,
                    )
                )
                continue

        left_blocked.append(
            (
                mid,
                "registration still mismatched after rescan",
            )
        )
        continue

    if len(physical)>1:
        quarantine[str(mid)]={
            "movie_id":mid,
            "title":title,
            "movie_path":movie_path,
            "reason":"multiple physical root movie files",
            "quarantined_at_utc":(
                datetime.datetime.now(
                    datetime.timezone.utc
                ).isoformat()
            ),
            "transaction":txn,
            "registered":[
                {
                    "id":int(
                        x.get("id") or 0
                    ),
                    "relativePath":str(
                        x.get("relativePath") or ""
                    ),
                    "size":int(
                        x.get("size") or 0
                    ),
                }
                for x in registered
            ],
            "physical_root":physical,
        }

        mark_closed(
            state,
            mid,
        )

        quarantined.append(
            (
                mid,
                title,
                len(physical),
            )
        )
        continue

    left_blocked.append(
        (
            mid,
            "registered=%d physical=%d"
            % (
                len(registered),
                len(physical),
            ),
        )
    )

# Re-check UI before committing state.
ensure_ui_stopped()

atomic_json(
    STATE,
    state,
)

if quarantined:
    atomic_json(
        QUARANTINE,
        quarantine,
    )

print()
print("===== RESULTS =====")

for mid,title,size in closed_clean:
    print(
        "CLOSED CLEAN       movie=%d | %s | %.3fGiB"
        % (
            mid,
            title,
            size/(1024**3),
        )
    )

for mid,title,size in closed_rescanned:
    print(
        "RESCANNED+CLOSED   movie=%d | %s | %.3fGiB"
        % (
            mid,
            title,
            size/(1024**3),
        )
    )

for mid,title,count in quarantined:
    print(
        "QUARANTINED DIRTY  movie=%d | %s | root_videos=%d"
        % (
            mid,
            title,
            count,
        )
    )

for mid,reason in left_active:
    print(
        "LEFT ACTIVE        movie=%d | %s"
        % (
            mid,
            reason,
        )
    )

for mid,reason in left_blocked:
    print(
        "LEFT BLOCKED       movie=%d | %s"
        % (
            mid,
            reason,
        )
    )

remaining=state.get(
    "pending_replacements"
) or {}

print()
print("========================================")
print("AGGRESSIVE BACKLOG NORMALIZE: COMPLETE")
print("Closed clean stale:",len(closed_clean))
print("Rescanned + closed:",len(closed_rescanned))
print("Dirty transaction quarantined:",len(quarantined))
print("Still active:",len(left_active))
print("Still blocked:",len(left_blocked))
print("Remaining pending:",len(remaining))
print("Movie files deleted/moved: NO")
print("Deluge torrents modified: NO")
print("smart-optimizer-ui remains STOPPED")
print("========================================")
