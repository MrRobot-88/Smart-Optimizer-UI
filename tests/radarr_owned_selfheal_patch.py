#!/usr/bin/env python3
import hashlib
import sys
from pathlib import Path

if len(sys.argv) != 3:
    raise SystemExit(
        "usage: radarr_owned_selfheal_patch.py RADARR_OPTIMIZER UI_SOURCE"
    )

rad_path = Path(sys.argv[1])
ui_path = Path(sys.argv[2])

rad = rad_path.read_text(encoding="utf-8")
ui = ui_path.read_text(encoding="utf-8")

EXPECTED_RAD = "d5004fdaf4c6dd1aa7e4f04be29c94268f7d2440a1f4bf21566dbe72a44b6882"
EXPECTED_UI = "3d79fcff9a409d9468a8f11f606fb92e696b6d12cf2bff63c80bc08f92a69d9a"

def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

if sha(rad) != EXPECTED_RAD:
    raise SystemExit("STOP: Radarr source hash mismatch: " + sha(rad))
if sha(ui) != EXPECTED_UI:
    raise SystemExit("STOP: UI source hash mismatch: " + sha(ui))

def once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise SystemExit(
            "STOP: %s: expected exactly 1 match, found %d" % (label, count)
        )
    return text.replace(old, new, 1)

# Persist exact old-file identity before every new optimizer-owned grab.
rad = once(
    rad,
    '''            old_file_id = current_file.get("id")
            old_file_size = int(current_file.get("size") or 0)

            if not old_file_id or old_file_size <= 0:
''',
    '''            old_file_id = current_file.get("id")
            old_file_size = int(current_file.get("size") or 0)
            old_relative_path = str(
                current_file.get("relativePath") or ""
            ).strip()

            if (
                not old_file_id
                or old_file_size <= 0
                or not old_relative_path
            ):
''',
    "capture exact old Radarr relative path",
)

rad = once(
    rad,
    '''                "old_file_id": int(old_file_id),
                "old_size": int(old_file_size),
                "approved_title": approved_title,
''',
    '''                "old_file_id": int(old_file_id),
                "old_size": int(old_file_size),
                "old_relative_path": old_relative_path,
                "approved_title": approved_title,
''',
    "persist old Radarr relative path",
)

