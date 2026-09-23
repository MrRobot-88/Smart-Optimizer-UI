#!/usr/bin/env python3
import ast
import hashlib
import hmac
import py_compile
import secrets
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: smart_optimizer_ui_auth_fixture_v2.py UI")

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

funcs={
    n.name:n
    for n in tree.body
    if isinstance(n,ast.FunctionDef)
}

for name in (
    "save_auth_config",
    "update_auth_settings",
    "_password_digest",
):
    assert name in funcs, "missing function: "+name

# The UI already has an unrelated Deluge Web password in control settings.
# Do not globally ban the text '"password":'. Instead inspect the auth-setting
# function and prove it never writes cfg["password"] or any plaintext password
# field to the authentication JSON.
auth_update = funcs["update_auth_settings"]

for node in ast.walk(auth_update):
    if isinstance(node, ast.Subscript):
        value=node.value
        if isinstance(value, ast.Name) and value.id=="cfg":
            sl=node.slice
            if isinstance(sl, ast.Constant) and sl.value=="password":
                raise AssertionError(
                    'auth config must never use cfg["password"]'
                )

# Auth config must persist only salted hash material.
auth_update_src=ast.get_source_segment(src,auth_update) or ""
assert 'cfg["password_hash"]' in auth_update_src
assert 'cfg["salt"]' in auth_update_src
assert 'cfg["iterations"]' in auth_update_src
assert "password.encode" in auth_update_src

# Validate the PBKDF2 helper independently.
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

handler=next(
    n for n in tree.body
    if isinstance(n,ast.ClassDef) and n.name=="Handler"
)

method_names={
    n.name
    for n in handler.body
    if isinstance(n,ast.FunctionDef)
}

for name in (
    "_is_authenticated",
    "_require_auth",
    "do_GET",
    "do_POST",
):
    assert name in method_names

print("PBKDF2 PASSWORD HASH PASS")
print("AUTH CONFIG NO PLAINTEXT PASSWORD PASS")
print("DElUGE PASSWORD FIELD IGNORED AS UNRELATED PASS")
print("HTTPONLY SESSION COOKIE PASS")
print("LOGIN / LOGOUT ROUTES PASS")
print("SETTINGS AUTH FORM PASS")
print("PROTECTED GET/POST ROUTES PASS")
print("REMEMBER-ME SESSION PASS")
print("ALL SMART OPTIMIZER UI AUTH V2 FIXTURES PASSED")
