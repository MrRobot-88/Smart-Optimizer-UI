#!/usr/bin/env python3
import hashlib
import re
import sys
from pathlib import Path

if len(sys.argv) != 4:
    raise SystemExit(
        "usage: tracker_retention_patch.py RADARR SONARR UI"
    )

rad_path=Path(sys.argv[1])
son_path=Path(sys.argv[2])
ui_path=Path(sys.argv[3])

rad=rad_path.read_text(encoding="utf-8")
son=son_path.read_text(encoding="utf-8")
ui=ui_path.read_text(encoding="utf-8")

EXPECTED_RAD="f31394c33f66387ae4e0b58a8f11cc348eb4ff805734685bfea066062baadc13"
EXPECTED_SON="7ef00f836575f227d5f0ec9b620d96b0321f298387a49ce6cf0e712e356ff45f"
EXPECTED_UI="ae54279fab022723e2f67b6a95e38e9a8d1af22ce2cfa6ca867731b0f252407e"

def sha(text):
    return hashlib.sha256(
        text.encode("utf-8")
    ).hexdigest()

for label,text,expected in (
    ("Radarr",rad,EXPECTED_RAD),
    ("Sonarr",son,EXPECTED_SON),
    ("UI",ui,EXPECTED_UI),
):
    actual=sha(text)
    if actual != expected:
        raise SystemExit(
            "STOP: %s source hash mismatch: %s"
            % (label,actual)
        )

def once(text,old,new,label):
    count=text.count(old)
    if count != 1:
        raise SystemExit(
            "STOP: %s: expected exactly 1 match, found %d"
            % (label,count)
        )
    return text.replace(old,new,1)

def regex_once(text,pattern,repl,label,flags=0):
    new,count=re.subn(
        pattern,
        repl,
        text,
        count=1,
        flags=flags,
    )
    if count != 1:
        raise SystemExit(
            "STOP: %s: expected exactly 1 regex match, found %d"
            % (label,count)
        )
    return new

TRACKER_POLICY_HELPER = r'''def tracker_policy_from_indexer(indexer):
    """
    Persist tracker retention from the exact indexer selected by Arr.

    TorrentLeech is retained/seeding. Every other identified indexer is
    eligible for exact-hash Deluge cleanup only after verified import.
    Unknown indexer stays unresolved and is never automatically deleted.
    """
    raw = str(indexer or "").strip()

    if not raw:
        return ""

    normalized = re.sub(
        r"[^a-z0-9]+",
        "",
        raw.lower()
    )

    if "torrentleech" in normalized:
        return "keep_seed"

    return "remove_after_verified_success"


'''

# ----------------------------------------------------------------------
# RADARR: tracker job persists independently of pending import transaction.
# ----------------------------------------------------------------------
rad=once(
    rad,
    '''        "pending_replacements": {},
        "auto_processed_movie_ids": []
''',
    '''        "pending_replacements": {},
        "auto_processed_movie_ids": [],
        "tracker_jobs": {}
''',
    "Radarr blank tracker jobs",
)

rad=once(
    rad,
    '''        state.setdefault("pending_replacements", {})
        state.setdefault("auto_processed_movie_ids", [])
''',
    '''        state.setdefault("pending_replacements", {})
        state.setdefault("auto_processed_movie_ids", [])
        state.setdefault("tracker_jobs", {})
''',
    "Radarr load tracker jobs",
)

rad=once(
    rad,
    '''def release_infohash(release):
''',
    TRACKER_POLICY_HELPER + '''def release_infohash(release):
''',
    "Radarr tracker policy helper",
)

rad=once(
    rad,
    '''            approved_release = choice["release"]
            approved_title = str(approved_release.get("title") or "")
            approved_size = int(approved_release.get("size") or 0)

            # Snapshot exact queue IDs BEFORE sending the release to Radarr.
''',
    '''            approved_release = choice["release"]
            approved_title = str(approved_release.get("title") or "")
            approved_size = int(approved_release.get("size") or 0)
            source_indexer = str(
                approved_release.get("indexer") or ""
            ).strip()
            tracker_policy = tracker_policy_from_indexer(
                source_indexer
            )

            # Snapshot exact queue IDs BEFORE sending the release to Radarr.
''',
    "Radarr capture selected indexer",
)

