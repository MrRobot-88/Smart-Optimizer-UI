#!/usr/bin/env python3
import ast
import importlib.util
import os
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

def uses_monitored_field(path):
    tree=ast.parse(open(path,encoding="utf-8").read())

    for node in ast.walk(tree):
        # object.get("monitored")
        if isinstance(node,ast.Call):
            fn=node.func
            if (
                isinstance(fn,ast.Attribute)
                and fn.attr=="get"
                and node.args
                and isinstance(node.args[0],ast.Constant)
                and node.args[0].value=="monitored"
            ):
                return True

        # object["monitored"]
        if isinstance(node,ast.Subscript):
            sl=node.slice
            if isinstance(sl,ast.Constant) and sl.value=="monitored":
                return True

        # monitored variable used directly
        if isinstance(node,ast.Name) and node.id.lower()=="monitored":
            return True

    return False

for needle in (
    "ABSOLUTE STORAGE INVARIANT",
    "candidate larger than current file",
    "1080P SIZE CEILING",
    "10 * 1024",
):
    assert needle in rad, "Radarr missing %r" % needle

assert not uses_monitored_field(rad_path), (
    "Radarr optimizer contains actual monitored-field logic"
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
assert not uses_monitored_field(son_path), (
    "Sonarr optimizer contains actual monitored-field logic"
)

print("SONARR GLOBAL NO-GROWTH PASS")
print("SONARR +40% EXCEPTION REMOVED PASS")
print("SONARR MONITORED/UNMONITORED INDEPENDENCE PASS")

for needle in (
    "def _dead_watchdog_research(app, media_id):",
    "target_movie_id=media_id",
    "target_episode_id=media_id",
    "manual_target=True",
    '"name": "MoviesSearch"',
    '"name": "EpisodeSearch"',
):
    assert needle in ui, "UI missing %r" % needle

assert "_dead_watchdog_native_search(" not in ui

print("WATCHDOG EXISTING-FILE RESEARCH ROUTES THROUGH OPTIMIZER PASS")
print("MISSING-FILE NATIVE ARR FALLBACK PRESERVED PASS")

# Runtime sanity: importing the optimizer modules with dummy keys must work.
os.environ.setdefault("RADARR_KEY","fixture-not-real")
os.environ.setdefault("SONARR_KEY","fixture-not-real")

def load_module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

rad_mod=load_module("rad_no_growth_fixture",rad_path)
son_mod=load_module("son_no_growth_fixture",son_path)

# Radarr: 23.54 GiB current -> 27.41 GiB candidate must reject.
rad_item={
    "title":"The Dark Knight Rises",
    "year":2012,
    "resolution":2160,
    "target_resolution":2160,
    "size_mib":23.54*1024,
    "dynamic_range":"HDR",
    "audio_channels":5.1,
    "atmos":False,
}
rad_release={
    "title":"The Dark Knight Rises 2012 2160p UHD BDRip HEVC HDR DTS-HD MA 5.1",
    "quality":{"quality":{"resolution":2160,"name":"Bluray-2160p"}},
    "size":int(27.41*(1024**3)),
    "seeders":10,
    "rejections":[],
    "indexer":"Fixture",
}
choice,reason=rad_mod.evaluate_release(
    rad_item,
    rad_release,
    {"attempted_releases":{}},
)
assert choice is None
assert reason=="candidate larger than current file"

# Radarr: any 1080p candidate over 10 GiB must reject even if current is larger.
rad_item_1080=dict(rad_item)
rad_item_1080.update({
    "title":"Fixture Movie",
    "year":2020,
    "resolution":1080,
    "target_resolution":1080,
    "size_mib":20*1024,
    "dynamic_range":"SDR_UNKNOWN",
})
rad_release_1080={
    "title":"Fixture Movie 2020 1080p WEB-DL x265 5.1",
    "quality":{"quality":{"resolution":1080,"name":"WEBDL-1080p"}},
    "size":int(11*(1024**3)),
    "seeders":10,
    "rejections":[],
    "indexer":"Fixture",
}
choice,reason=rad_mod.evaluate_release(
    rad_item_1080,
    rad_release_1080,
    {"attempted_releases":{}},
)
assert choice is None
assert reason=="1080p candidate above 10 GiB ceiling"

# Sonarr: even a low-res upgrade may no longer grow.
son_item={
    "resolution":720,
    "target_resolution":1080,
    "size_mib":1000.0,
    "dynamic_range":"SDR_UNKNOWN",
    "hdr":False,
    "audio_channels":2.0,
}
son_release={
    "title":"Fixture.Show.S01E01.1080p.WEB-DL.x265.Stereo",
    "quality":{"quality":{"resolution":1080,"name":"WEBDL-1080p"}},
    "size":int(1200*(1024**2)),
    "seeders":10,
    "rejections":[],
}
result=son_mod.evaluate_release(
    son_item,
    son_release,
    {"attempted_releases":{}},
)
assert result is None

print("RUNTIME NO-GROWTH CASES PASS")
print("ALL ABSOLUTE NO-GROWTH FIXTURES PASSED")
