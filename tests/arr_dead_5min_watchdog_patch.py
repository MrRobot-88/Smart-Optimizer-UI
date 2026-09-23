#!/usr/bin/env python3
import hashlib
import re
import sys
from pathlib import Path

if len(sys.argv) != 2:
    raise SystemExit("usage: arr_dead_5min_watchdog_patch.py UI_PATH")

path=Path(sys.argv[1])
text=path.read_text(encoding="utf-8")

EXPECTED="f35d2c9192465f2360712fe185411b10eeca02bdc6c5c5c4768bd7941329d559"

def sha(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

actual=sha(text)
if actual != EXPECTED:
    raise SystemExit("STOP: UI source hash mismatch: "+actual)

def once(text,old,new,label):
    n=text.count(old)
    if n != 1:
        raise SystemExit(
            "STOP: %s: expected exactly 1 match, found %d"
            % (label,n)
        )
    return text.replace(old,new,1)

# ------------------------------------------------------------------
# Sonarr needs a generic request helper so we can DELETE one exact queue
# row and POST an exact EpisodeSearch command.
# ------------------------------------------------------------------
text=once(
    text,
    '''def sonarr_get(path):
    cfg = connection("sonarr")
    if not cfg["api_key"]:
        raise RuntimeError("Sonarr API key is not configured")
    req = urllib.request.Request(
        connection_url("sonarr") + "/api/v3" + path,
        headers={"X-Api-Key": cfg["api_key"], "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        raw = response.read().decode("utf-8")
        return json.loads(raw) if raw else {}


''',
    '''def sonarr_request(path, method="GET", payload=None):
    cfg = connection("sonarr")
    if not cfg["api_key"]:
        raise RuntimeError("Sonarr API key is not configured")

    data = None if payload is None else json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        connection_url("sonarr") + "/api/v3" + path,
        data=data,
        method=method,
        headers={
            "X-Api-Key": cfg["api_key"],
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )

    with urllib.request.urlopen(req, timeout=30) as response:
        raw = response.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def sonarr_get(path):
    return sonarr_request(path)


''',
    "Sonarr request helper",
)

# ------------------------------------------------------------------
# Extend UI optimizer launcher with exact Sonarr episode targeting. This is
# used ONLY to retry the same failed optimizer transaction, with manual_target
# set internally so the permanent one-shot gate remains closed for normal
# future automatic passes.
# ------------------------------------------------------------------
text=once(
    text,
    '''    target_movie_id=None,
    target_series_id=None,
    manual_target=False
''',
    '''    target_movie_id=None,
    target_series_id=None,
    target_episode_id=None,
    manual_target=False
''',
    "run_optimizer signature",
)

text=once(
    text,
    '''            if target_movie_id or target_series_id
''',
    '''            if target_movie_id or target_series_id or target_episode_id
''',
    "run_optimizer requested count",
)

text=once(
    text,
    '''        if app == "sonarr" and target_series_id:
            env["SMART_OPTIMIZER_SERIES_ID"] = str(
                int(target_series_id)
            )
            env["SMART_OPTIMIZER_TARGET_GRABS"] = "1"
            env["SONARR_SEARCHES_PER_RUN"] = "100"
''',
    '''        if app == "sonarr" and target_series_id:
            env["SMART_OPTIMIZER_SERIES_ID"] = str(
                int(target_series_id)
            )
            env["SMART_OPTIMIZER_TARGET_GRABS"] = "1"
            env["SONARR_SEARCHES_PER_RUN"] = "100"

        if app == "sonarr" and target_episode_id:
            env["SMART_OPTIMIZER_EPISODE_ID"] = str(
                int(target_episode_id)
            )
            env["SMART_OPTIMIZER_TARGET_GRABS"] = "1"
            env["SONARR_SEARCHES_PER_RUN"] = "1"
''',
    "Sonarr target episode environment",
)

# Radarr dead retry is part of the SAME failed transaction. Explicitly use the
# internal manual-target bypass so the permanent one-shot record is preserved
# but does not prevent replacing a torrent that never downloaded a byte.
text=once(
    text,
    '''                    started = run_optimizer(
                        True,
                        app="radarr",
                        searches_per_run=None,
                        target_movie_id=movie_id
                    )
''',
    '''                    # Clear only the dead torrent's retention job. The
                    # replacement grab will create a fresh exact-hash job.
                    state.setdefault("tracker_jobs", {}).pop(
                        str(movie_id),
                        None
                    )
                    save_radarr_state(state)

                    started = run_optimizer(
                        True,
                        app="radarr",
                        searches_per_run=None,
                        target_movie_id=movie_id,
                        manual_target=True
                    )
''',
    "Radarr same-transaction one-shot bypass",
)

WATCHDOG=r'''def _dead_watchdog_all_queue_records(app):
    getter = radarr_get if app == "radarr" else sonarr_get
    records = []
    page = 1
    page_size = 250

    while True:
        unknown = (
            "includeUnknownMovieItems=true"
            if app == "radarr"
            else "includeUnknownSeriesItems=true"
        )

        data = getter(
            "/queue?page=%d&pageSize=%d&%s"
            % (page, page_size, unknown)
        ) or {}

        batch = data.get("records") or []
        records.extend(batch)

        try:
            total = int(data.get("totalRecords") or len(records))
        except (TypeError, ValueError):
            total = len(records)

        if (
            not batch
            or len(records) >= total
            or len(batch) < page_size
        ):
            break

        page += 1

    return records


def _dead_watchdog_deluge_statuses():
    fields = [
        "name",
        "label",
        "state",
        "progress",
        "total_done",
        "time_added",
        "num_seeds",
        "num_peers",
        "download_payload_rate",
    ]

    try:
        result = _deluge_rpc(
            "core.get_torrents_status",
            [{}, fields],
        )
    except Exception:
        result = _deluge_rpc(
            "core.get_torrents_status",
            [
                {},
                [
                    x
                    for x in fields
                    if x != "label"
                ],
            ],
        )

    return result if isinstance(result, dict) else {}


def _dead_watchdog_is_strict_dead(status, minimum_age=300):
    if not isinstance(status, dict) or not status:
        return False

    try:
        added = float(status.get("time_added") or 0)
        age = time.time() - added

        return (
            added > 0
            and age >= float(minimum_age)
            and float(status.get("progress") or 0) <= 0
            and int(status.get("total_done") or 0) <= 0
            and int(status.get("num_seeds") or 0) <= 0
            and int(status.get("num_peers") or 0) <= 0
            and int(status.get("download_payload_rate") or 0) <= 0
        )

    except (TypeError, ValueError):
        return False


def _dead_watchdog_optimizer_hashes():
    owned = {}

    rad_state = load_state()
    for key, job in (rad_state.get("tracker_jobs") or {}).items():
        download_id = str(
            (job or {}).get("download_id") or ""
        ).strip().upper()

        if download_id:
            owned[download_id] = ("radarr", str(key))

    son_state = load_sonarr_state()
    for key, job in (son_state.get("tracker_jobs") or {}).items():
        download_id = str(
            (job or {}).get("download_id") or ""
        ).strip().upper()

        if download_id:
            owned[download_id] = ("sonarr", str(key))

    return owned


def _dead_watchdog_remove_exact_queue(app, row):
    queue_id = int(row.get("id") or 0)
    download_id = str(row.get("downloadId") or "").strip().upper()

    if app == "radarr":
        media_id = int(row.get("movieId") or 0)
    else:
        media_id = int(row.get("episodeId") or 0)

    if not queue_id or not download_id or not media_id:
        raise RuntimeError("incomplete Arr queue identity")

    current = _dead_watchdog_all_queue_records(app)

    if app == "radarr":
        matches = [
            x for x in current
            if int(x.get("id") or 0) == queue_id
            and int(x.get("movieId") or 0) == media_id
            and str(x.get("downloadId") or "").strip().upper()
            == download_id
        ]
    else:
        matches = [
            x for x in current
            if int(x.get("id") or 0) == queue_id
            and int(x.get("episodeId") or 0) == media_id
            and str(x.get("downloadId") or "").strip().upper()
            == download_id
        ]

    if len(matches) != 1:
        raise RuntimeError("exact Arr queue revalidation failed")

    path = (
        "/queue/%d?removeFromClient=true&blocklist=true"
        % queue_id
    )

    if app == "radarr":
        radarr_request(path, method="DELETE")
    else:
        sonarr_request(path, method="DELETE")

    return media_id, download_id


def _dead_watchdog_native_search(app, media_id):
    if app == "radarr":
        return radarr_request(
            "/command",
            method="POST",
            payload={
                "name": "MoviesSearch",
                "movieIds": [int(media_id)],
            },
        )

    return sonarr_request(
        "/command",
        method="POST",
        payload={
            "name": "EpisodeSearch",
            "episodeIds": [int(media_id)],
        },
    )


def _dead_watchdog_handle_sonarr_optimizer(
    queue_rows,
    deluge_statuses,
):
    """
    Retry exact optimizer-owned Sonarr torrents that never start.

    Only the SAME failed transaction gets the manual-target bypass. The series
    remains permanently one-shot for all normal future automatic passes.
    """
    with job_lock:
        if jobs.get("sonarr", {}).get("running"):
            return False

    state = load_sonarr_state()
    tracker_jobs = state.setdefault("tracker_jobs", {})

    for key, job in list(tracker_jobs.items()):
        download_id = str(
            (job or {}).get("download_id") or ""
        ).strip().upper()

        if not download_id:
            continue

        status = None
        for torrent_hash, torrent_status in deluge_statuses.items():
            if str(torrent_hash or "").strip().upper() == download_id:
                status = torrent_status
                break

        if not _dead_watchdog_is_strict_dead(status):
            continue

        episode_id = int((job or {}).get("media_id") or key)
        queue_id = int((job or {}).get("queue_id") or 0)

        matches = [
            row for row in queue_rows
            if int(row.get("id") or 0) == queue_id
            and int(row.get("episodeId") or 0) == episode_id
            and str(row.get("downloadId") or "").strip().upper()
            == download_id
        ]

        if len(matches) != 1:
            continue

        history = state.setdefault("episodes", {}).setdefault(
            str(episode_id),
            {}
        )
        retry_count = int(history.get("dead_retry_count") or 0)

        # Always clean a proven strict-dead exact hash. Search again only
        # while below the bounded retry cap.
        _dead_watchdog_remove_exact_queue(
            "sonarr",
            matches[0],
        )

        history["dead_retry_count"] = retry_count + 1
        history["last_dead_download_id"] = download_id
        history["last_dead_removed_at"] = int(time.time())

        tracker_jobs.pop(str(key), None)
        save_sonarr_state(state)

        if retry_count >= 3:
            print(
                "[dead-watchdog] Sonarr retry cap reached; "
                "dead exact torrent removed without another search:",
                episode_id,
                download_id,
            )
            return True

        started = run_optimizer(
            True,
            app="sonarr",
            searches_per_run=None,
            target_episode_id=episode_id,
            manual_target=True,
        )

        print(
            "[dead-watchdog] SONARR OPTIMIZER DEAD:",
            episode_id,
            download_id,
            "retry",
            retry_count + 1,
            "started" if started else "not-started",
        )
        return True

    return False


def dead_download_watchdog_snapshot():
    """
    Read-only summary used for live validation before the worker is enabled.
    """
    torrents = _dead_watchdog_deluge_statuses()
    optimizer = _dead_watchdog_optimizer_hashes()

    result = {
        "strict_dead": 0,
        "radarr_queue_owned": 0,
        "sonarr_queue_owned": 0,
        "optimizer_owned": 0,
        "orphan": 0,
    }

    queue_hashes = {}

    for app in ("radarr", "sonarr"):
        for row in _dead_watchdog_all_queue_records(app):
            download_id = str(
                row.get("downloadId") or ""
            ).strip().upper()

            if download_id:
                queue_hashes.setdefault(download_id, []).append((app, row))

    for torrent_hash, status in torrents.items():
        if not _dead_watchdog_is_strict_dead(status):
            continue

        result["strict_dead"] += 1
        download_id = str(torrent_hash or "").strip().upper()

        if download_id in optimizer:
            result["optimizer_owned"] += 1

        owners = queue_hashes.get(download_id) or []

        if not owners:
            result["orphan"] += 1
            continue

        for app, _row in owners:
            result[app + "_queue_owned"] += 1

    return result


def dead_download_watchdog_worker():
    """
    5-minute strict-dead watchdog.

    * Optimizer-owned Radarr is left to the existing Radarr transaction worker.
    * Optimizer-owned Sonarr gets one bounded same-transaction targeted retry.
    * Other exact Radarr/Sonarr queue items are blocklisted, removed from the
      client, and searched again through their native Arr.
    * Orphan/manual torrents that cannot be tied to an exact Arr queue row are
      never guessed from title and are left for the 11-day stale cleanup.
    """
    while True:
        try:
            torrents = _dead_watchdog_deluge_statuses()
            optimizer = _dead_watchdog_optimizer_hashes()

            rad_rows = _dead_watchdog_all_queue_records("radarr")
            son_rows = _dead_watchdog_all_queue_records("sonarr")

            # Sonarr optimizer-owned retries are handled here.
            if _dead_watchdog_handle_sonarr_optimizer(
                son_rows,
                torrents,
            ):
                time.sleep(15)
                continue

            handled = 0

            for app, rows in (
                ("radarr", rad_rows),
                ("sonarr", son_rows),
            ):
                for row in rows:
                    if handled >= 3:
                        break

                    if str(row.get("status") or "").lower() == "completed":
                        continue

                    download_id = str(
                        row.get("downloadId") or ""
                    ).strip().upper()

                    if not download_id:
                        continue

                    # Existing optimizer transaction workers own these.
                    if download_id in optimizer:
                        continue

                    status = None
                    for torrent_hash, torrent_status in torrents.items():
                        if (
                            str(torrent_hash or "").strip().upper()
                            == download_id
                        ):
                            status = torrent_status
                            break

                    if not _dead_watchdog_is_strict_dead(status):
                        continue

                    media_id, exact_hash = _dead_watchdog_remove_exact_queue(
                        app,
                        row,
                    )

                    _dead_watchdog_native_search(
                        app,
                        media_id,
                    )

                    handled += 1

                    print(
                        "[dead-watchdog] CLEANED + RESEARCH:",
                        app,
                        media_id,
                        exact_hash,
                    )

                if handled >= 3:
                    break

        except Exception as exc:
            print(
                "[dead-watchdog] worker error:",
                exc,
            )

        time.sleep(15)


'''

text=once(
    text,
    '''def sonarr_tracker_retention_worker():
''',
    WATCHDOG + '''def sonarr_tracker_retention_worker():
''',
    "insert 5-minute watchdog",
)

text=once(
    text,
    '''    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
''',
    '''    threading.Thread(
        target=dead_download_watchdog_worker,
        name="arr-dead-download-watchdog",
        daemon=True
    ).start()
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
''',
    "start 5-minute watchdog",
)

path.write_text(text,encoding="utf-8")

print("PATCH COMPLETE")
print("UI SHA256",sha(text))
