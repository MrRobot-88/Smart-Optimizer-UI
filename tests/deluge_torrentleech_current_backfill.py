#!/usr/bin/env python3
import datetime
import http.cookiejar
import json
import os
import urllib.parse
import urllib.request

CONTROL="/volume1/WDBLACK/ContainerConfigs/Smart-Optimizer-UI/smart-optimizer-control.json"
BACKUP_ROOT="/volume1/WDBLACK/ContainerConfigs/Smart-Optimizer-UI"
TL_TOKENS=("torrentleech.org","torrentleech.me")

def load_json(path):
    with open(path,encoding="utf-8") as f:
        return json.load(f)

def rpc_client():
    controls=load_json(CONTROL)
    cfg=controls.get("deluge") or {}

    scheme=str(cfg.get("scheme") or "http").lower()
    host=str(cfg.get("host") or "").strip()
    port=int(cfg.get("port") or 8112)
    password=str(cfg.get("password") or "")

    if scheme not in ("http","https"):
        raise RuntimeError("invalid Deluge scheme")
    if not host or not password:
        raise RuntimeError("Deluge connection incomplete in Smart Optimizer controls")

    endpoint="%s://%s:%d/json" % (scheme,host,port)
    jar=http.cookiejar.CookieJar()
    opener=urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar)
    )
    counter={"id":0}

    def rpc(method,params=None):
        counter["id"] += 1
        body=json.dumps({
            "method":method,
            "params":params or [],
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
        raise RuntimeError("Deluge Web authentication failed")

    if not bool(rpc("web.connected",[])):
        hosts=rpc("web.get_hosts",[]) or []
        if not hosts:
            raise RuntimeError("Deluge Web has no daemon hosts")
        if rpc("web.connect",[str(hosts[0][0])]) is not True:
            raise RuntimeError("Deluge Web could not connect to daemon")

    return rpc

def tracker_hosts(item):
    hosts=set()

    raw=str((item or {}).get("tracker") or "").strip()
    if raw:
        try:
            host=urllib.parse.urlparse(raw).hostname
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

def is_torrentleech(hosts):
    for host in hosts:
        if any(
            host==token or host.endswith("."+token)
            for token in TL_TOKENS
        ):
            return True
    return False

rpc=rpc_client()

labels=set(
    str(x)
    for x in (rpc("label.get_labels",[]) or [])
)

for needed in ("torrentleech-movies","torrentleech-tv"):
    if needed not in labels:
        rpc("label.add",[needed])
        labels.add(needed)

try:
    torrents=rpc(
        "core.get_torrents_status",
        [{},["name","tracker","trackers","label","state","progress"]],
    ) or {}
except Exception:
    torrents=rpc(
        "core.get_torrents_status",
        [{},["name","tracker","trackers","state","progress"]],
    ) or {}

stamp=datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
backup_dir=os.path.join(
    BACKUP_ROOT,
    "pre-tl-label-backfill-"+stamp,
)
os.makedirs(backup_dir,exist_ok=False)

before={}

for h,data in torrents.items():
    before[str(h)]={
        "name":str((data or {}).get("name") or ""),
        "label":str((data or {}).get("label") or ""),
        "tracker_hosts":tracker_hosts(data),
        "state":str((data or {}).get("state") or ""),
        "progress":float((data or {}).get("progress") or 0),
    }

with open(
    os.path.join(backup_dir,"deluge-labels-before.json"),
    "w",
    encoding="utf-8",
) as f:
    json.dump(before,f,indent=2,sort_keys=True)
    f.write("\n")

changed=[]
already=[]
ambiguous=[]
non_tl=0

for torrent_hash,data in torrents.items():
    hosts=tracker_hosts(data)

    if not is_torrentleech(hosts):
        non_tl += 1
        continue

    current=str((data or {}).get("label") or "").strip()

    if current=="torrentleech-movies":
        already.append((torrent_hash,"torrentleech-movies",data))
        continue

    if current=="torrentleech-tv":
        already.append((torrent_hash,"torrentleech-tv",data))
        continue

    if current=="movies":
        target="torrentleech-movies"
    elif current=="tv-sonarr":
        target="torrentleech-tv"
    else:
        ambiguous.append((torrent_hash,current,data,hosts))
        continue

    rpc(
        "label.set_torrent",
        [str(torrent_hash).lower(),target],
    )

    changed.append(
        (torrent_hash,current,target,data,hosts)
    )

print("===== CURRENT TORRENTLEECH LABEL BACKFILL =====")
print("BACKUP:",backup_dir)
print("Deluge torrents:",len(torrents))
print("Non-TorrentLeech:",non_tl)
print("Relabeled:",len(changed))
print("Already correct:",len(already))
print("Ambiguous TL left untouched:",len(ambiguous))
print()

for h,old,new,data,hosts in changed:
    print(
        "RELABEL %s... | %s -> %s | %s"
        % (
            str(h)[:10],
            old or "NONE",
            new,
            str((data or {}).get("name") or ""),
        )
    )

for h,label,data,hosts in ambiguous:
    print(
        "AMBIGUOUS %s... | label=%s | %s"
        % (
            str(h)[:10],
            label or "NONE",
            str((data or {}).get("name") or ""),
        )
    )

print()
print("Movie files modified: NO")
print("Torrent payloads modified: NO")
print("Torrent removed: NO")
print("Only Deluge labels changed")
print("==============================================")
