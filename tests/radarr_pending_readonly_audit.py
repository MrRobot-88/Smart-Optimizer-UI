#!/usr/bin/env python3
import collections
import json
import os
import re
import urllib.parse
import urllib.request

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

def download_id(obj):
    data=obj.get("data") or {}
    return str(
        obj.get("downloadId")
        or data.get("downloadId")
        or ""
    ).strip().upper()

def root_files(movie_path):
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

with open(STATE,encoding="utf-8") as f:
    state=json.load(f)

pending=state.get("pending_replacements") or {}

print("Pending transactions:",len(pending))
print()
print("Loading Radarr history...")
history=all_pages(
    "/history?sortKey=date&sortDirection=descending"
)
print("History rows:",len(history))

print()
print("Loading Radarr queue...")
try:
    queue=all_pages(
        "/queue?includeUnknownMovieItems=true"
    )
    print("Queue rows:",len(queue))
except Exception as exc:
    queue=[]
    print(
        "QUEUE WARNING:",
        type(exc).__name__,
        str(exc),
    )

queue_by_download={}
for q in queue:
    did=download_id(q)
    if did:
        queue_by_download.setdefault(did,[]).append(q)

history_by_download={}
for h in history:
    did=download_id(h)
    if did:
        history_by_download.setdefault(did,[]).append(h)

def import_matches(mid,did):
    out=[]
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
        data=item.get("data") or {}
        name=os.path.basename(
            str(data.get("importedPath") or "")
        )
        if name:
            out.append(
                {
                    "name":name,
                    "date":item.get("date"),
                }
            )
    return out

counts=collections.Counter()
results=[]

for key,txn in sorted(
    pending.items(),
    key=lambda x:int(x[0]),
):
    try:
        mid=int(key)
        movie=api("/movie/%d" % mid)
        title=str(movie.get("title") or "UNKNOWN")
        movie_path=str(movie.get("path") or "")
        registered=api(
            "/moviefile?movieId=%d" % mid
        ) or []
        physical=root_files(movie_path)

        did=str(
            txn.get("download_id") or ""
        ).strip().upper()

        qmatches=(
            queue_by_download.get(did,[])
            if did else []
        )
        imports=(
            import_matches(mid,did)
            if did else []
        )

        old_size=int(txn.get("old_size") or 0)
        old_path=str(
            txn.get("old_relative_path") or ""
        ).strip()

        reg_map={
            str(x.get("relativePath") or ""):
            int(x.get("size") or 0)
            for x in registered
        }

        category="UNKNOWN"
        note=""

        if qmatches:
            category="ACTIVE_QUEUE"
            q=qmatches[0]
            note="queue=%s/%s" % (
                q.get("status"),
                q.get("trackedDownloadStatus"),
            )

        elif len(registered)==1 and len(physical)==1:
            rname=next(iter(reg_map))
            pname=next(iter(physical))
            same=(
                rname == pname
                and reg_map[rname] > 0
                and reg_map[rname] == physical[pname]
            )
            proven=[
                x for x in imports
                if x["name"] == rname
            ]

            if same and len(proven)==1:
                category="CLEAN_FINALIZABLE"
                note="%.2fGiB import-proven" % (
                    physical[pname]/(1024**3)
                )
            elif same:
                category="CLEAN_UNPROVEN"
                note=(
                    "clean folder but matching optimizer "
                    "import not unique"
                )
            else:
                category="REGISTERED_MISMATCH"
                note="registered/physical mismatch"

        elif len(registered)==0 and len(physical)==1:
            category="MISSING_REGISTRATION"
            name=next(iter(physical))
            note="%.2fGiB %s" % (
                physical[name]/(1024**3),
                name,
            )

        elif len(physical)==2:
            imported_names={
                x["name"]
                for x in imports
                if x["name"] in physical
            }

            if len(imported_names)==1:
                new_name=next(iter(imported_names))
                extras=[
                    n for n in physical
                    if n != new_name
                ]

                if len(extras)==1:
                    candidate=extras[0]
                    exact_old=(
                        old_size > 0
                        and physical[candidate] == old_size
                    )
                    path_ok=(
                        not old_path
                        or old_path == candidate
                    )

                    if exact_old and path_ok:
                        category="SELFHEAL_READY"
                        note=(
                            "new=%.2fGiB old=%.2fGiB"
                            % (
                                physical[new_name]/(1024**3),
                                physical[candidate]/(1024**3),
                            )
                        )
                    else:
                        category="AMBIGUOUS_DIRTY"
                        note=(
                            "second root file does not exactly "
                            "match old transaction identity"
                        )
                else:
                    category="AMBIGUOUS_DIRTY"
            else:
                category="AMBIGUOUS_DIRTY"
                note=(
                    "two root movies; optimizer import "
                    "not uniquely proven"
                )

        elif len(physical)>2:
            category="AMBIGUOUS_DIRTY"
            note="%d root videos" % len(physical)

        elif len(physical)==0:
            category="NO_ROOT_MOVIE"
            note="registered=%d" % len(registered)

        else:
            category="NEEDS_REVIEW"
            note=(
                "registered=%d root=%d imports=%d"
                % (
                    len(registered),
                    len(physical),
                    len(imports),
                )
            )

        counts[category]+=1
        results.append(
            (
                category,
                mid,
                title,
                str(txn.get("status") or ""),
                len(registered),
                len(physical),
                note,
            )
        )

    except Exception as exc:
        counts["API_ERROR"]+=1
        results.append(
            (
                "API_ERROR",
                int(key),
                "?",
                str(txn.get("status") or ""),
                -1,
                -1,
                "%s: %s"
                % (
                    type(exc).__name__,
                    str(exc),
                ),
            )
        )

print()
print("========================================")
print("PENDING AUDIT SUMMARY")
print("========================================")

for category,count in sorted(counts.items()):
    print("%-24s %d" % (category,count))

print()
print("========================================")
print("DETAILS")
print("========================================")

order={
    "CLEAN_FINALIZABLE":0,
    "SELFHEAL_READY":1,
    "MISSING_REGISTRATION":2,
    "ACTIVE_QUEUE":3,
    "CLEAN_UNPROVEN":4,
    "REGISTERED_MISMATCH":5,
    "AMBIGUOUS_DIRTY":6,
    "NO_ROOT_MOVIE":7,
    "NEEDS_REVIEW":8,
    "API_ERROR":9,
}

for row in sorted(
    results,
    key=lambda x:(
        order.get(x[0],99),
        x[1],
    ),
):
    category,mid,title,status,reg,root,note=row
    print(
        "[%s] movie=%d | %s | txn=%s | "
        "reg=%s root=%s | %s"
        % (
            category,
            mid,
            title,
            status,
            reg,
            root,
            note,
        )
    )

print()
print("========================================")
print("AUDIT COMPLETE - NOTHING MODIFIED")
print("smart-optimizer-ui REMAINS STOPPED")
print("========================================")
