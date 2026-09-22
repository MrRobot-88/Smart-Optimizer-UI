#!/usr/bin/env python3
import hashlib
import sys
from pathlib import Path

if len(sys.argv) != 3:
    raise SystemExit(
        "usage: radarr_selfheal_timeout_patch.py RADARR_OPTIMIZER UI_SOURCE"
    )

rad_path = Path(sys.argv[1])
ui_path = Path(sys.argv[2])

rad = rad_path.read_text(encoding="utf-8")
ui = ui_path.read_text(encoding="utf-8")

EXPECTED_RAD = "f31394c33f66387ae4e0b58a8f11cc348eb4ff805734685bfea066062baadc13"
EXPECTED_UI = "af29383d3c3109698c70ed0986397bea7c6a401e67c767ef01df12ae09ac25a5"

def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

if sha(rad) != EXPECTED_RAD:
    raise SystemExit(
        "STOP: Radarr source hash mismatch: " + sha(rad)
    )

if sha(ui) != EXPECTED_UI:
    raise SystemExit(
        "STOP: UI source hash mismatch: " + sha(ui)
    )

old = '''    try:
        radarr_request(
            "/moviefile/%d"
            % old_registered_id,
            method="DELETE"
        )
    except Exception as exc:
        return False, {
            "reason": "Radarr failed to recycle exact old file",
            "error": str(exc),
            "old_registered_id": old_registered_id,
        }

    try:
        _radarr_rescan_movie(movie_id)
'''

new = '''    delete_request_error = None

    try:
        radarr_request(
            "/moviefile/%d"
            % old_registered_id,
            method="DELETE"
        )
    except Exception as exc:
        # A Radarr DELETE can time out at the HTTP client while Radarr keeps
        # moving a large file into its recycle bin. Do NOT retry the DELETE.
        # Treat the outcome as unknown and prove the filesystem result below.
        delete_request_error = str(exc)

        print(
            "[radarr-import] recycle request returned uncertain outcome; "
            "waiting for exact filesystem proof:",
            movie_id,
            delete_request_error
        )

    recycle_wait_seconds = int(
        os.environ.get(
            "SMART_OPTIMIZER_RECYCLE_SETTLE_SECONDS",
            "1800"
        )
    )

    recycle_deadline = (
        time.time()
        + max(30, recycle_wait_seconds)
    )

    while True:
        try:
            _, physical = _radarr_root_video_details(
                movie_id
            )
        except Exception as exc:
            return False, {
                "reason": "recycle outcome inspection failed",
                "error": str(exc),
                "delete_request_error": delete_request_error,
                "old_registered_id": old_registered_id,
            }

        new_physical = physical.get(
            expected_new_relative_path
        )

        old_physical = physical.get(
            old_relative_path
        )

        new_exact = (
            bool(new_physical)
            and int(new_physical.get("size") or 0)
            == expected_new_size
        )

        if not new_exact:
            return False, {
                "reason": "expected new movie changed during recycle",
                "physical_root": physical,
                "delete_request_error": delete_request_error,
                "old_registered_id": old_registered_id,
            }

        if (
            old_physical is None
            and len(physical) == 1
        ):
            break

        if time.time() >= recycle_deadline:
            return False, {
                "reason": "exact old file did not leave movie folder before recycle timeout",
                "physical_root": physical,
                "delete_request_error": delete_request_error,
                "old_registered_id": old_registered_id,
                "wait_seconds": max(
                    30,
                    recycle_wait_seconds
                ),
            }

        time.sleep(5)

    try:
        _radarr_rescan_movie(movie_id)
'''

count = ui.count(old)

if count != 1:
    raise SystemExit(
        "STOP: expected exactly one self-heal delete block; found %d"
        % count
    )

ui = ui.replace(old, new, 1)

old_return = '''    return True, {
        "reason": "self-healed",
        "recycle_bin": recycle_bin,
        "recycled_old_file_id": old_registered_id,
        "new_file_id": int(
'''

new_return = '''    return True, {
        "reason": "self-healed",
        "recycle_bin": recycle_bin,
        "recycled_old_file_id": old_registered_id,
        "delete_request_error": delete_request_error,
        "new_file_id": int(
'''

count = ui.count(old_return)

if count != 1:
    raise SystemExit(
        "STOP: expected exactly one self-heal success block; found %d"
        % count
    )

ui = ui.replace(
    old_return,
    new_return,
    1
)

ui_path.write_text(
    ui,
    encoding="utf-8"
)

print("PATCH COMPLETE")
print("RADARR SHA256", sha(rad))
print("UI SHA256", sha(ui))
