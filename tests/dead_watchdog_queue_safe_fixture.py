#!/usr/bin/env python3
import importlib.util
import py_compile
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: dead_watchdog_queue_safe_fixture.py UI_PATH")

ui_path=sys.argv[1]
py_compile.compile(ui_path,doraise=True)
print("COMPILE PASS")

spec=importlib.util.spec_from_file_location(
    "queue_safe_watchdog_fixture",
    ui_path,
)
ui=importlib.util.module_from_spec(spec)
spec.loader.exec_module(ui)

# Deterministic clock.
clock={"now":1000.0}
ui.time.time=lambda: clock["now"]

def status(state="Downloading", **kw):
    base={
        "state":state,
        "progress":0,
        "total_done":0,
        "num_seeds":0,
        "num_peers":0,
        "download_payload_rate":0,
    }
    base.update(kw)
    return base

h="A"*40

ui._dead_watchdog_zero_since.clear()

# First active-zero observation starts timer, never deletes immediately.
assert ui._dead_watchdog_is_strict_dead(status(),h,300) is False

clock["now"]=1299.0
assert ui._dead_watchdog_is_strict_dead(status(),h,300) is False

clock["now"]=1300.0
assert ui._dead_watchdog_is_strict_dead(status(),h,300) is True

# Paused/queued NEVER count and reset an existing timer.
clock["now"]=1301.0
assert ui._dead_watchdog_is_strict_dead(status("Paused"),h,300) is False
assert h not in ui._dead_watchdog_zero_since

clock["now"]=5000.0
assert ui._dead_watchdog_is_strict_dead(status("Queued"),h,300) is False
assert h not in ui._dead_watchdog_zero_since

# When Deluge later activates it, a fresh 5-minute timer begins then.
clock["now"]=6000.0
assert ui._dead_watchdog_is_strict_dead(status("Downloading"),h,300) is False
clock["now"]=6299.0
assert ui._dead_watchdog_is_strict_dead(status("Downloading"),h,300) is False
clock["now"]=6300.0
assert ui._dead_watchdog_is_strict_dead(status("Downloading"),h,300) is True

# Any evidence of life resets timer.
clock["now"]=6301.0
assert ui._dead_watchdog_is_strict_dead(
    status("Downloading",num_peers=1),
    h,
    300,
) is False
assert h not in ui._dead_watchdog_zero_since

clock["now"]=7000.0
assert ui._dead_watchdog_is_strict_dead(
    status("Downloading",total_done=1),
    h,
    300,
) is False

clock["now"]=8000.0
assert ui._dead_watchdog_is_strict_dead(
    status("Downloading",download_payload_rate=1),
    h,
    300,
) is False

print("PAUSED/QUEUED EXEMPTION PASS")
print("FRESH 5-MIN TIMER ON ACTIVATION PASS")
print("ACTIVITY RESET PASS")
print("ALL QUEUE-SAFE WATCHDOG FIXTURES PASSED")
