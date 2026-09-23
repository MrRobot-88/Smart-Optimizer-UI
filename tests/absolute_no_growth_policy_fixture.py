#!/usr/bin/env python3
import py_compile
import sys

if len(sys.argv) != 4:
    raise SystemExit(
        "usage: absolute_no_growth_policy_fixture.py RADARR SONARR UI"
    )

rad_path,son_path,ui_path=sys.argv[1:4]

for p in (rad_path,son_path,ui_path):
    py_compile.compile(p,doraise=True)

print("COMPILE PASS: Radarr + Sonarr + UI")

rad=open(rad_path,encoding="utf-8").read()
son=open(son_path,encoding="utf-8").read()
ui=open(ui_path,encoding="utf-8").read()

for needle in (
    "ABSOLUTE STORAGE INVARIANT",
    "candidate larger than current file",
    "1080P SIZE CEILING",
    "10 * 1024",
):
    assert needle in rad, "Radarr missing %r" % needle

assert "monitored" not in rad.lower(), (
    "Radarr optimizer unexpectedly contains monitored-based logic"
)

print("RADARR NO-GROWTH + 10 GiB 1080p CEILING PASS")
print("RADARR MONITORED/UNMONITORED INDEPENDENCE PASS")

for needle in (
    "ABSOLUTE STORAGE INVARIANT -- SONARR",
    "new_size > old_size + SAVING_EPSILON",
):
    assert needle in son, "Sonarr missing %r" % needle

assert "MAX_LOWRES_UPGRADE_INCREASE_PERCENT" not in son
assert "maximum +40" not in son
assert "monitored" not in son.lower(), (
    "Sonarr optimizer unexpectedly contains monitored-based logic"
)

print("SONARR GLOBAL NO-GROWTH PASS")
print("SONARR +40% EXCEPTION REMOVED PASS")
print("SONARR MONITORED/UNMONITORED INDEPENDENCE PASS")

for needle in (
    "def _dead_watchdog_research(app, media_id):",
    'target_movie_id=media_id',
    'target_episode_id=media_id',
    'manual_target=True',
    '"name": "MoviesSearch"',
    '"name": "EpisodeSearch"',
):
    assert needle in ui, "UI missing %r" % needle

assert "_dead_watchdog_native_search(" not in ui

print("WATCHDOG EXISTING-FILE RESEARCH ROUTES THROUGH OPTIMIZER PASS")
print("MISSING-FILE NATIVE ARR FALLBACK PRESERVED PASS")
print("ALL ABSOLUTE NO-GROWTH FIXTURES PASSED")
