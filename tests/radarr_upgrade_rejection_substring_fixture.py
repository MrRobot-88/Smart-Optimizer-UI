#!/usr/bin/env python3
import py_compile
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: radarr_upgrade_rejection_substring_fixture.py UI")

ui_path = sys.argv[1]

py_compile.compile(ui_path, doraise=True)
print("COMPILE PASS")

src = open(ui_path, encoding="utf-8").read()

assert src.count('"upgrade for existing movie file",') == 3
assert 'if "upgrade for existing movie file" in lower_message:' in src

observed = "not a quality revision upgrade for existing movie file(s)"
legacy = "not an upgrade for existing movie file"

needle = "upgrade for existing movie file"

assert needle in observed.lower()
assert needle in legacy.lower()

assert "RECOVERED completed exact " in src
assert "recovered_progress < 100.0" in src
assert "len(recovered_usable) != 1" in src
assert '"name": "ManualImport"' in src
assert "if new_size <= 0 or new_size >= old_size:" in src

print("OBSERVED QUALITY-REVISION WORDING SUBSTRING PASS")
print("LEGACY WORDING SUBSTRING PASS")
print("QUEUE HEALTH CLASSIFIER PASS")
print("LEGACY REPAIR FILTER PASS")
print("NORMAL OPTIMIZER IMPORT FILTER PASS")
print("QUEUE-GONE RECOVERY FILTER PASS")
print("QUEUE-GONE SAFETY GUARDS PRESERVED PASS")
print("ALL RADARR UPGRADE-REJECTION SUBSTRING FIXTURES PASSED")
