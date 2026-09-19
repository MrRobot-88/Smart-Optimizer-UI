#!/usr/bin/env python3
import html, json, os, signal, subprocess, threading, time, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST=os.environ.get("SMART_UI_HOST","0.0.0.0")
PORT=int(os.environ.get("SMART_UI_PORT","8788"))
RADARR_URL=os.environ.get("RADARR_URL","http://127.0.0.1:7878").rstrip("/")
SONARR_URL=os.environ.get("SONARR_URL","http://127.0.0.1:8989").rstrip("/")
RADARR_KEY=os.environ.get("RADARR_KEY","").strip()
SONARR_KEY=os.environ.get("SONARR_KEY","").strip()
RADARR_SCRIPT=os.environ.get("RADARR_OPTIMIZER_SCRIPT","/optimizers/radarr.py")
SONARR_SCRIPT=os.environ.get("SONARR_OPTIMIZER_SCRIPT","/optimizers/sonarr.py")
RADARR_STATE=os.environ.get("RADARR_OPTIMIZER_STATE","/data/radarr-state.json")
SONARR_STATE=os.environ.get("SONARR_OPTIMIZER_STATE","/data/sonarr-state.json")
CONTROL_FILE=os.environ.get("SMART_OPTIMIZER_CONTROL","/config/smart-optimizer-control.json")
RADARR_BASE=int(os.environ.get("RADARR_DAILY_SEARCH_BUDGET","400"))
SONARR_BASE=int(os.environ.get("SONARR_DAILY_SEARCH_BUDGET","400"))
MAX_MANUAL=max(1,int(os.environ.get("SMART_UI_MAX_MANUAL_SEARCHES","10000")))

LOCK=threading.Lock()
JOBS={a:{"running":False,"requested":0,"start":0,"proc":None,"stopped":False,"started":None,"finished":None,"output":""} for a in ("radarr","sonarr")}

def load_json(path, fallback):
    try:
        with open(path,"r",encoding="utf-8") as f:
            x=json.load(f)
            return x if isinstance(x,dict) else fallback
    except Exception:
        return fallback

def save_json(path,data):
    os.makedirs(os.path.dirname(path),exist_ok=True)
    tmp=path+".tmp"
    with open(tmp,"w",encoding="utf-8") as f: json.dump(data,f,indent=2,sort_keys=True)
    os.replace(tmp,path)

def state_path(app): return RADARR_STATE if app=="radarr" else SONARR_STATE
def script_path(app): return RADARR_SCRIPT if app=="radarr" else SONARR_SCRIPT
def base_budget(app): return RADARR_BASE if app=="radarr" else SONARR_BASE

def search_count(app):
    st=load_json(state_path(app),{})
    today=time.strftime("%Y-%m-%d")
    return int(((st.get("daily") or {}).get(today) or {}).get("searches",0))

def controls(app):
    d=load_json(CONTROL_FILE,{})
    c=d.get(app,{}) if isinstance(d.get(app,{}),dict) else {}
    today=time.strftime("%Y-%m-%d")
    return float(c.get("min_saving_percent",5.0)), float(c.get("max_saving_percent",50.0)), int(((c.get("daily_extra") or {}).get(today)) or 0)

def set_window(app,lo,hi):
    if not (0<=lo<=hi<=100): raise ValueError("Use 0-100 and minimum <= maximum")
    d=load_json(CONTROL_FILE,{})
    c=d.setdefault(app,{})
    c["min_saving_percent"]=lo; c["max_saving_percent"]=hi
    save_json(CONTROL_FILE,d)

def add_extra(app,n):
    d=load_json(CONTROL_FILE,{})
    c=d.setdefault(app,{})
    today=time.strftime("%Y-%m-%d")
    c["daily_extra"]={today:int(((c.get("daily_extra") or {}).get(today)) or 0)+n}
    save_json(CONTROL_FILE,d)

def api_get(app,path):
    key,url=(RADARR_KEY,RADARR_URL) if app=="radarr" else (SONARR_KEY,SONARR_URL)
    if not key: raise RuntimeError(app+" API key missing")
    req=urllib.request.Request(url+"/api/v3"+path,headers={"X-Api-Key":key,"Accept":"application/json"})
    with urllib.request.urlopen(req,timeout=20) as r:
        raw=r.read().decode("utf-8")
        return json.loads(raw) if raw else {}

