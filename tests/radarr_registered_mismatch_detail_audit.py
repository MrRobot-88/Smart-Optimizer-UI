#!/usr/bin/env python3
import json
import os
import re
import urllib.parse
import urllib.request

MOVIES = {
    1883: "Live and Let Die",
    2059: "Grease",
    2779: "Journey 2: The Mysterious Island",
    2941: "Brave",
    3329: "Eastern Promises",
    3687: "Beetlejuice Beetlejuice",
    4099: "Armed and Dangerous",
    4152: "Dinosaur",
    4436: "Godzilla x Kong: The New Empire",
}

STATE="/volume1/WDBLACK/radarr-smart-optimizer-state.json"
CFG="/volume1/WDBLACK/ContainerConfigs/Radarr/config.xml"
BASE="http://127.0.0.1:7272/api/v3"

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
        data=api(path+sep+"page=%d&pageSize=250" % page)
        rows=data.get("records") or []
        result.extend(rows)
        total=int(data.get("totalRecords") or len(result))
        if not rows or len(result) >= total:
            break
        page += 1
    return result

def physical_root(movie_path):
    quoted=urllib.parse.quote(movie_path,safe="")
    media=api("/filesystem/mediafiles?path="+quoted) or []
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

    out=[]
    for item in media:
        rel=str(
            item.get("relativePath") or ""
        ).replace("\\","/").strip("/")

        if not rel or "/" in rel:
            continue

        out.append(
            {
                "name":rel,
                "size":sizes.get(rel,0),
                "path":str(item.get("path") or ""),
            }
        )

    return out

with open(STATE,encoding="utf-8") as f:
    state=json.load(f)

pending=state.get("pending_replacements") or {}

print("Loading Radarr history...")
history=all_pages(
    "/history?sortKey=date&sortDirection=descending"
)
print("History rows:",len(history))

history_by_movie={}
for item in history:
    mid=item.get("movieId")
    if (
        not mid
        and isinstance(item.get("movie"),dict)
    ):
        mid=item["movie"].get("id")

    if mid:
        history_by_movie.setdefault(int(mid),[]).append(item)

summary=[]

for mid,expected_title in MOVIES.items():
    print()
    print("============================================================")
    print("MOVIE %d | %s" % (mid,expected_title))
    print("============================================================")

    movie=api("/movie/%d" % mid)
    title=str(movie.get("title") or "")
    path=str(movie.get("path") or "")

    print("TITLE:",title)
    print("PATH :",path)

    if title != expected_title:
        print("WARNING: title differs from expected audit title")

    txn=pending.get(str(mid))

    if isinstance(txn,dict):
        print()
        print("TRANSACTION:")
        print(" status              =",txn.get("status"))
        print(" old_file_id         =",txn.get("old_file_id"))
        print(" old_size            =",txn.get("old_size"))
        print(" old_relative_path   =",txn.get("old_relative_path"))
        print(" approved_size       =",txn.get("approved_size"))
        print(" approved_title      =",txn.get("approved_title"))
        print(" download_id         =",txn.get("download_id"))
    else:
        print()
        print("TRANSACTION: MISSING")

    registered=api(
        "/moviefile?movieId=%d" % mid
    ) or []

    physical=physical_root(path)

    print()
    print("REGISTERED:")
    if not registered:
        print(" NONE")
    for f in registered:
        print(
            " id=%s | %d bytes | %.3f GiB | %s"
            % (
                f.get("id"),
                int(f.get("size") or 0),
                int(f.get("size") or 0)/(1024**3),
                f.get("relativePath"),
            )
        )

    print()
    print("PHYSICAL ROOT:")
    if not physical:
        print(" NONE")
    for f in physical:
        print(
            " %d bytes | %.3f GiB | %s"
            % (
                int(f.get("size") or 0),
                int(f.get("size") or 0)/(1024**3),
                f.get("name"),
            )
        )

    did=""
    if isinstance(txn,dict):
        did=str(
            txn.get("download_id") or ""
        ).strip().upper()

    exact_imports=[]
    movie_history=history_by_movie.get(mid,[])

    for item in movie_history:
        item_did=str(
            item.get("downloadId")
            or (item.get("data") or {}).get("downloadId")
            or ""
        ).strip().upper()

        if did and item_did != did:
            continue

        if str(item.get("eventType") or "") != "downloadFolderImported":
            continue

        data=item.get("data") or {}
        imported=os.path.basename(
            str(data.get("importedPath") or "")
        )

        exact_imports.append(
            {
                "date":item.get("date"),
                "name":imported,
                "quality":data.get("quality"),
                "download_id":item_did,
            }
        )

    print()
    print("EXACT DOWNLOAD-ID IMPORT HISTORY:")
    if not exact_imports:
        print(" NONE")
    for h in exact_imports:
        print(
            " %s | %s"
            % (
                h.get("date"),
                h.get("name"),
            )
        )

    print()
    print("RECENT FILE EVENTS:")

    shown=0
    for item in movie_history:
        et=str(item.get("eventType") or "")
        if et not in (
            "downloadFolderImported",
            "movieFileDeleted",
            "movieFileRenamed",
        ):
            continue

        data=item.get("data") or {}
        name=(
            data.get("importedPath")
            or data.get("reason")
            or data.get("sourcePath")
            or data.get("path")
            or ""
        )

        print(
            " %s | %s | %s"
            % (
                item.get("date"),
                et,
                name,
            )
        )

        shown += 1
        if shown >= 8:
            break

    reg_name=""
    reg_size=0
    if len(registered)==1:
        reg_name=str(
            registered[0].get("relativePath") or ""
        )
        reg_size=int(
            registered[0].get("size") or 0
        )

    phys_name=""
    phys_size=0
    if len(physical)==1:
        phys_name=str(physical[0].get("name") or "")
        phys_size=int(physical[0].get("size") or 0)

    import_names={
        x.get("name")
        for x in exact_imports
        if x.get("name")
    }

    classification="REVIEW"

    if (
        len(registered)==1
        and len(physical)==1
        and phys_name in import_names
        and reg_name != phys_name
    ):
        classification="PHYSICAL_MATCHES_EXACT_IMPORT"
    elif (
        len(registered)==1
        and len(physical)==1
        and reg_name == phys_name
        and reg_size != phys_size
    ):
        classification="SAME_NAME_SIZE_MISMATCH"
    elif (
        len(registered)==1
        and len(physical)==1
        and reg_name != phys_name
    ):
        classification="NAME_MISMATCH_UNPROVEN"
    elif len(registered)!=1 or len(physical)!=1:
        classification="CARDINALITY_CHANGED"

    print()
    print("CLASSIFICATION:",classification)

    summary.append(
        (
            mid,
            title,
            classification,
            reg_name,
            reg_size,
            phys_name,
            phys_size,
        )
    )

print()
print("============================================================")
print("SUMMARY")
print("============================================================")

for row in summary:
    mid,title,cls,rn,rs,pn,ps=row
    print(
        "movie=%d | %s | %s | "
        "reg=%.3fGiB %s | phys=%.3fGiB %s"
        % (
            mid,
            title,
            cls,
            rs/(1024**3),
            rn,
            ps/(1024**3),
            pn,
        )
    )

print()
print("============================================================")
print("REGISTERED-MISMATCH DETAIL AUDIT COMPLETE - NOTHING MODIFIED")
print("============================================================")
