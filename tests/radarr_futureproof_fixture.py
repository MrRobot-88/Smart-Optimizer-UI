#!/usr/bin/env python3
import importlib.util
import sys
import os

if len(sys.argv) != 3:
    raise SystemExit("usage: fixture.py UI_PATH RADARR_PATH")

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

os.environ.setdefault("RADARR_KEY", "fixture-not-a-real-key")
ui = load("smart_optimizer_ui_fixture", sys.argv[1])
rad = load("radarr_optimizer_fixture", sys.argv[2])

safe_cases = {
    "Not an upgrade for existing movie file.": "native_quality_upgrade",
    "Not an upgrade for existing movie file(s).": "native_quality_upgrade",
    "Not a quality upgrade for existing movie file. Existing quality: Remux-2160p. New Quality: Bluray-2160p.": "native_quality_upgrade",
    "Not a quality revision upgrade for existing movie file(s)": "native_revision_upgrade",
    "Existing file meets cutoff: Remux-2160p": "native_cutoff",
    "Quality for existing file on disk is of equal or higher preference: Remux-2160p": "native_quality_preference",
}
for reason, expected in safe_cases.items():
    got, raw = ui.classify_radarr_import_rejection({"reason": reason})
    assert got == expected, (reason, expected, got, raw)
    ok, accepted, blocked = ui.radarr_rejections_are_optimizer_safe([{"reason": reason}])
    assert ok and expected in accepted and not blocked, (reason, accepted, blocked)
print("FIXTURE PASS: rejection classifier")

unsafe_cases = [
    "Not a Custom Format upgrade for existing movie file(s).",
    "English is wanted, but found Danish",
    "Unable to determine if file is a sample",
    "Movie does not match",
    "No audio tracks detected",
]
for reason in unsafe_cases:
    got, _ = ui.classify_radarr_import_rejection({"reason": reason})
    ok, _, blocked = ui.radarr_rejections_are_optimizer_safe([{"reason": reason}])
    assert not got.startswith("native_") and not ok and blocked, (reason, got, ok, blocked)
print("FIXTURE PASS: unsafe/unknown rejection families fail closed")

unknown = "Radarr future rejection wording we have never seen before"
got, _ = ui.classify_radarr_import_rejection({"reason": unknown})
ok, _, blocked = ui.radarr_rejections_are_optimizer_safe([{"reason": unknown}])
assert got == "unknown" and not ok and blocked, (got, ok, blocked)
print("FIXTURE PASS: unknown wording fails closed")

pending = {
    "4986": {
        "download_id": "3317865974C20C6110E9260FD5C74C2A91B7C02B",
        "queue_id": 368786392,
    }
}
row = {
    "movieId": 4986,
    "id": 368786392,
    "downloadId": "3317865974c20c6110e9260fd5c74c2a91b7c02b",
}
assert ui._optimizer_owned_queue_row(row, pending)
bad = dict(row); bad["id"] = 368786393
assert not ui._optimizer_owned_queue_row(bad, pending)
bad = dict(row); bad["downloadId"] = "WRONG"
assert not ui._optimizer_owned_queue_row(bad, pending)
print("FIXTURE PASS: exact ownership")

ui_calls = []
def fake_ui_get(path):
    ui_calls.append(path)
    if "page=1" in path:
        return {"records": [{"id": i} for i in range(1, 101)], "totalRecords": 137}
    if "page=2" in path:
        return {"records": [{"id": i} for i in range(101, 138)], "totalRecords": 137}
    raise AssertionError(path)
ui.radarr_get = fake_ui_get
rows = ui.queue_records()
assert len(rows) == 137 and len(ui_calls) == 2 and rows[-1]["id"] == 137
print("FIXTURE PASS: UI queue pagination >100")

rad_calls = []
def fake_rad_get(path):
    rad_calls.append(path)
    if "page=1" in path:
        return {"records": [{"id": i, "movieId": 1} for i in range(1, 101)], "totalRecords": 137}
    if "page=2" in path:
        return {"records": [{"id": i, "movieId": 1} for i in range(101, 138)], "totalRecords": 137}
    raise AssertionError(path)
rad.get = fake_rad_get
rows = rad.all_radarr_queue_records()
assert len(rows) == 137 and len(rad_calls) == 2 and rows[-1]["id"] == 137
print("FIXTURE PASS: Radarr queue pagination >100")

rad.release_infohash = lambda release: None
rad.all_radarr_queue_records = lambda: [
    {"id": 10, "movieId": 4986, "downloadId": "OLD"},
    {"id": 11, "movieId": 4986, "downloadId": "NEW"},
]
rad.time.sleep = lambda _seconds: None
owned = rad.bind_grabbed_download(
    4986,
    {"title": "Arcadian"},
    preexisting_queue_ids=[10],
    attempts=1,
    delay=0,
)
assert owned == {"download_id": "NEW", "queue_id": 11}, owned
assert rad.bind_grabbed_download(
    4986,
    {"title": "Arcadian"},
    preexisting_queue_ids=None,
    attempts=1,
    delay=0,
) is None
print("FIXTURE PASS: pre-grab ownership binding")

print("ALL FUTURE-PROOF FIXTURES PASSED")