def queue(app):
    try:
        x=api_get(app,"/queue?page=1&pageSize=100&sortKey=timeleft&sortDirection=ascending")
        return x.get("records",[])
    except Exception: return []

def start_manual(app,n):
    with LOCK:
        if JOBS[app]["running"]: return False
        # prevent parallel optimizer jobs from this UI
        other="sonarr" if app=="radarr" else "radarr"
        if JOBS[other]["running"]: return False
        start=search_count(app)
        JOBS[app].update(running=True,requested=n,start=start,proc=None,stopped=False,started=time.time(),finished=None,output="")
    add_extra(app,n)
    def worker():
        env=os.environ.copy()
        env["SMART_OPTIMIZER_CONTROL"]=CONTROL_FILE
        env["RADARR_SEARCHES_PER_RUN" if app=="radarr" else "SONARR_SEARCHES_PER_RUN"]=str(n)
        p=None
        try:
            p=subprocess.Popen(["python3",script_path(app),"--live"],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,env=env,start_new_session=True)
            with LOCK: JOBS[app]["proc"]=p
            out,_=p.communicate()
        except Exception as e:
            out="ERROR: "+str(e)
        with LOCK:
            JOBS[app].update(running=False,finished=time.time(),output=(out or "")[-50000:],proc=None)
    threading.Thread(target=worker,daemon=True).start()
    return True

def stop_manual(app):
    with LOCK:
        j=JOBS[app]
        if not j["running"]: return False
        j["stopped"]=True
        p=j["proc"]
    if p and p.poll() is None:
        try: os.killpg(p.pid,signal.SIGTERM)
        except Exception:
            try: p.terminate()
            except Exception: pass
    return True

def status(app):
    with LOCK: j=dict(JOBS[app])
    searched=max(0,search_count(app)-int(j.get("start") or 0)) if j.get("started") else 0
    if not j.get("started"): state="idle"
    elif j.get("running"): state="stopping" if j.get("stopped") else "running"
    else: state="stopped" if j.get("stopped") else "finished"
    return {"state":state,"searched":searched,"requested":int(j.get("requested") or 0),"running":bool(j.get("running"))}

CSS="""
*{box-sizing:border-box}body{margin:0;background:#0b0e13;color:#edf2f7;font-family:Inter,system-ui,sans-serif}
.shell{max-width:1180px;margin:auto;padding:24px}.top{display:flex;justify-content:space-between;align-items:center;margin-bottom:24px}
.brand{font-weight:800;font-size:22px}.tabs{display:flex;gap:8px}.tabs a{color:#9aa6b7;text-decoration:none;padding:8px 11px;border:1px solid #28313e;border-radius:9px}
.tabs a.active{color:white;background:#18202b}.hero,.row,.stats{display:flex;gap:12px;align-items:center;flex-wrap:wrap}
.hero{justify-content:space-between;margin-bottom:14px}.card{background:#121720;border:1px solid #242b36;border-radius:14px;padding:16px}
.controls{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:12px}.box{display:flex;gap:7px;align-items:center;background:#10151d;border:1px solid #242b36;border-radius:10px;padding:7px 9px}
input{width:78px;background:#0b1016;color:white;border:1px solid #303947;border-radius:7px;height:32px;padding:0 8px}
button{height:32px;border-radius:8px;border:1px solid #344154;background:#182231;color:white;padding:0 12px;cursor:pointer}
button:disabled{opacity:.4;cursor:not-allowed}.stop{border-color:#7f1d1d;color:#fecaca}.muted{color:#8f9bad;font-size:12px}.state{font-weight:700}
.stats{margin:14px 0}.stat{flex:1;min-width:180px}.big{font-size:28px;font-weight:800}.queue{margin-top:14px}.q{padding:10px 0;border-top:1px solid #242b36;font-size:13px}
"""

def page(app):
    st=status(app); lo,hi,extra=controls(app); used=search_count(app); q=queue(app)
    state="Idle" if not st["requested"] else f"{st['state'].capitalize()} · {st['searched']} / {st['requested']} searched"
    other="sonarr" if app=="radarr" else "radarr"
    rows="".join("<div class='q'>"+html.escape(str(x.get("title") or x.get("movieId") or x.get("episodeId") or "Download"))+"</div>" for x in q[:12]) or "<div class='q muted'>Nothing in queue.</div>"
    run_disabled="disabled" if st["running"] else ""
    stop_disabled="" if st["running"] else "disabled"
    return f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Smart Optimizer UI</title><style>{CSS}</style></head>
