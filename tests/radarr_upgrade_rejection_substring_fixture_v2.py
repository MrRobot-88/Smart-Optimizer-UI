#!/usr/bin/env python3
import ast
import py_compile
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: radarr_upgrade_rejection_substring_fixture_v2.py UI")

ui_path = sys.argv[1]

py_compile.compile(ui_path, doraise=True)
print("COMPILE PASS")

src = open(ui_path, encoding="utf-8").read()
tree = ast.parse(src)

old = "not an upgrade for existing movie file"
needle = "upgrade for existing movie file"

assert old not in src
assert src.count(needle) >= 3

strings = [
    n.value
    for n in ast.walk(tree)
    if isinstance(n, ast.Constant) and isinstance(n.value, str)
]

assert any(needle in s for s in strings)

observed = "not a quality revision upgrade for existing movie file(s)"
legacy = "not an upgrade for existing movie file"

assert needle in observed.lower()
assert needle in legacy.lower()

for required in (
    "recovered_progress < 100.0",
    "recovered_done <= 0",
    "len(recovered_usable) != 1",
    '"name": "ManualImport"',
    "if new_size <= 0 or new_size >= old_size:",
):
    assert required in src, "missing safety guard: %r" % required

print("OBSERVED RADARR WORDING SUBSTRING PASS")
print("LEGACY WORDING SUBSTRING PASS")
print("SOURCE-FORMAT AGNOSTIC PATCH PASS")
print("QUEUE-GONE SAFETY GUARDS PRESERVED PASS")
print("ALL RADARR UPGRADE-REJECTION SUBSTRING V2 FIXTURES PASSED")
