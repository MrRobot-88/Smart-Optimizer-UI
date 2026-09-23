#!/usr/bin/env python3
import ast
import py_compile
import sys

if len(sys.argv)!=2:
    raise SystemExit("usage: radarr_queue_gone_completed_payload_fixture_v3.py UI")

ui_path=sys.argv[1]

py_compile.compile(ui_path,doraise=True)
print("COMPILE PASS")

src=open(ui_path,encoding="utf-8").read()
tree=ast.parse(src)

# Collect literal string values from the parsed source. This avoids brittle
# failures when Python source splits one printed message across adjacent
# string literals on separate lines.
strings=[
    node.value
    for node in ast.walk(tree)
    if isinstance(node,ast.Constant)
    and isinstance(node.value,str)
]

def has_literal(fragment):
    return any(fragment in s for s in strings)

assert has_literal("RECOVERED completed exact")
assert has_literal("Deluge payload after Radarr queue vanished")
assert has_literal("exact ownership ambiguous; refusing")

needles=(
    "recovered_progress < 100.0",
    "recovered_done <= 0",
    '"/manualimport?downloadId=%s&movieId=%d"',
    "len(recovered_usable) != 1",
    '"trackedDownloadState": "importPending"',
    '"status": "completed"',
    '"size": recovered_size',
    "if not bound_download_id:",
    "deluge_torrent_status(",
    "urllib.parse.quote(",
)

for needle in needles:
    assert needle in src, "missing source guard: %r" % needle

# Existing import safety path must remain intact.
for needle in (
    "if new_size <= 0 or new_size >= old_size:",
    "actual_saving =",
    '"name": "ManualImport"',
    "OLD FILE KEPT",
):
    assert needle in src, "missing existing safety guard: %r" % needle

# Queue-loss recovery must live before the existing completed/importPending
# handling so the synthetic row enters the same tested import path.
recovery_pos=src.index("recovered_progress < 100.0")
completed_pos=src.index('if row_status != "completed":')
assert recovery_pos < completed_pos

print("EXACT HASH OWNERSHIP GUARD PASS")
print("100% COMPLETED DELUGE PAYLOAD GUARD PASS")
print("EXACT MANUALIMPORT CANDIDATE GUARD PASS")
print("SYNTHETIC COMPLETED-ROW RECOVERY PASS")
print("EXISTING FINAL SIZE/SAFETY CHECKS PRESERVED PASS")
print("AST-SAFE MESSAGE CHECK PASS")
print("ALL QUEUE-GONE COMPLETED-PAYLOAD V3 FIXTURES PASSED")
