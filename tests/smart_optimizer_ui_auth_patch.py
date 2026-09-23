#!/usr/bin/env python3
import hashlib
import sys
from pathlib import Path

if len(sys.argv) != 2:
    raise SystemExit("usage: smart_optimizer_ui_auth_patch.py UI")

p = Path(sys.argv[1])
src = p.read_text(encoding="utf-8")

EXPECTED = "cd342503f102b099bd8927a45f2ad9cd049fea2afed035af6a120492a6430c30"
actual = hashlib.sha256(src.encode("utf-8")).hexdigest()
if actual != EXPECTED:
    raise SystemExit("STOP: UI hash mismatch: " + actual)

def replace_once(old, new, label):
    global src
    count = src.count(old)
    if count != 1:
        raise SystemExit("STOP: %s anchor count=%d" % (label, count))
    src = src.replace(old, new, 1)

replace_once(
'''import html
import http.cookiejar
import json
''',
'''import hashlib
import hmac
import html
import http.cookiejar
import json
import secrets
''',
"imports-1"
)

replace_once(
'''from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
''',
'''from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
''',
"imports-2"
)

replace_once(
'''CONNECTION_FILE = os.environ.get("SMART_OPTIMIZER_CONNECTIONS", os.path.join(BASE_DIR, "smart-optimizer-connections.json"))

LIBRARY_CACHE_DIR''',
'''CONNECTION_FILE = os.environ.get("SMART_OPTIMIZER_CONNECTIONS", os.path.join(BASE_DIR, "smart-optimizer-connections.json"))
AUTH_FILE = os.environ.get(
    "SMART_OPTIMIZER_AUTH",
    os.path.join(
        os.path.dirname(CONTROL_FILE),
        "smart-optimizer-auth.json"
    )
)
AUTH_COOKIE = "smart_optimizer_session"
AUTH_PBKDF2_ITERATIONS = 310000
AUTH_SESSIONS = {}
AUTH_SESSION_LOCK = threading.Lock()

LIBRARY_CACHE_DIR''',
"auth-constants"
)

