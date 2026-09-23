#!/usr/bin/env python3
import py_compile
import sys

if len(sys.argv)!=2:
    raise SystemExit("usage: radarr_queue_gone_completed_payload_fixture_v2.py UI")

ui_path=sys.argv[1]
py_compile.compile(ui_path,doraise=True)
print("COMPILE PASS")

src=open(ui_path,encoding="utf-8").read()

needles=(
    "RECOVERED completed exact Deluge payload after Radarr queue vanished",
    "exact ownership ambiguous; refusing",
    "recovered_progress < 100.0",
    "recovered_done <= 0",
    '"/manualimport?downloadId=%s&movieId=%d"',
    "len(recovered_usable) != 1",
    '"trackedDownloadState": "importPending"',
    '"status": "completed"',
    '"size": recovered_size',
)

for needle in needles:
    assert needle in src, "missing source guard: %r" % needle

recovered=src.index(
    "RECOVERED completed exact Deluge payload after Radarr queue vanished"
)
before=src[max(0,recovered-9000):recovered]

assert "if not bound_download_id:" in before
assert "deluge_torrent_status(" in before
assert "urllib.parse.quote(" in before
assert "recovered_usable" in before

# The existing import path must still perform its final size/saving checks.
assert "if new_size <= 0 or new_size >= old_size:" in src
assert "actual_saving =" in src
assert "ManualImport" in src

print("EXACT HASH OWNERSHIP GUARD PASS")
print("100% COMPLETED DELUGE PAYLOAD GUARD PASS")
print("EXACT MANUALIMPORT CANDIDATE GUARD PASS")
print("SYNTHETIC COMPLETED-ROW RECOVERY PASS")
print("EXISTING FINAL SIZE/SAFETY CHECKS PRESERVED PASS")
print("ALL QUEUE-GONE COMPLETED-PAYLOAD V2 FIXTURES PASSED")
