#!/usr/bin/env python3
import json
import os
import re
import subprocess
import tempfile
import http.cookiejar
import urllib.parse
import urllib.request

TARGETS = {
    1883: "Live and Let Die",
    2059: "Grease",
    2779: "Journey 2: The Mysterious Island",
    2941: "Brave",
    3329: "Eastern Promises",
}

STATE="/volume1/WDBLACK/radarr-smart-optimizer-state.json"
CFG="/volume1/WDBLACK/ContainerConfigs/Radarr/config.xml"
CONTROL="/volume1/WDBLACK/ContainerConfigs/Smart-Optimizer-UI/smart-optimizer-control.json"
BASE="http://127.0.0.1:7272/api/v3"

def ensure_ui_stopped():
    p=subprocess.run(
        [
            "docker","inspect","-f",
            "{{.State.Running}}",
            "smart-optimizer-ui",
        ],
        capture_output=True,
        text=True,
    )
    if p.returncode != 0:
        raise SystemExit(
            "STOP: cannot verify smart-optimizer-ui state"
        )
    if p.stdout.strip().lower() != "false":
        raise SystemExit(
            "STOP: smart-optimizer-ui must remain stopped"
        )

cfg=open(CFG,encoding="utf-8").read()
m=re.search(r"<ApiKey>(.*?)</ApiKey>",cfg,re.S)

if not m:
    raise SystemExit("STOP: cannot read Radarr API key")

KEY=m.group(1).strip()

def api(path):
    req=urllib.request.Request(
        BASE+path,
        headers={
            "X-Api-Key":KEY,
            "Accept":"application/json",
        },
    )
    with urllib.request.urlopen(req,timeout=30) as r:
        return json.load(r)

def all_pages(path,max_pages=50):
    result=[]
    page=1
    while page <= max_pages:
        sep="&" if "?" in path else "?"
        data=api(
            path
            + sep
            + "page=%d&pageSize=250" % page
        )
        rows=data.get("records") or []
        result.extend(rows)
        total=int(data.get("totalRecords") or len(result))
        if not rows or len(result) >= total:
            break
        page += 1
    return result

