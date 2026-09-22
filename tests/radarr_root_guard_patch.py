#!/usr/bin/env python3
import hashlib
import sys
from pathlib import Path

if len(sys.argv) != 3:
    raise SystemExit("usage: radarr_root_guard_patch.py RADARR_OPTIMIZER UI_SOURCE")

rad_path = Path(sys.argv[1])
ui_path = Path(sys.argv[2])

rad = rad_path.read_text(encoding="utf-8")
ui = ui_path.read_text(encoding="utf-8")

EXPECTED_RAD = "2328335f24ed059836de64ef86b8bc94a3a45da21e782db791cbde71374a6a06"
EXPECTED_UI = "1eba024b955fa9bbbe19d419bb978d7a64f7d9fd1c8d9da8b8576cb61a0c669f"

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

RAD_HELPER = r'''def _root_video_name(relative_path):
    """Return a root-level video relative path, or None for subfolders."""
    value = str(relative_path or "").replace("\\", "/").strip("/")
    if not value or "/" in value:
        return None
    return value


def radarr_root_movie_file_guard(movie_id, movie_path):
    """
    Fail closed unless Radarr has exactly one registered movie file and the
    filesystem has exactly one root-level video, and both names are identical.

    Subfolder videos such as backdrops/theme.mp4 and specials/* are ignored.
    """
    try:
        registered = get(
            "/moviefile?movieId=%d" % int(movie_id)
        ) or []

        media = get(
            "/filesystem/mediafiles?path=%s"
            % urllib.parse.quote(str(movie_path or ""), safe="")
        ) or []
    except Exception as exc:
        return False, {
            "reason": "filesystem guard API error",
            "error": str(exc),
            "registered_count": None,
            "root_video_count": None,
        }

    registered_root = []
    for item in registered:
        root_name = _root_video_name(item.get("relativePath"))
        if root_name:
            registered_root.append({
                "id": int(item.get("id") or 0),
                "name": root_name,
                "size": int(item.get("size") or 0),
            })

    physical_root = []
    for item in media:
        root_name = _root_video_name(item.get("relativePath"))
        if root_name:
            physical_root.append(root_name)

    clean = (
        len(registered) == 1
        and len(registered_root) == 1
        and len(physical_root) == 1
        and registered_root[0]["name"] == physical_root[0]
    )

    return clean, {
        "reason": "clean" if clean else "competing/missing root movie file",
        "registered_count": len(registered),
        "registered_root": registered_root,
        "physical_root": physical_root,
    }


'''

rad = once(
    rad,
    'def movie_item(movie, state, queued_ids):\n',
    RAD_HELPER + 'def movie_item(movie, state, queued_ids):\n',
    "insert Radarr root-file guard helper",
)

rad = once(
    rad,
    '''    if int(movie_id) in auto_processed_movie_ids(state):
        return None

    movie_file = movie.get("movieFile") or {}
''',
    '''    if int(movie_id) in auto_processed_movie_ids(state):
        return None

    # A movie folder must be unambiguous BEFORE we spend its one automatic
    # indexer search. Theme/special videos in subfolders are intentionally
    # ignored; a second root-level movie file fails closed.
    folder_clean, folder_state = radarr_root_movie_file_guard(
        movie_id,
        movie.get("path") or ""
    )
    if not folder_clean:
        print(
            "    FOLDER GUARD BLOCK: %s -- %s"
            % (
                movie.get("title") or "Unknown movie",
                json.dumps(folder_state, ensure_ascii=False)
            ),
            flush=True
        )
        return None

    movie_file = movie.get("movieFile") or {}
''',
    "Radarr pre-search root-file guard",
)

rad = once(
    rad,
    '''            current_movie = get("/movie/%d" % movie_id)
            current_file = current_movie.get("movieFile") or {}

            old_file_id = current_file.get("id")
''',
    '''            current_movie = get("/movie/%d" % movie_id)

            # Re-check immediately before the grab. The initial folder guard
            # ran before the interactive release search; the folder may have
            # changed while that search was being evaluated.
            folder_clean, folder_state = radarr_root_movie_file_guard(
                movie_id,
                current_movie.get("path") or ""
            )
            if not folder_clean:
                print(
                    "    SKIP: folder changed after search; refusing grab: %s"
                    % json.dumps(folder_state, ensure_ascii=False),
                    flush=True
                )
                print()
                continue

            current_file = current_movie.get("movieFile") or {}

            old_file_id = current_file.get("id")
''',
    "Radarr pre-grab root-file recheck",
)