rad=once(
    rad,
    '''            state.setdefault("pending_replacements", {})

            pending_key = str(movie_id)

            previous_pending = (
''',
    '''            state.setdefault("pending_replacements", {})
            state.setdefault("tracker_jobs", {})

            pending_key = str(movie_id)

            if state["tracker_jobs"].get(pending_key):
                raise RuntimeError(
                    "Tracker retention job already exists for movie %d"
                    % int(movie_id)
                )

            previous_pending = (
''',
    "Radarr tracker job guard",
)

rad=once(
    rad,
    '''            state["pending_replacements"][pending_key] = {
                "movie_id": int(movie_id),
                "old_file_id": int(old_file_id),
                "old_size": int(old_file_size),
                "old_relative_path": old_relative_path,
                "approved_title": approved_title,
                "approved_size": approved_size,
                "release_key": release_key(approved_release),
                "pre_grab_queue_ids": pre_grab_queue_ids,
                "created": now_ts(),
                "status": "grabbing",
                "dead_retry_count": dead_retry_count
            }

            save_state(state)
''',
    '''            state["pending_replacements"][pending_key] = {
                "movie_id": int(movie_id),
                "old_file_id": int(old_file_id),
                "old_size": int(old_file_size),
                "old_relative_path": old_relative_path,
                "approved_title": approved_title,
                "approved_size": approved_size,
                "release_key": release_key(approved_release),
                "pre_grab_queue_ids": pre_grab_queue_ids,
                "created": now_ts(),
                "status": "grabbing",
                "dead_retry_count": dead_retry_count
            }

            state["tracker_jobs"][pending_key] = {
                "media_type": "radarr",
                "media_id": int(movie_id),
                "approved_title": approved_title,
                "source_indexer": source_indexer,
                "tracker_policy": tracker_policy,
                "policy_source": (
                    "indexer"
                    if tracker_policy
                    else "unresolved"
                ),
                "desired_label": (
                    "torrentleech-movies"
                    if tracker_policy == "keep_seed"
                    else ""
                ),
                "pre_grab_queue_ids": pre_grab_queue_ids,
                "created": now_ts(),
                "status": "grabbing"
            }

            save_state(state)
''',
    "Radarr persist tracker job intent",
)

rad=once(
    rad,
    '''                state.setdefault("pending_replacements", {}).pop(
                    pending_key,
                    None
                )
                save_state(state)
                raise
''',
    '''                state.setdefault("pending_replacements", {}).pop(
                    pending_key,
                    None
                )
                state.setdefault("tracker_jobs", {}).pop(
                    pending_key,
                    None
                )
                save_state(state)
                raise
''',
    "Radarr rollback tracker job",
)

rad=once(
    rad,
    '''            state["pending_replacements"][pending_key]["status"] = "grabbed"
            state["pending_replacements"][pending_key]["grabbed_at"] = now_ts()

            # Bind this optimizer-owned grab to Radarr's exact queue/download
''',
    '''            state["pending_replacements"][pending_key]["status"] = "grabbed"
            state["pending_replacements"][pending_key]["grabbed_at"] = now_ts()
            state["tracker_jobs"][pending_key]["status"] = "grabbed"
            state["tracker_jobs"][pending_key]["grabbed_at"] = now_ts()

            # Bind this optimizer-owned grab to Radarr's exact queue/download
''',
    "Radarr tracker grabbed status",
)

rad=once(
    rad,
    '''                state["pending_replacements"][pending_key]["bound_at"] = now_ts()

                print(
''',
    '''                state["pending_replacements"][pending_key]["bound_at"] = now_ts()
                state["tracker_jobs"][pending_key]["download_id"] = (
                    ownership["download_id"]
                )
                state["tracker_jobs"][pending_key]["queue_id"] = (
                    ownership["queue_id"]
                )
                state["tracker_jobs"][pending_key]["bound_at"] = now_ts()

                print(
''',
    "Radarr bind tracker exact hash",
)

# ----------------------------------------------------------------------
# SONARR: exact optimizer-owned tracker jobs + exact queue/hash binding.
# ----------------------------------------------------------------------
son=once(
    son,
    '''        "queue_initialized": False,
        "auto_processed_series_ids": []
''',
    '''        "queue_initialized": False,
        "auto_processed_series_ids": [],
        "tracker_jobs": {}
''',
    "Sonarr blank tracker jobs",
)

