import base64
#!/usr/bin/env python3
"""Optional lightweight web UI for Radarr Smart Optimizer.

Standard library only. The optimizer remains fully usable without this file.
"""

import hashlib
import hmac
import html
import http.cookiejar
import json
import secrets
import os
import re
import signal
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OPTIMIZER = os.environ.get("RADARR_OPTIMIZER_SCRIPT", os.path.join(BASE_DIR, "radarr-smart-optimizer.py"))
STATE_FILE = os.environ.get("RADARR_OPTIMIZER_STATE", os.path.join(BASE_DIR, "radarr-smart-optimizer-state.json"))
RADARR_URL = os.environ.get("RADARR_URL", "http://127.0.0.1:7878").rstrip("/")
SONARR_URL = os.environ.get("SONARR_URL", "http://127.0.0.1:8989").rstrip("/")
SONARR_KEY = os.environ.get("SONARR_KEY", "").strip()
SONARR_STATE_FILE = os.environ.get("SONARR_OPTIMIZER_STATE", os.path.join(BASE_DIR, "sonarr-smart-optimizer-state.json"))
API_KEY = os.environ.get("RADARR_KEY", "").strip()
HOST = os.environ.get("SMART_UI_HOST", os.environ.get("RADARR_UI_HOST", "127.0.0.1"))
PORT = int(os.environ.get("SMART_UI_PORT", os.environ.get("RADARR_UI_PORT", "8788")))
ENABLE_ACTIONS = os.environ.get("SMART_UI_ENABLE_ACTIONS", "1").lower() in ("1", "true", "yes")
HISTORY_PAGES = max(1, min(20, int(os.environ.get("RADARR_UI_HISTORY_PAGES", "5"))))
MAX_OUTPUT = 50000
CONTROL_FILE = os.environ.get("SMART_OPTIMIZER_CONTROL", os.path.join(BASE_DIR, "smart-optimizer-control.json"))
SONARR_OPTIMIZER = os.environ.get("SONARR_OPTIMIZER_SCRIPT", os.path.join(BASE_DIR, "sonarr-smart-optimizer.py"))
RADARR_BASE_BUDGET = int(os.environ.get("RADARR_DAILY_SEARCH_BUDGET", "400"))
SONARR_BASE_BUDGET = int(os.environ.get("SONARR_DAILY_SEARCH_BUDGET", "400"))
MAX_MANUAL = max(1, int(os.environ.get("SMART_UI_MAX_MANUAL_SEARCHES", "10000")))
CONNECTION_FILE = os.environ.get("SMART_OPTIMIZER_CONNECTIONS", os.path.join(BASE_DIR, "smart-optimizer-connections.json"))
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


# SMART OPTIMIZER UPDATE SYSTEM START

SMART_OPTIMIZER_VERSION = (
    str(
        os.environ.get(
            "SMART_OPTIMIZER_VERSION",
            "2.0.0"
        )
        or "2.0.0"
    )
    .strip()
    .lstrip("vV")
)

UPDATE_REPOSITORY = (
    "MrRobot-88/Smart-Optimizer-UI"
)

UPDATE_RELEASE_API = (
    "https://api.github.com/repos/"
    + UPDATE_REPOSITORY
    + "/releases/latest"
)

SMART_OPTIMIZER_PACKAGE_VAR = (
    str(
        os.environ.get(
            "SMART_OPTIMIZER_PACKAGE_VAR",
            ""
        )
        or ""
    )
    .strip()
)


UPDATE_DIR = os.environ.get(
    "SMART_OPTIMIZER_UPDATE_DIR",
    (
        os.path.join(
            SMART_OPTIMIZER_PACKAGE_VAR,
            "updates"
        )
        if SMART_OPTIMIZER_PACKAGE_VAR
        else os.path.join(
            os.path.dirname(
                CONTROL_FILE
            ),
            "updates"
        )
    )
)

UPDATE_REQUEST_FILE = os.path.join(
    UPDATE_DIR,
    "install-request.json"
)

UPDATE_STATUS_FILE = os.path.join(
    UPDATE_DIR,
    "status.json"
)

SMART_SELF_UPDATE_MODE = (
    str(
        os.environ.get(
            "SMART_OPTIMIZER_SELF_UPDATE_MODE",
            ""
        )
        or ""
    )
    .strip()
    .lower()
)

APP_UPDATE_REQUEST_FILE = os.environ.get(
    "SMART_OPTIMIZER_APP_UPDATE_REQUEST",
    os.path.join(
        UPDATE_DIR,
        "app-update-request.json"
    )
)

UPDATE_LOCK = threading.Lock()

UPDATE_CACHE = {
    "loaded": 0,
    "release": None,
}

# SMART OPTIMIZER UPDATE SYSTEM END

LIBRARY_CACHE_DIR = os.environ.get("SMART_OPTIMIZER_CACHE_DIR", "/data")
LIBRARY_CACHE_REFRESH_SECONDS = 60

LIBRARY_CACHE_FILES = {
    "radarr": os.path.join(LIBRARY_CACHE_DIR, "radarr-library-cache.json"),
    "sonarr": os.path.join(LIBRARY_CACHE_DIR, "sonarr-library-cache.json"),
}

library_cache_lock = threading.Lock()
library_cache_refreshing = {
    "radarr": False,
    "sonarr": False,
}

def load_connections():
    try:
        with open(CONNECTION_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def save_connections(data):
    os.makedirs(os.path.dirname(CONNECTION_FILE), exist_ok=True)
    with open(CONNECTION_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.flush(); os.fsync(f.fileno())

def load_auth_config():
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
        or "\r" in value
        or "\n" in value
    ):
        return "/"

    return value



# SMART EXTERNAL ASSETS START
#
# Large UI artwork lives outside this Python source.
# Container path:
#     /app/assets
#
import os as _smart_assets_os

SMART_UI_ASSET_DIR = (
    _smart_assets_os.environ.get(
        "SMART_UI_ASSET_DIR",
        "/app/assets",
    )
)


def _smart_asset_bytes(filename):

    path = _smart_assets_os.path.join(
        SMART_UI_ASSET_DIR,
        filename,
    )

    with open(path, "rb") as fh:
        return fh.read()


# SMART EXTERNAL ASSETS END


# SMART COMMON FRAGMENTS START
#
# Shared UI fragments loaded once into RAM.
#
# Expansion happens AFTER old-style Python "%"
# page formatting, preventing literal CSS/JS percent
# signs from breaking the Python templates.
#

import os as _smart_common_os


SMART_COMMON_FRAGMENT_DIR = (
    _smart_common_os.path.join(
        _smart_common_os.environ.get(
            "SMART_UI_ASSET_DIR",
            "/app/assets",
        ),
        "fragments",
    )
)


def _smart_load_common_fragment(filename):

    path = _smart_common_os.path.join(
        SMART_COMMON_FRAGMENT_DIR,
        filename,
    )

    with open(
        path,
        "r",
        encoding="utf-8",
    ) as fh:
        return fh.read()


SMART_COMMON_UPDATE_STATUS_FAVICON = (
    _smart_load_common_fragment(
        "update-status-favicon.html"
    )
)


SMART_COMMON_ADMIN_UPDATE_MODE = (
    _smart_load_common_fragment(
        "admin-update-mode.html"
    )
)


SMART_COMMON_UPDATE_SENTINEL = (
    "<!-- SMART_COMMON_UPDATE_STATUS_FAVICON -->"
)


SMART_COMMON_ADMIN_SENTINEL = (
    "<!-- SMART_COMMON_ADMIN_UPDATE_MODE -->"
)


def _smart_expand_common(html_text):

    return (
        html_text
        .replace(
            SMART_COMMON_UPDATE_SENTINEL,
            SMART_COMMON_UPDATE_STATUS_FAVICON,
        )
        .replace(
            SMART_COMMON_ADMIN_SENTINEL,
            SMART_COMMON_ADMIN_UPDATE_MODE,
        )
    )


# SMART COMMON FRAGMENTS END






def login_page(message="", return_to="/"):
    notice = ""

    if message:
        notice = (
            '<div class="login-error">'
            + html.escape(message)
            + '</div>'
        )

    rendered = _smart_expand_common("""<!doctype html>
<html>

<head>

<meta charset="utf-8">

<meta
name="viewport"
content="width=device-width,initial-scale=1"
>

<title>Smart Optimizer · Sign in</title>

<style>

__BASE_CSS__

*{
    box-sizing:border-box;
}

html,
body{
    margin:0;
    width:100%;
    min-height:100%;
}

body{
    min-height:100vh;

    font-family:
        Inter,
        system-ui,
        -apple-system,
        BlinkMacSystemFont,
        "Segoe UI",
        sans-serif;

    color:#f5f9ff;

    background:#020811;

    overflow:hidden;
}


/* =========================================================
   BACKGROUND
   ========================================================= */

.login-background{
    position:fixed;
    inset:0;
    z-index:0;

    background:
        linear-gradient(
            180deg,
            rgba(1,7,14,.18),
            rgba(1,7,14,.30)
        ),
        url('/home-background.png?v=20260923-233901')
        center center / cover
        no-repeat;

}


.login-background::after{
    content:"";

    position:absolute;
    inset:0;

    background:
        radial-gradient(
            circle at center,
            rgba(0,170,255,.05) 0%,
            rgba(0,10,22,.08) 42%,
            rgba(0,5,12,.30) 100%
        );
}


/* =========================================================
   PAGE
   ========================================================= */

.login-page{
    position:relative;
    z-index:1;

    min-height:100vh;

    display:flex;
    align-items:center;
    justify-content:center;

    padding:32px 20px;
}


.login-shell{
    width:min(470px, 94vw);
}


/* =========================================================
   LOGO
   ========================================================= */

.login-logo{
    width:min(390px, 82vw);

    margin:
        0 auto
        22px;
}


.login-logo img{
    display:block;

    width:100%;
    height:auto;

    object-fit:contain;

    filter:
        drop-shadow(
            0 14px 30px
            rgba(0,0,0,.34)
        );
}


/* =========================================================
   GLASS CARD
   ========================================================= */

.login-card{
    position:relative;

    overflow:hidden;

    padding:
        34px
        34px
        30px;

    border-radius:22px;

    background:
        linear-gradient(
            145deg,
            rgba(11,27,46,.92),
            rgba(5,16,29,.90)
        );

    border:
        1px solid
        rgba(38,179,255,.40);

    backdrop-filter:
        blur(18px);

    -webkit-backdrop-filter:
        blur(18px);

    box-shadow:
        0 24px 70px
        rgba(0,0,0,.44),

        0 0 32px
        rgba(0,166,255,.10),

        inset
        0 0 0 1px
        rgba(255,255,255,.025);
}


.login-card::before{
    content:"";

    position:absolute;

    left:14%;
    right:14%;
    top:0;

    height:2px;

    border-radius:999px;

    background:
        linear-gradient(
            90deg,
            transparent,
            #00bfff,
            #4ce4ff,
            transparent
        );

    box-shadow:
        0 0 20px
        rgba(0,195,255,.75);
}


/* =========================================================
   CARD HEADER
   ========================================================= */

.login-heading{
    text-align:center;

    margin-bottom:28px;
}


.login-heading h1{
    margin:0;

    color:#f8fbff;

    font-size:1.55rem;

    line-height:1.2;

    font-weight:750;

    letter-spacing:-.025em;
}


.login-heading p{
    margin:
        9px 0
        0;

    color:
        rgba(206,220,240,.66);

    font-size:.90rem;

    line-height:1.5;
}


/* =========================================================
   ERROR
   ========================================================= */

.login-error{
    margin-bottom:20px;

    padding:
        12px
        14px;

    border-radius:12px;

    text-align:center;

    color:#ffd9d3;

    font-size:.84rem;

    background:
        rgba(103,27,30,.48);

    border:
        1px solid
        rgba(255,92,76,.48);
}


/* =========================================================
   FORM
   ========================================================= */

.login-form{
    display:flex;

    flex-direction:column;

    gap:18px;
}


.login-field label{
    display:block;

    margin-bottom:7px;

    color:
        rgba(219,231,247,.76);

    font-size:.78rem;

    font-weight:600;
}


.login-input-wrap{
    position:relative;
}


.login-input-icon{
    position:absolute;

    left:15px;
    top:50%;

    transform:
        translateY(-50%);

    width:20px;
    height:20px;

    color:
        rgba(67,199,255,.82);

    pointer-events:none;
}


.login-input-icon svg{
    display:block;

    width:100%;
    height:100%;
}


.login-page input[type=text],
.login-page input[type=password]{
    display:block;

    width:100%;
    height:52px;

    margin:0;

    padding:
        0 15px
        0 46px;

    color:#f8fbff;

    font-family:inherit;

    font-size:.94rem;

    border-radius:13px;

    border:
        1px solid
        rgba(89,142,202,.30);

    outline:none;

    background:
        rgba(3,13,24,.72);

    box-shadow:
        inset
        0 0 0 1px
        rgba(255,255,255,.015);

    transition:
        border-color .18s ease,
        box-shadow .18s ease,
        background .18s ease;
}


.login-page input[type=text]:hover,
.login-page input[type=password]:hover{
    border-color:
        rgba(71,174,229,.48);
}


.login-page input[type=text]:focus,
.login-page input[type=password]:focus{
    border-color:
        rgba(49,205,255,.90);

    background:
        rgba(5,18,32,.88);

    box-shadow:
        0 0 0 3px
        rgba(0,174,255,.08),

        0 0 20px
        rgba(0,166,255,.11);
}


/* =========================================================
   REMEMBER ME
   ========================================================= */

.login-options{
    display:flex;

    align-items:center;

    min-height:24px;
}


.remember-label{
    display:flex;

    align-items:center;

    gap:9px;

    cursor:pointer;

    color:
        rgba(211,224,242,.68);

    font-size:.82rem;
}


.remember-label input{
    width:16px;
    height:16px;

    margin:0;

    accent-color:#18bfff;
}


/* =========================================================
   BUTTON
   ========================================================= */

.login-submit{
    width:100%;
    height:52px;

    margin-top:2px;

    border-radius:13px;

    border:
        1px solid
        rgba(42,192,255,.58);

    cursor:pointer;

    color:#f8fcff;

    font-family:inherit;

    font-size:.94rem;

    font-weight:700;

    background:
        linear-gradient(
            135deg,
            rgba(11,72,116,.96),
            rgba(6,41,72,.96)
        );

    box-shadow:
        0 10px 24px
        rgba(0,0,0,.24),

        inset
        0 0 0 1px
        rgba(255,255,255,.025);

    transition:
        transform .18s ease,
        border-color .18s ease,
        box-shadow .18s ease,
        background .18s ease;
}


.login-submit:hover{
    transform:
        translateY(-2px);

    border-color:
        rgba(63,218,255,.98);

    background:
        linear-gradient(
            135deg,
            rgba(12,96,150,.98),
            rgba(7,55,94,.98)
        );

    box-shadow:
        0 12px 28px
        rgba(0,0,0,.28),

        0 0 22px
        rgba(0,178,255,.22);
}


.login-submit:active{
    transform:
        translateY(0);
}


/* =========================================================
   FOOTER
   ========================================================= */

.login-footer{
    margin-top:20px;

    text-align:center;

    color:
        rgba(172,194,222,.44);

    font-size:.62rem;

    letter-spacing:.22em;

    text-transform:uppercase;
}


.login-footer-line{
    width:58px;
    height:2px;

    margin:
        14px auto
        0;

    border-radius:999px;

    background:#25d3ff;

    box-shadow:
        0 0 14px
        rgba(0,194,255,.42);
}


/* =========================================================
   MOBILE
   ========================================================= */

@media(max-width:520px){

    .login-page{
        padding:
            22px
            14px;
    }


    .login-logo{
        width:min(
            360px,
            88vw
        );

        margin-bottom:16px;
    }


    .login-card{
        padding:
            28px
            22px
            24px;

        border-radius:19px;
    }

}



.rules-main-button:hover{
    transform:translateY(-1px);
}

.radarr-rules-button:hover{
    background:#ff7048 !important;
    border-color:#ff7048 !important;
    color:#fff !important;
    box-shadow:
        0 0 18px
        rgba(255,112,72,.52);
}

.sonarr-rules-button:hover{
    background:#28c5ff !important;
    border-color:#28c5ff !important;
    color:#041019 !important;
    box-shadow:
        0 0 18px
        rgba(40,197,255,.52);
}


/* SMART RULES STRONG HOVER GLOW */

.rules-main-button {
    display: inline-block !important;
    position: relative !important;
    transition:
        transform .16s ease,
        box-shadow .16s ease,
        filter .16s ease,
        background .16s ease,
        border-color .16s ease,
        color .16s ease !important;
}

.radarr-rules-button:hover,
.radarr-rules-button:focus-visible {
    color: #fff !important;
    background: rgba(255, 45, 45, .24) !important;
    border-color: #ff3b30 !important;

    box-shadow:
        0 0 6px rgba(255, 59, 48, 1),
        0 0 14px rgba(255, 59, 48, .95),
        0 0 28px rgba(255, 59, 48, .72),
        inset 0 0 12px rgba(255, 59, 48, .20) !important;

    filter:
        drop-shadow(0 0 5px rgba(255, 59, 48, 1))
        drop-shadow(0 0 12px rgba(255, 59, 48, .85)) !important;

    transform: translateY(-2px) scale(1.055) !important;
}

.sonarr-rules-button:hover,
.sonarr-rules-button:focus-visible {
    color: #fff !important;
    background: rgba(0, 174, 255, .24) !important;
    border-color: #20c7ff !important;

    box-shadow:
        0 0 6px rgba(32, 199, 255, 1),
        0 0 14px rgba(32, 199, 255, .95),
        0 0 28px rgba(32, 199, 255, .72),
        inset 0 0 12px rgba(32, 199, 255, .20) !important;

    filter:
        drop-shadow(0 0 5px rgba(32, 199, 255, 1))
        drop-shadow(0 0 12px rgba(32, 199, 255, .85)) !important;

    transform: translateY(-2px) scale(1.055) !important;
}

</style>

  <link rel="icon" type="image/png" sizes="32x32" href="/favicon.ico?v=2">
</head>


<body>


<div class="login-background"></div>


<div class="login-page">


<div class="login-shell">


<div class="login-logo">

<img
src="/smart-optimizer-logo-web.png"
alt="Smart Optimizer"
>

</div>


<div class="login-card">


<div class="login-heading">

<h1>Welcome back</h1>

<p>
Sign in to continue to Smart Optimizer.
</p>

</div>


__NOTICE__


<form
class="login-form"
method="post"
action="/login"
>


<input
type="hidden"
name="return_to"
value="__RETURN_TO__"
>


<div class="login-field">

<label for="login-username">
Username
</label>


<div class="login-input-wrap">


<div class="login-input-icon">

<svg
viewBox="0 0 24 24"
fill="none"
stroke="currentColor"
stroke-width="2"
>

<circle
cx="12"
cy="8"
r="4"
/>

<path
d="M4 21c0-4 3.6-7 8-7s8 3 8 7"
/>

</svg>

</div>


<input
id="login-username"
name="username"
type="text"
autocomplete="username"
required
autofocus
>


</div>

</div>



<div class="login-field">

<label for="login-password">
Password
</label>


<div class="login-input-wrap">


<div class="login-input-icon">

<svg
viewBox="0 0 24 24"
fill="none"
stroke="currentColor"
stroke-width="2"
>

<rect
x="4"
y="10"
width="16"
height="11"
rx="2"
/>

<path
d="M8 10V7a4 4 0 0 1 8 0v3"
/>

</svg>

</div>


<input
id="login-password"
name="password"
type="password"
autocomplete="current-password"
required
>


</div>

</div>



<div class="login-options">


<label class="remember-label">

<input
name="remember"
type="checkbox"
value="1"
>

<span>
Remember me
</span>

</label>


</div>



<button
class="login-submit"
type="submit"
>

Sign in

</button>


</form>


</div>


<div class="login-footer">

Secure local access

<div class="login-footer-line"></div>

</div>


</div>


</div>



<!-- SMART_COMMON_UPDATE_STATUS_FAVICON -->


<!-- SMART_COMMON_ADMIN_UPDATE_MODE -->

</body>

</html>""")


    rendered = rendered.replace(
        "__BASE_CSS__",
        CSS
    )

    rendered = rendered.replace(
        "__NOTICE__",
        notice
    )

    rendered = rendered.replace(
        "__RETURN_TO__",
        html.escape(
            safe_return_path(return_to),
            quote=True
        )
    )

    return rendered


def connection(app):
    data = load_connections().get(app, {})
    if app == "radarr":
        fallback_url, fallback_key, default_port = RADARR_URL, API_KEY, 7878
    else:
        fallback_url, fallback_key, default_port = SONARR_URL, SONARR_KEY, 8989
    parsed = urllib.parse.urlparse(fallback_url if "://" in fallback_url else "http://" + fallback_url)
    scheme = str(data.get("scheme") or parsed.scheme or "http").lower()
    host = str(data.get("host") or parsed.hostname or "127.0.0.1").strip()
    port = int(data.get("port") or parsed.port or default_port)
    key = str(data.get("api_key") or fallback_key or "").strip()
    return {"scheme": scheme if scheme in ("http", "https") else "http", "host": host, "port": port, "api_key": key}

def connection_url(app):
    cfg = connection(app)
    return "%s://%s:%d" % (cfg["scheme"], cfg["host"], cfg["port"])

def deluge_connection():
    """Private Deluge Web settings stored outside source code."""
    data = load_controls().get("deluge", {}) or {}
    return {
        "scheme": str(data.get("scheme") or "http").lower(),
        "host": str(data.get("host") or "172.17.0.2").strip(),
        "port": int(data.get("port") or 8112),
        "password": str(data.get("password") or ""),
    }


def _deluge_rpc(method, params=None):
    """Authenticated Deluge Web JSON-RPC call using private local settings."""
    cfg = deluge_connection()

    if not cfg["password"]:
        raise RuntimeError(
            "Deluge Web password is not configured"
        )

    url = "%s://%s:%d/json" % (
        cfg["scheme"],
        cfg["host"],
        cfg["port"],
    )

    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(
            jar
        )
    )

    rpc_id = 0

    def rpc(call_method, call_params=None):
        nonlocal rpc_id
        rpc_id += 1

        body = json.dumps({
            "method": call_method,
            "params": call_params or [],
            "id": rpc_id,
        }).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type":
                    "application/json",
                "Accept":"application/json",
            },
            method="POST",
        )

        with opener.open(
            req,
            timeout=15
        ) as response:
            result = json.loads(
                response.read().decode(
                    "utf-8"
                ) or "{}"
            )

        if result.get("error"):
            raise RuntimeError(
                "Deluge RPC %s failed: %s"
                % (
                    call_method,
                    result.get("error"),
                )
            )

        return result.get("result")

    if rpc(
        "auth.login",
        [cfg["password"]]
    ) is not True:
        raise RuntimeError(
            "Deluge Web login failed"
        )

    if rpc(
        "web.connected",
        []
    ) is not True:
        raise RuntimeError(
            "Deluge daemon is not connected"
        )

    return rpc(
        method,
        params or []
    )


def deluge_torrent_status(download_id):
    """
    Read one exact torrent by hash.

    Deluge's single-torrent RPC can reject plugin-provided status fields such
    as Label. The all-torrents status RPC is already proven on this NAS and
    returns the same data keyed by exact infohash, including Label fields.
    """
    wanted = str(
        download_id or ""
    ).strip().lower()

    if not wanted:
        return {}

    fields = [
        "state",
        "name",
        "progress",
        "total_done",
        "num_seeds",
        "total_seeds",
        "num_peers",
        "total_peers",
        "download_payload_rate",
        "distributed_copies",
        "last_seen_complete",
        "tracker",
        "trackers",
        "label",
    ]

    try:
        result = _deluge_rpc(
            "core.get_torrents_status",
            [
                {},
                fields,
            ],
        )
    except Exception:
        # Fail over without the optional Label status key. Tracker identity
        # still remains available and no destructive action is guessed.
        result = _deluge_rpc(
            "core.get_torrents_status",
            [
                {},
                [
                    x
                    for x in fields
                    if x != "label"
                ],
            ],
        )

    if not isinstance(result, dict):
        return {}

    for torrent_hash, status in result.items():
        if str(
            torrent_hash or ""
        ).strip().lower() == wanted:
            return (
                status
                if isinstance(status, dict)
                else {}
            )

    return {}


def deluge_torrent_is_dead(status):
    """True only when there is zero evidence of torrent activity."""
    if not isinstance(status, dict) or not status:
        return False

    try:
        return (
            float(status.get("progress") or 0) <= 0
            and int(status.get("total_done") or 0) <= 0
            and int(status.get("num_seeds") or 0) <= 0
            and int(status.get("num_peers") or 0) <= 0
            and int(status.get("download_payload_rate") or 0) <= 0
        )
    except (TypeError, ValueError):
        return False


def _deluge_tracker_hosts(status):
    hosts = set()

    raw = str(
        (status or {}).get("tracker") or ""
    ).strip()

    if raw:
        try:
            host = urllib.parse.urlparse(
                raw
            ).hostname
        except Exception:
            host = None

        if host:
            hosts.add(
                host.lower()
            )

    for item in (
        (status or {}).get("trackers")
        or []
    ):
        url = str(
            (item or {}).get("url")
            or ""
        ).strip()

        if not url:
            continue

        try:
            host = urllib.parse.urlparse(
                url
            ).hostname
        except Exception:
            host = None

        if host:
            hosts.add(
                host.lower()
            )

    return sorted(hosts)


def tracker_policy_from_deluge_status(status):
    """
    Backfill only old optimizer jobs that predate persisted indexer metadata.
    New grabs use the exact selected Arr indexer instead.
    """
    if not isinstance(status, dict) or not status:
        return ""

    label = str(
        status.get("label") or ""
    ).strip().lower()

    if label in (
        "torrentleech-movies",
        "torrentleech-tv",
    ):
        return "keep_seed"

    hosts = _deluge_tracker_hosts(
        status
    )

    if not hosts:
        return ""

    if any(
        "torrentleech" in host
        for host in hosts
    ):
        return "keep_seed"

    return "remove_after_verified_success"


def deluge_set_exact_label(
    download_id,
    label,
):
    """Label one exact optimizer-owned torrent hash."""
    download_id = str(
        download_id or ""
    ).strip().lower()

    label = str(
        label or ""
    ).strip()

    if not download_id or not label:
        raise RuntimeError(
            "exact Deluge hash/label required"
        )

    status = deluge_torrent_status(
        download_id
    )

    if not status:
        raise RuntimeError(
            "optimizer-owned torrent is absent"
        )

    labels = _deluge_rpc(
        "label.get_labels",
        []
    ) or []

    if label not in {
        str(x)
        for x in labels
    }:
        _deluge_rpc(
            "label.add",
            [label]
        )

    _deluge_rpc(
        "label.set_torrent",
        [
            download_id,
            label,
        ],
    )

    check = deluge_torrent_status(
        download_id
    )

    if str(
        check.get("label") or ""
    ) != label:
        raise RuntimeError(
            "Deluge label verification failed"
        )

    return True


def deluge_remove_exact_torrent(
    download_id,
):
    """
    Remove one exact optimizer-owned Deluge torrent and its download payload.

    Called ONLY after Arr import success is independently proven.
    Library files are never addressed here.
    """
    download_id = str(
        download_id or ""
    ).strip().lower()

    if not download_id:
        raise RuntimeError(
            "exact Deluge hash required"
        )

    status = deluge_torrent_status(
        download_id
    )

    if not status:
        return "absent"

    _deluge_rpc(
        "core.remove_torrent",
        [
            download_id,
            True,
        ],
    )

    check = deluge_torrent_status(
        download_id
    )

    if check:
        raise RuntimeError(
            "Deluge torrent still exists after exact removal"
        )

    return "removed"


def _all_arr_history_records(app):
    getter = (
        radarr_get
        if app == "radarr"
        else sonarr_get
    )

    records = []
    page = 1
    page_size = 250

    while page <= 50:
        data = getter(
            "/history?page=%d&pageSize=%d"
            "&sortKey=date&sortDirection=descending"
            % (
                page,
                page_size,
            )
        ) or {}

        batch = data.get("records") or []
        records.extend(batch)

        try:
            total = int(
                data.get("totalRecords")
                or len(records)
            )
        except (TypeError, ValueError):
            total = len(records)

        if (
            not batch
            or len(records) >= total
            or len(batch) < page_size
        ):
            break

        page += 1

    return records


def _tracker_import_proven(
    app,
    media_id,
    download_id,
):
    media_id = int(
        media_id or 0
    )

    wanted = str(
        download_id or ""
    ).strip().upper()

    if not media_id or not wanted:
        return False

    id_field = (
        "movieId"
        if app == "radarr"
        else "episodeId"
    )

    for event in _all_arr_history_records(
        app
    ):
        if str(
            event.get("eventType") or ""
        ) != "downloadFolderImported":
            continue

        if int(
            event.get(id_field) or 0
        ) != media_id:
            continue

        data = event.get("data") or {}

        event_download = str(
            event.get("downloadId")
            or data.get("downloadId")
            or ""
        ).strip().upper()

        if event_download == wanted:
            return True

    return False


def _ensure_radarr_tracker_jobs(
    state,
):
    """
    Backfill old pending Radarr grabs created before tracker metadata existed.

    Ownership still comes exclusively from the exact persisted download hash.
    """
    changed = False

    pending = state.get(
        "pending_replacements"
    ) or {}

    jobs = state.setdefault(
        "tracker_jobs",
        {}
    )

    for key, txn in pending.items():
        job = jobs.get(str(key))

        if not isinstance(job, dict):
            download_id = str(
                (txn or {}).get(
                    "download_id"
                )
                or ""
            ).strip()

            if not download_id:
                continue

            try:
                status = deluge_torrent_status(
                    download_id
                )
            except Exception:
                continue

            policy = (
                tracker_policy_from_deluge_status(
                    status
                )
            )

            if not policy:
                continue

            movie_id = int(
                (txn or {}).get(
                    "movie_id"
                )
                or key
            )

            jobs[str(key)] = {
                "media_type": "radarr",
                "media_id": movie_id,
                "approved_title": str(
                    (txn or {}).get(
                        "approved_title"
                    )
                    or ""
                ),
                "source_indexer": "",
                "tracker_policy": policy,
                "policy_source":
                    "deluge_trackers",
                "desired_label": (
                    "torrentleech-movies"
                    if policy == "keep_seed"
                    else ""
                ),
                "download_id": download_id,
                "queue_id": int(
                    (txn or {}).get(
                        "queue_id"
                    )
                    or 0
                ),
                "created": int(
                    (txn or {}).get(
                        "created"
                    )
                    or time.time()
                ),
                "status": "grabbed",
            }

            changed = True
            continue

        txn_download = str(
            (txn or {}).get(
                "download_id"
            )
            or ""
        ).strip()

        if (
            txn_download
            and not str(
                job.get("download_id")
                or ""
            ).strip()
        ):
            job["download_id"] = txn_download
            job["queue_id"] = int(
                (txn or {}).get(
                    "queue_id"
                )
                or 0
            )
            job["bound_at"] = int(
                time.time()
            )
            changed = True

    return changed


def _bind_sonarr_tracker_job(job):
    episode_id = int(
        job.get("media_id") or 0
    )

    if not episode_id:
        return False

    data = sonarr_get(
        "/queue?page=1&pageSize=1000"
        "&includeUnknownSeriesItems=true"
    ) or {}

    preexisting = {
        int(x)
        for x in (
            job.get(
                "pre_grab_queue_ids"
            )
            or []
        )
        if str(x).isdigit()
    }

    rows = [
        row
        for row in (
            data.get("records") or []
        )
        if int(
            row.get("episodeId") or 0
        ) == episode_id
        and int(
            row.get("id") or 0
        ) > 0
        and int(
            row.get("id") or 0
        ) not in preexisting
    ]

    if len(rows) != 1:
        return False

    row = rows[0]

    download_id = str(
        row.get("downloadId") or ""
    ).strip()

    queue_id = int(
        row.get("id") or 0
    )

    if not download_id or not queue_id:
        return False

    job["download_id"] = download_id
    job["queue_id"] = queue_id
    job["bound_at"] = int(
        time.time()
    )

    return True


def _process_tracker_jobs(
    app,
    state,
):
    jobs = state.setdefault(
        "tracker_jobs",
        {}
    )

    changed = False

    if app == "radarr":
        if _ensure_radarr_tracker_jobs(
            state
        ):
            changed = True

    pending = (
        state.get(
            "pending_replacements"
        )
        or {}
    )

    for key, job in list(
        jobs.items()
    ):
        try:
            media_id = int(
                job.get("media_id")
                or key
            )

            download_id = str(
                job.get("download_id")
                or ""
            ).strip()

            if (
                app == "sonarr"
                and not download_id
            ):
                if _bind_sonarr_tracker_job(
                    job
                ):
                    changed = True
                    download_id = str(
                        job.get(
                            "download_id"
                        )
                        or ""
                    ).strip()

            if not download_id:
                continue

            try:
                status = deluge_torrent_status(
                    download_id
                )
            except Exception as exc:
                job["last_error"] = str(
                    exc
                )
                continue

            policy = str(
                job.get("tracker_policy")
                or ""
            ).strip()

            if not policy and status:
                policy = (
                    tracker_policy_from_deluge_status(
                        status
                    )
                )

                if policy:
                    job[
                        "tracker_policy"
                    ] = policy
                    job[
                        "policy_source"
                    ] = "deluge_trackers"
                    job[
                        "desired_label"
                    ] = (
                        "torrentleech-movies"
                        if (
                            app == "radarr"
                            and policy
                            == "keep_seed"
                        )
                        else "torrentleech-tv"
                        if (
                            app == "sonarr"
                            and policy
                            == "keep_seed"
                        )
                        else ""
                    )
                    changed = True

            if policy == "keep_seed":
                desired = str(
                    job.get(
                        "desired_label"
                    )
                    or (
                        "torrentleech-movies"
                        if app == "radarr"
                        else "torrentleech-tv"
                    )
                )

                if status:
                    current_label = str(
                        status.get("label")
                        or ""
                    )

                    if current_label != desired:
                        deluge_set_exact_label(
                            download_id,
                            desired,
                        )
                        job[
                            "labeled_at"
                        ] = int(
                            time.time()
                        )
                        changed = True

            imported = (
                _tracker_import_proven(
                    app,
                    media_id,
                    download_id,
                )
            )

            if not imported:
                continue

            if (
                app == "radarr"
                and str(media_id)
                in pending
            ):
                # Radarr import worker has not yet proven the final
                # one-clean-movie invariant / self-heal completion.
                continue

            if app == "sonarr":
                episode = sonarr_get(
                    "/episode/%d"
                    % media_id
                ) or {}

                if not bool(
                    episode.get("hasFile")
                ):
                    continue

            if policy == "keep_seed":
                status = (
                    deluge_torrent_status(
                        download_id
                    )
                )

                if not status:
                    job[
                        "retention_issue"
                    ] = (
                        "TorrentLeech torrent "
                        "missing after verified "
                        "import"
                    )
                    changed = True
                    continue

                desired = str(
                    job.get(
                        "desired_label"
                    )
                    or (
                        "torrentleech-movies"
                        if app == "radarr"
                        else "torrentleech-tv"
                    )
                )

                if str(
                    status.get("label")
                    or ""
                ) != desired:
                    deluge_set_exact_label(
                        download_id,
                        desired,
                    )

                jobs.pop(
                    key,
                    None
                )
                changed = True

                print(
                    "[tracker-retention] KEEP SEEDING:",
                    app,
                    media_id,
                    download_id,
                    desired,
                )

            elif (
                policy
                == "remove_after_verified_success"
            ):
                result = (
                    deluge_remove_exact_torrent(
                        download_id
                    )
                )

                jobs.pop(
                    key,
                    None
                )
                changed = True

                print(
                    "[tracker-retention] CLEANED:",
                    app,
                    media_id,
                    download_id,
                    result,
                )

        except Exception as exc:
            job["last_error"] = str(
                exc
            )

            print(
                "[tracker-retention] job error %s/%s: %s"
                % (
                    app,
                    key,
                    exc,
                )
            )

    return changed


def update_connection(app, scheme, host, port, api_key):
    if app not in ("radarr", "sonarr"): raise ValueError("Unknown app")
    scheme = scheme.lower().strip()
    host = host.strip().rstrip("/")
    if scheme not in ("http", "https"): raise ValueError("Scheme must be http or https")
    if not host or "/" in host: raise ValueError("Enter only the hostname or IP address")
    port = int(port)
    if not 1 <= port <= 65535: raise ValueError("Port must be 1-65535")
    current = connection(app)
    key = api_key.strip() or current["api_key"]
    if not key: raise ValueError("API key is required")
    data = load_connections()
    data[app] = {"scheme": scheme, "host": host, "port": port, "api_key": key}
    save_connections(data)

def test_connection(app, scheme=None, host=None, port=None, api_key=None):
    cfg = connection(app)
    scheme = (scheme or cfg["scheme"]).strip().lower()
    host = (host or cfg["host"]).strip().rstrip("/")
    port = int(port or cfg["port"])
    key = (api_key or "").strip() or cfg["api_key"]
    if not key: raise RuntimeError("API key is missing")
    url = "%s://%s:%d/api/v3/system/status" % (scheme, host, port)
    req = urllib.request.Request(url, headers={"X-Api-Key": key, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=8) as response:
        data = json.loads(response.read().decode("utf-8") or "{}")
    return str(data.get("version") or "connected")

def api_online(app):
    try:
        test_connection(app)
        return True
    except Exception:
        return False

def load_controls():
    try:
        with open(CONTROL_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def save_controls(data):
    os.makedirs(os.path.dirname(CONTROL_FILE), exist_ok=True)
    # CONTROL_FILE may be bind-mounted as a single file in Docker.
    # Replacing the inode fails with EBUSY on such mounts, so update it in place.
    with open(CONTROL_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())

def daily_search_budget(app):
    data = load_controls()
    c = data.get(app, {})
    default = RADARR_BASE_BUDGET if app == "radarr" else SONARR_BASE_BUDGET
    return int(c.get("daily_search_budget", default))

def update_daily_search_budget(app, budget):
    budget = int(budget)
    if not 1 <= budget <= 100000:
        raise ValueError("Daily search budget must be between 1 and 100000.")
    data = load_controls()
    c = data.setdefault(app, {})
    c["daily_search_budget"] = budget
    save_controls(data)

def app_controls(app):
    data = load_controls()
    c = data.get(app, {})
    today = time.strftime("%Y-%m-%d")
    return float(c.get("min_saving_percent", 0.0)), float(c.get("max_saving_percent", 0.0)), int((c.get("daily_extra") or {}).get(today, 0))

def update_saving_window(app, minimum, maximum):
    if not (0 <= minimum <= maximum <= 100):
        raise ValueError("Use 0-100%, and minimum cannot be greater than maximum.")
    data = load_controls(); c = data.setdefault(app, {})
    c["min_saving_percent"] = minimum; c["max_saving_percent"] = maximum
    save_controls(data)

def add_daily_extra(app, amount=50):
    data = load_controls(); c = data.setdefault(app, {}); extras = c.setdefault("daily_extra", {})
    today = time.strftime("%Y-%m-%d")
    extras[today] = int(extras.get(today, 0)) + amount
    # Old overrides are irrelevant; prune them so the file stays tiny.
    c["daily_extra"] = {today: extras[today]}
    save_controls(data)
    return extras[today]



# SMART RULES UI BACKEND START

RULE_DEFINITIONS = {
    "radarr": (
        (
            "Targets",
            (
                (
                    "storage_optimization",
                    "Storage optimization",
                    "Allow same-resolution replacements that reduce file size."
                ),
                (
                    "uhd_upgrade",
                    "UHD upgrade",
                    "Allow UHD-profile movies to upgrade from 1080p to 2160p."
                ),
            ),
        ),
        (
            "Preferences & compatibility",
            (
                (
                    "prefer_dynamic_range",
                    "Prefer DV / HDR",
                    "Rank DV+HDR above HDR, and HDR above SDR, among releases that already pass safety checks."
                ),
                (
                    "prefer_atmos",
                    "Prefer Atmos",
                    "Prefer an Atmos release when the candidate is otherwise safe."
                ),
                (
                    "prefer_audio_channels",
                    "Prefer more audio channels",
                    "Prefer the candidate with the higher audio channel count."
                ),
                (
                    "prefer_torrentleech",
                    "Prefer TorrentLeech",
                    "Prefer a valid TorrentLeech candidate over valid fallback indexers."
                ),
                (
                    "prefer_x265",
                    "Prefer x265 / HEVC",
                    "Use x265 / HEVC as a ranking preference after the higher-priority rules."
                ),
                (
                    "block_av1",
                    "Block AV1",
                    "Reject AV1 replacement releases."
                ),
            ),
        ),
    ),

    "sonarr": (
        (
            "Targets",
            (
                (
                    "storage_optimization",
                    "Storage optimization",
                    "Allow same-resolution episode replacements that reduce file size."
                ),
                (
                    "upgrade_720_to_1080",
                    "Upgrade 720p to 1080p",
                    "Allow the special 720p to 1080p upgrade path."
                ),
                (
                    "uhd_upgrade",
                    "UHD 1080p to 2160p",
                    "Allow UHD-profile episodes currently at 1080p to upgrade to 2160p."
                ),
            ),
        ),
        (
            "Preferences & compatibility",
            (
                (
                    "require_hdr_uhd",
                    "Require HDR for UHD",
                    "Require 2160p UHD candidates to advertise HDR or DV+HDR."
                ),
                (
                    "prefer_dynamic_range",
                    "Prefer DV / HDR",
                    "For UHD candidates, prefer DV+HDR over HDR and HDR over SDR."
                ),
                (
                    "prefer_atmos",
                    "Prefer Atmos",
                    "For UHD candidates, prefer Atmos when higher-priority rules are equal."
                ),
                (
                    "prefer_torrentleech",
                    "Prefer TorrentLeech",
                    "Use the valid TorrentLeech pool first when one exists."
                ),
                (
                    "prefer_x265",
                    "Prefer x265 / HEVC",
                    "Use x265 / HEVC as a ranking preference without overriding the size policy."
                ),
                (
                    "block_av1",
                    "Block AV1",
                    "Reject AV1 replacement releases."
                ),
            ),
        ),
    ),
}


def selectable_rule_keys(app):
    if app not in RULE_DEFINITIONS:
        raise ValueError("Unknown optimizer")

    keys = []

    for _section, rules in RULE_DEFINITIONS[app]:
        for key, _label, _description in rules:
            keys.append(key)

    return tuple(keys)


def selectable_rules(app):
    if app not in RULE_DEFINITIONS:
        raise ValueError("Unknown optimizer")

    data = load_controls()
    raw = (
        (data.get(app, {}) or {}).get("rules")
        or {}
    )

    # Intentionally OFF when no saved value exists.
    # This is the fresh-install default.
    return {
        key: bool(raw.get(key, False))
        for key in selectable_rule_keys(app)
    }


def update_selectable_rule(app, key, enabled):
    if app not in RULE_DEFINITIONS:
        raise ValueError("Unknown optimizer")

    if key not in selectable_rule_keys(app):
        raise ValueError("Unknown rule")

    data = load_controls()
    config = data.setdefault(app, {})
    rules = config.setdefault("rules", {})

    rules[key] = bool(enabled)
    config["rules_version"] = 1

    save_controls(data)


def rules_summary(app):
    rules = selectable_rules(app)

    return (
        sum(1 for enabled in rules.values() if enabled),
        len(rules),
    )




# SMART RESOLUTION POLICY UI BACKEND START

RESOLUTION_PATH_KEYS = (
    "720_to_1080",
    "720_to_2160",
    "1080_to_2160",
)

RESOLUTION_LIMIT_KEYS = (
    "minimum_current_size",
    "maximum_1080_size",
    "maximum_2160_size",
)


def resolution_policy_defaults(app):
    if app == "radarr":
        minimum = {
            "enabled": False,
            "value": 5.0,
            "unit": "GiB",
        }
    elif app == "sonarr":
        minimum = {
            "enabled": False,
            "value": 400.0,
            "unit": "MiB",
        }
    else:
        raise ValueError("Unknown app")

    return {
        "paths": {
            "720_to_1080": False,
            "720_to_2160": False,
            "1080_to_2160": False,
        },

        "limits": {
            "minimum_current_size": minimum,

            "maximum_1080_size": {
                "enabled": False,
                "value": 10.0,
                "unit": "GiB",
            },

            "maximum_2160_size": {
                "enabled": False,
                "value": 20.0,
                "unit": "GiB",
            },
        },

        "upgrade_growth": {
            "720_to_1080": {
                "enabled": False,
                "min_percent": 0.0,
                "max_percent": 40.0,
            },

            "720_to_2160": {
                "enabled": False,
                "min_percent": 0.0,
                "max_percent": 100.0,
            },

            "1080_to_2160": {
                "enabled": False,
                "min_percent": 0.0,
                "max_percent": 0.0,
            },
        },
    }


def resolution_policy_settings(app):
    defaults = resolution_policy_defaults(app)

    data = load_controls()
    config = data.get(app, {}) or {}
    raw = config.get("resolution_policy", {}) or {}

    raw_paths = raw.get("paths", {}) or {}
    raw_limits = raw.get("limits", {}) or {}
    raw_growth = raw.get("upgrade_growth", {}) or {}

    out = resolution_policy_defaults(app)

    for key in RESOLUTION_PATH_KEYS:
        out["paths"][key] = bool(
            raw_paths.get(
                key,
                defaults["paths"][key]
            )
        )

    for key in RESOLUTION_LIMIT_KEYS:
        source = raw_limits.get(key, {}) or {}
        default = defaults["limits"][key]

        try:
            value = float(
                source.get(
                    "value",
                    default["value"]
                )
            )
        except (TypeError, ValueError):
            value = float(default["value"])

        unit = str(
            source.get(
                "unit",
                default["unit"]
            )
        )

        if unit not in ("MiB", "GiB"):
            unit = default["unit"]

        out["limits"][key] = {
            "enabled": bool(
                source.get(
                    "enabled",
                    default["enabled"]
                )
            ),
            "value": value,
            "unit": unit,
        }

    for key in RESOLUTION_PATH_KEYS:
        source = raw_growth.get(key, {}) or {}
        default = defaults["upgrade_growth"][key]

        try:
            minimum = float(
                source.get(
                    "min_percent",
                    default["min_percent"]
                )
            )
            maximum = float(
                source.get(
                    "max_percent",
                    default["max_percent"]
                )
            )
        except (TypeError, ValueError):
            minimum = default["min_percent"]
            maximum = default["max_percent"]

        out["upgrade_growth"][key] = {
            "enabled": bool(
                source.get(
                    "enabled",
                    default["enabled"]
                )
            ),
            "min_percent": minimum,
            "max_percent": maximum,
        }

    return out


def update_resolution_path(app, key, enabled):
    if app not in ("radarr", "sonarr"):
        raise ValueError("Unknown app")

    if key not in RESOLUTION_PATH_KEYS:
        raise ValueError("Unknown resolution path")

    data = load_controls()
    config = data.setdefault(app, {})
    policy = config.setdefault("resolution_policy", {})

    paths = policy.setdefault("paths", {})
    growth = policy.setdefault("upgrade_growth", {})

    enabled = bool(enabled)

    paths[key] = enabled

    entry = growth.setdefault(key, {})
    entry["enabled"] = enabled

    save_controls(data)


def update_resolution_limit(
    app,
    key,
    enabled,
    value,
    unit,
):
    if app not in ("radarr", "sonarr"):
        raise ValueError("Unknown app")

    if key not in RESOLUTION_LIMIT_KEYS:
        raise ValueError("Unknown size limit")

    value = float(value)

    if value <= 0 or value > 1000000:
        raise ValueError(
            "Size value must be greater than 0."
        )

    if unit not in ("MiB", "GiB"):
        raise ValueError(
            "Unit must be MiB or GiB."
        )

    data = load_controls()
    config = data.setdefault(app, {})
    policy = config.setdefault("resolution_policy", {})
    limits = policy.setdefault("limits", {})

    limits[key] = {
        "enabled": bool(enabled),
        "value": value,
        "unit": unit,
    }

    save_controls(data)


def update_upgrade_growth(
    app,
    key,
    minimum,
    maximum,
):
    if app not in ("radarr", "sonarr"):
        raise ValueError("Unknown app")

    if key not in RESOLUTION_PATH_KEYS:
        raise ValueError("Unknown upgrade path")

    minimum = float(minimum)
    maximum = float(maximum)

    if not (
        0 <= minimum <= maximum <= 10000
    ):
        raise ValueError(
            "Growth must be 0-10000%, "
            "with minimum <= maximum."
        )

    data = load_controls()
    config = data.setdefault(app, {})
    policy = config.setdefault("resolution_policy", {})
    growth = policy.setdefault("upgrade_growth", {})

    paths = policy.setdefault("paths", {})

    entry = growth.setdefault(key, {})
    entry["min_percent"] = minimum
    entry["max_percent"] = maximum

    # Growth follows the path toggle.
    entry["enabled"] = bool(
        paths.get(key, False)
    )

    save_controls(data)




# SMART ADVANCED PREFERENCES V2 UI BACKEND START

ADVANCED_BOOL_KEYS = (
    "prefer_remux",
    "prefer_bluray",
    "prefer_webdl",
    "prefer_webrip",
    "prefer_hdtv",
    "prefer_hdr10plus",
    "prefer_10bit",
    "prefer_dtsx",
    "prefer_lossless_audio",
    "prefer_eac3",
    "prefer_proper_repack",
    "prefer_freeleech",
    "prefer_smaller",
    "prefer_seeders",
)

ADVANCED_CODEC_VALUES = (
    "none",
    "x265",
    "x264",
    "av1",
)


def advanced_preferences(app):

    if app not in (
        "radarr",
        "sonarr",
    ):
        raise ValueError(
            "Unknown optimizer"
        )

    defaults = {
        key: False
        for key in ADVANCED_BOOL_KEYS
    }

    defaults["codec_preference"] = "none"
    defaults["indexer_priority"] = []

    data = load_controls()

    raw = (
        (
            data.get(app, {})
            or {}
        ).get(
            "advanced_preferences",
            {}
        )
        or {}
    )

    result = dict(defaults)

    for key in ADVANCED_BOOL_KEYS:
        result[key] = bool(
            raw.get(
                key,
                defaults[key]
            )
        )

    codec = str(
        raw.get(
            "codec_preference",
            "none"
        )
        or "none"
    ).lower()

    if codec not in ADVANCED_CODEC_VALUES:
        codec = "none"

    result["codec_preference"] = codec

    names = (
        raw.get(
            "indexer_priority"
        )
        or []
    )

    if not isinstance(
        names,
        list
    ):
        names = []

    cleaned = []

    for name in names:

        value = str(
            name
            or ""
        ).strip()

        if (
            value
            and value not in cleaned
        ):
            cleaned.append(
                value[:200]
            )

    result["indexer_priority"] = cleaned

    return result


def update_advanced_preferences(
    app,
    values,
):

    if not isinstance(
        values,
        dict
    ):
        raise ValueError(
            "Invalid preference payload"
        )

    current = advanced_preferences(
        app
    )

    for key in ADVANCED_BOOL_KEYS:

        if key in values:
            current[key] = bool(
                values[key]
            )

    if "codec_preference" in values:

        codec = str(
            values.get(
                "codec_preference"
            )
            or "none"
        ).lower()

        if codec not in ADVANCED_CODEC_VALUES:
            raise ValueError(
                "Unknown codec preference"
            )

        current["codec_preference"] = codec

    if "indexer_priority" in values:

        incoming = (
            values.get(
                "indexer_priority"
            )
            or []
        )

        if not isinstance(
            incoming,
            list
        ):
            raise ValueError(
                "Indexer priority must be a list"
            )

        cleaned = []

        for name in incoming:

            value = str(
                name
                or ""
            ).strip()

            if (
                value
                and value not in cleaned
            ):
                cleaned.append(
                    value[:200]
                )

        current[
            "indexer_priority"
        ] = cleaned

    data = load_controls()

    config = data.setdefault(
        app,
        {}
    )

    config[
        "advanced_preferences"
    ] = current

    save_controls(
        data
    )


def configured_indexers(app):

    if app == "radarr":
        getter = radarr_get

    elif app == "sonarr":
        getter = sonarr_get

    else:
        raise ValueError(
            "Unknown optimizer"
        )

    try:

        raw = (
            getter(
                "/indexer"
            )
            or []
        )

    except Exception as exc:

        return (
            [],
            str(exc)
        )

    if isinstance(
        raw,
        dict
    ):

        raw = (
            raw.get("records")
            or raw.get("indexers")
            or []
        )

    names = []

    for item in raw:

        if not isinstance(
            item,
            dict
        ):
            continue

        name = str(
            item.get("name")
            or ""
        ).strip()

        if (
            name
            and name not in names
        ):
            names.append(name)

    names.sort(
        key=lambda value:
            value.lower()
    )

    return (
        names,
        None
    )


# SMART ADVANCED PREFERENCES V2 UI BACKEND END


# SMART RESOLUTION POLICY UI BACKEND END


# SMART RULES UI BACKEND END


# SMART RULES LOCKED DEFINITIONS START

RULE_LOCKED = {

    "radarr": (

        (
            "Existing file required",
            "Only movies that already have a Radarr movie file can be optimized."
        ),

        (
            "Active-download protection",
            "A movie already represented in the download queue is skipped before searching."
        ),

        (
            "Automatic one-shot search",
            "Each movie receives at most one automatic optimizer release search. Explicit Manual Optimizer retries may bypass that one-shot history."
        ),

        (
            "Persistent A-Z queue",
            "Automatic work follows the saved alphabetical movie queue. Newly downloaded movies are appended to the bottom."
        ),

        (
            "Optimizer exclusions",
            "Movies on the Smart Optimizer exclusion list are skipped before any interactive release search."
        ),

        (
            "Unambiguous movie-folder guard",
            "The movie folder must contain exactly one registered root movie file and exactly one physical root video with the same filename."
        ),

        (
            "Minimum current movie size",
            "Current movie files below 5 GiB are not automatic optimization targets."
        ),

        (
            "Supported source resolutions",
            "Radarr evaluates current 720p, 1080p or 2160p movie files; 720p participates only through an enabled upgrade path."
        ),

        (
            "Dangerous-release protection",
            "Releases matching dangerous or unsafe payload patterns are rejected."
        ),

        (
            "Radarr rejection protection",
            "Candidate releases rejected by Radarr's applicable safety checks are not selected."
        ),

        (
            "Movie identity protection",
            "A candidate must match the intended movie rather than merely looking attractive by size or quality."
        ),

        (
            "Edition / cut protection",
            "An existing Extended, Limited, Special, Director's Cut or protected edition cannot be silently replaced by an ordinary theatrical release."
        ),

        (
            "Release-attempt cooldown",
            "The same attempted release is not deliberately grabbed again during the optimizer cooldown period."
        ),

        (
            "Seeder requirement",
            "Seeder information must be usable and the release must satisfy the optimizer minimum-seeder requirement."
        ),

        (
            "No resolution downgrade",
            "A replacement may never lower the current movie resolution."
        ),

        (
            "2160p upgrade profile guard",
            "A 2160p movie upgrade requires the UHD profile and an enabled 2160p upgrade path."
        ),

        (
            "Candidate size required",
            "A replacement with missing, zero or invalid file size is rejected."
        ),

        (
            "Same-resolution shrink protection",
            "Same-resolution replacements must be smaller. Enabled resolution upgrades may grow only within their configured Growth range."
        ),

        (
            "1080p replacement ceiling",
            "A 1080p replacement may not exceed 10 GiB."
        ),

        (
            "Configured saving window",
            "Accepted downsizes must remain inside the configured minimum and maximum saving percentages."
        ),

        (
            "Dolby Vision fallback protection",
            "Dolby Vision-only releases without HDR fallback are rejected."
        ),

        (
            "Existing HDR / DV protection",
            "A replacement cannot discard protected dynamic-range capability already present in the current movie."
        ),

        (
            "Existing Atmos protection",
            "A replacement cannot discard Atmos already present in the current movie."
        ),

        (
            "Existing audio-channel protection",
            "A replacement cannot reduce the protected audio channel count of the current movie."
        ),

        (
            "Final import / file validation",
            "Optimizer replacement tracking and import handling remain guarded so the current media is not treated as safely replaced until the expected Arr workflow is verified."
        ),

        (
            "Tracker cleanup protection",
            "Torrent cleanup follows the selected indexer policy and verified-import state; unknown tracker state is not automatically deleted."
        ),

        (
            "Daily search budget",
            "Automatic optimizer work remains limited by the configured daily interactive-search allowance."
        ),
    ),


    "sonarr": (

        (
            "Existing episode file required",
            "Only episodes that already have a Sonarr episode file can be optimized."
        ),

        (
            "Active-download protection",
            "Episodes already represented in the Sonarr download queue are skipped before searching."
        ),

        (
            "Automatic one-shot search",
            "Each episode receives at most one automatic optimizer release search. Explicit Manual Optimizer retries may bypass that one-shot history."
        ),

        (
            "Persistent A-Z series queue",
            "Automatic work follows the saved alphabetical series queue and processes episode files from each series in order."
        ),

        (
            "Series-wide optimizer exclusions",
            "A Sonarr series on the Smart Optimizer exclusion list is skipped before interactive release searches."
        ),

        (
            "Supported quality profiles",
            "Automatic Sonarr optimization only targets the configured Normal and UHD optimizer quality profiles."
        ),

        (
            "Minimum current episode size",
            "Current episode files below 400 MiB are not automatic optimization targets."
        ),

        (
            "Dangerous-release protection",
            "Releases matching dangerous or unsafe payload patterns are rejected."
        ),

        (
            "Sonarr rejection protection",
            "Sonarr release rejections are respected except the specific existing-file cutoff/upgrade notices the optimizer intentionally evaluates beyond."
        ),

        (
            "Single-episode protection",
            "Season packs and multi-episode releases are rejected; Smart Optimizer compares one episode file with one replacement file."
        ),

        (
            "Release-attempt cooldown",
            "The same attempted release is not deliberately grabbed again during the optimizer cooldown period."
        ),

        (
            "Seeder requirement",
            "Seeder information must be usable and the release must satisfy the optimizer minimum-seeder requirement."
        ),

        (
            "720p same-resolution protection",
            "A current 720p episode is not replaced at 720p; it can move only through an enabled 720p upgrade path."
        ),

        (
            "Other sub-1080 protection",
            "Current resolutions below 1080p that are not 720p are left untouched."
        ),

        (
            "2160p upgrade profile guard",
            "A 2160p upgrade requires the UHD optimizer profile; normal-profile 720p episodes may still use an enabled 720p-to-1080p path."
        ),

        (
            "UHD-profile resolution guard",
            "UHD-profile replacement candidates must be 2160p."
        ),

        (
            "Dolby Vision fallback protection",
            "Dolby Vision-only releases without HDR fallback are rejected."
        ),

        (
            "720p-to-1080p growth ceiling",
            "The special 720p-to-1080p quality upgrade may grow by at most 40 percent."
        ),

        (
            "Same-resolution shrink protection",
            "Same-resolution replacements must be smaller. Enabled resolution upgrades may grow only within their configured Growth range."
        ),

        (
            "Configured minimum saving",
            "Ordinary downsizes must satisfy the configured minimum saving percentage."
        ),

        (
            "Hard 40 percent downsize ceiling",
            "Sonarr Smart Optimizer will never downsize an episode by more than 40 percent even if the configured maximum is higher."
        ),

        (
            "Per-series search protection",
            "One series cannot consume the whole automatic run; the optimizer enforces its per-series search limit."
        ),

        (
            "Final import / file validation",
            "Replacement tracking and import handling remain guarded until Sonarr's expected file workflow is verified."
        ),

        (
            "Tracker cleanup protection",
            "Torrent cleanup follows verified import state and tracker policy; unresolved tracker state is never blindly removed."
        ),

        (
            "Daily search budget",
            "Automatic optimizer work remains limited by the configured daily interactive-search allowance."
        ),
    ),
}

# SMART RULES LOCKED DEFINITIONS END




# SMART FLEXIBLE RULES PAGE START


def rules_page(app):

    if app not in (
        "radarr",
        "sonarr",
    ):
        raise ValueError(
            "Unknown optimizer"
        )


    title = (
        "Radarr"
        if app == "radarr"
        else "Sonarr"
    )

    accent = (
        "#ff7048"
        if app == "radarr"
        else "#28c5ff"
    )

    accent_rgb = (
        "255,112,72"
        if app == "radarr"
        else "40,197,255"
    )

    background = (
        "/radarr-background.png"
        if app == "radarr"
        else "/sonarr-background.png"
    )

    icon = (
        "/radarr-icon.png"
        if app == "radarr"
        else "/sonarr-icon.png"
    )

    dashboard = (
        "/radarr"
        if app == "radarr"
        else "/sonarr"
    )


    rules = selectable_rules(
        app
    )

    policy = resolution_policy_settings(
        app
    )

    # SMART RULES V2 PAGE DATA START

    advanced = advanced_preferences(
        app
    )

    available_indexers, indexer_error = (
        configured_indexers(
            app
        )
    )

    # SMART RULES V2 PAGE DATA END

    down_min, down_max, _extra = (
        app_controls(app)
    )


    # These old target toggles have been replaced by the
    # generalized resolution-path controls below.
    obsolete_target_keys = {
        "uhd_upgrade",
        "upgrade_720_to_1080",

        # Superseded by RULES V2 generalized controls.
        "prefer_torrentleech",
        "prefer_x265",
    }


    selectable_html = []


    for section_name, entries in RULE_DEFINITIONS[app]:

        cards = []

        for key, label, description in entries:

            if key in obsolete_target_keys:
                continue

            enabled = bool(
                rules.get(
                    key,
                    False
                )
            )

            cards.append(
                """
<div class="rule-card">
  <div class="rule-copy">
    <div class="rule-title">%s</div>
    <div class="rule-description">%s</div>
  </div>

  <div class="rule-control">
    <span class="state-pill">%s</span>

    <label class="switch">
      <input
        class="rule-toggle"
        type="checkbox"
        data-rule="%s"
        %s
      >
      <span class="slider"></span>
    </label>
  </div>
</div>
"""
                % (
                    html.escape(label),
                    html.escape(description),
                    "ON" if enabled else "OFF",
                    html.escape(
                        key,
                        quote=True
                    ),
                    "checked"
                    if enabled
                    else "",
                )
            )


        if cards:

            selectable_html.append(
                """
<section class="rules-section">
  <div class="section-heading">
    <div>
      <h2>%s</h2>
      <p>Changes apply to the optimizer runtime policy.</p>
    </div>
  </div>

  <div class="rule-list">
    %s
  </div>
</section>
"""
                % (
                    html.escape(
                        section_name
                    ),
                    "".join(cards),
                )
            )


    # ========================================================
    # RESOLUTION PATHS
    # ========================================================

    path_labels = {
        "720_to_1080":
            (
                "720p → 1080p",
                "Allow an existing 720p file to upgrade to 1080p."
            ),

        "720_to_2160":
            (
                "720p → 2160p",
                "Allow an existing 720p file to upgrade directly to 2160p on the UHD profile."
            ),

        "1080_to_2160":
            (
                "1080p → 2160p",
                "Allow an existing 1080p file to upgrade to 2160p on the UHD profile."
            ),
    }


    path_cards = []

    for key in RESOLUTION_PATH_KEYS:

        label, description = (
            path_labels[key]
        )

        enabled = bool(
            policy["paths"].get(
                key,
                False
            )
        )

        growth = (
            policy["upgrade_growth"]
            [key]
        )

        path_cards.append(
            """
<div class="rule-card path-card">

  <div class="rule-copy">

    <div class="rule-title">%s</div>

    <div class="rule-description">
      %s
    </div>

    <div class="growth-controls">

      <span class="growth-label">
        Growth
      </span>

      <input
        class="growth-value"
        type="number"
        min="0"
        max="10000"
        step="0.1"
        data-growth-min="%s"
        value="%.1f"
        aria-label="Minimum growth percent"
      >

      <span class="growth-separator">
        –
      </span>

      <input
        class="growth-value"
        type="number"
        min="0"
        max="10000"
        step="0.1"
        data-growth-max="%s"
        value="%.1f"
        aria-label="Maximum growth percent"
      >

      <span class="growth-percent">
        %%
      </span>

      <button
        class="growth-save"
        type="button"
        data-growth-save="%s"
      >
        Save growth
      </button>

    </div>

  </div>


  <div class="rule-control">

    <span class="state-pill">
      %s
    </span>

    <label class="switch">

      <input
        class="path-toggle"
        type="checkbox"
        data-path="%s"
        %s
      >

      <span class="slider"></span>

    </label>

  </div>

</div>
"""
            % (
                html.escape(label),
                html.escape(description),

                html.escape(
                    key,
                    quote=True
                ),

                float(
                    growth.get(
                        "min_percent",
                        0
                    )
                ),

                html.escape(
                    key,
                    quote=True
                ),

                float(
                    growth.get(
                        "max_percent",
                        0
                    )
                ),

                html.escape(
                    key,
                    quote=True
                ),

                "ON"
                if enabled
                else "OFF",

                html.escape(
                    key,
                    quote=True
                ),

                "checked"
                if enabled
                else "",
            )
        )



    # ========================================================
    # SIZE LIMITS
    # ========================================================

    limit_labels = {

        "minimum_current_size":
            (
                "Minimum current file size",
                "Skip optimization when the existing file is smaller than this value."
            ),

        "maximum_1080_size":
            (
                "Maximum 1080p replacement size",
                "Optional absolute ceiling for any accepted 1080p replacement."
            ),

        "maximum_2160_size":
            (
                "Maximum 2160p / 4K replacement size",
                "Optional absolute ceiling for any accepted 2160p replacement."
            ),
    }


    limit_cards = []

    for key in RESOLUTION_LIMIT_KEYS:

        label, description = (
            limit_labels[key]
        )

        setting = (
            policy["limits"][key]
        )

        enabled = bool(
            setting.get(
                "enabled",
                False
            )
        )

        value = float(
            setting.get(
                "value",
                0
            )
        )

        unit = str(
            setting.get(
                "unit",
                "MiB"
            )
        )


        limit_cards.append(
            """
<div class="rule-card limit-card">

  <div class="rule-copy">
    <div class="rule-title">%s</div>
    <div class="rule-description">%s</div>
  </div>

  <div class="limit-controls">

    <label class="switch">
      <input
        class="limit-toggle"
        type="checkbox"
        data-limit="%s"
        %s
      >
      <span class="slider"></span>
    </label>

    <input
      class="limit-value"
      data-limit-value="%s"
      type="number"
      min="0.01"
      step="0.01"
      value="%s"
    >

    <select
      class="limit-unit"
      data-limit-unit="%s"
    >
      <option value="MiB"%s>MiB</option>
      <option value="GiB"%s>GiB</option>
    </select>

    <button
      class="limit-save"
      type="button"
      data-limit-save="%s"
    >
      Save
    </button>

  </div>
</div>
"""
            % (
                html.escape(label),
                html.escape(description),

                html.escape(
                    key,
                    quote=True
                ),

                "checked"
                if enabled
                else "",

                html.escape(
                    key,
                    quote=True
                ),

                (
                    "%.2f" % value
                ).rstrip("0").rstrip("."),

                html.escape(
                    key,
                    quote=True
                ),

                " selected"
                if unit == "MiB"
                else "",

                " selected"
                if unit == "GiB"
                else "",

                html.escape(
                    key,
                    quote=True
                ),
            )
        )



    # SMART RULES V2 ADVANCED CARDS START

    def advanced_toggle_card(
        key,
        label,
        description,
    ):

        enabled = bool(
            advanced.get(
                key,
                False
            )
        )

        return """
<div class="rule-card">

  <div class="rule-copy">
    <div class="rule-title">%s</div>

    <div class="rule-description">
      %s
    </div>
  </div>

  <div class="rule-control">

    <span class="state-pill">
      %s
    </span>

    <label class="switch">

      <input
        class="advanced-toggle"
        type="checkbox"
        data-advanced-key="%s"
        %s
      >

      <span class="slider"></span>

    </label>

  </div>

</div>
""" % (
            html.escape(label),
            html.escape(description),
            "ON" if enabled else "OFF",
            html.escape(
                key,
                quote=True
            ),
            "checked"
            if enabled
            else "",
        )


    advanced_sections = []


    groups = (

        (
            "Source priority",
            (
                (
                    "prefer_remux",
                    "Prefer Remux",
                    "Prefer a valid Remux over lower-priority source types. Size and safety rules still apply."
                ),

                (
                    "prefer_bluray",
                    "Prefer BluRay",
                    "Prefer valid BluRay-sourced releases after Remux."
                ),

                (
                    "prefer_webdl",
                    "Prefer WEB-DL",
                    "Prefer valid WEB-DL releases over lower-priority source types."
                ),

                (
                    "prefer_webrip",
                    "Prefer WEBRip",
                    "Give WEBRip a ranking preference when enabled."
                ),

                (
                    "prefer_hdtv",
                    "Prefer HDTV",
                    "Give HDTV releases a ranking preference when enabled."
                ),
            )
        ),

        (
            "Video / HDR",
            (
                (
                    "prefer_hdr10plus",
                    "Prefer HDR10+",
                    "Prefer HDR10+ among releases that already pass dynamic-range protection."
                ),

                (
                    "prefer_10bit",
                    "Prefer 10-bit video",
                    "Prefer releases that advertise 10-bit video when otherwise safe."
                ),
            )
        ),

        (
            "Audio",
            (
                (
                    "prefer_dtsx",
                    "Prefer DTS:X",
                    "Prefer DTS:X audio when the candidate already passes audio protection."
                ),

                (
                    "prefer_lossless_audio",
                    "Prefer lossless audio",
                    "Prefer TrueHD or DTS-HD MA when otherwise safe."
                ),

                (
                    "prefer_eac3",
                    "Prefer E-AC-3 / DD+",
                    "Prefer E-AC-3 / Dolby Digital Plus when otherwise safe."
                ),
            )
        ),

        (
            "Release ranking",
            (
                (
                    "prefer_proper_repack",
                    "Prefer PROPER / REPACK",
                    "Prefer corrected PROPER or REPACK releases when otherwise valid."
                ),

                (
                    "prefer_freeleech",
                    "Prefer Freeleech",
                    "Prefer releases reported by the indexer with zero download-volume factor."
                ),

                (
                    "prefer_smaller",
                    "Prefer smaller file on a tie",
                    "Use smaller size as a ranking preference after higher-priority enabled preferences."
                ),

                (
                    "prefer_seeders",
                    "Prefer more seeders on a tie",
                    "Use seeder count as a late ranking preference."
                ),
            )
        ),
    )


    for heading, entries in groups:

        cards = "".join(
            advanced_toggle_card(
                key,
                label,
                description
            )
            for (
                key,
                label,
                description
            )
            in entries
        )

        advanced_sections.append(
            """
<div style="margin-top:18px">

  <h3 style="margin:0 0 10px">
    %s
  </h3>

  <div class="rule-list">
    %s
  </div>

</div>
"""
            % (
                html.escape(heading),
                cards,
            )
        )


    codec = str(
        advanced.get(
            "codec_preference",
            "none"
        )
    )


    codec_options = []

    for value, label in (
        (
            "none",
            "No codec preference"
        ),
        (
            "x265",
            "x265 / HEVC"
        ),
        (
            "x264",
            "x264 / AVC"
        ),
        (
            "av1",
            "AV1"
        ),
    ):

        codec_options.append(
            '<option value="%s"%s>%s</option>'
            % (
                value,

                " selected"
                if codec == value
                else "",

                label,
            )
        )


    codec_html = """
<div style="margin-top:18px">

  <h3 style="margin:0 0 10px">
    Codec
  </h3>

  <div class="rule-card">

    <div class="rule-copy">

      <div class="rule-title">
        Preferred video codec
      </div>

      <div class="rule-description">
        Ranking preference only. Block AV1 still overrides
        an AV1 preference when Block AV1 is enabled.
      </div>

    </div>

    <div class="rule-control">

      <select
        id="advanced-codec"
        style="
          min-height:40px;
          min-width:190px;
          padding:0 10px;
          border-radius:9px;
          background:#071522;
          color:#eef7ff;
          border:1px solid rgba(255,255,255,.16)
        "
      >
        %s
      </select>

    </div>

  </div>

</div>
""" % "".join(
        codec_options
    )



    # SMART RULES V2 INDEXER UI START

    selected_indexers = list(
        advanced.get(
            "indexer_priority",
            []
        )
        or []
    )


    all_indexers = list(
        available_indexers
        or []
    )


    # Keep saved indexers visible even if an indexer has
    # temporarily disappeared from the Arr API response.
    for name in selected_indexers:

        if name not in all_indexers:
            all_indexers.append(
                name
            )


    all_indexers = sorted(
        all_indexers,
        key=lambda value:
            value.lower()
    )


    indexer_options = []

    for name in all_indexers:

        indexer_options.append(
            '<option value="%s">%s</option>'
            % (
                html.escape(
                    name,
                    quote=True
                ),
                html.escape(
                    name
                ),
            )
        )


    preferred_rows = []

    for position, name in enumerate(
        selected_indexers,
        1
    ):

        preferred_rows.append(
            """
<div
  class="preferred-indexer-row"
  data-indexer="%s"
  style="
    display:flex;
    align-items:center;
    gap:10px;
    padding:10px 12px;
    margin-top:7px;
    border-radius:10px;
    background:rgba(3,13,24,.68);
    border:1px solid rgba(255,255,255,.07)
  "
>

  <span
    class="indexer-position"
    style="
      width:28px;
      font-weight:900;
      color:var(--accent)
    "
  >
    %d.
  </span>

  <span
    class="indexer-name"
    style="
      flex:1;
      font-weight:650
    "
  >
    %s
  </span>

  <button
    type="button"
    data-indexer-action="up"
    title="Move up"
  >↑</button>

  <button
    type="button"
    data-indexer-action="down"
    title="Move down"
  >↓</button>

  <button
    type="button"
    data-indexer-action="remove"
    title="Remove"
  >×</button>

</div>
"""
            % (
                html.escape(
                    name,
                    quote=True
                ),
                position,
                html.escape(
                    name
                ),
            )
        )


    if indexer_error:

        indexer_status = (
            "Could not refresh the configured "
            + title
            + " indexers: "
            + html.escape(
                indexer_error
            )
        )

    else:

        indexer_status = (
            "%d configured indexer%s loaded from %s."
            % (
                len(
                    available_indexers
                    or []
                ),
                ""
                if len(
                    available_indexers
                    or []
                ) == 1
                else "s",
                title,
            )
        )


    indexer_html = """
<div style="margin-top:22px">

  <h3 style="margin:0 0 8px">
    Preferred indexers / trackers
  </h3>

  <div
    class="rule-description"
    style="margin-bottom:12px"
  >
    Choose from the indexers currently configured in %s.
    Position 1 has the highest priority. Indexers not in this
    list remain valid fallbacks.
  </div>

  <div
    style="
      color:rgba(200,218,238,.70);
      font-size:.82rem;
      margin-bottom:10px
    "
  >
    %s
  </div>

  <div
    style="
      display:flex;
      gap:8px;
      flex-wrap:wrap;
      align-items:center
    "
  >

    <select
      id="indexer-picker"
      style="
        flex:1 1 260px;
        min-height:40px;
        padding:0 10px;
        border-radius:9px;
        background:#071522;
        color:#eef7ff;
        border:1px solid rgba(255,255,255,.16)
      "
    >

      <option value="">
        Choose configured indexer…
      </option>

      %s

    </select>

    <button
      id="add-preferred-indexer"
      type="button"
    >
      Add
    </button>

    <button
      id="save-preferred-indexers"
      type="button"
    >
      Save favorites
    </button>

  </div>


  <div
    id="preferred-indexers"
    style="margin-top:10px"
  >
    %s
  </div>

</div>

    # SMART RULES V2 INDEXER UI END
""" % (
        html.escape(
            title
        ),
        indexer_status,
        "".join(
            indexer_options
        ),
        "".join(
            preferred_rows
        ),
    )


    advanced_html = """
<section class="rules-section">

  <div class="section-heading">

    <h2>
      Advanced ranking preferences
    </h2>

    <p>
      These controls only rank candidates that already passed
      the optimizer's hard safety and eligibility checks.
    </p>

  </div>

  %s

  %s

  %s

</section>
""" % (
        "".join(
            advanced_sections
        ),
        codec_html,
        indexer_html,
    )

    # SMART RULES V2 ADVANCED CARDS END


    # ========================================================
    # LOCKED SAFETY RULES
    #
    # Hide rules that have now become configurable controls.
    # ========================================================

    locked_cards = []

    locked = (
        RULE_LOCKED.get(
            app,
            ()
        )
    )


    def reclassified_locked_rule(label):

        value = str(
            label
            or ""
        ).strip().lower()


        if value.startswith(
            "minimum current"
        ):
            return True

        if value in (
            "1080p replacement ceiling",
            "uhd-profile resolution guard",
        ):
            return True


        if (
            "1080" in value
            and (
                "replacement" in value
                or "ceiling" in value
            )
            and "size" in value
        ):
            return True


        if (
            "configured" in value
            and "saving" in value
        ):
            return True


        if (
            "720" in value
            and "growth" in value
        ):
            return True


        return False


    for entry in locked:

        if not isinstance(
            entry,
            (list, tuple)
        ):
            continue

        if len(entry) < 2:
            continue

        label = str(
            entry[0]
            or ""
        )

        description = str(
            entry[1]
            or ""
        )


        if reclassified_locked_rule(
            label
        ):
            continue


        locked_cards.append(
            """
<div class="locked-card">
  <div>
    <div class="rule-title">%s</div>
    <div class="rule-description">%s</div>
  </div>

  <span class="locked-pill">
    LOCKED ON
  </span>
</div>
"""
            % (
                html.escape(label),
                html.escape(description),
            )
        )


    template = r"""<!doctype html>
<html>
<head>

<meta charset="utf-8">
<meta
  name="viewport"
  content="width=device-width,initial-scale=1"
>

<title>Smart Optimizer · __TITLE__ Rules</title>

<style>

__BASE_CSS__

*{
    box-sizing:border-box;
}

html,
body{
    margin:0;
    min-height:100%;
}

body.rules-body{

    --accent:__ACCENT__;
    --accent-rgb:__ACCENT_RGB__;

    min-height:100vh;

    color:#eef6ff;

    font-family:
        Inter,
        system-ui,
        -apple-system,
        BlinkMacSystemFont,
        "Segoe UI",
        sans-serif;

    background:
        linear-gradient(
            180deg,
            rgba(1,7,14,.34),
            rgba(1,7,14,.55)
        ),
        url('__BACKGROUND__')
        center center / cover
        no-repeat fixed;
}


.rules-shell{

    width:min(
        1180px,
        calc(100vw - 42px)
    );

    margin:0 auto;

    padding:
        28px 0
        56px;
}


.rules-top{

    display:flex;

    align-items:center;

    justify-content:
        space-between;

    gap:18px;

    margin-bottom:
        20px;

    padding:
        18px 20px;

    border-radius:
        20px;

    background:
        rgba(5,16,29,.88);

    border:
        1px solid
        rgba(
            var(--accent-rgb),
            .30
        );

    backdrop-filter:
        blur(14px);
}


.rules-brand{

    display:flex;

    align-items:center;

    gap:14px;
}


.rules-icon{

    width:48px;
    height:48px;
}


.rules-icon img{

    width:100%;
    height:100%;

    object-fit:contain;
}


.rules-title h1{

    margin:0;

    font-size:
        1.55rem;
}


.rules-title p{

    margin:
        5px 0 0;

    color:
        rgba(210,224,242,.70);
}


.back-link{

    min-height:40px;

    display:inline-flex;

    align-items:center;

    padding:
        0 15px;

    border-radius:
        999px;

    color:#eef7ff;

    text-decoration:none;

    background:
        rgba(6,21,36,.78);

    border:
        1px solid
        rgba(
            var(--accent-rgb),
            .38
        );
}


.rules-summary{

    margin-bottom:
        18px;

    padding:
        13px 16px;

    border-radius:
        14px;

    background:
        rgba(6,19,33,.82);

    border:
        1px solid
        rgba(
            var(--accent-rgb),
            .20
        );

    color:
        rgba(224,235,249,.82);
}


.rules-section{

    margin-bottom:
        18px;

    padding:
        18px;

    border-radius:
        18px;

    background:
        linear-gradient(
            145deg,
            rgba(10,27,47,.91),
            rgba(5,16,29,.90)
        );

    border:
        1px solid
        rgba(
            var(--accent-rgb),
            .18
        );

    box-shadow:
        0 16px 38px
        rgba(0,0,0,.22);
}


.section-heading{

    margin-bottom:
        13px;
}


.section-heading h2{

    margin:0;

    font-size:
        1.08rem;
}


.section-heading p{

    margin:
        5px 0 0;

    color:
        rgba(205,220,239,.62);

    font-size:
        .86rem;
}


.rule-list{

    display:grid;

    gap:10px;
}


.rule-card,
.locked-card{

    min-height:
        76px;

    display:flex;

    align-items:center;

    justify-content:
        space-between;

    gap:18px;

    padding:
        14px 15px;

    border-radius:
        14px;

    background:
        rgba(3,13,24,.68);

    border:
        1px solid
        rgba(255,255,255,.065);
}


.rule-copy{

    min-width:0;

    flex:1;
}


.rule-title{

    font-weight:
        720;

    color:#f5f9ff;
}


.rule-description{

    margin-top:
        4px;

    color:
        rgba(202,218,239,.66);

    font-size:
        .84rem;

    line-height:
        1.4;
}


.rule-control{

    display:flex;

    align-items:center;

    gap:10px;

    flex:0 0 auto;
}


.state-pill,
.locked-pill{

    min-width:
        44px;

    text-align:center;

    padding:
        5px 8px;

    border-radius:
        999px;

    font-size:
        .72rem;

    font-weight:
        760;

    letter-spacing:
        .04em;

    background:
        rgba(
            var(--accent-rgb),
            .10
        );

    border:
        1px solid
        rgba(
            var(--accent-rgb),
            .24
        );
}


.locked-pill{

    min-width:
        82px;

    color:
        rgba(225,235,247,.78);
}


.switch{

    position:relative;

    width:46px;

    height:26px;

    flex:
        0 0 46px;
}


.switch input{

    opacity:0;

    width:0;

    height:0;
}


.slider{

    position:absolute;

    inset:0;

    cursor:pointer;

    border-radius:
        999px;

    background:
        rgba(87,105,128,.42);

    border:
        1px solid
        rgba(255,255,255,.08);

    transition:
        .18s;
}


.slider::before{

    content:"";

    position:absolute;

    width:18px;

    height:18px;

    left:3px;

    top:3px;

    border-radius:
        50%;

    background:#dce8f7;

    transition:
        .18s;
}


.switch input:checked + .slider{

    background:
        rgba(
            var(--accent-rgb),
            .48
        );

    border-color:
        rgba(
            var(--accent-rgb),
            .78
        );
}


.switch input:checked + .slider::before{

    transform:
        translateX(20px);

    background:#fff;
}


.growth-preview{

    margin-top:
        8px;

    color:
        rgba(214,229,246,.76);

    font-size:
        .80rem;
}


.growth-preview span{

    color:
        rgba(183,201,224,.52);
}


.growth-controls{

    display:flex;

    align-items:center;

    gap:8px;

    flex-wrap:wrap;

    margin-top:12px;
}


.growth-label{

    color:
        rgba(221,233,248,.82);

    font-size:.82rem;

    font-weight:700;
}


.growth-value{

    width:82px;

    min-height:36px;

    padding:
        0 9px;

    color:#f3f8ff;

    background:
        rgba(0,8,17,.72);

    border:
        1px solid
        rgba(
            var(--accent-rgb),
            .28
        );

    border-radius:9px;
}


.growth-separator,
.growth-percent{

    color:
        rgba(211,225,243,.70);
}


.growth-save{

    min-height:36px;

    padding:
        0 12px;

    color:#fff;

    cursor:pointer;

    border-radius:9px;

    border:
        1px solid
        rgba(
            var(--accent-rgb),
            .48
        );

    background:
        rgba(
            var(--accent-rgb),
            .18
        );
}


.growth-save:hover{

    border-color:
        rgba(
            var(--accent-rgb),
            .82
        );
}



.limit-card{

    align-items:center;
}


.limit-controls{

    display:flex;

    align-items:center;

    justify-content:flex-end;

    gap:8px;

    flex-wrap:wrap;
}


.limit-value{

    width:90px;

    min-height:38px;

    padding:
        0 10px;

    color:#f3f8ff;

    background:
        rgba(0,8,17,.72);

    border:
        1px solid
        rgba(
            var(--accent-rgb),
            .28
        );

    border-radius:9px;
}


.limit-unit{

    min-height:38px;

    padding:
        0 8px;

    color:#f3f8ff;

    background:
        rgba(0,8,17,.72);

    border:
        1px solid
        rgba(
            var(--accent-rgb),
            .28
        );

    border-radius:9px;
}


.limit-save{

    min-height:38px;

    padding:
        0 13px;

    color:#fff;

    cursor:pointer;

    border-radius:9px;

    border:
        1px solid
        rgba(
            var(--accent-rgb),
            .48
        );

    background:
        rgba(
            var(--accent-rgb),
            .18
        );
}


.downsize-note{

    display:flex;

    align-items:center;

    justify-content:
        space-between;

    gap:16px;

    padding:
        14px 15px;

    border-radius:
        14px;

    background:
        rgba(3,13,24,.68);

    border:
        1px solid
        rgba(255,255,255,.065);
}


.downsize-value{

    font-size:
        1.05rem;

    font-weight:
        760;

    color:
        var(--accent);
}


.save-state{

    position:sticky;

    bottom:14px;

    margin:
        18px auto 0;

    width:
        fit-content;

    max-width:
        calc(100vw - 40px);

    padding:
        10px 15px;

    border-radius:
        999px;

    color:
        rgba(226,237,250,.80);

    background:
        rgba(2,10,19,.94);

    border:
        1px solid
        rgba(
            var(--accent-rgb),
            .25
        );

    box-shadow:
        0 10px 28px
        rgba(0,0,0,.28);
}


.save-state.good{

    color:#c8ffd9;

    border-color:
        rgba(80,225,128,.35);
}


.save-state.bad{

    color:#ffd0d0;

    border-color:
        rgba(255,92,92,.42);
}


@media(max-width:720px){

    .rules-shell{

        width:
            min(
                100% - 20px,
                1180px
            );

        padding-top:
            12px;
    }


    .rules-top{

        align-items:
            flex-start;

        flex-direction:
            column;
    }


    .rule-card,
    .locked-card{

        align-items:
            flex-start;

        flex-direction:
            column;
    }


    .rule-control,
    .limit-controls{

        width:100%;

        justify-content:
            space-between;
    }


    .limit-value{

        flex:1;
    }
}

</style>

</head>

<body class="rules-body">

<div class="rules-shell">

  <header class="rules-top">

    <div class="rules-brand">

      <div class="rules-icon">
        <img
          src="__ICON__"
          alt="__TITLE__"
        >
      </div>

      <div class="rules-title">
        <h1>__TITLE__ RULES</h1>
        <p>
          Live optimizer policy controls.
        </p>
      </div>

    </div>

    <a
      class="back-link"
      href="__DASHBOARD__"
    >
      ← Back to __TITLE__
    </a>

  </header>


  <div class="rules-summary">

    Resolution paths, Growth ranges and optional size thresholds
    are configured here. Changes save directly to the live optimizer policy.

  </div>


  <section class="rules-section">

    <div class="section-heading">
      <h2>Downsize</h2>
      <p>
        Current same-resolution / shrinking-candidate range.
      </p>
    </div>

    <div class="downsize-note">

      <div>
        <div class="rule-title">
          Current Downsize range
        </div>

        <div class="rule-description">
          Existing dashboard setting.
        </div>
      </div>

      <div class="downsize-value">
        __DOWN_MIN__% – __DOWN_MAX__%
      </div>

    </div>

  </section>


  <section class="rules-section">

    <div class="section-heading">
      <h2>Allowed resolution upgrades</h2>
      <p>
        Each path is independent. 2160p upgrade paths retain
        the UHD-profile requirement.
      </p>
    </div>

    <div class="rule-list">
      __PATH_CARDS__
    </div>

  </section>


  <section class="rules-section">

    <div class="section-heading">
      <h2>Optional size thresholds</h2>
      <p>
        Toggle a threshold on or off and choose its own MiB or GiB value.
      </p>
    </div>

    <div class="rule-list">
      __LIMIT_CARDS__
    </div>

  </section>


  __SELECTABLE_SECTIONS__

  __ADVANCED_PREFS__


  <section class="rules-section">

    <div class="section-heading">
      <h2>Locked safety rules</h2>
      <p>
        Core integrity and downgrade protections stay enabled.
      </p>
    </div>

    <div class="rule-list">
      __LOCKED_CARDS__
    </div>

  </section>


  <div
    id="save-state"
    class="save-state"
  >
    Changes save immediately.
  </div>

</div>


<script>

(function(){

    "use strict";

    const APP = __APP_JS__;

    const saveState =
        document.getElementById(
            "save-state"
        );


    function status(
        message,
        kind
    ){

        saveState.textContent =
            message;

        saveState.className =
            "save-state"
            + (
                kind
                ? " " + kind
                : ""
            );
    }


    async function post(
        url,
        values
    ){

        const body =
            new URLSearchParams();

        Object.keys(values)
            .forEach(
                function(key){
                    body.set(
                        key,
                        String(values[key])
                    );
                }
            );


        const response =
            await fetch(
                url,
                {
                    method:"POST",

                    headers:{
                        "Content-Type":
                            "application/x-www-form-urlencoded"
                    },

                    credentials:
                        "same-origin",

                    body:
                        body.toString()
                }
            );


        let result = {};

        try{
            result =
                await response.json();
        }
        catch(_error){
            result = {};
        }


        if(
            !response.ok
            || result.ok === false
        ){
            throw new Error(
                result.error
                || (
                    "HTTP "
                    + response.status
                )
            );
        }


        return result;
    }


    document
        .querySelectorAll(
            ".rule-toggle"
        )
        .forEach(
            function(input){

                input.addEventListener(
                    "change",
                    async function(){

                        const wanted =
                            input.checked;

                        const card =
                            input.closest(
                                ".rule-card"
                            );

                        const pill =
                            card.querySelector(
                                ".state-pill"
                            );


                        input.disabled =
                            true;

                        status(
                            "Saving rule…"
                        );


                        try{

                            await post(
                                "/rules-settings",
                                {
                                    app:APP,
                                    key:
                                        input.dataset.rule,
                                    enabled:
                                        wanted
                                        ? 1
                                        : 0
                                }
                            );

                            pill.textContent =
                                wanted
                                ? "ON"
                                : "OFF";

                            status(
                                "Rule saved.",
                                "good"
                            );
                        }
                        catch(error){

                            input.checked =
                                !wanted;

                            status(
                                "Save failed: "
                                + error.message,
                                "bad"
                            );
                        }
                        finally{

                            input.disabled =
                                false;
                        }
                    }
                );
            }
        );


    document
        .querySelectorAll(
            ".path-toggle"
        )
        .forEach(
            function(input){

                input.addEventListener(
                    "change",
                    async function(){

                        const wanted =
                            input.checked;

                        const card =
                            input.closest(
                                ".rule-card"
                            );

                        const pill =
                            card.querySelector(
                                ".state-pill"
                            );


                        input.disabled =
                            true;

                        status(
                            "Saving resolution path…"
                        );


                        try{

                            await post(
                                "/resolution-policy-settings",
                                {
                                    app:APP,
                                    kind:"path",
                                    key:
                                        input.dataset.path,
                                    enabled:
                                        wanted
                                        ? 1
                                        : 0
                                }
                            );

                            pill.textContent =
                                wanted
                                ? "ON"
                                : "OFF";

                            status(
                                "Resolution path saved.",
                                "good"
                            );
                        }
                        catch(error){

                            input.checked =
                                !wanted;

                            status(
                                "Save failed: "
                                + error.message,
                                "bad"
                            );
                        }
                        finally{

                            input.disabled =
                                false;
                        }
                    }
                );
            }
        );


    // SMART RULES V2 ADVANCED SAVE JS START

    function collectAdvancedPreferences(){

        const settings = {};

        document
            .querySelectorAll(
                ".advanced-toggle"
            )
            .forEach(
                function(input){

                    settings[
                        input.dataset.advancedKey
                    ] = input.checked;
                }
            );


        const codec =
            document.getElementById(
                "advanced-codec"
            );

        settings.codec_preference =
            codec
            ? codec.value
            : "none";


        return settings;
    }


    async function saveAdvancedPreferences(
        message
    ){

        status(
            message
            || "Saving preference…"
        );


        await post(
            "/advanced-preferences-settings",
            {
                app:APP,

                settings:
                    JSON.stringify(
                        collectAdvancedPreferences()
                    )
            }
        );


        document
            .querySelectorAll(
                ".advanced-toggle"
            )
            .forEach(
                function(input){

                    const card =
                        input.closest(
                            ".rule-card"
                        );

                    if(!card){
                        return;
                    }


                    const pill =
                        card.querySelector(
                            ".state-pill"
                        );

                    if(pill){

                        pill.textContent =
                            input.checked
                            ? "ON"
                            : "OFF";
                    }
                }
            );


        status(
            "Preference saved.",
            "good"
        );
    }


    document
        .querySelectorAll(
            ".advanced-toggle"
        )
        .forEach(
            function(input){

                input.addEventListener(
                    "change",
                    async function(){

                        const wanted =
                            input.checked;

                        input.disabled =
                            true;


                        try{

                            await saveAdvancedPreferences(
                                "Saving preference…"
                            );

                        }
                        catch(error){

                            input.checked =
                                !wanted;

                            status(
                                "Save failed: "
                                + error.message,
                                "bad"
                            );
                        }
                        finally{

                            input.disabled =
                                false;
                        }
                    }
                );
            }
        );


    const advancedCodec =
        document.getElementById(
            "advanced-codec"
        );


    if(advancedCodec){

        advancedCodec.dataset.savedValue =
            advancedCodec.value;


        advancedCodec.addEventListener(
            "change",
            async function(){

                const oldValue =
                    advancedCodec.dataset.savedValue;


                advancedCodec.disabled =
                    true;


                try{

                    await saveAdvancedPreferences(
                        "Saving codec preference…"
                    );

                    advancedCodec.dataset.savedValue =
                        advancedCodec.value;

                }
                catch(error){

                    advancedCodec.value =
                        oldValue;

                    status(
                        "Save failed: "
                        + error.message,
                        "bad"
                    );
                }
                finally{

                    advancedCodec.disabled =
                        false;
                }
            }
        );
    }



    // SMART RULES V2 INDEXER JS START

    function preferredIndexerRows(){

        return Array.from(
            document.querySelectorAll(
                ".preferred-indexer-row"
            )
        );
    }


    function preferredIndexerNames(){

        return preferredIndexerRows()
            .map(
                function(row){

                    return (
                        row.dataset.indexer
                        || ""
                    ).trim();
                }
            )
            .filter(
                function(name){

                    return Boolean(name);
                }
            );
    }


    function renumberPreferredIndexers(){

        preferredIndexerRows()
            .forEach(
                function(row, index){

                    const position =
                        row.querySelector(
                            ".indexer-position"
                        );

                    if(position){

                        position.textContent =
                            String(index + 1)
                            + ".";
                    }
                }
            );
    }


    function createPreferredIndexerRow(
        name
    ){

        const row =
            document.createElement(
                "div"
            );

        row.className =
            "preferred-indexer-row";

        row.dataset.indexer =
            name;

        row.style.display =
            "flex";

        row.style.alignItems =
            "center";

        row.style.gap =
            "10px";

        row.style.padding =
            "10px 12px";

        row.style.marginTop =
            "7px";

        row.style.borderRadius =
            "10px";

        row.style.background =
            "rgba(3,13,24,.68)";

        row.style.border =
            "1px solid rgba(255,255,255,.07)";


        const position =
            document.createElement(
                "span"
            );

        position.className =
            "indexer-position";

        position.style.width =
            "28px";

        position.style.fontWeight =
            "900";

        position.style.color =
            "var(--accent)";


        const label =
            document.createElement(
                "span"
            );

        label.className =
            "indexer-name";

        label.style.flex =
            "1";

        label.style.fontWeight =
            "650";

        label.textContent =
            name;


        const actions = (
            [
                [
                    "up",
                    "↑",
                    "Move up"
                ],

                [
                    "down",
                    "↓",
                    "Move down"
                ],

                [
                    "remove",
                    "×",
                    "Remove"
                ],
            ]
        );


        row.appendChild(
            position
        );

        row.appendChild(
            label
        );


        actions.forEach(
            function(definition){

                const button =
                    document.createElement(
                        "button"
                    );

                button.type =
                    "button";

                button.dataset.indexerAction =
                    definition[0];

                button.textContent =
                    definition[1];

                button.title =
                    definition[2];

                row.appendChild(
                    button
                );
            }
        );


        return row;
    }


    const addPreferredIndexer =
        document.getElementById(
            "add-preferred-indexer"
        );


    if(addPreferredIndexer){

        addPreferredIndexer.addEventListener(
            "click",
            function(){

                const picker =
                    document.getElementById(
                        "indexer-picker"
                    );

                const container =
                    document.getElementById(
                        "preferred-indexers"
                    );


                if(
                    !picker
                    || !container
                    || !picker.value
                ){
                    return;
                }


                const wanted =
                    picker.value;


                const alreadyExists =
                    preferredIndexerNames()
                        .some(
                            function(name){

                                return (
                                    name.toLowerCase()
                                    === wanted.toLowerCase()
                                );
                            }
                        );


                if(alreadyExists){

                    status(
                        "That indexer is already in the preferred list."
                    );

                    return;
                }


                container.appendChild(
                    createPreferredIndexerRow(
                        wanted
                    )
                );


                renumberPreferredIndexers();


                status(
                    "Indexer added. Press Save favorites to apply.",
                    "good"
                );
            }
        );
    }


    const preferredIndexerContainer =
        document.getElementById(
            "preferred-indexers"
        );


    if(preferredIndexerContainer){

        preferredIndexerContainer.addEventListener(
            "click",
            function(event){

                const button =
                    event.target.closest(
                        "[data-indexer-action]"
                    );


                if(!button){
                    return;
                }


                const row =
                    button.closest(
                        ".preferred-indexer-row"
                    );


                if(!row){
                    return;
                }


                const action =
                    button.dataset.indexerAction;


                if(
                    action === "up"
                    && row.previousElementSibling
                ){

                    row.parentNode.insertBefore(
                        row,
                        row.previousElementSibling
                    );
                }


                else if(
                    action === "down"
                    && row.nextElementSibling
                ){

                    row.parentNode.insertBefore(
                        row.nextElementSibling,
                        row
                    );
                }


                else if(
                    action === "remove"
                ){

                    row.remove();
                }


                renumberPreferredIndexers();


                status(
                    "Preferred indexer order changed. Press Save favorites to apply."
                );
            }
        );
    }


    const savePreferredIndexers =
        document.getElementById(
            "save-preferred-indexers"
        );


    if(savePreferredIndexers){

        savePreferredIndexers.addEventListener(
            "click",
            async function(){

                savePreferredIndexers.disabled =
                    true;


                try{

                    status(
                        "Saving preferred indexers…"
                    );


                    await post(
                        "/advanced-preferences-settings",
                        {
                            app:APP,

                            settings:
                                JSON.stringify({
                                    indexer_priority:
                                        preferredIndexerNames()
                                })
                        }
                    );


                    status(
                        "Preferred indexers saved.",
                        "good"
                    );

                }
                catch(error){

                    status(
                        "Save failed: "
                        + error.message,
                        "bad"
                    );
                }
                finally{

                    savePreferredIndexers.disabled =
                        false;
                }
            }
        );
    }


    renumberPreferredIndexers();

    // SMART RULES V2 INDEXER JS END


    // SMART RULES V2 ADVANCED SAVE JS END


    async function saveGrowth(
        key
    ){

        const minimum =
            document.querySelector(
                '[data-growth-min="'
                + key
                + '"]'
            );

        const maximum =
            document.querySelector(
                '[data-growth-max="'
                + key
                + '"]'
            );

        const button =
            document.querySelector(
                '[data-growth-save="'
                + key
                + '"]'
            );


        button.disabled =
            true;

        minimum.disabled =
            true;

        maximum.disabled =
            true;


        status(
            "Saving Growth range…"
        );


        try{

            await post(
                "/resolution-policy-settings",
                {
                    app:APP,
                    kind:"growth",
                    key:key,

                    min_percent:
                        minimum.value,

                    max_percent:
                        maximum.value
                }
            );


            status(
                "Growth range saved.",
                "good"
            );
        }
        catch(error){

            status(
                "Save failed: "
                + error.message,
                "bad"
            );
        }
        finally{

            button.disabled =
                false;

            minimum.disabled =
                false;

            maximum.disabled =
                false;
        }
    }


    document
        .querySelectorAll(
            ".growth-save"
        )
        .forEach(
            function(button){

                button.addEventListener(
                    "click",
                    function(){

                        saveGrowth(
                            button.dataset.growthSave
                        );
                    }
                );
            }
        );


    async function saveLimit(
        key
    ){

        const toggle =
            document.querySelector(
                '[data-limit="'
                + key
                + '"]'
            );

        const value =
            document.querySelector(
                '[data-limit-value="'
                + key
                + '"]'
            );

        const unit =
            document.querySelector(
                '[data-limit-unit="'
                + key
                + '"]'
            );

        const button =
            document.querySelector(
                '[data-limit-save="'
                + key
                + '"]'
            );


        button.disabled =
            true;

        toggle.disabled =
            true;

        status(
            "Saving size threshold…"
        );


        try{

            await post(
                "/resolution-policy-settings",
                {
                    app:APP,
                    kind:"limit",
                    key:key,
                    enabled:
                        toggle.checked
                        ? 1
                        : 0,
                    value:
                        value.value,
                    unit:
                        unit.value
                }
            );

            status(
                "Size threshold saved.",
                "good"
            );
        }
        catch(error){

            status(
                "Save failed: "
                + error.message,
                "bad"
            );
        }
        finally{

            button.disabled =
                false;

            toggle.disabled =
                false;
        }
    }


    document
        .querySelectorAll(
            ".limit-save"
        )
        .forEach(
            function(button){

                button.addEventListener(
                    "click",
                    function(){

                        saveLimit(
                            button.dataset.limitSave
                        );
                    }
                );
            }
        );


    document
        .querySelectorAll(
            ".limit-toggle"
        )
        .forEach(
            function(toggle){

                toggle.addEventListener(
                    "change",
                    function(){

                        saveLimit(
                            toggle.dataset.limit
                        );
                    }
                );
            }
        );

})();

</script>

</body>
</html>
"""


    replacements = {

        "__BASE_CSS__":
            "",

        "__TITLE__":
            html.escape(title),

        "__ACCENT__":
            accent,

        "__ACCENT_RGB__":
            accent_rgb,

        "__BACKGROUND__":
            background,

        "__ICON__":
            icon,

        "__DASHBOARD__":
            dashboard,

        "__DOWN_MIN__":
            "%.1f"
            % float(down_min),

        "__DOWN_MAX__":
            "%.1f"
            % float(down_max),

        "__PATH_CARDS__":
            "".join(
                path_cards
            ),

        "__LIMIT_CARDS__":
            "".join(
                limit_cards
            ),

        "__SELECTABLE_SECTIONS__":
            "".join(
                selectable_html
            ),

        "__ADVANCED_PREFS__":
            advanced_html,

        "__LOCKED_CARDS__":
            "".join(
                locked_cards
            ),

        "__APP_JS__":
            json.dumps(app),
    }


    for key, value in replacements.items():

        template = template.replace(
            key,
            value
        )


    return _smart_expand_common(
        template
    )


# SMART FLEXIBLE RULES PAGE END


def optimizer_exclusions(app):
    """Return persistent optimizer exclusions. Never modifies Radarr/Sonarr."""
    data = load_controls()
    c = data.get(app, {})
    raw = c.get("exclusions") or []

    out = []
    for x in raw:
        if not isinstance(x, dict):
            continue
        try:
            item_id = int(x.get("id"))
        except (TypeError, ValueError):
            continue
        out.append({
            "id": item_id,
            "title": str(x.get("title") or ""),
            "year": x.get("year"),
            "added": str(x.get("added") or ""),
        })

    return out


def add_optimizer_exclusion(app, item_id, title, year=None):
    """Add to Smart Optimizer exclusion list only. NO media deletion."""
    item_id = int(item_id)

    data = load_controls()
    c = data.setdefault(app, {})
    items = c.setdefault("exclusions", [])

    # Same Radarr movie / Sonarr series cannot be added twice.
    items[:] = [
        x for x in items
        if not isinstance(x, dict) or int(x.get("id", -1)) != item_id
    ]

    items.append({
        "id": item_id,
        "title": str(title or ""),
        "year": year,
        "added": time.strftime("%Y-%m-%dT%H:%M:%S"),
    })

    save_controls(data)


def remove_optimizer_exclusion(app, item_id):
    """Remove optimizer exclusion only. Does NOT delete anything."""
    item_id = int(item_id)

    data = load_controls()
    c = data.setdefault(app, {})
    items = c.setdefault("exclusions", [])

    c["exclusions"] = [
        x for x in items
        if not isinstance(x, dict) or int(x.get("id", -1)) != item_id
    ]

    save_controls(data)


def _library_cache_path(app):
    if app not in ("radarr", "sonarr"):
        raise ValueError("Unknown app")

    return LIBRARY_CACHE_FILES[app]


def _write_library_cache(app, items):
    """Atomically store the last known-good library snapshot."""
    path = _library_cache_path(app)

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    payload = {
        "app": app,
        "updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "items": items,
    }

    tmp = path + ".tmp"

    with library_cache_lock:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(
                payload,
                f,
                ensure_ascii=False,
                separators=(",", ":")
            )
            f.flush()
            os.fsync(f.fileno())

        os.replace(tmp, path)


def _read_library_cache(app):
    """Read cached library without contacting Radarr/Sonarr."""
    path = _library_cache_path(app)

    try:
        with library_cache_lock:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)

        if isinstance(payload, dict):
            raw = payload.get("items", [])
        else:
            raw = payload

        return raw if isinstance(raw, list) else []

    except Exception:
        return []


def refresh_library_cache(app):
    """
    Refresh one library in the background.

    A failed API request NEVER destroys the previous good cache.
    """

    if app not in ("radarr", "sonarr"):
        return

    if library_cache_refreshing.get(app):
        return

    library_cache_refreshing[app] = True

    try:
        if app == "radarr":
            raw = radarr_get("/movie")
        else:
            raw = sonarr_get("/series")

        items = []

        for x in raw if isinstance(raw, list) else []:
            try:
                item_id = int(x.get("id"))
            except (TypeError, ValueError):
                continue

            items.append({
                "id": item_id,
                "title": str(x.get("title") or ""),
                "year": x.get("year"),
            })

        items.sort(
            key=lambda x: (
                x["title"].casefold(),
                int(x.get("year") or 0)
            )
        )

        _write_library_cache(app, items)

        print(
            "[ui] %s library cache refreshed: %d items"
            % (app, len(items)),
            flush=True
        )

    except Exception as exc:
        print(
            "[ui] %s library cache refresh failed: %s"
            % (app, exc),
            flush=True
        )

    finally:
        library_cache_refreshing[app] = False


def library_cache_worker():
    """
    Keep Radarr + Sonarr cache synchronized.

    Full refresh means:
      new items appear
      removed items disappear
      renamed items update
    """

    while True:
        refresh_library_cache("radarr")
        refresh_library_cache("sonarr")

        time.sleep(LIBRARY_CACHE_REFRESH_SECONDS)


def library_items(app):
    """
    Instant exclusion-search library.

    IMPORTANT:
    The cache is ONLY for finding library items.

    smart-optimizer-control.json remains the authoritative
    source that determines whether the optimizer skips an item.
    """

    raw = _read_library_cache(app)

    excluded = {
        x["id"]
        for x in optimizer_exclusions(app)
    }

    out = []

    for x in raw:
        try:
            item_id = int(x.get("id"))
        except (TypeError, ValueError):
            continue

        out.append({
            "id": item_id,
            "title": str(x.get("title") or ""),
            "year": x.get("year"),
            "excluded": item_id in excluded,
        })

    return out

def exclusion_panel(app):
    count = len(optimizer_exclusions(app))
    return (
        "<div class='searchmodebar'>"
        "<button type='button' class='searchmodebtn active' "
        "data-search-mode='current'>↩ Current downloads</button>"
        "<button type='button' id='exclusionModeButton' "
        "class='searchmodebtn' data-search-mode='exclude'>"
        "⊘ Exclusions <span>%d</span></button>"
        "<button type='button' class='searchmodebtn' "
        "data-search-mode='manual'>⌕ Manual Optimizer</button>"
        "</div>"
    ) % count

def exclusions_page(app):
    noun = "movie" if app == "radarr" else "series"
    plural = "movies" if app == "radarr" else "series"

    items = sorted(
        optimizer_exclusions(app),
        key=lambda x: x.get("added") or "",
        reverse=True
    )

    rows = ""
    for x in items:
        year = " (%s)" % html.escape(str(x["year"])) if x.get("year") else ""
        added = html.escape((x.get("added") or "").replace("T", " "))

        rows += (
            "<div class='exclusionrow'>"
            "<div><b>%s</b>%s"
            "<div class='sub'>Excluded %s</div></div>"
            "<form method='post' action='/exclusion-remove'>"
            "<input type='hidden' name='app' value='%s'>"
            "<input type='hidden' name='id' value='%d'>"
            "<button type='submit'>Remove</button>"
            "</form></div>"
        ) % (
            html.escape(x["title"]),
            year,
            added,
            app,
            x["id"],
        )

    if not rows:
        rows = "<div class='empty'>No excluded %s.</div>" % plural

    return _smart_expand_common("""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>%s exclusions</title>
<style>%s</style>
  <link rel="icon" type="image/png" sizes="32x32" href="/favicon.ico?v=2">
</head>
<body>
<div class="shell">
<div class="topbar compact">
  <div class="brand">
    <div class="brandcopy">
      <h1>%s exclusion list</h1>
      <div>These %s are ignored by Smart Optimizer.</div>
    </div>
  </div>
  <div class="nav">
    <a class="badge" href="/%s">← %s Optimizer</a>
  </div>
</div>

<div class="panel">
  <div class="panelhead">
    <div>
      <h3>All exclusions</h3>
      <p>Removing an exclusion never deletes the %s or its files.</p>
    </div>
    <span class="badge">%d EXCLUDED</span>
  </div>
  %s
</div>
</div>

<!-- SMART_COMMON_UPDATE_STATUS_FAVICON -->


<!-- SMART_COMMON_ADMIN_UPDATE_MODE -->

</body>
</html>""" % (
        app.capitalize(),
        CSS,
        app.capitalize(),
        plural,
        app,
        app.capitalize(),
        noun,
        len(items),
        rows,
    ))


job_lock = threading.Lock()
jobs = {a: {"running": False, "requested": 0, "start": 0, "proc": None, "stopped": False, "started": None, "finished": None, "output": "", "current": "", "last": "", "display_searched": 0, "display_item": "", "returncode": None} for a in ("radarr", "sonarr")}


def radarr_request(path, method="GET", payload=None):
    cfg = connection("radarr")
    if not cfg["api_key"]:
        raise RuntimeError("Radarr API key is not configured")
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        connection_url("radarr") + "/api/v3" + path,
        data=data,
        method=method,
        headers={"X-Api-Key": cfg["api_key"], "Accept": "application/json",
                 "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        raw = response.read().decode("utf-8")
        return json.loads(raw) if raw else {}

def radarr_get(path):
    return radarr_request(path)


def sonarr_request(path, method="GET", payload=None):
    cfg = connection("sonarr")
    if not cfg["api_key"]:
        raise RuntimeError("Sonarr API key is not configured")

    data = None if payload is None else json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        connection_url("sonarr") + "/api/v3" + path,
        data=data,
        method=method,
        headers={
            "X-Api-Key": cfg["api_key"],
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )

    with urllib.request.urlopen(req, timeout=30) as response:
        raw = response.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def sonarr_get(path):
    return sonarr_request(path)


def load_sonarr_state():
    try:
        with open(SONARR_STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {
            "daily": {},
            "episodes": {},
            "tracker_jobs": {},
        }


def save_sonarr_state(data):
    """Persist Sonarr optimizer state without replacing a bind-mounted inode."""
    with open(
        SONARR_STATE_FILE,
        "w",
        encoding="utf-8"
    ) as f:
        json.dump(
            data,
            f,
            indent=2,
            sort_keys=True
        )
        f.flush()
        os.fsync(
            f.fileno()
        )


def sonarr_history_records():
    records = []
    for page_num in range(1, HISTORY_PAGES + 1):
        data = sonarr_get("/history?page=%d&pageSize=100&sortKey=date&sortDirection=descending" % page_num)
        batch = data.get("records", [])
        records.extend(batch)
        if len(batch) < 100:
            break
    return records


def sonarr_queue_records():
    data = sonarr_get("/queue?page=1&pageSize=100&sortKey=timeleft&sortDirection=ascending")
    return data.get("records", [])


def sonarr_completed_upgrades(records):
    pending = {}
    upgrades = []
    for event in reversed(records):
        episode_id = event.get("episodeId")
        etype = event.get("eventType")
        data = event.get("data") or {}
        if etype == "episodeFileDeleted" and data.get("reason") == "Upgrade":
            try:
                pending[episode_id] = int(data.get("size") or 0)
            except (TypeError, ValueError):
                pass
        elif etype == "downloadFolderImported" and episode_id in pending:
            try:
                new_size = int(data.get("size") or 0)
            except (TypeError, ValueError):
                new_size = 0
            old_size = pending.pop(episode_id)
            if old_size > 0 and new_size > 0:
                upgrades.append({
                    "date": event.get("date") or "",
                    "title": event.get("sourceTitle") or ("Episode ID %s" % episode_id),
                    "old": old_size, "new": new_size, "saved": old_size - new_size,
                })
    return sorted(upgrades, key=lambda x: x["date"], reverse=True)


def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"daily": {}, "movies": {}}


def history_records():
    records = []
    for page in range(1, HISTORY_PAGES + 1):
        data = radarr_get("/history?page=%d&pageSize=100&sortKey=date&sortDirection=descending" % page)
        batch = data.get("records", [])
        records.extend(batch)
        if len(batch) < 100:
            break
    return records


def queue_records():
    """Return every current Radarr queue row, not only the first 100."""
    records = []
    page = 1
    page_size = 100

    while True:
        data = radarr_get(
            "/queue?page=%d&pageSize=%d&sortKey=timeleft&sortDirection=ascending"
            % (page, page_size)
        ) or {}
        batch = data.get("records") or []
        records.extend(batch)

        try:
            total = int(data.get("totalRecords") or len(records))
        except (TypeError, ValueError):
            total = len(records)

        if not batch or len(records) >= total or len(batch) < page_size:
            break

        page += 1

    return records


def _radarr_rejection_reason(rejection):
    """Extract and normalize one Radarr ManualImport rejection reason."""
    if isinstance(rejection, dict):
        raw = rejection.get("reason") or rejection.get("message") or ""
    else:
        raw = rejection or ""

    raw = str(raw).strip()
    normalized = raw.lower().replace("(s)", "s")
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized).strip()
    normalized = re.sub(r"\s+", " ", normalized)
    return raw, normalized


def classify_radarr_import_rejection(rejection):
    """Classify a Radarr rejection. Unknown reasons always fail closed."""
    raw, reason = _radarr_rejection_reason(rejection)

    if not reason:
        return "unknown", raw

    # Explicitly dangerous/non-quality families. These are never overridden.
    unsafe_markers = (
        "custom format",
        "language",
        "sample",
        "does not match",
        "no audio",
        "audio track",
        "encrypted",
        "corrupt",
        "unpack",
        "free space",
        "unable to determine",
    )

    if any(marker in reason for marker in unsafe_markers):
        return "unsafe", raw

    # These are native Radarr quality/revision/cutoff objections only.
    safe_patterns = (
        ("native_cutoff", "existing file meets cutoff"),
        (
            "native_quality_preference",
            "quality for existing file on disk is of equal or higher preference",
        ),
        (
            "native_revision_upgrade",
            "not a quality revision upgrade for existing movie file",
        ),
        (
            "native_quality_upgrade",
            "not a quality upgrade for existing movie file",
        ),
        (
            "native_quality_upgrade",
            "upgrade for existing movie file",
        ),
    )

    for category, pattern in safe_patterns:
        if pattern in reason:
            return category, raw

    return "unknown", raw


def radarr_rejections_are_optimizer_safe(rejections):
    """Return (safe, accepted_categories, blocked_details)."""
    accepted = []
    blocked = []

    for rejection in rejections or []:
        category, raw = classify_radarr_import_rejection(rejection)

        if category.startswith("native_"):
            accepted.append(category)
        else:
            blocked.append({"category": category, "reason": raw})

    return not blocked, accepted, blocked


def _optimizer_owned_queue_row(row, pending=None):
    """Prove ownership only from persisted exact queue/download identity."""
    if pending is None:
        pending = (load_state().get("pending_replacements") or {})

    movie_id = int(row.get("movieId") or 0)
    queue_id = int(row.get("id") or 0)
    download_id = str(row.get("downloadId") or "").strip().upper()

    txn = pending.get(str(movie_id)) or {}
    expected_download_id = str(txn.get("download_id") or "").strip().upper()
    expected_queue_id = int(txn.get("queue_id") or 0)

    if not movie_id or not download_id or not expected_download_id:
        return False

    if download_id != expected_download_id:
        return False

    if expected_queue_id and queue_id != expected_queue_id:
        return False

    return True


def queue_health(rows):
    """Classify queue rows conservatively from Radarr plus optimizer ownership."""
    result = []
    pending = (load_state().get("pending_replacements") or {})

    for row in rows:
        status = str(row.get("status") or "").lower()
        tracked = str(row.get("trackedDownloadStatus") or "").lower()
        messages = row.get("statusMessages") or []
        message_parts = []
        for m in messages:
            if not isinstance(m, dict):
                continue
            title = str(m.get("title") or "")
            details = m.get("messages") or []
            if isinstance(details, list):
                detail_text = " ".join(
                    str(x.get("message") or "") if isinstance(x, dict) else str(x)
                    for x in details
                )
            elif isinstance(details, dict):
                detail_text = str(details.get("message") or details)
            else:
                detail_text = str(details)
            message_parts.append((title + " " + detail_text).strip())
        message_text = " ".join(x for x in message_parts if x).strip()
        size = float(row.get("size") or 0)
        left = float(row.get("sizeleft") or 0)
        progress = max(0.0, min(100.0, ((size - left) / size * 100.0) if size else 0.0))
        attention = tracked in ("warning", "error") or status in ("warning", "failed")
        message_category, _ = classify_radarr_import_rejection(message_text)
        optimizer_owned = _optimizer_owned_queue_row(row, pending)
        import_pending = str(row.get("trackedDownloadState") or "").lower() == "importpending"

        if message_category.startswith("native_"):
            health = "Optimizer import blocked"
            health_kind = "optimizer_blocked"
        elif optimizer_owned and import_pending:
            health = "Optimizer-owned import issue"
            health_kind = "optimizer_owned_issue"
        elif tracked == "error" or status == "failed":
            health = "Failed"
            health_kind = "failed"
        elif tracked == "warning" or status == "warning":
            health = "Needs attention"
            health_kind = "warning"
        else:
            health = row.get("status") or row.get("trackedDownloadStatus") or "unknown"
            health_kind = "normal"
        result.append({
            "title": row.get("title") or ("Movie ID %s" % row.get("movieId")),
            "movie_id": row.get("movieId"),
            "status": row.get("status") or row.get("trackedDownloadStatus") or "unknown",
            "tracked": row.get("trackedDownloadStatus") or "",
            "progress": progress,
            "timeleft": row.get("timeleft") or "—",
            "message": message_text,
            "attention": attention,
            "health": health,
            "health_kind": health_kind,
            "queue_id": row.get("id"),
        })
    return result


def repair_import(queue_id):
    """Safely enroll one legacy optimizer-blocked Radarr import."""
    rows = queue_records()
    row = next(
        (x for x in rows if str(x.get("id")) == str(queue_id)),
        None
    )

    if not row:
        raise RuntimeError("Queue item is no longer present")

    classified = queue_health([row])[0]

    if classified.get("health_kind") != "optimizer_blocked":
        raise RuntimeError("This item is not an optimizer import block")

    if str(row.get("status") or "").lower() != "completed":
        raise RuntimeError("Download is not completed")

    if str(row.get("trackedDownloadState") or "").lower() != "importpending":
        raise RuntimeError("Download is not waiting for import")

    if int(row.get("sizeleft") or 0) != 0:
        raise RuntimeError("Download still has data remaining")

    movie_id = int(row.get("movieId") or 0)

    if not movie_id:
        raise RuntimeError("Queue item has no movieId")

    movie = radarr_get("/movie/%d" % movie_id)
    old_file = movie.get("movieFile") or {}

    old_file_id = int(old_file.get("id") or 0)
    old_size = int(old_file.get("size") or 0)
    new_size = int(row.get("size") or 0)

    old_res = int(
        (((old_file.get("quality") or {}).get("quality") or {})
         .get("resolution") or 0)
    )

    new_res = int(
        (((row.get("quality") or {}).get("quality") or {})
         .get("resolution") or 0)
    )

    if not old_file_id or not old_size:
        raise RuntimeError("Cannot identify the existing movie file safely")

    if not new_size or not old_res or not new_res:
        raise RuntimeError(
            "Cannot safely compare current and downloaded file"
        )

    if new_res < old_res:
        raise RuntimeError("Refusing resolution downgrade")

    if new_size >= old_size:
        raise RuntimeError("Refusing replacement that is not smaller")

    saving = ((old_size - new_size) / old_size) * 100.0

    minimum, maximum, _ = app_controls("radarr")

    if saving < minimum or saving > maximum:
        raise RuntimeError(
            "Replacement saving %.2f%% is outside %.1f%%-%.1f%%"
            % (saving, minimum, maximum)
        )

    download_id = str(row.get("downloadId") or "")

    if not download_id:
        raise RuntimeError("Queue item has no downloadId")

    items = radarr_get(
        "/manualimport?downloadId=%s&movieId=%d"
        "&filterExistingFiles=false"
        % (urllib.parse.quote(download_id), movie_id)
    )

    usable = []

    for item in items if isinstance(items, list) else []:
        rejections = item.get("rejections") or []
        safe, _accepted, _blocked = radarr_rejections_are_optimizer_safe(
            rejections
        )

        if safe and item.get("path"):
            usable.append(item)

    if len(usable) != 1:
        raise RuntimeError(
            "Expected exactly one safely reprocessable video file, found %d"
            % len(usable)
        )

    state = load_state()
    pending = state.setdefault("pending_replacements", {})
    key = str(movie_id)

    existing = pending.get(key)

    if existing:
        raise RuntimeError(
            "Smart Optimizer already has a pending replacement "
            "for this movie"
        )

    pending[key] = {
        "movie_id": movie_id,
        "old_file_id": old_file_id,
        "old_size": old_size,
        "approved_title": str(row.get("title") or "").strip(),
        "approved_size": new_size,
        "download_id": download_id,
        "created": int(time.time()),
        "status": "grabbed",
        "legacy_repair": True
    }

    save_radarr_state(state)

    return {
        "status": "queued",
        "message": (
            "Safe optimizer import queued. "
            "Existing file will remain until the replacement "
            "has imported and been verified."
        )
    }


def save_radarr_state(data):
    """Persist Radarr optimizer state without replacing a bind-mounted inode."""
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())



def remove_exact_radarr_download(queue_id, movie_id, download_id):
    """Remove only an exactly revalidated Radarr queue/download item."""
    rows = queue_records()

    matches = [
        row for row in rows
        if int(row.get("id") or 0) == int(queue_id)
        and int(row.get("movieId") or 0) == int(movie_id)
        and str(row.get("downloadId") or "").strip().upper()
        == str(download_id or "").strip().upper()
    ]

    if len(matches) != 1:
        raise RuntimeError(
            "exact Radarr queue ownership revalidation failed"
        )

    return radarr_request(
        "/queue/%d?removeFromClient=true&blocklist=false"
        % int(queue_id),
        method="DELETE"
    )


def _radarr_root_video_name(relative_path):
    value = str(relative_path or "").replace("\\", "/").strip("/")
    if not value or "/" in value:
        return None
    return value


def radarr_root_movie_file_guard(movie_id, expected_file_id=None):
    """
    Verify Radarr database state against physical root-level movie videos.

    Subfolder video extras (backdrops/theme.*, specials/*, trailers/*, etc.)
    are ignored. Unknown/API-error state fails closed.
    """
    try:
        movie = radarr_get("/movie/%d" % int(movie_id)) or {}
        movie_path = str(movie.get("path") or "")

        if not movie_path:
            return False, {
                "reason": "movie path unavailable"
            }

        registered = radarr_get(
            "/moviefile?movieId=%d" % int(movie_id)
        ) or []

        media = radarr_get(
            "/filesystem/mediafiles?path=%s"
            % urllib.parse.quote(movie_path, safe="")
        ) or []
    except Exception as exc:
        return False, {
            "reason": "filesystem guard API error",
            "error": str(exc),
        }

    registered_root = []
    for item in registered:
        root_name = _radarr_root_video_name(item.get("relativePath"))
        if root_name:
            registered_root.append({
                "id": int(item.get("id") or 0),
                "name": root_name,
                "size": int(item.get("size") or 0),
            })

    physical_root = []
    for item in media:
        root_name = _radarr_root_video_name(item.get("relativePath"))
        if root_name:
            physical_root.append(root_name)

    clean = (
        len(registered) == 1
        and len(registered_root) == 1
        and len(physical_root) == 1
        and registered_root[0]["name"] == physical_root[0]
    )

    if expected_file_id is not None:
        clean = (
            clean
            and registered_root[0]["id"] == int(expected_file_id)
        )

    return clean, {
        "reason": "clean" if clean else "competing/missing root movie file",
        "movie_path": movie_path,
        "registered_count": len(registered),
        "registered_root": registered_root,
        "physical_root": physical_root,
        "expected_file_id": (
            int(expected_file_id)
            if expected_file_id is not None
            else None
        ),
    }


def _radarr_root_video_details(movie_id):
    """
    Return physical root-level video files with exact byte sizes.

    /filesystem/mediafiles identifies videos. /filesystem supplies the exact
    root-file sizes. Subfolder extras are ignored.
    """
    movie = radarr_get("/movie/%d" % int(movie_id)) or {}
    movie_path = str(movie.get("path") or "").strip()

    if not movie_path:
        raise RuntimeError("movie path unavailable")

    media = radarr_get(
        "/filesystem/mediafiles?path=%s"
        % urllib.parse.quote(movie_path, safe="")
    ) or []

    listing = radarr_get(
        "/filesystem?path=%s&includeFiles=true"
        "&allowFoldersWithoutTrailingSlashes=true"
        % urllib.parse.quote(movie_path, safe="")
    ) or {}

    root_video_names = set()

    for item in media:
        name = _radarr_root_video_name(item.get("relativePath"))
        if name:
            root_video_names.add(name)

    root_files = {}

    for item in listing.get("files") or []:
        name = str(item.get("name") or "").strip()

        if name in root_video_names:
            root_files[name] = {
                "name": name,
                "path": str(item.get("path") or ""),
                "size": int(item.get("size") or 0),
            }

    # Fail closed if the two Radarr filesystem views disagree.
    if set(root_files) != root_video_names:
        raise RuntimeError(
            "filesystem listing/mediafiles disagreement"
        )

    return movie_path, root_files


def _radarr_wait_command(command_id, timeout=120):
    deadline = time.time() + int(timeout)

    while time.time() < deadline:
        result = radarr_get(
            "/command/%d" % int(command_id)
        ) or {}

        status = str(
            result.get("status") or ""
        ).lower()

        if status == "completed":
            return True

        if status in ("failed", "aborted"):
            raise RuntimeError(
                "Radarr command %d ended %s"
                % (int(command_id), status)
            )

        time.sleep(2)

    raise RuntimeError(
        "Radarr command %d timed out"
        % int(command_id)
    )


def _radarr_rescan_movie(movie_id):
    result = radarr_request(
        "/command",
        method="POST",
        payload={
            "name": "RescanMovie",
            "movieId": int(movie_id),
        },
    ) or {}

    command_id = int(result.get("id") or 0)

    if not command_id:
        raise RuntimeError(
            "Radarr did not return a RescanMovie command id"
        )

    _radarr_wait_command(command_id)


def radarr_self_heal_owned_old_copy(
    movie_id,
    txn,
    expected_new_file_id,
    expected_new_size,
    expected_new_relative_path,
):
    """
    Self-heal ONLY an optimizer-owned duplicate after a verified import.

    Safety proof required before any delete:
      * transaction has exact old path + exact old byte size
      * expected new file is physically present at exact path + size
      * exactly one extra root video exists
      * that extra is the exact old transaction path + exact old byte size
      * Radarr recycle bin is configured
      * a rescan exposes exactly one registered file with old exact byte size

    The old file is removed through Radarr, so configured Radarr recycling
    remains authoritative. Any ambiguity fails closed without deleting.
    """
    old_size = int(txn.get("old_size") or 0)
    old_relative_path = str(
        txn.get("old_relative_path") or ""
    ).strip()

    expected_new_file_id = int(
        expected_new_file_id or 0
    )
    expected_new_size = int(
        expected_new_size or 0
    )
    expected_new_relative_path = str(
        expected_new_relative_path or ""
    ).strip()

    if (
        old_size <= 0
        or not old_relative_path
        or expected_new_file_id <= 0
        or expected_new_size <= 0
        or not expected_new_relative_path
        or old_size == expected_new_size
        or old_relative_path == expected_new_relative_path
    ):
        return False, {
            "reason": "insufficient exact transaction identity"
        }

    try:
        movie_path, physical = _radarr_root_video_details(
            movie_id
        )
    except Exception as exc:
        return False, {
            "reason": "physical root inspection failed",
            "error": str(exc),
        }

    expected_new = physical.get(
        expected_new_relative_path
    )

    if (
        not expected_new
        or int(expected_new.get("size") or 0)
        != expected_new_size
    ):
        return False, {
            "reason": "expected new physical movie is not exact",
            "movie_path": movie_path,
            "physical_root": physical,
        }

    extras = [
        item
        for name, item in physical.items()
        if name != expected_new_relative_path
    ]

    if len(extras) != 1:
        return False, {
            "reason": "expected exactly one competing root movie",
            "physical_root": physical,
        }

    extra = extras[0]

    if (
        str(extra.get("name") or "")
        != old_relative_path
        or int(extra.get("size") or 0)
        != old_size
    ):
        return False, {
            "reason": "competing root movie is not exact old transaction file",
            "expected_old_relative_path": old_relative_path,
            "expected_old_size": old_size,
            "actual_extra": extra,
        }

    try:
        media_cfg = radarr_get(
            "/config/mediamanagement"
        ) or {}
    except Exception as exc:
        return False, {
            "reason": "cannot verify Radarr recycle bin",
            "error": str(exc),
        }

    recycle_bin = str(
        media_cfg.get("recycleBin") or ""
    ).strip()

    if not recycle_bin:
        return False, {
            "reason": "Radarr recycle bin is not configured"
        }

    # Make the exact physical old copy visible to Radarr's movie-file DB.
    try:
        _radarr_rescan_movie(movie_id)
    except Exception as exc:
        return False, {
            "reason": "pre-heal Radarr rescan failed",
            "error": str(exc),
        }

    try:
        registered = radarr_get(
            "/moviefile?movieId=%d"
            % int(movie_id)
        ) or []
    except Exception as exc:
        return False, {
            "reason": "cannot inspect registered files after rescan",
            "error": str(exc),
        }

    old_matches = [
        item
        for item in registered
        if int(item.get("size") or 0) == old_size
    ]

    if len(old_matches) != 1:
        return False, {
            "reason": "old exact byte-size file is not uniquely registered",
            "registered": [
                {
                    "id": int(x.get("id") or 0),
                    "relativePath": str(
                        x.get("relativePath") or ""
                    ),
                    "size": int(x.get("size") or 0),
                }
                for x in registered
            ],
        }

    old_registered_id = int(
        old_matches[0].get("id") or 0
    )

    if not old_registered_id:
        return False, {
            "reason": "old registered file id unavailable"
        }

    # Revalidate that the expected new physical file still exists immediately
    # before asking Radarr to recycle the exact old registered file.
    try:
        _, physical = _radarr_root_video_details(
            movie_id
        )
    except Exception as exc:
        return False, {
            "reason": "final pre-delete physical revalidation failed",
            "error": str(exc),
        }

    new_physical = physical.get(
        expected_new_relative_path
    )

    old_physical = physical.get(
        old_relative_path
    )

    if (
        not new_physical
        or int(new_physical.get("size") or 0)
        != expected_new_size
        or not old_physical
        or int(old_physical.get("size") or 0)
        != old_size
        or len(physical) != 2
    ):
        return False, {
            "reason": "physical state changed before recycle",
            "physical_root": physical,
        }

    delete_request_error = None

    try:
        radarr_request(
            "/moviefile/%d"
            % old_registered_id,
            method="DELETE"
        )
    except Exception as exc:
        # A Radarr DELETE can time out at the HTTP client while Radarr keeps
        # moving a large file into its recycle bin. Do NOT retry the DELETE.
        # Treat the outcome as unknown and prove the filesystem result below.
        delete_request_error = str(exc)

        print(
            "[radarr-import] recycle request returned uncertain outcome; "
            "waiting for exact filesystem proof:",
            movie_id,
            delete_request_error
        )

    recycle_wait_seconds = int(
        os.environ.get(
            "SMART_OPTIMIZER_RECYCLE_SETTLE_SECONDS",
            "1800"
        )
    )

    recycle_deadline = (
        time.time()
        + max(30, recycle_wait_seconds)
    )

    while True:
        try:
            _, physical = _radarr_root_video_details(
                movie_id
            )
        except Exception as exc:
            return False, {
                "reason": "recycle outcome inspection failed",
                "error": str(exc),
                "delete_request_error": delete_request_error,
                "old_registered_id": old_registered_id,
            }

        new_physical = physical.get(
            expected_new_relative_path
        )

        old_physical = physical.get(
            old_relative_path
        )

        new_exact = (
            bool(new_physical)
            and int(new_physical.get("size") or 0)
            == expected_new_size
        )

        if not new_exact:
            return False, {
                "reason": "expected new movie changed during recycle",
                "physical_root": physical,
                "delete_request_error": delete_request_error,
                "old_registered_id": old_registered_id,
            }

        if (
            old_physical is None
            and len(physical) == 1
        ):
            break

        if time.time() >= recycle_deadline:
            return False, {
                "reason": "exact old file did not leave movie folder before recycle timeout",
                "physical_root": physical,
                "delete_request_error": delete_request_error,
                "old_registered_id": old_registered_id,
                "wait_seconds": max(
                    30,
                    recycle_wait_seconds
                ),
            }

        time.sleep(5)

    try:
        _radarr_rescan_movie(movie_id)
    except Exception as exc:
        return False, {
            "reason": "post-heal Radarr rescan failed",
            "error": str(exc),
            "old_registered_id": old_registered_id,
        }

    try:
        registered = radarr_get(
            "/moviefile?movieId=%d"
            % int(movie_id)
        ) or []

        _, physical = _radarr_root_video_details(
            movie_id
        )
    except Exception as exc:
        return False, {
            "reason": "post-heal verification failed",
            "error": str(exc),
            "old_registered_id": old_registered_id,
        }

    final_registered = [
        item
        for item in registered
        if (
            str(item.get("relativePath") or "")
            == expected_new_relative_path
            and int(item.get("size") or 0)
            == expected_new_size
        )
    ]

    final_physical = physical.get(
        expected_new_relative_path
    )

    if (
        len(registered) != 1
        or len(final_registered) != 1
        or len(physical) != 1
        or not final_physical
        or int(final_physical.get("size") or 0)
        != expected_new_size
    ):
        return False, {
            "reason": "self-heal did not reach one-clean-movie invariant",
            "registered": [
                {
                    "id": int(x.get("id") or 0),
                    "relativePath": str(
                        x.get("relativePath") or ""
                    ),
                    "size": int(x.get("size") or 0),
                }
                for x in registered
            ],
            "physical_root": physical,
            "old_registered_id": old_registered_id,
        }

    return True, {
        "reason": "self-healed",
        "recycle_bin": recycle_bin,
        "recycled_old_file_id": old_registered_id,
        "delete_request_error": delete_request_error,
        "new_file_id": int(
            final_registered[0].get("id") or 0
        ),
        "new_relative_path": expected_new_relative_path,
        "new_size": expected_new_size,
    }


def optimizer_import_worker():
    """Finish only replacements previously approved by Smart Optimizer."""
    while True:
        try:
            state = load_state()
            pending = state.get("pending_replacements") or {}

            tracker_changed = _process_tracker_jobs(
                "radarr",
                state,
            )

            if tracker_changed:
                save_radarr_state(
                    state
                )

            if pending:
                rows = queue_records()
                changed = False

                for movie_key, txn in list(pending.items()):
                    try:
                        movie_id = int(txn.get("movie_id") or movie_key)
                        old_file_id = int(txn.get("old_file_id") or 0)
                        old_size = int(txn.get("old_size") or 0)
                        approved_title = str(txn.get("approved_title") or "").strip()
                        approved_size = int(txn.get("approved_size") or 0)
                        status = str(txn.get("status") or "grabbed")

                        if not old_file_id or not old_size or not approved_title:
                            continue

                        if status in ("grabbing", "grabbed"):
                            matches = []

                            bound_download_id = str(
                                txn.get("download_id") or ""
                            ).strip()
                            bound_queue_id = int(txn.get("queue_id") or 0)
                            pre_grab_queue_ids = {
                                int(x)
                                for x in (txn.get("pre_grab_queue_ids") or [])
                                if str(x).isdigit()
                            }

                            for row in rows:
                                if int(row.get("movieId") or 0) != movie_id:
                                    continue

                                row_download_id = str(
                                    row.get("downloadId") or ""
                                ).strip()
                                row_queue_id = int(row.get("id") or 0)

                                # Strongest proof: exact persisted download ID.
                                if bound_download_id:
                                    if (
                                        row_download_id.upper()
                                        == bound_download_id.upper()
                                        and (
                                            not bound_queue_id
                                            or row_queue_id == bound_queue_id
                                        )
                                    ):
                                        matches.append(row)
                                    continue

                                # Older transaction may already have an exact
                                # queue ID even if downloadId was not persisted.
                                if bound_queue_id:
                                    if row_queue_id == bound_queue_id:
                                        matches.append(row)
                                    continue

                                # New transactions snapshot queue IDs BEFORE
                                # grabbing. Only a newly appeared row can bind.
                                if pre_grab_queue_ids:
                                    if (
                                        row_queue_id
                                        and row_queue_id not in pre_grab_queue_ids
                                    ):
                                        matches.append(row)
                                    continue

                                # No exact identity proof: fail closed. Never
                                # fall back to release-title equality.

                            if len(matches) > 1:
                                print(
                                    "[radarr-import] exact ownership ambiguous; "
                                    "refusing:",
                                    movie_id,
                                    "count",
                                    len(matches)
                                )
                                continue

                            if len(matches) == 1:
                                row = matches[0]
                            else:
                                # Radarr may drop a completed download from its
                                # queue after rejecting the native automatic
                                # import ("not a quality revision upgrade").
                                #
                                # Recover ONLY the exact optimizer-owned hash.
                                # No title guessing and no alternate torrent.
                                if not bound_download_id:
                                    continue

                                health = deluge_torrent_status(
                                    bound_download_id
                                )

                                try:
                                    recovered_progress = float(
                                        health.get("progress") or 0
                                    )
                                except (TypeError, ValueError):
                                    recovered_progress = 0.0

                                try:
                                    recovered_done = int(
                                        health.get("total_done") or 0
                                    )
                                except (TypeError, ValueError):
                                    recovered_done = 0

                                if (
                                    not health
                                    or recovered_progress < 100.0
                                    or recovered_done <= 0
                                ):
                                    continue

                                recovered_items = radarr_get(
                                    "/manualimport?downloadId=%s&movieId=%d"
                                    "&filterExistingFiles=false"
                                    % (
                                        urllib.parse.quote(
                                            bound_download_id
                                        ),
                                        movie_id
                                    )
                                )

                                recovered_usable = []
                                recovered_allowed_rejections = (
                                    "existing file meets cutoff",
                                    "upgrade for existing movie file",
                                    "quality for existing file on disk is of equal or higher preference",
                                )

                                for recovered_item in (
                                    recovered_items
                                    if isinstance(recovered_items, list)
                                    else []
                                ):
                                    recovered_bad = []

                                    for rejection in (
                                        recovered_item.get("rejections")
                                        or []
                                    ):
                                        reason = str(
                                            (
                                                rejection.get("reason")
                                                or rejection.get("message")
                                                or ""
                                            )
                                            if isinstance(
                                                rejection,
                                                dict
                                            )
                                            else rejection
                                        ).lower()

                                        if not any(
                                            allowed in reason
                                            for allowed in
                                            recovered_allowed_rejections
                                        ):
                                            recovered_bad.append(
                                                rejection
                                            )

                                    if (
                                        not recovered_bad
                                        and recovered_item.get("path")
                                    ):
                                        recovered_usable.append(
                                            recovered_item
                                        )

                                if len(recovered_usable) != 1:
                                    print(
                                        "[radarr-import] queue gone; exact "
                                        "completed payload found but safe "
                                        "ManualImport candidate count:",
                                        len(recovered_usable),
                                        "movie:",
                                        movie_id
                                    )
                                    continue

                                recovered_item = recovered_usable[0]

                                try:
                                    recovered_size = int(
                                        recovered_item.get("size")
                                        or approved_size
                                        or 0
                                    )
                                except (TypeError, ValueError):
                                    recovered_size = 0

                                if recovered_size <= 0:
                                    continue

                                # Synthesize the completed queue facts needed by
                                # the existing, already-tested import path below.
                                # row["size"] is the actual ManualImport file size,
                                # so the existing final size/saving checks use it.
                                row = {
                                    "id": int(txn.get("queue_id") or 0),
                                    "movieId": movie_id,
                                    "downloadId": bound_download_id,
                                    "status": "completed",
                                    "trackedDownloadState": "importPending",
                                    "sizeleft": 0,
                                    "size": recovered_size,
                                    "title": approved_title,
                                    "languages": (
                                        recovered_item.get("languages")
                                        or []
                                    ),
                                }

                                print(
                                    "[radarr-import] RECOVERED completed exact "
                                    "Deluge payload after Radarr queue vanished:",
                                    movie_id,
                                    bound_download_id,
                                    "%.2f GiB"
                                    % (
                                        recovered_size
                                        / 1073741824
                                    )
                                )

                            if status == "grabbing":
                                txn["status"] = "grabbed"
                                status = "grabbed"
                                changed = True

                            download_id = str(
                                row.get("downloadId") or ""
                            ).strip()

                            queue_id = int(row.get("id") or 0)

                            if not download_id or not queue_id:
                                continue

                            if not bound_download_id:
                                txn["download_id"] = download_id
                                txn["queue_id"] = queue_id
                                txn["bound_at"] = int(time.time())
                                changed = True

                                state["pending_replacements"] = pending
                                save_radarr_state(state)

                                print(
                                    "[radarr-fallback] ownership bound:",
                                    movie_id,
                                    "queue",
                                    queue_id,
                                    "download",
                                    download_id
                                )

                            elif (
                                download_id.upper()
                                != bound_download_id.upper()
                            ):
                                continue

                            row_status = str(
                                row.get("status") or ""
                            ).lower()

                            created = int(
                                txn.get("grabbed_at")
                                or txn.get("created")
                                or time.time()
                            )

                            age_seconds = max(
                                0,
                                int(time.time()) - created
                            )

                            # MONITOR ONLY.
                            # No torrent/queue/file deletion in Patch 3C.
                            if (
                                age_seconds >= 300
                                and row_status != "completed"
                            ):
                                try:
                                    health = deluge_torrent_status(
                                        download_id
                                    )

                                    if deluge_torrent_is_dead(health):
                                        retry_count = int(
                                            txn.get("dead_retry_count") or 0
                                        )

                                        if retry_count >= 3:
                                            print(
                                                "[radarr-fallback] retry cap "
                                                "reached; keeping download:",
                                                movie_id
                                            )
                                            continue

                                        release_key = str(
                                            txn.get("release_key") or ""
                                        ).strip()

                                        attempted = (
                                            state.get("attempted_releases")
                                            or {}
                                        )

                                        if (
                                            not release_key
                                            or release_key not in attempted
                                        ):
                                            print(
                                                "[radarr-fallback] release "
                                                "blacklist not proven; "
                                                "keeping download:",
                                                movie_id
                                            )
                                            continue

                                        files = radarr_get(
                                            "/moviefile?movieId=%d"
                                            % movie_id
                                        )

                                        old_file = next(
                                            (
                                                f for f in files
                                                if int(
                                                    f.get("id") or 0
                                                ) == old_file_id
                                            ),
                                            None
                                        )

                                        if (
                                            not old_file
                                            or int(
                                                old_file.get("size") or 0
                                            ) != old_size
                                        ):
                                            print(
                                                "[radarr-fallback] original "
                                                "file changed; refusing "
                                                "dead cleanup:",
                                                movie_id
                                            )
                                            continue

                                        # Query Deluge a second time
                                        # immediately before deletion.
                                        final_health = (
                                            deluge_torrent_status(
                                                download_id
                                            )
                                        )

                                        if not deluge_torrent_is_dead(
                                            final_health
                                        ):
                                            print(
                                                "[radarr-fallback] torrent "
                                                "became active; keeping:",
                                                movie_id
                                            )
                                            continue

                                        remove_exact_radarr_download(
                                            queue_id,
                                            movie_id,
                                            download_id
                                        )

                                        # Radarr must no longer expose this
                                        # exact queue/hash.
                                        remaining = queue_records()

                                        still_present = any(
                                            int(
                                                r.get("id") or 0
                                            ) == queue_id
                                            or (
                                                int(
                                                    r.get("movieId") or 0
                                                ) == movie_id
                                                and str(
                                                    r.get(
                                                        "downloadId"
                                                    ) or ""
                                                ).strip().upper()
                                                == download_id.upper()
                                            )
                                            for r in remaining
                                        )

                                        if still_present:
                                            raise RuntimeError(
                                                "dead download still present "
                                                "after exact removal"
                                            )

                                        # The original movie file is the
                                        # safety anchor and MUST still exist.
                                        files = radarr_get(
                                            "/moviefile?movieId=%d"
                                            % movie_id
                                        )

                                        old_file = next(
                                            (
                                                f for f in files
                                                if int(
                                                    f.get("id") or 0
                                                ) == old_file_id
                                            ),
                                            None
                                        )

                                        if (
                                            not old_file
                                            or int(
                                                old_file.get("size") or 0
                                            ) != old_size
                                        ):
                                            raise RuntimeError(
                                                "SAFETY ANCHOR CHANGED "
                                                "after dead cleanup"
                                            )

                                        txn["dead_retry_count"] = (
                                            retry_count + 1
                                        )
                                        txn["status"] = (
                                            "dead_removed_retry_pending"
                                        )
                                        txn["dead_removed_at"] = int(
                                            time.time()
                                        )
                                        txn["failed_download_id"] = (
                                            download_id
                                        )
                                        txn["failed_queue_id"] = queue_id

                                        # Clear active ownership. The next
                                        # optimizer grab must establish new
                                        # ownership itself.
                                        txn.pop("download_id", None)
                                        txn.pop("queue_id", None)
                                        txn.pop("bound_at", None)

                                        changed = True

                                        print(
                                            "[radarr-fallback] DEAD REMOVED "
                                            "SAFELY - retry pending:",
                                            movie_id,
                                            "attempt",
                                            retry_count + 1
                                        )

                                        continue
                                    else:
                                        print(
                                            "[radarr-fallback] torrent "
                                            "has activity:",
                                            movie_id,
                                            download_id
                                        )

                                except Exception as exc:
                                    print(
                                        "[radarr-fallback] Deluge health "
                                        "check failed - keeping torrent:",
                                        movie_id,
                                        exc
                                    )

                            if row_status != "completed":
                                continue

                            if str(row.get("trackedDownloadState") or "").lower() != "importpending":
                                continue

                            if int(row.get("sizeleft") or 0) != 0:
                                continue

                            download_id = str(row.get("downloadId") or "")

                            if not download_id:
                                continue

                            files = radarr_get(
                                "/moviefile?movieId=%d" % movie_id
                            )

                            old_file = next(
                                (
                                    f for f in files
                                    if int(f.get("id") or 0)
                                    == old_file_id
                                ),
                                None
                            )


                            old_relative_path = str(
                                txn.get(
                                    "old_relative_path"
                                ) or ""
                            ).strip()


                            # ------------------------------------------------
                            # RADARR MOVIEFILE-ID CHURN RECOVERY
                            #
                            # The physical old file may be unchanged while
                            # Radarr deletes/recreates its DB row with a new ID.
                            #
                            # Rebind ONLY from exact persisted path + byte size.
                            # Size alone is deliberately insufficient here.
                            # ------------------------------------------------

                            old_id_valid = (
                                old_file is not None
                                and int(
                                    old_file.get("size") or 0
                                ) == old_size
                            )


                            if not old_id_valid:

                                rebound_candidates = []

                                if old_relative_path:

                                    rebound_candidates = [
                                        f
                                        for f in files
                                        if (
                                            str(
                                                f.get(
                                                    "relativePath"
                                                ) or ""
                                            ).strip()
                                            == old_relative_path
                                            and int(
                                                f.get("size") or 0
                                            ) == old_size
                                        )
                                    ]


                                if len(
                                    rebound_candidates
                                ) == 1:

                                    old_file = (
                                        rebound_candidates[0]
                                    )

                                    rebound_id = int(
                                        old_file.get("id")
                                        or 0
                                    )


                                    if rebound_id <= 0:
                                        print(
                                            "[radarr-import] exact old file "
                                            "rebind has no file id; refusing:",
                                            movie_id
                                        )
                                        continue


                                    print(
                                        "[radarr-import] old movieFile id "
                                        "rebound safely:",
                                        movie_id,
                                        old_file_id,
                                        "->",
                                        rebound_id
                                    )


                                    old_file_id = rebound_id

                                    txn[
                                        "old_file_id"
                                    ] = rebound_id

                                    txn[
                                        "old_file_id_rebound_at"
                                    ] = int(
                                        time.time()
                                    )

                                    changed = True


                                else:

                                    print(
                                        "[radarr-import] old file identity "
                                        "changed; exact path+size rebind "
                                        "not proven:",
                                        movie_id
                                    )

                                    continue


                            if int(
                                old_file.get("size") or 0
                            ) != old_size:

                                print(
                                    "[radarr-import] old file size "
                                    "changed; refusing:",
                                    movie_id
                                )

                                continue


                            # Legacy transactions may predate persisted path.
                            # Learn it only while the ORIGINAL old_file_id
                            # is still authoritative. Once that ID vanished,
                            # path recovery is left to the stricter physical
                            # reconciliation/self-heal path.
                            if not old_relative_path:

                                old_relative_path = str(
                                    old_file.get(
                                        "relativePath"
                                    ) or ""
                                ).strip()


                                if old_relative_path:

                                    txn[
                                        "old_relative_path"
                                    ] = (
                                        old_relative_path
                                    )

                                    changed = True

                            # The import worker must see one and only one
                            # physical root movie before starting an override.
                            # This prevents a hidden/orphan movie file from
                            # competing with the optimizer transaction.
                            folder_clean, folder_state = (
                                radarr_root_movie_file_guard(movie_id)
                            )

                            if not folder_clean:
                                txn["status"] = "folder_dirty"
                                txn["folder_issue"] = folder_state
                                changed = True
                                print(
                                    "[radarr-import] FOLDER DIRTY before import; "
                                    "OLD FILE KEPT:",
                                    movie_id,
                                    json.dumps(
                                        folder_state,
                                        ensure_ascii=False
                                    )
                                )
                                continue

                            items = radarr_get(
                                "/manualimport?downloadId=%s&movieId=%d"
                                "&filterExistingFiles=false"
                                % (
                                    urllib.parse.quote(download_id),
                                    movie_id
                                )
                            )

                            usable = []
                            blocked_rejections = []

                            # One central fail-closed classifier is shared by
                            # dashboard, repair path and automatic worker.
                            for item in items if isinstance(items, list) else []:
                                rejections = item.get("rejections") or []
                                safe, _accepted, blocked = (
                                    radarr_rejections_are_optimizer_safe(
                                        rejections
                                    )
                                )

                                if safe and item.get("path"):
                                    usable.append(item)
                                elif blocked:
                                    blocked_rejections.extend(blocked)

                            if len(usable) != 1:
                                print(
                                    "[radarr-import] safe candidate count:",
                                    len(usable),
                                    "movie:",
                                    movie_id,
                                    "blocked:",
                                    json.dumps(
                                        blocked_rejections,
                                        ensure_ascii=False
                                    )
                                )
                                continue

                            item = usable[0]

                            if not item.get("path"):
                                continue

                            # ManualImport item size is the actual video file
                            # Radarr will register. Queue size can include sidecars.
                            new_size = int(
                                item.get("size")
                                or row.get("size")
                                or approved_size
                                or 0
                            )

                            if new_size <= 0 or new_size >= old_size:
                                print(
                                    "[radarr-import] final size check refused:",
                                    movie_id
                                )
                                continue

                            # Re-check the REAL downloaded size, not merely
                            # the indexer's advertised size.
                            actual_saving = (
                                (old_size - new_size) / old_size
                            ) * 100.0

                            minimum, maximum, _ = app_controls("radarr")

                            if (
                                actual_saving < minimum
                                or actual_saving > maximum
                            ):
                                print(
                                    "[radarr-import] actual saving %.2f%% "
                                    "outside %.1f%%-%.1f%%; OLD FILE KEPT:"
                                    % (
                                        actual_saving,
                                        minimum,
                                        maximum
                                    ),
                                    movie_id
                                )
                                txn["status"] = "failed"
                                changed = True
                                continue

                            payload = {
                                "name": "ManualImport",
                                "files": [{
                                    "path": item.get("path"),
                                    "folderName": item.get("folderName"),
                                    "quality": item.get("quality"),
                                    "languages": (
                                        item.get("languages")
                                        or row.get("languages")
                                        or []
                                    ),
                                    "releaseGroup": item.get("releaseGroup"),
                                    "indexerFlags": (
                                        item.get("indexerFlags") or 0
                                    ),
                                    "downloadId": download_id,
                                    "movieId": movie_id
                                }],
                                "importMode": "copy"
                            }

                            result = radarr_request(
                                "/command",
                                method="POST",
                                payload=payload
                            )

                            command_id = int(result.get("id") or 0)

                            if not command_id:
                                continue

                            txn["download_id"] = download_id
                            txn["command_id"] = command_id
                            txn["status"] = "importing"
                            txn["actual_download_size"] = new_size

                            changed = True

                            print(
                                "[radarr-import] import started:",
                                movie_id,
                                "command",
                                command_id
                            )

                        elif status == "importing":
                            command_id = int(
                                txn.get("command_id") or 0
                            )

                            if not command_id:
                                continue

                            command = radarr_get(
                                "/command/%d" % command_id
                            )

                            command_status = str(
                                command.get("status") or ""
                            ).lower()

                            command_result = str(
                                command.get("result") or ""
                            ).lower()

                            if command_status in ("failed", "aborted"):
                                txn["status"] = "failed"
                                changed = True
                                print(
                                    "[radarr-import] import failed; "
                                    "OLD FILE KEPT:",
                                    movie_id
                                )
                                continue

                            # Radarr can finish/register a ManualImport while
                            # leaving the command stuck at "started". Warcraft
                            # proved that command state alone is not authoritative.
                            #
                            # Explicit failure/abort remains a hard failure.
                            # Otherwise verify the REAL registered movie file
                            # below. Only verified replacement state may trigger
                            # cleanup of the exact recorded old_file_id.
                            if (
                                command_status == "completed"
                                and command_result not in (
                                    "successful",
                                    "success"
                                )
                            ):
                                txn["status"] = "failed"
                                changed = True
                                print(
                                    "[radarr-import] unsuccessful import; "
                                    "OLD FILE KEPT:",
                                    movie_id
                                )
                                continue

                            # Do not trust movie.movieFile here. Radarr can
                            # temporarily have both old and new files registered.
                            # Identify the replacement from ALL registered files
                            # using the exact expected downloaded size.
                            files = radarr_get(
                                "/moviefile?movieId=%d" % movie_id
                            )

                            expected_size = int(
                                txn.get("actual_download_size")
                                or txn.get("approved_size")
                                or 0
                            )

                            replacements = [
                                f for f in files
                                if int(f.get("id") or 0) != old_file_id
                                and int(f.get("size") or 0) > 0
                                and int(f.get("size") or 0) < old_size
                                and (
                                    not expected_size
                                    or int(f.get("size") or 0)
                                    == expected_size
                                )
                            ]

                            if len(replacements) != 1:
                                print(
                                    "[radarr-import] replacement verification "
                                    "waiting; candidate count:",
                                    len(replacements),
                                    "movie:",
                                    movie_id
                                )
                                continue

                            current = replacements[0]
                            new_file_id = int(current.get("id") or 0)
                            new_size = int(current.get("size") or 0)

                            if (
                                not new_file_id
                                or not new_size
                                or new_size >= old_size
                            ):
                                txn["status"] = "failed"
                                changed = True
                                print(
                                    "[radarr-import] imported file failed "
                                    "final size check; OLD FILE KEPT:",
                                    movie_id
                                )
                                continue

                            old_exists = any(
                                int(f.get("id") or 0) == old_file_id
                                for f in files
                            )

                            new_exists = any(
                                int(f.get("id") or 0) == new_file_id
                                for f in files
                            )

                            if not new_exists:
                                continue

                            if old_exists:
                                radarr_request(
                                    "/moviefile/%d" % old_file_id,
                                    method="DELETE"
                                )

                            files = radarr_get(
                                "/moviefile?movieId=%d" % movie_id
                            )

                            if any(
                                int(f.get("id") or 0) == old_file_id
                                for f in files
                            ):
                                print(
                                    "[radarr-import] old file still exists:",
                                    movie_id
                                )
                                continue

                            # Do not clear a transaction merely because the
                            # expected new DB row exists. Prove that the movie
                            # folder itself contains exactly one root video and
                            # that Radarr owns that exact replacement. This is
                            # the Gremlins/Hellboy/HTTYD safety invariant.
                            folder_clean, folder_state = (
                                radarr_root_movie_file_guard(
                                    movie_id,
                                    expected_file_id=new_file_id
                                )
                            )

                            if not folder_clean:
                                new_relative_path = str(
                                    current.get("relativePath") or ""
                                ).strip()

                                healed, heal_state = (
                                    radarr_self_heal_owned_old_copy(
                                        movie_id,
                                        txn,
                                        expected_new_file_id=new_file_id,
                                        expected_new_size=new_size,
                                        expected_new_relative_path=(
                                            new_relative_path
                                        ),
                                    )
                                )

                                if healed:
                                    new_file_id = int(
                                        heal_state.get("new_file_id")
                                        or new_file_id
                                    )
                                    txn["self_healed_at"] = int(
                                        time.time()
                                    )
                                    txn["self_heal"] = heal_state
                                    changed = True

                                    print(
                                        "[radarr-import] SELF-HEAL SUCCESS:",
                                        movie_id,
                                        json.dumps(
                                            heal_state,
                                            ensure_ascii=False
                                        )
                                    )
                                else:
                                    txn["status"] = "folder_dirty"
                                    txn["folder_issue"] = folder_state
                                    txn["self_heal"] = heal_state
                                    txn["verified_new_file_id"] = (
                                        new_file_id
                                    )
                                    txn["verified_new_size"] = new_size
                                    changed = True

                                    print(
                                        "[radarr-import] FOLDER DIRTY after import; "
                                        "self-heal refused; transaction retained:",
                                        movie_id,
                                        json.dumps(
                                            heal_state,
                                            ensure_ascii=False
                                        )
                                    )
                                    continue

                            txn.pop("folder_issue", None)

                            print(
                                "[radarr-import] SUCCESS:",
                                movie_id,
                                "%.2f GiB -> %.2f GiB"
                                % (
                                    old_size / 1073741824,
                                    new_size / 1073741824
                                )
                            )

                            pending.pop(movie_key, None)
                            changed = True

                    except Exception as exc:
                        print(
                            "[radarr-import] transaction error %s: %s"
                            % (movie_key, exc)
                        )

                if changed:
                    state["pending_replacements"] = pending
                    save_radarr_state(state)

                # Dead-download retries are deliberately launched only on a
                # later worker cycle, after removal state has been persisted.
                retry_jobs = [
                    (key, txn)
                    for key, txn in pending.items()
                    if str(txn.get("status") or "")
                    == "dead_removed_retry_pending"
                ]

                for retry_key, retry_txn in retry_jobs:
                    movie_id = int(
                        retry_txn.get("movie_id") or retry_key
                    )
                    old_file_id = int(
                        retry_txn.get("old_file_id") or 0
                    )
                    old_size = int(
                        retry_txn.get("old_size") or 0
                    )
                    retry_count = int(
                        retry_txn.get("dead_retry_count") or 0
                    )

                    if retry_count <= 0 or retry_count >= 3:
                        continue

                    # The failed queue/hash must already be gone.
                    failed_download_id = str(
                        retry_txn.get("failed_download_id") or ""
                    ).strip().upper()

                    if not failed_download_id:
                        continue

                    if any(
                        str(
                            row.get("downloadId") or ""
                        ).strip().upper() == failed_download_id
                        for row in queue_records()
                    ):
                        continue

                    # Original movie file remains our safety anchor.
                    files = radarr_get(
                        "/moviefile?movieId=%d" % movie_id
                    )

                    old_file = next(
                        (
                            f for f in files
                            if int(f.get("id") or 0)
                            == old_file_id
                        ),
                        None
                    )

                    if (
                        not old_file
                        or int(old_file.get("size") or 0)
                        != old_size
                    ):
                        print(
                            "[radarr-fallback] retry refused; "
                            "original file changed:",
                            movie_id
                        )
                        continue

                    # Clear only the dead torrent's retention job. The
                    # replacement grab will create a fresh exact-hash job.
                    state.setdefault("tracker_jobs", {}).pop(
                        str(movie_id),
                        None
                    )
                    save_radarr_state(state)

                    started = run_optimizer(
                        True,
                        app="radarr",
                        searches_per_run=None,
                        target_movie_id=movie_id,
                        manual_target=True
                    )

                    if started:
                        print(
                            "[radarr-fallback] targeted retry launched:",
                            movie_id,
                            "attempt",
                            retry_count + 1
                        )
                        break

            state = load_state()

            # RADARR NATIVE IMPORT RECONCILE HOOK
            if _reconcile_radarr_native_imported_replacements(state):
                save_radarr_state(state)

            if _process_tracker_jobs(
                "radarr",
                state,
            ):
                save_radarr_state(
                    state
                )

        except Exception as exc:
            print("[radarr-import] worker error:", exc)

        time.sleep(15)



# ============================================================
# RADARR NATIVE IMPORT RECONCILE START
# ============================================================

def _radarr_native_reconcile_title(value):
    value = str(value or "").lower()

    cleaned = "".join(
        ch if ch.isalnum() else " "
        for ch in value
    )

    return " ".join(
        cleaned.split()
    )


def _radarr_native_import_is_proven(
    movie_id,
    txn,
    new_file,
):
    """
    Prove that Radarr has already imported THIS pending optimizer replacement.

    This is deliberately fail-closed.

    Strongest proof:
      exact optimizer download hash in Radarr import history.

    Safe fallback:
      exact imported byte size + matching approved release title.

    The registered/current file must also be within the expected downloaded
    size envelope before this helper is ever called.
    """

    new_size = int(
        new_file.get("size") or 0
    )

    if new_size <= 0:
        return False, {
            "reason": "new registered file has no size"
        }


    expected_download_id = str(
        txn.get("download_id") or ""
    ).strip().upper()

    approved_title = (
        _radarr_native_reconcile_title(
            txn.get("approved_title")
        )
    )


    try:
        history = radarr_get(
            "/history"
            "?page=1"
            "&pageSize=100"
            "&sortKey=date"
            "&sortDirection=descending"
            "&movieId=%d"
            "&includeMovie=false"
            % int(movie_id)
        )
    except Exception as exc:
        return False, {
            "reason": "cannot inspect Radarr import history",
            "error": str(exc),
        }


    if isinstance(history, dict):
        records = history.get("records") or []
    elif isinstance(history, list):
        records = history
    else:
        records = []


    for event in records:

        if (
            str(event.get("eventType") or "")
            != "downloadFolderImported"
        ):
            continue

        event_movie_id = int(
            event.get("movieId") or 0
        )

        if event_movie_id != int(movie_id):
            continue


        data = event.get("data") or {}

        try:
            event_size = int(
                data.get("size") or 0
            )
        except (TypeError, ValueError):
            event_size = 0


        # When Radarr exposes exact imported byte size,
        # it must agree with the candidate.
        if (
            event_size > 0
            and event_size != new_size
        ):
            continue


        event_download_id = str(
            data.get("downloadId") or ""
        ).strip().upper()


        # Exact torrent ownership is the strongest evidence.
        if (
            expected_download_id
            and event_download_id
        ):

            if (
                event_download_id
                == expected_download_id
            ):
                return True, {
                    "proof": "exact downloadId",
                    "event_size": event_size,
                }

            continue


        source_title = (
            _radarr_native_reconcile_title(
                event.get("sourceTitle")
            )
        )


        # Radarr may strip release-group suffixes such as [QxR].
        # Allow prefix/subset equivalence only after movie + size already match.
        if (
            approved_title
            and source_title
            and (
                source_title in approved_title
                or approved_title in source_title
            )
        ):
            return True, {
                "proof": "history title + imported size",
                "event_size": event_size,
                "source_title": event.get(
                    "sourceTitle"
                ),
            }


    return False, {
        "reason": (
            "no matching optimizer-owned "
            "downloadFolderImported history"
        )
    }


def _radarr_native_size_matches(
    txn,
    new_size,
):
    """
    actual_download_size is exact when available.

    For older/stuck transactions only approved_size may exist. Torrent size
    can include small sidecars, so allow a tightly capped difference:
      max 64 MiB
      normally 2 percent
      never more than 256 MiB
    """

    new_size = int(new_size or 0)

    if new_size <= 0:
        return False


    actual_size = int(
        txn.get("actual_download_size") or 0
    )

    if actual_size > 0:
        return new_size == actual_size


    approved_size = int(
        txn.get("approved_size") or 0
    )

    if approved_size <= 0:
        return False


    tolerance = max(
        64 * 1024 ** 2,
        min(
            256 * 1024 ** 2,
            int(approved_size * 0.02)
        )
    )


    return (
        abs(new_size - approved_size)
        <= tolerance
    )


def _reconcile_radarr_native_imported_replacements(
    state,
):
    """
    Recover optimizer transactions where Radarr imported the replacement
    itself and its queue row disappeared before Smart Optimizer entered the
    normal 'importing' verification path.

    Nothing is deleted directly here.

    When an exact old duplicate exists, deletion is delegated to the existing
    radarr_self_heal_owned_old_copy(), which performs its own physical,
    byte-size, recycle-bin and Radarr-registration safety checks.
    """

    # Never compete with an optimizer process writing the same state.
    with job_lock:
        if jobs.get(
            "radarr",
            {}
        ).get("running"):
            return False


    pending = state.setdefault(
        "pending_replacements",
        {}
    )

    if not pending:
        return False


    try:
        queue_rows = queue_records()
    except Exception as exc:

        print(
            "[radarr-reconcile] queue inspection failed; "
            "no reconciliation:",
            exc
        )

        return False


    changed = False
    now = int(time.time())


    for movie_key, txn in list(
        pending.items()
    ):

        if not isinstance(txn, dict):
            continue


        status = str(
            txn.get("status") or ""
        ).strip().lower()


        # Existing normal importing path remains authoritative.
        if status == "importing":
            continue


        if status not in (
            "grabbed",
            "grabbing",
            "folder_dirty",
        ):
            continue


        movie_id = int(
            txn.get("movie_id")
            or movie_key
            or 0
        )

        old_size = int(
            txn.get("old_size") or 0
        )

        if (
            movie_id <= 0
            or old_size <= 0
        ):
            continue


        created = int(
            txn.get("grabbed_at")
            or txn.get("created")
            or now
        )

        age = max(
            0,
            now - created
        )


        # Do not interfere during the normal first moments of a grab.
        if (
            status == "grabbing"
            and age < 120
        ):
            continue

        if (
            status == "grabbed"
            and age < 15
        ):
            continue


        bound_download_id = str(
            txn.get("download_id") or ""
        ).strip().upper()

        bound_queue_id = int(
            txn.get("queue_id") or 0
        )


        # --------------------------------------------------------
        # Exact queue ownership may remain visible even AFTER Radarr
        # has imported the replacement.
        #
        # Never reconcile an active/incomplete download.
        # But a terminal completed row (sizeleft == 0) must not block
        # post-import recovery, provided all stronger import/file proofs
        # below also succeed.
        # --------------------------------------------------------

        owned_queue_rows = [
            row
            for row in queue_rows
            if (
                (
                    bound_queue_id
                    and int(row.get("id") or 0)
                    == bound_queue_id
                )
                or (
                    bound_download_id
                    and int(
                        row.get("movieId") or 0
                    ) == movie_id
                    and str(
                        row.get("downloadId") or ""
                    ).strip().upper()
                    == bound_download_id
                )
            )
        ]


        if owned_queue_rows:

            terminal_rows = []

            for owned_row in owned_queue_rows:

                owned_status = str(
                    owned_row.get("status") or ""
                ).strip().lower()

                try:
                    owned_sizeleft = int(
                        owned_row.get("sizeleft") or 0
                    )
                except (TypeError, ValueError):
                    owned_sizeleft = -1


                if (
                    owned_status == "completed"
                    and owned_sizeleft == 0
                ):
                    terminal_rows.append(
                        owned_row
                    )


            # Every exact owned row must be terminal before recovery.
            if (
                len(terminal_rows)
                != len(owned_queue_rows)
            ):

                continue


        try:
            movie = radarr_get(
                "/movie/%d" % movie_id
            ) or {}

            registered = radarr_get(
                "/moviefile?movieId=%d"
                % movie_id
            ) or []

        except Exception as exc:

            print(
                "[radarr-reconcile] Radarr inspection failed:",
                movie_id,
                exc
            )

            continue


        current = movie.get(
            "movieFile"
        ) or {}

        current_id = int(
            current.get("id")
            or movie.get("movieFileId")
            or 0
        )


        # Some Radarr responses expose only movieFileId.
        if (
            current_id > 0
            and not current.get("size")
        ):

            matches = [
                f for f in registered
                if int(
                    f.get("id") or 0
                ) == current_id
            ]

            if len(matches) == 1:
                current = matches[0]


        new_file_id = int(
            current.get("id") or 0
        )

        new_size = int(
            current.get("size") or 0
        )

        new_relative_path = str(
            current.get("relativePath") or ""
        ).strip()


        if (
            new_file_id <= 0
            or new_size <= 0
            or not new_relative_path
            or new_size >= old_size
        ):
            continue


        if not _radarr_native_size_matches(
            txn,
            new_size,
        ):
            continue


        proven, proof = (
            _radarr_native_import_is_proven(
                movie_id,
                txn,
                current,
            )
        )


        if not proven:
            continue


        try:
            movie_path, physical = (
                _radarr_root_video_details(
                    movie_id
                )
            )
        except Exception as exc:

            print(
                "[radarr-reconcile] physical inspection failed:",
                movie_id,
                exc
            )

            continue


        new_physical = physical.get(
            new_relative_path
        )


        if (
            not new_physical
            or int(
                new_physical.get("size") or 0
            ) != new_size
        ):
            continue


        # --------------------------------------------------------
        # CASE 1:
        # Old physical file is already gone.
        # Rescan once and finalize only when normal folder guard
        # proves exactly one registered/physical new movie remains.
        # --------------------------------------------------------

        if len(physical) == 1:

            try:
                _radarr_rescan_movie(
                    movie_id
                )
            except Exception as exc:

                print(
                    "[radarr-reconcile] clean-folder rescan failed:",
                    movie_id,
                    exc
                )

                continue


            clean, clean_state = (
                radarr_root_movie_file_guard(
                    movie_id,
                    expected_file_id=(
                        new_file_id
                    )
                )
            )


            if clean:

                pending.pop(
                    movie_key,
                    None
                )

                changed = True

                print(
                    "[radarr-reconcile] RECOVERED already-clean "
                    "native import:",
                    movie_id,
                    proof
                )

            continue


        # --------------------------------------------------------
        # CASE 2:
        # Exactly one old physical duplicate should remain.
        # --------------------------------------------------------

        old_relative_path = str(
            txn.get(
                "old_relative_path"
            ) or ""
        ).strip()


        if not old_relative_path:

            old_physical_matches = [
                item
                for name, item
                in physical.items()
                if (
                    name != new_relative_path
                    and int(
                        item.get("size") or 0
                    ) == old_size
                )
            ]


            # Recover old path only from an unambiguous exact-byte
            # physical identity.
            if (
                len(physical) != 2
                or len(
                    old_physical_matches
                ) != 1
            ):
                continue


            recovered_old = (
                old_physical_matches[0]
            )

            recovered_name = str(
                recovered_old.get("name")
                or ""
            ).strip()


            if not recovered_name:
                continue


            txn[
                "old_relative_path"
            ] = recovered_name

            old_relative_path = (
                recovered_name
            )

            changed = True

            print(
                "[radarr-reconcile] recovered exact old path:",
                movie_id,
                old_relative_path,
                old_size
            )


        old_physical = physical.get(
            old_relative_path
        )


        if (
            len(physical) != 2
            or not old_physical
            or int(
                old_physical.get("size")
                or 0
            ) != old_size
        ):
            continue


        # Existing self-heal deliberately ignores stale old_file_id and
        # re-identifies the old file after a Radarr rescan by exact byte size.
        healed, heal_state = (
            radarr_self_heal_owned_old_copy(
                movie_id,
                txn,
                expected_new_file_id=(
                    new_file_id
                ),
                expected_new_size=(
                    new_size
                ),
                expected_new_relative_path=(
                    new_relative_path
                ),
            )
        )


        if not healed:

            reason = str(
                (heal_state or {}).get(
                    "reason"
                )
                or "self-heal refused"
            )


            # Persist a changed reason once, rather than rewriting
            # the JSON every 15 seconds forever.
            if (
                txn.get(
                    "reconcile_last_reason"
                )
                != reason
            ):

                txn[
                    "reconcile_last_reason"
                ] = reason

                changed = True

                print(
                    "[radarr-reconcile] safe self-heal refused:",
                    movie_id,
                    reason
                )

            continue


        resolved_new_id = int(
            (heal_state or {}).get(
                "new_file_id"
            )
            or new_file_id
        )


        clean, clean_state = (
            radarr_root_movie_file_guard(
                movie_id,
                expected_file_id=(
                    resolved_new_id
                )
            )
        )


        if not clean:

            print(
                "[radarr-reconcile] self-heal completed but "
                "final folder proof is not clean:",
                movie_id,
                json.dumps(
                    clean_state,
                    ensure_ascii=False
                )
            )

            continue


        pending.pop(
            movie_key,
            None
        )

        changed = True


        print(
            "[radarr-reconcile] NATIVE IMPORT RECOVERED + "
            "OLD COPY RECYCLED:",
            movie_id,
            "%.2f GiB -> %.2f GiB"
            % (
                old_size / 1073741824,
                new_size / 1073741824
            ),
            proof
        )


    return changed


# ============================================================
# RADARR NATIVE IMPORT RECONCILE END
# ============================================================


def completed_upgrades(records):
    """Pair Upgrade deletion -> subsequent import for the same movie.

    This reports observed Radarr history, not predicted optimizer savings.
    """
    pending = {}
    upgrades = []
    # API records are newest first; process oldest first.
    for event in reversed(records):
        movie_id = event.get("movieId")
        etype = event.get("eventType")
        data = event.get("data") or {}
        if etype == "movieFileDeleted" and data.get("reason") == "Upgrade":
            try:
                pending[movie_id] = {
                    "old": int(data.get("size") or 0),
                    "deleted": event.get("date"),
                    "old_path": event.get("sourceTitle") or "",
                }
            except (TypeError, ValueError):
                pass
        elif etype == "downloadFolderImported" and movie_id in pending:
            try:
                new_size = int(data.get("size") or 0)
            except (TypeError, ValueError):
                new_size = 0
            old = pending.pop(movie_id)
            if old["old"] > 0 and new_size > 0:
                upgrades.append({
                    "movie_id": movie_id,
                    "date": event.get("date") or "",
                    "title": event.get("sourceTitle") or ("Movie ID %s" % movie_id),
                    "old": old["old"],
                    "new": new_size,
                    "saved": old["old"] - new_size,
                })
    return sorted(upgrades, key=lambda x: x["date"], reverse=True)


def gib(n):
    return n / (1024.0 ** 3)


def search_count(app):
    state = load_state() if app == "radarr" else load_sonarr_state()
    today = time.strftime("%Y-%m-%d")
    return int((state.get("daily") or {}).get(today, {}).get("searches", 0))

def manual_status(app):
    with job_lock:
        snap = dict(jobs[app])
    searched = int(snap.get("display_searched") or 0) if snap.get("started") else 0
    requested = int(snap.get("requested") or 0)
    if not snap.get("started"):
        state, detail = "idle", ""
    elif snap.get("running"):
        state, detail = ("stopping" if snap.get("stopped") else "running"), ""
    elif snap.get("stopped"):
        state, detail = "stopped", ""
    elif snap.get("returncode") not in (None, 0):
        state, detail = "failed", "Optimizer exited with code %s" % snap.get("returncode")
    else:
        state = "finished"
        detail = "No eligible items" if requested and searched == 0 else ""
    display_searched = int(snap.get("display_searched") or 0)
    display_item = str(snap.get("display_item") or "")
    if not snap.get("running") and not display_item:
        display_item = str(snap.get("last") or display_item)
    return {"state": state, "searched": display_searched if snap.get("started") else searched,
            "grabbed": display_searched,
            "requested": requested, "running": bool(snap.get("running")), "detail": detail,
            "current": str(snap.get("current") or "") if snap.get("running") else "",
            "last": display_item,
            "started": snap.get("started"),
            "finished": snap.get("finished"),
            "returncode": snap.get("returncode")}

def run_optimizer(
    live,
    app="radarr",
    searches_per_run=None,
    daily_extra=0,
    target_movie_id=None,
    target_series_id=None,
    target_episode_id=None,
    manual_target=False
):
    with job_lock:
        other = "sonarr" if app == "radarr" else "radarr"
        if jobs[app]["running"] or jobs[other]["running"]: return False
        start = search_count(app)
        if daily_extra: add_daily_extra(app, daily_extra)
        requested = (
            1
            if target_movie_id or target_series_id or target_episode_id
            else int(searches_per_run or 0)
        )
        jobs[app].update(running=True, requested=requested, start=start, proc=None, stopped=False, started=time.time(), finished=None, output="", current="", last="", display_searched=0, display_item="", returncode=None)
    def worker():
        script = OPTIMIZER if app == "radarr" else SONARR_OPTIMIZER
        cmd = ["python3", "-u", script] + (["--live"] if live else [])
        env = os.environ.copy()
        if manual_target:
            env["SMART_OPTIMIZER_MANUAL_TARGET"] = "1"
        env["SMART_OPTIMIZER_CONTROL"] = CONTROL_FILE
        rcfg, scfg = connection("radarr"), connection("sonarr")
        env["RADARR_URL"] = connection_url("radarr"); env["RADARR_KEY"] = rcfg["api_key"]
        env["SONARR_URL"] = connection_url("sonarr"); env["SONARR_KEY"] = scfg["api_key"]
        env["RADARR_DAILY_SEARCH_BUDGET"] = str(daily_search_budget("radarr"))
        env["SONARR_DAILY_SEARCH_BUDGET"] = str(daily_search_budget("sonarr"))

        if app == "radarr" and target_movie_id:
            env["SMART_OPTIMIZER_MOVIE_ID"] = str(
                int(target_movie_id)
            )
            env["SMART_OPTIMIZER_TARGET_GRABS"] = "1"
            env["RADARR_SEARCHES_PER_RUN"] = "1"

        if app == "sonarr" and target_series_id:
            env["SMART_OPTIMIZER_SERIES_ID"] = str(
                int(target_series_id)
            )
            env["SMART_OPTIMIZER_TARGET_GRABS"] = "1"
            env["SONARR_SEARCHES_PER_RUN"] = "100"

        if app == "sonarr" and target_episode_id:
            env["SMART_OPTIMIZER_EPISODE_ID"] = str(
                int(target_episode_id)
            )
            env["SMART_OPTIMIZER_TARGET_GRABS"] = "1"
            env["SONARR_SEARCHES_PER_RUN"] = "1"

        if searches_per_run:
            # Manual number = successful upgrades wanted.
            env["SMART_OPTIMIZER_TARGET_GRABS"] = str(searches_per_run)

            # Do NOT stop merely because N items were searched.
            # The optimizer's existing DAILY budget remains the real
            # search ceiling.
            env[
                "RADARR_SEARCHES_PER_RUN"
                if app == "radarr"
                else "SONARR_SEARCHES_PER_RUN"
            ] = "100000"
        output = ""
        returncode = None
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env, start_new_session=True)
            with job_lock:
                jobs[app]["proc"] = proc; stop_now = jobs[app]["stopped"]
            if stop_now: os.killpg(proc.pid, signal.SIGTERM)
            chunks = []
            for line in proc.stdout:
                chunks.append(line)
                if len(chunks) > 2000:
                    chunks = chunks[-1000:]
                clean = line.strip()
                # Sonarr item lines look like: 20. 'Allo 'Allo! S04E01
                # Avoid whitespace-regex escaping issues: locate the SxxExx token,
                # then derive the series title from the text before it.
                match = re.search(r"S([0-9]{2})E([0-9]{2})", clean)
                if app == "sonarr" and match and ". " in clean[:match.start()]:
                    before = clean[:match.start()].strip()
                    title = before.split(". ", 1)[1].strip()
                    current = "%s · S%sE%s" % (title, match.group(1), match.group(2))
                    with job_lock:
                        jobs[app]["current"] = current
                        jobs[app]["last"] = current
                elif app == "radarr" and "NOW CHECKING:" in clean:
                    current = clean.split("NOW CHECKING:", 1)[1].strip()
                    with job_lock:
                        jobs[app]["current"] = current
                        jobs[app]["last"] = current
                # Manual counter tracks successful upgrades only.
                # SEARCH PROGRESS does NOT change this counter.
                if "UPGRADE GRABBED:" in clean:
                    try:
                        progress = clean.split(
                            "UPGRADE GRABBED:", 1
                        )[1].strip()

                        done = int(
                            progress.split("/", 1)[0].strip()
                        )

                        with job_lock:
                            jobs[app]["display_searched"] = done
                            jobs[app]["display_item"] = str(
                                jobs[app].get("current")
                                or jobs[app].get("last")
                                or ""
                            )
                    except (ValueError, IndexError):
                        pass
                with job_lock:
                    jobs[app]["output"] = "".join(chunks)[-MAX_OUTPUT:]
            proc.wait()
            output = "".join(chunks)
            returncode = proc.returncode
        except Exception as exc:
            output = "ERROR: %s" % exc
            returncode = -1
        with job_lock: jobs[app].update(running=False, finished=time.time(), proc=None, output=(output or "")[-MAX_OUTPUT:], returncode=returncode)
    threading.Thread(target=worker, daemon=True).start(); return True

def stop_optimizer(app):
    with job_lock:
        item = jobs[app]
        if not item["running"]: return False
        item["stopped"] = True; proc = item.get("proc")
    if proc and proc.poll() is None:
        try: os.killpg(proc.pid, signal.SIGTERM)
        except Exception:
            try: proc.terminate()
            except Exception: pass
    return True


CSS = """
*{box-sizing:border-box}
:root{color-scheme:dark;--bg:#0b0e13;--panel:#121720;--panel2:#161c26;--line:#242b36;--text:#f3f4f6;--muted:#8993a4;--accent:#7dd3fc;--accent2:#a78bfa;--good:#86efac;--bad:#fda4af;--warn:#fde68a}
html,body{margin:0;min-height:100%;background:var(--bg);color:var(--text)}
body{font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:radial-gradient(circle at 18% -10%,rgba(59,130,246,.10),transparent 32rem),radial-gradient(circle at 90% 0%,rgba(168,85,247,.08),transparent 28rem),var(--bg)}
a{color:inherit}
.shell{max-width:1220px;margin:0 auto;padding:30px 28px 54px}
.topbar{display:flex;align-items:center;justify-content:space-between;gap:20px;margin-bottom:28px}
.brand{display:flex;align-items:center;gap:13px}.mark{width:42px;height:42px;border-radius:12px;display:grid;place-items:center;font-size:20px;font-weight:800;background:linear-gradient(145deg,#2563eb,#7c3aed);box-shadow:inset 0 1px rgba(255,255,255,.22),0 8px 24px rgba(37,99,235,.18)}
.brandcopy h1{margin:0;font-size:1.15rem;letter-spacing:-.025em}.brandcopy div{font-size:.76rem;color:var(--muted);margin-top:2px}
.nav{display:flex;align-items:center;gap:8px}.appswitch{display:flex;gap:6px;padding:4px;border:1px solid var(--line);background:#0e131a;border-radius:11px}.appswitch a{text-decoration:none;padding:7px 12px;border-radius:8px;color:#8f9bad;font-size:.75rem;font-weight:700}.appswitch a:hover{color:#fff;background:#17202c}.appswitch a.active{color:#fff;background:#1d2939}.homewrap{min-height:70vh;display:grid;place-items:center}.homecard{text-align:center;max-width:720px}.homecard h1{font-size:2.25rem;margin:0 0 8px;letter-spacing:-.05em}.homecard p{color:var(--muted);margin:0 0 28px}.chooser{display:grid;grid-template-columns:1fr 1fr;gap:14px}.choice{text-decoration:none;text-align:left;padding:24px;border-radius:16px;border:1px solid var(--line);background:linear-gradient(180deg,var(--panel2),var(--panel));transition:.15s}.choice:hover{transform:translateY(-2px);border-color:#3b4758}.choice b{display:block;font-size:1.15rem;margin-bottom:6px}.choice span{font-size:.78rem;color:var(--muted)}.navchip,.status{height:34px;display:inline-flex;align-items:center;gap:8px;padding:0 11px;border-radius:9px;border:1px solid var(--line);background:#10151d;color:#b8c0cc;font-size:.76rem}
.dot{width:7px;height:7px;border-radius:999px;background:var(--good);box-shadow:0 0 10px rgba(134,239,172,.55)}
.hero{display:flex;justify-content:space-between;align-items:flex-end;gap:24px;margin-bottom:18px}
.hero h2{font-size:1.75rem;line-height:1.1;letter-spacing:-.04em;margin:0 0 7px}.hero p{margin:0;color:var(--muted);font-size:.86rem}
.actions{display:flex;gap:8px;flex-wrap:wrap}.actions form{margin:0}
button{height:36px;padding:0 13px;border-radius:9px;border:1px solid #334155;background:#172033;color:#e5e7eb;font-weight:700;font-size:.78rem;cursor:pointer}
button:hover:not(:disabled){background:#1d2940}button.live{background:#2a1720;border-color:#5f2437;color:#fecdd3}button.live:hover:not(:disabled){background:#351b27}button:disabled{opacity:.38;cursor:not-allowed}
.notice{margin:0 0 16px;padding:10px 12px;border-radius:10px;border:1px solid var(--line);background:#10151d;color:var(--muted);font-size:.78rem}.notice.bad{color:var(--bad)}
.grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-bottom:16px}
.stat{background:linear-gradient(180deg,var(--panel2),var(--panel));border:1px solid var(--line);border-radius:13px;padding:16px;min-height:120px}
.stathead{display:flex;align-items:center;justify-content:space-between;gap:12px;color:#aab3c1;font-size:.75rem}.stathead span:first-child{display:flex;align-items:center;gap:8px}.mini{width:24px;height:24px;border-radius:7px;display:grid;place-items:center;background:#0f141c;border:1px solid #222a35;color:#cbd5e1;font-size:.74rem}
.value{font-size:1.85rem;font-weight:760;letter-spacing:-.045em;margin-top:18px}.good{color:var(--good)}.bad{color:var(--bad)}.muted{color:var(--muted)}.sub{font-size:.73rem;color:var(--muted);margin-top:6px}
.layout{display:grid;grid-template-columns:minmax(0,1.7fr) minmax(280px,.8fr);gap:16px}
.panel{background:linear-gradient(180deg,#141a23,#10151c);border:1px solid var(--line);border-radius:13px;overflow:hidden}
.panel+.panel{margin-top:16px}.layout .panel+.panel{margin-top:0}
.panelhead{display:flex;justify-content:space-between;align-items:flex-start;gap:14px;padding:16px 17px 13px;border-bottom:1px solid var(--line)}.panelhead h3{font-size:.9rem;margin:0;letter-spacing:-.015em}.panelhead p{font-size:.73rem;color:var(--muted);margin:4px 0 0}.badge{font-size:.64rem;padding:5px 7px;border-radius:999px;border:1px solid #2a3340;color:#93a4b8;background:#0e131a;white-space:nowrap}
table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:12px 17px;border-bottom:1px solid #1f2630;font-size:.79rem}th{font-size:.62rem;color:#667085;text-transform:uppercase;letter-spacing:.09em;background:#0f141b}tr:last-child td{border-bottom:0}td:first-child{max-width:520px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.sidecontent{padding:16px}.metricline{display:flex;align-items:center;justify-content:space-between;padding:11px 0;border-bottom:1px solid #202731;font-size:.78rem}.metricline:last-child{border-bottom:0}.metricline span:first-child{color:var(--muted)}.metricline b{font-size:.8rem}
pre{margin:0;white-space:pre-wrap;word-break:break-word;max-height:305px;overflow:auto;background:#0c1117;padding:15px 17px;color:#bbc5d3;font:11.5px/1.55 ui-monospace,SFMono-Regular,Menlo,monospace}

.exclusionpanel{margin:12px 0}
.exclusionpanel summary{cursor:pointer;display:flex;align-items:center;justify-content:space-between;gap:14px;list-style:none;padding:14px 16px}
.exclusionpanel summary::-webkit-details-marker{display:none}
.exclusionpanel summary small{display:block;margin-top:3px;opacity:.65;font-weight:400}
.exclusionbody{padding:0 14px 14px}
.exclusionsearch{margin:2px 0 12px}
.exclusionresults{display:grid;gap:6px;margin-bottom:12px}
.exclusionrow{display:flex;align-items:center;justify-content:space-between;gap:14px;padding:10px 12px;border-top:1px solid rgba(255,255,255,.06)}
.exclusionrow form{margin:0}
.exclusionrecent{margin-top:10px}
.exclusionmore{display:block;text-align:center;text-decoration:none;margin-top:8px}

.searchmodebar{
  display:flex;
  align-items:center;
  margin:10px 0 6px
}

.searchmodebtn{
  min-height:30px;
  padding:0 11px;
  display:inline-flex;
  align-items:center;
  gap:7px;
  border-radius:7px;
  font-size:.78rem;
  font-weight:650
}

.searchmodebtn span{
  opacity:.6;
  font-size:.72rem
}

.exclusionSearchResults{
  display:grid;
  gap:5px;
  margin-top:7px
}

.exclusionSearchResults:empty{
  display:none
}

.exclusionsearchrow{
  min-height:42px;
  display:flex;
  align-items:center;
  justify-content:space-between;
  gap:14px;
  padding:7px 10px;
  border:1px solid rgba(255,255,255,.07);
  border-radius:8px;
  background:rgba(255,255,255,.02)
}

.exclusionsearchrow form{
  margin:0
}

.exclusiontitle{
  min-width:0
}

.exsearchstatus{
  padding:9px 10px;
  opacity:.65
}


/* exclusion mode layout */
.toolbar{
  display:block !important;
}

.toolbar .searchbox{
  width:100%;
}

.exclusionSearchResults{
  width:100%;
  margin-top:8px;
}

.exclusionsearchrow,
.recentexclusionrow{
  width:100%;
  box-sizing:border-box;
  display:grid;
  grid-template-columns:minmax(0,1fr) 128px;
  align-items:center;
  gap:14px;
}

.exclusionsearchrow form,
.recentexclusionrow form{
  width:128px;
  margin:0;
}

.exclusionsearchrow form button,
.recentexclusionrow form button{
  width:100%;
}

.exclusiontitle{
  min-width:0;
  overflow:hidden;
  text-overflow:ellipsis;
  white-space:nowrap;
}

.recentExclusions{
  display:none;
  margin-top:12px;
  border:1px solid rgba(255,255,255,.07);
  border-radius:10px;
  overflow:hidden;
  background:rgba(255,255,255,.015);
}

.recentExclusions.visible{
  display:block;
}

.recentExclusionsHead{
  display:flex;
  justify-content:space-between;
  align-items:center;
  gap:12px;
  padding:10px 12px;
  border-bottom:1px solid rgba(255,255,255,.06);
}

.recentExclusionsHead strong{
  font-size:.78rem;
}

.recentExclusionsHead a{
  font-size:.72rem;
  text-decoration:none;
}

.recentExclusionRows{
  display:grid;
}

.recentexclusionrow{
  padding:8px 10px;
  border-bottom:1px solid rgba(255,255,255,.05);
}

.recentexclusionrow:last-child{
  border-bottom:0;
}

.recentempty{
  padding:12px;
  font-size:.75rem;
  color:var(--muted);
}

@media(max-width:620px){
  .exclusionsearchrow,
  .recentexclusionrow{
    grid-template-columns:minmax(0,1fr) 105px;
  }

  .exclusionsearchrow form,
  .recentexclusionrow form{
    width:105px;
  }
}

.footer{padding-top:22px;text-align:center;font-size:.68rem;color:#4c5667}
.toolbar{display:flex;gap:10px;align-items:center;margin-bottom:16px}.searchbox{position:relative;flex:1}.searchbox input{width:100%;height:40px;border-radius:10px;border:1px solid var(--line);background:#0f141b;color:var(--text);padding:0 14px 0 38px;outline:none;font-size:.8rem}.searchbox input:focus{border-color:#3b82f6;box-shadow:0 0 0 3px rgba(59,130,246,.10)}.searchicon{position:absolute;left:13px;top:10px;color:#64748b}.queueitem{padding:14px 17px;border-bottom:1px solid #1f2630}.queueitem.extra{display:none}.queueitem:last-child{border-bottom:0}.qtop{display:flex;justify-content:space-between;gap:12px;align-items:center}.qtitle{font-size:.8rem;font-weight:650;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.qmeta{font-size:.7rem;color:var(--muted);margin-top:5px}.progress{height:5px;background:#0b1016;border-radius:99px;overflow:hidden;margin-top:10px}.progress span{display:block;height:100%;background:linear-gradient(90deg,#3b82f6,#8b5cf6);border-radius:99px}.attention{color:var(--warn)}.empty{padding:22px 17px;color:var(--muted);font-size:.78rem}.expandbar{width:100%;height:38px;border:0;border-top:1px solid var(--line);border-radius:0;background:#10161e;color:#9aa6b7;font-size:.74rem;box-shadow:none}.expandbar:hover:not(:disabled){transform:none;background:#151c26;color:#e5e7eb}.controlbar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:0 0 16px}.controlbox{display:flex;gap:7px;align-items:center;padding:7px 9px;border:1px solid var(--line);border-radius:10px;background:#10151d}.controlbox label{font-size:.7rem;color:var(--muted)}.controlbox input{width:62px;height:32px;border:1px solid #303947;border-radius:7px;background:#0b1016;color:var(--text);padding:0 8px}.controlbox button{height:32px}.sectiontabs{display:flex;gap:5px;margin-bottom:12px}.tab{font-size:.72rem;padding:6px 9px;border-radius:8px;background:#10151d;border:1px solid var(--line);color:#8e99aa}.tab.active{color:#e5e7eb;background:#17202c}.kpi{font-size:.66rem;color:#667085;text-transform:uppercase;letter-spacing:.08em}
.manualstate{font-size:.78rem;font-weight:700;color:#dbeafe}.stopbtn{border-color:#6b2635;color:#fecdd3}.hint{font-size:.7rem;color:var(--muted)}
.grid.five{grid-template-columns:repeat(5,minmax(0,1fr))}
.dashboard2{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:14px;align-items:start}
.dashboard2 .panel{margin-bottom:0;align-self:start}
.topbar.compact{margin-bottom:16px}
.controlbar.primary{margin-bottom:8px}
.manualstate{display:flex;flex-direction:column;align-items:flex-start;justify-content:center;gap:4px;min-width:260px;line-height:1.25}.runmain{display:block;font-weight:700;white-space:nowrap}.runitem{display:block;font-size:.72rem;color:var(--muted);font-weight:500;white-space:nowrap}.ajaxmsg{font-size:.72rem;color:var(--muted)}.queueitem.extra,.changeextra{display:none}.changes{width:100%;table-layout:fixed}.changes .releasecol{width:45%}.changes .sizecol{width:18%}.changes .changecol{width:19%}.changes th,.changes td{overflow:hidden;text-overflow:ellipsis}.changes th:not(:first-child),.changes td:not(:first-child){white-space:nowrap;text-align:right}.changes td:first-child{white-space:nowrap}
@media(max-width:1100px){.grid.five{grid-template-columns:repeat(3,1fr)}}
@media(max-width:900px){.dashboard2{grid-template-columns:1fr}.grid.five{grid-template-columns:repeat(2,1fr)}}
@media(max-width:900px){.grid{grid-template-columns:repeat(2,1fr)}.layout{grid-template-columns:1fr}.hero{align-items:flex-start;flex-direction:column}.topbar{align-items:flex-start;flex-direction:column}.nav{width:100%;justify-content:space-between}}
.status.bad{color:#ff8b8b}.status.bad .dot{background:#ff5f67;box-shadow:0 0 12px rgba(255,95,103,.55)}
@media(max-width:520px){.shell{padding:22px 14px 40px}.grid{grid-template-columns:1fr}.hero h2{font-size:1.45rem}th,td{padding:11px 12px}}
"""

AJAX_SCRIPT = """<script>
(function(){
 const form=document.querySelector('.manualform'); if(!form)return;
 const app=form.querySelector('input[name="app"]').value;
 const state=document.getElementById('runstate-'+app);
 async function refresh(){
  try{const r=await fetch('/status?app='+app,{cache:'no-store'});const x=await r.json();
   const word=x.state.charAt(0).toUpperCase()+x.state.slice(1);
   const main=x.requested?(word+' · '+x.searched+' / '+x.requested+' upgrades'+(x.detail?' · '+x.detail:'')):'Idle';
   const now=x.running&&x.current?('Now checking: '+x.current):'';
   const last=!x.running&&x.last?('Last upgraded: '+x.last):'';
   const esc=s=>s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
   state.innerHTML='<span class="runmain">'+esc(main)+'</span>'
     +(now?'<span class="runitem">'+esc(now)+'</span>':'')
     +(last?'<span class="runitem">'+esc(last)+'</span>':'');
   form.querySelector('button:not(.stopbtn)').disabled=!!x.running;
   form.querySelector('.stopbtn').disabled=!x.running;
  }catch(e){}
 }
 form.addEventListener('submit',async function(e){
  e.preventDefault(); const submit=e.submitter; const target=(submit&&submit.classList.contains('stopbtn'))?'/stop':'/manual-search';
  try{const r=await fetch(target,{method:'POST',body:new URLSearchParams(new FormData(form)),headers:{'Content-Type':'application/x-www-form-urlencoded'}});
   if(!r.ok){state.textContent='Error · '+r.status; return;} await refresh();
  }catch(e){state.textContent='Connection error';}
 });
 refresh(); setInterval(refresh,2000);
})();
</script>"""

CSS += r"""
.searchmodebtn.active{
  border-color:rgba(255,255,255,.28);
  background:rgba(255,255,255,.11);
  box-shadow:0 0 0 1px rgba(255,255,255,.04) inset;
}
.manualOptimizeButton{
  min-width:92px;
}
.manualOptimizeButton:disabled{
  opacity:.55;
  cursor:wait;
}
.manualOptimizerToast{
  position:fixed;
  right:24px;
  bottom:24px;
  z-index:9999;
  max-width:min(420px,calc(100vw - 48px));
  padding:14px 18px;
  border-radius:12px;
  background:#171b22;
  border:1px solid rgba(255,255,255,.14);
  box-shadow:0 12px 40px rgba(0,0,0,.38);
  opacity:0;
  transform:translateY(12px);
  pointer-events:none;
  transition:opacity .18s ease,transform .18s ease;
  font-weight:600;
}
.manualOptimizerToast.visible{
  opacity:1;
  transform:translateY(0);
}
.manualOptimizerToast.success{
  border-color:rgba(80,210,140,.45);
}
.manualOptimizerToast.error{
  border-color:rgba(255,100,100,.5);
}
"""

def _dead_watchdog_all_queue_records(app):
    getter = radarr_get if app == "radarr" else sonarr_get
    records = []
    page = 1
    page_size = 250

    while True:
        unknown = (
            "includeUnknownMovieItems=true"
            if app == "radarr"
            else "includeUnknownSeriesItems=true"
        )

        data = getter(
            "/queue?page=%d&pageSize=%d&%s"
            % (page, page_size, unknown)
        ) or {}

        batch = data.get("records") or []
        records.extend(batch)

        try:
            total = int(data.get("totalRecords") or len(records))
        except (TypeError, ValueError):
            total = len(records)

        if (
            not batch
            or len(records) >= total
            or len(batch) < page_size
        ):
            break

        page += 1

    return records


def _dead_watchdog_deluge_statuses():
    fields = [
        "name",
        "label",
        "state",
        "progress",
        "total_done",
        "time_added",
        "num_seeds",
        "num_peers",
        "download_payload_rate",
    ]

    try:
        result = _deluge_rpc(
            "core.get_torrents_status",
            [{}, fields],
        )
    except Exception:
        result = _deluge_rpc(
            "core.get_torrents_status",
            [
                {},
                [
                    x
                    for x in fields
                    if x != "label"
                ],
            ],
        )

    return result if isinstance(result, dict) else {}


# Continuous-zero timers are intentionally in-memory. A UI restart resets
# them, which is safer than deleting a torrent too early after a restart.
_dead_watchdog_zero_since = {}


def _dead_watchdog_is_strict_dead(
    status,
    torrent_hash=None,
    minimum_age=300,
):
    """
    True only after a torrent has spent minimum_age seconds continuously in
    Deluge's Downloading state with literally zero transfer/activity evidence.

    Paused/Queued torrents NEVER accumulate this timer. Any data, peers, seeds,
    or download rate resets it. This makes Deluge active-download limits safe.
    """
    key = str(torrent_hash or "").strip().upper()

    if not isinstance(status, dict) or not status:
        if key:
            _dead_watchdog_zero_since.pop(key, None)
        return False

    try:
        state = str(status.get("state") or "").strip().lower()

        zero_now = (
            state == "downloading"
            and float(status.get("progress") or 0) <= 0
            and int(status.get("total_done") or 0) <= 0
            and int(status.get("num_seeds") or 0) <= 0
            and int(status.get("num_peers") or 0) <= 0
            and int(status.get("download_payload_rate") or 0) <= 0
        )
    except (TypeError, ValueError):
        zero_now = False

    if not zero_now:
        if key:
            _dead_watchdog_zero_since.pop(key, None)
        return False

    # The production watchdog always supplies an exact infohash. Fail closed
    # if no identity is available rather than sharing a timer accidentally.
    if not key:
        return False

    now = time.time()
    started = _dead_watchdog_zero_since.setdefault(key, now)

    return (now - started) >= float(minimum_age)


def _dead_watchdog_optimizer_hashes():
    owned = {}

    rad_state = load_state()
    for key, job in (rad_state.get("tracker_jobs") or {}).items():
        download_id = str(
            (job or {}).get("download_id") or ""
        ).strip().upper()

        if download_id:
            owned[download_id] = ("radarr", str(key))

    son_state = load_sonarr_state()
    for key, job in (son_state.get("tracker_jobs") or {}).items():
        download_id = str(
            (job or {}).get("download_id") or ""
        ).strip().upper()

        if download_id:
            owned[download_id] = ("sonarr", str(key))

    return owned


def _dead_watchdog_remove_exact_queue(app, row):
    queue_id = int(row.get("id") or 0)
    download_id = str(row.get("downloadId") or "").strip().upper()

    if app == "radarr":
        media_id = int(row.get("movieId") or 0)
    else:
        media_id = int(row.get("episodeId") or 0)

    if not queue_id or not download_id or not media_id:
        raise RuntimeError("incomplete Arr queue identity")

    current = _dead_watchdog_all_queue_records(app)

    if app == "radarr":
        matches = [
            x for x in current
            if int(x.get("id") or 0) == queue_id
            and int(x.get("movieId") or 0) == media_id
            and str(x.get("downloadId") or "").strip().upper()
            == download_id
        ]
    else:
        matches = [
            x for x in current
            if int(x.get("id") or 0) == queue_id
            and int(x.get("episodeId") or 0) == media_id
            and str(x.get("downloadId") or "").strip().upper()
            == download_id
        ]

    if len(matches) != 1:
        raise RuntimeError("exact Arr queue revalidation failed")

    path = (
        "/queue/%d?removeFromClient=true&blocklist=true"
        % queue_id
    )

    if app == "radarr":
        radarr_request(path, method="DELETE")
    else:
        sonarr_request(path, method="DELETE")

    return media_id, download_id


def _dead_watchdog_research(app, media_id):
    """
    Re-search one exact failed Arr item.

    Existing library file:
        Route through Smart Optimizer so absolute size, codec, audio, HDR/DV
        and source rules are enforced. Monitored/unmonitored does not matter.

    Missing library file:
        There is no current-size baseline to compare against, so leave that
        acquisition to the native Arr missing-media search.
    """
    media_id = int(media_id)

    if app == "radarr":
        movie = radarr_get(
            "/movie/%d" % media_id
        ) or {}

        if (
            bool(movie.get("hasFile"))
            and int(movie.get("movieFileId") or 0) > 0
        ):
            return run_optimizer(
                True,
                app="radarr",
                searches_per_run=None,
                target_movie_id=media_id,
                manual_target=True,
            )

        return radarr_request(
            "/command",
            method="POST",
            payload={
                "name": "MoviesSearch",
                "movieIds": [media_id],
            },
        )

    episode = sonarr_get(
        "/episode/%d" % media_id
    ) or {}

    if (
        bool(episode.get("hasFile"))
        and int(episode.get("episodeFileId") or 0) > 0
    ):
        return run_optimizer(
            True,
            app="sonarr",
            searches_per_run=None,
            target_episode_id=media_id,
            manual_target=True,
        )

    return sonarr_request(
        "/command",
        method="POST",
        payload={
            "name": "EpisodeSearch",
            "episodeIds": [media_id],
        },
    )



# SONARR V3C ZERO-DEAD START

_sonarr_optimizer_zero_since = {}


def _sonarr_optimizer_is_zero_progress_dead(
    status,
    torrent_hash,
    minimum_age=300,
):
    """
    Sonarr optimizer only:

    Active Downloading torrent that remains exactly 0.00%
    with zero payload rate for five continuous minutes is dead.

    Peers/seeds do not cancel the timer.

    Paused/Queued/etc never accumulate time.
    Any non-zero progress or download rate resets the timer.
    """
    key = str(
        torrent_hash
        or ""
    ).strip().upper()


    if (
        not key
        or not isinstance(status, dict)
        or not status
    ):

        if key:
            _sonarr_optimizer_zero_since.pop(
                key,
                None
            )

        return False


    try:

        state = str(
            status.get("state")
            or ""
        ).strip().lower()

        progress = float(
            status.get("progress")
            or 0
        )

        rate = int(
            status.get(
                "download_payload_rate"
            )
            or 0
        )

    except (
        TypeError,
        ValueError,
    ):

        _sonarr_optimizer_zero_since.pop(
            key,
            None
        )

        return False


    zero_now = (
        state == "downloading"
        and progress <= 0.0
        and rate <= 0
    )


    if not zero_now:

        _sonarr_optimizer_zero_since.pop(
            key,
            None
        )

        return False


    now = time.time()

    started = (
        _sonarr_optimizer_zero_since
        .setdefault(
            key,
            now
        )
    )


    return (
        now - started
    ) >= float(
        minimum_age
    )


# SONARR V3C ZERO-DEAD END


def _dead_watchdog_handle_sonarr_optimizer(
    queue_rows,
    deluge_statuses,
):
    """
    Retry exact optimizer-owned Sonarr torrents that never start.

    Only the SAME failed transaction gets the manual-target bypass. The series
    remains permanently one-shot for all normal future automatic passes.
    """
    with job_lock:
        if jobs.get("sonarr", {}).get("running"):
            return False

    state = load_sonarr_state()
    tracker_jobs = state.setdefault("tracker_jobs", {})

    for key, job in list(tracker_jobs.items()):
        download_id = str(
            (job or {}).get("download_id") or ""
        ).strip().upper()

        if not download_id:
            continue

        status = None
        for torrent_hash, torrent_status in deluge_statuses.items():
            if str(torrent_hash or "").strip().upper() == download_id:
                status = torrent_status
                break

        if not _sonarr_optimizer_is_zero_progress_dead(
            status,
            download_id,
            minimum_age=300,
        ):
            continue

        episode_id = int((job or {}).get("media_id") or key)
        queue_id = int((job or {}).get("queue_id") or 0)

        matches = [
            row for row in queue_rows
            if int(row.get("id") or 0) == queue_id
            and int(row.get("episodeId") or 0) == episode_id
            and str(row.get("downloadId") or "").strip().upper()
            == download_id
        ]

        if len(matches) != 1:
            continue

        history = state.setdefault("episodes", {}).setdefault(
            str(episode_id),
            {}
        )
        retry_count = int(history.get("dead_retry_count") or 0)

        # Remove the exact proven-dead optimizer torrent and retry
        # this SAME episode with the next unused qualifying release.
        _dead_watchdog_remove_exact_queue(
            "sonarr",
            matches[0],
        )

        _sonarr_optimizer_zero_since.pop(
            download_id,
            None
        )

        history["dead_retry_count"] = retry_count + 1
        history["last_dead_download_id"] = download_id
        history["last_dead_removed_at"] = int(time.time())

        tracker_jobs.pop(str(key), None)
        save_sonarr_state(state)

        # No artificial Sonarr retry ceiling.
        #
        # The exact dead queue item is removed with blocklist=true,
        # and the release is already present in attempted_releases.
        #
        # Retry this SAME episode until no unused qualifying
        # candidate remains.

        started = run_optimizer(
            True,
            app="sonarr",
            searches_per_run=None,
            target_episode_id=episode_id,
            manual_target=True,
        )

        print(
            "[dead-watchdog] SONARR OPTIMIZER DEAD:",
            episode_id,
            download_id,
            "retry",
            retry_count + 1,
            "started" if started else "not-started",
        )
        return True

    return False


def dead_download_watchdog_snapshot():
    """
    Read-only summary used for live validation before the worker is enabled.
    """
    torrents = _dead_watchdog_deluge_statuses()
    optimizer = _dead_watchdog_optimizer_hashes()

    result = {
        "strict_dead": 0,
        "radarr_queue_owned": 0,
        "sonarr_queue_owned": 0,
        "optimizer_owned": 0,
        "orphan": 0,
    }

    queue_hashes = {}

    for app in ("radarr", "sonarr"):
        for row in _dead_watchdog_all_queue_records(app):
            download_id = str(
                row.get("downloadId") or ""
            ).strip().upper()

            if download_id:
                queue_hashes.setdefault(download_id, []).append((app, row))

    for torrent_hash, status in torrents.items():
        download_id = str(torrent_hash or "").strip().upper()

        if not _dead_watchdog_is_strict_dead(
            status,
            download_id,
        ):
            continue

        result["strict_dead"] += 1

        if download_id in optimizer:
            result["optimizer_owned"] += 1

        owners = queue_hashes.get(download_id) or []

        if not owners:
            result["orphan"] += 1
            continue

        for app, _row in owners:
            result[app + "_queue_owned"] += 1

    return result


def dead_download_watchdog_worker():
    """
    5-minute strict-dead watchdog.

    * Optimizer-owned Radarr is left to the existing Radarr transaction worker.
    * Optimizer-owned Sonarr gets one bounded same-transaction targeted retry.
    * Other exact Radarr/Sonarr queue items are blocklisted, removed from the
      client, and searched again through their native Arr.
    * Orphan/manual torrents that cannot be tied to an exact Arr queue row are
      never guessed from title and are left for the 11-day stale cleanup.
    """
    while True:
        try:
            torrents = _dead_watchdog_deluge_statuses()
            optimizer = _dead_watchdog_optimizer_hashes()

            rad_rows = _dead_watchdog_all_queue_records("radarr")
            son_rows = _dead_watchdog_all_queue_records("sonarr")

            # Sonarr optimizer-owned retries are handled here.
            if _dead_watchdog_handle_sonarr_optimizer(
                son_rows,
                torrents,
            ):
                time.sleep(15)
                continue

            handled = 0

            for app, rows in (
                ("radarr", rad_rows),
                ("sonarr", son_rows),
            ):
                for row in rows:
                    if handled >= 3:
                        break

                    if str(row.get("status") or "").lower() == "completed":
                        continue

                    download_id = str(
                        row.get("downloadId") or ""
                    ).strip().upper()

                    if not download_id:
                        continue

                    # Existing optimizer transaction workers own these.
                    if download_id in optimizer:
                        continue

                    status = None
                    for torrent_hash, torrent_status in torrents.items():
                        if (
                            str(torrent_hash or "").strip().upper()
                            == download_id
                        ):
                            status = torrent_status
                            break

                    if not _dead_watchdog_is_strict_dead(
                        status,
                        download_id,
                    ):
                        continue

                    media_id, exact_hash = _dead_watchdog_remove_exact_queue(
                        app,
                        row,
                    )

                    _dead_watchdog_research(
                        app,
                        media_id,
                    )

                    handled += 1

                    print(
                        "[dead-watchdog] CLEANED + RESEARCH:",
                        app,
                        media_id,
                        exact_hash,
                    )

                if handled >= 3:
                    break

        except Exception as exc:
            print(
                "[dead-watchdog] worker error:",
                exc,
            )

        time.sleep(15)


def sonarr_tracker_retention_worker():
    """
    Label/retain TorrentLeech and clean other exact optimizer-owned torrents.

    Sonarr state is not touched while the UI-launched Sonarr optimizer process
    is running, avoiding concurrent state writers.
    """
    while True:
        try:
            with job_lock:
                running = bool(
                    jobs.get(
                        "sonarr",
                        {}
                    ).get("running")
                )

            if not running:
                state = (
                    load_sonarr_state()
                )

                if _process_tracker_jobs(
                    "sonarr",
                    state,
                ):
                    save_sonarr_state(
                        state
                    )

        except Exception as exc:
            print(
                "[tracker-retention] Sonarr worker error:",
                exc
            )

        time.sleep(15)


def page():
    state = load_state()
    today = time.strftime("%Y-%m-%d")
    used = int((state.get("daily") or {}).get(today, {}).get("searches", 0))
    rule_min, rule_max, extra_today = app_controls("radarr")
    error = ""
    try:
        records = history_records()
        upgrades = completed_upgrades(records)
        queue = queue_health(queue_records())
    except Exception as exc:
        upgrades, queue = [], []
        error = str(exc)
    saved = sum(x["saved"] for x in upgrades)
    positive = sum(1 for x in upgrades if x["saved"] > 0)
    total_before = sum(x["old"] for x in upgrades)
    reduction_pct = (saved / total_before * 100.0) if total_before else 0.0
    attention = [x for x in queue if x["attention"] and x.get("health_kind") != "optimizer_blocked"]
    optimizer_blocked = [x for x in queue if x.get("health_kind") == "optimizer_blocked"]
    last_date = upgrades[0]["date"][:10] if upgrades else "—"
    with job_lock:
        snap = dict(jobs["radarr"])

    rows = ""
    for i, x in enumerate(upgrades[:15]):
        extra = " changeextra" if i >= 5 else ""
        delta = gib(x["saved"])
        cls = "good" if delta >= 0 else "bad"
        rows += "<tr class='filterrow%s' data-search='%s'><td>%s</td><td>%.2f GiB</td><td>%.2f GiB</td><td class='%s'>%+.2f GiB</td></tr>" % (
            extra, html.escape(x["title"].lower(), quote=True), html.escape(x["title"]), gib(x["old"]), gib(x["new"]), cls, delta)
    if not rows:
        rows = "<tr><td colspan='4' class='muted'>No completed upgrade pairs found in the loaded history window.</td></tr>"
    elif len(upgrades) > 5:
        rows += "<tr id='changesExpandRow'><td colspan='4'><button type='button' class='expandbar' id='changesExpand' onclick='toggleChanges()'>Show %d more changes ↓</button></td></tr>" % (min(len(upgrades), 15) - 5)

    qrows = ""
    visible_queue = queue[:4]
    for i, x in enumerate(queue[:15]):
        cls = "attention" if x["attention"] else ""
        note = x["message"] or ("Time left: %s" % x["timeleft"])
        extra = " extra" if i >= 4 else ""
        qrows += """<div class="queueitem filterrow%s" data-search="%s"><div class="qtop"><div class="qtitle">%s</div><div class="%s">%s</div></div><div class="qmeta">%.1f%% · %s</div><div class="progress"><span style="width:%.1f%%"></span></div></div>""" % (
            extra, html.escape(x["title"].lower(), quote=True), html.escape(x["title"]), cls,
            html.escape(str(x.get("health") or x["status"])),
            x["progress"], html.escape(note) + ("""<form method="post" action="/repair-import" style="margin-top:9px"><input type="hidden" name="queue_id" value="%s"><button class="live" type="submit">Try safe import</button></form>""" % html.escape(str(x.get("queue_id") or ""), quote=True) if x.get("health_kind") == "optimizer_blocked" else ""), x["progress"])
    if not qrows:
        qrows = "<div class='empty'>Nothing is currently in Radarr's download queue.</div>"
    elif len(queue) > 4:
        qrows += """<button type="button" class="expandbar" id="queueExpand" onclick="toggleQueue()">Show %d more downloads ↓</button>""" % (min(len(queue), 15) - 4)

    runstat = manual_status("radarr")
    runlabel = "Idle" if not runstat["requested"] else ("%s%s · %d / %d searched" % (runstat["state"].capitalize(), (" · Searching " + runstat["current"]) if runstat.get("running") and runstat.get("current") else "", runstat["searched"], runstat["requested"]))
    actions = """<div class="controlbar primary"><form class="controlbox manualform" method="post" action="/manual-search"><input type="hidden" name="app" value="radarr"><label>Manual search</label><input name="count" type="number" min="1" max="%d" value="0"><button %s>Search</button><button class="stopbtn" formaction="/stop" %s>STOP</button></form><span id="runstate-radarr" class="manualstate">%s</span></div>
<!-- SMART LIVE RULES HOVER CSS -->
<style>
.rules-main-button{
    display:inline-block !important;
    position:relative !important;
    transition:
        background .16s ease,
        border-color .16s ease,
        box-shadow .16s ease,
        color .16s ease !important;
}

/* Radarr RULES:
   same soft glow amount as the normal controls,
   only the colour is Radarr red. */
.radarr-rules-button:hover,
.radarr-rules-button:focus-visible{
    color:#fff !important;
    background:rgba(255,70,55,.14) !important;
    border-color:#ff604f !important;

    box-shadow:
        0 0 10px rgba(255,70,55,.42) !important;

    filter:none !important;
    transform:none !important;
}

/* Sonarr RULES:
   same soft glow amount as the normal controls,
   only the colour is Sonarr blue. */
.sonarr-rules-button:hover,
.sonarr-rules-button:focus-visible{
    color:#fff !important;
    background:rgba(35,190,255,.14) !important;
    border-color:#32c7ff !important;

    box-shadow:
        0 0 10px rgba(35,190,255,.42) !important;

    filter:none !important;
    transform:none !important;
}
</style>
<form class="controlbar" method="post" action="/settings"><input type="hidden" name="app" value="radarr"><div class="controlbox"><label>Downsize</label><input name="min" type="number" min="0" max="100" step="0.1" value="%.1f"><span>–</span><input name="max" type="number" min="0" max="100" step="0.1" value="%.1f"><span>%%</span><button type="submit">Apply</button></div><span class="badge">%d/%d searches · +%d today</span><a class="badge rules-main-button radarr-rules-button" style="text-decoration:none;font-weight:900;font-size:.92rem;padding:8px 17px;letter-spacing:.07em;transition:.18s ease" href="/radarr/rules">RULES</a></form>""" % (MAX_MANUAL, "disabled" if runstat["running"] else "", "" if runstat["running"] else "disabled", html.escape(runlabel), rule_min, rule_max, used, daily_search_budget("radarr") + extra_today, extra_today)
    output = """<div class="sidecontent">
<div class="metricline"><span>Downsize range</span><b>%.1f – %.1f%%</b></div>
<div class="metricline"><span>Resolution policy</span><b>Downsize only</b></div>
<div class="metricline"><span>Daily search budget</span><b>%d</b></div>
<div class="metricline"><span>Temporary extra searches</span><b>%d</b></div>
<div class="metricline"><span>Status</span><b class="%s">%s</b></div>
</div>""" % (
        rule_min,
        rule_max,
        daily_search_budget("radarr"),
        extra_today,
        "good" if not runstat["running"] else "",
        "Running" if runstat["running"] else "Ready"
    )
    status = "Online" if api_online("radarr") else "Offline"
    warning = "" if ENABLE_ACTIONS else "<div class='notice'>Read-only mode is active. Smart retry controls will only be enabled after we validate queue detection and candidate selection.</div>"
    err = ("<div class='notice bad'>Radarr API error: %s</div>" % html.escape(error)) if error else ""

    return """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="theme-color" content="#0b0e13"><title>Smart Optimizer UI · Radarr</title><style>%s

/* SMART ARR DASHBOARD MODERN START */


/* ==========================================================
   PAGE THEME
   ========================================================== */

body.dashboard-page{

    --dash-accent:#28c5ff;
    --dash-accent-rgb:40,197,255;

    margin:0;

    min-height:100vh;

    color:#eef6ff;

    background:

        linear-gradient(
            180deg,
            rgba(1,7,14,.22),
            rgba(1,7,14,.36)
        ),

        url('/radarr-background.png?v=20260923-233901')
        center center / cover
        no-repeat fixed !important;

    position:relative;
}


body.radarr-dashboard{

    --dash-accent:#ff7048;
    --dash-accent-rgb:255,112,72;

}


body.sonarr-dashboard{

    --dash-accent:#28c5ff;
    --dash-accent-rgb:40,197,255;

}


/* dark reading layer */

body.dashboard-page::before{

    content:"";

    position:fixed;

    inset:0;

    z-index:0;

    pointer-events:none;

    background:

        radial-gradient(
            circle at center,
            rgba(0,12,26,.10),
            rgba(0,5,14,.34)
        );

}


/* ==========================================================
   MAIN WIDTH
   ========================================================== */

body.dashboard-page .shell{

    position:relative;

    z-index:1;

    width:
        calc(100vw - 56px) !important;

    max-width:
        1320px !important;

    margin:
        0 auto !important;

    padding-top:
        26px !important;

    padding-bottom:
        50px !important;

}


/* ==========================================================
   TOP HEADER
   ========================================================== */

body.dashboard-page .topbar{

    position:relative;

    padding:
        18px
        20px !important;

    margin-bottom:
        18px !important;

    border-radius:
        20px !important;

    background:

        linear-gradient(
            145deg,
            rgba(10,25,43,.86),
            rgba(5,16,29,.80)
        );

    border:

        1px solid
        rgba(
            var(--dash-accent-rgb),
            .24
        );

    backdrop-filter:
        blur(14px);

    -webkit-backdrop-filter:
        blur(14px);

    box-shadow:

        0 18px 44px
        rgba(0,0,0,.24),

        inset
        0 0 0 1px
        rgba(255,255,255,.018);

}


/* accent line */

body.dashboard-page .topbar::after{

    content:"";

    position:absolute;

    left:22px;

    right:22px;

    bottom:-1px;

    height:2px;

    border-radius:999px;

    background:

        linear-gradient(
            90deg,
            transparent,
            rgba(
                var(--dash-accent-rgb),
                .72
            ),
            transparent
        );

}


/* ==========================================================
   RADARR / SONARR HEADER ICON
   ========================================================== */

body.dashboard-page .topbar .brand{

    display:flex;

    align-items:center;

    gap:14px;

}


body.dashboard-page .topbar .brand::before{

    content:"";

    width:48px;

    height:48px;

    flex:
        0 0 48px;

    display:block;

    background:
        var(--dashboard-icon)
        center center / contain
        no-repeat;

    filter:

        drop-shadow(
            0 8px 14px
            rgba(0,0,0,.28)
        );

}


body.radarr-dashboard{

    --dashboard-icon:
        url('/radarr-icon.png');

}


body.sonarr-dashboard{

    --dashboard-icon:
        url('/sonarr-icon.png');

}


body.dashboard-page .brandcopy h1{

    margin-bottom:
        3px !important;

    font-size:
        1.55rem !important;

    letter-spacing:
        -.025em;

}


body.dashboard-page .brandcopy > div{

    color:
        rgba(210,224,242,.67) !important;

}


/* ==========================================================
   TOP NAV
   ========================================================== */

body.dashboard-page .nav{

    display:flex;

    align-items:center;

    gap:8px !important;

    flex-wrap:wrap;

}


body.dashboard-page .nav .badge{

    min-height:
        36px;

    display:inline-flex;

    align-items:center;

    justify-content:center;

    padding:
        0 13px !important;

    border-radius:
        999px !important;

    text-decoration:none;

    background:
        rgba(6,21,36,.76) !important;

    border:

        1px solid
        rgba(84,136,194,.22) !important;

    transition:

        transform .18s ease,
        border-color .18s ease,
        background .18s ease,
        box-shadow .18s ease;

}


body.dashboard-page .nav .badge:hover{

    transform:
        translateY(-1px);

    border-color:

        rgba(
            var(--dash-accent-rgb),
            .72
        ) !important;

    box-shadow:

        0 0 10px
        rgba(
            var(--dash-accent-rgb),
            .26
        ),

        0 0 22px
        rgba(
            var(--dash-accent-rgb),
            .10
        );

}


/* Radarr nav stays orange */

body.dashboard-page
.nav a[href="/radarr"]:hover,

body.radarr-dashboard
.nav a[href="/radarr"]{

    border-color:
        rgba(255,112,72,.76) !important;

    box-shadow:
        0 0 16px
        rgba(255,94,55,.17);

}


/* Sonarr nav stays blue */

body.dashboard-page
.nav a[href="/sonarr"]:hover,

body.sonarr-dashboard
.nav a[href="/sonarr"]{

    border-color:
        rgba(40,197,255,.80) !important;

    box-shadow:
        0 0 16px
        rgba(0,174,255,.17);

}


/* ==========================================================
   INPUTS / SELECTS
   ========================================================== */

body.dashboard-page input,

body.dashboard-page select{

    color:#f4f9ff !important;

    background:

        rgba(
            3,
            13,
            24,
            .76
        ) !important;

    border:

        1px solid
        rgba(
            81,
            135,
            195,
            .28
        ) !important;

    border-radius:
        10px !important;

    transition:

        border-color .18s ease,
        box-shadow .18s ease,
        background .18s ease;

}


body.dashboard-page input:focus,

body.dashboard-page select:focus{

    outline:none !important;

    border-color:

        rgba(
            var(--dash-accent-rgb),
            .80
        ) !important;

    box-shadow:

        0 0 0 3px
        rgba(
            var(--dash-accent-rgb),
            .07
        ),

        0 0 18px
        rgba(
            var(--dash-accent-rgb),
            .08
        ) !important;

}


/* ==========================================================
   BUTTONS
   ========================================================== */

body.dashboard-page button{

    border-radius:
        10px !important;

    transition:

        transform .18s ease,
        border-color .18s ease,
        background .18s ease,
        box-shadow .18s ease;

}


body.dashboard-page
button:not(:disabled):hover{

    transform:
        translateY(-1px);

    border-color:

        rgba(
            var(--dash-accent-rgb),
            .80
        ) !important;

    box-shadow:

        0 0 9px
        rgba(
            var(--dash-accent-rgb),
            .28
        ),

        0 0 22px
        rgba(
            var(--dash-accent-rgb),
            .10
        ) !important;

}


body.dashboard-page
button:not(:disabled):active{

    transform:
        translateY(0)
        scale(.985);

}


/* ==========================================================
   SEARCH MODE TABS
   ========================================================== */

body.dashboard-page
[data-search-mode]{

    border-radius:
        10px !important;

}


body.dashboard-page
[data-search-mode]:hover,

body.dashboard-page
[data-search-mode].active{

    border-color:

        rgba(
            var(--dash-accent-rgb),
            .78
        ) !important;

    box-shadow:

        0 0 10px
        rgba(
            var(--dash-accent-rgb),
            .20
        ) !important;

}


/* ==========================================================
   SEARCH BAR
   ========================================================== */

body.dashboard-page
input[type="search"]{

    min-height:
        44px !important;

    border-color:

        rgba(
            var(--dash-accent-rgb),
            .48
        ) !important;

}


/* ==========================================================
   STATS
   ========================================================== */

body.dashboard-page .stat{

    border-radius:
        16px !important;

    background:

        linear-gradient(
            145deg,
            rgba(12,28,47,.88),
            rgba(6,17,30,.84)
        ) !important;

    border:

        1px solid
        rgba(
            83,
            136,
            195,
            .16
        ) !important;

    backdrop-filter:
        blur(12px);

    -webkit-backdrop-filter:
        blur(12px);

    box-shadow:

        0 13px 30px
        rgba(0,0,0,.18),

        inset
        0 0 0 1px
        rgba(255,255,255,.014);

    transition:

        transform .18s ease,
        border-color .18s ease,
        box-shadow .18s ease;

}


body.dashboard-page .stat:hover{

    transform:
        translateY(-2px);

    border-color:

        rgba(
            var(--dash-accent-rgb),
            .32
        ) !important;

    box-shadow:

        0 15px 34px
        rgba(0,0,0,.22),

        0 0 18px
        rgba(
            var(--dash-accent-rgb),
            .07
        );

}


body.dashboard-page .stat .value{

    font-size:
        1.8rem !important;

    line-height:
        1.08;

}


/* ==========================================================
   PANELS
   ========================================================== */

body.dashboard-page .panel{

    border-radius:
        18px !important;

    background:

        linear-gradient(
            145deg,
            rgba(11,27,46,.90),
            rgba(5,16,29,.87)
        ) !important;

    border:

        1px solid
        rgba(
            78,
            133,
            194,
            .15
        ) !important;

    backdrop-filter:
        blur(13px);

    -webkit-backdrop-filter:
        blur(13px);

    box-shadow:

        0 17px 40px
        rgba(0,0,0,.22),

        inset
        0 0 0 1px
        rgba(255,255,255,.012);

    transition:

        border-color .18s ease,
        box-shadow .18s ease;

}


body.dashboard-page .panel:hover{

    border-color:

        rgba(
            var(--dash-accent-rgb),
            .27
        ) !important;

}


/* panel heading accent */

body.dashboard-page
.panelhead h3{

    letter-spacing:
        -.015em;

}


/* ==========================================================
   TABLE / RECENT CHANGES
   ========================================================== */

body.dashboard-page table{

    border-collapse:
        separate;

    border-spacing:
        0;

}


body.dashboard-page tbody tr{

    transition:
        background .15s ease;

}


body.dashboard-page tbody tr:hover{

    background:

        rgba(
            var(--dash-accent-rgb),
            .035
        );

}


/* ==========================================================
   METRIC LINES
   ========================================================== */

body.dashboard-page .metricline{

    border-color:
        rgba(255,255,255,.055) !important;

}


/* ==========================================================
   BADGES
   ========================================================== */

body.dashboard-page .badge{

    border-radius:
        999px !important;

}


/* ==========================================================
   RIGHT-SIDE RADAR / QUEUE
   ========================================================== */

body.dashboard-page
.panel progress{

    accent-color:
        var(--dash-accent);

}


/* ==========================================================
   RESPONSIVE
   ========================================================== */

@media(max-width:900px){

    body.dashboard-page .shell{

        width:
            calc(100vw - 24px) !important;

    }


    body.dashboard-page .topbar{

        padding:
            15px !important;

    }


    body.dashboard-page
    .topbar .brand::before{

        width:40px;

        height:40px;

        flex-basis:40px;

    }

}


/* SMART ARR DASHBOARD MODERN END */



/* ==========================================================
   MODERN HISTORY BUTTON
   ========================================================== */

.history-modern{

    display:inline-flex !important;

    align-items:center;

    justify-content:center;

    min-height:30px;

    padding:
        0 12px !important;

    border-radius:
        999px !important;

    color:
        #dce8f5 !important;

    background:
        rgba(5,18,31,.76) !important;

    border:
        1px solid
        rgba(112,154,198,.30) !important;

    text-decoration:
        none !important;

    font-size:
        .76rem !important;

    font-weight:
        800 !important;

    letter-spacing:
        .055em;

    text-transform:
        uppercase;

    box-shadow:

        0 7px 18px
        rgba(0,0,0,.17),

        inset
        0 0 0 1px
        rgba(255,255,255,.015);

    transition:

        transform .18s ease,
        color .18s ease,
        border-color .18s ease,
        background .18s ease,
        box-shadow .18s ease;

}


/* RADARR */

body.radarr-dashboard
.history-modern:hover{

    transform:
        translateY(-1px);

    color:
        #ffffff !important;

    border-color:
        rgba(255,112,72,.92) !important;

    background:

        linear-gradient(
            135deg,
            rgba(74,29,25,.90),
            rgba(34,17,21,.90)
        ) !important;

    box-shadow:

        0 9px 22px
        rgba(0,0,0,.23),

        0 0 9px
        rgba(255,94,54,.42),

        0 0 22px
        rgba(255,74,36,.18) !important;

}


/* SONARR */

body.sonarr-dashboard
.history-modern:hover{

    transform:
        translateY(-1px);

    color:
        #ffffff !important;

    border-color:
        rgba(40,197,255,.94) !important;

    background:

        linear-gradient(
            135deg,
            rgba(7,55,91,.90),
            rgba(5,28,52,.90)
        ) !important;

    box-shadow:

        0 9px 22px
        rgba(0,0,0,.23),

        0 0 9px
        rgba(0,187,255,.44),

        0 0 22px
        rgba(0,145,255,.19) !important;

}


.history-modern:active{

    transform:
        translateY(0)
        scale(.975) !important;

}




/* ==========================================================
   DASHBOARD NAV + SMALL BADGE TYPOGRAPHY
   ========================================================== */


/* ----------------------------------------------------------
   HOME + SETTINGS
   Match Radarr / Sonarr typography
   ---------------------------------------------------------- */

body.dashboard-page
.nav .dashboard-home-link,

body.dashboard-page
.nav .dashboard-settings-link{

    font-size:
        .88rem !important;

    font-weight:
        750 !important;

    letter-spacing:
        0 !important;

    color:
        #eaf3fd !important;

}


/* ----------------------------------------------------------
   ACTIVE / SUMMARY PILLS
   Same typography family as HISTORY
   ---------------------------------------------------------- */

body.dashboard-page
.panelhead .badge{

    font-size:
        .76rem !important;

    font-weight:
        800 !important;

    letter-spacing:
        .055em !important;

    text-transform:
        uppercase !important;

    color:
        #dce8f5 !important;

}


/* slightly stronger pill appearance */

body.dashboard-page
.panelhead .badge:not(.history-modern){

    padding:
        0 11px !important;

    min-height:
        28px;

    display:
        inline-flex;

    align-items:
        center;

    justify-content:
        center;

    border-radius:
        999px !important;

    background:
        rgba(5,18,31,.74) !important;

    border:
        1px solid
        rgba(112,154,198,.28) !important;

    box-shadow:
        0 6px 16px
        rgba(0,0,0,.14);

}


</style></head>
<body class="dashboard-page radarr-dashboard"><div class="shell">
<div class="topbar compact"><div class="brand"><div class="brandcopy"><h1>Radarr Optimizer</h1><div>Find smaller releases for your movies while keeping quality.</div></div></div><div class="nav"><div class="appswitch"><a class="active" href="/radarr">Radarr</a><a href="/sonarr">Sonarr</a></div><span class="status%s"><span class="dot"></span>%s</span></div></div>
%s
%s%s
%s
<div class="toolbar">
<div class="searchbox"><span class="searchicon">⌕</span><input id="librarySearch" autocomplete="off" placeholder="Search releases and current downloads…"></div>
<div id="exclusionSearchResults" class="exclusionSearchResults"></div>
<div id="recentExclusions" class="recentExclusions">
  <div class="recentExclusionsHead">
    <strong>Recently excluded</strong>
    <a id="showAllExclusions" href="#">Show all exclusions →</a>
  </div>
  <div id="recentExclusionRows" class="recentExclusionRows"></div>
</div>
</div>
<div class="grid five">
<div class="stat"><div class="stathead"><span>Storage saved</span></div><div class="value %s">%+.2f GiB</div><div class="sub">Observed across loaded upgrade history</div></div>
<div class="stat"><div class="stathead"><span>Space reductions</span></div><div class="value">%d</div><div class="sub">Observed upgrades that ended smaller</div></div>
<div class="stat"><div class="stathead"><span>Active downloads</span></div><div class="value">%d</div><div class="sub">%d need attention · %d optimizer import blocked</div></div>
<div class="stat"><div class="stathead"><span>Searches today</span></div><div class="value">%d</div><div class="sub">Optimizer state counter</div></div>
<div class="stat"><div class="stathead"><span>Temporary extra today</span></div><div class="value">+%d</div><div class="sub">Resets tomorrow</div></div>
</div>
<div class="layout"><div>
<div class="panel"><div class="panelhead"><div><h3>Recent file changes</h3><p>Observed Radarr upgrade pairs. These are not all necessarily optimizer-triggered.</p></div><a class="badge history-modern" href="/radarr/history">HISTORY</a></div>
<table class="changes"><colgroup><col class="releasecol"><col class="sizecol"><col class="sizecol"><col class="changecol"></colgroup><thead><tr><th>Release</th><th>Before</th><th>After</th><th>Change</th></tr></thead><tbody>%s</tbody></table></div>
<div class="panel"><div class="panelhead"><div><h3>Settings &amp; status</h3><p>Current Radarr configuration and system status.</p></div></div>%s</div>
</div>
<div>
<div class="panel"><div class="panelhead"><div><h3>Download radar</h3><p>Live Radarr queue with problem jobs surfaced automatically.</p></div><span class="badge">%d ACTIVE</span></div>%s</div>
<div class="panel"><div class="panelhead"><div><h3>Optimizer intelligence</h3><p>Useful context without pretending Radarr history equals optimizer success.</p></div><span class="badge">SUMMARY</span></div><div class="sidecontent">
<div class="metricline"><span>Observed net reduction</span><b class="%s">%.1f%%</b></div>
<div class="metricline"><span>Smaller replacements</span><b>%d</b></div>
<div class="metricline"><span>Downloads needing attention</span><b class="%s">%d</b></div>
<div class="metricline"><span>Optimizer imports blocked</span><b class="%s">%d</b></div>
<div class="metricline"><span>Last observed upgrade</span><b>%s</b></div>
<div class="metricline"><span>Engine</span><b>%s</b></div>
<div class="metricline"><span>UI mode</span><b>%s</b></div>
</div></div></div></div>
<div class="footer">Radarr Smart Optimizer · storage intelligence, not another Radarr replacement</div>
</div>
<script>
let queueOpen=false;
function toggleQueue(){const b=document.getElementById('queueExpand');if(queueOpen){location.href='/radarr/history';return;}queueOpen=true;document.querySelectorAll('.queueitem.extra').forEach(el=>el.style.display='block');if(b)b.textContent='History →';}
let changesOpen=false;function toggleChanges(){const b=document.getElementById('changesExpand');if(changesOpen){location.href='/radarr/history';return;}changesOpen=true;document.querySelectorAll('.changeextra').forEach(el=>el.style.display='table-row');if(b)b.textContent='History →';}
const box=document.getElementById('librarySearch');
const modeButtons=Array.from(
  document.querySelectorAll('[data-search-mode]')
);
const exclusionModeButton=
  document.getElementById('exclusionModeButton');
const exclusionResults=
  document.getElementById('exclusionSearchResults');

const SEARCH_APP=
  location.pathname.indexOf('/sonarr')===0
    ? 'sonarr'
    : 'radarr';

let searchMode='current';
let exclusionTimer=null;

const recentExclusions=document.getElementById('recentExclusions');
const recentExclusionRows=document.getElementById('recentExclusionRows');
const showAllExclusions=document.getElementById('showAllExclusions');

if(showAllExclusions){
  showAllExclusions.href='/'+SEARCH_APP+'/exclusions';
}

function updateExclusionButtonCount(count){
  const span=exclusionModeButton
    ? exclusionModeButton.querySelector('span')
    : null;

  if(span){
    span.textContent=String(count);
  }
}

async function exclusionAction(action,id){
  const body=new URLSearchParams();
  body.set('app',SEARCH_APP);
  body.set('id',String(id));

  const response=await fetch(
    action,
    {
      method:'POST',
      headers:{
        'Content-Type':'application/x-www-form-urlencoded;charset=UTF-8',
        'X-Requested-With':'fetch'
      },
      body:body.toString(),
      cache:'no-store'
    }
  );

  if(!response.ok){
    throw new Error('HTTP '+response.status);
  }

  return await response.json();
}

async function loadRecentExclusions(){
  try{
    const response=await fetch(
      '/exclusions-json?app='+encodeURIComponent(SEARCH_APP),
      {cache:'no-store'}
    );

    if(!response.ok){
      throw new Error('HTTP '+response.status);
    }

    const items=await response.json();

    updateExclusionButtonCount(
      Array.isArray(items) ? items.length : 0
    );

    const latest=
      Array.isArray(items)
        ? items.slice(0,5)
        : [];

    if(!latest.length){
      recentExclusionRows.innerHTML=
        '<div class="recentempty">No exclusions yet.</div>';
      return;
    }

    recentExclusionRows.innerHTML=latest.map(x=>{
      const year=
        x.year
          ? ' ('+escSearch(x.year)+')'
          : '';

      return (
        '<div class="recentexclusionrow">'+
          '<div class="exclusiontitle">'+
            '<b>'+escSearch(x.title)+'</b>'+year+
          '</div>'+
          '<form class="removeExclusionForm">'+
            '<input type="hidden" name="id" value="'+
              escSearch(x.id)+'">'+
            '<button type="submit">Remove exclusion</button>'+
          '</form>'+
        '</div>'
      );
    }).join('');

  }catch(err){
    console.error('Could not load exclusions:',err);
    recentExclusionRows.innerHTML=
      '<div class="recentempty">Could not load exclusions.</div>';
  }
}

async function refreshExclusionMode(){
  await loadRecentExclusions();

  if(box.value.trim()){
    searchExclusionLibrary();
  }
}

document.addEventListener('submit',async event=>{
  const addForm=event.target.closest(
    '#exclusionSearchResults form'
  );

  const removeForm=event.target.closest(
    '.removeExclusionForm'
  );

  if(!addForm && !removeForm){
    return;
  }

  event.preventDefault();

  const form=addForm || removeForm;
  const button=form.querySelector('button');
  const id=form.querySelector('[name="id"]').value;

  button.disabled=true;

  try{
    const result=await exclusionAction(
      addForm ? '/exclusion-add' : '/exclusion-remove',
      id
    );

    updateExclusionButtonCount(result.count);

    await refreshExclusionMode();

  }catch(err){
    console.error('Exclusion action failed:',err);
    button.disabled=false;
    button.textContent='Try again';
  }
});

function escSearch(v){
  return String(v==null?'':v)
    .replace(/&/g,'&amp;')
    .replace(/</g,'&lt;')
    .replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;')
    .replace(/'/g,'&#39;');
}

function restoreDashboardRows(){
  document.querySelectorAll('.filterrow').forEach(el=>{
    if(el.classList.contains('extra') && !queueOpen){
      el.style.display='none';
    }else if(
      el.classList.contains('changeextra') &&
      typeof changesOpen!=='undefined' &&
      !changesOpen
    ){
      el.style.display='none';
    }else{
      el.style.display='';
    }
  });
}

function filterCurrentDownloads(){
  const q=box.value.trim().toLowerCase();

  document.querySelectorAll('.filterrow').forEach(el=>{
    const match=
      !q ||
      ((el.dataset.search||'').includes(q));

    if(el.classList.contains('extra') && !queueOpen && !q){
      el.style.display='none';
    }else if(
      el.classList.contains('changeextra') &&
      typeof changesOpen!=='undefined' &&
      !changesOpen &&
      !q
    ){
      el.style.display='none';
    }else{
      el.style.display=match?'':'none';
    }
  });
}

function searchExclusionLibrary(){
  clearTimeout(exclusionTimer);

  const q=box.value.trim();

  if(!q){
    exclusionResults.innerHTML='';
    return;
  }

  exclusionResults.innerHTML=
    '<div class="exsearchstatus">Searching library…</div>';

  exclusionTimer=setTimeout(async()=>{
    try{
      const response=await fetch(
        '/library-search?app='+
        encodeURIComponent(SEARCH_APP)+
        '&q='+
        encodeURIComponent(q),
        {cache:'no-store'}
      );

      if(!response.ok){
        throw new Error('HTTP '+response.status);
      }

      const data=await response.json();

      if(!Array.isArray(data) || data.length===0){
        exclusionResults.innerHTML=
          '<div class="exsearchstatus">No matches found in your '+
          (SEARCH_APP==='radarr'?'Radarr':'Sonarr')+
          ' library.</div>';
        return;
      }

      exclusionResults.innerHTML=data.map(x=>{
        const year=
          x.year
            ? ' ('+escSearch(x.year)+')'
            : '';

        if(x.excluded){
          return (
            '<div class="exclusionsearchrow">'+
              '<div class="exclusiontitle">'+
                '<b>'+escSearch(x.title)+'</b>'+year+
              '</div>'+
              '<span class="badge">EXCLUDED</span>'+
            '</div>'
          );
        }

        return (
          '<div class="exclusionsearchrow">'+
            '<div class="exclusiontitle">'+
              '<b>'+escSearch(x.title)+'</b>'+year+
            '</div>'+
            '<form method="post" action="/exclusion-add">'+
              '<input type="hidden" name="app" value="'+
                SEARCH_APP+'">'+
              '<input type="hidden" name="id" value="'+
                escSearch(x.id)+'">'+
              '<button type="submit">Exclude</button>'+
            '</form>'+
          '</div>'
        );
      }).join('');

    }catch(err){
      console.error('Library exclusion search failed:',err);

      exclusionResults.innerHTML=
        '<div class="exsearchstatus bad">'+
        'Library search failed.'+
        '</div>';
    }
  },150);
}

function escapeSearchHTML(value){
  return String(value==null ? '' : value)
    .replace(/&/g,'&amp;')
    .replace(/</g,'&lt;')
    .replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;')
    .replace(/'/g,'&#39;');
}

function showOptimizerToast(message,kind){
  let toast=document.getElementById('manualOptimizerToast');

  if(!toast){
    toast=document.createElement('div');
    toast.id='manualOptimizerToast';
    toast.className='manualOptimizerToast';
    document.body.appendChild(toast);
  }

  toast.className=
    'manualOptimizerToast visible '+(kind||'');
  toast.textContent=message;

  clearTimeout(window.manualOptimizerToastTimer);

  window.manualOptimizerToastTimer=setTimeout(()=>{
    toast.classList.remove('visible');
  },5000);
}

function searchManualLibrary(){
  clearTimeout(exclusionTimer);

  const q=box.value.trim();

  if(!q){
    exclusionResults.innerHTML='';
    return;
  }

  exclusionResults.innerHTML=
    '<div class="exsearchstatus">Searching cached library…</div>';

  exclusionTimer=setTimeout(async()=>{
    try{
      const response=await fetch(
        '/library-search?app='+
        encodeURIComponent(SEARCH_APP)+
        '&q='+
        encodeURIComponent(q),
        {cache:'no-store'}
      );

      if(!response.ok){
        throw new Error('HTTP '+response.status);
      }

      const data=await response.json();

      if(!Array.isArray(data) || !data.length){
        exclusionResults.innerHTML=
          '<div class="exsearchstatus">No library matches.</div>';
        return;
      }

      exclusionResults.innerHTML=data.map(x=>{
        const title=escapeSearchHTML(x.title||'Untitled');
        const year=x.year
          ? ' <span class="muted">('+
            escapeSearchHTML(x.year)+
            ')</span>'
          : '';

        if(x.excluded){
          return (
            '<div class="exclusionsearchrow">'+
              '<div class="exclusiontitle">'+
                title+year+
              '</div>'+
              '<span class="badge">EXCLUDED</span>'+
            '</div>'
          );
        }

        return (
          '<div class="exclusionsearchrow">'+
            '<div class="exclusiontitle">'+
              title+year+
            '</div>'+
            '<button type="button" class="manualOptimizeButton" '+
              'data-id="'+Number(x.id)+'" '+
              'data-title="'+
                escapeSearchHTML(x.title||'Untitled')+
              '">Optimize</button>'+
          '</div>'
        );
      }).join('');
    }catch(err){
      console.error('Manual library search failed:',err);
      exclusionResults.innerHTML=
        '<div class="exsearchstatus">Could not search library.</div>';
    }
  },150);
}

async function waitForManualOptimizer(id,title,button,jobStarted){
  const clickedAt=Date.now();
  let searchingShown=false;

  for(let attempt=0;attempt<600;attempt++){
    await new Promise(resolve=>setTimeout(resolve,1000));

    if(!searchingShown && Date.now()-clickedAt>=3000){
      searchingShown=true;

      if(button){
        button.textContent='Searching…';
      }
    }

    try{
      const response=await fetch(
        '/status?app='+encodeURIComponent(SEARCH_APP),
        {cache:'no-store'}
      );

      if(!response.ok){
        continue;
      }

      const status=await response.json();
      const statusStarted=Number(status.started || 0);

      if(
        jobStarted &&
        statusStarted &&
        statusStarted < jobStarted
      ){
        continue;
      }

      if(status.running){
        continue;
      }

      if(!status.finished){
        continue;
      }

      const grabbed=Number(status.grabbed || 0);

      if(status.state==='failed'){
        if(button){
          button.textContent='Failed';
        }

        showOptimizerToast(
          'Optimizer failed for '+title+'.',
          'error'
        );

      }else if(status.state==='stopped'){
        if(button){
          button.textContent='Stopped';
        }

        showOptimizerToast(
          'Optimizer stopped for '+title+'.',
          'error'
        );

      }else if(grabbed>0){
        if(button){
          button.textContent='Downloading';
        }

        showOptimizerToast(
          'Downloading upgrade for '+title+'.',
          'success'
        );

      }else{
        if(button){
          button.textContent='No Upgrade';

          setTimeout(()=>{
            const currentButton=Array.from(
              document.querySelectorAll('.manualOptimizeButton')
            ).find(candidate=>
              Number(candidate.dataset.id)===Number(id)
            );

            if(currentButton){
              currentButton.textContent='Optimize';
              currentButton.disabled=false;
            }
          },5000);
        }

        showOptimizerToast(
          'No upgrade found for '+title+'.',
          'neutral'
        );
      }

      return;
    }catch(err){
      console.error(
        'Manual optimizer status failed:',
        err
      );
    }
  }

  if(button){
    button.textContent='Searching…';
  }

  showOptimizerToast(
    'Optimizer is still running for '+title+'.',
    'neutral'
  );
}

async function startManualOptimizer(id,title,button){
  if(button){
    button.disabled=true;
    button.textContent='Starting…';
  }

  const body=new URLSearchParams();
  body.set('app',SEARCH_APP);
  body.set('id',String(id));

  try{
    const response=await fetch(
      '/manual-optimize',
      {
        method:'POST',
        headers:{
          'Content-Type':
            'application/x-www-form-urlencoded;charset=UTF-8',
          'X-Requested-With':'fetch'
        },
        body:body.toString(),
        cache:'no-store'
      }
    );

    if(!response.ok){
      let message='Could not start optimizer.';

      if(response.status===409){
        message=
          'Optimizer is already running, or this item is excluded.';
      }

      throw new Error(message);
    }

    const result=await response.json();

    showOptimizerToast(
      'Optimizing '+title+'…',
      'running'
    );

    await waitForManualOptimizer(
      id,
      title,
      button,
      Number(result.started || 0)
    );

  }catch(err){
    showOptimizerToast(
      err.message || 'Could not start optimizer.',
      'error'
    );

    if(button){
      button.disabled=false;
      button.textContent='Optimize';
    }
  }
}

exclusionResults.addEventListener('click',event=>{
  const button=
    event.target.closest('.manualOptimizeButton');

  if(!button){
    return;
  }

  startManualOptimizer(
    Number(button.dataset.id),
    button.dataset.title || 'Selected item',
    button
  );
});

function setSearchMode(mode){
  searchMode=mode;

  box.value='';
  exclusionResults.innerHTML='';

  modeButtons.forEach(button=>{
    button.classList.toggle(
      'active',
      button.dataset.searchMode===mode
    );
  });

  restoreDashboardRows();

  if(recentExclusions){
    recentExclusions.classList.toggle(
      'visible',
      mode==='exclude'
    );
  }

  if(mode==='exclude'){
    box.placeholder=
      SEARCH_APP==='radarr'
        ? 'Search movies in Radarr library to exclude…'
        : 'Search series in Sonarr library to exclude…';

    loadRecentExclusions();

  }else if(mode==='manual'){
    box.placeholder=
      SEARCH_APP==='radarr'
        ? 'Search a movie to optimize…'
        : 'Search a series to optimize…';

  }else{
    box.placeholder=
      'Search releases and current downloads…';
  }

  box.focus();
}

box.addEventListener('input',()=>{
  if(searchMode==='exclude'){
    searchExclusionLibrary();
  }else if(searchMode==='manual'){
    searchManualLibrary();
  }else{
    filterCurrentDownloads();
  }
});

modeButtons.forEach(button=>{
  button.addEventListener('click',()=>{
    setSearchMode(button.dataset.searchMode);
  });
});

setSearchMode('current');
</script><script>
(function(){var el=document.querySelector('[id^="runstate-"]');if(!el)return;var app=el.id.replace('runstate-','');async function tick(){try{var r=await fetch('/status?app='+app,{cache:'no-store'});var x=await r.json();el.textContent=x.requested?(x.state.charAt(0).toUpperCase()+x.state.slice(1)+' · '+x.searched+' / '+x.requested+' upgrades'):'Idle';}catch(e){}}tick();setInterval(tick,10000);})();
</script>%s
<!-- SMART DASHBOARD NAV START -->

<script>
(function(){

    const nav =
        document.querySelector(
            '.topbar .nav'
        );

    if(!nav){
        return;
    }


    if(
        !nav.querySelector(
            '.dashboard-home-link'
        )
    ){

        const home =
            document.createElement(
                'a'
            );

        home.href = '/';

        home.className =
            'badge dashboard-home-link';

        home.textContent =
            'Home';

        nav.insertBefore(
            home,
            nav.firstChild
        );

    }


    if(
        !nav.querySelector(
            '.dashboard-settings-link'
        )
    ){

        const settings =
            document.createElement(
                'a'
            );

        settings.href =
            '/settings';

        settings.className =
            'badge dashboard-settings-link';

        settings.textContent =
            '⚙ Settings';

        nav.appendChild(
            settings
        );

    }

})();
</script>

<!-- SMART DASHBOARD NAV END -->

</body></html>""" % (
        CSS, "" if status == "Online" else " bad", html.escape(status), actions, warning, err, exclusion_panel("radarr"),
        "good" if saved >= 0 else "bad", gib(saved), positive,
        len(queue), len(attention), len(optimizer_blocked), used, extra_today, rows, output, len(queue), qrows,
        "good" if reduction_pct >= 0 else "bad", reduction_pct, positive,
        "bad" if attention else "good", len(attention),
        "bad" if optimizer_blocked else "good", len(optimizer_blocked), html.escape(last_date),
        html.escape(status), "Actions enabled" if ENABLE_ACTIONS else "Read-only", AJAX_SCRIPT)
















# ============================================================
# FAST HOME PAGE STATS CACHE
# ============================================================

HOME_STATS_CACHE_FILE = os.environ.get(
    "SMART_OPTIMIZER_HOME_STATS_CACHE",
    os.path.join(
        LIBRARY_CACHE_DIR,
        "smart-optimizer-home-stats.json"
    )
)

HOME_STATS_CACHE_TTL = 60

home_stats_cache_lock = threading.Lock()

home_stats_refreshing = {
    "radarr": False,
    "sonarr": False,
}


def _read_home_stats_cache():
    try:
        with home_stats_cache_lock:
            with open(
                HOME_STATS_CACHE_FILE,
                "r",
                encoding="utf-8"
            ) as f:
                data = json.load(f)

        return data if isinstance(data, dict) else {}

    except Exception:
        return {}


def _write_home_stats_cache(data):
    try:
        directory = os.path.dirname(
            HOME_STATS_CACHE_FILE
        )

        if directory:
            os.makedirs(
                directory,
                exist_ok=True
            )

        tmp = HOME_STATS_CACHE_FILE + ".tmp"

        with home_stats_cache_lock:

            with open(
                tmp,
                "w",
                encoding="utf-8"
            ) as f:

                json.dump(
                    data,
                    f,
                    indent=2,
                    sort_keys=True
                )

            os.replace(
                tmp,
                HOME_STATS_CACHE_FILE
            )

    except Exception as exc:

        print(
            "[ui] home stats cache write failed:",
            exc,
            flush=True
        )


def _calculate_front_page_stats(app):
    """
    Calculate only the three values required by the home page.

    IMPORTANT:
    Do NOT call page() or sonarr_page() here.
    """

    result = {
        "count": "—",
        "saved": "—",
        "optimizations": "—",
    }

    # --------------------------------------------------------
    # Library count comes from the existing local library
    # JSON cache. No Radarr/Sonarr request is needed here.
    # --------------------------------------------------------

    try:
        result["count"] = str(
            len(
                library_items(app)
            )
        )
    except Exception as exc:
        print(
            "[ui] home count failed for %s: %s"
            % (app, exc),
            flush=True
        )


    # --------------------------------------------------------
    # Reuse the underlying upgrade calculations directly.
    #
    # This is dramatically cheaper than rendering an entire
    # Radarr/Sonarr HTML dashboard and parsing it afterwards.
    # --------------------------------------------------------

    try:

        if app == "radarr":

            upgrades = completed_upgrades(
                history_records()
            )

        elif app == "sonarr":

            upgrades = sonarr_completed_upgrades(
                sonarr_history_records()
            )

        else:

            return result


        saved = sum(
            int(
                x.get("saved") or 0
            )
            for x in upgrades
        )


        positive = sum(
            1
            for x in upgrades
            if int(
                x.get("saved") or 0
            ) > 0
        )


        result["saved"] = (
            "%+.2f GiB"
            % gib(saved)
        )


        result["optimizations"] = str(
            positive
        )


    except Exception as exc:

        print(
            "[ui] home history stats failed for %s: %s"
            % (app, exc),
            flush=True
        )


    return result


def _refresh_front_page_stats(app):

    try:

        fresh = _calculate_front_page_stats(
            app
        )

        cache = _read_home_stats_cache()

        cache[app] = {
            "updated": int(time.time()),
            "values": fresh,
        }

        _write_home_stats_cache(
            cache
        )

        print(
            "[ui] home stats refreshed: %s"
            % app,
            flush=True
        )


    except Exception as exc:

        print(
            "[ui] home stats refresh failed for %s: %s"
            % (app, exc),
            flush=True
        )


    finally:

        with home_stats_cache_lock:
            home_stats_refreshing[app] = False


def front_page_stats(app):
    """
    Home-page read path.

    Fresh cache:
        return immediately.

    Stale cache:
        return old values immediately and refresh in background.

    Missing cache:
        calculate once, then persist as JSON.
    """

    cache = _read_home_stats_cache()

    entry = cache.get(app)

    if isinstance(entry, dict):

        values = entry.get("values")

        updated = int(
            entry.get("updated") or 0
        )

        if isinstance(values, dict):

            age = (
                int(time.time())
                - updated
            )

            if age <= HOME_STATS_CACHE_TTL:

                return values


            # -----------------------------------------------
            # Cache is stale.
            #
            # DO NOT make the browser wait.
            # Return old data immediately and refresh it
            # asynchronously.
            # -----------------------------------------------

            start_refresh = False

            with home_stats_cache_lock:

                if not home_stats_refreshing.get(app):

                    home_stats_refreshing[app] = True

                    start_refresh = True


            if start_refresh:

                threading.Thread(
                    target=_refresh_front_page_stats,
                    args=(app,),
                    name="home-stats-%s" % app,
                    daemon=True
                ).start()


            return values


    # --------------------------------------------------------
    # First ever load: no JSON exists yet.
    #
    # Calculate once and persist. Every later load is cached.
    # --------------------------------------------------------

    fresh = _calculate_front_page_stats(
        app
    )

    cache = _read_home_stats_cache()

    cache[app] = {
        "updated": int(time.time()),
        "values": fresh,
    }

    _write_home_stats_cache(
        cache
    )

    return fresh


def home_page():

    radarr = front_page_stats(
        "radarr"
    )

    sonarr = front_page_stats(
        "sonarr"
    )


    rendered = _smart_expand_common("""<!doctype html>

<html>

<head>

<meta charset="utf-8">

<meta
name="viewport"
content="width=device-width,initial-scale=1"
>

<title>Smart Optimizer</title>


<style>

__BASE_CSS__


/* ==========================================================
   SMART OPTIMIZER HOME
   ========================================================== */


html,
body{

    margin:0;

    min-height:100%;

}


body.smart-home{

    min-height:100vh;

    overflow-x:hidden;

    color:#f5f8ff;

    background:#030912;

    font-family:
        Inter,
        system-ui,
        -apple-system,
        BlinkMacSystemFont,
        "Segoe UI",
        sans-serif;

}


/* ==========================================================
   REAL BACKGROUND.PNG
   ========================================================== */


.smart-home-bg{

    position:fixed;

    inset:0;

    z-index:0;

    background:

        linear-gradient(
            180deg,
            rgba(2,8,16,.16) 0%,
            rgba(2,8,16,.22) 45%,
            rgba(2,8,16,.38) 100%
        ),

        url('/home-background.png?v=20260923-233901')
        center center / cover
        no-repeat;

}


.smart-home-bg::after{

    content:"";

    position:absolute;

    inset:0;

    background:

        radial-gradient(
            ellipse at center,
            transparent 20%,
            rgba(0,4,10,.10) 60%,
            rgba(0,4,10,.32) 100%
        );

    pointer-events:none;

}


/* ==========================================================
   MAIN PAGE
   ========================================================== */


.smart-home-shell{

    position:relative;

    z-index:1;

    width:min(
        1180px,
        calc(100vw - 36px)
    );

    margin:0 auto;

    box-sizing:border-box;

    padding:
        30px
        0
        42px;

}


/* ==========================================================
   LOGO
   ========================================================== */


.smart-brand{

    display:flex;

    justify-content:center;

    align-items:center;

}


.smart-brand img{

    display:block;

    width:min(
        500px,
        70vw
    );

    height:auto;

    object-fit:contain;

    filter:

        drop-shadow(
            0 12px 24px
            rgba(0,0,0,.28)
        );

}


/* ==========================================================
   TITLE
   ========================================================== */


.home-title{

    margin:
        8px auto
        0;

    text-align:center;

    color:#f7f9ff;

    font-size:
        clamp(
            2rem,
            3vw,
            2.75rem
        );

    line-height:1.08;

    letter-spacing:
        -.035em;

    font-weight:750;

}


.home-subtitle{

    margin:
        12px auto
        28px;

    text-align:center;

    color:
        rgba(
            209,
            222,
            242,
            .72
        );

    font-size:
        1.04rem;

}


.home-accent{

    width:64px;

    height:3px;

    margin:
        0 auto
        22px;

    border-radius:999px;

    background:

        linear-gradient(
            90deg,
            transparent,
            #00d4ff,
            transparent
        );

    box-shadow:

        0 0 18px
        rgba(
            0,
            190,
            255,
            .38
        );

}


/* ==========================================================
   RADARR + SONARR GRID
   ========================================================== */


.library-grid{

    display:grid;

    grid-template-columns:
        repeat(
            2,
            minmax(0,1fr)
        );

    gap:24px;

}


/* ==========================================================
   SHARED CARD
   ========================================================== */


.library-card{

    position:relative;

    overflow:hidden;

    box-sizing:border-box;

    min-height:310px;

    display:grid;

    grid-template-columns:
        152px
        minmax(0,1fr)
        56px;

    grid-template-rows:
        auto
        1fr;

    column-gap:22px;

    row-gap:4px;

    align-items:center;

    padding:
        28px;

    text-decoration:none;

    color:#f4f8ff;

    border-radius:22px;

    background:

        linear-gradient(
            145deg,
            rgba(12,25,43,.91),
            rgba(5,15,28,.86)
        );

    backdrop-filter:
        blur(15px);

    -webkit-backdrop-filter:
        blur(15px);

    transition:

        transform .20s ease,
        border-color .20s ease,
        box-shadow .20s ease,
        background .20s ease;

}


.library-card::before{

    content:"";

    position:absolute;

    inset:0;

    pointer-events:none;

    background:

        linear-gradient(
            120deg,
            rgba(255,255,255,.045),
            transparent 30%,
            transparent 65%,
            rgba(255,255,255,.018)
        );

}


.library-card:hover{

    transform:
        translateY(-5px);

}


/* ==========================================================
   RADARR
   ========================================================== */


.library-card.radarr{

    border:
        1px solid
        rgba(
            255,
            94,
            62,
            .62
        );

    box-shadow:

        inset
        0 0 0 1px
        rgba(255,255,255,.02),

        0 18px 48px
        rgba(0,0,0,.30),

        0 0 22px
        rgba(
            255,
            77,
            44,
            .12
        );

}


.library-card.radarr:hover{

    border-color:
        rgba(
            255,
            115,
            75,
            .95
        );

    box-shadow:

        inset
        0 0 0 1px
        rgba(255,255,255,.03),

        0 20px 52px
        rgba(0,0,0,.36),

        0 0 18px
        rgba(
            255,
            73,
            42,
            .42
        ),

        0 0 44px
        rgba(
            255,
            73,
            42,
            .20
        );

}


/* ==========================================================
   SONARR
   ========================================================== */


.library-card.sonarr{

    border:
        1px solid
        rgba(
            20,
            178,
            255,
            .68
        );

    box-shadow:

        inset
        0 0 0 1px
        rgba(255,255,255,.02),

        0 18px 48px
        rgba(0,0,0,.30),

        0 0 22px
        rgba(
            0,
            163,
            255,
            .12
        );

}


.library-card.sonarr:hover{

    border-color:
        rgba(
            43,
            197,
            255,
            .98
        );

    box-shadow:

        inset
        0 0 0 1px
        rgba(255,255,255,.03),

        0 20px 52px
        rgba(0,0,0,.36),

        0 0 18px
        rgba(
            0,
            174,
            255,
            .45
        ),

        0 0 44px
        rgba(
            0,
            174,
            255,
            .20
        );

}


/* ==========================================================
   MAIN APP ICON
   ========================================================== */


.library-icon{

    grid-row:
        1 / 3;

    width:150px;

    height:150px;

    display:flex;

    justify-content:center;

    align-items:center;

    position:relative;

}


.library-icon::before{

    content:"";

    position:absolute;

    inset:4px;

    border-radius:50%;

    border:
        1px solid
        rgba(
            255,
            255,
            255,
            .055
        );

}


.library-icon img{

    position:relative;

    z-index:1;

    width:120px;

    height:120px;

    object-fit:contain;

    display:block;

    filter:

        drop-shadow(
            0 12px 22px
            rgba(0,0,0,.34)
        );

}


/* ==========================================================
   CARD COPY
   ========================================================== */


.library-copy{

    align-self:end;

}


.library-name{

    margin:0;

    font-size:
        1.8rem;

    line-height:1.1;

    font-weight:750;

}


.library-type{

    margin-top:8px;

    font-size:.86rem;

    font-weight:750;

    letter-spacing:.20em;

    text-transform:uppercase;

}


.radarr .library-type{

    color:#ff7058;

}


.sonarr .library-type{

    color:#35c8ff;

}


.library-description{

    margin-top:18px;

    max-width:330px;

    color:
        rgba(
            216,
            227,
            244,
            .76
        );

    line-height:1.55;

    font-size:.96rem;

}


/* ==========================================================
   ARROW
   ========================================================== */


.library-arrow{

    align-self:center;

    width:52px;

    height:52px;

    display:flex;

    justify-content:center;

    align-items:center;

    border-radius:50%;

    color:#fff;

    font-size:2rem;

    line-height:1;

    border:
        1px solid
        rgba(
            255,
            255,
            255,
            .09
        );

}


.radarr .library-arrow{

    background:
        rgba(
            90,
            25,
            31,
            .76
        );

    border-color:
        rgba(
            255,
            95,
            68,
            .46
        );

}


.sonarr .library-arrow{

    background:
        rgba(
            7,
            49,
            79,
            .82
        );

    border-color:
        rgba(
            29,
            174,
            255,
            .48
        );

}


/* ==========================================================
   LIVE STATS
   ========================================================== */


.library-stats{

    grid-column:
        2 / 4;

    display:grid;

    grid-template-columns:
        repeat(
            3,
            minmax(0,1fr)
        );

    gap:10px;

    align-self:end;

    margin-top:18px;

}


.stat-box{

    min-height:68px;

    box-sizing:border-box;

    display:flex;

    gap:10px;

    align-items:center;

    padding:
        10px
        12px;

    border-radius:14px;

    background:

        linear-gradient(
            180deg,
            rgba(22,39,62,.79),
            rgba(12,24,40,.76)
        );

    border:
        1px solid
        rgba(
            116,
            158,
            209,
            .16
        );

    box-shadow:

        inset
        0 0 0 1px
        rgba(255,255,255,.018);

}


.stat-icon{

    width:28px;

    height:28px;

    flex:
        0 0 28px;

    display:flex;

    justify-content:center;

    align-items:center;

}


.stat-icon svg{

    width:27px;

    height:27px;

    display:block;

}


.radarr .stat-icon{

    color:#ff6a50;

}


.sonarr .stat-icon{

    color:#38c8ff;

}


.stat-label{

    color:
        rgba(
            205,
            219,
            240,
            .68
        );

    font-size:.71rem;

    line-height:1.2;

}


.stat-value{

    margin-top:3px;

    color:#f7fbff;

    font-size:
        1.03rem;

    line-height:1.15;

    font-weight:750;

    white-space:nowrap;

}


/* ==========================================================
   SETTINGS
   ========================================================== */


.settings-wrap{

    display:flex;

    justify-content:center;

    margin-top:26px;

}


.settings-card{

    width:min(
        610px,
        100%
    );

    box-sizing:border-box;

    min-height:138px;

    display:grid;

    grid-template-columns:
        108px
        1fr
        48px;

    gap:22px;

    align-items:center;

    padding:
        20px
        26px;

    text-decoration:none;

    color:#f4f8ff;

    border-radius:20px;

    background:

        linear-gradient(
            145deg,
            rgba(10,28,47,.91),
            rgba(5,17,30,.88)
        );

    border:
        1px solid
        rgba(
            32,
            177,
            255,
            .42
        );

    backdrop-filter:
        blur(15px);

    -webkit-backdrop-filter:
        blur(15px);

    box-shadow:

        0 16px 42px
        rgba(0,0,0,.28),

        0 0 22px
        rgba(
            0,
            165,
            255,
            .08
        );

    transition:

        transform .2s ease,
        box-shadow .2s ease,
        border-color .2s ease;

}


.settings-card:hover{

    transform:
        translateY(-4px);

    border-color:
        rgba(
            49,
            202,
            255,
            .90
        );

    box-shadow:

        0 18px 48px
        rgba(0,0,0,.32),

        0 0 32px
        rgba(
            0,
            177,
            255,
            .20
        );

}


.settings-image{

    width:100px;

    height:100px;

    object-fit:contain;

    display:block;

    filter:

        drop-shadow(
            0 10px 18px
            rgba(0,0,0,.28)
        );

}


.settings-name{

    font-size:
        1.45rem;

    font-weight:750;

}


.settings-type{

    margin-top:6px;

    color:#3ed1ff;

    font-size:.77rem;

    font-weight:750;

    letter-spacing:.20em;

    text-transform:uppercase;

}


.settings-description{

    margin-top:12px;

    color:
        rgba(
            212,
            226,
            244,
            .72
        );

    line-height:1.5;

    font-size:.92rem;

}


.settings-arrow{

    width:46px;

    height:46px;

    display:flex;

    justify-content:center;

    align-items:center;

    border-radius:50%;

    background:
        rgba(
            12,
            42,
            67,
            .84
        );

    border:
        1px solid
        rgba(
            40,
            178,
            255,
            .36
        );

    color:#fff;

    font-size:
        1.8rem;

}


/* ==========================================================
   FOOTER
   ========================================================== */


.home-footer{

    margin-top:36px;

    text-align:center;

    color:
        rgba(
            170,
            192,
            222,
            .54
        );

    font-size:.67rem;

    letter-spacing:.30em;

    text-transform:uppercase;

}


.footer-line{

    width:72px;

    height:2px;

    margin:
        20px auto
        0;

    border-radius:999px;

    background:#27d3ff;

    box-shadow:

        0 0 15px
        rgba(
            0,
            190,
            255,
            .38
        );

}


/* ==========================================================
   RESPONSIVE
   ========================================================== */


@media(max-width:950px){

    .library-grid{

        grid-template-columns:
            1fr;

    }

}


@media(max-width:650px){

    .smart-home-shell{

        width:
            calc(100vw - 24px);

        padding-top:18px;

    }


    .smart-brand img{

        width:
            min(
                460px,
                92vw
            );

    }


    .home-title{

        font-size:1.9rem;

    }


    .library-card{

        grid-template-columns:
            94px
            1fr
            42px;

        min-height:unset;

        padding:20px;

        column-gap:14px;

    }


    .library-icon{

        width:90px;

        height:90px;

    }


    .library-icon img{

        width:78px;

        height:78px;

    }


    .library-name{

        font-size:1.5rem;

    }


    .library-description{

        font-size:.9rem;

    }


    .library-stats{

        grid-column:
            1 / 4;

        grid-template-columns:
            1fr;

        margin-top:16px;

    }


    .settings-card{

        grid-template-columns:
            78px
            1fr
            40px;

        padding:18px;

        gap:14px;

    }


    .settings-image{

        width:72px;

        height:72px;

    }

}



/* ==========================================================
   FINAL HOME PAGE POLISH
   ========================================================== */


/* ----------------------------------------------------------
   BACKGROUND
   ---------------------------------------------------------- */

body.smart-home{

    background:
        #020811
        url('/home-background.png?v=20260923-233901')
        center center / cover
        no-repeat fixed !important;

}


.smart-home-bg{

    background:
        linear-gradient(
            180deg,
            rgba(1,7,14,.12) 0%,
            rgba(1,7,14,.17) 52%,
            rgba(1,7,14,.26) 100%
        ),
        url('/home-background.png?v=20260923-233901')
        center center / cover
        no-repeat fixed !important;

}


.smart-home-bg::after{

    background:
        radial-gradient(
            ellipse at center,
            rgba(0,0,0,0) 18%,
            rgba(0,4,10,.05) 58%,
            rgba(0,4,10,.18) 100%
        ) !important;

}


/* ----------------------------------------------------------
   MORE ROOM
   ---------------------------------------------------------- */

.smart-home-shell{

    width:min(
        1380px,
        calc(100vw - 50px)
    ) !important;

    padding:
        34px 0
        56px !important;

}


.smart-brand img{

    width:min(
        500px,
        68vw
    ) !important;

}


.home-title{

    margin-top:
        14px !important;

}


.home-subtitle{

    margin-bottom:
        24px !important;

}


.library-grid{

    gap:
        32px !important;

}


/* ----------------------------------------------------------
   BIGGER CARDS
   ---------------------------------------------------------- */

.library-card{

    min-height:
        350px !important;

    grid-template-columns:
        170px
        minmax(0,1fr) !important;

    grid-template-rows:
        auto
        1fr !important;

    column-gap:
        30px !important;

    padding:
        34px
        34px
        30px !important;

    border-radius:
        24px !important;

}


/* ----------------------------------------------------------
   REMOVE DARK SQUARE AROUND ICON
   ---------------------------------------------------------- */

.library-icon{

    grid-row:
        1 / 3 !important;

    width:
        165px !important;

    height:
        165px !important;

    border:
        none !important;

    background:
        transparent !important;

    box-shadow:
        none !important;

    outline:
        none !important;

}


.library-icon::before,
.library-icon::after{

    display:
        none !important;

}


.library-icon img{

    width:
        138px !important;

    height:
        138px !important;

    border:
        none !important;

    outline:
        none !important;

    background:
        transparent !important;

    box-shadow:
        none !important;

}


/* ----------------------------------------------------------
   RADARR ICON GLOW
   ---------------------------------------------------------- */

.library-card.radarr
.library-icon img{

    filter:

        drop-shadow(
            0 0 10px
            rgba(255,111,62,.22)
        )

        drop-shadow(
            0 12px 22px
            rgba(0,0,0,.28)
        ) !important;

}


.library-card.radarr:hover
.library-icon img{

    filter:

        drop-shadow(
            0 0 12px
            rgba(255,107,57,.68)
        )

        drop-shadow(
            0 0 28px
            rgba(255,68,25,.35)
        ) !important;

}


/* ----------------------------------------------------------
   SONARR ICON GLOW
   ---------------------------------------------------------- */

.library-card.sonarr
.library-icon img{

    filter:

        drop-shadow(
            0 0 10px
            rgba(0,182,255,.22)
        )

        drop-shadow(
            0 12px 22px
            rgba(0,0,0,.28)
        ) !important;

}


.library-card.sonarr:hover
.library-icon img{

    filter:

        drop-shadow(
            0 0 12px
            rgba(0,192,255,.72)
        )

        drop-shadow(
            0 0 28px
            rgba(0,150,255,.40)
        ) !important;

}


/* ----------------------------------------------------------
   MORE CARD TEXT SPACE
   ---------------------------------------------------------- */

.library-copy{

    align-self:
        center !important;

}


.library-name{

    font-size:
        1.95rem !important;

}


.library-description{

    max-width:
        390px !important;

    font-size:
        1rem !important;

    line-height:
        1.6 !important;

}


/* ----------------------------------------------------------
   STATS
   ---------------------------------------------------------- */

.library-stats{

    grid-column:
        2 / 3 !important;

    grid-template-columns:
        repeat(
            3,
            minmax(0,1fr)
        ) !important;

    gap:
        14px !important;

    margin-top:
        26px !important;

}


.stat-box{

    min-height:
        78px !important;

    padding:
        12px
        14px !important;

    border-radius:
        15px !important;

}


.stat-label{

    font-size:
        .75rem !important;

}


.stat-value{

    font-size:
        1.13rem !important;

}


/* ----------------------------------------------------------
   REMOVE ARROW SPACE COMPLETELY
   ---------------------------------------------------------- */

.library-arrow,
.settings-arrow{

    display:
        none !important;

}


/* ----------------------------------------------------------
   RADARR HOVER
   ---------------------------------------------------------- */

.library-card.radarr{

    border-color:
        rgba(
            255,
            91,
            56,
            .36
        ) !important;

}


.library-card.radarr:hover{

    border-color:
        rgba(
            255,
            110,
            67,
            1
        ) !important;

    background:

        linear-gradient(
            145deg,
            rgba(39,22,29,.94),
            rgba(12,17,29,.91)
        ) !important;

    box-shadow:

        0 22px 56px
        rgba(0,0,0,.38),

        0 0 10px
        rgba(255,83,43,.50),

        0 0 32px
        rgba(255,63,28,.28) !important;

}


/* ----------------------------------------------------------
   SONARR HOVER
   ---------------------------------------------------------- */

.library-card.sonarr{

    border-color:
        rgba(
            22,
            167,
            255,
            .40
        ) !important;

}


.library-card.sonarr:hover{

    border-color:
        rgba(
            36,
            193,
            255,
            1
        ) !important;

    background:

        linear-gradient(
            145deg,
            rgba(9,31,51,.96),
            rgba(6,17,30,.91)
        ) !important;

    box-shadow:

        0 22px 56px
        rgba(0,0,0,.38),

        0 0 10px
        rgba(0,179,255,.55),

        0 0 34px
        rgba(0,151,255,.28) !important;

}


/* ----------------------------------------------------------
   SETTINGS = GREEN
   ---------------------------------------------------------- */

.settings-wrap{

    margin-top:
        32px !important;

}


.settings-card{

    width:
        min(
            680px,
            100%
        ) !important;

    min-height:
        150px !important;

    grid-template-columns:
        120px
        1fr !important;

    gap:
        26px !important;

    padding:
        24px
        30px !important;

    border-color:
        rgba(
            66,
            217,
            139,
            .40
        ) !important;

    background:

        linear-gradient(
            145deg,
            rgba(9,30,31,.93),
            rgba(5,19,27,.90)
        ) !important;

    box-shadow:

        0 18px 46px
        rgba(0,0,0,.30),

        0 0 22px
        rgba(53,213,138,.08) !important;

}


.settings-card:hover{

    border-color:
        rgba(
            80,
            235,
            153,
            .98
        ) !important;

    box-shadow:

        0 20px 52px
        rgba(0,0,0,.34),

        0 0 12px
        rgba(54,230,143,.45),

        0 0 34px
        rgba(39,211,128,.25) !important;

}


.settings-image{

    width:
        105px !important;

    height:
        105px !important;

    border:
        none !important;

    outline:
        none !important;

    background:
        transparent !important;

    box-shadow:
        none !important;

    filter:

        drop-shadow(
            0 0 10px
            rgba(54,226,145,.34)
        )

        drop-shadow(
            0 12px 22px
            rgba(0,0,0,.26)
        ) !important;

}


.settings-card:hover
.settings-image{

    filter:

        drop-shadow(
            0 0 12px
            rgba(62,237,151,.70)
        )

        drop-shadow(
            0 0 28px
            rgba(39,211,128,.34)
        ) !important;

}


.settings-type{

    color:
        #49e79b !important;

}


/* ----------------------------------------------------------
   FOOTER
   ---------------------------------------------------------- */

.home-footer{

    margin-top:
        44px !important;

}


/* ----------------------------------------------------------
   MOBILE
   ---------------------------------------------------------- */

@media(max-width:950px){

    .smart-home-shell{

        width:
            calc(100vw - 28px) !important;

    }


    .library-card{

        min-height:
            320px !important;

    }

}


@media(max-width:650px){

    .library-card{

        grid-template-columns:
            100px
            1fr !important;

        padding:
            22px !important;

    }


    .library-icon{

        width:
            96px !important;

        height:
            96px !important;

    }


    .library-icon img{

        width:
            86px !important;

        height:
            86px !important;

    }


    .library-stats{

        grid-column:
            1 / 3 !important;

        grid-template-columns:
            1fr !important;

    }


    .settings-card{

        grid-template-columns:
            86px
            1fr !important;

    }


    .settings-image{

        width:
            78px !important;

        height:
            78px !important;

    }

}




/* ==========================================================
   FINAL CARD / STAT SPACING FIX
   ========================================================== */

.smart-home-shell{
    width:min(1500px, calc(100vw - 60px)) !important;
    max-width:none !important;
}

.library-grid{
    width:100% !important;
    max-width:none !important;
    gap:30px !important;
}

/* Give each library card noticeably more room */
.library-card{
    min-width:0 !important;
    min-height:365px !important;

    grid-template-columns:
        190px
        minmax(0,1fr) !important;

    grid-template-rows:
        auto
        auto
        auto !important;

    column-gap:30px !important;

    padding:
        32px
        34px
        28px !important;
}

/* ----------------------------------------------------------
   BIGGER ICONS + MOVE THEM UP
   ---------------------------------------------------------- */

.library-icon{
    grid-column:1 !important;
    grid-row:1 / 3 !important;

    width:180px !important;
    height:180px !important;

    align-self:start !important;

    margin-top:-2px !important;

    background:transparent !important;
    border:none !important;
    outline:none !important;
    box-shadow:none !important;
}

.library-icon::before,
.library-icon::after{
    display:none !important;
}

.library-icon img{
    width:154px !important;
    height:154px !important;

    display:block !important;

    object-fit:contain !important;

    background:transparent !important;
    border:none !important;
    outline:none !important;
    box-shadow:none !important;
}

/* Copy starts closer to the top beside icon */
.library-copy{
    grid-column:2 !important;
    grid-row:1 !important;

    align-self:start !important;

    padding-top:12px !important;
}

.library-name{
    font-size:2rem !important;
}

.library-description{
    max-width:430px !important;
    margin-top:17px !important;

    font-size:1rem !important;
    line-height:1.55 !important;
}


/* ==========================================================
   LIVE STAT BOXES
   Use the COMPLETE width underneath icon + description.
   ========================================================== */

.library-stats{
    grid-column:1 / -1 !important;
    grid-row:3 !important;

    width:100% !important;

    display:grid !important;

    grid-template-columns:
        repeat(3, minmax(0,1fr)) !important;

    gap:14px !important;

    margin-top:20px !important;
}

.stat-box{
    min-width:0 !important;

    min-height:82px !important;

    box-sizing:border-box !important;

    padding:
        12px
        15px !important;

    gap:12px !important;

    overflow:hidden !important;
}

.stat-box > div:last-child{
    min-width:0 !important;
}

.stat-label{
    display:block !important;

    white-space:nowrap !important;

    overflow:hidden !important;

    text-overflow:ellipsis !important;

    font-size:.76rem !important;
}

.stat-value{
    display:block !important;

    max-width:100% !important;

    white-space:nowrap !important;

    overflow:hidden !important;

    text-overflow:clip !important;

    font-size:1.16rem !important;

    line-height:1.18 !important;

    letter-spacing:-.015em !important;
}

/* Give the middle Space Saved tile slightly more room */
.stat-box:nth-child(1){
    width:auto !important;
}

.library-stats{
    grid-template-columns:
        .90fr
        1.20fr
        .90fr !important;
}


/* ==========================================================
   CARD HOVER STILL DOES THE NAVIGATION CUE
   ========================================================== */

.library-card.radarr:hover{
    transform:translateY(-5px) scale(1.006) !important;
}

.library-card.sonarr:hover{
    transform:translateY(-5px) scale(1.006) !important;
}


/* ==========================================================
   SETTINGS – retain green style but slightly roomier
   ========================================================== */

.settings-card{
    width:min(760px, 100%) !important;

    min-height:154px !important;

    grid-template-columns:
        125px
        minmax(0,1fr) !important;
}

.settings-image{
    width:110px !important;
    height:110px !important;
}


/* ==========================================================
   TABLET
   ========================================================== */

@media(max-width:1100px){

    .smart-home-shell{
        width:calc(100vw - 32px) !important;
    }

    .library-grid{
        grid-template-columns:1fr !important;
    }

    .library-card{
        max-width:850px !important;
        width:100% !important;
        margin:0 auto !important;
    }

}


/* ==========================================================
   MOBILE
   ========================================================== */

@media(max-width:650px){

    .smart-home-shell{
        width:calc(100vw - 22px) !important;
    }

    .library-card{
        grid-template-columns:
            105px
            minmax(0,1fr) !important;

        min-height:unset !important;

        padding:20px !important;

        column-gap:14px !important;
    }

    .library-icon{
        width:100px !important;
        height:100px !important;
    }

    .library-icon img{
        width:92px !important;
        height:92px !important;
    }

    .library-copy{
        padding-top:3px !important;
    }

    .library-name{
        font-size:1.55rem !important;
    }

    .library-stats{
        grid-column:1 / -1 !important;

        grid-template-columns:
            1fr !important;
    }

    .stat-box{
        min-height:66px !important;
    }

}




/* ==========================================================
   FINAL ICON SIZE TWEAK
   ========================================================== */

.library-icon{
    width:192px !important;
    height:192px !important;
}

.library-icon img{
    width:166px !important;
    height:166px !important;
}

@media(max-width:650px){

    .library-icon{
        width:108px !important;
        height:108px !important;
    }

    .library-icon img{
        width:98px !important;
        height:98px !important;
    }

}


</style>

  <link rel="icon" type="image/png" sizes="32x32" href="/favicon.ico?v=2">
</head>


<body class="smart-home">


<div class="smart-home-bg"></div>


<main class="smart-home-shell">


<div class="smart-brand">

<img
src="/smart-optimizer-logo-web.png"
alt="Smart Optimizer"
>

</div>





<p class="home-subtitle">

Find smaller releases for your media while keeping quality.

</p>


<div class="home-accent"></div>



<section class="library-grid">


<!-- ======================================================
     RADARR
     ====================================================== -->


<a
class="library-card radarr"
href="/radarr"
>


<div class="library-icon">

<img
src="/radarr-icon.png"
alt="Radarr"
>

</div>


<div class="library-copy">

<div class="library-name">

Radarr

</div>


<div class="library-type">

Movie library

</div>


<div class="library-description">

Optimize your movie collection with smaller, higher quality releases.

</div>

</div>


<div class="library-stats">


<div class="stat-box">

<div class="stat-icon">

<svg
viewBox="0 0 24 24"
fill="none"
stroke="currentColor"
stroke-width="2"
>

<rect
x="4"
y="3"
width="16"
height="18"
rx="2"
/>

<path d="M8 3v18M16 3v18M4 8h4M4 16h4M16 8h4M16 16h4"/>

</svg>

</div>


<div>

<div class="stat-label">

Movies

</div>

<div class="stat-value">

__RADARR_COUNT__

</div>

</div>

</div>



<div class="stat-box">

<div class="stat-icon">

<svg
viewBox="0 0 24 24"
fill="none"
stroke="currentColor"
stroke-width="2"
>

<ellipse
cx="12"
cy="5"
rx="7"
ry="3"
/>

<path d="M5 5v6c0 1.7 3.1 3 7 3s7-1.3 7-3V5"/>

<path d="M5 11v6c0 1.7 3.1 3 7 3s7-1.3 7-3v-6"/>

</svg>

</div>


<div>

<div class="stat-label">

Space Saved

</div>

<div class="stat-value">

__RADARR_SAVED__

</div>

</div>

</div>



<div class="stat-box">

<div class="stat-icon">

<svg
viewBox="0 0 24 24"
fill="none"
stroke="currentColor"
stroke-width="2"
>

<path d="M5 20V12M12 20V5M19 20V9"/>

</svg>

</div>


<div>

<div class="stat-label">

Optimizations

</div>

<div class="stat-value">

__RADARR_OPT__

</div>

</div>

</div>


</div>


</a>



<!-- ======================================================
     SONARR
     ====================================================== -->


<a
class="library-card sonarr"
href="/sonarr"
>


<div class="library-icon">

<img
src="/sonarr-icon.png"
alt="Sonarr"
>

</div>


<div class="library-copy">

<div class="library-name">

Sonarr

</div>


<div class="library-type">

TV library

</div>


<div class="library-description">

Optimize your TV series collection with smaller, higher quality releases.

</div>

</div>


<div class="library-stats">


<div class="stat-box">

<div class="stat-icon">

<svg
viewBox="0 0 24 24"
fill="none"
stroke="currentColor"
stroke-width="2"
>

<rect
x="3"
y="5"
width="18"
height="15"
rx="2"
/>

<path d="M8 2l4 3 4-3"/>

</svg>

</div>


<div>

<div class="stat-label">

Series

</div>

<div class="stat-value">

__SONARR_COUNT__

</div>

</div>

</div>



<div class="stat-box">

<div class="stat-icon">

<svg
viewBox="0 0 24 24"
fill="none"
stroke="currentColor"
stroke-width="2"
>

<ellipse
cx="12"
cy="5"
rx="7"
ry="3"
/>

<path d="M5 5v6c0 1.7 3.1 3 7 3s7-1.3 7-3V5"/>

<path d="M5 11v6c0 1.7 3.1 3 7 3s7-1.3 7-3v-6"/>

</svg>

</div>


<div>

<div class="stat-label">

Space Saved

</div>

<div class="stat-value">

__SONARR_SAVED__

</div>

</div>

</div>



<div class="stat-box">

<div class="stat-icon">

<svg
viewBox="0 0 24 24"
fill="none"
stroke="currentColor"
stroke-width="2"
>

<path d="M5 20V12M12 20V5M19 20V9"/>

</svg>

</div>


<div>

<div class="stat-label">

Optimizations

</div>

<div class="stat-value">

__SONARR_OPT__

</div>

</div>

</div>


</div>


</a>


</section>



<!-- ======================================================
     SETTINGS
     ====================================================== -->


<div class="settings-wrap">


<a
class="settings-card"
href="/settings"
>


<img
class="settings-image"
src="/settings-icon.png"
alt="Settings"
>


<div>


<div class="settings-name">

Settings

</div>


<div class="settings-type">

Configuration

</div>


<div class="settings-description">

Configure connections, optimizer limits and Smart Optimizer preferences.

</div>


</div>


</a>


</div>



<div class="home-footer">

Media today. A cleaner tomorrow.

</div>


<div class="footer-line"></div>


</main>



<!-- SMART_COMMON_UPDATE_STATUS_FAVICON -->


<!-- SMART_COMMON_ADMIN_UPDATE_MODE -->

</body>

</html>""")


    replacements = {

        "__BASE_CSS__":
            CSS,

        "__RADARR_COUNT__":
            html.escape(
                str(
                    radarr["count"]
                )
            ),

        "__RADARR_SAVED__":
            html.escape(
                str(
                    radarr["saved"]
                )
            ),

        "__RADARR_OPT__":
            html.escape(
                str(
                    radarr["optimizations"]
                )
            ),

        "__SONARR_COUNT__":
            html.escape(
                str(
                    sonarr["count"]
                )
            ),

        "__SONARR_SAVED__":
            html.escape(
                str(
                    sonarr["saved"]
                )
            ),

        "__SONARR_OPT__":
            html.escape(
                str(
                    sonarr["optimizations"]
                )
            ),

    }


    for key, value in replacements.items():

        rendered = rendered.replace(
            key,
            value
        )


    return rendered




# ============================================================
# SMART OPTIMIZER UPDATE SYSTEM
# ============================================================

def _update_version_key(value):
    parts = [
        int(x)
        for x in re.findall(
            r"\d+",
            str(value or "")
        )[:4]
    ]

    while len(parts) < 4:
        parts.append(0)

    return tuple(parts)


def _update_json_write(path, value):
    os.makedirs(
        os.path.dirname(path)
        or ".",
        exist_ok=True
    )

    tmp = (
        path
        + ".tmp-%d"
        % os.getpid()
    )

    with open(
        tmp,
        "w",
        encoding="utf-8"
    ) as handle:

        json.dump(
            value,
            handle,
            ensure_ascii=False,
            indent=2
        )

        handle.flush()
        os.fsync(
            handle.fileno()
        )

    os.replace(
        tmp,
        path
    )


def _update_json_read(path):
    try:
        with open(
            path,
            "r",
            encoding="utf-8"
        ) as handle:

            value = json.load(
                handle
            )

            return (
                value
                if isinstance(
                    value,
                    dict
                )
                else {}
            )

    except Exception:
        return {}


def update_runtime_status():
    return _update_json_read(
        UPDATE_STATUS_FILE
    )


def latest_update_release(
    force=False
):
    now = time.time()

    with UPDATE_LOCK:

        cached = (
            UPDATE_CACHE.get(
                "release"
            )
        )

        loaded = float(
            UPDATE_CACHE.get(
                "loaded"
            )
            or 0
        )

        if (
            not force
            and cached
            and now - loaded < 300
        ):
            return dict(cached)


        request = urllib.request.Request(
            UPDATE_RELEASE_API,
            headers={
                "Accept":
                    "application/vnd.github+json",

                "User-Agent":
                    "Smart-Optimizer-UI/%s"
                    % SMART_OPTIMIZER_VERSION,
            }
        )


        with urllib.request.urlopen(
            request,
            timeout=20
        ) as response:

            raw = json.loads(
                response.read().decode(
                    "utf-8"
                )
            )


        if not isinstance(
            raw,
            dict
        ):
            raise RuntimeError(
                "GitHub returned invalid release metadata."
            )


        assets = (
            raw.get("assets")
            or []
        )


        spk_assets = [
            asset
            for asset in assets
            if str(
                asset.get("name")
                or ""
            ).lower().endswith(
                ".spk"
            )
        ]


        app_assets = [
            asset
            for asset in assets
            if (
                str(
                    asset.get("name")
                    or ""
                )
                .lower()
                .startswith(
                    "smartoptimizerui-app-"
                )
                and str(
                    asset.get("name")
                    or ""
                )
                .lower()
                .endswith(
                    ".tar.gz"
                )
            )
        ]


        # Supervised SPK installs update only the writable app payload.
        # Legacy/non-supervised installs keep the verified SPK flow.
        if SMART_SELF_UPDATE_MODE == "app-bundle":

            chosen = (
                app_assets[0]
                if app_assets
                else None
            )

        else:

            offline = [
                asset
                for asset in spk_assets
                if "offline" in str(
                    asset.get("name")
                    or ""
                ).lower()
            ]


            chosen = (
                offline[0]
                if offline
                else (
                    spk_assets[0]
                    if spk_assets
                    else None
                )
            )


        tag = str(
            raw.get("tag_name")
            or ""
        ).strip()

        version = (
            tag.lstrip("vV")
            if tag
            else ""
        )


        release = {
            "tag": tag,
            "version": version,

            "name": str(
                raw.get("name")
                or tag
                or "Latest release"
            ),

            "notes": str(
                raw.get("body")
                or ""
            ),

            "published_at": str(
                raw.get("published_at")
                or ""
            ),

            "html_url": str(
                raw.get("html_url")
                or ""
            ),

            "asset": chosen,
        }


        UPDATE_CACHE[
            "loaded"
        ] = now

        UPDATE_CACHE[
            "release"
        ] = dict(
            release
        )


        return release


def download_latest_update():
    release = latest_update_release(
        force=True
    )

    asset = release.get(
        "asset"
    )


    if not asset:
        raise RuntimeError(
            (
                "The latest GitHub release has no application update bundle."
                if SMART_SELF_UPDATE_MODE == "app-bundle"
                else "The latest GitHub release has no SPK package."
            )
        )


    asset_url = str(
        asset.get(
            "browser_download_url"
        )
        or ""
    ).strip()


    filename = os.path.basename(
        str(
            asset.get("name")
            or ""
        ).strip()
    )


    if SMART_SELF_UPDATE_MODE == "app-bundle":

        valid_asset = (
            bool(
                asset_url
                and filename
            )
            and filename.lower().startswith(
                "smartoptimizerui-app-"
            )
            and filename.lower().endswith(
                ".tar.gz"
            )
        )

        invalid_message = (
            "Invalid application update asset."
        )

    else:

        valid_asset = (
            bool(
                asset_url
                and filename
            )
            and filename.lower().endswith(
                ".spk"
            )
        )

        invalid_message = (
            "Invalid SPK release asset."
        )


    if not valid_asset:
        raise RuntimeError(
            invalid_message
        )


    os.makedirs(
        UPDATE_DIR,
        exist_ok=True
    )


    final_path = os.path.join(
        UPDATE_DIR,
        filename
    )


    temporary = (
        final_path
        + ".download"
    )


    request = urllib.request.Request(
        asset_url,
        headers={
            "Accept":
                "application/octet-stream",

            "User-Agent":
                "Smart-Optimizer-UI/%s"
                % SMART_OPTIMIZER_VERSION,
        }
    )


    digest = hashlib.sha256()


    try:

        with urllib.request.urlopen(
            request,
            timeout=90
        ) as response:

            with open(
                temporary,
                "wb"
            ) as output:

                while True:

                    chunk = response.read(
                        1024 * 1024
                    )

                    if not chunk:
                        break

                    output.write(
                        chunk
                    )

                    digest.update(
                        chunk
                    )


                output.flush()

                os.fsync(
                    output.fileno()
                )


        actual_sha = (
            digest.hexdigest()
        )


        expected_digest = str(
            asset.get("digest")
            or ""
        ).strip().lower()


        if expected_digest.startswith(
            "sha256:"
        ):

            expected_sha = (
                expected_digest.split(
                    ":",
                    1
                )[1]
                .strip()
            )


            if actual_sha.lower() != expected_sha:

                raise RuntimeError(
                    "Downloaded SPK failed SHA-256 verification."
                )


        os.replace(
            temporary,
            final_path
        )


    except Exception:

        try:
            os.remove(
                temporary
            )
        except OSError:
            pass

        raise


    version = str(
        release.get("version")
        or ""
    )


    if SMART_SELF_UPDATE_MODE == "app-bundle":

        status = {
            "state": "queued",

            "message":
                "Update verified. Smart Optimizer will restart automatically.",

            "current_version":
                SMART_OPTIMIZER_VERSION,

            "target_version":
                version,

            "filename":
                filename,

            "sha256":
                actual_sha,

            "updated_at":
                int(time.time()),
        }


        _update_json_write(
            UPDATE_STATUS_FILE,
            status
        )


        _update_json_write(
            APP_UPDATE_REQUEST_FILE,
            {
                "action":
                    "install-app-bundle",

                "current_version":
                    SMART_OPTIMIZER_VERSION,

                "target_version":
                    version,

                "filename":
                    filename,

                "bundle_path":
                    final_path,

                "sha256":
                    actual_sha,

                # Give the HTTP response time to reach the browser.
                "not_before":
                    int(time.time()) + 5,

                "requested_at":
                    int(time.time()),
            }
        )

    else:

        status = {
            "state": "ready",

            "message":
                "Verified SPK ready for manual installation.",

            "current_version":
                SMART_OPTIMIZER_VERSION,

            "target_version":
                version,

            "filename":
                filename,

            "sha256":
                actual_sha,

            "updated_at":
                int(time.time()),
        }


        _update_json_write(
            UPDATE_STATUS_FILE,
            status
        )


    return {
        "release":
            release,

        "filename":
            filename,

        "sha256":
            actual_sha,

        "path":
            final_path,
    }


def verified_update_file():
    status = update_runtime_status()

    filename = os.path.basename(
        str(
            status.get(
                "filename"
            )
            or ""
        ).strip()
    )

    target_version = str(
        status.get(
            "target_version"
        )
        or ""
    ).strip()

    if (
        str(
            status.get(
                "state"
            )
            or ""
        )
        != "ready"
        or not filename
        or not filename.lower().endswith(
            ".spk"
        )
    ):
        raise RuntimeError(
            "No verified SPK is ready for download."
        )

    path = os.path.join(
        UPDATE_DIR,
        filename
    )

    if not os.path.isfile(
        path
    ):
        raise RuntimeError(
            "The verified SPK file is no longer available."
        )

    return (
        filename,
        path,
        target_version,
    )


def update_status_payload():
    status = (
        update_runtime_status()
    )

    payload = {
        "installed":
            SMART_OPTIMIZER_VERSION,

        "state":
            str(
                status.get(
                    "state"
                )
                or "idle"
            ),

        "message":
            str(
                status.get(
                    "message"
                )
                or ""
            ),

        "target_version":
            str(
                status.get(
                    "target_version"
                )
                or ""
            ),

        "updated_at":
            status.get(
                "updated_at"
            ),
    }


    try:

        latest = latest_update_release(
            force=False
        )

        payload[
            "latest"
        ] = str(
            latest.get(
                "version"
            )
            or ""
        )

        payload[
            "update_available"
        ] = (
            _update_version_key(
                payload["latest"]
            )
            >
            _update_version_key(
                SMART_OPTIMIZER_VERSION
            )
        )


    except Exception as exc:

        payload[
            "latest"
        ] = ""

        payload[
            "update_available"
        ] = False

        payload[
            "check_error"
        ] = str(
            exc
        )


    return payload


def updates_page(
    message="",
    bad=False,
    force=False
):
    release = None
    release_error = ""


    try:

        release = latest_update_release(
            force=force
        )

    except Exception as exc:

        release_error = str(
            exc
        )


    latest_version = (
        str(
            (release or {}).get(
                "version"
            )
            or ""
        )
    )


    available = bool(
        latest_version
        and _update_version_key(
            latest_version
        )
        >
        _update_version_key(
            SMART_OPTIMIZER_VERSION
        )
    )


    runtime = (
        update_runtime_status()
    )


    if release_error:

        availability = (
            "Update server unavailable"
        )

        availability_class = (
            "offline"
        )

    elif available:

        availability = (
            "Update available"
        )

        availability_class = (
            "available"
        )

    else:

        availability = (
            "Up to date"
        )

        availability_class = (
            "current"
        )


    notes = str(
        (release or {}).get(
            "notes"
        )
        or "No release notes were provided."
    )


    asset = (
        (release or {}).get(
            "asset"
        )
        or {}
    )


    asset_name = str(
        asset.get(
            "name"
        )
        or "No update asset"
    )


    asset_size = int(
        asset.get(
            "size"
        )
        or 0
    )


    size_text = (
        "%.1f MB"
        % (
            asset_size
            / 1024.0
            / 1024.0
        )
        if asset_size
        else "—"
    )


    runtime_state = str(
        runtime.get(
            "state"
        )
        or "idle"
    )


    runtime_message = str(
        runtime.get(
            "message"
        )
        or ""
    )


    prepared_update = False

    if SMART_SELF_UPDATE_MODE != "app-bundle":

        try:
            (
                _prepared_name,
                _prepared_path,
                _prepared_version,
            ) = verified_update_file()

            prepared_update = bool(
                latest_version
                and _prepared_version
                == latest_version
            )

        except Exception:
            prepared_update = False


    notice = ""

    if message:

        notice = (
            "<div class='update-notice%s'>%s</div>"
            % (
                " bad"
                if bad
                else "",

                html.escape(
                    message
                ),
            )
        )


    if release_error:

        notes_html = (
            "<div class='update-empty'>"
            "Could not contact GitHub Releases: %s"
            "</div>"
            % html.escape(
                release_error
            )
        )

    else:

        notes_html = (
            "<pre class='update-notes'>%s</pre>"
            % html.escape(
                notes
            )
        )


    download_disabled = (
        " disabled"
        if not asset
        else ""
    )


    if (
        SMART_SELF_UPDATE_MODE == "app-bundle"
        and runtime_state
        in (
            "queued",
            "installing",
        )
    ):

        update_action = (
            "<button "
            "id='updateButton' "
            "class='update-btn' "
            "type='button' "
            "disabled>"
            "Updating…"
            "</button>"
        )

    elif (
        SMART_SELF_UPDATE_MODE == "app-bundle"
        and not available
    ):

        update_action = (
            "<button "
            "id='updateButton' "
            "class='update-btn' "
            "type='button' "
            "disabled>"
            "Up to date"
            "</button>"
        )

    elif SMART_SELF_UPDATE_MODE == "app-bundle":

        update_action = (
            "<form "
            "id='updateForm' "
            "method='post' "
            "action='/update-download'>"
            "<button "
            "id='updateButton' "
            "class='update-btn' "
            "type='submit'%s>"
            "Update now"
            "</button>"
            "</form>"
            % download_disabled
        )

    elif prepared_update:

        update_action = (
            "<a "
            "id='updateButton' "
            "class='update-btn' "
            "href='/update-package-file'>"
            "Download verified SPK"
            "</a>"
        )

    else:

        update_action = (
            "<form "
            "id='updateForm' "
            "method='post' "
            "action='/update-download'>"
            "<button "
            "id='updateButton' "
            "class='update-btn' "
            "type='submit'%s>"
            "Prepare update"
            "</button>"
            "</form>"
            % download_disabled
        )


    rendered = _smart_expand_common("""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Smart Optimizer · Updates</title>

<style>
__BASE_CSS__

*{box-sizing:border-box}

html,
body{
    margin:0;
    min-height:100%%;
}

body.updatebody{
    min-height:100vh;
    color:#eef5ff;
    font-family:
        Inter,
        system-ui,
        -apple-system,
        BlinkMacSystemFont,
        "Segoe UI",
        sans-serif;

    background:#020811;
    position:relative;
    overflow-x:hidden;
}

.update-bg{
    position:fixed;
    inset:0;
    z-index:0;

    background:
        linear-gradient(
            180deg,
            rgba(1,7,14,.20),
            rgba(1,7,14,.34)
        ),
        url('/home-background.png')
        center center / cover no-repeat;
}

.update-bg::after{
    content:"";
    position:absolute;
    inset:0;

    background:
        radial-gradient(
            circle at 50%% 26%%,
            rgba(40,211,143,.08),
            rgba(0,14,31,.10) 42%%,
            rgba(0,3,10,.28) 100%%
        );
}

.update-shell{
    position:relative;
    z-index:1;

    width:min(
        1180px,
        calc(100vw - 42px)
    );

    margin:0 auto;
    padding:28px 0 42px;
}

.update-top{
    display:flex;
    justify-content:space-between;
    align-items:flex-start;
    gap:20px;
    margin-bottom:24px;
}

.update-heading{
    display:flex;
    gap:14px;
    align-items:center;
}

.update-icon{
    width:48px;
    height:48px;
    display:flex;
    align-items:center;
    justify-content:center;

    border-radius:15px;

    font-size:1.55rem;

    background:
        linear-gradient(
            145deg,
            rgba(17,92,68,.80),
            rgba(6,35,38,.82)
        );

    border:
        1px solid
        rgba(73,235,166,.25);

    box-shadow:
        0 10px 26px rgba(0,0,0,.25),
        0 0 20px rgba(55,222,157,.10);
}

.update-heading h1{
    margin:0;
    font-size:2rem;
    letter-spacing:-.03em;
}

.update-heading p{
    margin:5px 0 0;
    color:rgba(211,225,242,.70);
}

.update-nav{
    display:flex;
    gap:9px;
    flex-wrap:wrap;
}

.update-nav a{
    min-height:42px;
    padding:0 16px;

    display:inline-flex;
    align-items:center;
    justify-content:center;

    border-radius:999px;

    color:#eef7ff;
    text-decoration:none;
    font-size:.88rem;
    font-weight:650;

    background:
        rgba(9,25,42,.72);

    border:
        1px solid
        rgba(43,180,255,.20);

    box-shadow:
        0 10px 24px
        rgba(0,0,0,.16);
}

.update-nav a:hover{
    transform:translateY(-1px);
    border-color:
        rgba(75,229,158,.70);

    box-shadow:
        0 10px 24px rgba(0,0,0,.20),
        0 0 20px rgba(55,222,157,.16);
}

.update-notice{
    margin-bottom:18px;
    padding:14px 16px;

    border-radius:14px;

    color:#dcfff0;

    background:
        rgba(8,57,42,.72);

    border:
        1px solid
        rgba(66,227,145,.30);
}

.update-notice.bad{
    color:#ffdcd7;

    background:
        rgba(91,22,30,.55);

    border-color:
        rgba(255,98,84,.38);
}

.update-hero{
    padding:24px;

    border-radius:24px;

    background:
        linear-gradient(
            145deg,
            rgba(8,28,45,.92),
            rgba(4,15,28,.91)
        );

    border:
        1px solid
        rgba(70,225,159,.21);

    backdrop-filter:blur(16px);

    box-shadow:
        0 24px 62px
        rgba(0,0,0,.34);
}

.update-version-grid{
    display:grid;
    grid-template-columns:
        repeat(3,minmax(0,1fr));
    gap:16px;
}

.update-version-card{
    min-height:126px;

    padding:18px;

    border-radius:18px;

    background:
        rgba(3,15,27,.62);

    border:
        1px solid
        rgba(255,255,255,.065);
}

.update-version-label{
    color:
        rgba(201,218,239,.60);

    font-size:.76rem;
    font-weight:700;
    letter-spacing:.08em;
    text-transform:uppercase;
}

.update-version-value{
    margin-top:9px;

    font-size:1.6rem;
    font-weight:760;
    letter-spacing:-.025em;
}

.update-version-sub{
    margin-top:7px;

    color:
        rgba(205,220,239,.64);

    font-size:.84rem;
}

.update-status-pill{
    display:inline-flex;
    align-items:center;

    min-height:35px;

    margin-top:10px;
    padding:0 12px;

    border-radius:999px;

    font-size:.82rem;
    font-weight:720;
}

.update-status-pill.current{
    color:#d9ffec;

    border:
        1px solid
        rgba(72,228,151,.32);

    background:
        rgba(17,91,61,.38);
}

.update-status-pill.available{
    color:#e6fff5;

    border:
        1px solid
        rgba(51,235,164,.52);

    background:
        rgba(16,116,75,.48);

    box-shadow:
        0 0 22px
        rgba(44,225,150,.13);
}

.update-status-pill.offline{
    color:#ffe2dc;

    border:
        1px solid
        rgba(255,113,90,.34);

    background:
        rgba(104,40,34,.42);
}

.update-section{
    margin-top:20px;

    overflow:hidden;

    border-radius:22px;

    background:
        linear-gradient(
            145deg,
            rgba(9,27,45,.88),
            rgba(4,15,27,.88)
        );

    border:
        1px solid
        rgba(43,180,255,.16);

    box-shadow:
        0 20px 54px
        rgba(0,0,0,.27);
}

.update-section-head{
    padding:18px 21px;

    border-bottom:
        1px solid
        rgba(255,255,255,.06);
}

.update-section-head h2{
    margin:0;
    font-size:1.25rem;
}

.update-section-head p{
    margin:6px 0 0;
    color:
        rgba(207,221,240,.64);
}

.update-notes{
    margin:0;
    padding:22px;

    max-height:440px;
    overflow:auto;

    white-space:pre-wrap;
    overflow-wrap:anywhere;

    color:
        rgba(232,242,255,.88);

    font:
        .90rem/1.62
        Inter,
        system-ui,
        sans-serif;

    background:
        rgba(2,10,19,.34);
}

.update-empty{
    padding:22px;
    color:
        rgba(219,229,244,.70);
}

.update-package{
    display:grid;
    grid-template-columns:
        minmax(0,1fr)
        auto;

    align-items:center;
    gap:20px;

    padding:20px 22px;
}

.update-package-name{
    font-weight:700;
    overflow-wrap:anywhere;
}

.update-package-meta{
    margin-top:5px;
    color:
        rgba(202,219,239,.62);
    font-size:.84rem;
}

.update-btn{
    min-height:46px;
    padding:0 20px;

    display:inline-flex;
    align-items:center;
    justify-content:center;
    text-decoration:none;

    border-radius:13px;

    border:
        1px solid
        rgba(73,235,166,.34);

    color:#effff8;
    font:inherit;
    font-weight:740;

    cursor:pointer;

    background:
        linear-gradient(
            135deg,
            rgba(25,157,104,.92),
            rgba(10,93,67,.94)
        );

    box-shadow:
        0 10px 24px rgba(0,0,0,.22),
        0 0 20px rgba(53,223,154,.10);
}

.update-btn:hover:not(:disabled){
    transform:
        translateY(-1px);

    border-color:
        rgba(86,242,177,.72);

    box-shadow:
        0 12px 28px rgba(0,0,0,.25),
        0 0 26px rgba(53,223,154,.20);
}

.update-btn:disabled{
    opacity:.42;
    cursor:not-allowed;
}

.update-runtime{
    margin-top:18px;

    padding:16px 18px;

    border-radius:16px;

    background:
        rgba(6,21,35,.70);

    border:
        1px solid
        rgba(255,255,255,.065);

    color:
        rgba(216,230,247,.76);
}

.update-runtime strong{
    color:#efffff;
}

.update-runtime-message{
    margin-top:5px;
}

.update-refresh{
    margin-left:8px;
    color:#89d9ff;
    text-decoration:none;
}

@media(max-width:760px){

    .update-shell{
        width:
            calc(100vw - 22px);
    }

    .update-top{
        flex-direction:column;
    }

    .update-version-grid{
        grid-template-columns:1fr;
    }

    .update-package{
        grid-template-columns:1fr;
    }

    .update-btn{
        width:100%%;
    }
}

</style>
  <link rel="icon" type="image/png" sizes="32x32" href="/favicon.ico?v=2">
</head>


<body class="updatebody">

<div class="update-bg"></div>

<main class="update-shell">

    <div class="update-top">

        <div class="update-heading">

            <div class="update-icon">
                ⇧
            </div>

            <div>
                <h1>Updates</h1>

                <p>
                    Smart Optimizer release channel
                </p>
            </div>

        </div>


        <div class="update-nav">

            <a href="/settings">
                Settings
            </a>

            <a href="/">
                Smart Optimizer
            </a>

            <a href="/radarr">
                Radarr
            </a>

            <a href="/sonarr">
                Sonarr
            </a>

        </div>

    </div>


    __NOTICE__


    <section class="update-hero">

        <div class="update-version-grid">

            <div class="update-version-card">

                <div class="update-version-label">
                    Installed version
                </div>

                <div class="update-version-value">
                    v__CURRENT_VERSION__
                </div>

                <div class="update-version-sub">
                    Currently running
                </div>

            </div>


            <div class="update-version-card">

                <div class="update-version-label">
                    Latest release
                </div>

                <div class="update-version-value">
                    __LATEST_VERSION__
                </div>

                <div class="update-version-sub">
                    Official GitHub release
                </div>

            </div>


            <div class="update-version-card">

                <div class="update-version-label">
                    Update status
                </div>

                <div
                    class="update-status-pill __AVAILABILITY_CLASS__"
                >
                    __AVAILABILITY__
                </div>

                <div class="update-version-sub">
                    <a
                        class="update-refresh"
                        href="/updates?refresh=1"
                    >
                        Check again
                    </a>
                </div>

            </div>

        </div>


        <div
            id="updateRuntime"
            class="update-runtime"
        >

            <strong>
                Updater:
            </strong>

            <span id="updateRuntimeState">
                __RUNTIME_STATE__
            </span>

            <div
                id="updateRuntimeMessage"
                class="update-runtime-message"
            >
                __RUNTIME_MESSAGE__
            </div>

        </div>

    </section>


    <section class="update-section">

        <div class="update-section-head">

            <h2>
                What's new
            </h2>

            <p>
                Release notes from GitHub.
            </p>

        </div>

        __RELEASE_NOTES__

    </section>


    <section class="update-section">

        <div class="update-section-head">

            <h2>
                Package update
            </h2>

            <p>
                __UPDATE_EXPLANATION__
            </p>

        </div>


        <div class="update-package">

            <div>

                <div class="update-package-name">
                    __ASSET_NAME__
                </div>

                <div class="update-package-meta">
                    __ASSET_SIZE__
                    · GitHub Release
                    · SHA-256 verified before download
                </div>

            </div>


            __UPDATE_ACTION__

        </div>

    </section>

</main>


<script>

(function(){

    const form =
        document.getElementById(
            "updateForm"
        );

    const button =
        document.getElementById(
            "updateButton"
        );


    if(form && button){

        form.addEventListener(
            "submit",
            function(){

                button.disabled = true;

                button.textContent =
                    (
                        "__SELF_UPDATE_MODE__" === "app-bundle"
                        ? "Updating…"
                        : "Downloading…"
                    );

            }
        );

    }


    async function pollUpdate(){

        try{

            const response =
                await fetch(
                    "/update-status",
                    {
                        cache:"no-store"
                    }
                );


            if(!response.ok){
                return;
            }


            const data =
                await response.json();


            const state =
                document.getElementById(
                    "updateRuntimeState"
                );

            const message =
                document.getElementById(
                    "updateRuntimeMessage"
                );


            if(state){

                state.textContent =
                    data.state
                    || "idle";

            }


            if(message){

                message.textContent =
                    data.message
                    || "";

            }


            if(
                "__SELF_UPDATE_MODE__" === "app-bundle"
                &&
                data.state === "updated"
                &&
                data.installed
                &&
                data.target_version
                &&
                data.installed === data.target_version
            ){

                window.setTimeout(
                    function(){
                        window.location.href =
                            "/updates?refresh=1";
                    },
                    900
                );

            }


        }catch(_error){
        }

    }


    pollUpdate();

    setInterval(
        pollUpdate,
        2500
    );

})();

</script>



<!-- SMART_COMMON_UPDATE_STATUS_FAVICON -->


<!-- SMART_COMMON_ADMIN_UPDATE_MODE -->

</body>
</html>""")


    replacements = {

        "__BASE_CSS__":
            CSS,

        "__NOTICE__":
            notice,

        "__CURRENT_VERSION__":
            html.escape(
                SMART_OPTIMIZER_VERSION
            ),

        "__LATEST_VERSION__":
            (
                "v"
                + html.escape(
                    latest_version
                )
                if latest_version
                else "Unavailable"
            ),

        "__AVAILABILITY_CLASS__":
            availability_class,

        "__AVAILABILITY__":
            html.escape(
                availability
            ),

        "__RUNTIME_STATE__":
            html.escape(
                runtime_state
            ),

        "__RUNTIME_MESSAGE__":
            html.escape(
                runtime_message
                or "Ready"
            ),

        "__RELEASE_NOTES__":
            notes_html,

        "__UPDATE_EXPLANATION__":
            (
                "Download, verify, install, and restart automatically. "
                "The Synology package stays in place while the writable "
                "Smart Optimizer application payload is updated, similar "
                "to the in-app updater model used by Radarr."
                if SMART_SELF_UPDATE_MODE == "app-bundle"
                else
                "Prepare and verify the latest SPK release. "
                "Download the verified SPK here, then install it over "
                "the current version using DSM Package Center > "
                "Manual Install. Your saved settings are preserved."
            ),

        "__ASSET_NAME__":
            html.escape(
                asset_name
            ),

        "__ASSET_SIZE__":
            html.escape(
                size_text
            ),

        "__DOWNLOAD_DISABLED__":
            download_disabled,

        "__SELF_UPDATE_MODE__":
            html.escape(
                SMART_SELF_UPDATE_MODE
            ),

        "__UPDATE_ACTION__":
            update_action,

    }


    for key, value in replacements.items():

        rendered = rendered.replace(
            key,
            value
        )


    return rendered



def settings_page(message="", bad=False):
    rcfg, scfg = connection("radarr"), connection("sonarr")
    acfg = load_auth_config()
    auth_on = auth_enabled()
    auth_user = str(acfg.get("username") or "admin")

    notice = ""
    if message:
        notice = "<div class='cfg-notice%s'>%s</div>" % (
            " bad" if bad else "",
            html.escape(message)
        )

    def connection_card(app, cfg, default_port, icon_src, title, desc, theme):
        masked = "Configured · leave blank to keep current key" if cfg["api_key"] else "Not configured"
        budget = daily_search_budget(app)
        status = "Configured" if cfg["api_key"] else "Setup"

        return """<section class="cfg-card %s">
<div class="cfg-card-head">
  <div class="cfg-card-titlewrap">
    <div class="cfg-card-icon"><img src="%s" alt="%s"></div>
    <div class="cfg-card-titletext">
      <h2>%s</h2>
      <p>%s</p>
    </div>
  </div>
  <div class="cfg-status %s">%s</div>
</div>

<form method="post" action="/connection-settings" class="cfg-form">
  <input type="hidden" name="app" value="%s">

  <div class="cfg-row">
    <label class="cfg-label">Protocol</label>
    <select name="scheme">
      <option value="http"%s>http</option>
      <option value="https"%s>https</option>
    </select>
  </div>

  <div class="cfg-row">
    <label class="cfg-label">IP / hostname</label>
    <input name="host" value="%s" placeholder="127.0.0.1" required>
  </div>

  <div class="cfg-row">
    <label class="cfg-label">Port</label>
    <input name="port" type="number" min="1" max="65535" value="%d" placeholder="%d" required>
  </div>

  <div class="cfg-row">
    <label class="cfg-label">API key</label>
    <input name="api_key" type="password" value="" placeholder="%s" autocomplete="new-password">
  </div>

  <div class="cfg-actions">
    <button class="cfg-btn cfg-btn-ghost" name="action" value="test" type="submit">Test connection</button>
    <button class="cfg-btn cfg-btn-primary %s" name="action" value="save" type="submit">Save</button>
  </div>
</form>

<form method="post" action="/budget-settings" class="cfg-form cfg-budget-form">
  <input type="hidden" name="app" value="%s">
  <div class="cfg-row">
    <label class="cfg-label">Daily search budget</label>
    <input name="budget" type="number" min="1" max="100000" value="%d" required>
  </div>
  <div class="cfg-actions">
    <button class="cfg-btn cfg-btn-primary %s" type="submit">Save budget</button>
  </div>
</form>
</section>""" % (
            theme,
            icon_src,
            html.escape(title, quote=True),
            html.escape(title),
            html.escape(desc),
            theme,
            html.escape(status),
            app,
            " selected" if cfg["scheme"] == "http" else "",
            " selected" if cfg["scheme"] == "https" else "",
            html.escape(cfg["host"], quote=True),
            cfg["port"],
            default_port,
            html.escape(masked, quote=True),
            "accent-orange" if theme == "radarr" else "accent-blue",
            app,
            budget,
            "accent-orange" if theme == "radarr" else "accent-blue"
        )

    auth_card = """<section class="cfg-card auth">
<div class="cfg-card-head">
  <div class="cfg-card-titlewrap">
    <div class="cfg-card-icon"><img src="/settings-icon.png" alt="Settings"></div>
    <div class="cfg-card-titletext">
      <h2>UI authentication</h2>
      <p>Require a username and password before opening Smart Optimizer.</p>
    </div>
  </div>
  <div class="cfg-status auth">%s</div>
</div>

<form method="post" action="/auth-settings" class="cfg-form">
  <div class="cfg-row cfg-row-switch">
    <label class="cfg-label">Authentication</label>
    <label class="cfg-check">
      <input name="enabled" type="checkbox" value="1"%s>
      <span>Enabled</span>
    </label>
  </div>

  <div class="cfg-auth-grid">
    <div class="cfg-field">
      <label class="cfg-label">Username</label>
      <input name="username" value="%s" autocomplete="username" required>
    </div>

    <div class="cfg-field">
      <label class="cfg-label">Password</label>
      <input name="password" type="password" autocomplete="new-password" placeholder="%s">
    </div>

    <div class="cfg-field">
      <label class="cfg-label">Confirm password</label>
      <input name="confirmation" type="password" autocomplete="new-password" placeholder="Repeat new password">
    </div>
  </div>

  <div class="cfg-actions">
    %s
    <button class="cfg-btn cfg-btn-primary accent-green" type="submit">Save authentication</button>
  </div>
</form>
</section>""" % (
        "Enabled" if auth_on else "Disabled",
        " checked" if auth_on else "",
        html.escape(auth_user, quote=True),
        "Leave blank to keep current password" if acfg.get("password_hash") else "Minimum 8 characters",
        '<a class="cfg-btn cfg-btn-ghost" href="/logout">Log out</a>' if auth_on else ""
    )

    rendered = _smart_expand_common("""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Smart Optimizer · Settings</title>
<style>
__BASE_CSS__

*{box-sizing:border-box}

html,
body{
    margin:0;
    min-height:100%;
}

body.settingsbody{
    min-height:100vh;
    color:#eef5ff;
    font-family:Inter,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
    background:#020811;
    position:relative;
    overflow-x:hidden;
}

.settings-bg{
    position:fixed;
    inset:0;
    z-index:0;
    background:
        linear-gradient(180deg,rgba(1,7,14,.18),rgba(1,7,14,.30)),
        url('/home-background.png?v=20260923-233901') center center / cover no-repeat;
}

.settings-bg::after{
    content:"";
    position:absolute;
    inset:0;
    background:
        radial-gradient(circle at center,
        rgba(0,160,255,.04) 0%%,
        rgba(0,7,18,.08) 46%%,
        rgba(0,3,10,.22) 100%%);
}

.settings-shell{
    position:relative;
    z-index:1;
    width:min(1280px, calc(100vw - 42px));
    margin:0 auto;
    padding:28px 0 38px;
}

.settings-top{
    display:flex;
    align-items:flex-start;
    justify-content:space-between;
    gap:18px;
    margin-bottom:22px;
}

.settings-heading{
    display:flex;
    align-items:center;
    gap:14px;
}

.settings-heading-icon{
    width:46px;
    height:46px;
    flex:0 0 46px;
}

.settings-heading-icon img{
    width:100%;
    height:100%;
    object-fit:contain;
    display:block;
    filter:drop-shadow(0 8px 16px rgba(0,0,0,.28));
}

.settings-heading-copy h1{
    margin:0;
    font-size:2rem;
    line-height:1.12;
    font-weight:760;
    letter-spacing:-.03em;
}

.settings-heading-copy p{
    margin:6px 0 0;
    color:rgba(209,222,241,.70);
    font-size:.96rem;
}

.settings-home-link{
    display:inline-flex;
    align-items:center;
    justify-content:center;
    gap:8px;
    min-height:42px;
    padding:0 16px;
    border-radius:999px;
    color:#eef7ff;
    text-decoration:none;
    background:rgba(9,25,42,.70);
    border:1px solid rgba(43,180,255,.26);
    box-shadow:0 10px 24px rgba(0,0,0,.18);
    white-space:nowrap;
}

.settings-home-link:hover{
    border-color:rgba(66,201,255,.62);
    box-shadow:0 12px 28px rgba(0,0,0,.22), 0 0 18px rgba(0,174,255,.10);
}

.settings-nav{
    display:flex;
    align-items:center;
    justify-content:flex-end;
    gap:9px;
    flex-wrap:wrap;
}

.settings-nav-link{
    display:inline-flex;
    align-items:center;
    justify-content:center;

    min-height:42px;

    padding:
        0 16px;

    border-radius:999px;

    color:#eef7ff;

    text-decoration:none;

    font-size:.88rem;
    font-weight:650;

    background:
        rgba(9,25,42,.70);

    border:
        1px solid
        rgba(43,180,255,.18);

    box-shadow:
        0 10px 24px
        rgba(0,0,0,.16);

    transition:
        border-color .18s ease,
        box-shadow .18s ease,
        background .18s ease,
        transform .18s ease;
}

.settings-nav-link:hover{
    transform:translateY(-1px);
}

.settings-nav-link.radarr:hover{
    border-color:
        rgba(255,112,72,.72);

    background:
        rgba(67,27,27,.72);

    box-shadow:
        0 10px 24px rgba(0,0,0,.20),
        0 0 18px rgba(255,91,51,.16);
}

.settings-nav-link.sonarr:hover{
    border-color:
        rgba(42,191,255,.78);

    background:
        rgba(8,42,70,.76);

    box-shadow:
        0 10px 24px rgba(0,0,0,.20),
        0 0 18px rgba(0,174,255,.16);
}

.settings-nav-link.updates:hover{
    border-color:
        rgba(75,229,158,.78);

    background:
        rgba(12,64,47,.76);

    box-shadow:
        0 10px 24px rgba(0,0,0,.20),
        0 0 18px rgba(55,222,157,.17);
}

.cfg-notice{
    margin-bottom:18px;
    padding:13px 15px;
    border-radius:14px;
    color:#def4ff;
    background:rgba(6,34,53,.78);
    border:1px solid rgba(33,182,255,.30);
    box-shadow:0 10px 24px rgba(0,0,0,.16);
}

.cfg-notice.bad{
    color:#ffd9d3;
    background:rgba(91,22,30,.54);
    border-color:rgba(255,98,84,.36);
}

.cfg-grid{
    display:grid;
    grid-template-columns:repeat(2, minmax(0, 1fr));
    gap:24px;
}

.cfg-card{
    border-radius:22px;
    overflow:hidden;
    background:
        linear-gradient(145deg,
        rgba(10,25,43,.90),
        rgba(5,16,29,.88));
    border:1px solid rgba(38,179,255,.18);
    backdrop-filter:blur(14px);
    -webkit-backdrop-filter:blur(14px);
    box-shadow:
        0 22px 56px rgba(0,0,0,.32),
        inset 0 0 0 1px rgba(255,255,255,.015);
}

.cfg-card.radarr{
    border-color:rgba(255,113,78,.24);
}

.cfg-card.sonarr{
    border-color:rgba(40,186,255,.24);
}

.cfg-card.auth{
    grid-column:1 / -1;
    border-color:rgba(75,229,158,.24);
}

.cfg-card-head{
    display:flex;
    align-items:flex-start;
    justify-content:space-between;
    gap:18px;
    padding:20px 22px 16px;
    border-bottom:1px solid rgba(255,255,255,.06);
}

.cfg-card-titlewrap{
    display:flex;
    align-items:center;
    gap:14px;
    min-width:0;
}

.cfg-card-icon{
    width:50px;
    height:50px;
    flex:0 0 50px;
    display:flex;
    align-items:center;
    justify-content:center;
    border-radius:14px;
    background:rgba(255,255,255,.025);
    border:1px solid rgba(255,255,255,.06);
}

.cfg-card-icon img{
    width:42px;
    height:42px;
    object-fit:contain;
    display:block;
    filter:drop-shadow(0 8px 14px rgba(0,0,0,.22));
}

.cfg-card-titletext{
    min-width:0;
}

.cfg-card-titletext h2{
    margin:0;
    font-size:1.55rem;
    line-height:1.15;
    font-weight:740;
    letter-spacing:-.025em;
}

.cfg-card-titletext p{
    margin:6px 0 0;
    color:rgba(206,220,241,.68);
    font-size:.92rem;
    line-height:1.45;
}

.cfg-status{
    flex:0 0 auto;
    display:inline-flex;
    align-items:center;
    justify-content:center;
    min-height:34px;
    padding:0 12px;
    border-radius:999px;
    font-size:.78rem;
    font-weight:700;
    letter-spacing:.01em;
    border:1px solid rgba(255,255,255,.08);
    background:rgba(255,255,255,.03);
    color:#f5fbff;
}

.cfg-status.radarr{
    border-color:rgba(255,116,75,.24);
    color:#ffd3c7;
}

.cfg-status.sonarr{
    border-color:rgba(46,191,255,.24);
    color:#d7f4ff;
}

.cfg-status.auth{
    border-color:rgba(64,223,143,.24);
    color:#d6ffe7;
}

.cfg-form{
    padding:18px 22px 22px;
}

.cfg-budget-form{
    border-top:1px solid rgba(255,255,255,.06);
}

.cfg-row{
    display:grid;
    grid-template-columns:170px minmax(0,1fr);
    gap:16px;
    align-items:center;
    padding:11px 0;
}

.cfg-row-switch{
    align-items:center;
}

.cfg-auth-grid{
    display:grid;

    grid-template-columns:
        repeat(3, minmax(0,1fr));

    gap:18px;

    margin-top:10px;

    align-items:end;
}

.cfg-field{
    min-width:0;
}

.cfg-auth-grid .cfg-field:last-child{
    grid-column:auto;
}

/* Keep auth fields compact and balanced */
.cfg-card.auth .cfg-form{
    padding-top:18px;
}

.cfg-card.auth .cfg-auth-grid input{
    width:100%;
    min-width:0;
}

@media(max-width:1050px){

    .cfg-auth-grid{
        grid-template-columns:
            repeat(2, minmax(0,1fr));
    }

    .cfg-auth-grid .cfg-field:last-child{
        grid-column:2;
    }

}

@media(max-width:700px){

    .cfg-auth-grid{
        grid-template-columns:1fr;
        gap:14px;
    }

    .cfg-auth-grid .cfg-field:last-child{
        grid-column:auto;
    }

}

.cfg-label{
    display:block;
    color:rgba(216,229,246,.76);
    font-size:.86rem;
    font-weight:600;
    line-height:1.35;
}

.cfg-check{
    display:inline-flex;
    align-items:center;
    gap:10px;
    color:#eff8ff;
    font-size:.88rem;
}

.cfg-check input{
    width:16px;
    height:16px;
    margin:0;
    accent-color:#18bfff;
}

.cfg-form input,
.cfg-form select{
    width:100%%;
    min-height:44px;
    padding:0 13px;
    color:#f6fbff;
    font:inherit;
    border-radius:12px;
    border:1px solid rgba(87,141,202,.28);
    outline:none;
    background:rgba(4,13,24,.70);
    box-shadow:inset 0 0 0 1px rgba(255,255,255,.014);
    transition:border-color .18s ease, box-shadow .18s ease, background .18s ease;
}

.cfg-form input:hover,
.cfg-form select:hover{
    border-color:rgba(77,172,229,.44);
}

.cfg-form input:focus,
.cfg-form select:focus{
    border-color:rgba(47,199,255,.82);
    background:rgba(5,17,32,.86);
    box-shadow:0 0 0 3px rgba(0,174,255,.08), 0 0 20px rgba(0,166,255,.10);
}

.cfg-actions{
    display:flex;
    justify-content:flex-end;
    gap:10px;
    flex-wrap:wrap;
    margin-top:14px;
}

.cfg-btn{
    display:inline-flex;
    align-items:center;
    justify-content:center;
    gap:8px;
    min-height:44px;
    padding:0 16px;
    border-radius:12px;
    text-decoration:none;
    font:inherit;
    font-weight:700;
    color:#f6fbff;
    border:1px solid rgba(64,151,213,.28);
    background:rgba(7,23,39,.74);
    cursor:pointer;
    box-shadow:0 8px 20px rgba(0,0,0,.18);
}

.cfg-btn:hover{
    border-color:rgba(91,199,255,.56);
    box-shadow:0 10px 24px rgba(0,0,0,.20), 0 0 18px rgba(0,174,255,.08);
}

.cfg-btn-ghost{
    background:rgba(6,19,31,.68);
}

.cfg-btn-primary{
    border-color:transparent;
}

.cfg-btn-primary.accent-orange{
    background:linear-gradient(135deg, rgba(170,74,54,.96), rgba(112,40,31,.96));
}

.cfg-btn-primary.accent-orange:hover{
    box-shadow:0 10px 24px rgba(0,0,0,.22), 0 0 18px rgba(255,107,57,.14);
}

.cfg-btn-primary.accent-blue{
    background:linear-gradient(135deg, rgba(24,104,179,.96), rgba(9,58,111,.96));
}

.cfg-btn-primary.accent-blue:hover{
    box-shadow:0 10px 24px rgba(0,0,0,.22), 0 0 18px rgba(0,174,255,.14);
}

.cfg-btn-primary.accent-green{
    background:linear-gradient(135deg, rgba(24,151,101,.96), rgba(11,98,66,.96));
}

.cfg-btn-primary.accent-green:hover{
    box-shadow:0 10px 24px rgba(0,0,0,.22), 0 0 18px rgba(72,220,152,.14);
}

.cfg-footer-note{
    margin-top:14px;
    padding:14px 16px;
    border-radius:16px;
    color:rgba(205,220,240,.72);
    font-size:.84rem;
    line-height:1.5;
    background:rgba(8,24,40,.72);
    border:1px solid rgba(255,255,255,.06);
    box-shadow:0 10px 24px rgba(0,0,0,.14);
}

@media(max-width:1050px){
    .cfg-grid{
        grid-template-columns:1fr;
    }

    .cfg-card.auth{
        grid-column:auto;
    }
}

@media(max-width:760px){
    .settings-shell{
        width:calc(100vw - 22px);
        padding-top:18px;
    }

    .settings-top{
        flex-direction:column;
        align-items:stretch;
    }

    .cfg-row{
        grid-template-columns:1fr;
        gap:8px;
    }

    .cfg-auth-grid{
        grid-template-columns:1fr;
    }

    .cfg-auth-grid .cfg-field:last-child{
        grid-column:auto;
    }

    .cfg-card-head{
        flex-direction:column;
        align-items:flex-start;
    }

    .cfg-actions{
        justify-content:stretch;
    }

    .cfg-btn{
        width:100%%;
    }
}



/* ==========================================================
   SETTINGS BUTTON THEME GLOW
   ========================================================== */

.cfg-btn{
    transition:
        transform .18s ease,
        border-color .18s ease,
        background .18s ease,
        box-shadow .18s ease !important;
}

.cfg-btn:hover{
    transform:translateY(-2px);
}


/* ----------------------------------------------------------
   RADARR BUTTONS = RED / ORANGE
   ---------------------------------------------------------- */

.cfg-card.radarr .cfg-btn:hover{
    border-color:
        rgba(255,112,72,.88) !important;

    background:
        linear-gradient(
            135deg,
            rgba(80,31,27,.88),
            rgba(39,18,22,.88)
        ) !important;

    box-shadow:
        0 10px 24px rgba(0,0,0,.24),
        0 0 10px rgba(255,93,53,.42),
        0 0 24px rgba(255,73,37,.20) !important;
}

.cfg-card.radarr .cfg-btn-primary:hover{
    background:
        linear-gradient(
            135deg,
            rgba(211,82,54,.98),
            rgba(142,45,33,.98)
        ) !important;

    box-shadow:
        0 10px 26px rgba(0,0,0,.25),
        0 0 12px rgba(255,107,63,.50),
        0 0 28px rgba(255,70,31,.22) !important;
}


/* ----------------------------------------------------------
   SONARR BUTTONS = BLUE / CYAN
   ---------------------------------------------------------- */

.cfg-card.sonarr .cfg-btn:hover{
    border-color:
        rgba(47,199,255,.92) !important;

    background:
        linear-gradient(
            135deg,
            rgba(8,57,92,.90),
            rgba(6,30,56,.90)
        ) !important;

    box-shadow:
        0 10px 24px rgba(0,0,0,.24),
        0 0 10px rgba(0,185,255,.46),
        0 0 25px rgba(0,145,255,.22) !important;
}

.cfg-card.sonarr .cfg-btn-primary:hover{
    background:
        linear-gradient(
            135deg,
            rgba(22,128,215,.98),
            rgba(8,75,142,.98)
        ) !important;

    box-shadow:
        0 10px 26px rgba(0,0,0,.25),
        0 0 12px rgba(32,199,255,.52),
        0 0 28px rgba(0,151,255,.24) !important;
}


/* ----------------------------------------------------------
   AUTH / BOTTOM BUTTONS = GREEN
   ---------------------------------------------------------- */

.cfg-card.auth .cfg-btn:hover{
    border-color:
        rgba(76,235,157,.88) !important;

    background:
        linear-gradient(
            135deg,
            rgba(14,71,50,.90),
            rgba(8,40,31,.90)
        ) !important;

    box-shadow:
        0 10px 24px rgba(0,0,0,.24),
        0 0 10px rgba(65,226,145,.42),
        0 0 25px rgba(39,199,123,.20) !important;
}

.cfg-card.auth .cfg-btn-primary:hover{
    background:
        linear-gradient(
            135deg,
            rgba(31,179,116,.98),
            rgba(12,117,76,.98)
        ) !important;

    box-shadow:
        0 10px 26px rgba(0,0,0,.25),
        0 0 12px rgba(74,238,157,.50),
        0 0 28px rgba(42,211,133,.22) !important;
}


/* ----------------------------------------------------------
   CLICK FEEDBACK
   ---------------------------------------------------------- */

.cfg-card .cfg-btn:active{
    transform:translateY(0) scale(.985);
}




/* ==========================================================
   SETTINGS DARK BUTTON BASE + SECTION HOVER
   ========================================================== */

/* Same dark resting appearance for all buttons */

.cfg-card .cfg-btn,
.cfg-btn-primary,
.cfg-btn-primary.accent-orange,
.cfg-btn-primary.accent-blue,
.cfg-btn-primary.accent-green{

    color:#f6fbff !important;

    background:
        rgba(6,19,31,.72) !important;

    border:
        1px solid
        rgba(64,151,213,.28) !important;

    box-shadow:
        0 8px 20px
        rgba(0,0,0,.18) !important;

    transition:
        transform .18s ease,
        border-color .18s ease,
        background .18s ease,
        box-shadow .18s ease !important;
}


/* ==========================================================
   RADARR = RED / ORANGE ON HOVER
   ========================================================== */

.cfg-card.radarr .cfg-btn:hover{

    transform:translateY(-2px);

    color:#fff !important;

    border-color:
        rgba(255,112,72,.92) !important;

    background:
        linear-gradient(
            135deg,
            rgba(88,33,28,.94),
            rgba(43,19,22,.94)
        ) !important;

    box-shadow:
        0 10px 24px rgba(0,0,0,.24),
        0 0 10px rgba(255,93,53,.48),
        0 0 26px rgba(255,73,37,.20)
        !important;
}


/* ==========================================================
   SONARR = BLUE / CYAN ON HOVER
   ========================================================== */

.cfg-card.sonarr .cfg-btn:hover{

    transform:translateY(-2px);

    color:#fff !important;

    border-color:
        rgba(45,198,255,.94) !important;

    background:
        linear-gradient(
            135deg,
            rgba(8,59,96,.94),
            rgba(6,31,58,.94)
        ) !important;

    box-shadow:
        0 10px 24px rgba(0,0,0,.24),
        0 0 10px rgba(0,185,255,.50),
        0 0 26px rgba(0,145,255,.22)
        !important;
}


/* ==========================================================
   AUTHENTICATION = GREEN ON HOVER
   ========================================================== */

.cfg-card.auth .cfg-btn:hover{

    transform:translateY(-2px);

    color:#fff !important;

    border-color:
        rgba(74,235,157,.92) !important;

    background:
        linear-gradient(
            135deg,
            rgba(16,78,54,.94),
            rgba(8,43,32,.94)
        ) !important;

    box-shadow:
        0 10px 24px rgba(0,0,0,.24),
        0 0 10px rgba(66,227,145,.46),
        0 0 26px rgba(39,199,123,.20)
        !important;
}


/* Click feedback */

.cfg-card .cfg-btn:active{

    transform:
        translateY(0)
        scale(.985) !important;

}




/* ==========================================================
   SETTINGS PRIMARY BUTTONS - DARK BASE / COLORED HOVER
   ========================================================== */


/* ----------------------------------------------------------
   ALL SAVE BUTTONS:
   same dark resting style as Test connection
   ---------------------------------------------------------- */

.cfg-btn-primary,
.cfg-btn-primary.accent-orange,
.cfg-btn-primary.accent-blue,
.cfg-btn-primary.accent-green{

    color:#f6fbff !important;

    background:
        rgba(
            6,
            19,
            31,
            .68
        ) !important;

    border:
        1px solid
        rgba(
            64,
            151,
            213,
            .28
        ) !important;

    box-shadow:
        0 8px 20px
        rgba(0,0,0,.18) !important;

}


/* ----------------------------------------------------------
   RADARR SAVE / SAVE BUDGET
   ---------------------------------------------------------- */

.cfg-card.radarr
.cfg-btn-primary:hover{

    color:#fff !important;

    border-color:
        rgba(
            255,
            112,
            72,
            .92
        ) !important;

    background:
        linear-gradient(
            135deg,
            rgba(92,35,29,.94),
            rgba(45,20,22,.94)
        ) !important;

    box-shadow:
        0 10px 24px
        rgba(0,0,0,.24),

        0 0 10px
        rgba(255,93,53,.48),

        0 0 26px
        rgba(255,73,37,.20)
        !important;

}


/* Test connection gets same Radarr hover feel */

.cfg-card.radarr
.cfg-btn-ghost:hover{

    border-color:
        rgba(
            255,
            112,
            72,
            .82
        ) !important;

    box-shadow:
        0 10px 24px
        rgba(0,0,0,.22),

        0 0 9px
        rgba(255,93,53,.38),

        0 0 22px
        rgba(255,73,37,.15)
        !important;

}


/* ----------------------------------------------------------
   SONARR SAVE / SAVE BUDGET
   ---------------------------------------------------------- */

.cfg-card.sonarr
.cfg-btn-primary:hover{

    color:#fff !important;

    border-color:
        rgba(
            45,
            198,
            255,
            .94
        ) !important;

    background:
        linear-gradient(
            135deg,
            rgba(8,59,96,.94),
            rgba(6,31,58,.94)
        ) !important;

    box-shadow:
        0 10px 24px
        rgba(0,0,0,.24),

        0 0 10px
        rgba(0,185,255,.50),

        0 0 26px
        rgba(0,145,255,.22)
        !important;

}


/* Test connection gets same Sonarr hover feel */

.cfg-card.sonarr
.cfg-btn-ghost:hover{

    border-color:
        rgba(
            45,
            198,
            255,
            .86
        ) !important;

    box-shadow:
        0 10px 24px
        rgba(0,0,0,.22),

        0 0 9px
        rgba(0,185,255,.40),

        0 0 22px
        rgba(0,145,255,.16)
        !important;

}


/* ----------------------------------------------------------
   AUTHENTICATION SAVE
   ---------------------------------------------------------- */

.cfg-card.auth
.cfg-btn-primary:hover{

    color:#fff !important;

    border-color:
        rgba(
            74,
            235,
            157,
            .92
        ) !important;

    background:
        linear-gradient(
            135deg,
            rgba(16,78,54,.94),
            rgba(8,43,32,.94)
        ) !important;

    box-shadow:
        0 10px 24px
        rgba(0,0,0,.24),

        0 0 10px
        rgba(66,227,145,.46),

        0 0 26px
        rgba(39,199,123,.20)
        !important;

}


/* Log out also uses the green section hover */

.cfg-card.auth
.cfg-btn-ghost:hover{

    border-color:
        rgba(
            74,
            235,
            157,
            .80
        ) !important;

    box-shadow:
        0 10px 24px
        rgba(0,0,0,.22),

        0 0 9px
        rgba(66,227,145,.36),

        0 0 22px
        rgba(39,199,123,.14)
        !important;

}


/* ----------------------------------------------------------
   CLICK FEEDBACK
   ---------------------------------------------------------- */

.cfg-card .cfg-btn:active{

    transform:
        translateY(0)
        scale(.985) !important;

}


</style>
  <link rel="icon" type="image/png" sizes="32x32" href="/favicon.ico?v=2">
</head>
<body class="settingsbody">

<div class="settings-bg"></div>

<main class="settings-shell">

  <div class="settings-top">
    <div class="settings-heading">
      <div class="settings-heading-icon">
        <img src="/settings-icon.png" alt="Settings">
      </div>
      <div class="settings-heading-copy">
        <h1>Connection settings</h1>
        <p>Radarr, Sonarr and UI access.</p>
      </div>
    </div>

    <div class="settings-nav">

      <a
        class="settings-home-link"
        href="/"
      >
        ← Smart Optimizer
      </a>

      <a
        class="settings-nav-link radarr"
        href="/radarr"
      >
        Radarr
      </a>

      <a
        class="settings-nav-link sonarr"
        href="/sonarr"
      >
        Sonarr
      </a>

      <a
        class="settings-nav-link updates"
        href="/updates"
      >
        Updates
      </a>

    </div>
  </div>

  __NOTICE__

  <div class="cfg-grid">
    __RADARR_CARD__
    __SONARR_CARD__
    __AUTH_CARD__
  </div>

  <div class="cfg-footer-note">
    Passwords are stored only as a salted PBKDF2-SHA256 hash. Saved connection settings override Docker environment values. Optimizer queues, cursors, history and downsize rules are not changed here.
  </div>

</main>


<!-- SMART_COMMON_UPDATE_STATUS_FAVICON -->


<!-- SMART_COMMON_ADMIN_UPDATE_MODE -->

</body>
</html>""")

    replacements = {
        "__BASE_CSS__": CSS,
        "__NOTICE__": notice,
        "__RADARR_CARD__": connection_card(
            "radarr",
            rcfg,
            7878,
            "/radarr-icon.png",
            "Radarr connection",
            "Configure the Radarr API used by the dashboard and optimizer.",
            "radarr"
        ),
        "__SONARR_CARD__": connection_card(
            "sonarr",
            scfg,
            8989,
            "/sonarr-icon.png",
            "Sonarr connection",
            "Configure the Sonarr API used by the dashboard and optimizer.",
            "sonarr"
        ),
        "__AUTH_CARD__": auth_card,
    }

    for key, value in replacements.items():
        rendered = rendered.replace(key, value)

    return rendered

def history_page(app):
    try:
        if app == "radarr":
            upgrades = completed_upgrades(history_records())
            title, noun = "Radarr history", "movies"
        else:
            upgrades = sonarr_completed_upgrades(sonarr_history_records())
            title, noun = "Sonarr history", "episodes"
        rows = ""
        for x in upgrades:
            delta = gib(x["saved"])
            cls = "good" if delta >= 0 else "bad"
            rows += "<tr><td>%s</td><td>%.2f GiB</td><td>%.2f GiB</td><td class='%s'>%+.2f GiB</td><td>%s</td></tr>" % (
                html.escape(x["title"]), gib(x["old"]), gib(x["new"]), cls, delta, html.escape((x.get("date") or "")[:19].replace("T", " ")))
        if not rows:
            rows = "<tr><td colspan='5' class='muted'>No completed upgrade pairs found in the loaded history window.</td></tr>"
        err = ""
    except Exception as exc:
        rows = ""
        err = "<div class='notice bad'>%s API error: %s</div>" % (app.capitalize(), html.escape(str(exc)))
        title, noun = app.capitalize() + " history", "items"
    return _smart_expand_common("""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Smart Optimizer UI · %s</title><style>%s</style>  <link rel="icon" type="image/png" sizes="32x32" href="/favicon.ico?v=2">
</head><body><div class="shell">
<div class="topbar compact"><div class="brand"><div class="brandcopy"><h1>%s</h1><div>Complete observed upgrade history available in the loaded API history window.</div></div></div><div class="nav"><a class="badge" href="/%s">← Back to dashboard</a></div></div>
%s
<div class="panel"><div class="panelhead"><div><h3>All recent observed changes</h3><p>Size changes for %s returned by the configured history window.</p></div><span class="badge">HISTORY</span></div>
<table><thead><tr><th>Release</th><th>Before</th><th>After</th><th>Change</th><th>Date</th></tr></thead><tbody>%s</tbody></table></div>
</div>
<!-- SMART_COMMON_UPDATE_STATUS_FAVICON -->


<!-- SMART_COMMON_ADMIN_UPDATE_MODE -->

</body></html>""" % (html.escape(title), CSS, html.escape(title), app, err, noun, rows))


def sonarr_page():
    state = load_sonarr_state()
    today = time.strftime("%Y-%m-%d")
    used = int((state.get("daily") or {}).get(today, {}).get("searches", 0))
    rule_min, rule_max, extra_today = app_controls("sonarr")
    error = ""
    try:
        records = sonarr_history_records()
        upgrades = sonarr_completed_upgrades(records)
        queue = sonarr_queue_records()
    except Exception as exc:
        upgrades, queue = [], []
        error = str(exc)
    saved = sum(x["saved"] for x in upgrades)
    positive = sum(1 for x in upgrades if x["saved"] > 0)
    total_before = sum(x["old"] for x in upgrades)
    reduction_pct = (saved / total_before * 100.0) if total_before else 0.0
    last_date = upgrades[0]["date"][:10] if upgrades else "—"
    rows = ""
    for i, x in enumerate(upgrades[:15]):
        delta = gib(x["saved"])
        cls = "good" if delta >= 0 else "bad"
        extra = " changeextra" if i >= 5 else ""
        rows += "<tr class='filterrow%s' data-search='%s'><td>%s</td><td>%.2f GiB</td><td>%.2f GiB</td><td class='%s'>%+.2f GiB</td></tr>" % (
            extra, html.escape(x["title"].lower(), quote=True), html.escape(x["title"]), gib(x["old"]), gib(x["new"]), cls, delta)
    if not rows:
        rows = "<tr><td colspan='4' class='muted'>No completed episode upgrade pairs found in the loaded history window.</td></tr>"
    elif len(upgrades) > 5:
        rows += "<tr id='changesExpandRow'><td colspan='4'><button type='button' class='expandbar' id='changesExpand' onclick='toggleChanges()'>Show %d more changes ↓</button></td></tr>" % (min(len(upgrades), 15) - 5)
    qrows = ""
    for i, x in enumerate(queue[:15]):
        size = float(x.get("size") or 0); left = float(x.get("sizeleft") or 0)
        progress = max(0.0, min(100.0, ((size-left)/size*100.0) if size else 0.0))
        title = x.get("title") or ("Episode ID %s" % x.get("episodeId"))
        status = x.get("status") or x.get("trackedDownloadStatus") or "unknown"
        extra = " extra" if i >= 4 else ""
        qrows += "<div class='queueitem filterrow%s' data-search='%s'><div class='qtop'><div class='qtitle'>%s</div><div>%s</div></div><div class='qmeta'>%.1f%%</div><div class='progress'><span style='width:%.1f%%'></span></div></div>" % (
            extra, html.escape(str(title).lower(), quote=True), html.escape(str(title)), html.escape(str(status)), progress, progress)
    if not qrows:
        qrows = "<div class='empty'>Nothing is currently in Sonarr's download queue.</div>"
    elif len(queue) > 4:
        qrows += "<button type='button' class='expandbar' id='queueExpand' onclick='toggleQueue()'>Show %d more downloads ↓</button>" % (min(len(queue), 15) - 4)
    runstat = manual_status("sonarr")
    runlabel = "Idle" if not runstat["requested"] else ("%s · %d / %d upgrades" % (runstat["state"].capitalize(), runstat["searched"], runstat["requested"]))
    if runstat.get("running") and runstat.get("current"):
        runlabel += "<span class='runitem'>Now checking: %s</span>" % html.escape(runstat["current"])
    elif not runstat.get("running") and runstat.get("last"):
        runlabel += "<span class='runitem'>Last searched: %s</span>" % html.escape(runstat["last"])
    son_actions = """<div class="controlbar primary"><form class="controlbox manualform" method="post" action="/manual-search"><input type="hidden" name="app" value="sonarr"><label>Manual search</label><input name="count" type="number" min="1" max="%d" value="0"><button %s>Search</button><button class="stopbtn" formaction="/stop" %s>STOP</button></form><span id="runstate-sonarr" class="manualstate">%s</span></div>
<!-- SMART LIVE RULES HOVER CSS -->
<style>
.rules-main-button{
    display:inline-block !important;
    position:relative !important;
    transition:
        background .16s ease,
        border-color .16s ease,
        box-shadow .16s ease,
        color .16s ease !important;
}

/* Radarr RULES:
   same soft glow amount as the normal controls,
   only the colour is Radarr red. */
.radarr-rules-button:hover,
.radarr-rules-button:focus-visible{
    color:#fff !important;
    background:rgba(255,70,55,.14) !important;
    border-color:#ff604f !important;

    box-shadow:
        0 0 10px rgba(255,70,55,.42) !important;

    filter:none !important;
    transform:none !important;
}

/* Sonarr RULES:
   same soft glow amount as the normal controls,
   only the colour is Sonarr blue. */
.sonarr-rules-button:hover,
.sonarr-rules-button:focus-visible{
    color:#fff !important;
    background:rgba(35,190,255,.14) !important;
    border-color:#32c7ff !important;

    box-shadow:
        0 0 10px rgba(35,190,255,.42) !important;

    filter:none !important;
    transform:none !important;
}
</style>
<form class="controlbar" method="post" action="/settings"><input type="hidden" name="app" value="sonarr"><div class="controlbox"><label>Downsize</label><input name="min" type="number" min="0" max="100" step="0.1" value="%.1f"><span>–</span><input name="max" type="number" min="0" max="100" step="0.1" value="%.1f"><span>%%</span><button type="submit">Apply</button></div><span class="badge">%d/%d searches · +%d today</span><a class="badge rules-main-button sonarr-rules-button" style="text-decoration:none;font-weight:900;font-size:.92rem;padding:8px 17px;letter-spacing:.07em;transition:.18s ease" href="/sonarr/rules">RULES</a></form>""" % (MAX_MANUAL, "disabled" if runstat["running"] else "", "" if runstat["running"] else "disabled", runlabel, rule_min, rule_max, used, daily_search_budget("sonarr") + extra_today, extra_today)
    err = ("<div class='notice bad'>Sonarr API error: %s</div>" % html.escape(error)) if error else ""
    connection_status = "Online" if api_online("sonarr") else "Offline"
    return """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Smart Optimizer UI · Sonarr</title><style>%s

/* SMART ARR DASHBOARD MODERN START */


/* ==========================================================
   PAGE THEME
   ========================================================== */

body.dashboard-page{

    --dash-accent:#28c5ff;
    --dash-accent-rgb:40,197,255;

    margin:0;

    min-height:100vh;

    color:#eef6ff;

    background:

        linear-gradient(
            180deg,
            rgba(1,7,14,.22),
            rgba(1,7,14,.36)
        ),

        url('/sonarr-background.png?v=20260923-233901')
        center center / cover
        no-repeat fixed !important;

    position:relative;
}


body.radarr-dashboard{

    --dash-accent:#ff7048;
    --dash-accent-rgb:255,112,72;

}


body.sonarr-dashboard{

    --dash-accent:#28c5ff;
    --dash-accent-rgb:40,197,255;

}


/* dark reading layer */

body.dashboard-page::before{

    content:"";

    position:fixed;

    inset:0;

    z-index:0;

    pointer-events:none;

    background:

        radial-gradient(
            circle at center,
            rgba(0,12,26,.10),
            rgba(0,5,14,.34)
        );

}


/* ==========================================================
   MAIN WIDTH
   ========================================================== */

body.dashboard-page .shell{

    position:relative;

    z-index:1;

    width:
        calc(100vw - 56px) !important;

    max-width:
        1320px !important;

    margin:
        0 auto !important;

    padding-top:
        26px !important;

    padding-bottom:
        50px !important;

}


/* ==========================================================
   TOP HEADER
   ========================================================== */

body.dashboard-page .topbar{

    position:relative;

    padding:
        18px
        20px !important;

    margin-bottom:
        18px !important;

    border-radius:
        20px !important;

    background:

        linear-gradient(
            145deg,
            rgba(10,25,43,.86),
            rgba(5,16,29,.80)
        );

    border:

        1px solid
        rgba(
            var(--dash-accent-rgb),
            .24
        );

    backdrop-filter:
        blur(14px);

    -webkit-backdrop-filter:
        blur(14px);

    box-shadow:

        0 18px 44px
        rgba(0,0,0,.24),

        inset
        0 0 0 1px
        rgba(255,255,255,.018);

}


/* accent line */

body.dashboard-page .topbar::after{

    content:"";

    position:absolute;

    left:22px;

    right:22px;

    bottom:-1px;

    height:2px;

    border-radius:999px;

    background:

        linear-gradient(
            90deg,
            transparent,
            rgba(
                var(--dash-accent-rgb),
                .72
            ),
            transparent
        );

}


/* ==========================================================
   RADARR / SONARR HEADER ICON
   ========================================================== */

body.dashboard-page .topbar .brand{

    display:flex;

    align-items:center;

    gap:14px;

}


body.dashboard-page .topbar .brand::before{

    content:"";

    width:48px;

    height:48px;

    flex:
        0 0 48px;

    display:block;

    background:
        var(--dashboard-icon)
        center center / contain
        no-repeat;

    filter:

        drop-shadow(
            0 8px 14px
            rgba(0,0,0,.28)
        );

}


body.radarr-dashboard{

    --dashboard-icon:
        url('/radarr-icon.png');

}


body.sonarr-dashboard{

    --dashboard-icon:
        url('/sonarr-icon.png');

}


body.dashboard-page .brandcopy h1{

    margin-bottom:
        3px !important;

    font-size:
        1.55rem !important;

    letter-spacing:
        -.025em;

}


body.dashboard-page .brandcopy > div{

    color:
        rgba(210,224,242,.67) !important;

}


/* ==========================================================
   TOP NAV
   ========================================================== */

body.dashboard-page .nav{

    display:flex;

    align-items:center;

    gap:8px !important;

    flex-wrap:wrap;

}


body.dashboard-page .nav .badge{

    min-height:
        36px;

    display:inline-flex;

    align-items:center;

    justify-content:center;

    padding:
        0 13px !important;

    border-radius:
        999px !important;

    text-decoration:none;

    background:
        rgba(6,21,36,.76) !important;

    border:

        1px solid
        rgba(84,136,194,.22) !important;

    transition:

        transform .18s ease,
        border-color .18s ease,
        background .18s ease,
        box-shadow .18s ease;

}


body.dashboard-page .nav .badge:hover{

    transform:
        translateY(-1px);

    border-color:

        rgba(
            var(--dash-accent-rgb),
            .72
        ) !important;

    box-shadow:

        0 0 10px
        rgba(
            var(--dash-accent-rgb),
            .26
        ),

        0 0 22px
        rgba(
            var(--dash-accent-rgb),
            .10
        );

}


/* Radarr nav stays orange */

body.dashboard-page
.nav a[href="/radarr"]:hover,

body.radarr-dashboard
.nav a[href="/radarr"]{

    border-color:
        rgba(255,112,72,.76) !important;

    box-shadow:
        0 0 16px
        rgba(255,94,55,.17);

}


/* Sonarr nav stays blue */

body.dashboard-page
.nav a[href="/sonarr"]:hover,

body.sonarr-dashboard
.nav a[href="/sonarr"]{

    border-color:
        rgba(40,197,255,.80) !important;

    box-shadow:
        0 0 16px
        rgba(0,174,255,.17);

}


/* ==========================================================
   INPUTS / SELECTS
   ========================================================== */

body.dashboard-page input,

body.dashboard-page select{

    color:#f4f9ff !important;

    background:

        rgba(
            3,
            13,
            24,
            .76
        ) !important;

    border:

        1px solid
        rgba(
            81,
            135,
            195,
            .28
        ) !important;

    border-radius:
        10px !important;

    transition:

        border-color .18s ease,
        box-shadow .18s ease,
        background .18s ease;

}


body.dashboard-page input:focus,

body.dashboard-page select:focus{

    outline:none !important;

    border-color:

        rgba(
            var(--dash-accent-rgb),
            .80
        ) !important;

    box-shadow:

        0 0 0 3px
        rgba(
            var(--dash-accent-rgb),
            .07
        ),

        0 0 18px
        rgba(
            var(--dash-accent-rgb),
            .08
        ) !important;

}


/* ==========================================================
   BUTTONS
   ========================================================== */

body.dashboard-page button{

    border-radius:
        10px !important;

    transition:

        transform .18s ease,
        border-color .18s ease,
        background .18s ease,
        box-shadow .18s ease;

}


body.dashboard-page
button:not(:disabled):hover{

    transform:
        translateY(-1px);

    border-color:

        rgba(
            var(--dash-accent-rgb),
            .80
        ) !important;

    box-shadow:

        0 0 9px
        rgba(
            var(--dash-accent-rgb),
            .28
        ),

        0 0 22px
        rgba(
            var(--dash-accent-rgb),
            .10
        ) !important;

}


body.dashboard-page
button:not(:disabled):active{

    transform:
        translateY(0)
        scale(.985);

}


/* ==========================================================
   SEARCH MODE TABS
   ========================================================== */

body.dashboard-page
[data-search-mode]{

    border-radius:
        10px !important;

}


body.dashboard-page
[data-search-mode]:hover,

body.dashboard-page
[data-search-mode].active{

    border-color:

        rgba(
            var(--dash-accent-rgb),
            .78
        ) !important;

    box-shadow:

        0 0 10px
        rgba(
            var(--dash-accent-rgb),
            .20
        ) !important;

}


/* ==========================================================
   SEARCH BAR
   ========================================================== */

body.dashboard-page
input[type="search"]{

    min-height:
        44px !important;

    border-color:

        rgba(
            var(--dash-accent-rgb),
            .48
        ) !important;

}


/* ==========================================================
   STATS
   ========================================================== */

body.dashboard-page .stat{

    border-radius:
        16px !important;

    background:

        linear-gradient(
            145deg,
            rgba(12,28,47,.88),
            rgba(6,17,30,.84)
        ) !important;

    border:

        1px solid
        rgba(
            83,
            136,
            195,
            .16
        ) !important;

    backdrop-filter:
        blur(12px);

    -webkit-backdrop-filter:
        blur(12px);

    box-shadow:

        0 13px 30px
        rgba(0,0,0,.18),

        inset
        0 0 0 1px
        rgba(255,255,255,.014);

    transition:

        transform .18s ease,
        border-color .18s ease,
        box-shadow .18s ease;

}


body.dashboard-page .stat:hover{

    transform:
        translateY(-2px);

    border-color:

        rgba(
            var(--dash-accent-rgb),
            .32
        ) !important;

    box-shadow:

        0 15px 34px
        rgba(0,0,0,.22),

        0 0 18px
        rgba(
            var(--dash-accent-rgb),
            .07
        );

}


body.dashboard-page .stat .value{

    font-size:
        1.8rem !important;

    line-height:
        1.08;

}


/* ==========================================================
   PANELS
   ========================================================== */

body.dashboard-page .panel{

    border-radius:
        18px !important;

    background:

        linear-gradient(
            145deg,
            rgba(11,27,46,.90),
            rgba(5,16,29,.87)
        ) !important;

    border:

        1px solid
        rgba(
            78,
            133,
            194,
            .15
        ) !important;

    backdrop-filter:
        blur(13px);

    -webkit-backdrop-filter:
        blur(13px);

    box-shadow:

        0 17px 40px
        rgba(0,0,0,.22),

        inset
        0 0 0 1px
        rgba(255,255,255,.012);

    transition:

        border-color .18s ease,
        box-shadow .18s ease;

}


body.dashboard-page .panel:hover{

    border-color:

        rgba(
            var(--dash-accent-rgb),
            .27
        ) !important;

}


/* panel heading accent */

body.dashboard-page
.panelhead h3{

    letter-spacing:
        -.015em;

}


/* ==========================================================
   TABLE / RECENT CHANGES
   ========================================================== */

body.dashboard-page table{

    border-collapse:
        separate;

    border-spacing:
        0;

}


body.dashboard-page tbody tr{

    transition:
        background .15s ease;

}


body.dashboard-page tbody tr:hover{

    background:

        rgba(
            var(--dash-accent-rgb),
            .035
        );

}


/* ==========================================================
   METRIC LINES
   ========================================================== */

body.dashboard-page .metricline{

    border-color:
        rgba(255,255,255,.055) !important;

}


/* ==========================================================
   BADGES
   ========================================================== */

body.dashboard-page .badge{

    border-radius:
        999px !important;

}


/* ==========================================================
   RIGHT-SIDE RADAR / QUEUE
   ========================================================== */

body.dashboard-page
.panel progress{

    accent-color:
        var(--dash-accent);

}


/* ==========================================================
   RESPONSIVE
   ========================================================== */

@media(max-width:900px){

    body.dashboard-page .shell{

        width:
            calc(100vw - 24px) !important;

    }


    body.dashboard-page .topbar{

        padding:
            15px !important;

    }


    body.dashboard-page
    .topbar .brand::before{

        width:40px;

        height:40px;

        flex-basis:40px;

    }

}


/* SMART ARR DASHBOARD MODERN END */



/* ==========================================================
   MODERN HISTORY BUTTON
   ========================================================== */

.history-modern{

    display:inline-flex !important;

    align-items:center;

    justify-content:center;

    min-height:30px;

    padding:
        0 12px !important;

    border-radius:
        999px !important;

    color:
        #dce8f5 !important;

    background:
        rgba(5,18,31,.76) !important;

    border:
        1px solid
        rgba(112,154,198,.30) !important;

    text-decoration:
        none !important;

    font-size:
        .76rem !important;

    font-weight:
        800 !important;

    letter-spacing:
        .055em;

    text-transform:
        uppercase;

    box-shadow:

        0 7px 18px
        rgba(0,0,0,.17),

        inset
        0 0 0 1px
        rgba(255,255,255,.015);

    transition:

        transform .18s ease,
        color .18s ease,
        border-color .18s ease,
        background .18s ease,
        box-shadow .18s ease;

}


/* RADARR */

body.radarr-dashboard
.history-modern:hover{

    transform:
        translateY(-1px);

    color:
        #ffffff !important;

    border-color:
        rgba(255,112,72,.92) !important;

    background:

        linear-gradient(
            135deg,
            rgba(74,29,25,.90),
            rgba(34,17,21,.90)
        ) !important;

    box-shadow:

        0 9px 22px
        rgba(0,0,0,.23),

        0 0 9px
        rgba(255,94,54,.42),

        0 0 22px
        rgba(255,74,36,.18) !important;

}


/* SONARR */

body.sonarr-dashboard
.history-modern:hover{

    transform:
        translateY(-1px);

    color:
        #ffffff !important;

    border-color:
        rgba(40,197,255,.94) !important;

    background:

        linear-gradient(
            135deg,
            rgba(7,55,91,.90),
            rgba(5,28,52,.90)
        ) !important;

    box-shadow:

        0 9px 22px
        rgba(0,0,0,.23),

        0 0 9px
        rgba(0,187,255,.44),

        0 0 22px
        rgba(0,145,255,.19) !important;

}


.history-modern:active{

    transform:
        translateY(0)
        scale(.975) !important;

}




/* ==========================================================
   DASHBOARD NAV + SMALL BADGE TYPOGRAPHY
   ========================================================== */


/* ----------------------------------------------------------
   HOME + SETTINGS
   Match Radarr / Sonarr typography
   ---------------------------------------------------------- */

body.dashboard-page
.nav .dashboard-home-link,

body.dashboard-page
.nav .dashboard-settings-link{

    font-size:
        .88rem !important;

    font-weight:
        750 !important;

    letter-spacing:
        0 !important;

    color:
        #eaf3fd !important;

}


/* ----------------------------------------------------------
   ACTIVE / SUMMARY PILLS
   Same typography family as HISTORY
   ---------------------------------------------------------- */

body.dashboard-page
.panelhead .badge{

    font-size:
        .76rem !important;

    font-weight:
        800 !important;

    letter-spacing:
        .055em !important;

    text-transform:
        uppercase !important;

    color:
        #dce8f5 !important;

}


/* slightly stronger pill appearance */

body.dashboard-page
.panelhead .badge:not(.history-modern){

    padding:
        0 11px !important;

    min-height:
        28px;

    display:
        inline-flex;

    align-items:
        center;

    justify-content:
        center;

    border-radius:
        999px !important;

    background:
        rgba(5,18,31,.74) !important;

    border:
        1px solid
        rgba(112,154,198,.28) !important;

    box-shadow:
        0 6px 16px
        rgba(0,0,0,.14);

}


</style></head><body class="dashboard-page sonarr-dashboard"><div class="shell">
<div class="topbar compact"><div class="brand"><div class="brandcopy"><h1>Sonarr Optimizer</h1><div>Find smaller releases for your episodes while keeping quality.</div></div></div><div class="nav"><div class="appswitch"><a href="/radarr">Radarr</a><a class="active" href="/sonarr">Sonarr</a></div><span class="status%s"><span class="dot"></span>%s</span></div></div>
%s%s
%s
<div class="toolbar">
<div class="searchbox"><span class="searchicon">⌕</span><input id="librarySearch" autocomplete="off" placeholder="Search releases and current downloads…"></div>
<div id="exclusionSearchResults" class="exclusionSearchResults"></div>
<div id="recentExclusions" class="recentExclusions">
  <div class="recentExclusionsHead">
    <strong>Recently excluded</strong>
    <a id="showAllExclusions" href="#">Show all exclusions →</a>
  </div>
  <div id="recentExclusionRows" class="recentExclusionRows"></div>
</div>
</div>
<div class="grid five">
<div class="stat"><div class="stathead"><span>Storage saved</span></div><div class="value %s">%+.2f GiB</div><div class="sub">Observed across loaded upgrade history</div></div>
<div class="stat"><div class="stathead"><span>Reductions</span></div><div class="value">%d</div><div class="sub">Observed upgrades that ended smaller</div></div>
<div class="stat"><div class="stathead"><span>Active downloads</span></div><div class="value">%d</div><div class="sub">Live Sonarr queue</div></div>
<div class="stat"><div class="stathead"><span>Searches today</span></div><div class="value">%d</div><div class="sub">Optimizer state counter</div></div>
<div class="stat"><div class="stathead"><span>Temporary extra today</span></div><div class="value">+%d</div><div class="sub">Resets tomorrow</div></div>
</div>
<div class="layout"><div>
<div class="panel"><div class="panelhead"><div><h3>Recent changes</h3><p>Latest optimized episodes and their size changes.</p></div><a class="badge history-modern" href="/sonarr/history">HISTORY</a></div><table><thead><tr><th>Release</th><th>Before</th><th>After</th><th>Change</th></tr></thead><tbody>%s</tbody></table></div>
<div class="panel"><div class="panelhead"><div><h3>Settings &amp; status</h3><p>Current configuration and system status.</p></div></div><div class="sidecontent">
<div class="metricline"><span>Downsize range</span><b>%.1f – %.1f%%</b></div>
<div class="metricline"><span>UHD (1080 → 2160) exception</span><b>Enabled (unchanged)</b></div>
<div class="metricline"><span>Daily search budget</span><b>%d</b></div>
<div class="metricline"><span>Temporary extra searches</span><b>%d</b></div>
<div class="metricline"><span>Status</span><b class="good">Ready</b></div>
</div></div>
</div>
<div>
<div class="panel"><div class="panelhead"><div><h3>Download radar</h3><p>Live Sonarr queue.</p></div><span class="badge">%d ACTIVE</span></div>%s</div>
<div class="panel"><div class="panelhead"><div><h3>Optimizer intelligence</h3><p>Useful context without pretending Sonarr history equals optimizer success.</p></div><span class="badge">SUMMARY</span></div><div class="sidecontent">
<div class="metricline"><span>Observed net reduction</span><b class="%s">%.1f%%</b></div>
<div class="metricline"><span>Total smaller replacements</span><b>%d</b></div>
<div class="metricline"><span>Active downloads</span><b>%d</b></div>
<div class="metricline"><span>Last observed upgrade</span><b>%s</b></div>
</div></div>
</div></div>
<div class="footer"><a href="/">Smart Optimizer</a> · Sonarr dashboard</div></div>
<script>
let queueOpen=false;function toggleQueue(){const b=document.getElementById('queueExpand');if(queueOpen){location.href='/sonarr/history';return;}queueOpen=true;document.querySelectorAll('.queueitem.extra').forEach(el=>el.style.display='block');if(b)b.textContent='History →';}
let changesOpen=false;function toggleChanges(){const b=document.getElementById('changesExpand');if(changesOpen){location.href='/sonarr/history';return;}changesOpen=true;document.querySelectorAll('.changeextra').forEach(el=>el.style.display='table-row');if(b)b.textContent='History →';}
const box=document.getElementById('librarySearch');
const modeButtons=Array.from(
  document.querySelectorAll('[data-search-mode]')
);
const exclusionModeButton=
  document.getElementById('exclusionModeButton');
const exclusionResults=
  document.getElementById('exclusionSearchResults');

const SEARCH_APP=
  location.pathname.indexOf('/sonarr')===0
    ? 'sonarr'
    : 'radarr';

let searchMode='current';
let exclusionTimer=null;

const recentExclusions=document.getElementById('recentExclusions');
const recentExclusionRows=document.getElementById('recentExclusionRows');
const showAllExclusions=document.getElementById('showAllExclusions');

if(showAllExclusions){
  showAllExclusions.href='/'+SEARCH_APP+'/exclusions';
}

function updateExclusionButtonCount(count){
  const span=exclusionModeButton
    ? exclusionModeButton.querySelector('span')
    : null;

  if(span){
    span.textContent=String(count);
  }
}

async function exclusionAction(action,id){
  const body=new URLSearchParams();
  body.set('app',SEARCH_APP);
  body.set('id',String(id));

  const response=await fetch(
    action,
    {
      method:'POST',
      headers:{
        'Content-Type':'application/x-www-form-urlencoded;charset=UTF-8',
        'X-Requested-With':'fetch'
      },
      body:body.toString(),
      cache:'no-store'
    }
  );

  if(!response.ok){
    throw new Error('HTTP '+response.status);
  }

  return await response.json();
}

async function loadRecentExclusions(){
  try{
    const response=await fetch(
      '/exclusions-json?app='+encodeURIComponent(SEARCH_APP),
      {cache:'no-store'}
    );

    if(!response.ok){
      throw new Error('HTTP '+response.status);
    }

    const items=await response.json();

    updateExclusionButtonCount(
      Array.isArray(items) ? items.length : 0
    );

    const latest=
      Array.isArray(items)
        ? items.slice(0,5)
        : [];

    if(!latest.length){
      recentExclusionRows.innerHTML=
        '<div class="recentempty">No exclusions yet.</div>';
      return;
    }

    recentExclusionRows.innerHTML=latest.map(x=>{
      const year=
        x.year
          ? ' ('+escSearch(x.year)+')'
          : '';

      return (
        '<div class="recentexclusionrow">'+
          '<div class="exclusiontitle">'+
            '<b>'+escSearch(x.title)+'</b>'+year+
          '</div>'+
          '<form class="removeExclusionForm">'+
            '<input type="hidden" name="id" value="'+
              escSearch(x.id)+'">'+
            '<button type="submit">Remove exclusion</button>'+
          '</form>'+
        '</div>'
      );
    }).join('');

  }catch(err){
    console.error('Could not load exclusions:',err);
    recentExclusionRows.innerHTML=
      '<div class="recentempty">Could not load exclusions.</div>';
  }
}

async function refreshExclusionMode(){
  await loadRecentExclusions();

  if(box.value.trim()){
    searchExclusionLibrary();
  }
}

document.addEventListener('submit',async event=>{
  const addForm=event.target.closest(
    '#exclusionSearchResults form'
  );

  const removeForm=event.target.closest(
    '.removeExclusionForm'
  );

  if(!addForm && !removeForm){
    return;
  }

  event.preventDefault();

  const form=addForm || removeForm;
  const button=form.querySelector('button');
  const id=form.querySelector('[name="id"]').value;

  button.disabled=true;

  try{
    const result=await exclusionAction(
      addForm ? '/exclusion-add' : '/exclusion-remove',
      id
    );

    updateExclusionButtonCount(result.count);

    await refreshExclusionMode();

  }catch(err){
    console.error('Exclusion action failed:',err);
    button.disabled=false;
    button.textContent='Try again';
  }
});

function escSearch(v){
  return String(v==null?'':v)
    .replace(/&/g,'&amp;')
    .replace(/</g,'&lt;')
    .replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;')
    .replace(/'/g,'&#39;');
}

function restoreDashboardRows(){
  document.querySelectorAll('.filterrow').forEach(el=>{
    if(el.classList.contains('extra') && !queueOpen){
      el.style.display='none';
    }else if(
      el.classList.contains('changeextra') &&
      typeof changesOpen!=='undefined' &&
      !changesOpen
    ){
      el.style.display='none';
    }else{
      el.style.display='';
    }
  });
}

function filterCurrentDownloads(){
  const q=box.value.trim().toLowerCase();

  document.querySelectorAll('.filterrow').forEach(el=>{
    const match=
      !q ||
      ((el.dataset.search||'').includes(q));

    if(el.classList.contains('extra') && !queueOpen && !q){
      el.style.display='none';
    }else if(
      el.classList.contains('changeextra') &&
      typeof changesOpen!=='undefined' &&
      !changesOpen &&
      !q
    ){
      el.style.display='none';
    }else{
      el.style.display=match?'':'none';
    }
  });
}

function searchExclusionLibrary(){
  clearTimeout(exclusionTimer);

  const q=box.value.trim();

  if(!q){
    exclusionResults.innerHTML='';
    return;
  }

  exclusionResults.innerHTML=
    '<div class="exsearchstatus">Searching library…</div>';

  exclusionTimer=setTimeout(async()=>{
    try{
      const response=await fetch(
        '/library-search?app='+
        encodeURIComponent(SEARCH_APP)+
        '&q='+
        encodeURIComponent(q),
        {cache:'no-store'}
      );

      if(!response.ok){
        throw new Error('HTTP '+response.status);
      }

      const data=await response.json();

      if(!Array.isArray(data) || data.length===0){
        exclusionResults.innerHTML=
          '<div class="exsearchstatus">No matches found in your '+
          (SEARCH_APP==='radarr'?'Radarr':'Sonarr')+
          ' library.</div>';
        return;
      }

      exclusionResults.innerHTML=data.map(x=>{
        const year=
          x.year
            ? ' ('+escSearch(x.year)+')'
            : '';

        if(x.excluded){
          return (
            '<div class="exclusionsearchrow">'+
              '<div class="exclusiontitle">'+
                '<b>'+escSearch(x.title)+'</b>'+year+
              '</div>'+
              '<span class="badge">EXCLUDED</span>'+
            '</div>'
          );
        }

        return (
          '<div class="exclusionsearchrow">'+
            '<div class="exclusiontitle">'+
              '<b>'+escSearch(x.title)+'</b>'+year+
            '</div>'+
            '<form method="post" action="/exclusion-add">'+
              '<input type="hidden" name="app" value="'+
                SEARCH_APP+'">'+
              '<input type="hidden" name="id" value="'+
                escSearch(x.id)+'">'+
              '<button type="submit">Exclude</button>'+
            '</form>'+
          '</div>'
        );
      }).join('');

    }catch(err){
      console.error('Library exclusion search failed:',err);

      exclusionResults.innerHTML=
        '<div class="exsearchstatus bad">'+
        'Library search failed.'+
        '</div>';
    }
  },150);
}

function escapeSearchHTML(value){
  return String(value==null ? '' : value)
    .replace(/&/g,'&amp;')
    .replace(/</g,'&lt;')
    .replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;')
    .replace(/'/g,'&#39;');
}

function showOptimizerToast(message,kind){
  let toast=document.getElementById('manualOptimizerToast');

  if(!toast){
    toast=document.createElement('div');
    toast.id='manualOptimizerToast';
    toast.className='manualOptimizerToast';
    document.body.appendChild(toast);
  }

  toast.className=
    'manualOptimizerToast visible '+(kind||'');
  toast.textContent=message;

  clearTimeout(window.manualOptimizerToastTimer);

  window.manualOptimizerToastTimer=setTimeout(()=>{
    toast.classList.remove('visible');
  },5000);
}

function searchManualLibrary(){
  clearTimeout(exclusionTimer);

  const q=box.value.trim();

  if(!q){
    exclusionResults.innerHTML='';
    return;
  }

  exclusionResults.innerHTML=
    '<div class="exsearchstatus">Searching cached library…</div>';

  exclusionTimer=setTimeout(async()=>{
    try{
      const response=await fetch(
        '/library-search?app='+
        encodeURIComponent(SEARCH_APP)+
        '&q='+
        encodeURIComponent(q),
        {cache:'no-store'}
      );

      if(!response.ok){
        throw new Error('HTTP '+response.status);
      }

      const data=await response.json();

      if(!Array.isArray(data) || !data.length){
        exclusionResults.innerHTML=
          '<div class="exsearchstatus">No library matches.</div>';
        return;
      }

      exclusionResults.innerHTML=data.map(x=>{
        const title=escapeSearchHTML(x.title||'Untitled');
        const year=x.year
          ? ' <span class="muted">('+
            escapeSearchHTML(x.year)+
            ')</span>'
          : '';

        if(x.excluded){
          return (
            '<div class="exclusionsearchrow">'+
              '<div class="exclusiontitle">'+
                title+year+
              '</div>'+
              '<span class="badge">EXCLUDED</span>'+
            '</div>'
          );
        }

        return (
          '<div class="exclusionsearchrow">'+
            '<div class="exclusiontitle">'+
              title+year+
            '</div>'+
            '<button type="button" class="manualOptimizeButton" '+
              'data-id="'+Number(x.id)+'" '+
              'data-title="'+
                escapeSearchHTML(x.title||'Untitled')+
              '">Optimize</button>'+
          '</div>'
        );
      }).join('');
    }catch(err){
      console.error('Manual library search failed:',err);
      exclusionResults.innerHTML=
        '<div class="exsearchstatus">Could not search library.</div>';
    }
  },150);
}

async function waitForManualOptimizer(id,title,button,jobStarted){
  const clickedAt=Date.now();
  let searchingShown=false;

  for(let attempt=0;attempt<600;attempt++){
    await new Promise(resolve=>setTimeout(resolve,1000));

    if(!searchingShown && Date.now()-clickedAt>=3000){
      searchingShown=true;

      if(button){
        button.textContent='Searching…';
      }
    }

    try{
      const response=await fetch(
        '/status?app='+encodeURIComponent(SEARCH_APP),
        {cache:'no-store'}
      );

      if(!response.ok){
        continue;
      }

      const status=await response.json();
      const statusStarted=Number(status.started || 0);

      if(
        jobStarted &&
        statusStarted &&
        statusStarted < jobStarted
      ){
        continue;
      }

      if(status.running){
        continue;
      }

      if(!status.finished){
        continue;
      }

      const grabbed=Number(status.grabbed || 0);

      if(status.state==='failed'){
        if(button){
          button.textContent='Failed';
        }

        showOptimizerToast(
          'Optimizer failed for '+title+'.',
          'error'
        );

      }else if(status.state==='stopped'){
        if(button){
          button.textContent='Stopped';
        }

        showOptimizerToast(
          'Optimizer stopped for '+title+'.',
          'error'
        );

      }else if(grabbed>0){
        if(button){
          button.textContent='Downloading';
        }

        showOptimizerToast(
          'Downloading upgrade for '+title+'.',
          'success'
        );

      }else{
        if(button){
          button.textContent='No Upgrade';

          setTimeout(()=>{
            const currentButton=Array.from(
              document.querySelectorAll('.manualOptimizeButton')
            ).find(candidate=>
              Number(candidate.dataset.id)===Number(id)
            );

            if(currentButton){
              currentButton.textContent='Optimize';
              currentButton.disabled=false;
            }
          },5000);
        }

        showOptimizerToast(
          'No upgrade found for '+title+'.',
          'neutral'
        );
      }

      return;
    }catch(err){
      console.error(
        'Manual optimizer status failed:',
        err
      );
    }
  }

  if(button){
    button.textContent='Searching…';
  }

  showOptimizerToast(
    'Optimizer is still running for '+title+'.',
    'neutral'
  );
}

async function startManualOptimizer(id,title,button){
  if(button){
    button.disabled=true;
    button.textContent='Starting…';
  }

  const body=new URLSearchParams();
  body.set('app',SEARCH_APP);
  body.set('id',String(id));

  try{
    const response=await fetch(
      '/manual-optimize',
      {
        method:'POST',
        headers:{
          'Content-Type':
            'application/x-www-form-urlencoded;charset=UTF-8',
          'X-Requested-With':'fetch'
        },
        body:body.toString(),
        cache:'no-store'
      }
    );

    if(!response.ok){
      let message='Could not start optimizer.';

      if(response.status===409){
        message=
          'Optimizer is already running, or this item is excluded.';
      }

      throw new Error(message);
    }

    const result=await response.json();

    showOptimizerToast(
      'Optimizing '+title+'…',
      'running'
    );

    await waitForManualOptimizer(
      id,
      title,
      button,
      Number(result.started || 0)
    );

  }catch(err){
    showOptimizerToast(
      err.message || 'Could not start optimizer.',
      'error'
    );

    if(button){
      button.disabled=false;
      button.textContent='Optimize';
    }
  }
}

exclusionResults.addEventListener('click',event=>{
  const button=
    event.target.closest('.manualOptimizeButton');

  if(!button){
    return;
  }

  startManualOptimizer(
    Number(button.dataset.id),
    button.dataset.title || 'Selected item',
    button
  );
});

function setSearchMode(mode){
  searchMode=mode;

  box.value='';
  exclusionResults.innerHTML='';

  modeButtons.forEach(button=>{
    button.classList.toggle(
      'active',
      button.dataset.searchMode===mode
    );
  });

  restoreDashboardRows();

  if(recentExclusions){
    recentExclusions.classList.toggle(
      'visible',
      mode==='exclude'
    );
  }

  if(mode==='exclude'){
    box.placeholder=
      SEARCH_APP==='radarr'
        ? 'Search movies in Radarr library to exclude…'
        : 'Search series in Sonarr library to exclude…';

    loadRecentExclusions();

  }else if(mode==='manual'){
    box.placeholder=
      SEARCH_APP==='radarr'
        ? 'Search a movie to optimize…'
        : 'Search a series to optimize…';

  }else{
    box.placeholder=
      'Search releases and current downloads…';
  }

  box.focus();
}

box.addEventListener('input',()=>{
  if(searchMode==='exclude'){
    searchExclusionLibrary();
  }else if(searchMode==='manual'){
    searchManualLibrary();
  }else{
    filterCurrentDownloads();
  }
});

modeButtons.forEach(button=>{
  button.addEventListener('click',()=>{
    setSearchMode(button.dataset.searchMode);
  });
});

setSearchMode('current');
</script>
%s

<!-- SMART DASHBOARD NAV START -->

<script>
(function(){

    const nav =
        document.querySelector(
            '.topbar .nav'
        );

    if(!nav){
        return;
    }


    if(
        !nav.querySelector(
            '.dashboard-home-link'
        )
    ){

        const home =
            document.createElement(
                'a'
            );

        home.href = '/';

        home.className =
            'badge dashboard-home-link';

        home.textContent =
            'Home';

        nav.insertBefore(
            home,
            nav.firstChild
        );

    }


    if(
        !nav.querySelector(
            '.dashboard-settings-link'
        )
    ){

        const settings =
            document.createElement(
                'a'
            );

        settings.href =
            '/settings';

        settings.className =
            'badge dashboard-settings-link';

        settings.textContent =
            '⚙ Settings';

        nav.appendChild(
            settings
        );

    }

})();
</script>

<!-- SMART DASHBOARD NAV END -->

</body></html>""" % (
        CSS, "" if connection_status == "Online" else " bad", html.escape(connection_status), son_actions, err, exclusion_panel("sonarr"), "good" if saved >= 0 else "bad", gib(saved), positive, len(queue), used, extra_today,
        rows, rule_min, rule_max, daily_search_budget("sonarr"), extra_today, len(queue), qrows,
        "good" if reduction_pct >= 0 else "bad", reduction_pct, positive, len(queue),
        html.escape(last_date), AJAX_SCRIPT)



# SMART RAM SWR CACHE START
#
# RAM-only stale-while-revalidate cache.
#
# Nothing in this cache is written to disk.
# Existing Smart Optimizer state/config files are untouched.
#
# Behaviour:
#   - first fetch populates RAM
#   - fresh cache returns immediately
#   - stale cache returns immediately
#   - stale data refreshes in a daemon thread
#

import threading as _smart_ram_threading
import time as _smart_ram_time

_SMART_RAM_CACHE = {}
_SMART_RAM_LOCK = _smart_ram_threading.RLock()
_SMART_RAM_ORIGINALS = {}


def _smart_ram_make_key(name, args, kwargs):

    try:
        args_key = repr(args)
    except Exception:
        args_key = "<args>"

    try:
        kwargs_key = repr(
            sorted(
                kwargs.items(),
                key=lambda item: str(item[0])
            )
        )
    except Exception:
        kwargs_key = "<kwargs>"

    return (
        name,
        args_key,
        kwargs_key,
    )


def _smart_ram_refresh(
    key,
    name,
    original,
    args,
    kwargs,
):

    try:

        value = original(
            *args,
            **kwargs
        )

    except Exception as exc:

        with _SMART_RAM_LOCK:

            entry = _SMART_RAM_CACHE.setdefault(
                key,
                {}
            )

            entry["refreshing"] = False
            entry["error"] = repr(exc)

        print(
            "[ram-cache] refresh error",
            name,
            repr(exc),
            flush=True,
        )

        return

    with _SMART_RAM_LOCK:

        entry = _SMART_RAM_CACHE.setdefault(
            key,
            {}
        )

        entry["value"] = value
        entry["updated"] = (
            _smart_ram_time.monotonic()
        )
        entry["refreshing"] = False
        entry["error"] = None


def _smart_ram_wrap(
    name,
    ttl,
):

    original = globals().get(name)

    if not callable(original):

        print(
            "[ram-cache] SKIP - function not found:",
            name,
            flush=True,
        )

        return False

    # Prevent wrapping an already wrapped function.
    if getattr(
        original,
        "_smart_ram_cached",
        False,
    ):

        print(
            "[ram-cache] already wrapped:",
            name,
            flush=True,
        )

        return True

    _SMART_RAM_ORIGINALS[name] = original


    def cached(
        *args,
        **kwargs
    ):

        key = _smart_ram_make_key(
            name,
            args,
            kwargs,
        )

        now = (
            _smart_ram_time.monotonic()
        )

        with _SMART_RAM_LOCK:

            entry = _SMART_RAM_CACHE.get(
                key
            )

            if (
                entry is not None
                and "value" in entry
            ):

                value = entry["value"]

                age = (
                    now
                    - float(
                        entry.get(
                            "updated",
                            0.0,
                        )
                    )
                )

                # Still fresh.
                if age < ttl:

                    return value

                # Stale:
                # return existing value immediately,
                # refresh asynchronously only once.
                if not entry.get(
                    "refreshing",
                    False,
                ):

                    entry["refreshing"] = True

                    _smart_ram_threading.Thread(
                        target=_smart_ram_refresh,
                        args=(
                            key,
                            name,
                            original,
                            args,
                            kwargs,
                        ),
                        daemon=True,
                        name=(
                            "smart-cache-"
                            + name
                        ),
                    ).start()

                return value


        # No cached value exists yet.
        # First load is synchronous so the page
        # receives real data instead of empty data.

        try:

            value = original(
                *args,
                **kwargs
            )

        except Exception:

            raise


        with _SMART_RAM_LOCK:

            _SMART_RAM_CACHE[key] = {
                "value": value,
                "updated": (
                    _smart_ram_time.monotonic()
                ),
                "refreshing": False,
                "error": None,
            }

        return value


    cached._smart_ram_cached = True
    cached._smart_ram_original = original
    cached.__name__ = getattr(
        original,
        "__name__",
        name,
    )

    globals()[name] = cached

    print(
        "[ram-cache] wrapped:",
        name,
        "TTL=",
        ttl,
        "seconds",
        flush=True,
    )

    return True


#
# History changes comparatively slowly,
# so keep it longer.
#

_smart_ram_wrap(
    "history_records",
    30.0,
)

_smart_ram_wrap(
    "sonarr_history_records",
    30.0,
)


#
# Queue information should stay fairly fresh.
#

_smart_ram_wrap(
    "queue_records",
    5.0,
)

_smart_ram_wrap(
    "sonarr_queue_records",
    5.0,
)


#
# Online/offline checks are also network calls.
#

_smart_ram_wrap(
    "api_online",
    5.0,
)


def _smart_ram_prewarm():

    # Allow normal module startup to finish first.
    _smart_ram_time.sleep(
        0.75
    )

    jobs = (
        (
            "history_records",
            (),
        ),
        (
            "queue_records",
            (),
        ),
        (
            "sonarr_history_records",
            (),
        ),
        (
            "sonarr_queue_records",
            (),
        ),
        (
            "api_online",
            ("radarr",),
        ),
        (
            "api_online",
            ("sonarr",),
        ),
    )

    for name, args in jobs:

        fn = globals().get(
            name
        )

        if not callable(fn):
            continue

        try:

            fn(
                *args
            )

            print(
                "[ram-cache] prewarmed:",
                name,
                args,
                flush=True,
            )

        except Exception as exc:

            print(
                "[ram-cache] prewarm error:",
                name,
                repr(exc),
                flush=True,
            )


_smart_ram_threading.Thread(
    target=_smart_ram_prewarm,
    daemon=True,
    name="smart-dashboard-cache-prewarm",
).start()

# SMART RAM SWR CACHE END

class Handler(BaseHTTPRequestHandler):
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

        if path == "/smart-optimizer-logo.png":

            body = _smart_asset_bytes('smart-optimizer-logo.png')

            self.send_response(200)

            self.send_header(
                "Content-Type",
                "image/png"
            )

            self.send_header(
                "Content-Length",
                str(len(body))
            )

            self.send_header(
                "Cache-Control",
                "public, max-age=86400"
            )

            self.end_headers()
            self.wfile.write(body)
            return


        if path == "/home-background.png":

            body = _smart_asset_bytes('home-background.png')

            self.send_response(200)
            self.send_header(
                "Content-Type",
                "image/png"
            )
            self.send_header(
                "Content-Length",
                str(len(body))
            )
            self.send_header(
                "Cache-Control",
                "no-cache, must-revalidate"
            )
            self.end_headers()
            self.wfile.write(body)
            return


        if path == "/radarr-icon.png":

            body = _smart_asset_bytes('radarr-icon.png')

            self.send_response(200)
            self.send_header(
                "Content-Type",
                "image/png"
            )
            self.send_header(
                "Content-Length",
                str(len(body))
            )
            self.send_header(
                "Cache-Control",
                "public, max-age=86400"
            )
            self.end_headers()
            self.wfile.write(body)
            return


        if path == "/sonarr-icon.png":

            body = _smart_asset_bytes('sonarr-icon.png')

            self.send_response(200)
            self.send_header(
                "Content-Type",
                "image/png"
            )
            self.send_header(
                "Content-Length",
                str(len(body))
            )
            self.send_header(
                "Cache-Control",
                "public, max-age=86400"
            )
            self.end_headers()
            self.wfile.write(body)
            return


        if path == "/settings-icon.png":

            body = _smart_asset_bytes('settings-icon.png')

            self.send_response(200)
            self.send_header(
                "Content-Type",
                "image/png"
            )
            self.send_header(
                "Content-Length",
                str(len(body))
            )
            self.send_header(
                "Cache-Control",
                "public, max-age=86400"
            )
            self.end_headers()
            self.wfile.write(body)
            return



        if path == "/favicon.ico":

            body = _smart_asset_bytes(
                "pixel32.png"
            )

            self.send_response(200)

            self.send_header(
                "Content-Type",
                "image/png"
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

            self.wfile.write(
                body
            )

            return


        if path == "/smart-optimizer-logo-web.png":

            body = _smart_asset_bytes('smart-optimizer-logo-web.png')

            self.send_response(200)

            self.send_header(
                "Content-Type",
                "image/png"
            )

            self.send_header(
                "Content-Length",
                str(len(body))
            )

            self.send_header(
                "Cache-Control",
                "public, max-age=86400"
            )

            self.end_headers()

            self.wfile.write(
                body
            )

            return



        if path == "/radarr-background.png":

            body = _smart_asset_bytes('radarr-background.png')

            self.send_response(200)

            self.send_header(
                "Content-Type",
                "image/png"
            )

            self.send_header(
                "Content-Length",
                str(len(body))
            )

            self.send_header(
                "Cache-Control",
                "no-cache, must-revalidate"
            )

            self.end_headers()

            self.wfile.write(body)

            return


        if path == "/sonarr-background.png":

            body = _smart_asset_bytes('sonarr-background.png')

            self.send_response(200)

            self.send_header(
                "Content-Type",
                "image/png"
            )

            self.send_header(
                "Content-Length",
                str(len(body))
            )

            self.send_header(
                "Cache-Control",
                "no-cache, must-revalidate"
            )

            self.end_headers()

            self.wfile.write(body)

            return

        if not self._require_auth(path):
            return
        if path in (
            "/connection-settings",
            "/budget-settings",
            "/auth-settings",
        ):
            self.send_response(303)
            self.send_header("Location", "/settings")
            self.end_headers()
            return

        if path == "/update-package-file":

            try:
                (
                    filename,
                    file_path,
                    _target_version,
                ) = verified_update_file()

                file_size = os.path.getsize(
                    file_path
                )

                safe_name = (
                    filename
                    .replace(
                        '"',
                        ""
                    )
                    .replace(
                        "\\",
                        "_"
                    )
                )

                self.send_response(
                    200
                )

                self.send_header(
                    "Content-Type",
                    "application/octet-stream"
                )

                self.send_header(
                    "Content-Disposition",
                    'attachment; filename="%s"'
                    % safe_name
                )

                self.send_header(
                    "Content-Length",
                    str(
                        file_size
                    )
                )

                self.send_header(
                    "Cache-Control",
                    "no-store"
                )

                self.end_headers()

                with open(
                    file_path,
                    "rb"
                ) as fh:

                    while True:

                        chunk = fh.read(
                            1024 * 1024
                        )

                        if not chunk:
                            break

                        self.wfile.write(
                            chunk
                        )

            except Exception as exc:

                self.send_error(
                    404,
                    str(exc)
                )

            return


        if path == "/update-status":

            payload = (
                update_status_payload()
            )

            body = json.dumps(
                payload
            ).encode(
                "utf-8"
            )

            self.send_response(200)

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

            self.wfile.write(
                body
            )

            return


        if path == "/status":
            app = (urllib.parse.parse_qs(parsed.query).get("app") or [""])[0]
            if app not in ("radarr", "sonarr"):
                self.send_error(400); return
            body = json.dumps(manual_status(app)).encode("utf-8")
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(body); return
        if path == "/library-search":
            qs = urllib.parse.parse_qs(parsed.query)
            app = (qs.get("app") or [""])[0]
            q = (qs.get("q") or [""])[0].strip().lower()

            if app not in ("radarr", "sonarr"):
                self.send_error(400)
                return

            try:
                matches = []
                if q:
                    for x in library_items(app):
                        hay = ("%s %s" % (
                            x.get("title") or "",
                            x.get("year") or ""
                        )).lower()

                        if q in hay:
                            matches.append(x)

                        if len(matches) >= 12:
                            break

                body = json.dumps(matches).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
            except Exception as exc:
                self.send_error(500, str(exc))
            return

        if path == "/exclusions-json":
            qs = urllib.parse.parse_qs(parsed.query)
            app = (qs.get("app") or [""])[0]

            if app not in ("radarr", "sonarr"):
                self.send_error(400)
                return

            items = sorted(
                optimizer_exclusions(app),
                key=lambda x: x.get("added") or "",
                reverse=True
            )

            body = json.dumps(items).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/":
            rendered = home_page()
        elif path == "/radarr":
            rendered = page()
        elif path == "/sonarr":
            rendered = sonarr_page()
        elif path == "/radarr/rules":
            rendered = rules_page("radarr")
        elif path == "/sonarr/rules":
            rendered = rules_page("sonarr")
        elif path == "/settings":
            rendered = settings_page()

        elif path == "/updates":

            qs = urllib.parse.parse_qs(
                parsed.query
            )

            force = (
                (
                    qs.get("refresh")
                    or [""]
                )[0]
                == "1"
            )

            rendered = updates_page(
                force=force
            )
        elif path == "/radarr/history":
            rendered = history_page("radarr")
        elif path == "/sonarr/history":
            rendered = history_page("sonarr")
        elif path == "/radarr/exclusions":
            rendered = exclusions_page("radarr")
        elif path == "/sonarr/exclusions":
            rendered = exclusions_page("sonarr")
        else:
            self.send_error(404); return
        body = rendered.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
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

        if path == "/update-download":

            if not ENABLE_ACTIONS:

                self.send_error(
                    403
                )

                return


            try:

                result = (
                    download_latest_update()
                )

                target = str(
                    (
                        result.get(
                            "release"
                        )
                        or {}
                    ).get(
                        "version"
                    )
                    or ""
                )


                if SMART_SELF_UPDATE_MODE == "app-bundle":

                    self.send_response(
                        303
                    )

                    self.send_header(
                        "Location",
                        "/updates?refresh=1"
                    )

                    self.send_header(
                        "Cache-Control",
                        "no-store"
                    )

                    self.end_headers()

                    return


                update_message = (
                    "Smart Optimizer v%s downloaded and SHA-256 "
                    "verified. Use Download verified SPK below, then "
                    "install it over the current version in DSM "
                    "Package Center > Manual Install."
                    % target
                )


                rendered = updates_page(
                    update_message
                )


            except Exception as exc:

                rendered = updates_page(
                    str(exc),
                    True
                )


            body = rendered.encode(
                "utf-8"
            )


            self.send_response(
                200
            )

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

            self.wfile.write(
                body
            )

            return



        # SMART RULES SAVE ENDPOINT START
        if self.path == "/rules-settings":

            if not ENABLE_ACTIONS:
                self.send_error(403)
                return

            app = (
                form.get("app")
                or [""]
            )[0]

            key = (
                form.get("key")
                or [""]
            )[0]

            enabled_raw = (
                form.get("enabled")
                or [""]
            )[0]

            if enabled_raw not in (
                "0",
                "1",
            ):
                self.send_error(
                    400,
                    "Invalid enabled value"
                )
                return

            try:
                update_selectable_rule(
                    app,
                    key,
                    enabled_raw == "1",
                )

                enabled_count, total_count = (
                    rules_summary(app)
                )

                result = {
                    "ok": True,
                    "app": app,
                    "key": key,
                    "value": (
                        enabled_raw == "1"
                    ),
                    "enabled": enabled_count,
                    "total": total_count,
                }

                body = json.dumps(
                    result
                ).encode(
                    "utf-8"
                )

                self.send_response(200)

            except Exception as exc:

                body = json.dumps(
                    {
                        "ok": False,
                        "error": str(exc),
                    }
                ).encode(
                    "utf-8"
                )

                self.send_response(400)

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
            return

        # SMART RULES SAVE ENDPOINT END
        # SMART RESOLUTION POLICY SAVE ENDPOINT START

        if path == "/resolution-policy-settings":

            if not ENABLE_ACTIONS:
                self.send_error(403)
                return

            try:
                app = (
                    form.get("app")
                    or [""]
                )[0]

                kind = (
                    form.get("kind")
                    or [""]
                )[0]

                key = (
                    form.get("key")
                    or [""]
                )[0]


                if kind == "path":

                    enabled = (
                        (
                            form.get("enabled")
                            or ["0"]
                        )[0]
                        == "1"
                    )

                    update_resolution_path(
                        app,
                        key,
                        enabled
                    )


                elif kind == "limit":

                    enabled = (
                        (
                            form.get("enabled")
                            or ["0"]
                        )[0]
                        == "1"
                    )

                    value = (
                        form.get("value")
                        or [""]
                    )[0]

                    unit = (
                        form.get("unit")
                        or [""]
                    )[0]

                    update_resolution_limit(
                        app,
                        key,
                        enabled,
                        value,
                        unit,
                    )


                elif kind == "growth":

                    minimum = (
                        form.get("min_percent")
                        or [""]
                    )[0]

                    maximum = (
                        form.get("max_percent")
                        or [""]
                    )[0]

                    update_upgrade_growth(
                        app,
                        key,
                        minimum,
                        maximum,
                    )


                else:
                    raise ValueError(
                        "Unknown policy setting type"
                    )


                result = {
                    "ok": True,
                    "app": app,
                    "policy":
                        resolution_policy_settings(app),
                }

                body = json.dumps(
                    result
                ).encode("utf-8")

                self.send_response(200)

                self.send_header(
                    "Content-Type",
                    "application/json; charset=utf-8"
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


            except Exception as exc:

                body = json.dumps({
                    "ok": False,
                    "error": str(exc),
                }).encode("utf-8")

                self.send_response(400)

                self.send_header(
                    "Content-Type",
                    "application/json; charset=utf-8"
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


        # SMART RESOLUTION POLICY SAVE ENDPOINT END
        # SMART ADVANCED PREFERENCES V2 SAVE ENDPOINT START

        if path == "/advanced-preferences-settings":

            if not ENABLE_ACTIONS:
                self.send_error(403)
                return

            try:

                app = (
                    form.get("app")
                    or [""]
                )[0]

                settings_raw = (
                    form.get("settings")
                    or ["{}"]
                )[0]

                settings = json.loads(
                    settings_raw
                )

                update_advanced_preferences(
                    app,
                    settings
                )

                result = {
                    "ok": True,
                    "app": app,
                    "preferences":
                        advanced_preferences(
                            app
                        ),
                }

                body = json.dumps(
                    result
                ).encode(
                    "utf-8"
                )

                self.send_response(200)

                self.send_header(
                    "Content-Type",
                    "application/json; charset=utf-8"
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

                self.wfile.write(
                    body
                )

                return


            except Exception as exc:

                body = json.dumps({
                    "ok": False,
                    "error": str(exc),
                }).encode(
                    "utf-8"
                )

                self.send_response(400)

                self.send_header(
                    "Content-Type",
                    "application/json; charset=utf-8"
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

                self.wfile.write(
                    body
                )

                return


        # SMART ADVANCED PREFERENCES V2 SAVE ENDPOINT END



        if self.path == "/connection-settings":
            if not ENABLE_ACTIONS:
                self.send_error(403); return
            app = (form.get("app") or [""])[0]
            action = (form.get("action") or [""])[0]
            if app not in ("radarr", "sonarr") or action not in ("test", "save"):
                self.send_error(400); return
            try:
                scheme = (form.get("scheme") or ["http"])[0]
                host = (form.get("host") or [""])[0]
                port = int((form.get("port") or ["0"])[0])
                key = (form.get("api_key") or [""])[0]
                if action == "test":
                    version = test_connection(app, scheme, host, port, key)
                    rendered = settings_page("%s connection successful · version %s" % (app.capitalize(), version))
                else:
                    test_connection(app, scheme, host, port, key)
                    update_connection(app, scheme, host, port, key)
                    rendered = settings_page("%s settings saved and connection verified." % app.capitalize())
            except Exception as exc:
                rendered = settings_page("%s: %s" % (app.capitalize(), str(exc)), True)
            body = rendered.encode("utf-8")
            self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(body))); self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(body); return
        if self.path == "/budget-settings":
            if not ENABLE_ACTIONS:
                self.send_error(403); return
            app = (form.get("app") or [""])[0]
            if app not in ("radarr", "sonarr"):
                self.send_error(400); return
            try:
                update_daily_search_budget(app, int((form.get("budget") or [""])[0]))
                rendered = settings_page("%s daily search budget saved." % app.capitalize())
            except Exception as exc:
                rendered = settings_page(str(exc), True)
            body = rendered.encode("utf-8")
            self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(body))); self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(body); return
        if self.path == "/settings":
            if not ENABLE_ACTIONS:
                self.send_error(403); return
            app = (form.get("app") or [""])[0]
            if app not in ("radarr", "sonarr"):
                self.send_error(400); return
            try:
                update_saving_window(app, float((form.get("min") or [""])[0]), float((form.get("max") or [""])[0]))
            except Exception as exc:
                self.send_error(400, str(exc)); return
            self.send_response(303); self.send_header("Location", "/" + app); self.end_headers(); return
        if self.path == "/exclusion-add":
            if not ENABLE_ACTIONS:
                self.send_error(403)
                return

            app = (form.get("app") or [""])[0]

            if app not in ("radarr", "sonarr"):
                self.send_error(400)
                return

            try:
                item_id = int((form.get("id") or [""])[0])

                # Resolve title/year ourselves from the configured library.
                # Browser is never trusted to tell us what item this is.
                found = None
                for x in library_items(app):
                    if int(x["id"]) == item_id:
                        found = x
                        break

                if not found:
                    raise ValueError("Library item not found")

                add_optimizer_exclusion(
                    app,
                    item_id,
                    found["title"],
                    found.get("year")
                )
            except Exception as exc:
                self.send_error(400, str(exc))
                return

            if self.headers.get("X-Requested-With") == "fetch":
                body = json.dumps({
                    "ok": True,
                    "app": app,
                    "id": item_id,
                    "count": len(optimizer_exclusions(app))
                }).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return

            self.send_response(303)
            self.send_header("Location", "/" + app)
            self.end_headers()
            return

        if self.path == "/exclusion-remove":
            if not ENABLE_ACTIONS:
                self.send_error(403)
                return

            app = (form.get("app") or [""])[0]

            if app not in ("radarr", "sonarr"):
                self.send_error(400)
                return

            try:
                item_id = int((form.get("id") or [""])[0])
                remove_optimizer_exclusion(app, item_id)
            except Exception as exc:
                self.send_error(400, str(exc))
                return

            if self.headers.get("X-Requested-With") == "fetch":
                body = json.dumps({
                    "ok": True,
                    "app": app,
                    "id": item_id,
                    "count": len(optimizer_exclusions(app))
                }).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return

            referer = self.headers.get("Referer") or ""
            target = "/" + app

            if "/exclusions" in referer:
                target += "/exclusions"

            self.send_response(303)
            self.send_header("Location", target)
            self.end_headers()
            return

        if self.path == "/manual-optimize":
            if not ENABLE_ACTIONS:
                self.send_error(403)
                return

            app = (form.get("app") or [""])[0]

            if app not in ("radarr", "sonarr"):
                self.send_error(400, "Invalid app")
                return

            try:
                item_id = int((form.get("id") or [""])[0])
            except (TypeError, ValueError):
                self.send_error(400, "Invalid library item")
                return

            item = next(
                (
                    x for x in library_items(app)
                    if int(x.get("id") or 0) == item_id
                ),
                None
            )

            if not item:
                self.send_error(404, "Library item not found")
                return

            if item.get("excluded"):
                self.send_error(
                    409,
                    "Remove this item from Exclusions before optimizing it."
                )
                return

            if app == "radarr":
                started = run_optimizer(
                    True,
                    app="radarr",
                    target_movie_id=item_id,
                    manual_target=True
                )
            else:
                started = run_optimizer(
                    True,
                    app="sonarr",
                    target_series_id=item_id,
                    manual_target=True
                )

            if not started:
                self.send_error(
                    409,
                    "Another optimizer job is already running."
                )
                return

            with job_lock:
                job_started = jobs[app].get("started")

            body = json.dumps({
                "ok": True,
                "app": app,
                "id": item_id,
                "title": item.get("title") or "",
                "year": item.get("year"),
                "started": job_started
            }).encode("utf-8")

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path == "/manual-search":
            if not ENABLE_ACTIONS: self.send_error(403); return
            app = (form.get("app") or [""])[0]
            try:
                count = int((form.get("count") or [""])[0])
                if app not in ("radarr", "sonarr") or not 1 <= count <= MAX_MANUAL: raise ValueError()
            except Exception:
                self.send_error(400, "Invalid manual search amount"); return
            if not run_optimizer(True, app, count, daily_extra=count):
                self.send_error(409, "Another UI optimizer run is already active"); return
            self.send_response(303); self.send_header("Location", "/" + app); self.end_headers(); return
        if self.path == "/stop":
            if not ENABLE_ACTIONS: self.send_error(403); return
            app = (form.get("app") or [""])[0]
            if app not in ("radarr", "sonarr"): self.send_error(400); return
            stop_optimizer(app)
            self.send_response(303); self.send_header("Location", "/" + app); self.end_headers(); return
        if self.path == "/repair-import":
            if not ENABLE_ACTIONS: self.send_error(403); return
            try:
                repair_import((form.get("queue_id") or [""])[0])
            except Exception as exc:
                print("[ui] safe import failed:", exc)
                self.send_error(409, str(exc)); return
            self.send_response(303); self.send_header("Location", "/radarr"); self.end_headers(); return
        if self.path != "/run" or not ENABLE_ACTIONS:
            self.send_error(403); return
        mode = (form.get("mode") or [""])[0]
        if mode not in ("dry", "live"):
            self.send_error(400); return
        run_optimizer(mode == "live")
        self.send_response(303)
        self.send_header("Location", "/radarr")
        self.end_headers()

    def log_message(self, fmt, *args):
        print("[ui] " + fmt % args)


if __name__ == "__main__":
    print("Radarr Smart Optimizer UI")
    print("Listening on http://%s:%d" % (HOST, PORT))
    print("Actions:", "ENABLED" if ENABLE_ACTIONS else "disabled (read-only)")
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
    threading.Thread(
        target=library_cache_worker,
        name="library-cache",
        daemon=True
    ).start()

    threading.Thread(
        target=optimizer_import_worker,
        name="radarr-optimizer-import",
        daemon=True
    ).start()

    threading.Thread(
        target=sonarr_tracker_retention_worker,
        name="sonarr-tracker-retention",
        daemon=True
    ).start()
    threading.Thread(
        target=dead_download_watchdog_worker,
        name="arr-dead-download-watchdog",
        daemon=True
    ).start()
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