def physical_root(movie_path):
    quoted=urllib.parse.quote(movie_path,safe="")
    media=api(
        "/filesystem/mediafiles?path="+quoted
    ) or []
    listing=api(
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

def load_deluge_hashes():
    """
    Read-only Deluge Web JSON-RPC query using the already-saved local UI
    connection settings. No password/token is embedded or printed.
    """
    try:
        with open(CONTROL,encoding="utf-8") as f:
            controls=json.load(f)
    except Exception as exc:
        raise RuntimeError(
            "cannot read Smart Optimizer control file: %s"
            % exc
        )

    dcfg=(controls.get("deluge") or {})

    scheme=str(
        dcfg.get("scheme") or "http"
    ).lower()

    if scheme not in ("http","https"):
        raise RuntimeError(
            "invalid saved Deluge scheme"
        )

    host=str(
        dcfg.get("host") or ""
    ).strip()

    port=int(
        dcfg.get("port") or 8112
    )

    password=str(
        dcfg.get("password") or ""
    )

    if not host:
        raise RuntimeError(
            "Deluge host is not configured in Smart Optimizer controls"
        )

    if not password:
        raise RuntimeError(
            "Deluge Web password is not configured in Smart Optimizer controls"
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

        data=json.dumps(
            {
                "method":method,
                "params":params,
                "id":rpc_id,
            }
        ).encode("utf-8")

        req=urllib.request.Request(
            endpoint,
            data=data,
            headers={
                "Content-Type":"application/json",
                "Accept":"application/json",
            },
            method="POST",
        )

        with opener.open(req,timeout=30) as r:
            obj=json.load(r)

        if obj.get("error"):
            raise RuntimeError(
                "%s: %s"
                % (
                    method,
                    obj.get("error"),
                )
            )

        return obj.get("result")

    if rpc("auth.login",[password]) is not True:
        raise RuntimeError(
            "Deluge Web authentication failed"
        )

    connected=bool(
        rpc("web.connected",[])
    )

    if not connected:
        hosts=rpc(
            "web.get_hosts",
            []
        ) or []

        if not hosts:
            raise RuntimeError(
                "Deluge Web has no daemon hosts"
            )

        host_id=str(
            hosts[0][0]
        )

        if rpc(
            "web.connect",
            [host_id]
        ) is not True:
            raise RuntimeError(
                "Deluge Web could not connect to daemon"
            )

    torrents=rpc(
        "core.get_torrents_status",
        [{},["name"]],
    )

    if not isinstance(torrents,dict):
        raise RuntimeError(
            "Deluge returned invalid torrent listing"
        )

    return {
        str(k).strip().upper()
        for k in torrents
        if str(k).strip()
    }

ensure_ui_stopped()

with open(STATE,encoding="utf-8") as f:
    state=json.load(f)

pending=state.get("pending_replacements") or {}

print("Loading full Radarr queue...")
queue=all_pages(
    "/queue?includeUnknownMovieItems=true"
)

queue_ids=set()
for q in queue:
    data=q.get("data") or {}
    did=str(
        q.get("downloadId")
        or data.get("downloadId")
        or ""
    ).strip().upper()
    if did:
        queue_ids.add(did)

print("Queue rows:",len(queue))

print("Loading read-only Deluge torrent list...")
deluge_hashes=load_deluge_hashes()
print("Deluge torrents:",len(deluge_hashes))

print()
print("===== STRICT STALE-PENDING PROOF =====")

proofs=[]

for mid,title in TARGETS.items():
    txn=pending.get(str(mid))

    if not isinstance(txn,dict):
        raise SystemExit(
            "STOP: movie %d pending transaction missing"
            % mid
        )

    if str(txn.get("status") or "") != "grabbed":
        raise SystemExit(
            "STOP: movie %d transaction status changed"
            % mid
        )

    did=str(
        txn.get("download_id") or ""
    ).strip().upper()

    if not did:
        raise SystemExit(
            "STOP: movie %d download id missing"
            % mid
        )

    if did in queue_ids:
        raise SystemExit(
            "STOP: movie %d exact download is still in Radarr queue"
            % mid
        )

    movie=api("/movie/%d" % mid)

    if str(movie.get("title") or "") != title:
        raise SystemExit(
            "STOP: movie %d title changed"
            % mid
        )

    registered=api(
        "/moviefile?movieId=%d" % mid
    ) or []

    physical=physical_root(
        str(movie.get("path") or "")
    )

    if len(registered)!=1 or len(physical)!=1:
        raise SystemExit(
            "STOP: movie %d is no longer one registered + one physical"
            % mid
        )

    rf=registered[0]
    reg_name=str(rf.get("relativePath") or "")
    reg_size=int(rf.get("size") or 0)
    phys_name=next(iter(physical))
    phys_size=int(physical[phys_name] or 0)

    if (
        not reg_name
        or reg_name != phys_name
        or reg_size <= 0
        or reg_size != phys_size
    ):
        raise SystemExit(
            "STOP: movie %d registration no longer exactly matches disk"
            % mid
        )

    old_size=int(txn.get("old_size") or 0)

    if old_size <= 0 or reg_size != old_size:
        raise SystemExit(
            "STOP: movie %d current file is not exact old-size survivor"
            % mid
        )

    present=(did in deluge_hashes)

    if present:
        raise SystemExit(
            "STOP: movie %d exact download still exists in Deluge"
            % mid
        )

    proofs.append(
        {
            "movie_id":mid,
            "title":title,
            "download_id":did,
            "file_name":reg_name,
            "size":reg_size,
        }
    )

    print(
        "PASS movie=%d | %s | %.3fGiB | "
        "Radarr queue=absent | Deluge=absent"
        % (
            mid,
            title,
            reg_size/(1024**3),
        )
    )

print()
print("ALL TARGETS PROVEN STALE AND SAFE TO CLOSE")

# Re-read state so an external state change cannot be overwritten silently.
with open(STATE,encoding="utf-8") as f:
    latest=json.load(f)

latest_pending=latest.get("pending_replacements") or {}

for proof in proofs:
    mid=proof["movie_id"]
    txn=latest_pending.get(str(mid))

    if not isinstance(txn,dict):
        raise SystemExit(
            "STOP: state changed during verification for movie %d"
            % mid
        )

    if str(txn.get("status") or "") != "grabbed":
        raise SystemExit(
            "STOP: status changed during verification for movie %d"
            % mid
        )

    if str(
        txn.get("download_id") or ""
    ).strip().upper() != proof["download_id"]:
        raise SystemExit(
            "STOP: download id changed during verification for movie %d"
            % mid
        )

processed={
    int(x)
    for x in latest.get(
        "auto_processed_movie_ids",
        []
    )
    if str(x).isdigit()
}

movies=latest.setdefault("movies",{})
latest_pending=latest.setdefault(
    "pending_replacements",
    {}
)

for proof in proofs:
    mid=proof["movie_id"]
    latest_pending.pop(str(mid),None)

    entry=movies.setdefault(str(mid),{})
    entry["search_cycles"]=max(
        1,
        int(entry.get("search_cycles",0))
    )
    entry["auto_processed"]=True
    processed.add(mid)

latest["auto_processed_movie_ids"]=sorted(processed)

state_dir=os.path.dirname(STATE)
state_stat=os.stat(STATE)

fd,tmp=tempfile.mkstemp(
    prefix=".radarr-smart-optimizer-state.",
    suffix=".tmp",
    dir=state_dir,
)

try:
    with os.fdopen(fd,"w",encoding="utf-8") as f:
        json.dump(
            latest,
            f,
            indent=2,
            sort_keys=True,
        )
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())

    os.chmod(
        tmp,
        state_stat.st_mode & 0o777,
    )

    os.replace(tmp,STATE)

    dfd=os.open(
        state_dir,
        os.O_DIRECTORY,
    )
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)

finally:
    if os.path.exists(tmp):
        os.unlink(tmp)

print()
print("========================================")
print("STALE PENDING CLOSE: SUCCESS")
print("Transactions closed:",len(proofs))
print("Automatic optimizer: PERMANENTLY CLOSED for all 5")
print("Movie files modified: NO")
print("Deluge torrents modified: NO")
print("smart-optimizer-ui remains STOPPED")
print("========================================")
