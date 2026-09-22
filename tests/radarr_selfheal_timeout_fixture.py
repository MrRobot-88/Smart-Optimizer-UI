#!/usr/bin/env python3
import importlib.util
import os
import sys

os.environ.setdefault("RADARR_KEY", "fixture-not-a-real-key")
os.environ.setdefault("SONARR_KEY", "fixture-not-a-real-key")
os.environ.setdefault("SMART_OPTIMIZER_MANUAL_TARGET", "0")
os.environ.setdefault("SMART_OPTIMIZER_RECYCLE_SETTLE_SECONDS", "30")

if len(sys.argv) != 2:
    raise SystemExit(
        "usage: radarr_selfheal_timeout_fixture.py UI_SOURCE"
    )

def load(name, path):
    spec = importlib.util.spec_from_file_location(
        name,
        path
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

ui = load(
    "ui_selfheal_timeout_fixture",
    sys.argv[1]
)

phase = {
    "deleted": False,
    "rescan_count": 0,
}

delete_calls = []

physical_both = {
    "New.mkv": {
        "name": "New.mkv",
        "path": "/movies/Test/New.mkv",
        "size": 70,
    },
    "Old.mkv": {
        "name": "Old.mkv",
        "path": "/movies/Test/Old.mkv",
        "size": 100,
    },
}

physical_new = {
    "New.mkv": {
        "name": "New.mkv",
        "path": "/movies/Test/New.mkv",
        "size": 70,
    },
}

def fake_details(movie_id):
    assert movie_id == 1

    if phase["deleted"]:
        return "/movies/Test", dict(physical_new)

    return "/movies/Test", dict(physical_both)

def fake_rescan(movie_id):
    assert movie_id == 1
    phase["rescan_count"] += 1

def fake_get(path):
    if path == "/config/mediamanagement":
        return {
            "recycleBin": "/recycle"
        }

    if path.startswith("/moviefile?movieId="):
        if phase["deleted"]:
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

    raise AssertionError(
        "unexpected GET: " + path
    )

def fake_request(path, method="GET", payload=None):
    assert path == "/moviefile/55"
    assert method == "DELETE"

    delete_calls.append(path)

    # Reproduce the real NAS behavior:
    # client reports timeout, but Radarr continues the recycle move.
    phase["deleted"] = True

    raise TimeoutError("timed out")

ui._radarr_root_video_details = fake_details
ui._radarr_rescan_movie = fake_rescan
ui.radarr_get = fake_get
ui.radarr_request = fake_request

healed, state = (
    ui.radarr_self_heal_owned_old_copy(
        1,
        {
            "old_size": 100,
            "old_relative_path": "Old.mkv",
        },
        expected_new_file_id=77,
        expected_new_size=70,
        expected_new_relative_path="New.mkv",
    )
)

assert healed is True, state
assert delete_calls == ["/moviefile/55"]
assert state["reason"] == "self-healed"
assert state["delete_request_error"] == "timed out"
assert state["new_file_id"] == 88
assert phase["rescan_count"] >= 2

print(
    "FIXTURE PASS: HTTP DELETE timeout is treated as unknown "
    "outcome, not automatic failure"
)

print(
    "FIXTURE PASS: no second DELETE is issued; filesystem proof "
    "allows safe completion"
)

print(
    "ALL RADARR SELF-HEAL TIMEOUT FIXTURES PASSED"
)
