#!/usr/bin/env python3
import copy
import datetime
import http.cookiejar
import json
import os
import re
import subprocess
import urllib.parse
import urllib.request

CONTAINER="Deluge-SSD"
CONTROL="/volume1/WDBLACK/ContainerConfigs/Smart-Optimizer-UI/smart-optimizer-control.json"
AR="/config/autoremoveplus.conf"
STALE="/config/scripts/remove-stale-incomplete.py"
FIX="/config/scripts/fix-missing-labels.py"
TL_TOKENS=("torrentleech.org","torrentleech.me")

def run(args,input_text=None):
    p=subprocess.run(
        args,
        input=input_text,
        text=True,
        capture_output=True,
    )
    if p.returncode != 0:
        raise RuntimeError(
            "%s failed: %s"
            % (" ".join(args),(p.stderr or p.stdout).strip())
        )
    return p.stdout

def ccat(path):
    return run(["docker","exec",CONTAINER,"cat",path])

def cwrite(path,text):
    run(
        ["docker","exec","-i",CONTAINER,"sh","-c","cat > "+path],
        input_text=text,
    )

def cmkdir(path):
    run(["docker","exec",CONTAINER,"mkdir","-p",path])

def rpc_client():
    with open(CONTROL,encoding="utf-8") as f:
        controls=json.load(f)
    cfg=controls.get("deluge") or {}

    scheme=str(cfg.get("scheme") or "http").lower()
    host=str(cfg.get("host") or "").strip()
    port=int(cfg.get("port") or 8112)
    password=str(cfg.get("password") or "")

    if scheme not in ("http","https"):
        raise RuntimeError("invalid Deluge scheme")
    if not host or not password:
        raise RuntimeError("Deluge connection incomplete")

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
        raise RuntimeError("Deluge login failed")

    if not bool(rpc("web.connected",[])):
        hosts=rpc("web.get_hosts",[]) or []
        if not hosts:
            raise RuntimeError("No Deluge daemon hosts")
        if rpc("web.connect",[str(hosts[0][0])]) is not True:
            raise RuntimeError("Could not connect Deluge daemon")

    return rpc

def tracker_hosts(item):
    out=set()

    raw=str((item or {}).get("tracker") or "").strip()
    if raw:
        try:
            host=urllib.parse.urlparse(raw).hostname
        except Exception:
            host=None
        if host:
            out.add(host.lower())

    for tr in (item or {}).get("trackers") or []:
        url=str((tr or {}).get("url") or "").strip()
        if not url:
            continue
        try:
            host=urllib.parse.urlparse(url).hostname
        except Exception:
            host=None
        if host:
            out.add(host.lower())

    return sorted(out)

def is_tl(hosts):
    return any(
        host==token or host.endswith("."+token)
        for host in hosts
        for token in TL_TOKENS
    )

def looks_tv(name):
    n=str(name or "")
    return bool(
        re.search(r"(?i)(?:^|[ ._\-])S\d{1,2}(?:E\d{1,3})?(?:[ ._\-]|$)",n)
        or re.search(r"(?i)season[ ._\-]*\d+",n)
        or re.search(r"(?i)complete[ ._\-]*series",n)
    )

def replace_once(text,old,new,label):
    n=text.count(old)
    if n != 1:
        raise RuntimeError(
            "%s: expected 1 match, found %d"
            % (label,n)
        )
    return text.replace(old,new,1)

rpc=rpc_client()

stamp=datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
backup="/config/backups/pre-tl-11day-rpc-"+stamp
cmkdir(backup)

original_ar=ccat(AR)
original_stale=ccat(STALE)
original_fix=ccat(FIX)
original_live=rpc("autoremoveplus.get_config",[]) or {}

for name,text in (
    ("autoremoveplus.conf",original_ar),
    ("remove-stale-incomplete.py",original_stale),
    ("fix-missing-labels.py",original_fix),
    ("autoremoveplus-live-before.json",
     json.dumps(original_live,indent=2,sort_keys=True)+"\n"),
):
    cwrite(backup+"/"+name,text)

print("BACKUP:",backup)

