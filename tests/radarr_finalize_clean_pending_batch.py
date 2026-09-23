#!/usr/bin/env python3
import json
import os
import re
import subprocess
import tempfile
import urllib.parse
import urllib.request

EXPECTED_MOVIES = {
    1927: "Angel Has Fallen",
    1935: "Army of Thieves",
    1980: "Casino Royale",
    2002: "Die Another Day",
    2018: "Ender's Game",
    2038: "Free Guy",
    3555: "A Quiet Place",
    4616: "Hugo",
    4972: "Enola Holmes 3",
    5014: "Arrival",
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

def physical_root_videos(movie_path):
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
        str(x.get("name") or ""):int(x.get("size") or 0)
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

ensure_ui_stopped()

with open(STATE,encoding="utf-8") as f:
    state=json.load(f)

pending=state.get("pending_replacements") or {}

missing=[
    mid
    for mid in EXPECTED_MOVIES
    if str(mid) not in pending
]
if missing:
    raise SystemExit(
        "STOP: expected pending transactions missing: %s"
        % ",".join(str(x) for x in sorted(missing))
    )

print("Loading Radarr history...")
history=all_pages(
    "/history?sortKey=date&sortDirection=descending"
)

history_by_download={}
for item in history:
    data=item.get("data") or {}
    did=str(
        item.get("downloadId")
        or data.get("downloadId")
        or ""
    ).strip().upper()
    if did:
        history_by_download.setdefault(did,[]).append(item)

proofs=[]

for mid,title in sorted(EXPECTED_MOVIES.items()):
    txn=pending.get(str(mid))
    if not isinstance(txn,dict):
        raise SystemExit(
            "STOP: invalid pending transaction for movie %d"
            % mid
        )

    if str(txn.get("status") or "") != "grabbed":
        raise SystemExit(
            "STOP: movie %d transaction status changed: %s"
            % (mid,txn.get("status"))
        )

    did=str(
        txn.get("download_id") or ""
    ).strip().upper()

    if not did:
        raise SystemExit(
            "STOP: movie %d has no exact download id"
            % mid
        )

    movie=api("/movie/%d" % mid)
    actual_title=str(movie.get("title") or "")
    if actual_title != title:
        raise SystemExit(
            "STOP: movie %d title changed: %r"
            % (mid,actual_title)
        )

    movie_path=str(movie.get("path") or "")
    if not movie_path:
        raise SystemExit(
            "STOP: movie %d has no path"
            % mid
        )

    registered=api(
        "/moviefile?movieId=%d" % mid
    ) or []

    if len(registered) != 1:
        raise SystemExit(
            "STOP: movie %d expected 1 registered file, found %d"
            % (mid,len(registered))
        )

    rf=registered[0]
    reg_name=str(rf.get("relativePath") or "")
    reg_size=int(rf.get("size") or 0)
    reg_id=int(rf.get("id") or 0)

    if not reg_name or reg_size <= 0 or reg_id <= 0:
        raise SystemExit(
            "STOP: movie %d registered identity incomplete"
            % mid
        )

    physical=physical_root_videos(movie_path)

    if set(physical) != {reg_name}:
        raise SystemExit(
            "STOP: movie %d physical root state changed"
            % mid
        )

    if int(physical[reg_name] or 0) != reg_size:
        raise SystemExit(
            "STOP: movie %d registered/physical size mismatch"
            % mid
        )

    matches=[]
    for item in history_by_download.get(did,[]):
        hm=item.get("movieId")
        if (
            not hm
            and isinstance(item.get("movie"),dict)
        ):
            hm=item["movie"].get("id")

        if int(hm or 0) != mid:
            continue

        if str(item.get("eventType") or "") != "downloadFolderImported":
            continue

        imported=os.path.basename(
            str(
                (item.get("data") or {})
                .get("importedPath")
                or ""
            )
        )

        if imported == reg_name:
            matches.append(item)

    if len(matches) != 1:
        raise SystemExit(
            "STOP: movie %d exact optimizer import proof count=%d"
            % (mid,len(matches))
        )

    proofs.append(
        {
            "movie_id":mid,
            "title":title,
            "file_id":reg_id,
            "file_name":reg_name,
            "size":reg_size,
            "download_id":did,
            "import_date":matches[0].get("date"),
        }
    )

print()
print("ALL 10 MOVIES RE-VERIFIED BEFORE STATE CHANGE")

for proof in proofs:
    print(
        "movie=%d | %s | %.2fGiB | file_id=%d"
        % (
            proof["movie_id"],
            proof["title"],
            proof["size"]/(1024**3),
            proof["file_id"],
        )
    )

# Re-check that state was not externally changed while API/history proof ran.
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
    if str(
        txn.get("download_id") or ""
    ).strip().upper() != proof["download_id"]:
        raise SystemExit(
            "STOP: download id changed during verification for movie %d"
            % mid
        )
    if str(txn.get("status") or "") != "grabbed":
        raise SystemExit(
            "STOP: status changed during verification for movie %d"
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

    os.chmod(tmp,state_stat.st_mode & 0o777)
    os.replace(tmp,STATE)

    dfd=os.open(state_dir,os.O_DIRECTORY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)
finally:
    if os.path.exists(tmp):
        os.unlink(tmp)

print()
print("========================================")
print("CLEAN PENDING BATCH FINALIZE: SUCCESS")
print("Finalized:",len(proofs))
print("Pending transactions removed: 10")
print("Movie files modified: NO")
print("smart-optimizer-ui remains STOPPED")
print("========================================")
