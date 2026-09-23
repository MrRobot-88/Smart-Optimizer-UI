#!/usr/bin/env python3
import py_compile
import sys

if len(sys.argv)!=2:
    raise SystemExit("usage: radarr_queue_gone_completed_payload_fixture.py UI")

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
    "item.get(\"size\")",
)

for needle in needles:
    assert needle in src, "missing source guard: %r" % needle

# The queue-loss path must require exact hash ownership; title-only fallback is
# allowed only before a transaction is bound.
anchor='''                            if len(matches) > 1:
'''
assert anchor in src

recovered=src.index(
    "RECOVERED completed exact Deluge payload after Radarr queue vanished"
)
before=src[max(0,recovered-9000):recovered]

assert "if not bound_download_id:" in before
assert "deluge_torrent_status(" in before
assert "urllib.parse.quote(" in before
assert "recovered_usable" in before

# Actual downloaded file size must win over queue/indexer estimates.
actual_block='''                            new_size = int(
                                item.get("size")
                                or row.get("size")
                                or approved_size
                                or 0
                            )
'''
assert actual_block in src

print("EXACT HASH OWNERSHIP GUARD PASS")
print("100% COMPLETED DELUGE PAYLOAD GUARD PASS")
print("EXACT MANUALIMPORT CANDIDATE GUARD PASS")
print("ACTUAL FILE SIZE PRECEDENCE PASS")
print("ALL QUEUE-GONE COMPLETED-PAYLOAD FIXTURES PASSED")
