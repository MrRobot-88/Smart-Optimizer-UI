#!/usr/bin/env python3
import http.cookiejar
import json
import os
import re
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path

WARCRAFT_TOKEN="warcraft"
CONTROL=Path("/volume1/WDBLACK/ContainerConfigs/Smart-Optimizer-UI/smart-optimizer-control.json")
RAD_STATE=Path("/volume1/WDBLACK/radarr-smart-optimizer-state.json")
SON_STATE=Path("/volume1/WDBLACK/sonarr-smart-optimizer-state.json")

def sh(args, timeout=60):
    p=subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return p.returncode,p.stdout.strip(),p.stderr.strip()

def human(n):
    n=float(n or 0)
    for unit in ("B","KiB","MiB","GiB","TiB"):
        if abs(n) < 1024 or unit=="TiB":
            return "%.2f %s" % (n,unit)
        n/=1024.0

def api_key(container):
    rc,out,err=sh(["docker","exec",container,"cat","/config/config.xml"])
    if rc!=0:
        raise RuntimeError("cannot read %s config.xml: %s" % (container,err))
    m=re.search(r"<ApiKey>([^<]+)</ApiKey>",out)
    if not m:
        raise RuntimeError("API key missing in "+container)
    return m.group(1).strip()

RAD_KEY=api_key("Radarr-latest-SSD")

def arr(base,key,method,path,payload=None):
    body=None
    headers={
        "X-Api-Key":key,
        "Accept":"application/json",
    }
    if payload is not None:
        body=json.dumps(payload).encode("utf-8")
        headers["Content-Type"]="application/json"
    req=urllib.request.Request(
        base+"/api/v3"+path,
        data=body,
        method=method,
        headers=headers,
    )
    with urllib.request.urlopen(req,timeout=45) as r:
        raw=r.read()
        return json.loads(raw.decode("utf-8")) if raw else {}

RAD_URL="http://127.0.0.1:7272"

def rget(path):
    return arr(RAD_URL,RAD_KEY,"GET",path)

def load_json(path):
    try:
        with open(path,encoding="utf-8") as f:
            x=json.load(f)
            return x if isinstance(x,dict) else {}
    except Exception:
        return {}

def du(path):
    if not path:
        return None
    p=Path(path)
    if not p.exists():
        return None
    rc,out,err=sh(["du","-sh",str(p)],timeout=180)
    return out if rc==0 else "ERROR: "+err

def df(path):
    if not path:
        return None
    p=Path(path)
    if not p.exists():
        return None
    rc,out,err=sh(["df","-h",str(p)])
    return out if rc==0 else "ERROR: "+err

def container_mounts(container):
    rc,out,err=sh(["docker","inspect",container])
    if rc!=0:
        return []
    try:
        obj=json.loads(out)[0]
    except Exception:
        return []
    mounts=[]
    for m in obj.get("Mounts") or []:
        src=str(m.get("Source") or "").rstrip("/")
        dst=str(m.get("Destination") or "").rstrip("/")
        if src and dst:
            mounts.append((dst,src))
    mounts.sort(key=lambda x:len(x[0]),reverse=True)
    return mounts

def host_path(container_path,mounts):
    p=str(container_path or "")
    if not p:
        return ""
    for dst,src in mounts:
        if p==dst or p.startswith(dst+"/"):
            suffix=p[len(dst):]
            return src+suffix
    return p if Path(p).exists() else ""

RAD_MOUNTS=container_mounts("Radarr-latest-SSD")
DELUGE_MOUNTS=container_mounts("Deluge-SSD")

def stat_line(path):
    p=Path(path)
    if not p.exists():
        return None
    try:
        st=p.stat()
        return {
            "path":str(p),
            "size":st.st_size,
            "inode":st.st_ino,
            "links":st.st_nlink,
            "device":st.st_dev,
        }
    except Exception as exc:
        return {"path":str(p),"error":str(exc)}

def list_queue():
    out=[]
    page=1
    while True:
        data=rget(
            "/queue?page=%d&pageSize=250&includeUnknownMovieItems=true"
            % page
        ) or {}
        batch=data.get("records") or []
        out.extend(batch)
        total=int(data.get("totalRecords") or len(out))
        if not batch or len(out)>=total or len(batch)<250:
            break
        page+=1
    return out