son=once(
    son,
    '''        state.setdefault("queue_initialized", False)
        state.setdefault("auto_processed_series_ids", [])
''',
    '''        state.setdefault("queue_initialized", False)
        state.setdefault("auto_processed_series_ids", [])
        state.setdefault("tracker_jobs", {})
''',
    "Sonarr load tracker jobs",
)

SON_QUEUE_HELPERS = r'''def all_sonarr_queue_records():
    records = []
    page = 1
    page_size = 250

    while True:
        data = get(
            "/queue?page=%d&pageSize=%d"
            "&includeUnknownSeriesItems=true"
            % (page, page_size)
        ) or {}

        batch = data.get("records") or []
        records.extend(batch)

        try:
            total = int(
                data.get("totalRecords")
                or len(records)
            )
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


def queue_ids_for_episode(episode_id):
    return {
        int(row.get("id") or 0)
        for row in all_sonarr_queue_records()
        if int(row.get("episodeId") or 0)
        == int(episode_id)
        and int(row.get("id") or 0) > 0
    }


def release_infohash(release):
    for field in (
        "downloadId",
        "torrentInfoHash",
        "infoHash",
        "guid",
        "downloadUrl",
        "magnetUrl",
    ):
        value = str(
            release.get(field) or ""
        ).strip()

        if not value:
            continue

        match = re.search(
            r"(?i)(?:btih:|/)([a-f0-9]{40})(?:$|[?&/])",
            value
        )

        if not match:
            match = re.search(
                r"(?i)\b([a-f0-9]{40})\b",
                value
            )

        if match:
            return match.group(1).upper()

    return ""


def bind_grabbed_download(
    episode_id,
    approved_release,
    preexisting_queue_ids=None,
    attempts=15,
    delay=1.0,
):
    expected_hash = release_infohash(
        approved_release
    )

    preexisting = set()

    for value in (
        preexisting_queue_ids or []
    ):
        try:
            queue_id = int(value)
        except (TypeError, ValueError):
            continue

        if queue_id > 0:
            preexisting.add(queue_id)

    if (
        not expected_hash
        and preexisting_queue_ids is None
    ):
        return None

    for _ in range(attempts):
        try:
            rows = [
                row
                for row in all_sonarr_queue_records()
                if int(row.get("episodeId") or 0)
                == int(episode_id)
            ]

            if expected_hash:
                rows = [
                    row
                    for row in rows
                    if str(
                        row.get("downloadId") or ""
                    ).strip().upper()
                    == expected_hash
                ]
            else:
                rows = [
                    row
                    for row in rows
                    if int(row.get("id") or 0) > 0
                    and int(row.get("id") or 0)
                    not in preexisting
                ]

            if len(rows) == 1:
                row = rows[0]

                download_id = str(
                    row.get("downloadId") or ""
                ).strip()

                queue_id = int(
                    row.get("id") or 0
                )

                if download_id and queue_id:
                    return {
                        "download_id": download_id,
                        "queue_id": queue_id,
                    }

        except Exception:
            pass

        time.sleep(delay)

    return None


''' + TRACKER_POLICY_HELPER

son=once(
    son,
    '''# ============================================================
# STATE
# ============================================================
''',
    SON_QUEUE_HELPERS + '''# ============================================================
# STATE
# ============================================================
''',
    "Sonarr queue/hash helpers",
)

SON_OLD_GRAB = '''        try:
            post("/release", choice["release"])
            grabs += 1

            if LIVE and TARGET_GRABS > 0:
                print(
                    "    UPGRADE GRABBED: %d / %d"
                    % (grabs, TARGET_GRABS),
                    flush=True
                )
            mark_release_attempted(state, choice["release"])
            save_state(state)
            print("    LIVE: RELEASE SENT TO SONARR", flush=True)
            print("    QUALIFYING REPLACEMENTS FOUND: %d" % grabs, flush=True)
            print("    Existing episode remains until Sonarr successfully downloads and imports replacement.")
        except Exception as e:
            errors += 1
            print("    GRAB ERROR:", e, flush=True)
'''

