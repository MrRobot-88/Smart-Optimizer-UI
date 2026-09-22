#!/usr/bin/env python3
import importlib.util
import os
import sys

os.environ.setdefault("RADARR_KEY", "fixture-not-a-real-key")
os.environ.setdefault("SONARR_KEY", "fixture-not-a-real-key")
os.environ.setdefault("SMART_OPTIMIZER_MANUAL_TARGET", "0")

if len(sys.argv) != 3:
    raise SystemExit(
        "usage: radarr_owned_selfheal_fixture.py RADARR_OPTIMIZER UI_SOURCE"
    )

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

rad = load("rad_selfheal_fixture", sys.argv[1])
ui = load("ui_selfheal_fixture", sys.argv[2])

# ------------------------------------------------------------
# Root-file detail correlation: subfolder theme ignored, exact
# root video byte sizes come from /filesystem.
# ------------------------------------------------------------
def detail_get(path):
    if path.startswith("/movie/"):
        return {
            "id": 1,
            "path": "/movies/Test Movie",
        }

    if path.startswith("/filesystem/mediafiles?"):
        return [
            {
                "relativePath": "New.mkv",
                "path": "/movies/Test Movie/New.mkv",
            },
            {
                "relativePath": "backdrops/theme.mp4",
                "path": "/movies/Test Movie/backdrops/theme.mp4",
            },
        ]

    if path.startswith("/filesystem?"):
        return {
            "files": [
                {
                    "name": "New.mkv",
                    "path": "/movies/Test Movie/New.mkv",
                    "size": 70,
                },
                {
                    "name": "movie.nfo",
                    "path": "/movies/Test Movie/movie.nfo",
                    "size": 5,
                },
            ]
        }

    raise AssertionError("unexpected detail path: " + path)

ui.radarr_get = detail_get
movie_path, details = ui._radarr_root_video_details(1)

assert movie_path == "/movies/Test Movie"
assert set(details) == {"New.mkv"}
assert details["New.mkv"]["size"] == 70

print(
    "FIXTURE PASS: self-heal filesystem correlation ignores "
    "subfolder theme/special videos"
)

# ------------------------------------------------------------
# Full successful self-heal:
#   two physical root videos -> rescan -> old exact file visible
#   -> recycle exact old file -> rescan -> one clean new movie.
# ------------------------------------------------------------
phase = {"value": 0}
deletes = []

physical_both = {
    "New.mkv": {
        "name": "New.mkv",
        "path": "/movies/Test Movie/New.mkv",
        "size": 70,
    },
    "Old.mkv": {
        "name": "Old.mkv",
        "path": "/movies/Test Movie/Old.mkv",
        "size": 100,
    },
}

physical_new = {
    "New.mkv": {
        "name": "New.mkv",
        "path": "/movies/Test Movie/New.mkv",
        "size": 70,
    },
}

def fake_details(movie_id):
    assert movie_id == 1
    if phase["value"] >= 2:
        return "/movies/Test Movie", dict(physical_new)
    return "/movies/Test Movie", dict(physical_both)

def fake_rescan(movie_id):
    assert movie_id == 1
    phase["value"] += 1

def fake_get(path):
    if path == "/config/mediamanagement":
        return {
            "recycleBin": "/recycle/radarr"
        }

    if path.startswith("/moviefile?movieId="):
        if phase["value"] >= 2:
            return [
                {
                    "id": 88,
                    "relativePath": "New.mkv",
                    "size": 70,
                }
            ]

        return [
            {
                "id": 77,
                "relativePath": "New.mkv",
                "size": 70,
            },
            {
                "id": 55,
                "relativePath": "Old.mkv",
                "size": 100,
            },
        ]

    raise AssertionError("unexpected self-heal GET: " + path)

def fake_request(path, method="GET", payload=None):
    if method == "DELETE":
        deletes.append(path)
        return {}
    raise AssertionError(
        "unexpected self-heal request: %s %s" % (method, path)
    )

ui._radarr_root_video_details = fake_details
ui._radarr_rescan_movie = fake_rescan
ui.radarr_get = fake_get
ui.radarr_request = fake_request

healed, state = ui.radarr_self_heal_owned_old_copy(
    1,
    {
        "old_size": 100,
        "old_relative_path": "Old.mkv",
    },
    expected_new_file_id=77,
    expected_new_size=70,
    expected_new_relative_path="New.mkv",
)

assert healed is True, state
assert deletes == ["/moviefile/55"]
assert state["reason"] == "self-healed"
assert state["new_file_id"] == 88
assert state["recycled_old_file_id"] == 55

print(
    "FIXTURE PASS: exact optimizer-owned old copy is recycled "
    "and clean new movie is re-verified"
)

# ------------------------------------------------------------
# No recycle bin -> absolutely no delete.
# ------------------------------------------------------------
phase["value"] = 0
deletes[:] = []

def no_recycle_get(path):
    if path == "/config/mediamanagement":
        return {
            "recycleBin": ""
        }

    raise AssertionError("unexpected no-recycle GET: " + path)

ui._radarr_root_video_details = lambda movie_id: (
    "/movies/Test Movie",
    dict(physical_both),
)
ui.radarr_get = no_recycle_get
ui.radarr_request = fake_request

healed, state = ui.radarr_self_heal_owned_old_copy(
    1,
    {
        "old_size": 100,
        "old_relative_path": "Old.mkv",
    },
    expected_new_file_id=77,
    expected_new_size=70,
    expected_new_relative_path="New.mkv",
)

assert healed is False
assert state["reason"] == "Radarr recycle bin is not configured"
assert deletes == []

print(
    "FIXTURE PASS: self-heal fails closed when Radarr recycle "
    "bin is unavailable"
)

# ------------------------------------------------------------
# Wrong extra path/size -> no delete.
# ------------------------------------------------------------
deletes[:] = []

ui._radarr_root_video_details = lambda movie_id: (
    "/movies/Test Movie",
    {
        "New.mkv": {
            "name": "New.mkv",
            "path": "/movies/Test Movie/New.mkv",
            "size": 70,
        },
        "SomeoneElse.mkv": {
            "name": "SomeoneElse.mkv",
            "path": "/movies/Test Movie/SomeoneElse.mkv",
            "size": 99,
        },
    },
)

healed, state = ui.radarr_self_heal_owned_old_copy(
    1,
    {
        "old_size": 100,
        "old_relative_path": "Old.mkv",
    },
    expected_new_file_id=77,
    expected_new_size=70,
    expected_new_relative_path="New.mkv",
)

assert healed is False
assert (
    state["reason"]
    == "competing root movie is not exact old transaction file"
)
assert deletes == []

print(
    "FIXTURE PASS: ambiguous/non-owned extra root movie is "
    "never deleted"
)

# ------------------------------------------------------------
# Old transaction without persisted path -> no self-heal.
# ------------------------------------------------------------
healed, state = ui.radarr_self_heal_owned_old_copy(
    1,
    {
        "old_size": 100,
    },
    expected_new_file_id=77,
    expected_new_size=70,
    expected_new_relative_path="New.mkv",
)

assert healed is False
assert state["reason"] == "insufficient exact transaction identity"

print(
    "FIXTURE PASS: legacy transaction without exact old path "
    "fails closed"
)

print("ALL RADARR OPTIMIZER-OWNED SELF-HEAL FIXTURES PASSED")