def list_history():
    out=[]
    page=1
    while page<=8:
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

def deluge_rpc():
    controls=load_json(CONTROL)
    cfg=controls.get("deluge") or {}
    scheme=str(cfg.get("scheme") or "http").lower()
    host=str(cfg.get("host") or "").strip()
    port=int(cfg.get("port") or 8112)
    password=str(cfg.get("password") or "")
    if not host or not password:
        raise RuntimeError("Deluge connection missing in Smart Optimizer controls")

    url="%s://%s:%d/json" % (scheme,host,port)
    jar=http.cookiejar.CookieJar()
    opener=urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar)
    )
    counter={"id":0}

    def rpc(method,params=None):
        counter["id"]+=1
        req=urllib.request.Request(
            url,
            data=json.dumps({
                "method":method,
                "params":params or [],
                "id":counter["id"],
            }).encode("utf-8"),
            headers={
                "Content-Type":"application/json",
                "Accept":"application/json",
            },
        )
        with opener.open(req,timeout=30) as r:
            obj=json.load(r)
        if obj.get("error"):
            raise RuntimeError("%s: %s" % (method,obj.get("error")))
        return obj.get("result")

    if rpc("auth.login",[password]) is not True:
        raise RuntimeError("Deluge login failed")

    if not bool(rpc("web.connected",[])):
        hosts=rpc("web.get_hosts",[]) or []
        if not hosts or rpc("web.connect",[str(hosts[0][0])]) is not True:
            raise RuntimeError("Deluge daemon unavailable")

    return rpc

print("===== STORAGE + WARCRAFT READ-ONLY AUDIT =====")

rc,out,err=sh(
    ["docker","inspect","-f","{{.State.Running}}","smart-optimizer-ui"]
)
print("smart-optimizer-ui running:",out if rc==0 else "UNKNOWN")

print()
print("===== RADARR MEDIA MANAGEMENT =====")
try:
    mm=rget("/config/mediamanagement") or {}
    print("recycleBin:",mm.get("recycleBin") or "NONE")
    print("recycleBinCleanupDays:",mm.get("recycleBinCleanupDays"))
    print("copyUsingHardlinks:",mm.get("copyUsingHardlinks"))
except Exception as exc:
    mm={}
    print("media management read error:",exc)

roots=rget("/rootfolder") or []

print()
print("===== RADARR ROOTS + FILESYSTEM FREE SPACE =====")
host_roots=[]
for root in roots:
    path=str(root.get("path") or "")
    if not path:
        continue
    hp=host_path(path,RAD_MOUNTS)
    print("ROOT(container):",path)
    print("ROOT(host)     :",hp or "UNMAPPED")
    if hp:
        host_roots.append(hp)
        print(df(hp) or "df unavailable")

# Candidate locations that can retain deleted/replaced bytes.
candidate_dirs=[]

recycle=str(mm.get("recycleBin") or "").strip()
recycle_host=host_path(recycle,RAD_MOUNTS) if recycle else ""
if recycle_host:
    candidate_dirs.append(("Radarr recycle bin",recycle_host))

for rp in host_roots:
    rp=str(rp).rstrip("/")
    if not rp:
        continue
    candidate_dirs.extend([
        ("Share recycle",rp+"/#recycle"),
        ("Snapshot dir",rp+"/.snapshot"),
    ])

candidate_dirs.extend([
    ("Optimizer quarantine","/volume1/WDBLACK/ContainerConfigs/Smart-Optimizer-UI"),
    ("USB optimizer quarantine","/volumeUSB2/usbshare/.smart-optimizer-quarantine"),
    ("USB2 optimizer quarantine","/usb2/.smart-optimizer-quarantine"),
])

print()
print("===== RETENTION / QUARANTINE FOOTPRINTS =====")
seen=set()
for label,path in candidate_dirs:
    if path in seen:
        continue
    seen.add(path)
    if not Path(path).exists():
        continue
    # The optimizer config backups are source/state files and tiny compared
    # with media; only report actual quarantine-like children here.
    if path=="/volume1/WDBLACK/ContainerConfigs/Smart-Optimizer-UI":
        for child in sorted(Path(path).iterdir()):
            if "quarantine" in child.name.lower():
                value=du(str(child))
                if value:
                    print(label,child,":",value)
    else:
        value=du(path)
        if value:
            print(label,path,":",value)

