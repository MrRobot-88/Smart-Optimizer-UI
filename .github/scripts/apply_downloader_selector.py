from pathlib import Path

p = Path('smart-optimizer-ui.py')
s = p.read_text()

backend_anchor = '''def connection_url(app):
    cfg = connection(app)
    return "%s://%s:%d" % (cfg["scheme"], cfg["host"], cfg["port"])

'''

backend = r'''def download_client_settings():
    data = load_controls()
    selected = str((data.get("download_client") or {}).get("selected") or "rqbit").strip().lower()
    if selected not in ("deluge", "rqbit"):
        selected = "rqbit"

    deluge = data.get("deluge", {}) or {}
    rqbit = data.get("rqbit", {}) or {}

    return {
        "selected": selected,
        "deluge": {
            "scheme": str(deluge.get("scheme") or "http").lower(),
            "host": str(deluge.get("host") or "127.0.0.1").strip(),
            "port": int(deluge.get("port") or 8112),
            "password": str(deluge.get("password") or ""),
        },
        "rqbit": {
            "scheme": str(rqbit.get("scheme") or "http").lower(),
            "host": str(rqbit.get("host") or "127.0.0.1").strip(),
            "port": int(rqbit.get("port") or 3030),
        },
    }


def _validate_download_client_endpoint(scheme, host, port):
    scheme = str(scheme or "http").strip().lower()
    host = str(host or "").strip().rstrip("/")
    if scheme not in ("http", "https"):
        raise ValueError("Scheme must be http or https")
    if not host or "/" in host:
        raise ValueError("Enter only the hostname or IP address")
    port = int(port)
    if not 1 <= port <= 65535:
        raise ValueError("Port must be 1-65535")
    return scheme, host, port


def update_download_client_settings(selected, deluge_scheme, deluge_host, deluge_port, deluge_password, rqbit_scheme, rqbit_host, rqbit_port):
    selected = str(selected or "").strip().lower()
    if selected not in ("deluge", "rqbit"):
        raise ValueError("Choose Deluge or rqbit")

    d_scheme, d_host, d_port = _validate_download_client_endpoint(deluge_scheme, deluge_host, deluge_port)
    r_scheme, r_host, r_port = _validate_download_client_endpoint(rqbit_scheme, rqbit_host, rqbit_port)

    current = download_client_settings()
    password = str(deluge_password or "").strip() or current["deluge"]["password"]

    data = load_controls()
    data["download_client"] = {"selected": selected}
    data["deluge"] = {"scheme": d_scheme, "host": d_host, "port": d_port, "password": password}
    data["rqbit"] = {"scheme": r_scheme, "host": r_host, "port": r_port}
    save_controls(data)
    return download_client_settings()


def test_download_client(client, scheme=None, host=None, port=None, password=None):
    cfg = download_client_settings()
    client = str(client or cfg["selected"]).strip().lower()
    if client not in ("deluge", "rqbit"):
        raise ValueError("Unknown download client")

    current = cfg[client]
    scheme, host, port = _validate_download_client_endpoint(scheme or current["scheme"], host or current["host"], port or current["port"])
    base = "%s://%s:%d" % (scheme, host, port)

    if client == "rqbit":
        req = urllib.request.Request(base + "/", headers={"Accept": "application/json,text/html,*/*"})
        with urllib.request.urlopen(req, timeout=8) as response:
            response.read(256)
            return "rqbit connected (HTTP %s)" % response.status

    password = str(password or current.get("password") or "").strip()
    if not password:
        raise RuntimeError("Deluge Web password is not configured")

    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    def rpc(method, params):
        body = json.dumps({"method": method, "params": params, "id": 1}).encode("utf-8")
        req = urllib.request.Request(base + "/json", data=body, headers={"Content-Type": "application/json"})
        with opener.open(req, timeout=8) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
        if payload.get("error"):
            raise RuntimeError(str(payload["error"]))
        return payload.get("result")

    if rpc("auth.login", [password]) is not True:
        raise RuntimeError("Deluge authentication failed")
    rpc("web.connected", [])
    return "Deluge connected"


def _download_client_kind(item):
    text = " ".join(str(item.get(key) or "") for key in ("name", "implementation", "implementationName", "configContract")).lower()
    compact = text.replace("-", "").replace("_", "").replace(" ", "")
    if "deluge" in compact:
        return "deluge"
    if "rqbit" in compact:
        return "rqbit"
    return ""


def sync_arr_download_client_selection(app, selected):
    selected = str(selected or "").strip().lower()
    if selected not in ("deluge", "rqbit"):
        raise ValueError("Unknown selected client")

    if app == "radarr":
        getter, requester = radarr_get, radarr_request
    elif app == "sonarr":
        getter, requester = sonarr_get, sonarr_request
    else:
        raise ValueError("Unknown app")

    clients = getter("/downloadclient") or []
    if isinstance(clients, dict):
        clients = clients.get("records") or clients.get("downloadClients") or []

    managed = [(kind, item) for item in clients if isinstance(item, dict) for kind in [_download_client_kind(item)] if kind]
    if not any(kind == selected for kind, _ in managed):
        raise RuntimeError("%s has no configured %s download client" % (app.capitalize(), selected))

    changed = []
    for kind, item in managed:
        client_id = int(item.get("id") or 0)
        if not client_id:
            continue
        desired = (kind == selected)
        current = bool(item.get("enable", True))
        if current == desired:
            continue
        payload = dict(item)
        payload["enable"] = desired
        requester("/downloadclient/%d" % client_id, method="PUT", payload=payload)
        changed.append("%s=%s" % (str(item.get("name") or kind), "ON" if desired else "OFF"))

    verify = getter("/downloadclient") or []
    if isinstance(verify, dict):
        verify = verify.get("records") or verify.get("downloadClients") or []

    selected_rows = [item for item in verify if isinstance(item, dict) and _download_client_kind(item) == selected and bool(item.get("enable", True))]
    other_enabled = [item for item in verify if isinstance(item, dict) and _download_client_kind(item) in ("deluge", "rqbit") and _download_client_kind(item) != selected and bool(item.get("enable", True))]
    if not selected_rows or other_enabled:
        raise RuntimeError("%s download-client routing verification failed" % app.capitalize())
    return changed


def apply_download_client_selection(selected):
    results, errors = {}, {}
    for app in ("radarr", "sonarr"):
        try:
            if not connection(app)["api_key"]:
                results[app] = ["skipped: API key not configured"]
                continue
            results[app] = sync_arr_download_client_selection(app, selected)
        except Exception as exc:
            errors[app] = str(exc)
    return results, errors


'''