SON_NEW_GRAB = '''        try:
            approved_release = choice["release"]
            approved_title = str(
                approved_release.get("title") or ""
            ).strip()
            source_indexer = str(
                approved_release.get("indexer") or ""
            ).strip()
            tracker_policy = tracker_policy_from_indexer(
                source_indexer
            )
            pre_grab_queue_ids = sorted(
                queue_ids_for_episode(
                    episode_id
                )
            )

            state.setdefault(
                "tracker_jobs",
                {}
            )
            tracker_key = str(
                episode_id
            )

            if state["tracker_jobs"].get(
                tracker_key
            ):
                raise RuntimeError(
                    "Tracker retention job already exists "
                    "for episode %d"
                    % int(episode_id)
                )

            state["tracker_jobs"][
                tracker_key
            ] = {
                "media_type": "sonarr",
                "media_id": int(episode_id),
                "series_id": int(
                    item.get("series_id") or 0
                ),
                "approved_title": approved_title,
                "source_indexer": source_indexer,
                "tracker_policy": tracker_policy,
                "policy_source": (
                    "indexer"
                    if tracker_policy
                    else "unresolved"
                ),
                "desired_label": (
                    "torrentleech-tv"
                    if tracker_policy == "keep_seed"
                    else ""
                ),
                "pre_grab_queue_ids": (
                    pre_grab_queue_ids
                ),
                "created": now_ts(),
                "status": "grabbing",
            }

            save_state(state)

            try:
                post(
                    "/release",
                    approved_release
                )
            except Exception:
                state.setdefault(
                    "tracker_jobs",
                    {}
                ).pop(
                    tracker_key,
                    None
                )
                save_state(state)
                raise

            state["tracker_jobs"][
                tracker_key
            ]["status"] = "grabbed"
            state["tracker_jobs"][
                tracker_key
            ]["grabbed_at"] = now_ts()

            ownership = bind_grabbed_download(
                episode_id,
                approved_release,
                pre_grab_queue_ids,
            )

            if ownership:
                state["tracker_jobs"][
                    tracker_key
                ]["download_id"] = (
                    ownership["download_id"]
                )
                state["tracker_jobs"][
                    tracker_key
                ]["queue_id"] = (
                    ownership["queue_id"]
                )
                state["tracker_jobs"][
                    tracker_key
                ]["bound_at"] = now_ts()

                print(
                    "    OWNERSHIP BOUND:",
                    ownership["download_id"],
                    flush=True
                )
            else:
                print(
                    "    OWNERSHIP PENDING: tracker worker "
                    "will bind the Sonarr queue entry.",
                    flush=True
                )

            grabs += 1

            if LIVE and TARGET_GRABS > 0:
                print(
                    "    UPGRADE GRABBED: %d / %d"
                    % (grabs, TARGET_GRABS),
                    flush=True
                )

            mark_release_attempted(
                state,
                approved_release
            )
            save_state(state)

            print(
                "    LIVE: RELEASE SENT TO SONARR",
                flush=True
            )
            print(
                "    QUALIFYING REPLACEMENTS FOUND: %d"
                % grabs,
                flush=True
            )
            print(
                "    Existing episode remains until Sonarr "
                "successfully downloads and imports replacement."
            )

        except Exception as e:
            errors += 1
            print("    GRAB ERROR:", e, flush=True)
'''

son=once(
    son,
    SON_OLD_GRAB,
    SON_NEW_GRAB,
    "Sonarr persist tracker job around grab",
)

# ----------------------------------------------------------------------
# UI: exact-hash Deluge actions + Radarr/Sonarr retention workers.
# ----------------------------------------------------------------------

