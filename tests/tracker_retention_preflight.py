#!/usr/bin/env python3
import http.cookiejar
import json
import os
import re
import subprocess
import urllib.parse
import urllib.request

RAD_STATE="/volume1/WDBLACK/radarr-smart-optimizer-state.json"
SON_STATE="/volume1/WDBLACK/sonarr-smart-optimizer-state.json"
CONTROL="/volume1/WDBLACK/ContainerConfigs/Smart-Optimizer-UI/smart-optimizer-control.json"
RAD_SRC="/volume1/WDBLACK/radarr-smart-optimizer.py"
SON_SRC="/volume1/WDBLACK/sonarr-smart-optimizer.py"
UI_SRC="/volume1/WDBLACK/ContainerConfigs/Smart-Optimizer-UI/smart-optimizer-ui.py"

TL_HOST_TOKENS=("torrentleech.org","torrentleech.me")

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

def load_json(path):
    with open(path,encoding="utf-8") as f:
        return json.load(f)

def deluge_rpc():
    controls=load_json(CONTROL)
    dcfg=controls.get("deluge") or {}

    scheme=str(dcfg.get("scheme") or "http").lower()
    host=str(dcfg.get("host") or "").strip()
    port=int(dcfg.get("port") or 8112)
    password=str(dcfg.get("password") or "")

    if scheme not in ("http","https"):
        stop("invalid saved Deluge scheme")
    if not host or not password:
        stop("Deluge connection incomplete in Smart Optimizer controls")

    endpoint="%s://%s:%d/json" % (scheme,host,port)
    jar=http.cookiejar.CookieJar()
    opener=urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar)
    )
    counter={"id":0}

    def rpc(method,params):
        counter["id"] += 1
        body=json.dumps({
            "method":method,
            "params":params,
            "id":counter["id"],
        }).encode("utf-8")

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
            raise RuntimeError("%s: %s" % (method,obj.get("error")))

        return obj.get("result")

    if rpc("auth.login",[password]) is not True:
        stop("Deluge Web authentication failed")

    if not bool(rpc("web.connected",[])):
        hosts=rpc("web.get_hosts",[]) or []
        if not hosts:
            stop("Deluge Web has no daemon hosts")
        if rpc("web.connect",[str(hosts[0][0])]) is not True:
            stop("Deluge Web could not connect to daemon")

    return rpc

def tracker_hosts(item):
    hosts=set()

    tracker=str((item or {}).get("tracker") or "").strip()
    if tracker:
        try:
            host=urllib.parse.urlparse(tracker).hostname
        except Exception:
            host=None
        if host:
            hosts.add(host.lower())

    for tr in (item or {}).get("trackers") or []:
        url=str((tr or {}).get("url") or "").strip()
        if not url:
            continue
        try:
            host=urllib.parse.urlparse(url).hostname
        except Exception:
            host=None
        if host:
            hosts.add(host.lower())

    return sorted(hosts)

def is_tl(hosts):
    for host in hosts:
        if any(
            host==token or host.endswith("."+token)
            for token in TL_HOST_TOKENS
        ):
            return True
    return False

def label_of(item):
    return str((item or {}).get("label") or "").strip()

def pending_map(path):
    try:
        data=load_json(path)
    except FileNotFoundError:
        return {}
    return data.get("pending_replacements") or {}

def safe_source_anchors(path,patterns):
    try:
        lines=open(path,encoding="utf-8").read().splitlines()
    except Exception as exc:
        return ["ERROR reading %s: %s" % (path,exc)]

    out=[]
    for i,line in enumerate(lines,1):
        low=line.lower()
        if any(p.lower() in low for p in patterns):
            clean=line
            clean=re.sub(
                r'(?i)(api[_-]?key|password|token)\s*[:=]\s*[^,}\s]+',
                r'\1=<redacted>',
                clean,
            )
            if "downloadurl" in low or "guid" in low:
                continue
            out.append("%d:%s" % (i,clean[:220]))
    return out[:120]

ensure_ui_stopped()

print("===== TRACKER RETENTION PREFLIGHT =====")
print("UI: STOPPED")

rpc=deluge_rpc()

print()
print("===== DELUGE PLUGINS =====")

available=[]
enabled=[]

for method,target in (
    ("core.get_available_plugins",available),
    ("core.get_enabled_plugins",enabled),
):
    try:
        value=rpc(method,[]) or []
        target.extend(str(x) for x in value)
    except Exception as exc:
        print("%s: ERROR %s" % (method,exc))

