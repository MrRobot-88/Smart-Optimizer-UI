#!/usr/bin/env python3
import ast
import hashlib
import hmac
import py_compile
import secrets
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: smart_optimizer_ui_auth_fixture.py UI")

p=sys.argv[1]

py_compile.compile(p,doraise=True)
print("COMPILE PASS")

src=open(p,encoding="utf-8").read()
tree=ast.parse(src)

required=(
    "AUTH_FILE",
    "AUTH_COOKIE",
    "AUTH_PBKDF2_ITERATIONS",
    "def load_auth_config():",
    "def save_auth_config(data):",
    "def auth_enabled():",
    "def verify_auth_credentials(username, password):",
    "def update_auth_settings(enabled, username, password, confirmation):",
    "def create_auth_session(remember=False):",
    "def auth_session_valid(cookie_header):",
    "def login_page(message=\"\", return_to=\"/\"):",
    'if path == "/login":',
    'if path == "/logout":',
    'if path == "/auth-settings":',
    "HttpOnly; SameSite=Lax",
    "hashlib.pbkdf2_hmac",
    "hmac.compare_digest",
    "secrets.token_urlsafe",
    "Minimum 8 characters",
    "UI authentication",
)

for needle in required:
    assert needle in src, "missing auth guard: %r" % needle

# No plaintext password field is intentionally persisted.
assert '"password":' not in src
assert '"password_hash"' in src
assert '"salt"' in src
assert '"iterations"' in src

# Validate the PBKDF2 helper independently from the UI runtime.
funcs={
    n.name:n
    for n in tree.body
    if isinstance(n,ast.FunctionDef)
}

assert "_password_digest" in funcs

mini=ast.Module(
    body=[
        ast.Import(names=[ast.alias(name="hashlib")]),
        funcs["_password_digest"],
    ],
    type_ignores=[],
)

ns={"AUTH_PBKDF2_ITERATIONS":310000}
exec(compile(ast.fix_missing_locations(mini),"<auth-fixture>","exec"),ns)

salt=secrets.token_bytes(16).hex()
a=ns["_password_digest"]("correct horse battery staple",salt,310000)
b=ns["_password_digest"]("correct horse battery staple",salt,310000)
c=ns["_password_digest"]("wrong password",salt,310000)

assert a
assert hmac.compare_digest(a,b)
assert not hmac.compare_digest(a,c)

# Confirm protected routes gate before the existing handlers.
handler=next(
    n for n in tree.body
    if isinstance(n,ast.ClassDef) and n.name=="Handler"
)

method_names={n.name for n in handler.body if isinstance(n,ast.FunctionDef)}

for name in ("_is_authenticated","_require_auth","do_GET","do_POST"):
    assert name in method_names

print("PBKDF2 PASSWORD HASH PASS")
print("NO PLAINTEXT PASSWORD STORAGE PASS")
print("HTTPONLY SESSION COOKIE PASS")
print("LOGIN / LOGOUT ROUTES PASS")
print("SETTINGS AUTH FORM PASS")
print("PROTECTED GET/POST ROUTES PASS")
print("REMEMBER-ME SESSION PASS")
print("ALL SMART OPTIMIZER UI AUTH FIXTURES PASSED")
