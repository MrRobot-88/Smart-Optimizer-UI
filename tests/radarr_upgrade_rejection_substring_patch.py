#!/usr/bin/env python3
import hashlib
import sys
from pathlib import Path

if len(sys.argv) != 2:
    raise SystemExit("usage: radarr_upgrade_rejection_substring_patch.py UI")

ui_path = Path(sys.argv[1])
ui = ui_path.read_text(encoding="utf-8")

EXPECTED = "4bee1f0e1ae2dcd1d45c33806174ec164835b60b42367d28c1c0563d9af25c53"
actual = hashlib.sha256(ui.encode("utf-8")).hexdigest()

if actual != EXPECTED:
    raise SystemExit("STOP: UI hash mismatch: " + actual)

old_classifier = 'if "not an upgrade for existing movie file" in lower_message:'
new_classifier = 'if "upgrade for existing movie file" in lower_message:'

if ui.count(old_classifier) != 1:
    raise SystemExit(
        "STOP: classifier anchor count=%d"
        % ui.count(old_classifier)
    )

ui = ui.replace(old_classifier, new_classifier, 1)

old_allowed = '"not an upgrade for existing movie file",'
new_allowed = '"upgrade for existing movie file",'

count = ui.count(old_allowed)
if count != 3:
    raise SystemExit(
        "STOP: expected 3 allowed-rejection anchors, found %d"
        % count
    )

ui = ui.replace(old_allowed, new_allowed)

ui_path.write_text(ui, encoding="utf-8")

print("RADARR UPGRADE-REJECTION SUBSTRING PATCH COMPLETE")
print(
    "UI SHA256",
    hashlib.sha256(ui.encode("utf-8")).hexdigest()
)
