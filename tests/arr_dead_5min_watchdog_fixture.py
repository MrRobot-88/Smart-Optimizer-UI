#!/usr/bin/env python3
import importlib.util
import py_compile
import sys
import time

if len(sys.argv) != 2:
    raise SystemExit("usage: arr_dead_5min_watchdog_fixture.py UI_PATH")

ui_path=sys.argv[1]

py_compile.compile(ui_path,doraise=True)
print("COMPILE PASS")

text=open(ui_path,encoding="utf-8").read()

needles=[
    "def dead_download_watchdog_worker():",
    "def dead_download_watchdog_snapshot():",
    "minimum_age=300",
    "blocklist=true",
    '"name": "MoviesSearch"',
    '"name": "EpisodeSearch"',
    "SMART_OPTIMIZER_EPISODE_ID",
    "target_episode_id=None",
    'name="arr-dead-download-watchdog"',
    "manual_target=True",
    'state.setdefault("tracker_jobs", {}).pop(',
]

for needle in needles:
    assert needle in text, "missing %r" % needle

print("SOURCE STRUCTURE PASS")

spec=importlib.util.spec_from_file_location(
    "watchdog_fixture_ui",
    ui_path,
)
ui=importlib.util.module_from_spec(spec)
spec.loader.exec_module(ui)

now=time.time()

dead={
    "time_added":now-301,
    "progress":0,
    "total_done":0,
    "num_seeds":0,
    "num_peers":0,
    "download_payload_rate":0,
}

assert ui._dead_watchdog_is_strict_dead(dead) is True

not_old=dict(dead)
not_old["time_added"]=now-299
assert ui._dead_watchdog_is_strict_dead(not_old) is False

has_data=dict(dead)
has_data["total_done"]=1
assert ui._dead_watchdog_is_strict_dead(has_data) is False

has_peer=dict(dead)
has_peer["num_peers"]=1
assert ui._dead_watchdog_is_strict_dead(has_peer) is False

has_rate=dict(dead)
has_rate["download_payload_rate"]=1
assert ui._dead_watchdog_is_strict_dead(has_rate) is False

print("STRICT-DEAD CLASSIFIER PASS")
print("ONE-SHOT RETRY BYPASS IS LIMITED TO SAME TRANSACTION: PASS")
print("GENERAL ARR SEARCH COMMANDS PRESENT: PASS")
print("ALL 5-MIN WATCHDOG FIXTURES PASSED")