replace_once(
'''def connection(app):
''',
'''def load_auth_config():
    try:
        with open(AUTH_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_auth_config(data):
    folder = os.path.dirname(AUTH_FILE)
    if folder:
        os.makedirs(folder, exist_ok=True)

    with open(AUTH_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())

    try:
        os.chmod(AUTH_FILE, 0o600)
    except Exception:
        pass


def auth_enabled():
    cfg = load_auth_config()

    return bool(
        cfg.get("enabled")
        and cfg.get("username")
        and cfg.get("salt")
        and cfg.get("password_hash")
    )


def _password_digest(password, salt_hex, iterations):
    try:
        salt = bytes.fromhex(str(salt_hex or ""))
        rounds = int(iterations or AUTH_PBKDF2_ITERATIONS)
    except Exception:
        return ""

    if not salt or rounds < 100000:
        return ""

    return hashlib.pbkdf2_hmac(
        "sha256",
        str(password or "").encode("utf-8"),
        salt,
        rounds
    ).hex()


def verify_auth_credentials(username, password):
    cfg = load_auth_config()

    if not auth_enabled():
        return True

    stored_user = str(cfg.get("username") or "")
    supplied_user = str(username or "")

    if not hmac.compare_digest(stored_user, supplied_user):
        return False

    expected = str(cfg.get("password_hash") or "")
    actual = _password_digest(
        password,
        cfg.get("salt"),
        cfg.get("iterations")
    )

    return bool(
        actual
        and hmac.compare_digest(expected, actual)
    )


def clear_auth_sessions():
    with AUTH_SESSION_LOCK:
        AUTH_SESSIONS.clear()


def update_auth_settings(enabled, username, password, confirmation):
    cfg = load_auth_config()
    username = str(username or "").strip()
    password = str(password or "")
    confirmation = str(confirmation or "")

    if not enabled:
        cfg["enabled"] = False
        save_auth_config(cfg)
        clear_auth_sessions()
        return

    if not username:
        raise ValueError("Username is required")

    if password:
        if len(password) < 8:
            raise ValueError("Password must be at least 8 characters")

        if password != confirmation:
            raise ValueError("Password confirmation does not match")

        salt = secrets.token_bytes(16)
        iterations = AUTH_PBKDF2_ITERATIONS

        cfg["salt"] = salt.hex()
        cfg["iterations"] = iterations
        cfg["password_hash"] = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            iterations
        ).hex()

    elif not (
        cfg.get("salt")
        and cfg.get("password_hash")
    ):
        raise ValueError(
            "Set a password before enabling authentication"
        )

    cfg["enabled"] = True
    cfg["username"] = username

    save_auth_config(cfg)
    clear_auth_sessions()


def create_auth_session(remember=False):
    token = secrets.token_urlsafe(32)
    lifetime = 30 * 86400 if remember else 12 * 3600
    expires = int(time.time()) + lifetime

    with AUTH_SESSION_LOCK:
        now = int(time.time())

        for old_token, old_expiry in list(AUTH_SESSIONS.items()):
            if int(old_expiry or 0) <= now:
                AUTH_SESSIONS.pop(old_token, None)

        AUTH_SESSIONS[token] = expires

    return token, lifetime


def auth_session_valid(cookie_header):
    if not auth_enabled():
        return True

    try:
        cookie = SimpleCookie()
        cookie.load(str(cookie_header or ""))
        morsel = cookie.get(AUTH_COOKIE)
        token = morsel.value if morsel else ""
    except Exception:
        token = ""

    if not token:
        return False

    now = int(time.time())

    with AUTH_SESSION_LOCK:
        expiry = int(AUTH_SESSIONS.get(token) or 0)

        if expiry <= now:
            AUTH_SESSIONS.pop(token, None)
            return False

    return True


def safe_return_path(value):
    value = str(value or "/").strip()

    if (
        not value.startswith("/")
        or value.startswith("//")
        or "\\r" in value
        or "\\n" in value
    ):
        return "/"

    return value


def login_page(message="", return_to="/"):
    notice = ""

    if message:
        notice = (
            "<div class='notice bad' style='margin-bottom:18px'>%s</div>"
            % html.escape(message)
        )

    return """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Smart Optimizer · Sign in</title>
<style>
%s
.loginpage{min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px}
.logincard{width:min(390px,100%%);background:#15191f;border:1px solid rgba(255,255,255,.10);border-radius:16px;overflow:hidden;box-shadow:0 24px 70px rgba(0,0,0,.42)}
.loginbrand{background:#232932;padding:20px;text-align:center}
.loginmark{width:48px;height:48px;margin:auto;border-radius:14px;display:flex;align-items:center;justify-content:center;background:#f0bd26;color:#111;font-weight:900;font-size:25px}
.loginbody{padding:26px}
.loginbody h2{text-align:center;margin:0 0 22px;font-size:1rem;letter-spacing:.08em}
.loginbody label{display:block;margin:12px 0 6px;color:#aeb8c5;font-size:.82rem}
.loginbody input[type=text],.loginbody input[type=password]{width:100%%;box-sizing:border-box}
.loginrow{display:flex;align-items:center;justify-content:space-between;margin:15px 0 20px}
.loginrow label{margin:0;display:flex;align-items:center;gap:8px}
.loginbody button{width:100%%;padding:11px 14px}
</style>
</head>
<body>
<div class="loginpage">
<div class="logincard">
<div class="loginbrand"><div class="loginmark">S</div></div>
<div class="loginbody">
<h2>SIGN IN TO CONTINUE</h2>
%s
<form method="post" action="/login">
<input type="hidden" name="return_to" value="%s">
<label>Username</label>
<input name="username" type="text" autocomplete="username" required autofocus>
<label>Password</label>
<input name="password" type="password" autocomplete="current-password" required>
<div class="loginrow">
<label><input name="remember" type="checkbox" value="1"> Remember me</label>
</div>
<button type="submit">Login</button>
</form>
</div>
</div>
</div>
</body>
</html>""" % (
        CSS,
        notice,
        html.escape(
            safe_return_path(return_to),
            quote=True
        )
    )


def connection(app):
''',
"auth-functions"
)

replace_once(
'''def settings_page(message="", bad=False):
    rcfg, scfg = connection("radarr"), connection("sonarr")
    notice = ""
''',
'''def settings_page(message="", bad=False):
    rcfg, scfg = connection("radarr"), connection("sonarr")
    acfg = load_auth_config()
    auth_on = auth_enabled()
    auth_user = str(acfg.get("username") or "admin")
    notice = ""
''',
"settings-head"
)