print("available:",", ".join(sorted(available)) or "UNKNOWN")
print("enabled  :",", ".join(sorted(enabled)) or "UNKNOWN")

label_capable=False

try:
    labels=rpc("label.get_labels",[]) or []
    label_capable=True
    print("label.get_labels: PASS")
    print("existing labels:",", ".join(sorted(str(x) for x in labels)) or "NONE")
except Exception as exc:
    print("label.get_labels: FAIL:",str(exc))

try:
    torrents=rpc(
        "core.get_torrents_status",
        [{},["name","state","progress","tracker","trackers","label"]],
    ) or {}
except Exception:
    torrents=rpc(
        "core.get_torrents_status",
        [{},["name","state","progress","tracker","trackers"]],
    ) or {}

torrent_map={
    str(h).strip().upper():(data or {})
    for h,data in torrents.items()
}

print()
print("Deluge torrents:",len(torrent_map))
print("Label plugin callable:","YES" if label_capable else "NO")

def audit_pending(kind,path):
    pending=pending_map(path)

    print()
    print("===== %s PENDING (%d) =====" % (kind,len(pending)))

    counts={
        "torrentleech":0,
        "other_tracker":0,
        "missing_deluge":0,
        "no_download_id":0,
    }

    for key,txn in sorted(
        pending.items(),
        key=lambda x:int(x[0]) if str(x[0]).isdigit() else str(x[0]),
    ):
        did=str((txn or {}).get("download_id") or "").strip().upper()

        if not did:
            counts["no_download_id"] += 1
            print(
                "%s=%s | status=%s | NO DOWNLOAD ID"
                % (kind.lower(),key,(txn or {}).get("status"))
            )
            continue

        item=torrent_map.get(did)

        if not item:
            counts["missing_deluge"] += 1
            print(
                "%s=%s | status=%s | hash=%s... | DELUGE ABSENT"
                % (
                    kind.lower(),
                    key,
                    (txn or {}).get("status"),
                    did[:10],
                )
            )
            continue

        hosts=tracker_hosts(item)
        tl=is_tl(hosts)

        if tl:
            counts["torrentleech"] += 1
        else:
            counts["other_tracker"] += 1

        print(
            "%s=%s | status=%s | hash=%s... | state=%s %.1f%% | "
            "tracker=%s | label=%s | desired=%s"
            % (
                kind.lower(),
                key,
                (txn or {}).get("status"),
                did[:10],
                item.get("state"),
                float(item.get("progress") or 0),
                ",".join(hosts) or "UNKNOWN",
                label_of(item) or "NONE",
                (
                    "torrentleech-movies"
                    if kind=="RADARR" and tl
                    else "torrentleech-tv"
                    if kind=="SONARR" and tl
                    else "REMOVE-AFTER-VERIFIED-SUCCESS"
                ),
            )
        )

    print(
        "%s summary: TL=%d other=%d absent=%d no_id=%d"
        % (
            kind,
            counts["torrentleech"],
            counts["other_tracker"],
            counts["missing_deluge"],
            counts["no_download_id"],
        )
    )

audit_pending("RADARR",RAD_STATE)
audit_pending("SONARR",SON_STATE)

print()
print("===== SOURCE ANCHORS: RADARR OPTIMIZER =====")
for x in safe_source_anchors(
    RAD_SRC,
    [
        "pending_replacements",
        "old_file_id",
        "old_relative_path",
        "approved_title",
        "download_id",
        "indexer",
        "grab",
    ],
):
    print(x)

print()
print("===== SOURCE ANCHORS: SONARR OPTIMIZER =====")
for x in safe_source_anchors(
    SON_SRC,
    [
        "pending_replacements",
        "download_id",
        "indexer",
        "grab",
        "series",
    ],
):
    print(x)

print()
print("===== SOURCE ANCHORS: UI COMPLETION/CLEANUP =====")
for x in safe_source_anchors(
    UI_SRC,
    [
        "deluge_torrent",
        "pending_replacements",
        "self_heal",
        "verified_new",
        "pop(str(movie_id",
        "pop(str(series",
        "downloadfolderimported",
    ],
):
    print(x)

print()
print("========================================")
print("TRACKER RETENTION PREFLIGHT COMPLETE")
print("Nothing modified")
print("smart-optimizer-ui remains STOPPED")
print("========================================")