UI_SELFHEAL_HELPERS = r'''def _radarr_root_video_details(movie_id):
    """
    Return physical root-level video files with exact byte sizes.

    /filesystem/mediafiles identifies videos. /filesystem supplies the exact
    root-file sizes. Subfolder extras are ignored.
    """
    movie = radarr_get("/movie/%d" % int(movie_id)) or {}
    movie_path = str(movie.get("path") or "").strip()

    if not movie_path:
        raise RuntimeError("movie path unavailable")

    media = radarr_get(
        "/filesystem/mediafiles?path=%s"
        % urllib.parse.quote(movie_path, safe="")
    ) or []

    listing = radarr_get(
        "/filesystem?path=%s&includeFiles=true"
        "&allowFoldersWithoutTrailingSlashes=true"
        % urllib.parse.quote(movie_path, safe="")
    ) or {}

    root_video_names = set()

    for item in media:
        name = _radarr_root_video_name(item.get("relativePath"))
        if name:
            root_video_names.add(name)

    root_files = {}

    for item in listing.get("files") or []:
        name = str(item.get("name") or "").strip()

        if name in root_video_names:
            root_files[name] = {
                "name": name,
                "path": str(item.get("path") or ""),
                "size": int(item.get("size") or 0),
            }

    # Fail closed if the two Radarr filesystem views disagree.
    if set(root_files) != root_video_names:
        raise RuntimeError(
            "filesystem listing/mediafiles disagreement"
        )

    return movie_path, root_files


def _radarr_wait_command(command_id, timeout=120):
    deadline = time.time() + int(timeout)

    while time.time() < deadline:
        result = radarr_get(
            "/command/%d" % int(command_id)
        ) or {}

        status = str(
            result.get("status") or ""
        ).lower()

        if status == "completed":
            return True

        if status in ("failed", "aborted"):
            raise RuntimeError(
                "Radarr command %d ended %s"
                % (int(command_id), status)
            )

        time.sleep(2)

    raise RuntimeError(
        "Radarr command %d timed out"
        % int(command_id)
    )


def _radarr_rescan_movie(movie_id):
    result = radarr_request(
        "/command",
        method="POST",
        payload={
            "name": "RescanMovie",
            "movieId": int(movie_id),
        },
    ) or {}

    command_id = int(result.get("id") or 0)

    if not command_id:
        raise RuntimeError(
            "Radarr did not return a RescanMovie command id"
        )

    _radarr_wait_command(command_id)


def radarr_self_heal_owned_old_copy(
    movie_id,
    txn,
    expected_new_file_id,
    expected_new_size,
    expected_new_relative_path,
):
    """
    Self-heal ONLY an optimizer-owned duplicate after a verified import.

    Safety proof required before any delete:
      * transaction has exact old path + exact old byte size
      * expected new file is physically present at exact path + size
      * exactly one extra root video exists
      * that extra is the exact old transaction path + exact old byte size
      * Radarr recycle bin is configured
      * a rescan exposes exactly one registered file with old exact byte size

    The old file is removed through Radarr, so configured Radarr recycling
    remains authoritative. Any ambiguity fails closed without deleting.
    """
    old_size = int(txn.get("old_size") or 0)
    old_relative_path = str(
        txn.get("old_relative_path") or ""
    ).strip()

    expected_new_file_id = int(
        expected_new_file_id or 0
    )
    expected_new_size = int(
        expected_new_size or 0
    )
    expected_new_relative_path = str(
        expected_new_relative_path or ""
    ).strip()

    if (
        old_size <= 0
        or not old_relative_path
        or expected_new_file_id <= 0
        or expected_new_size <= 0
        or not expected_new_relative_path
        or old_size == expected_new_size
        or old_relative_path == expected_new_relative_path
    ):
        return False, {
            "reason": "insufficient exact transaction identity"
        }

    try:
        movie_path, physical = _radarr_root_video_details(
            movie_id
        )
    except Exception as exc:
        return False, {
            "reason": "physical root inspection failed",
            "error": str(exc),
        }

    expected_new = physical.get(
        expected_new_relative_path
    )

    if (
        not expected_new
        or int(expected_new.get("size") or 0)
        != expected_new_size
    ):
        return False, {
            "reason": "expected new physical movie is not exact",
            "movie_path": movie_path,
            "physical_root": physical,
        }

    extras = [
        item
        for name, item in physical.items()
        if name != expected_new_relative_path
    ]

    if len(extras) != 1:
        return False, {
            "reason": "expected exactly one competing root movie",
            "physical_root": physical,
        }

    extra = extras[0]

    if (
        str(extra.get("name") or "")
        != old_relative_path
        or int(extra.get("size") or 0)
        != old_size
    ):
        return False, {
            "reason": "competing root movie is not exact old transaction file",
            "expected_old_relative_path": old_relative_path,
            "expected_old_size": old_size,
            "actual_extra": extra,
        }

    try:
        media_cfg = radarr_get(
            "/config/mediamanagement"
        ) or {}
    except Exception as exc:
        return False, {
            "reason": "cannot verify Radarr recycle bin",
            "error": str(exc),
        }

    recycle_bin = str(
        media_cfg.get("recycleBin") or ""
    ).strip()

    if not recycle_bin:
        return False, {
            "reason": "Radarr recycle bin is not configured"
        }

    # Make the exact physical old copy visible to Radarr's movie-file DB.
    try:
        _radarr_rescan_movie(movie_id)
    except Exception as exc:
        return False, {
            "reason": "pre-heal Radarr rescan failed",
            "error": str(exc),
        }

    try:
        registered = radarr_get(
            "/moviefile?movieId=%d"
            % int(movie_id)
        ) or []
    except Exception as exc:
        return False, {
            "reason": "cannot inspect registered files after rescan",
            "error": str(exc),
        }

    old_matches = [
        item
        for item in registered
        if int(item.get("size") or 0) == old_size
    ]

    if len(old_matches) != 1:
        return False, {
            "reason": "old exact byte-size file is not uniquely registered",
            "registered": [
                {
                    "id": int(x.get("id") or 0),
                    "relativePath": str(
                        x.get("relativePath") or ""
                    ),
                    "size": int(x.get("size") or 0),
                }
                for x in registered
            ],
        }

    old_registered_id = int(
        old_matches[0].get("id") or 0
    )

    if not old_registered_id:
        return False, {
            "reason": "old registered file id unavailable"
        }

    # Revalidate that the expected new physical file still exists immediately
    # before asking Radarr to recycle the exact old registered file.
    try:
        _, physical = _radarr_root_video_details(
            movie_id
        )
    except Exception as exc:
        return False, {
            "reason": "final pre-delete physical revalidation failed",
            "error": str(exc),
        }

    new_physical = physical.get(
        expected_new_relative_path
    )

    old_physical = physical.get(
        old_relative_path
    )

    if (
        not new_physical
        or int(new_physical.get("size") or 0)
        != expected_new_size
        or not old_physical
        or int(old_physical.get("size") or 0)
        != old_size
        or len(physical) != 2
    ):
        return False, {
            "reason": "physical state changed before recycle",
            "physical_root": physical,
        }

    try:
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
    except Exception as exc:
        return False, {
            "reason": "post-heal Radarr rescan failed",
            "error": str(exc),
            "old_registered_id": old_registered_id,
        }

    try:
        registered = radarr_get(
            "/moviefile?movieId=%d"
            % int(movie_id)
        ) or []

        _, physical = _radarr_root_video_details(
            movie_id
        )
    except Exception as exc:
        return False, {
            "reason": "post-heal verification failed",
            "error": str(exc),
            "old_registered_id": old_registered_id,
        }

    final_registered = [
        item
        for item in registered
        if (
            str(item.get("relativePath") or "")
            == expected_new_relative_path
            and int(item.get("size") or 0)
            == expected_new_size
        )
    ]

    final_physical = physical.get(
        expected_new_relative_path
    )

    if (
        len(registered) != 1
        or len(final_registered) != 1
        or len(physical) != 1
        or not final_physical
        or int(final_physical.get("size") or 0)
        != expected_new_size
    ):
        return False, {
            "reason": "self-heal did not reach one-clean-movie invariant",
            "registered": [
                {
                    "id": int(x.get("id") or 0),
                    "relativePath": str(
                        x.get("relativePath") or ""
                    ),
                    "size": int(x.get("size") or 0),
                }
                for x in registered
            ],
            "physical_root": physical,
            "old_registered_id": old_registered_id,
        }

    return True, {
        "reason": "self-healed",
        "recycle_bin": recycle_bin,
        "recycled_old_file_id": old_registered_id,
        "new_file_id": int(
            final_registered[0].get("id") or 0
        ),
        "new_relative_path": expected_new_relative_path,
        "new_size": expected_new_size,
    }


'''

