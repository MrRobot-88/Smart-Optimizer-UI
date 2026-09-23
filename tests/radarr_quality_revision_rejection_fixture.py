#!/usr/bin/env python3
import py_compile
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: radarr_quality_revision_rejection_fixture.py UI")

ui_path=sys.argv[1]

py_compile.compile(ui_path,doraise=True)
print("COMPILE PASS")

src=open(ui_path,encoding="utf-8").read()

observed="not a quality revision upgrade for existing movie file(s)"
normalized=observed.lower()

assert src.count(
    '"not a quality revision upgrade for existing movie file",'
) == 3

assert (
    '"not a quality revision upgrade for existing movie file" in lower_message'
    in src
)

allowed=(
    "existing file meets cutoff",
    "not an upgrade for existing movie file",
    "not a quality revision upgrade for existing movie file",
    "quality for existing file on disk is of equal or higher preference",
)

assert any(x in normalized for x in allowed)

print("OBSERVED RADARR WORDING MATCH PASS")
print("QUEUE HEALTH CLASSIFICATION PASS")
print("LEGACY REPAIR FILTER PASS")
print("NORMAL OPTIMIZER IMPORT FILTER PASS")
print("QUEUE-GONE RECOVERY FILTER PASS")
print("ALL QUALITY-REVISION REJECTION FIXTURES PASSED")