<body><div class='shell'><div class='top'><div class='brand'>Smart Optimizer UI</div><div class='tabs'><a class='{"active" if app=="radarr" else ""}' href='/radarr'>Radarr</a><a class='{"active" if app=="sonarr" else ""}' href='/sonarr'>Sonarr</a></div></div>
<div class='hero'><div><h2>{app.title()} overview</h2><div class='muted'>Optional sidecar UI. The optimizer works without this dashboard.</div></div></div>
<div class='controls'>
<form class='box' method='post' action='/manual-search'><input type='hidden' name='app' value='{app}'><label>Manual search</label><input name='count' type='number' min='1' max='{MAX_MANUAL}' value='50'><button {run_disabled}>Search</button><button class='stop' formaction='/stop' {stop_disabled}>STOP</button></form>
<span id='runstate-{app}' class='state'>{html.escape(state)}</span><span class='muted'>Live status updates every 10 seconds.</span>
</div>
<div class='controls'><form class='box' method='post' action='/settings'><input type='hidden' name='app' value='{app}'><label>Downsize</label><input name='min' type='number' min='0' max='100' step='0.1' value='{lo:.1f}'><span>–</span><input name='max' type='number' min='0' max='100' step='0.1' value='{hi:.1f}'><span>%</span><button>Apply</button></form><span class='muted'>{used}/{base_budget(app)+extra} searches · +{extra} today</span></div>
<div class='stats'><div class='card stat'><div class='muted'>Searches today</div><div class='big'>{used}</div></div><div class='card stat'><div class='muted'>Active downloads</div><div class='big'>{len(q)}</div></div><div class='card stat'><div class='muted'>Temporary extra today</div><div class='big'>+{extra}</div></div></div>
<div class='card queue'><b>Download queue</b>{rows}</div></div>
<script>
(function(){{const el=document.getElementById('runstate-{app}');async function tick(){{try{{const r=await fetch('/status?app={app}',{{cache:'no-store'}});const x=await r.json();el.textContent=x.requested?(x.state.charAt(0).toUpperCase()+x.state.slice(1)+' · '+x.searched+' / '+x.requested+' searched'):'Idle';}}catch(e){{}}}}tick();setInterval(tick,10000);}})();
</script></body></html>"""

class H(BaseHTTPRequestHandler):
    def send_html(self,s,code=200):
        b=s.encode(); self.send_response(code); self.send_header("Content-Type","text/html; charset=utf-8"); self.send_header("Content-Length",str(len(b))); self.send_header("Cache-Control","no-store"); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        u=urllib.parse.urlparse(self.path)
        if u.path=="/": self.send_response(302); self.send_header("Location","/radarr"); self.end_headers(); return
        if u.path=="/status":
            app=(urllib.parse.parse_qs(u.query).get("app") or [""])[0]
            if app not in ("radarr","sonarr"): self.send_error(400); return
            b=json.dumps(status(app)).encode(); self.send_response(200); self.send_header("Content-Type","application/json"); self.send_header("Content-Length",str(len(b))); self.send_header("Cache-Control","no-store"); self.end_headers(); self.wfile.write(b); return
        if u.path in ("/radarr","/sonarr"): self.send_html(page(u.path[1:])); return
        self.send_error(404)
    def do_POST(self):
        n=min(int(self.headers.get("Content-Length","0")),4096)
        f=urllib.parse.parse_qs(self.rfile.read(n).decode())
        app=(f.get("app") or [""])[0]
        if app not in ("radarr","sonarr"): self.send_error(400); return
        if self.path=="/manual-search":
            try:
                count=int((f.get("count") or [""])[0])
                if not 1<=count<=MAX_MANUAL: raise ValueError()
            except Exception: self.send_error(400,"Invalid search count"); return
            if not start_manual(app,count): self.send_error(409,"Another UI optimizer run is already active"); return
        elif self.path=="/stop":
            stop_manual(app)
        elif self.path=="/settings":
            try: set_window(app,float((f.get("min") or [""])[0]),float((f.get("max") or [""])[0]))
            except Exception as e: self.send_error(400,str(e)); return
        else: self.send_error(404); return
        self.send_response(303); self.send_header("Location","/"+app); self.end_headers()
    def log_message(self,fmt,*args): print("[ui]",fmt%args)

if __name__=="__main__":
    print("Smart Optimizer UI listening on %s:%d"%(HOST,PORT),flush=True)
    ThreadingHTTPServer((HOST,PORT),H).serve_forever()