NEW_DELUGE_RPC = r'''def _deluge_rpc(method, params=None):
    """Authenticated Deluge Web JSON-RPC call using private local settings."""
    cfg = deluge_connection()

    if not cfg["password"]:
        raise RuntimeError(
            "Deluge Web password is not configured"
        )

    url = "%s://%s:%d/json" % (
        cfg["scheme"],
        cfg["host"],
        cfg["port"],
    )

    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(
            jar
        )
    )

    rpc_id = 0

    def rpc(call_method, call_params=None):
        nonlocal rpc_id
        rpc_id += 1

        body = json.dumps({
            "method": call_method,
            "params": call_params or [],
            "id": rpc_id,
        }).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type":
                    "application/json",
                "Accept":"application/json",
            },
            method="POST",
        )

        with opener.open(
            req,
            timeout=15
        ) as response:
            result = json.loads(
                response.read().decode(
                    "utf-8"
                ) or "{}"
            )

        if result.get("error"):
            raise RuntimeError(
                "Deluge RPC %s failed: %s"
                % (
                    call_method,
                    result.get("error"),
                )
            )

        return result.get("result")

    if rpc(
        "auth.login",
        [cfg["password"]]
    ) is not True:
        raise RuntimeError(
            "Deluge Web login failed"
        )

    if rpc(
        "web.connected",
        []
    ) is not True:
        raise RuntimeError(
            "Deluge daemon is not connected"
        )

    return rpc(
        method,
        params or []
    )


def deluge_torrent_status(download_id):
    """Read one exact torrent from Deluge Web JSON-RPC."""
    fields = [
        "state",
        "name",
        "progress",
        "total_done",
        "num_seeds",
        "total_seeds",
        "num_peers",
        "total_peers",
        "download_payload_rate",
        "distributed_copies",
        "last_seen_complete",
        "tracker",
        "trackers",
        "label",
    ]

    result = _deluge_rpc(
        "core.get_torrent_status",
        [
            str(
                download_id or ""
            ).lower(),
            fields,
        ],
    )

    return (
        result
        if isinstance(result, dict)
        else {}
    )


'''

ui=regex_once(
    ui,
    r'''def deluge_torrent_status\(download_id\):.*?(?=def deluge_torrent_is_dead\(status\):)''',
    NEW_DELUGE_RPC,
    "UI shared Deluge RPC",
    flags=re.S,
)

