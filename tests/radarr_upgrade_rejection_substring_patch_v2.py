#!/usr/bin/env python3
import hashlib
import sys
from pathlib import Path

if len(sys.argv) != 2:
    raise SystemExit("usage: radarr_upgrade_rejection_substring_patch_v2.py UI")

ui_path = Path(sys.argv[1])
ui = ui_path.read_text(encoding="utf-8")

EXPECTED = "4bee1f0e1ae2dcd1d45c33806174ec164835b60b42367d28c1c0563d9af25c53"
actual = hashlib.sha256(ui.encode("utf-8")).hexdigest()

if actual != EXPECTED:
    raise SystemExit("STOP: UI hash mismatch: " + actual)

old = "not an upgrade for existing movie file"
new = "upgrade for existing movie file"

count = ui.count(old)
if count < 1:
    raise SystemExit("STOP: old Radarr rejection phrase not found")

print("Matching old phrase occurrences:", count)

ui = ui.replace(old, new)

if old in ui:
    raise SystemExit("STOP: old phrase still present after replacement")

ui_path.write_text(ui, encoding="utf-8")

print("RADARR UPGRADE-REJECTION SUBSTRING PATCH V2 COMPLETE")
print("Replaced occurrences:", count)
print(
    "UI SHA256",
    hashlib.sha256(ui.encode("utf-8")).hexdigest()
)
