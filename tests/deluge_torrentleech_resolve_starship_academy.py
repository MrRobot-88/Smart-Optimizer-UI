#!/usr/bin/env python3
import http.cookiejar
import json
import urllib.parse
import urllib.request

CONTROL="/volume1/WDBLACK/ContainerConfigs/Smart-Optimizer-UI/smart-optimizer-control.json"
TARGET_NAME="Star.Trek.Starfleet.Academy.S01.1080p.10bit.WEBRip.6CH.x265.HEVC-PSA"
TARGET_LABEL="torrentleech-tv"

def load_json(path):
    with open(path,encoding="utf-8") as f:
        return json.load(f)

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

def is_tl(hosts):
    return any(
        h=="torrentleech.org"
        or h.endswith(".torrentleech.org")
        or h=="torrentleech.me"
        or h.endswith(".torrentleech.me")
        for h in hosts
    )

controls=load_json(CONTROL)
cfg=controls.get("deluge") or {}
scheme=str(cfg.get("scheme") or "http").lower()
host=str(cfg.get("host") or "").strip()
port=int(cfg.get("port") or 8112)
password=str(cfg.get("password") or "")

if scheme not in ("http","https") or not host or not password:
    raise SystemExit("STOP: Deluge connection incomplete")

endpoint="%s://%s:%d/json" % (scheme,host,port)
jar=http.cookiejar.CookieJar()
opener=urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(jar)
)
counter={"id":0}

def rpc(method,params=None):
    counter["id"]+=1
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
    raise SystemExit("STOP: Deluge login failed")

if not bool(rpc("web.connected",[])):
    raise SystemExit("STOP: Deluge daemon is not connected")

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

matches=[
    (str(h),data or {})
    for h,data in torrents.items()
    if str((data or {}).get("name") or "")==TARGET_NAME
]

if len(matches)!=1:
    raise SystemExit(
        "STOP: expected exactly one target torrent, found %d"
        % len(matches)
    )

torrent_hash,data=matches[0]
hosts=tracker_hosts(data)

if not is_tl(hosts):
    raise SystemExit(
        "STOP: target is not proven TorrentLeech"
    )

current=str(data.get("label") or "").strip()

if current not in ("",TARGET_LABEL):
    raise SystemExit(
        "STOP: unexpected existing label: %s"
        % current
    )

labels=set(str(x) for x in (rpc("label.get_labels",[]) or []))
if TARGET_LABEL not in labels:
    rpc("label.add",[TARGET_LABEL])

if current!=TARGET_LABEL:
    rpc(
        "label.set_torrent",
        [torrent_hash.lower(),TARGET_LABEL],
    )

check=rpc(
    "core.get_torrent_status",
    [torrent_hash.lower(),["name","label","state","progress"]],
) or {}

if str(check.get("label") or "")!=TARGET_LABEL:
    raise SystemExit(
        "STOP: label verification failed"
    )

print("AMBIGUOUS TL RESOLVED")
print("Torrent:",TARGET_NAME)
print("Hash:",torrent_hash)
print("Label:",check.get("label"))
print("State:",check.get("state"))
print("Progress:",check.get("progress"))
print("Torrent payload modified: NO")
print("Torrent removed: NO")