TRACKER_UI_HELPERS = r'''def _deluge_tracker_hosts(status):
    hosts = set()

    raw = str(
        (status or {}).get("tracker") or ""
    ).strip()

    if raw:
        try:
            host = urllib.parse.urlparse(
                raw
            ).hostname
        except Exception:
            host = None

        if host:
            hosts.add(
                host.lower()
            )

    for item in (
        (status or {}).get("trackers")
        or []
    ):
        url = str(
            (item or {}).get("url")
            or ""
        ).strip()

        if not url:
            continue

        try:
            host = urllib.parse.urlparse(
                url
            ).hostname
        except Exception:
            host = None

        if host:
            hosts.add(
                host.lower()
            )

    return sorted(hosts)


def tracker_policy_from_deluge_status(status):
    """
    Backfill only old optimizer jobs that predate persisted indexer metadata.
    New grabs use the exact selected Arr indexer instead.
    """
    if not isinstance(status, dict) or not status:
        return ""

    label = str(
        status.get("label") or ""
    ).strip().lower()

    if label in (
        "torrentleech-movies",
        "torrentleech-tv",
    ):
        return "keep_seed"

    hosts = _deluge_tracker_hosts(
        status
    )

    if not hosts:
        return ""

    if any(
        "torrentleech" in host
        for host in hosts
    ):
        return "keep_seed"

    return "remove_after_verified_success"


def deluge_set_exact_label(
    download_id,
    label,
):
    """Label one exact optimizer-owned torrent hash."""
    download_id = str(
        download_id or ""
    ).strip().lower()

    label = str(
        label or ""
    ).strip()

    if not download_id or not label:
        raise RuntimeError(
            "exact Deluge hash/label required"
        )

    status = deluge_torrent_status(
        download_id
    )

    if not status:
        raise RuntimeError(
            "optimizer-owned torrent is absent"
        )

    labels = _deluge_rpc(
        "label.get_labels",
        []
    ) or []

    if label not in {
        str(x)
        for x in labels
    }:
        _deluge_rpc(
            "label.add",
            [label]
        )

    _deluge_rpc(
        "label.set_torrent",
        [
            download_id,
            label,
        ],
    )

    check = deluge_torrent_status(
        download_id
    )

    if str(
        check.get("label") or ""
    ) != label:
        raise RuntimeError(
            "Deluge label verification failed"
        )

    return True


def deluge_remove_exact_torrent(
    download_id,
):
    """
    Remove one exact optimizer-owned Deluge torrent and its download payload.

    Called ONLY after Arr import success is independently proven.
    Library files are never addressed here.
    """
    download_id = str(
        download_id or ""
    ).strip().lower()

    if not download_id:
        raise RuntimeError(
            "exact Deluge hash required"
        )

    status = deluge_torrent_status(
        download_id
    )

    if not status:
        return "absent"

    _deluge_rpc(
        "core.remove_torrent",
        [
            download_id,
            True,
        ],
    )

    check = deluge_torrent_status(
        download_id
    )

    if check:
        raise RuntimeError(
            "Deluge torrent still exists after exact removal"
        )

    return "removed"


def _all_arr_history_records(app):
    getter = (
        radarr_get
        if app == "radarr"
        else sonarr_get
    )

    records = []
    page = 1
    page_size = 250

    while page <= 50:
        data = getter(
            "/history?page=%d&pageSize=%d"
            "&sortKey=date&sortDirection=descending"
            % (
                page,
                page_size,
            )
        ) or {}

        batch = data.get("records") or []
        records.extend(batch)

        try:
            total = int(
                data.get("totalRecords")
                or len(records)
            )
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


def _tracker_import_proven(
    app,
    media_id,
    download_id,
):
    media_id = int(
        media_id or 0
    )

    wanted = str(
        download_id or ""
    ).strip().upper()

    if not media_id or not wanted:
        return False

    id_field = (
        "movieId"
        if app == "radarr"
        else "episodeId"
    )

    for event in _all_arr_history_records(
        app
    ):
        if str(
            event.get("eventType") or ""
        ) != "downloadFolderImported":
            continue

        if int(
            event.get(id_field) or 0
        ) != media_id:
            continue

        data = event.get("data") or {}

        event_download = str(
            event.get("downloadId")
            or data.get("downloadId")
            or ""
        ).strip().upper()

        if event_download == wanted:
            return True

    return False


def _ensure_radarr_tracker_jobs(
    state,
):
    """
    Backfill old pending Radarr grabs created before tracker metadata existed.

    Ownership still comes exclusively from the exact persisted download hash.
    """
    changed = False

    pending = state.get(
        "pending_replacements"
    ) or {}

    jobs = state.setdefault(
        "tracker_jobs",
        {}
    )

    for key, txn in pending.items():
        job = jobs.get(str(key))

        if not isinstance(job, dict):
            download_id = str(
                (txn or {}).get(
                    "download_id"
                )
                or ""
            ).strip()

            if not download_id:
                continue

            try:
                status = deluge_torrent_status(
                    download_id
                )
            except Exception:
                continue

            policy = (
                tracker_policy_from_deluge_status(
                    status
                )
            )

            if not policy:
                continue

            movie_id = int(
                (txn or {}).get(
                    "movie_id"
                )
                or key
            )

            jobs[str(key)] = {
                "media_type": "radarr",
                "media_id": movie_id,
                "approved_title": str(
                    (txn or {}).get(
                        "approved_title"
                    )
                    or ""
                ),
                "source_indexer": "",
                "tracker_policy": policy,
                "policy_source":
                    "deluge_trackers",
                "desired_label": (
                    "torrentleech-movies"
                    if policy == "keep_seed"
                    else ""
                ),
                "download_id": download_id,
                "queue_id": int(
                    (txn or {}).get(
                        "queue_id"
                    )
                    or 0
                ),
                "created": int(
                    (txn or {}).get(
                        "created"
                    )
                    or time.time()
                ),
                "status": "grabbed",
            }

            changed = True
            continue

        txn_download = str(
            (txn or {}).get(
                "download_id"
            )
            or ""
        ).strip()

        if (
            txn_download
            and not str(
                job.get("download_id")
                or ""
            ).strip()
        ):
            job["download_id"] = txn_download
            job["queue_id"] = int(
                (txn or {}).get(
                    "queue_id"
                )
                or 0
            )
            job["bound_at"] = int(
                time.time()
            )
            changed = True

    return changed


def _bind_sonarr_tracker_job(job):
    episode_id = int(
        job.get("media_id") or 0
    )

    if not episode_id:
        return False

    data = sonarr_get(
        "/queue?page=1&pageSize=1000"
        "&includeUnknownSeriesItems=true"
    ) or {}

    preexisting = {
        int(x)
        for x in (
            job.get(
                "pre_grab_queue_ids"
            )
            or []
        )
        if str(x).isdigit()
    }

    rows = [
        row
        for row in (
            data.get("records") or []
        )
        if int(
            row.get("episodeId") or 0
        ) == episode_id
        and int(
            row.get("id") or 0
        ) > 0
        and int(
            row.get("id") or 0
        ) not in preexisting
    ]

    if len(rows) != 1:
        return False

    row = rows[0]

    download_id = str(
        row.get("downloadId") or ""
    ).strip()

    queue_id = int(
        row.get("id") or 0
    )

    if not download_id or not queue_id:
        return False

    job["download_id"] = download_id
    job["queue_id"] = queue_id
    job["bound_at"] = int(
        time.time()
    )

    return True


def _process_tracker_jobs(
    app,
    state,
):
    jobs = state.setdefault(
        "tracker_jobs",
        {}
    )

    changed = False

    if app == "radarr":
        if _ensure_radarr_tracker_jobs(
            state
        ):
            changed = True

    pending = (
        state.get(
            "pending_replacements"
        )
        or {}
    )

    for key, job in list(
        jobs.items()
    ):
        try:
            media_id = int(
                job.get("media_id")
                or key
            )

            download_id = str(
                job.get("download_id")
                or ""
            ).strip()

            if (
                app == "sonarr"
                and not download_id
            ):
                if _bind_sonarr_tracker_job(
                    job
                ):
                    changed = True
                    download_id = str(
                        job.get(
                            "download_id"
                        )
                        or ""
                    ).strip()

            if not download_id:
                continue

            try:
                status = deluge_torrent_status(
                    download_id
                )
            except Exception as exc:
                job["last_error"] = str(
                    exc
                )
                continue

            policy = str(
                job.get("tracker_policy")
                or ""
            ).strip()

            if not policy and status:
                policy = (
                    tracker_policy_from_deluge_status(
                        status
                    )
                )

                if policy:
                    job[
                        "tracker_policy"
                    ] = policy
                    job[
                        "policy_source"
                    ] = "deluge_trackers"
                    job[
                        "desired_label"
                    ] = (
                        "torrentleech-movies"
                        if (
                            app == "radarr"
                            and policy
                            == "keep_seed"
                        )
                        else "torrentleech-tv"
                        if (
                            app == "sonarr"
                            and policy
                            == "keep_seed"
                        )
                        else ""
                    )
                    changed = True

            if policy == "keep_seed":
                desired = str(
                    job.get(
                        "desired_label"
                    )
                    or (
                        "torrentleech-movies"
                        if app == "radarr"
                        else "torrentleech-tv"
                    )
                )

                if status:
                    current_label = str(
                        status.get("label")
                        or ""
                    )

                    if current_label != desired:
                        deluge_set_exact_label(
                            download_id,
                            desired,
                        )
                        job[
                            "labeled_at"
                        ] = int(
                            time.time()
                        )
                        changed = True

            imported = (
                _tracker_import_proven(
                    app,
                    media_id,
                    download_id,
                )
            )

            if not imported:
                continue

            if (
                app == "radarr"
                and str(media_id)
                in pending
            ):
                # Radarr import worker has not yet proven the final
                # one-clean-movie invariant / self-heal completion.
                continue

            if app == "sonarr":
                episode = sonarr_get(
                    "/episode/%d"
                    % media_id
                ) or {}

                if not bool(
                    episode.get("hasFile")
                ):
                    continue

            if policy == "keep_seed":
                status = (
                    deluge_torrent_status(
                        download_id
                    )
                )

                if not status:
                    job[
                        "retention_issue"
                    ] = (
                        "TorrentLeech torrent "
                        "missing after verified "
                        "import"
                    )
                    changed = True
                    continue

                desired = str(
                    job.get(
                        "desired_label"
                    )
                    or (
                        "torrentleech-movies"
                        if app == "radarr"
                        else "torrentleech-tv"
                    )
                )

                if str(
                    status.get("label")
                    or ""
                ) != desired:
                    deluge_set_exact_label(
                        download_id,
                        desired,
                    )

                jobs.pop(
                    key,
                    None
                )
                changed = True

                print(
                    "[tracker-retention] KEEP SEEDING:",
                    app,
                    media_id,
                    download_id,
                    desired,
                )

            elif (
                policy
                == "remove_after_verified_success"
            ):
                result = (
                    deluge_remove_exact_torrent(
                        download_id
                    )
                )

                jobs.pop(
                    key,
                    None
                )
                changed = True

                print(
                    "[tracker-retention] CLEANED:",
                    app,
                    media_id,
                    download_id,
                    result,
                )

        except Exception as exc:
            job["last_error"] = str(
                exc
            )

            print(
                "[tracker-retention] job error %s/%s: %s"
                % (
                    app,
                    key,
                    exc,
                )
            )

    return changed


'''