# Btrfs snapshot/subvolume hints.
print()
print("===== BTRFS / SNAPSHOT HINTS =====")
for target in ("/volume1",):
    if not Path(target).exists():
        continue
    rc,out,err=sh(["btrfs","subvolume","list",target],timeout=90)
    if rc==0:
        lines=[
            x for x in out.splitlines()
            if (
                "snapshot" in x.lower()
                or "@sharesnap" in x.lower()
                or "snap" in x.lower()
            )
        ]
        print("snapshot-like subvolumes:",len(lines))
        for line in lines[:80]:
            print(line)
    else:
        print("btrfs subvolume list unavailable:",err or out)

movies=rget("/movie") or []
warcraft=[
    m for m in movies
    if WARCRAFT_TOKEN in str(m.get("title") or "").lower()
]

queue=list_queue()
history=list_history()

print()
print("===== WARCRAFT RADARR =====")
warcraft_ids=set()

for movie in warcraft:
    mid=int(movie.get("id") or 0)
    warcraft_ids.add(mid)
    path=str(movie.get("path") or "")
    print(
        "Movie: %s | id=%s | monitored=%s | hasFile=%s | path=%s"
        % (
            movie.get("title"),
            mid,
            movie.get("monitored"),
            movie.get("hasFile"),
            path,
        )
    )

    files=rget("/moviefile?movieId=%d" % mid) or []
    if not files:
        print("  registered file: NONE")

    for f in files:
        full=str(f.get("path") or "")
        if not full and path:
            full=os.path.join(path,str(f.get("relativePath") or ""))
        full_host=host_path(full,RAD_MOUNTS)
        print(
            "  registered file id=%s | %s | %s"
            % (
                f.get("id"),
                human(f.get("size") or 0),
                f.get("relativePath") or full,
            )
        )
        print("    host path:",full_host or "UNMAPPED")
        st=stat_line(full_host) if full_host else None
        if st:
            print(
                "    stat: inode=%s links=%s size=%s path=%s"
                % (
                    st.get("inode"),
                    st.get("links"),
                    human(st.get("size") or 0),
                    st.get("path"),
                )
            )

print()
print("===== WARCRAFT QUEUE =====")
for row in queue:
    title=str(row.get("title") or "")
    mid=int(row.get("movieId") or 0)
    if mid in warcraft_ids or WARCRAFT_TOKEN in title.lower():
        print(
            "queue=%s | movie=%s | status=%s | %s | hash=%s... | %s"
            % (
                row.get("id"),
                mid,
                row.get("status"),
                human(row.get("size") or 0),
                str(row.get("downloadId") or "")[:10],
                title,
            )
        )

print()
print("===== WARCRAFT RECENT HISTORY =====")
shown=0
for ev in history:
    mid=int(ev.get("movieId") or 0)
    source=str(ev.get("sourceTitle") or "")
    mt=str((ev.get("movie") or {}).get("title") or "")
    hay=(source+" "+mt).lower()
    if mid not in warcraft_ids and WARCRAFT_TOKEN not in hay:
        continue
    data=ev.get("data") or {}
    print(
        "%s | %-25s | movie=%s | download=%s... | reason=%s | %s"
        % (
            ev.get("date") or "",
            ev.get("eventType") or "",
            mid,
            str(ev.get("downloadId") or data.get("downloadId") or "")[:10],
            data.get("reason") or "",
            source,
        )
    )
    shown+=1
    if shown>=40:
        break

rad_state=load_json(RAD_STATE)

print()
print("===== WARCRAFT OPTIMIZER STATE =====")
for mid in sorted(warcraft_ids):
    key=str(mid)
    print("movie",mid)
    print(
        " pending:",
        json.dumps(
            (rad_state.get("pending_replacements") or {}).get(key),
            sort_keys=True,
        )[:2000]
    )
    print(
        " tracker:",
        json.dumps(
            (rad_state.get("tracker_jobs") or {}).get(key),
            sort_keys=True,
        )[:2000]
    )
    print(
        " processed:",
        mid in {
            int(x)
            for x in (rad_state.get("auto_processed_movie_ids") or [])
            if str(x).isdigit()
        }
    )

rpc=deluge_rpc()

try:
    torrents=rpc(
        "core.get_torrents_status",
        [{},[
            "name","state","progress","total_size","save_path",
            "label","tracker","trackers",
        ]],
    ) or {}