if 'def download_client_settings():' not in s:
    if backend_anchor not in s:
        raise SystemExit('backend anchor missing')
    s = s.replace(backend_anchor, backend_anchor + backend, 1)

old_settings_head = '''def settings_page(message="", bad=False):
    rcfg, scfg = connection("radarr"), connection("sonarr")
    acfg = load_auth_config()
'''
new_settings_head = '''def settings_page(message="", bad=False):
    rcfg, scfg = connection("radarr"), connection("sonarr")
    dcfg = download_client_settings()
    acfg = load_auth_config()
'''
if old_settings_head in s:
    s = s.replace(old_settings_head, new_settings_head, 1)

card_anchor = '    auth_card = """<section class="cfg-card auth">'
if '__DOWNLOADER_CARD__' not in s:
    if card_anchor not in s:
        raise SystemExit('auth card anchor missing')
    downloader_card = r'''    downloader_card = """<section class="cfg-card downloader">
<div class="cfg-card-head">
  <div class="cfg-card-titlewrap">
    <div class="cfg-card-icon"><img src="/settings-icon.png" alt="Downloader"></div>
    <div class="cfg-card-titletext">
      <h2>Download client</h2>
      <p>Choose where Smart Optimizer sends torrent and magnet grabs through Radarr and Sonarr.</p>
    </div>
  </div>
  <div class="cfg-status good">ACTIVE: %s</div>
</div>
<form method="post" action="/download-client-settings" class="cfg-form">
  <div class="cfg-row"><label class="cfg-label">Active client</label><select name="selected"><option value="rqbit"%s>rqbit</option><option value="deluge"%s>Deluge</option></select></div>
  <div class="cfg-row"><label class="cfg-label">rqbit protocol</label><select name="rqbit_scheme"><option value="http"%s>http</option><option value="https"%s>https</option></select></div>
  <div class="cfg-row"><label class="cfg-label">rqbit host</label><input name="rqbit_host" value="%s"></div>
  <div class="cfg-row"><label class="cfg-label">rqbit port</label><input name="rqbit_port" type="number" min="1" max="65535" value="%d"></div>
  <div class="cfg-help">rqbit has no username/password in this setup.</div>
  <div class="cfg-row"><label class="cfg-label">Deluge protocol</label><select name="deluge_scheme"><option value="http"%s>http</option><option value="https"%s>https</option></select></div>
  <div class="cfg-row"><label class="cfg-label">Deluge host</label><input name="deluge_host" value="%s"></div>
  <div class="cfg-row"><label class="cfg-label">Deluge port</label><input name="deluge_port" type="number" min="1" max="65535" value="%d"></div>
  <div class="cfg-row"><label class="cfg-label">Deluge password</label><input name="deluge_password" type="password" value="" placeholder="%s"></div>
  <div class="cfg-actions"><button type="submit" name="action" value="test">Test selected client</button><button type="submit" name="action" value="save">Save &amp; route Arr grabs</button></div>
  <div class="cfg-help">Saving enables the selected Deluge/rqbit client and disables the other managed client in both Radarr and Sonarr. Other client types are left untouched.</div>
</form>
</section>""" % (
    dcfg["selected"].upper(),
    " selected" if dcfg["selected"] == "rqbit" else "",
    " selected" if dcfg["selected"] == "deluge" else "",
    " selected" if dcfg["rqbit"]["scheme"] == "http" else "",
    " selected" if dcfg["rqbit"]["scheme"] == "https" else "",
    html.escape(dcfg["rqbit"]["host"], quote=True),
    int(dcfg["rqbit"]["port"]),
    " selected" if dcfg["deluge"]["scheme"] == "http" else "",
    " selected" if dcfg["deluge"]["scheme"] == "https" else "",
    html.escape(dcfg["deluge"]["host"], quote=True),
    int(dcfg["deluge"]["port"]),
    "Saved" if dcfg["deluge"]["password"] else "Enter password",
)

'''
    s = s.replace(card_anchor, downloader_card + card_anchor, 1)