ui=once(
    ui,
    '''def update_connection(app, scheme, host, port, api_key):
''',
    TRACKER_UI_HELPERS + '''def update_connection(app, scheme, host, port, api_key):
''',
    "UI tracker helpers",
)

ui=once(
    ui,
    '''def load_sonarr_state():
    try:
        with open(SONARR_STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"daily": {}, "episodes": {}}


''',
    '''def load_sonarr_state():
    try:
        with open(SONARR_STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {
            "daily": {},
            "episodes": {},
            "tracker_jobs": {},
        }


def save_sonarr_state(data):
    """Persist Sonarr optimizer state without replacing a bind-mounted inode."""
    with open(
        SONARR_STATE_FILE,
        "w",
        encoding="utf-8"
    ) as f:
        json.dump(
            data,
            f,
            indent=2,
            sort_keys=True
        )
        f.flush()
        os.fsync(
            f.fileno()
        )


''',
    "UI Sonarr state writer",
)

# Let the existing Radarr worker own Radarr tracker-state writes, avoiding a
# second Radarr-state writer thread.
ui=once(
    ui,
    '''            state = load_state()
            pending = state.get("pending_replacements") or {}

            if pending:
''',
    '''            state = load_state()
            pending = state.get("pending_replacements") or {}

            tracker_changed = _process_tracker_jobs(
                "radarr",
                state,
            )

            if tracker_changed:
                save_radarr_state(
                    state
                )

            if pending:
''',
    "UI Radarr tracker processing before import",
)