except Exception:
    torrents=rpc(
        "core.get_torrents_status",
        [{},[
            "name","state","progress","total_size","save_path",
            "tracker","trackers",
        ]],
    ) or {}

print()
print("===== WARCRAFT DELUGE =====")
warcraft_save_paths=set()

for h,t in torrents.items():
    name=str((t or {}).get("name") or "")
    if WARCRAFT_TOKEN not in name.lower():
        continue
    save_path=str((t or {}).get("save_path") or "")
    save_host=host_path(save_path,DELUGE_MOUNTS)
    if save_host:
        warcraft_save_paths.add(save_host)
    tracker=str((t or {}).get("tracker") or "")
    host=urllib.parse.urlparse(tracker).hostname or ""
    print(
        "%s... | %-11s | %6.2f%% | %s | label=%s | tracker=%s"
        % (
            str(h)[:10],
            (t or {}).get("state") or "",
            float((t or {}).get("progress") or 0),
            human((t or {}).get("total_size") or 0),
            (t or {}).get("label") or "NONE",
            host,
        )
    )
    print("  name:",name)
    print("  save_path(container):",save_path)
    print("  save_path(host)     :",save_host or "UNMAPPED")

print()
print("===== WARCRAFT FILE SEARCH IN RELEVANT LOCATIONS =====")
search_roots=set()

for movie in warcraft:
    p=str(movie.get("path") or "")
    hp=host_path(p,RAD_MOUNTS)
    if hp:
        search_roots.add(hp)

for p in warcraft_save_paths:
    search_roots.add(p)

if recycle_host:
    search_roots.add(recycle_host)

for p in (
    "/volumeUSB2/usbshare/.smart-optimizer-quarantine",
    "/usb2/.smart-optimizer-quarantine",
):
    if Path(p).exists():
        search_roots.add(p)

needle_names=(
    "Warcraft.2016.2160p.UHD.BluRay.HDR.DoVi.TrueHD.7.1.Atmos.x265-SPHD.mkv",
)

found=[]

for root in sorted(search_roots):
    if not Path(root).exists():
        continue
    rc,out,err=sh(
        [
            "find",root,
            "-xdev",
            "-type","f",
            "(",
            "-iname","*warcraft*",
            ")",
            "-print",
        ],
        timeout=120,
    )
    if rc not in (0,1):
        print("find error:",root,err)
        continue
    for line in out.splitlines():
        if line and line not in found:
            found.append(line)

for p in found:
    st=stat_line(p)
    if not st:
        continue
    print(
        "%s | inode=%s links=%s | %s"
        % (
            human(st.get("size") or 0),
            st.get("inode"),
            st.get("links"),
            st.get("path"),
        )
    )

if not found:
    print("No Warcraft-named files found in the relevant roots above.")

print()
print("===== DELUGE PAYLOAD SUMMARY =====")
by_label={}
by_path={}
host_by_path={}

for h,t in torrents.items():
    size=int((t or {}).get("total_size") or 0)
    label=str((t or {}).get("label") or "NONE")
    save_path=str((t or {}).get("save_path") or "UNKNOWN")
    save_host=host_path(save_path,DELUGE_MOUNTS) or "UNMAPPED"
    by_label[label]=by_label.get(label,0)+size
    by_path[save_path]=by_path.get(save_path,0)+size
    host_by_path[save_host]=host_by_path.get(save_host,0)+size

for label,size in sorted(
    by_label.items(),
    key=lambda x:x[1],
    reverse=True,
):
    print("label %-22s %s" % (label,human(size)))

print()
for path,size in sorted(
    by_path.items(),
    key=lambda x:x[1],
    reverse=True,
):
    print("save_path(container) %-32s %s" % (path,human(size)))

print()
print("===== DELUGE HOST PATH DISK USAGE =====")
for path,size in sorted(
    host_by_path.items(),
    key=lambda x:x[1],
    reverse=True,
):
    print("save_path(host) %-38s logical=%s" % (path,human(size)))
    if path!="UNMAPPED" and Path(path).exists():
        value=du(path)
        if value:
            print("  actual du:",value)
        print(df(path) or "")

print()
print("========================================")
print("STORAGE + WARCRAFT AUDIT COMPLETE")
print("Nothing modified")
print("========================================")
