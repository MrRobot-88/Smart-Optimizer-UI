#!/usr/bin/env python3
import http.cookiejar
import json
import os
import re
import subprocess
import urllib.request
import zipfile
from pathlib import Path

CONTAINER="Deluge-SSD"
CONTROL="/volume1/WDBLACK/ContainerConfigs/Smart-Optimizer-UI/smart-optimizer-control.json"

def run(args):
    p=subprocess.run(args,capture_output=True,text=True)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout).strip())
    return p.stdout

def rpc_client():
    with open(CONTROL,encoding="utf-8") as f:
        controls=json.load(f)
    cfg=controls.get("deluge") or {}
    scheme=str(cfg.get("scheme") or "http").lower()
    host=str(cfg.get("host") or "").strip()
    port=int(cfg.get("port") or 8112)
    password=str(cfg.get("password") or "")
    if not host or not password:
        raise RuntimeError("Deluge connection incomplete")

    endpoint=f"{scheme}://{host}:{port}/json"
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
        }).encode()
        req=urllib.request.Request(
            endpoint,
            data=body,
            method="POST",
            headers={"Content-Type":"application/json","Accept":"application/json"},
        )
        with opener.open(req,timeout=30) as r:
            obj=json.load(r)
        if obj.get("error"):
            raise RuntimeError(f"{method}: {obj.get('error')}")
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

print("===== AUTOREMOVEPLUS LIVE PROBE =====")

print()
print("===== CURRENT CONFIG =====")
print(run(["docker","exec",CONTAINER,"cat","/config/autoremoveplus.conf"]))

print()
print("===== PLUGIN FILES =====")
plugin_listing=run([
    "docker","exec",CONTAINER,"sh","-c",
    "find /config/plugins -maxdepth 2 -type f -printf '%p\n' 2>/dev/null || true"
])
print(plugin_listing)

rpc=rpc_client()

print()
print("===== ENABLED PLUGINS =====")
print(json.dumps(rpc("core.get_enabled_plugins",[]) or [],indent=2))

print()
print("===== RPC METHOD DISCOVERY =====")
method_names=[]
for method in ("system.listMethods","web.get_method_list","daemon.get_method_list"):
    try:
        value=rpc(method,[])
        if isinstance(value,(list,tuple)):
            method_names.extend(str(x) for x in value)
        print(method,":",json.dumps(value,indent=2)[:12000])
    except Exception as exc:
        print(method,": ERROR:",exc)

arp=sorted({
    x for x in method_names
    if "autoremove" in x.lower()
})
print("AUTOREMOVEPLUS METHODS:",json.dumps(arp,indent=2))

print()
print("===== READ-ONLY PLUGIN RPC PROBES =====")
for method in (
    "autoremoveplus.get_config",
    "autoremoveplus.get_config_values",
    "autoremoveplus.get_label_rules",
    "autoremoveplus.get_trackers",
):
    try:
        value=rpc(method,[])
        print(method,":",json.dumps(value,indent=2)[:20000])
    except Exception as exc:
        print(method,": ERROR:",exc)

print()
print("===== PLUGIN SOURCE HINTS =====")
# Copy candidate egg(s) to /tmp on host only for read-only inspection.
host_tmp=Path("/tmp/autoremoveplus-probe")
host_tmp.mkdir(exist_ok=True)

files=[]
try:
    out=run([
        "docker","exec",CONTAINER,"sh","-c",
        "find /config/plugins -maxdepth 1 -type f \( -name '*AutoRemovePlus*.egg' -o -name '*autoremoveplus*.egg' \) -print"
    ])
    files=[x.strip() for x in out.splitlines() if x.strip()]
except Exception:
    files=[]

if not files:
    print("NO AUTOREMOVEPLUS EGG FOUND")
else:
    for idx,cpath in enumerate(files):
        hpath=host_tmp/f"plugin-{idx}.egg"
        subprocess.run(
            ["docker","cp",f"{CONTAINER}:{cpath}",str(hpath)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print("EGG:",cpath)
        try:
            with zipfile.ZipFile(hpath) as z:
                for name in z.namelist():
                    if not name.endswith(".py"):
                        continue
                    try:
                        text=z.read(name).decode("utf-8","replace")
                    except Exception:
                        continue
                    if any(tok in text for tok in (
                        "label_rules","set_config","get_config",
                        "remove_data","func_seed_time"
                    )):
                        print("---",name,"---")
                        lines=text.splitlines()
                        for i,line in enumerate(lines,1):
                            low=line.lower()
                            if any(tok in low for tok in (
                                "label_rules","set_config","get_config",
                                "remove_data","func_seed_time"
                            )):
                                start=max(1,i-4); end=min(len(lines),i+8)
                                for n in range(start,end+1):
                                    print(f"{n}: {lines[n-1]}")
                                print("...")
        except Exception as exc:
            print("EGG READ ERROR:",exc)

print()
print("========================================")
print("AUTOREMOVEPLUS LIVE PROBE COMPLETE")
print("Nothing modified")
print("========================================")
