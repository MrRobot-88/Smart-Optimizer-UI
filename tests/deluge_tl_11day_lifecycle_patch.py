#!/usr/bin/env python3
import copy
import datetime
import http.cookiejar
import json
import re
import subprocess
import sys
import urllib.parse
import urllib.request

CONTAINER="Deluge-SSD"
CONTROL="/volume1/WDBLACK/ContainerConfigs/Smart-Optimizer-UI/smart-optimizer-control.json"
AR="/config/autoremoveplus.conf"
STALE="/config/scripts/remove-stale-incomplete.py"
FIX="/config/scripts/fix-missing-labels.py"
TL_TOKENS=("torrentleech.org","torrentleech.me")

def run(args, input_text=None, check=True):
    p=subprocess.run(
        args,
        input=input_text,
        text=True,
        capture_output=True,
    )
    if check and p.returncode != 0:
        raise RuntimeError(
            "%s failed: %s"
            % (" ".join(args), (p.stderr or p.stdout).strip())
        )
    return p

def ccat(path):
    return run(
        ["docker","exec",CONTAINER,"cat",path]
    ).stdout

def cwrite(path,text):
    run(
        ["docker","exec","-i",CONTAINER,"sh","-c","cat > "+path],
        input_text=text,
    )

def ccopy(src,dst):
    run(
        ["docker","exec",CONTAINER,"cp","-p",src,dst]
    )

def cmkdir(path):
    run(
        ["docker","exec",CONTAINER,"mkdir","-p",path]
    )

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

def parse_concat_json(text):
    dec=json.JSONDecoder()
    first,idx=dec.raw_decode(text)
    rest=text[idx:].lstrip()
    second,idx2=dec.raw_decode(rest)
    if rest[idx2:].strip():
        raise RuntimeError("unexpected trailing data in autoremoveplus.conf")
    if not isinstance(first,dict) or not isinstance(second,dict):
        raise RuntimeError("unexpected AutoRemovePlus config structure")
    return first,second

def patch_exact(text,old,new,label):
    count=text.count(old)
    if count != 1:
        raise RuntimeError(
            "%s: expected exactly 1 match, found %d"
            % (label,count)
        )
    return text.replace(old,new,1)

rpc=rpc_client()

stamp=datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
backup="/config/backups/pre-tl-11day-"+stamp
cmkdir(backup)

originals={
    AR:ccat(AR),
    STALE:ccat(STALE),
    FIX:ccat(FIX),
}

for src,text in originals.items():
    name=src.rsplit("/",1)[-1]
    cwrite(backup+"/"+name,text)

print("BACKUP:",backup)

plugin_disabled=False