replace_once(
'''    return """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Smart Optimizer · Settings</title><style>%s</style></head><body><div class="shell">
<div class="topbar compact"><div class="brand"><div class="brandcopy"><h1>Connection settings</h1><div>Radarr and Sonarr API connections.</div></div></div><div class="nav"><a class="badge" href="/">← Smart Optimizer</a></div></div>
%s
<div class="dashboard2">%s%s</div>
<div class="notice">Saved settings override Docker environment values. Leaving the API-key field blank keeps the currently configured key. Optimizer queues, cursors, history and downsize rules are not changed here.</div>
</div></body></html>""" % (CSS, notice, card("radarr", rcfg, 7878), card("sonarr", scfg, 8989))
''',
'''    auth_card = """<div class="panel"><div class="panelhead"><div><h3>UI authentication</h3><p>Require a username and password before opening Smart Optimizer.</p></div><span class="badge">%s</span></div>
<form method="post" action="/auth-settings" class="sidecontent">
<div class="metricline"><span>Authentication</span><label style="display:flex;align-items:center;gap:8px"><input name="enabled" type="checkbox" value="1"%s> Enabled</label></div>
<div class="metricline"><span>Username</span><input name="username" value="%s" autocomplete="username" required></div>
<div class="metricline"><span>Password</span><input name="password" type="password" autocomplete="new-password" placeholder="%s"></div>
<div class="metricline"><span>Confirm password</span><input name="confirmation" type="password" autocomplete="new-password" placeholder="Repeat new password"></div>
<div style="display:flex;gap:10px;justify-content:flex-end;margin-top:16px">%s<button type="submit">Save authentication</button></div>
</form></div>""" % (
        "ENABLED" if auth_on else "DISABLED",
        " checked" if auth_on else "",
        html.escape(auth_user, quote=True),
        "Leave blank to keep current password" if acfg.get("password_hash") else "Minimum 8 characters",
        '<a class="badge" href="/logout">Log out</a>' if auth_on else ""
    )

    return """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Smart Optimizer · Settings</title><style>%s</style></head><body><div class="shell">
<div class="topbar compact"><div class="brand"><div class="brandcopy"><h1>Connection settings</h1><div>Radarr, Sonarr and UI access.</div></div></div><div class="nav"><a class="badge" href="/">← Smart Optimizer</a></div></div>
%s
<div class="dashboard2">%s%s%s</div>
<div class="notice">Passwords are stored only as a salted PBKDF2-SHA256 hash. Saved connection settings override Docker environment values. Optimizer queues, cursors, history and downsize rules are not changed here.</div>
</div></body></html>""" % (
        CSS,
        notice,
        card("radarr", rcfg, 7878),
        card("sonarr", scfg, 8989),
        auth_card
    )
''',
"settings-auth-card"
)

replace_once(
'''class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
''',
'''class Handler(BaseHTTPRequestHandler):
    def _is_authenticated(self):
        return auth_session_valid(
            self.headers.get("Cookie") or ""
        )

    def _send_redirect(self, target, clear_cookie=False):
        self.send_response(303)
        self.send_header("Location", target)

        if clear_cookie:
            self.send_header(
                "Set-Cookie",
                "%s=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"
                % AUTH_COOKIE
            )

        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _send_login_redirect(self):
        target = safe_return_path(self.path)
        self._send_redirect(
            "/login?next=%s"
            % urllib.parse.quote(target, safe="")
        )

    def _require_auth(self, path):
        if self._is_authenticated():
            return True

        if path in (
            "/status",
            "/library-search",
            "/exclusions-json",
        ):
            body = json.dumps({
                "error": "authentication required"
            }).encode("utf-8")

            self.send_response(401)
            self.send_header(
                "Content-Type",
                "application/json"
            )
            self.send_header(
                "Content-Length",
                str(len(body))
            )
            self.send_header(
                "Cache-Control",
                "no-store"
            )
            self.end_headers()
            self.wfile.write(body)
            return False

        self._send_login_redirect()
        return False

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/login":
            if not auth_enabled():
                self._send_redirect("/")
                return

            qs = urllib.parse.parse_qs(parsed.query)
            return_to = (
                qs.get("next")
                or ["/"]
            )[0]

            body = login_page(
                "",
                return_to
            ).encode("utf-8")

            self.send_response(200)
            self.send_header(
                "Content-Type",
                "text/html; charset=utf-8"
            )
            self.send_header(
                "Content-Length",
                str(len(body))
            )
            self.send_header(
                "Cache-Control",
                "no-store"
            )
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/logout":
            clear_auth_sessions()
            self._send_redirect(
                "/login" if auth_enabled() else "/",
                clear_cookie=True
            )
            return

        if not self._require_auth(path):
            return
''',
"handler-get-auth"
)