try:
    cfg=copy.deepcopy(original_live)
    rules=cfg.setdefault("label_rules",{})

    if "movies" not in rules or "tv-sonarr" not in rules:
        raise RuntimeError("base movies/tv-sonarr rules are missing")

    rules["torrentleech-movies"]=copy.deepcopy(rules["movies"])
    rules["torrentleech-tv"]=copy.deepcopy(rules["tv-sonarr"])

    if cfg.get("remove") is not True or cfg.get("remove_data") is not True:
        raise RuntimeError("AutoRemovePlus remove/remove_data is not enabled")

    rpc("autoremoveplus.set_config",[cfg])

    verify=rpc("autoremoveplus.get_config",[]) or {}
    vrules=verify.get("label_rules") or {}

    for label in (
        "movies",
        "tv-sonarr",
        "torrentleech-movies",
        "torrentleech-tv",
    ):
        flat=json.dumps(vrules.get(label) or [])
        if "func_seed_time" not in flat or "264.0" not in flat:
            raise RuntimeError(
                "live AutoRemovePlus rule verification failed for "+label
            )

    if verify.get("remove_data") is not True:
        raise RuntimeError("remove_data live verification failed")

    stale=ccat(STALE)
    if "torrentleech-movies" not in stale:
        stale=replace_once(
            stale,
            'LABELS = {"movies", "tv-sonarr"}',
            'LABELS = {"movies", "tv-sonarr", "torrentleech-movies", "torrentleech-tv"}',
            "stale cleanup labels",
        )
    cwrite(STALE,stale)

    fix=ccat(FIX)

    if "torrentleech-movies" not in fix:
        fix=replace_once(
            fix,
            'for needed in ["tv-sonarr", "movies"]:',
            'for needed in ["tv-sonarr", "movies", "torrentleech-tv", "torrentleech-movies"]:',
            "label creation",
        )

        fix=replace_once(
            fix,
            'fields = ["name", "label"]',
            'fields = ["name", "label", "tracker", "trackers"]',
            "tracker fields",
        )

        fix=replace_once(
            fix,
            '            target = guess_label(name)\n',
            '''            target = guess_label(name)

            tracker_values = [str(t.get("tracker", "") or "")]
            for tracker in (t.get("trackers", []) or []):
                if isinstance(tracker, dict):
                    tracker_values.append(str(tracker.get("url", "") or ""))
                else:
                    tracker_values.append(str(tracker or ""))

            tracker_text = " ".join(tracker_values).lower()

            if "torrentleech" in tracker_text:
                if target == "tv-sonarr":
                    target = "torrentleech-tv"
                elif target == "movies":
                    target = "torrentleech-movies"
''',
            "TL-aware missing-label mapping",
        )

    cwrite(FIX,fix)

    run([
        "docker","exec",CONTAINER,
        "python3","-m","py_compile",
        STALE,FIX,
    ])

    labels=set(str(x) for x in (rpc("label.get_labels",[]) or []))
    for needed in ("torrentleech-movies","torrentleech-tv"):
        if needed not in labels:
            rpc("label.add",[needed])

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

    relabeled=[]

    for torrent_hash,data in torrents.items():
        hosts=tracker_hosts(data)

        if not is_tl(hosts):
            continue

        current=str((data or {}).get("label") or "").strip()

        if current in ("torrentleech-movies","torrentleech-tv"):
            continue

        if current=="movies":
            target="torrentleech-movies"
        elif current=="tv-sonarr":
            target="torrentleech-tv"
        elif not current:
            target=(
                "torrentleech-tv"
                if looks_tv((data or {}).get("name"))
                else "torrentleech-movies"
            )
        else:
            raise RuntimeError(
                "unexpected existing label on TL torrent %s: %s"
                % (str(torrent_hash)[:10],current)
            )

        rpc(
            "label.set_torrent",
            [str(torrent_hash).lower(),target],
        )

        relabeled.append((
            str(torrent_hash),
            current,
            target,
            str((data or {}).get("name") or ""),
        ))

    # Final current TL audit.
    final=rpc(
        "core.get_torrents_status",
        [{},["name","tracker","trackers","label"]],
    ) or {}

    bad=[]

    for torrent_hash,data in final.items():
        hosts=tracker_hosts(data)

        if not is_tl(hosts):
            continue

        label=str((data or {}).get("label") or "").strip()

        if label not in ("torrentleech-movies","torrentleech-tv"):
            bad.append((
                str(torrent_hash),
                label,
                str((data or {}).get("name") or ""),
            ))

    if bad:
        raise RuntimeError(
            "current TL label audit failed: %r"
            % (bad,)
        )

    print()
    print("===== TL 11-DAY LIFECYCLE: SUCCESS =====")
    print("torrentleech-movies: 264h seed-time rule")
    print("torrentleech-tv: 264h seed-time rule")
    print("remove_data:",verify.get("remove_data"))
    print("stale incomplete script: includes both TL labels")
    print("missing-label fixer: TL tracker-aware")
    print("current TL relabeled this run:",len(relabeled))
    print("current TL unresolved: 0")

    for h,old,new,name in relabeled:
        print(
            "RELABEL %s... | %s -> %s | %s"
            % (h[:10],old or "NONE",new,name)
        )

    print("Recurring scripts: COMPILE PASS")
    print("Current TL audit: PASS")
    print("Payloads removed now: NO")
    print("==========================================")

except Exception:
    print("PATCH FAILED - RESTORING FILES + LIVE AUTOREMOVEPLUS CONFIG")

    cwrite(AR,original_ar)
    cwrite(STALE,original_stale)
    cwrite(FIX,original_fix)

    try:
        rpc("autoremoveplus.set_config",[original_live])
    except Exception as exc:
        print("WARNING: live AutoRemovePlus restore error:",exc)

    raise