try:
    enabled=set(str(x) for x in (rpc("core.get_enabled_plugins",[]) or []))

    if "AutoRemovePlus" in enabled:
        rpc("core.disable_plugin",["AutoRemovePlus"])
        plugin_disabled=True
        print("AutoRemovePlus: temporarily disabled for config reload")

    # Re-read after disable because plugin shutdown may flush config.
    ar_text=ccat(AR)
    meta,cfg=parse_concat_json(ar_text)

    rules=cfg.setdefault("label_rules",{})

    if "movies" not in rules or "tv-sonarr" not in rules:
        raise RuntimeError("base 11-day label rules are missing")

    rules["torrentleech-movies"]=copy.deepcopy(rules["movies"])
    rules["torrentleech-tv"]=copy.deepcopy(rules["tv-sonarr"])

    if cfg.get("remove") is not True or cfg.get("remove_data") is not True:
        raise RuntimeError("AutoRemovePlus remove/remove_data is not enabled")

    for label in (
        "movies",
        "tv-sonarr",
        "torrentleech-movies",
        "torrentleech-tv",
    ):
        rule=rules.get(label)
        if not rule:
            raise RuntimeError("missing AutoRemovePlus rule for "+label)

        flat=json.dumps(rule)
        if "func_seed_time" not in flat or "264.0" not in flat:
            raise RuntimeError("11-day/264h seed rule not proven for "+label)

    new_ar=(
        json.dumps(meta,indent=4)
        + json.dumps(cfg,indent=4)
        + "\n"
    )
    cwrite(AR,new_ar)

    stale=ccat(STALE)
    stale=patch_exact(
        stale,
        'LABELS = {"movies", "tv-sonarr"}',
        'LABELS = {"movies", "tv-sonarr", "torrentleech-movies", "torrentleech-tv"}',
        "stale-incomplete labels",
    )
    cwrite(STALE,stale)

    fix=ccat(FIX)
    fix=patch_exact(
        fix,
        'for needed in ["tv-sonarr", "movies"]:',
        'for needed in ["tv-sonarr", "movies", "torrentleech-tv", "torrentleech-movies"]:',
        "fix-missing-label creation",
    )
    fix=patch_exact(
        fix,
        'fields = ["name", "label"]',
        'fields = ["name", "label", "tracker", "trackers"]',
        "fix-missing-label fields",
    )
    fix=patch_exact(
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
        "fix-missing-label TL mapping",
    )
    cwrite(FIX,fix)

    # Syntax-check recurring scripts before re-enabling plugin.
    run([
        "docker","exec",CONTAINER,
        "python3","-m","py_compile",
        STALE,FIX,
    ])
    print("Recurring scripts: COMPILE PASS")

    if plugin_disabled:
        rpc("core.enable_plugin",["AutoRemovePlus"])
        plugin_disabled=False
        print("AutoRemovePlus: re-enabled")

    # Ensure labels exist.
    labels=set(str(x) for x in (rpc("label.get_labels",[]) or []))
    for needed in ("torrentleech-movies","torrentleech-tv"):
        if needed not in labels:
            rpc("label.add",[needed])
            labels.add(needed)

    # Immediate current-TL backfill, including the previously ambiguous S01 torrent.
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
    unresolved=[]

    for torrent_hash,data in torrents.items():
        hosts=tracker_hosts(data)
        if not is_tl(hosts):
            continue

        label=str((data or {}).get("label") or "").strip()

        if label in ("torrentleech-movies","torrentleech-tv"):
            continue

        if label=="movies":
            target="torrentleech-movies"
        elif label=="tv-sonarr":
            target="torrentleech-tv"
        elif not label:
            target=(
                "torrentleech-tv"
                if looks_tv((data or {}).get("name"))
                else "torrentleech-movies"
            )
        else:
            unresolved.append((
                str(torrent_hash),
                label,
                str((data or {}).get("name") or ""),
            ))
            continue

        rpc(
            "label.set_torrent",
            [str(torrent_hash).lower(),target],
        )
        relabeled.append((
            str(torrent_hash),
            label,
            target,
            str((data or {}).get("name") or ""),
        ))

    # Verify config survived plugin reload.
    _meta,verify_cfg=parse_concat_json(ccat(AR))
    verify_rules=verify_cfg.get("label_rules") or {}

    for label in (
        "torrentleech-movies",
        "torrentleech-tv",
    ):
        flat=json.dumps(verify_rules.get(label) or [])
        if "func_seed_time" not in flat or "264.0" not in flat:
            raise RuntimeError("AutoRemovePlus reload lost rule for "+label)

    stale_verify=ccat(STALE)
    fix_verify=ccat(FIX)

    for label in (
        "torrentleech-movies",
        "torrentleech-tv",
    ):
        if label not in stale_verify:
            raise RuntimeError("stale cleanup missing "+label)
        if label not in fix_verify:
            raise RuntimeError("missing-label fixer missing "+label)

    # Final Deluge TL label audit.
    torrents2=rpc(
        "core.get_torrents_status",
        [{},["name","tracker","trackers","label"]],
    ) or {}

    bad=[]

    for torrent_hash,data in torrents2.items():
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

    print()
    print("===== TL 11-DAY LIFECYCLE RESULT =====")
    print("AutoRemovePlus TL movie rule: 264h seed time")
    print("AutoRemovePlus TL TV rule: 264h seed time")
    print("remove_data:",verify_cfg.get("remove_data"))
    print("Stale incomplete labels: UPDATED")
    print("Missing-label fixer: TRACKER-AWARE")
    print("Current TL relabeled now:",len(relabeled))
    print("Current TL unresolved:",len(bad))

    for h,old,new,name in relabeled:
        print(
            "RELABEL %s... | %s -> %s | %s"
            % (h[:10],old or "NONE",new,name)
        )

    for h,label,name in bad:
        print(
            "UNRESOLVED %s... | label=%s | %s"
            % (h[:10],label or "NONE",name)
        )

    if bad:
        raise RuntimeError(
            "current TorrentLeech torrents still have unresolved labels"
        )

    print("CURRENT TORRENTLEECH LABEL AUDIT: PASS")
    print("Movie/library files modified: NO")
    print("Torrent payloads removed now: NO")
    print("========================================")

except Exception:
    print("PATCH FAILED - RESTORING")

    for src,text in originals.items():
        cwrite(src,text)

    try:
        if plugin_disabled:
            rpc("core.enable_plugin",["AutoRemovePlus"])
            plugin_disabled=False
    except Exception:
        pass

    raise
