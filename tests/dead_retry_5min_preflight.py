#!/usr/bin/env python3
import hashlib
import http.cookiejar
import json
import os
import time
import urllib.request
from pathlib import Path

RAD=Path("/volume1/WDBLACK/radarr-smart-optimizer.py")
SON=Path("/volume1/WDBLACK/sonarr-smart-optimizer.py")
UI=Path("/volume1/WDBLACK/ContainerConfigs/Smart-Optimizer-UI/smart-optimizer-ui.py")
RS=Path("/volume1/WDBLACK/radarr-smart-optimizer-state.json")
SS=Path("/volume1/WDBLACK/sonarr-smart-optimizer-state.json")
CONTROL=Path("/volume1/WDBLACK/ContainerConfigs/Smart-Optimizer-UI/smart-optimizer-control.json")

EXPECTED={
    RAD:"6594cbb61efb9cf1d62b798a7847c39bee86e64c8f03e16ed3517ab10edee85c",
    SON:"e4abbefadc5922e9cc2c25b7096bd062ad012850ec21764cb0690fb3f2f80789",
    UI:"f35d2c9192465f2360712fe185411b10eeca02bdc6c5c5c4768bd7941329d559",
}

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

print("===== 5-MIN DEAD RETRY PREFLIGHT =====")

for path,expected in EXPECTED.items():
    actual=sha(path)
    print(path.name,actual)
    if actual != expected:
        raise SystemExit("STOP: live source hash mismatch for "+str(path))

def load(path):
    try:
        with open(path,encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

rad_state=load(RS)
son_state=load(SS)

rad_jobs=rad_state.get("tracker_jobs") or {}
son_jobs=son_state.get("tracker_jobs") or {}

owned={}
for app,jobs in (("radarr",rad_jobs),("sonarr",son_jobs)):
    for key,job in jobs.items():
        h=str((job or {}).get("download_id") or "").strip().lower()
        if h:
            owned[h]=(app,str(key),str((job or {}).get("status") or ""))

controls=load(CONTROL)
cfg=controls.get("deluge") or {}
scheme=str(cfg.get("scheme") or "http").lower()
host=str(cfg.get("host") or "").strip()
port=int(cfg.get("port") or 8112)
password=str(cfg.get("password") or "")

if not host or not password:
    raise SystemExit("STOP: Deluge connection incomplete")

url="%s://%s:%d/json" % (scheme,host,port)
jar=http.cookiejar.CookieJar()
opener=urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(jar)
)
rpc_id=0

def rpc(method,params=None):
    global rpc_id
    rpc_id += 1
    body=json.dumps({
        "method":method,
        "params":params or [],
        "id":rpc_id,
    }).encode()
    req=urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type":"application/json","Accept":"application/json"},
    )
    with opener.open(req,timeout=30) as r:
        obj=json.load(r)
    if obj.get("error"):
        raise RuntimeError("%s: %s" % (method,obj.get("error")))
    return obj.get("result")

if rpc("auth.login",[password]) is not True:
    raise SystemExit("STOP: Deluge login failed")

if not bool(rpc("web.connected",[])):
    hosts=rpc("web.get_hosts",[]) or []
    if not hosts or rpc("web.connect",[str(hosts[0][0])]) is not True:
        raise SystemExit("STOP: Deluge daemon unavailable")

fields=[
    "name","label","state","progress","total_done","time_added",
    "num_seeds","num_peers","download_payload_rate",
]

try:
    torrents=rpc("core.get_torrents_status",[{},fields]) or {}
except Exception:
    fields=[x for x in fields if x!="label"]
    torrents=rpc("core.get_torrents_status",[{},fields]) or {}

now=time.time()
dead5=[]
zero=[]

for h,t in torrents.items():
    h=str(h).lower()
    p=float((t or {}).get("progress") or 0)
    total=int((t or {}).get("total_done") or 0)
    age=max(0,now-float((t or {}).get("time_added") or now))
    seeds=int((t or {}).get("num_seeds") or 0)
    peers=int((t or {}).get("num_peers") or 0)
    rate=int((t or {}).get("download_payload_rate") or 0)
    label=str((t or {}).get("label") or "")
    name=str((t or {}).get("name") or "")

    if p <= 0:
        zero.append((h,label,name,age))

    if (
        age >= 300
        and p <= 0
        and total <= 0
        and seeds <= 0
        and peers <= 0
        and rate <= 0
    ):
        dead5.append((h,label,name,age,owned.get(h)))

print()
print("===== CURRENT DELUGE =====")
print("Torrents:",len(torrents))
print("Displayed 0.00%:",len(zero))
print("Strict dead >=5 min:",len(dead5))
print("Exact optimizer-owned torrent hashes:",len(owned))

owned_dead=sum(1 for x in dead5 if x[4])
other_dead=len(dead5)-owned_dead
print("Strict dead + optimizer-owned:",owned_dead)
print("Strict dead + NOT optimizer-owned:",other_dead)

for h,label,name,age,owner in dead5[:30]:
    if owner:
        owner_text="%s/%s" % (owner[0],owner[1])
    else:
        owner_text="NOT-OPTIMIZER-OWNED"
    print(
        "dead5 %s... | %-20s | %-24s | age=%.1fm | %s"
        % (h[:10],label or "NONE",owner_text,age/60.0,name)
    )

print()
print("===== OPTIMIZER TRACKER JOBS =====")
print("Radarr tracker jobs:",len(rad_jobs))
print("Sonarr tracker jobs:",len(son_jobs))

def anchors(path,patterns):
    lines=path.read_text(encoding="utf-8").splitlines()
    hits=[]
    for i,line in enumerate(lines):
        if any(p.lower() in line.lower() for p in patterns):
            for n in range(max(0,i-5),min(len(lines),i+12)):
                hits.append(n)
    hits=sorted(set(hits))
    print()
    print("-----",path.name,"-----")
    last=-2
    for n in hits:
        if n>last+1:
            print("...")
        print("%5d: %s" % (n+1,lines[n]))
        last=n

print()
print("===== SOURCE ANCHORS =====")
anchors(
    UI,
    [
        "def deluge_torrent_is_dead",
        "age_seconds >= 300",
        "dead_removed_retry_pending",
        "def run_optimizer(",
        "def sonarr_tracker_retention_worker",
        "target_episode_id",
    ],
)
anchors(
    RAD,
    [
        "MANUAL_TARGET_MODE",
        "AUTO ONE-SHOT",
        "targeted_state",
        "auto_processed_movie_ids",
    ],
)
anchors(
    SON,
    [
        "MANUAL_TARGET_MODE",
        "auto_processed_series_ids",
        "ignore_search_history",
        "TARGET_EPISODE_ID",
        "tracker_jobs",
        "dead_retry_count",
    ],
)

print()
print("========================================")
print("5-MIN DEAD RETRY PREFLIGHT COMPLETE")
print("Nothing modified")
print("========================================")
