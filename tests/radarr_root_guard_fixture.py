#!/usr/bin/env python3
import importlib.util
import os
import sys

os.environ.setdefault("RADARR_KEY", "fixture-not-a-real-key")
os.environ.setdefault("SONARR_KEY", "fixture-not-a-real-key")
os.environ.setdefault("SMART_OPTIMIZER_MANUAL_TARGET", "0")

if len(sys.argv) != 3:
    raise SystemExit(
        "usage: radarr_root_guard_fixture.py RADARR_OPTIMIZER UI_SOURCE"
    )

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

rad = load("rad_root_guard_fixture", sys.argv[1])
ui = load("ui_root_guard_fixture", sys.argv[2])

def make_rad_get(registered, media, fail=False):
    def fake(path):
        if fail:
            raise RuntimeError("fixture API failure")
        if path.startswith("/moviefile?movieId="):
            return registered
        if path.startswith("/filesystem/mediafiles?path="):
            return media
        raise AssertionError("unexpected Radarr optimizer path: " + path)
    return fake

registered_one = [{
    "id": 10,
    "relativePath": "Movie (2026) Bluray-2160p.mkv",
    "size": 10 * 1024**3,
}]
media_clean_with_theme = [
    {
        "relativePath": "Movie (2026) Bluray-2160p.mkv",
        "path": "/movies/Movie/Movie (2026) Bluray-2160p.mkv",
    },
    {
        "relativePath": "backdrops/theme.mp4",
        "path": "/movies/Movie/backdrops/theme.mp4",
    },
]

rad.get = make_rad_get(
    registered_one,
    media_clean_with_theme,
)
clean, state = rad.radarr_root_movie_file_guard(
    1,
    "/movies/Movie"
)
assert clean is True, state
assert len(state["physical_root"]) == 1
print("FIXTURE PASS: Radarr guard ignores subfolder theme/special videos")

media_dirty = list(media_clean_with_theme) + [{
    "relativePath": "Old competing copy.mkv",
    "path": "/movies/Movie/Old competing copy.mkv",
}]
rad.get = make_rad_get(
    registered_one,
    media_dirty,
)
clean, state = rad.radarr_root_movie_file_guard(
    1,
    "/movies/Movie"
)
assert clean is False
assert len(state["physical_root"]) == 2
print("FIXTURE PASS: Radarr guard blocks a competing root movie file")

rad.get = make_rad_get(
    registered_one + [{
        "id": 11,
        "relativePath": "Old competing copy.mkv",
        "size": 15 * 1024**3,
    }],
    media_dirty,
)
clean, state = rad.radarr_root_movie_file_guard(
    1,
    "/movies/Movie"
)
assert clean is False
assert state["registered_count"] == 2
print("FIXTURE PASS: Radarr guard blocks multiple registered movie files")

rad.get = make_rad_get([], [], fail=True)
clean, state = rad.radarr_root_movie_file_guard(
    1,
    "/movies/Movie"
)
assert clean is False
assert state["reason"] == "filesystem guard API error"
print("FIXTURE PASS: Radarr guard fails closed on filesystem API errors")

def make_ui_get(registered, media, fail=False):
    def fake(path):
        if fail:
            raise RuntimeError("fixture API failure")
        if path.startswith("/movie/"):
            return {
                "id": 1,
                "title": "Movie",
                "path": "/movies/Movie",
            }
        if path.startswith("/moviefile?movieId="):
            return registered
        if path.startswith("/filesystem/mediafiles?path="):
            return media
        raise AssertionError("unexpected UI path: " + path)
    return fake

ui.radarr_get = make_ui_get(
    registered_one,
    media_clean_with_theme,
)
clean, state = ui.radarr_root_movie_file_guard(
    1,
    expected_file_id=10
)
assert clean is True, state
print("FIXTURE PASS: UI post-import guard accepts exact clean replacement")

clean, state = ui.radarr_root_movie_file_guard(
    1,
    expected_file_id=99
)
assert clean is False
print("FIXTURE PASS: UI post-import guard rejects wrong registered replacement")

ui.radarr_get = make_ui_get(
    registered_one,
    media_dirty,
)
clean, state = ui.radarr_root_movie_file_guard(
    1,
    expected_file_id=10
)
assert clean is False
assert len(state["physical_root"]) == 2
print("FIXTURE PASS: UI post-import guard retains transaction on dirty folder")

ui.radarr_get = make_ui_get([], [], fail=True)
clean, state = ui.radarr_root_movie_file_guard(
    1,
    expected_file_id=10
)
assert clean is False
assert state["reason"] == "filesystem guard API error"
print("FIXTURE PASS: UI post-import guard fails closed on API errors")

print("ALL RADARR ROOT-FILE GUARD FIXTURES PASSED")