replace_once(
'''    def do_POST(self):
        length = min(int(self.headers.get("Content-Length", "0")), 4096)
        form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
        if self.path == "/connection-settings":
''',
'''    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path

        if path == "/login":
            length = min(
                int(self.headers.get("Content-Length", "0")),
                4096
            )

            form = urllib.parse.parse_qs(
                self.rfile.read(length).decode("utf-8")
            )

            username = (
                form.get("username")
                or [""]
            )[0]

            password = (
                form.get("password")
                or [""]
            )[0]

            remember = (
                form.get("remember")
                or [""]
            )[0] == "1"

            return_to = safe_return_path(
                (
                    form.get("return_to")
                    or ["/"]
                )[0]
            )

            if not (
                auth_enabled()
                and verify_auth_credentials(
                    username,
                    password
                )
            ):
                body = login_page(
                    "Invalid username or password.",
                    return_to
                ).encode("utf-8")

                self.send_response(401)
                self.send_header(
                    "Content-Type",
                    "text/html; charset=utf-8"
                )
                self.send_header(
                    "Content-Length",
                    str(len(body))
                )
                self.send_header(
                    "Cache-Control",
                    "no-store"
                )
                self.end_headers()
                self.wfile.write(body)
                return

            token, lifetime = create_auth_session(
                remember
            )

            self.send_response(303)
            self.send_header(
                "Location",
                return_to
            )
            self.send_header(
                "Set-Cookie",
                "%s=%s; Path=/; Max-Age=%d; HttpOnly; SameSite=Lax"
                % (
                    AUTH_COOKIE,
                    token,
                    lifetime
                )
            )
            self.send_header(
                "Cache-Control",
                "no-store"
            )
            self.end_headers()
            return

        if path == "/auth-settings":
            if auth_enabled() and not self._is_authenticated():
                self._send_login_redirect()
                return

            length = min(
                int(self.headers.get("Content-Length", "0")),
                4096
            )

            form = urllib.parse.parse_qs(
                self.rfile.read(length).decode("utf-8")
            )

            enabled = (
                form.get("enabled")
                or [""]
            )[0] == "1"

            try:
                update_auth_settings(
                    enabled,
                    (form.get("username") or [""])[0],
                    (form.get("password") or [""])[0],
                    (form.get("confirmation") or [""])[0],
                )
            except Exception as exc:
                body = settings_page(
                    str(exc),
                    True
                ).encode("utf-8")

                self.send_response(400)
                self.send_header(
                    "Content-Type",
                    "text/html; charset=utf-8"
                )
                self.send_header(
                    "Content-Length",
                    str(len(body))
                )
                self.send_header(
                    "Cache-Control",
                    "no-store"
                )
                self.end_headers()
                self.wfile.write(body)
                return

            if enabled:
                self._send_redirect(
                    "/login",
                    clear_cookie=True
                )
            else:
                self._send_redirect(
                    "/settings",
                    clear_cookie=True
                )

            return

        if not self._require_auth(path):
            return

        length = min(int(self.headers.get("Content-Length", "0")), 4096)
        form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))

        if self.path == "/connection-settings":
''',
"handler-post-auth"
)

replace_once(
'''    print("Actions:", "ENABLED" if ENABLE_ACTIONS else "disabled (read-only)")
    if HOST not in ("127.0.0.1", "localhost", "::1"):
        print("WARNING: UI has no built-in authentication; expose only on a trusted LAN/reverse proxy.")
''',
'''    print("Actions:", "ENABLED" if ENABLE_ACTIONS else "disabled (read-only)")
    print(
        "Authentication:",
        "ENABLED" if auth_enabled() else "disabled"
    )
    if (
        HOST not in ("127.0.0.1", "localhost", "::1")
        and not auth_enabled()
    ):
        print(
            "WARNING: UI authentication is disabled; "
            "expose only on a trusted LAN/reverse proxy."
        )
''',
"startup-auth"
)

p.write_text(src, encoding="utf-8")

print("SMART OPTIMIZER UI AUTH PATCH COMPLETE")
print(
    "UI SHA256",
    hashlib.sha256(src.encode("utf-8")).hexdigest()
)