# After import/retry work, process again so a just-cleared pending transaction
# can immediately trigger exact-hash cleanup.
ui=once(
    ui,
    '''                    if started:
                        print(
                            "[radarr-fallback] targeted retry launched:",
                            movie_id,
                            "attempt",
                            retry_count + 1
                        )
                        break

        except Exception as exc:
''',
    '''                    if started:
                        print(
                            "[radarr-fallback] targeted retry launched:",
                            movie_id,
                            "attempt",
                            retry_count + 1
                        )
                        break

            state = load_state()

            if _process_tracker_jobs(
                "radarr",
                state,
            ):
                save_radarr_state(
                    state
                )

        except Exception as exc:
''',
    "UI Radarr tracker processing after import",
)

SONARR_TRACKER_WORKER = r'''def sonarr_tracker_retention_worker():
    """
    Label/retain TorrentLeech and clean other exact optimizer-owned torrents.

    Sonarr state is not touched while the UI-launched Sonarr optimizer process
    is running, avoiding concurrent state writers.
    """
    while True:
        try:
            with job_lock:
                running = bool(
                    jobs.get(
                        "sonarr",
                        {}
                    ).get("running")
                )

            if not running:
                state = (
                    load_sonarr_state()
                )

                if _process_tracker_jobs(
                    "sonarr",
                    state,
                ):
                    save_sonarr_state(
                        state
                    )

        except Exception as exc:
            print(
                "[tracker-retention] Sonarr worker error:",
                exc
            )

        time.sleep(15)


'''

ui=once(
    ui,
    '''def page():
''',
    SONARR_TRACKER_WORKER + '''def page():
''',
    "UI Sonarr tracker worker",
)

ui=once(
    ui,
    '''    threading.Thread(
        target=optimizer_import_worker,
        name="radarr-optimizer-import",
        daemon=True
    ).start()
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
''',
    '''    threading.Thread(
        target=optimizer_import_worker,
        name="radarr-optimizer-import",
        daemon=True
    ).start()
    threading.Thread(
        target=sonarr_tracker_retention_worker,
        name="sonarr-tracker-retention",
        daemon=True
    ).start()
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
''',
    "UI start Sonarr tracker worker",
)

rad_path.write_text(
    rad,
    encoding="utf-8"
)
son_path.write_text(
    son,
    encoding="utf-8"
)
ui_path.write_text(
    ui,
    encoding="utf-8"
)

print("PATCH COMPLETE")
print("RADARR SHA256",sha(rad))
print("SONARR SHA256",sha(son))
print("UI SHA256",sha(ui))