UI_HELPER = r'''def _radarr_root_video_name(relative_path):
    value = str(relative_path or "").replace("\\", "/").strip("/")
    if not value or "/" in value:
        return None
    return value


def radarr_root_movie_file_guard(movie_id, expected_file_id=None):
    """
    Verify Radarr database state against physical root-level movie videos.

    Subfolder video extras (backdrops/theme.*, specials/*, trailers/*, etc.)
    are ignored. Unknown/API-error state fails closed.
    """
    try:
        movie = radarr_get("/movie/%d" % int(movie_id)) or {}
        movie_path = str(movie.get("path") or "")

        if not movie_path:
            return False, {
                "reason": "movie path unavailable"
            }

        registered = radarr_get(
            "/moviefile?movieId=%d" % int(movie_id)
        ) or []

        media = radarr_get(
            "/filesystem/mediafiles?path=%s"
            % urllib.parse.quote(movie_path, safe="")
        ) or []
    except Exception as exc:
        return False, {
            "reason": "filesystem guard API error",
            "error": str(exc),
        }

    registered_root = []
    for item in registered:
        root_name = _radarr_root_video_name(item.get("relativePath"))
        if root_name:
            registered_root.append({
                "id": int(item.get("id") or 0),
                "name": root_name,
                "size": int(item.get("size") or 0),
            })

    physical_root = []
    for item in media:
        root_name = _radarr_root_video_name(item.get("relativePath"))
        if root_name:
            physical_root.append(root_name)

    clean = (
        len(registered) == 1
        and len(registered_root) == 1
        and len(physical_root) == 1
        and registered_root[0]["name"] == physical_root[0]
    )

    if expected_file_id is not None:
        clean = (
            clean
            and registered_root[0]["id"] == int(expected_file_id)
        )

    return clean, {
        "reason": "clean" if clean else "competing/missing root movie file",
        "movie_path": movie_path,
        "registered_count": len(registered),
        "registered_root": registered_root,
        "physical_root": physical_root,
        "expected_file_id": (
            int(expected_file_id)
            if expected_file_id is not None
            else None
        ),
    }


'''

ui = once(
    ui,
    'def optimizer_import_worker():\n',
    UI_HELPER + 'def optimizer_import_worker():\n',
    "insert UI root-file guard helper",
)

ui = once(
    ui,
    '''                            if int(old_file.get("size") or 0) != old_size:
                                print(
                                    "[radarr-import] old file size changed; refusing:",
                                    movie_id
                                )
                                continue

                            items = radarr_get(
''',
    '''                            if int(old_file.get("size") or 0) != old_size:
                                print(
                                    "[radarr-import] old file size changed; refusing:",
                                    movie_id
                                )
                                continue

                            # The import worker must see one and only one
                            # physical root movie before starting an override.
                            # This prevents a hidden/orphan movie file from
                            # competing with the optimizer transaction.
                            folder_clean, folder_state = (
                                radarr_root_movie_file_guard(movie_id)
                            )

                            if not folder_clean:
                                txn["status"] = "folder_dirty"
                                txn["folder_issue"] = folder_state
                                changed = True
                                print(
                                    "[radarr-import] FOLDER DIRTY before import; "
                                    "OLD FILE KEPT:",
                                    movie_id,
                                    json.dumps(
                                        folder_state,
                                        ensure_ascii=False
                                    )
                                )
                                continue

                            items = radarr_get(
''',
    "UI pre-import root-file guard",
)

ui = once(
    ui,
    '''                            if any(
                                int(f.get("id") or 0) == old_file_id
                                for f in files
                            ):
                                print(
                                    "[radarr-import] old file still exists:",
                                    movie_id
                                )
                                continue

                            print(
                                "[radarr-import] SUCCESS:",
''',
    '''                            if any(
                                int(f.get("id") or 0) == old_file_id
                                for f in files
                            ):
                                print(
                                    "[radarr-import] old file still exists:",
                                    movie_id
                                )
                                continue

                            # Do not clear a transaction merely because the
                            # expected new DB row exists. Prove that the movie
                            # folder itself contains exactly one root video and
                            # that Radarr owns that exact replacement. This is
                            # the Gremlins/Hellboy/HTTYD safety invariant.
                            folder_clean, folder_state = (
                                radarr_root_movie_file_guard(
                                    movie_id,
                                    expected_file_id=new_file_id
                                )
                            )

                            if not folder_clean:
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

                            print(
                                "[radarr-import] SUCCESS:",
''',
    "UI post-import root-file guard",
)

rad_path.write_text(rad, encoding="utf-8")
ui_path.write_text(ui, encoding="utf-8")

print("PATCH COMPLETE")
print("RADARR SHA256", sha(rad))
print("UI SHA256", sha(ui))