s = s.replace('    __RADARR_CARD__\n    __SONARR_CARD__\n    __AUTH_CARD__', '    __RADARR_CARD__\n    __SONARR_CARD__\n    __DOWNLOADER_CARD__\n    __AUTH_CARD__', 1)
if '"__DOWNLOADER_CARD__": downloader_card' not in s:
    s = s.replace('        "__AUTH_CARD__": auth_card,', '        "__DOWNLOADER_CARD__": downloader_card,\n        "__AUTH_CARD__": auth_card,', 1)

s = s.replace('            "/connection-settings",\n            "/budget-settings",', '            "/connection-settings",\n            "/download-client-settings",\n            "/budget-settings",', 1)

handler_anchor = '        if self.path == "/connection-settings":'
if 'if self.path == "/download-client-settings":' not in s:
    if handler_anchor not in s:
        raise SystemExit('connection handler anchor missing')
    handler = r'''        if self.path == "/download-client-settings":
            if not ENABLE_ACTIONS:
                self.send_error(403); return
            action = (form.get("action") or [""])[0]
            selected = (form.get("selected") or [""])[0]
            d_scheme = (form.get("deluge_scheme") or ["http"])[0]
            d_host = (form.get("deluge_host") or ["127.0.0.1"])[0]
            d_port = (form.get("deluge_port") or ["8112"])[0]
            d_password = (form.get("deluge_password") or [""])[0]
            r_scheme = (form.get("rqbit_scheme") or ["http"])[0]
            r_host = (form.get("rqbit_host") or ["127.0.0.1"])[0]
            r_port = (form.get("rqbit_port") or ["3030"])[0]

            try:
                if action == "test":
                    if selected == "deluge":
                        message = test_download_client("deluge", d_scheme, d_host, d_port, d_password)
                    elif selected == "rqbit":
                        message = test_download_client("rqbit", r_scheme, r_host, r_port)
                    else:
                        raise ValueError("Choose Deluge or rqbit")
                    bad = False
                elif action == "save":
                    update_download_client_settings(selected, d_scheme, d_host, d_port, d_password, r_scheme, r_host, r_port)
                    results, errors = apply_download_client_selection(selected)
                    if errors:
                        details = "; ".join("%s: %s" % (name, error) for name, error in sorted(errors.items()))
                        raise RuntimeError("Settings saved, but Arr routing failed: " + details)
                    message = "%s saved and selected for Radarr/Sonarr grabs." % selected
                    bad = False
                else:
                    raise ValueError("Unknown action")
            except Exception as exc:
                message = str(exc)
                bad = True

            location = "/settings?message=" + urllib.parse.quote(message)
            if bad:
                location += "&bad=1"
            self.send_response(303)
            self.send_header("Location", location)
            self.end_headers()
            return


'''
    s = s.replace(handler_anchor, handler + handler_anchor, 1)

required = [
    'def download_client_settings():',
    'def sync_arr_download_client_selection(app, selected):',
    'action="/download-client-settings"',
    'if self.path == "/download-client-settings":',
    '__DOWNLOADER_CARD__',
]
missing = [x for x in required if x not in s]
if missing:
    raise SystemExit('missing after patch: %r' % missing)

p.write_text(s)
print('patch applied')