ui = once(
    ui,
    'def optimizer_import_worker():\n',
    UI_SELFHEAL_HELPERS + 'def optimizer_import_worker():\n',
    "insert optimizer-owned self-heal helpers",
)

# Migrate older still-valid pending transactions when their original old
# movie-file ID is still present. This gives already-grabbed clean items the
# same future safety without guessing.
ui = once(
    ui,
    '''                            if int(old_file.get("size") or 0) != old_size:
                                print(
                                    "[radarr-import] old file size changed; refusing:",
                                    movie_id
                                )
                                continue

                            # The import worker must see one and only one
''',
    '''                            if int(old_file.get("size") or 0) != old_size:
                                print(
                                    "[radarr-import] old file size changed; refusing:",
                                    movie_id
                                )
                                continue

                            if not str(
                                txn.get("old_relative_path") or ""
                            ).strip():
                                old_relative_path = str(
                                    old_file.get("relativePath") or ""
                                ).strip()

                                if old_relative_path:
                                    txn["old_relative_path"] = (
                                        old_relative_path
                                    )
                                    changed = True

                            # The import worker must see one and only one
''',
    "migrate pending exact old path",
)

ui = once(
    ui,
    '''                            if not folder_clean:
                                txn["status"] = "folder_dirty"
                                txn["folder_issue"] = folder_state
                                txn["verified_new_file_id"] = new_file_id
                                txn["verified_new_size"] = new_size
                                changed = True
                                print(
                                    "[radarr-import] FOLDER DIRTY after import; "
                                    "transaction retained:",
                                    movie_id,
                                    json.dumps(
                                        folder_state,
                                        ensure_ascii=False
                                    )
                                )
                                continue

                            txn.pop("folder_issue", None)
''',
    '''                            if not folder_clean:
                                new_relative_path = str(
                                    current.get("relativePath") or ""
                                ).strip()

                                healed, heal_state = (
                                    radarr_self_heal_owned_old_copy(
                                        movie_id,
                                        txn,
                                        expected_new_file_id=new_file_id,
                                        expected_new_size=new_size,
                                        expected_new_relative_path=(
                                            new_relative_path
                                        ),
                                    )
                                )

                                if healed:
                                    new_file_id = int(
                                        heal_state.get("new_file_id")
                                        or new_file_id
                                    )
                                    txn["self_healed_at"] = int(
                                        time.time()
                                    )
                                    txn["self_heal"] = heal_state
                                    changed = True

                                    print(
                                        "[radarr-import] SELF-HEAL SUCCESS:",
                                        movie_id,
                                        json.dumps(
                                            heal_state,
                                            ensure_ascii=False
                                        )
                                    )
                                else:
                                    txn["status"] = "folder_dirty"
                                    txn["folder_issue"] = folder_state
                                    txn["self_heal"] = heal_state
                                    txn["verified_new_file_id"] = (
                                        new_file_id
                                    )
                                    txn["verified_new_size"] = new_size
                                    changed = True

                                    print(
                                        "[radarr-import] FOLDER DIRTY after import; "
                                        "self-heal refused; transaction retained:",
                                        movie_id,
                                        json.dumps(
                                            heal_state,
                                            ensure_ascii=False
                                        )
                                    )
                                    continue

                            txn.pop("folder_issue", None)
''',
    "activate post-import optimizer-owned self-heal",
)

rad_path.write_text(rad, encoding="utf-8")
ui_path.write_text(ui, encoding="utf-8")

print("PATCH COMPLETE")
print("RADARR SHA256", sha(rad))
print("UI SHA256", sha(ui))
