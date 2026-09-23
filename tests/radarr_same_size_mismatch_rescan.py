#!/usr/bin/env python3
import json
import re
import subprocess
import time
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
BASE="http://127.0.0.1:7272/api/v3"

def ensure_ui_stopped():
    p=subprocess.run(
        [
            "docker",
            "inspect",
            "-f",
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

def request(path,method="GET",payload=None):
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

def physical_root(movie_path):
    quoted=urllib.parse.quote(movie_path,safe="")
    media=request(
        "/filesystem/mediafiles?path="+quoted
    ) or []
    listing=request(
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

def wait_command(command_id,timeout=180):
    deadline=time.time()+timeout

    while time.time() < deadline:
        cmd=request(
            "/command/%d" % int(command_id)
        ) or {}

        status=str(
            cmd.get("status") or ""
        ).lower()

        if status=="completed":
            return

        if status in ("failed","aborted"):
            raise RuntimeError(
                "command %d ended %s"
                % (command_id,status)
            )

        time.sleep(2)

    raise RuntimeError(
        "command %d timed out"
        % command_id
    )

ensure_ui_stopped()

with open(STATE,encoding="utf-8") as f:
    state=json.load(f)

pending=state.get("pending_replacements") or {}

pre={}

print("===== STRICT PRE-RESCAN VERIFICATION =====")

for mid,title in TARGETS.items():
    txn=pending.get(str(mid))

    if not isinstance(txn,dict):
        raise SystemExit(
            "STOP: movie %d pending transaction missing"
            % mid
        )

    if str(txn.get("status") or "")!="grabbed":
        raise SystemExit(
            "STOP: movie %d transaction status changed"
            % mid
        )

    old_id=int(txn.get("old_file_id") or 0)
    old_size=int(txn.get("old_size") or 0)

    movie=request("/movie/%d" % mid) or {}

    if str(movie.get("title") or "") != title:
        raise SystemExit(
            "STOP: movie %d title changed"
            % mid
        )

    registered=request(
        "/moviefile?movieId=%d" % mid
    ) or []

    if len(registered)!=1:
        raise SystemExit(
            "STOP: movie %d no longer has exactly one registered file"
            % mid
        )

    rf=registered[0]
    reg_id=int(rf.get("id") or 0)
    reg_size=int(rf.get("size") or 0)
    reg_name=str(rf.get("relativePath") or "")

    if reg_id != old_id:
        raise SystemExit(
            "STOP: movie %d registered id is not exact old_file_id"
            % mid
        )

    if reg_size != old_size:
        raise SystemExit(
            "STOP: movie %d registered size is not exact old_size"
            % mid
        )

    physical=physical_root(
        str(movie.get("path") or "")
    )

    if len(physical)!=1:
        raise SystemExit(
            "STOP: movie %d no longer has exactly one physical root video"
            % mid
        )

    phys_name=next(iter(physical))
    phys_size=int(physical[phys_name] or 0)

    if phys_size != old_size:
        raise SystemExit(
            "STOP: movie %d physical size is not exact old_size"
            % mid
        )

    if phys_name == reg_name:
        raise SystemExit(
            "STOP: movie %d is no longer a filename mismatch"
            % mid
        )

    pre[mid]={
        "title":title,
        "old_file_id":old_id,
        "size":old_size,
        "registered_name":reg_name,
        "physical_name":phys_name,
    }

    print(
        "PASS movie=%d | %s | %.3fGiB | reg=%s | phys=%s"
        % (
            mid,
            title,
            old_size/(1024**3),
            reg_name,
            phys_name,
        )
    )

print()
print("===== RADARR RESCAN =====")

for mid,title in TARGETS.items():
    result=request(
        "/command",
        method="POST",
        payload={
            "name":"RescanMovie",
            "movieId":mid,
        },
    ) or {}

    command_id=int(result.get("id") or 0)

    if not command_id:
        raise SystemExit(
            "STOP: Radarr returned no command id for movie %d"
            % mid
        )

    print(
        "Rescanning movie=%d | %s | command=%d"
        % (mid,title,command_id)
    )

    wait_command(command_id)

print()
print("===== POST-RESCAN VERIFICATION =====")

for mid,title in TARGETS.items():
    movie=request("/movie/%d" % mid) or {}

    registered=request(
        "/moviefile?movieId=%d" % mid
    ) or []

    physical=physical_root(
        str(movie.get("path") or "")
    )

    if len(registered)!=1 or len(physical)!=1:
        raise SystemExit(
            "STOP: movie %d did not reach 1 registered + 1 physical"
            % mid
        )

    rf=registered[0]
    reg_name=str(rf.get("relativePath") or "")
    reg_size=int(rf.get("size") or 0)

    phys_name=next(iter(physical))
    phys_size=int(physical[phys_name] or 0)

    if (
        reg_name != phys_name
        or reg_size != phys_size
        or phys_size != pre[mid]["size"]
    ):
        raise SystemExit(
            "STOP: movie %d registration still does not match physical file"
            % mid
        )

    print(
        "PASS movie=%d | %s | file_id=%s | %.3fGiB | %s"
        % (
            mid,
            title,
            rf.get("id"),
            reg_size/(1024**3),
            reg_name,
        )
    )

print()
print("========================================")
print("SAME-SIZE MISMATCH RESCAN: SUCCESS")
print("Movies repaired:",len(TARGETS))
print("Physical movie files modified: NO")
print("Optimizer state modified: NO")
print("smart-optimizer-ui remains STOPPED")
print("========================================")
