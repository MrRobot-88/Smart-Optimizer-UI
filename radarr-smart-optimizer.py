#!/usr/bin/env python3

import os
import sys
import json
import re
import time
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone

# ============================================================
# RADARR SMART OPTIMIZER
#
# Default = DRY RUN
# Live    = --live
#
# IMPORTANT:
# - This script NEVER calls Radarr DELETE endpoints directly
# - In live mode Radarr may replace an existing file after importing a selected release
# - NEVER touches Deluge directly
# - Radarr performs normal Completed Download Handling/import
# - Search budgets are configurable; defaults are conservative for scheduled use
# ============================================================

# ============================================================
# QUICK SETUP
# ============================================================
# API keys are intentionally NOT stored in this source file.
# Set RADARR_KEY in your environment or use a protected wrapper/key file.
# Check the URL if Radarr is not on the same machine, then review
# SEARCHES_PER_RUN plus NORMAL_PROFILE_ID and UHD_PROFILE_ID below.
RADARR_URL_DEFAULT = "http://127.0.0.1:7878"
SEARCHES_PER_RUN = 10

RADARR_URL = os.environ.get("RADARR_URL", RADARR_URL_DEFAULT).rstrip("/")
API_KEY = os.environ.get("RADARR_KEY", "").strip()
SEARCHES_PER_RUN = int(os.environ.get("RADARR_SEARCHES_PER_RUN", SEARCHES_PER_RUN))

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.environ.get(
    "RADARR_OPTIMIZER_STATE",
    os.path.join(SCRIPT_DIR, "radarr-smart-optimizer-state.json")
)

NORMAL_PROFILE_ID = 4
UHD_PROFILE_ID = 5

DAILY_SEARCH_BUDGET = 300
MIN_SEEDERS = 1
MIN_SAVING_PERCENT = float(os.environ.get("RADARR_MIN_SAVING_PERCENT", "5.0"))
MAX_SAVING_PERCENT = float(os.environ.get("RADARR_MAX_SAVING_PERCENT", "50.0"))
CONTROL_FILE = os.environ.get("SMART_OPTIMIZER_CONTROL", os.path.join(SCRIPT_DIR, "smart-optimizer-control.json"))

def load_runtime_controls():
    controls = {}
    try:
        with open(CONTROL_FILE, "r", encoding="utf-8") as f:
            controls = (json.load(f) or {}).get("radarr", {})
    except Exception:
        pass
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        minimum = float(controls.get("min_saving_percent", MIN_SAVING_PERCENT))
        maximum = float(controls.get("max_saving_percent", MAX_SAVING_PERCENT))
        if not (0 <= minimum <= maximum <= 100):
            raise ValueError
    except (TypeError, ValueError):
        minimum, maximum = MIN_SAVING_PERCENT, MAX_SAVING_PERCENT
    try:
        extra = int((controls.get("daily_extra") or {}).get(today, 0))
    except (TypeError, ValueError):
        extra = 0
    return minimum, maximum, max(0, extra)

MIN_SAVING_PERCENT, MAX_SAVING_PERCENT, DAILY_EXTRA_BUDGET = load_runtime_controls()


# Don't deliberately grab the exact same release again for this long
ATTEMPT_COOLDOWN_DAYS = 365

# These are NOT hard quality limits.
# They are only used for PRIORITY.
LARGE_1080P_MIB = 1800
COMPACT_1080P_X265_MIB = 1200
LARGE_2160P_MIB = 6000

LIVE = "--live" in sys.argv

# Manual UI mode:
# Number entered in the UI means SUCCESSFUL upgrades/grabs,
# not number of indexer searches.
try:
    TARGET_GRABS = max(
        0,
        int(os.environ.get("SMART_OPTIMIZER_TARGET_GRABS", "0"))
    )
except (TypeError, ValueError):
    TARGET_GRABS = 0

if not API_KEY:
    print("ERROR: Radarr API key is not configured.")
    print()
    print("Set RADARR_KEY in your environment or protected wrapper/key file.")
    print("Then run: python3 radarr-smart-optimizer.py")
    sys.exit(1)


# ============================================================
# BASIC HELPERS
# ============================================================

def now_ts():
    return int(time.time())


def age_days(timestamp):
    if not timestamp:
        return 999999
    return (now_ts() - int(timestamp)) / 86400.0


def mib(value):
    try:
        return float(value) / 1024 / 1024
    except Exception:
        return 0.0


def api(method, path, data=None):
    url = RADARR_URL + "/api/v3" + path

    headers = {
        "X-Api-Key": API_KEY,
        "Accept": "application/json"
    }

    body = None

    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(
        url,
        data=body,
        headers=headers,
        method=method
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            raw = response.read()

            if not raw:
                return None

            return json.loads(raw.decode("utf-8"))

    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            "%s %s -> HTTP %s\n%s" %
            (method, path, e.code, detail)
        )

    except Exception as e:
        raise RuntimeError(
            "%s %s -> %s" %
            (method, path, e)
        )


def get(path):
    return api("GET", path)


def post(path, data):
    return api("POST", path, data)


def all_radarr_queue_records():
    """Return every Radarr queue row across all pages."""
    records = []
    page = 1
    page_size = 100

    while True:
        data = get(
            "/queue?page=%d&pageSize=%d&includeUnknownMovieItems=true"
            % (page, page_size)
        ) or {}
        batch = data.get("records") or []
        records.extend(batch)

        try:
            total = int(data.get("totalRecords") or len(records))
        except (TypeError, ValueError):
            total = len(records)

        if not batch or len(records) >= total or len(batch) < page_size:
            break

        page += 1

    return records


def queue_ids_for_movie(movie_id):
    return {
        int(row.get("id") or 0)
        for row in all_radarr_queue_records()
        if int(row.get("movieId") or 0) == int(movie_id)
        and int(row.get("id") or 0) > 0
    }


def bind_grabbed_download(
    movie_id,
    approved_release,
    preexisting_queue_ids=None,
    attempts=15,
    delay=1.0
):
    """Bind only an exact hash or a queue row proven new after our grab."""
    expected_hash = release_infohash(approved_release)
    preexisting = set()

    for value in (preexisting_queue_ids or []):
        try:
            queue_id = int(value)
        except (TypeError, ValueError):
            continue
        if queue_id > 0:
            preexisting.add(queue_id)

    # Without a release hash, a pre-grab queue snapshot is mandatory. This
    # deliberately fails closed instead of guessing from title/movie alone.
    if not expected_hash and preexisting_queue_ids is None:
        return None

    for _ in range(attempts):
        try:
            rows = [
                row for row in all_radarr_queue_records()
                if int(row.get("movieId") or 0) == int(movie_id)
            ]

            if expected_hash:
                rows = [
                    row for row in rows
                    if str(row.get("downloadId") or "").strip().upper()
                    == expected_hash.upper()
                ]
            else:
                rows = [
                    row for row in rows
                    if int(row.get("id") or 0) > 0
                    and int(row.get("id") or 0) not in preexisting
                ]

            if len(rows) == 1:
                row = rows[0]
                download_id = str(row.get("downloadId") or "").strip()
                queue_id = int(row.get("id") or 0)

                if download_id and queue_id:
                    return {
                        "download_id": download_id,
                        "queue_id": queue_id
                    }

        except Exception:
            pass

        time.sleep(delay)

    return None


# ============================================================
# STATE
# ============================================================

def blank_state():
    return {
        "version": 1,
        "movies": {},
        "attempted_releases": {},
        "daily": {},
        "movie_queue": [],
        "movie_cursor": 0,
        "known_movie_ids": [],
        "queue_initialized": False,
        "pending_replacements": {},
        "auto_processed_movie_ids": [],
        "tracker_jobs": {}
    }


def load_state():
    if not os.path.exists(STATE_FILE):
        return blank_state()

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)

        state.setdefault("version", 1)
        state.setdefault("movies", {})
        state.setdefault("attempted_releases", {})
        state.setdefault("daily", {})
        state.setdefault("movie_queue", [])
        state.setdefault("movie_cursor", 0)
        state.setdefault("known_movie_ids", [])
        state.setdefault("queue_initialized", False)
        state.setdefault("pending_replacements", {})
        state.setdefault("auto_processed_movie_ids", [])
        state.setdefault("tracker_jobs", {})

        return state

    except Exception as e:
        print("WARNING: Could not read state file:")
        print(" ", e)
        print("Using empty state for this run.")
        return blank_state()


def save_state(state):
    if not LIVE:
        return

    # STATE_FILE may be a Docker single-file bind mount. Replacing the inode
    # with os.replace() can fail with EBUSY. Write in place instead, matching
    # the production-safe Sonarr implementation.
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())


def today_key():
    return datetime.now().strftime("%Y-%m-%d")


def searches_used_today(state):
    return int(
        state.get("daily", {})
             .get(today_key(), {})
             .get("searches", 0)
    )


def increment_search_count(state):
    day = today_key()

    state.setdefault("daily", {})
    state["daily"].setdefault(day, {"searches": 0})

    state["daily"][day]["searches"] += 1

    # Remove ancient daily counters.
    keys = sorted(state["daily"].keys())

    if len(keys) > 60:
        for old in keys[:-60]:
            state["daily"].pop(old, None)


def auto_processed_movie_ids(state):
    """Movies that have already had their one automatic optimizer search."""
    processed = {
        int(x)
        for x in state.get("auto_processed_movie_ids", [])
        if str(x).isdigit()
    }

    # Migration from all older optimizer state versions: any movie with at
    # least one successful /release search is permanently one-shot processed.
    for key, entry in (state.get("movies", {}) or {}).items():
        try:
            if bool(entry.get("auto_processed")) or int(entry.get("search_cycles", 0)) >= 1:
                processed.add(int(key))
        except (TypeError, ValueError, AttributeError):
            continue

    return processed


def mark_movie_searched(state, movie_id):
    """Persist the permanent automatic one-shot gate after a real search."""
    movie_id = int(movie_id)
    key = str(movie_id)
    state["movies"].setdefault(key, {})
    entry = state["movies"][key]
    entry["last_search"] = now_ts()
    entry["search_cycles"] = max(1, int(entry.get("search_cycles", 0)))
    entry["auto_processed"] = True

    processed = auto_processed_movie_ids(state)
    processed.add(movie_id)
    state["auto_processed_movie_ids"] = sorted(processed)


def tracker_policy_from_indexer(indexer):
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


def release_infohash(release):
    """Return canonical BTIH when Prowlarr exposes one directly or in a magnet."""
    for field in (
        "downloadId",
        "torrentInfoHash",
        "infoHash",
        "guid",
        "downloadUrl",
        "magnetUrl"
    ):
        value = str(release.get(field) or "").strip()

        if not value:
            continue

        match = re.search(
            r"(?:btih:)?([A-Fa-f0-9]{40})(?:[^A-Fa-f0-9]|$)",
            value
        )

        if match:
            return match.group(1).upper()

    return None


def release_key(release):
    infohash = release_infohash(release)

    if infohash:
        return "btih:" + infohash

    for field in (
        "guid",
        "downloadUrl",
        "infoUrl",
        "title"
    ):
        value = release.get(field)
        if value:
            return str(value)

    return str(release.get("title", "UNKNOWN"))


def release_recently_attempted(state, release):
    key = release_key(release)
    attempts = state.get("attempted_releases", {})

    ts = attempts.get(key)

    if ts and age_days(ts) < ATTEMPT_COOLDOWN_DAYS:
        return True

    # Backward compatibility: older state entries used URL/magnet keys.
    # If this release has a BTIH, also detect that hash inside old keys.
    infohash = release_infohash(release)

    if infohash:
        needle = infohash.lower()

        for old_key, old_ts in attempts.items():
            if (
                needle in str(old_key).lower()
                and age_days(old_ts) < ATTEMPT_COOLDOWN_DAYS
            ):
                return True

    return False


def mark_release_attempted(state, release):
    key = release_key(release)
    state.setdefault("attempted_releases", {})
    state["attempted_releases"][key] = now_ts()


def clean_old_attempts(state):
    cutoff = ATTEMPT_COOLDOWN_DAYS + 30

    remove = []

    for key, ts in state.get("attempted_releases", {}).items():
        if age_days(ts) > cutoff:
            remove.append(key)

    for key in remove:
        state["attempted_releases"].pop(key, None)


# ============================================================
# MEDIA PARSING
# ============================================================

def codec_from_text(text):
    """
    Detect VIDEO codec from a release title.

    Normalized values:
      x265
      x264
      av1
      vp9
      vp8
      vc1
      unknown

    MKV/MP4 are containers and intentionally do NOT determine codec.
    """
    text = text or ""

    # H.265 / HEVC
    if re.search(
        r'(?i)(?:\bx[ ._-]?265\b|\bh[ ._-]?265\b|\bhevc\b)',
        text
    ):
        return "x265"

    # H.264 / AVC
    if re.search(
        r'(?i)(?:\bx[ ._-]?264\b|\bh[ ._-]?264\b|\bavc\b)',
        text
    ):
        return "x264"

    # AV1 / AV01
    if re.search(
        r'(?i)(?:\bav[ ._-]?1\b|\bav01\b)',
        text
    ):
        return "av1"

    # VP9 / VP09
    if re.search(
        r'(?i)(?:\bvp[ ._-]?9\b|\bvp09\b)',
        text
    ):
        return "vp9"

    # VP8 / VP08
    if re.search(
        r'(?i)(?:\bvp[ ._-]?8\b|\bvp08\b)',
        text
    ):
        return "vp8"

    # VC-1 / VC1
    if re.search(
        r'(?i)\bvc[ ._-]?1\b',
        text
    ):
        return "vc1"

    return "unknown"


def audio_channels_from_text(text):
    text = (text or "").lower()

    patterns = [
        (7.1, r"(?<!\d)7[\s._-]?1\b"),
        (5.1, r"(?<!\d)5[\s._-]?1\b"),
        (2.1, r"(?<!\d)2[\s._-]?1\b"),
        (2.0, r"(?<!\d)2[\s._-]?0\b"),
        (1.0, r"(?<!\d)1[\s._-]?0\b"),
    ]

    for channels, pattern in patterns:
        if re.search(pattern, text):
            return channels

    if "stereo" in text:
        return 2.0

    return None


DANGEROUS_EXTENSIONS = (
    "exe", "scr", "bat", "cmd", "msi", "com",
    "pif", "vbs", "js", "jar", "ps1"
)


def dangerous_release_title(text):
    """
    Hard-block executable/script payloads.
    MKV, MP4 and other normal media containers are unaffected.
    """
    text = text or ""

    pattern = (
        r'(?i)\.(?:'
        + '|'.join(re.escape(ext) for ext in DANGEROUS_EXTENSIONS)
        + r')(?=$|[\s._\-\[\]\(\)])'
    )

    return bool(re.search(pattern, text))


def current_codec(file_obj):
    media = file_obj.get("mediaInfo") or {}

    codec = codec_from_text(
        " ".join([
            str(media.get("videoCodec", "")),
            str(file_obj.get("sceneName", "")),
            str(file_obj.get("relativePath", ""))
        ])
    )

    return codec


def quality_resolution(quality_obj):
    q = quality_obj or {}

    if "quality" in q:
        q = q.get("quality") or {}

    resolution = q.get("resolution")

    try:
        if resolution:
            return int(resolution)
    except Exception:
        pass

    name = str(q.get("name", ""))

    match = re.search(r"(2160|1080|720|480)", name)

    if match:
        return int(match.group(1))

    return 0


def file_resolution(file_obj):
    media = file_obj.get("mediaInfo") or {}

    width = media.get("width")
    height = media.get("height")

    try:
        height = int(height or 0)
    except Exception:
        height = 0

    if height >= 2000:
        return 2160

    if height >= 1000:
        return 1080

    if height >= 700:
        return 720

    return quality_resolution(file_obj.get("quality"))


# ============================================================
# QUEUE
# ============================================================

def active_movie_ids():
    ids = set()

    for item in all_radarr_queue_records():
        movie_id = item.get("movieId")

        if movie_id:
            ids.add(int(movie_id))

    return ids


# ============================================================
# LOCAL LIBRARY CANDIDATES
# ============================================================

def priority_score(item):
    """
    Higher = search earlier.

    IMPORTANT:
    This only decides WHICH existing movies deserve one of
    our scarce interactive searches.

    It does NOT decide which release wins after searching.
    Profile 5 can still prefer a valid <=8 GiB 2160p release.
    If no valid 2160p exists, smaller qualifying 1080p releases
    remain fully eligible.
    """

    res = item["resolution"]
    target = item["target_resolution"]
    size = item["size_mib"]
    codec = item["codec"]

    score = 0.0

    # --------------------------------------------------------
    # PROFILE 5 / 4K-PREFERRED MOVIES
    # --------------------------------------------------------
    if item["profile_id"] == UHD_PROFILE_ID:

        # Missing target resolution is important, but don't give
        # every 1080p movie an identical gigantic score.
        if res < 2160:
            score += 600000

            # Lower-than-1080p files are much more urgent.
            if res < 1080:
                score += 300000

            # Larger existing files have more optimization
            # potential if no acceptable 4K release exists.
            score += min(size * 100, 300000)

            # x264 gets extra attention because x265 often gives
            # worthwhile space savings.
            if codec == "x264":
                score += 100000
            elif codec == "unknown":
                score += 50000

            # Already tiny 1080p x265 files can still eventually
            # be searched for 4K, but should not steal today's
            # scarce slots from much larger files.
            if (
                res == 1080
                and codec == "x265"
                and size <= COMPACT_1080P_X265_MIB
            ):
                score -= 150000

        else:
            # Already 2160p: only optimization potential matters.
            score += min(size * 25, 200000)

            if codec == "x264":
                score += 75000

            if size >= LARGE_2160P_MIB:
                score += 100000

        return score

    # --------------------------------------------------------
    # NORMAL 1080P PROFILE
    # --------------------------------------------------------

    # Resolution deficiency has highest priority.
    if res < target:
        score += 800000
        score += (target - res) * 500

    # x264 generally has greater compression-saving potential.
    if codec == "x264":
        score += 200000
    elif codec == "unknown":
        score += 100000

    # Large files deserve attention.
    if size >= LARGE_1080P_MIB:
        score += 200000

    score += min(size * 50, 250000)

    # Compact 1080p x265 is already in a very good state.
    if (
        res >= 1080
        and codec == "x265"
        and size <= COMPACT_1080P_X265_MIB
    ):
        score -= 200000

    return score

def initialize_movie_queue(state, movies):
    """Create the persistent A-Z optimizer queue once."""
    with_files = [m for m in movies if m.get("id") and m.get("hasFile")]
    ordered = sorted(
        with_files,
        key=lambda m: ((m.get("title") or "").casefold(), int(m.get("id", 0)))
    )
    state["movie_queue"] = [
        {
            "movie_id": int(m["id"]),
            "title": m.get("title") or "Unknown movie",
            "year": m.get("year"),
        }
        for m in ordered
    ]
    state["movie_cursor"] = 0
    state["known_movie_ids"] = [x["movie_id"] for x in state["movie_queue"]]
    state["queue_initialized"] = True
    save_state(state)
    print("PERMANENT A-Z MOVIE QUEUE READY:", len(state["movie_queue"]), "movies", flush=True)


def append_new_movies(state, movies):
    """Append newly downloaded movies to the END; never reorder the existing queue."""
    known = set(int(x) for x in state.get("known_movie_ids", []))
    added = 0
    for movie in movies:
        mid = int(movie.get("id", 0) or 0)
        if not mid or mid in known or not movie.get("hasFile"):
            continue
        state.setdefault("movie_queue", []).append({
            "movie_id": mid,
            "title": movie.get("title") or "Unknown movie",
            "year": movie.get("year"),
        })
        known.add(mid)
        added += 1
        print("APPENDED NEW MOVIE TO BOTTOM:", movie.get("title"), flush=True)
    state["known_movie_ids"] = sorted(known)
    if added:
        save_state(state)
    return added


def _root_video_name(relative_path):
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


def movie_item(movie, state, queued_ids):
    """Return an optimizer-searchable movie item, or None without consuming search quota."""
    movie_id = movie.get("id")
    if not movie_id or not movie.get("hasFile") or movie_id in queued_ids:
        return None

    # Automatic optimizer is strictly one-shot per movie forever.
    # Manual Optimizer creates a temporary state with this ID removed, so the
    # user can explicitly retry as many times as desired.
    if int(movie_id) in auto_processed_movie_ids(state):
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
    size_bytes = movie_file.get("size") or 0
    if size_bytes <= 0:
        return None

    # Do not spend searches/downloads optimizing already-small movies.
    # 5 GiB is based on the CURRENT file, not the replacement candidate.
    if size_bytes < 5 * 1024 ** 3:
        return None

    resolution = file_resolution(movie_file)
    if resolution not in (1080, 2160):
        return None

    profile_id = movie.get("qualityProfileId")
    target_resolution = 2160 if profile_id == UHD_PROFILE_ID else resolution

    return {
        "movie_id": movie_id,
        "tmdb_id": movie.get("tmdbId"),
        "title": movie.get("title") or "Unknown movie",
        "year": movie.get("year"),
        "profile_id": profile_id,
        "resolution": resolution,
        "target_resolution": target_resolution,
        "size_bytes": size_bytes,
        "size_mib": mib(size_bytes),
        "codec": current_codec(movie_file),
        "audio_channels": radarr_current_audio_channels(movie_file),
        "atmos": radarr_current_atmos(movie_file),
        "dynamic_range": radarr_current_dynamic_range(movie_file),
        "movie_file": movie_file,
    }


def next_movie_item(state, movies_by_id, queued_ids):
    """Advance the one persistent cursor until an eligible movie is found or queue ends."""
    queue = state.get("movie_queue", [])
    while int(state.get("movie_cursor", 0)) < len(queue):
        cursor = int(state.get("movie_cursor", 0))
        ref = queue[cursor]
        state["movie_cursor"] = cursor + 1
        if LIVE:
            save_state(state)

        movie = movies_by_id.get(int(ref.get("movie_id", 0)))
        if not movie:
            continue

        # User-managed Smart Optimizer exclusion.
        # Skip BEFORE any interactive release search.
        if int(movie.get("id", 0)) in optimizer_excluded_ids("radarr"):
            print(
                "    EXCLUDED: %s -- skipped without searching"
                % (movie.get("title") or "Unknown movie"),
                flush=True
            )
            continue

        item = movie_item(movie, state, queued_ids)
        if item is not None:
            return item
    return None


def optimizer_excluded_ids(app="radarr"):
    try:
        with open(CONTROL_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        raw = (data.get(app, {}) or {}).get("exclusions") or []
        return {
            int(x.get("id"))
            for x in raw
            if isinstance(x, dict) and x.get("id") is not None
        }
    except Exception:
        return set()


def collect_candidates(state, queued_ids):
    movies = get("/movie")
    items = []

    stats = {
        "movies": len(movies),
        "with_file": 0,
        "queued": 0,
        "eligible": 0,
    }

    for movie in movies:
        movie_id = movie.get("id")
        tmdb_id = movie.get("tmdbId")

        if not movie.get("hasFile"):
            continue

        stats["with_file"] += 1

        if movie_id in queued_ids:
            stats["queued"] += 1
            continue

        # Optimizer search-cycle policy:
        #   cycle 0: eligible now
        #   cycle 1: wait at least 180 days
        #   cycle 2: permanently excluded from this optimizer
        history = state.get("movies", {}).get(str(movie_id), {})
        cycles = int(history.get("search_cycles", 0))
        last_search = history.get("last_search")

        if cycles >= 2:
            continue

        if cycles == 1 and last_search and age_days(last_search) < 180:
            continue

        movie_file = movie.get("movieFile") or {}
        if not movie_file:
            continue

        size_bytes = movie_file.get("size") or 0
        if size_bytes <= 0:
            continue

        # Keep candidate collection consistent with movie_item():
        # current files below 5 GiB are not optimizer targets.
        if size_bytes < 5 * 1024 ** 3:
            continue

        resolution = file_resolution(movie_file)
        if not resolution:
            continue

        # Optimize actual 1080p and 2160p files regardless of
        # the Radarr quality profile assigned to the movie.
        # Ignore 720p and lower.
        if resolution not in (1080, 2160):
            continue

        profile_id = movie.get("qualityProfileId")

        # Normal profiles keep their current resolution.
        # UHD-profile movies may upgrade an existing 1080p file to 2160p.
        target_resolution = 2160 if profile_id == UHD_PROFILE_ID else resolution

        media = movie_file.get("mediaInfo") or {}

        item = {
            "movie_id": movie_id,
            "tmdb_id": tmdb_id,
            "title": movie.get("title") or "Unknown movie",
            "year": movie.get("year"),
            "profile_id": profile_id,
            "resolution": resolution,
            "target_resolution": target_resolution,
            "size_bytes": size_bytes,
            "size_mib": mib(size_bytes),
            "codec": current_codec(movie_file),
            "audio_channels": radarr_current_audio_channels(movie_file),
            "atmos": radarr_current_atmos(movie_file),
            "dynamic_range": radarr_current_dynamic_range(movie_file),
            "movie_file": movie_file,
        }

        items.append(item)
        stats["eligible"] += 1

    return items, stats


def rejection_allowed(rejection):
    """
    We ONLY ignore Radarr's cutoff rejection because the
    optimizer intentionally evaluates replacements beyond
    Radarr's normal cutoff.

    Every other Radarr rejection remains respected.
    """

    reason = ""

    if isinstance(rejection, dict):
        reason = str(
            rejection.get("reason")
            or rejection.get("message")
            or ""
        )
    else:
        reason = str(rejection)

    reason = reason.lower()

    # The optimizer has its own conservative quality gate below. Radarr may
    # call a smaller same-resolution BluRay release a downgrade when the current
    # file is a Remux. That is not automatically a downgrade for this project:
    # resolution/HDR/DV/audio protections and storage efficiency decide.
    optimizer_quality_rejections = (
        "existing file meets cutoff",
        "not an upgrade for existing movie file",
        "quality for existing file on disk is of equal or higher preference",
    )
    return any(text in reason for text in optimizer_quality_rejections)


def radarr_rejections_ok(release):
    rejected = release.get("rejections") or []

    for rejection in rejected:
        if not rejection_allowed(rejection):
            return False

    return True



def candidate_resolution_from_release(release):
    return quality_resolution(release.get("quality"))


def release_matches_movie(item, release):
    """Fail closed when an indexer result does not identify this movie."""
    release_title = str(release.get("title") or "").strip()
    movie_title = str(item.get("title") or "").strip()
    movie_year = int(item.get("year") or 0)

    if not release_title or not movie_title:
        return False

    def tokens(text):
        return re.findall(r"[a-z0-9]+", text.lower())

    release_tokens = tokens(release_title)
    movie_tokens = tokens(movie_title)

    if (
        not movie_tokens
        or len(release_tokens) < len(movie_tokens)
        or release_tokens[:len(movie_tokens)] != movie_tokens
    ):
        return False

    rest = release_tokens[len(movie_tokens):]

    # Nothing after the movie title gives us too little evidence that this is
    # a real release for the requested movie.
    if not rest:
        return False

    # If the first token after the title is a plausible movie year, it must
    # match Radarr's movie year exactly.
    if re.fullmatch(r"(?:19|20)\d{2}", rest[0]):
        if movie_year and int(rest[0]) != movie_year:
            return False
        rest = rest[1:]

    if not rest:
        return False

    # After title[/year], require recognizable release metadata. This prevents
    # prefix collisions such as "12 Strong Orphans" from matching "12 Strong".
    metadata_tokens = {
        "2160p", "1080p", "1080i", "720p", "576p", "480p",
        "uhd", "bluray", "bdrip", "brrip", "remux",
        "web", "webdl", "webrip", "webhd",
        "hdtv", "dvdrip",
        "x264", "x265", "h264", "h265", "hevc", "avc"
    }

    # Tokenization splits WEB-DL / WEBRip-style punctuation safely.
    first = rest[0]
    first_two = "".join(rest[:2]) if len(rest) >= 2 else first

    if first not in metadata_tokens and first_two not in metadata_tokens:
        return False

    return True



def _radarr_cut_suffix_tokens(text, movie_title="", movie_year=0):
    """Return release/file tokens after the known movie title and year."""
    tokens = re.findall(r"[a-z0-9]+", str(text or "").lower())
    movie_tokens = re.findall(
        r"[a-z0-9]+",
        str(movie_title or "").lower()
    )

    if (
        movie_tokens
        and len(tokens) >= len(movie_tokens)
        and tokens[:len(movie_tokens)] == movie_tokens
    ):
        tokens = tokens[len(movie_tokens):]

    year = str(int(movie_year or 0)) if movie_year else ""

    if year and tokens and tokens[0] == year:
        tokens = tokens[1:]

    return tokens


def radarr_protected_cut(
    text,
    movie_title="",
    movie_year=0,
    edition="",
):
    """
    Detect cuts that must never be replaced by an ordinary/theatrical release.

    Protected:
      * Extended / Extended Cut / Extended Edition / Extended Version
      * Limited Edition
      * Special Edition
      * Director Cut / Directors Cut / Director's Cut
      * DC abbreviation when it appears as an edition or near the front of the
        release suffix, e.g. Movie.2012.DC.1080p.BluRay...

    The known movie title is stripped first so titles containing words such as
    "Extended" or "DC" do not accidentally trigger edition protection.
    """
    suffix = _radarr_cut_suffix_tokens(
        text,
        movie_title,
        movie_year
    )

    edition_norm = " ".join(
        re.findall(
            r"[a-z0-9]+",
            str(edition or "").lower()
        )
    )

    suffix_norm = " ".join(suffix)
    combined = (edition_norm + " " + suffix_norm).strip()

    if re.search(
        r"\bextended(?:\s+(?:cut|edition|version))?\b",
        combined
    ):
        return True

    if re.search(
        r"\b(?:limited|special)\s+edition\b",
        combined
    ):
        return True

    if re.search(
        r"\bdirector(?:s|\s+s)?\s+cut\b",
        combined
    ):
        return True

    # Explicit edition metadata saying DC is authoritative.
    if "dc" in edition_norm.split():
        return True

    # Standalone DC is commonly placed immediately after title/year.
    # Restrict it to the first few suffix tokens to avoid treating a release
    # group ending in "-DC" as an edition.
    if "dc" in suffix[:4]:
        return True

    return False


def radarr_cut_replacement_allowed(item, release):
    """
    If the CURRENT library file is a protected non-theatrical cut, the
    replacement must explicitly advertise a protected cut as well.
    """
    movie_file = item.get("movie_file") or {}
    movie_title = item.get("title") or ""
    movie_year = item.get("year") or 0

    current_text = str(
        movie_file.get("relativePath")
        or movie_file.get("path")
        or ""
    )

    current_edition = str(
        movie_file.get("edition")
        or ""
    )

    if not radarr_protected_cut(
        current_text,
        movie_title,
        movie_year,
        current_edition,
    ):
        return True

    candidate_title = str(
        release.get("title")
        or ""
    )

    candidate_edition = str(
        release.get("edition")
        or ""
    )

    return radarr_protected_cut(
        candidate_title,
        movie_title,
        movie_year,
        candidate_edition,
    )


def evaluate_release(item, release, state):
    title = release.get("title") or ""

    if not release_matches_movie(item, release):
        return None, "movie identity mismatch"

    if dangerous_release_title(title):
        return None, "dangerous"

    if not radarr_rejections_ok(release):
        return None, "radarr rejection"

    # EDITION SAFETY RULE:
    # An ordinary/theatrical release may never replace an existing
    # Extended / Limited Edition / Special Edition / Director's Cut / DC movie.
    if not radarr_cut_replacement_allowed(item, release):
        print(
            "    CUT RULE: REJECT theatrical/ordinary candidate "
            "for protected Extended/Limited/Special/Director's Cut/DC current file",
            flush=True
        )
        return None, "theatrical cannot replace protected cut"

    if release_recently_attempted(state, release):
        return None, "recently attempted"

    seeders = release.get("seeders")
    try:
        seeders = int(seeders)
    except (TypeError, ValueError):
        return None, "unknown seeders"

    if seeders < MIN_SEEDERS:
        return None, "not enough seeders"

    candidate_resolution = candidate_resolution_from_release(release)
    if not candidate_resolution:
        return None, "unknown resolution"

    current_resolution = item["resolution"]

    # Never downgrade resolution.
    if candidate_resolution < current_resolution:
        return None, "resolution downgrade"

    # Higher resolution is only allowed toward the UHD target.
    if candidate_resolution > current_resolution:
        if item["target_resolution"] != 2160 or candidate_resolution != 2160:
            return None, "resolution upgrade not allowed"

    size_bytes = release.get("size") or 0
    try:
        size_bytes = int(size_bytes)
    except (TypeError, ValueError):
        return None, "unknown size"

    if size_bytes <= 0:
        return None, "unknown size"

    candidate_mib = mib(size_bytes)
    current_mib = item["size_mib"]

    # ABSOLUTE STORAGE INVARIANT:
    # A replacement is NEVER allowed to be larger than the file already in
    # the library, regardless of resolution upgrade, profile or monitored
    # state. 0% growth tolerance means equal size is the absolute ceiling;
    # the normal minimum-saving rule below is still stricter in practice.
    SIZE_EPSILON_MIB = 1e-6

    if candidate_mib > current_mib + SIZE_EPSILON_MIB:
        print(
            "    ABSOLUTE SIZE RULE: candidate %.1f MiB > current %.1f MiB | REJECT"
            % (candidate_mib, current_mib),
            flush=True
        )
        return None, "candidate larger than current file"

    # 1080p movie downloads should stay compact even when the current file is
    # very large. Prefer ranking still chooses smaller qualifying releases.
    MAX_1080P_REPLACEMENT_MIB = 10 * 1024

    if (
        candidate_resolution == 1080
        and candidate_mib > MAX_1080P_REPLACEMENT_MIB + SIZE_EPSILON_MIB
    ):
        print(
            "    1080P SIZE CEILING: %.2f GiB > 10.00 GiB | REJECT"
            % (candidate_mib / 1024.0),
            flush=True
        )
        return None, "1080p candidate above 10 GiB ceiling"

    saving = ((current_mib - candidate_mib) / current_mib) * 100.0

    # Storage-first policy applies to EVERY replacement, including 1080p -> 2160p.
    # A candidate must save meaningful space, but an extreme reduction is rejected
    # as a compression/quality-risk guardrail.
    # Tiny tolerance prevents floating-point conversion noise from rejecting
    # a candidate that is mathematically exactly on the configured boundary.
    SAVING_EPSILON = 1e-6

    if saving < MIN_SAVING_PERCENT - SAVING_EPSILON:
        print(
            "    SIZE RULE: %.3f%% saving | allowed %.1f%%-%.1f%% | REJECT: below minimum"
            % (saving, MIN_SAVING_PERCENT, MAX_SAVING_PERCENT),
            flush=True
        )
        return None, "candidate does not save enough space"

    if saving > MAX_SAVING_PERCENT + SAVING_EPSILON:
        print(
            "    SIZE RULE: %.3f%% saving | allowed %.1f%%-%.1f%% | REJECT: above maximum"
            % (saving, MIN_SAVING_PERCENT, MAX_SAVING_PERCENT),
            flush=True
        )
        return None, "candidate saves too much space (quality-risk guardrail)"

    print(
        "    SIZE RULE: %.3f%% saving | allowed %.1f%%-%.1f%% | PASS"
        % (saving, MIN_SAVING_PERCENT, MAX_SAVING_PERCENT),
        flush=True
    )

    candidate_codec = codec_from_text(title)

    # HARD COMPATIBILITY RULE:
    # AV1 replacements are disabled because the configured playback
    # environment is not guaranteed to support AV1.
    if candidate_codec == "av1":
        print("    CODEC RULE: AV1 | REJECT: AV1 not allowed", flush=True)
        return None, "AV1 not allowed"

    candidate_channels = audio_channels_from_text(title)
    candidate_atmos = radarr_candidate_atmos(title)
    candidate_dr = radarr_candidate_dynamic_range(title)

    # 4K Dolby Vision must explicitly include HDR fallback.
    # Size safety is handled by the relative MIN/MAX saving window above,
    # rather than a fixed GiB range that cannot scale with the current file.
    if candidate_resolution == 2160 and candidate_dr == "DV_ONLY":
        return None, "4K DV without HDR fallback"

    if not radarr_dynamic_range_allowed(item["dynamic_range"], candidate_dr):
        return None, "dynamic range protection"

    if not radarr_audio_allowed(
        item["audio_channels"],
        item["atmos"],
        candidate_channels,
        candidate_atmos
    ):
        return None, "audio protection"

    return {
        "release": release,
        "title": title,
        "resolution": candidate_resolution,
        "size_bytes": size_bytes,
        "size_mib": candidate_mib,
        "saving_percent": saving,
        "codec": candidate_codec,
        "audio_channels": candidate_channels,
        "atmos": candidate_atmos,
        "dynamic_range": candidate_dr,
        "seeders": seeders,
        "indexer": str(release.get("indexer") or ""),
    }, None


def choose_best(item, releases, state):
    accepted = []

    for release in releases:
        choice, reason = evaluate_release(item, release, state)
        if choice:
            accepted.append(choice)

    if not accepted:
        return None

    current_res = item["resolution"]

    # UHD profile: a valid higher-resolution candidate wins over
    # same-resolution storage optimization.
    higher = [x for x in accepted if x["resolution"] > current_res]
    pool = higher if higher else [
        x for x in accepted if x["resolution"] == current_res
    ]

    if not pool:
        return None

    # All candidates in this pool have already passed the hard safety gates.
    #
    # Preference order:
    #   1. Dynamic range: DV+HDR > HDR > SDR/unknown
    #   2. Atmos
    #   3. Audio channel count
    #   4. Smaller file
    #   5. x265/HEVC
    #   6. More seeders
    #
    # Hard protections still prevent losing existing HDR/DV, Atmos or
    # channel count. These preferences only rank already-safe candidates.
    dr_rank = {
        "DV_HDR": 3,
        "HDR": 2,
        "SDR_UNKNOWN": 1
    }

    def indexer_rank(choice):
        name = (choice.get("indexer") or "").lower()

        # Prefer TorrentLeech among candidates that already passed every
        # optimizer safety gate. Public indexers remain valid fallbacks.
        if "torrentleech" in name:
            return 0

        return 1

    pool.sort(key=lambda x: (
        -dr_rank.get(x.get("dynamic_range", "SDR_UNKNOWN"), 0),
        -int(bool(x.get("atmos", False))),
        -(x.get("audio_channels") or 0),
        indexer_rank(x),
        x["size_bytes"],
        0 if x["codec"] == "x265" else 1,
        -x["seeders"],
        x["title"].lower()
    ))

    return pool[0]


def describe_item(number, item):
    print(
        "%2d. %s (%s)" %
        (
            number,
            item.get("title", "Unknown"),
            item.get("year", "?")
        )
    )

    print(
        "    Current: %sp | %s | %.0f MiB | %.1fch | Atmos=%s | DR=%s" %
        (
            item.get("resolution", 0),
            item.get("codec") or "unknown",
            item.get("size_mib", 0),
            item.get("audio_channels", 0),
            item.get("atmos", False),
            item.get("dynamic_range", "SDR_UNKNOWN")
        )
    )

    print(
        "    Target: %sp | profile %s" %
        (
            item.get("target_resolution", 0),
            item.get("profile_id", "?")
        )
    )


def describe_choice(choice):
    print(
        "    FOUND: %s" %
        choice.get("title", choice["release"].get("title", "Unknown"))
    )

    print(
        "    New: %sp | %s | %.0f MiB | %.1fch | Atmos=%s | DR=%s | Seeders=%s" %
        (
            choice.get("resolution", 0),
            choice.get("codec") or "unknown",
            choice.get("size_mib", 0),
            choice.get("audio_channels") or 0,
            choice.get("atmos", False),
            choice.get("dynamic_range", "SDR_UNKNOWN"),
            choice.get("seeders", "?")
        )
    )

    if choice.get("saving_percent") is not None:
        print(
            "    Saving: %.1f%%" %
            choice["saving_percent"]
        )



# ============================================================
# RADARR MEDIA PROTECTION
# ============================================================

def radarr_current_dynamic_range(movie_file):
    media=(movie_file or {}).get("mediaInfo") or {}

    dr=str(media.get("videoDynamicRange") or "").lower()
    typ=str(media.get("videoDynamicRangeType") or "").lower()

    extra=" ".join([
        str((movie_file or {}).get("sceneName") or ""),
        str((movie_file or {}).get("relativePath") or "")
    ]).lower()

    if (
        "dolby vision" in typ
        or "dovi" in typ
        or " dv" in (" " + typ)
        or "dv " in (typ + " ")
        or "dolby vision" in extra
        or "dovi" in extra
    ):
        return "DV_HDR"

    if any(x in dr or x in typ or x in extra for x in (
        "hdr10+",
        "hdr10plus",
        "hdr10",
        "hdr",
        "hlg"
    )):
        return "HDR"

    return "SDR_UNKNOWN"


def radarr_current_atmos(movie_file):
    media=(movie_file or {}).get("mediaInfo") or {}

    text=" ".join([
        str(media.get("audioCodec") or ""),
        str((movie_file or {}).get("sceneName") or ""),
        str((movie_file or {}).get("relativePath") or "")
    ]).lower()

    return "atmos" in text


def radarr_current_audio_channels(movie_file):
    media=(movie_file or {}).get("mediaInfo") or {}

    try:
        return float(media.get("audioChannels") or 0)
    except (TypeError, ValueError):
        return 0.0


def radarr_candidate_atmos(title):
    return "atmos" in str(title or "").lower()


def radarr_candidate_dynamic_range(title):
    t=str(title or "").lower()

    dv=(
        "dolby vision" in t
        or "dovi" in t
        or bool(re.search(
            r"(?<![a-z0-9])dv(?![a-z0-9])",
            t
        ))
    )

    hdr=any(x in t for x in (
        "hdr10+",
        "hdr10plus",
        "hdr10",
        "hdr",
        "hlg"
    ))

    if dv and hdr:
        return "DV_HDR"

    if dv:
        return "DV_ONLY"

    if hdr:
        return "HDR"

    return "SDR_UNKNOWN"


def radarr_dynamic_range_allowed(current, candidate):
    # DV without HDR fallback is never accepted.
    if candidate == "DV_ONLY":
        return False

    # Existing DV+HDR must remain DV+HDR.
    if current == "DV_HDR":
        return candidate == "DV_HDR"

    # Existing HDR may remain HDR or become DV+HDR.
    if current == "HDR":
        return candidate in ("HDR", "DV_HDR")

    # SDR/unknown may move to HDR/DV or remain SDR/unknown.
    return candidate in (
        "SDR_UNKNOWN",
        "HDR",
        "DV_HDR"
    )


def radarr_audio_allowed(
    current_channels,
    current_atmos,
    candidate_channels,
    candidate_atmos
):
    # Known 5.1/7.1 can never become lower-channel audio.
    if current_channels >= 5.0:
        if (
            not candidate_channels
            or candidate_channels < current_channels
        ):
            return False

    # Atmos may never disappear.
    if current_atmos and not candidate_atmos:
        return False

    return True




def main():
    state = load_state()

    # Optional internal mode used by the persistent UI worker when an
    # optimizer-owned torrent dies.  It searches ONE exact movie only and
    # never advances the normal persistent A-Z cursor.
    try:
        TARGET_MOVIE_ID = int(
            os.environ.get("SMART_OPTIMIZER_MOVIE_ID", "0") or 0
        )
    except (TypeError, ValueError):
        TARGET_MOVIE_ID = 0

    MANUAL_TARGET_MODE = (
        str(os.environ.get("SMART_OPTIMIZER_MANUAL_TARGET", "0"))
        .strip()
        .lower()
        in ("1", "true", "yes", "on")
    )

    if LIVE:
        clean_old_attempts(state)

    print()
    print("=" * 68)
    print("RADARR SMART OPTIMIZER")
    print("=" * 68)

    if LIVE:
        print("MODE: LIVE")
    else:
        print("MODE: DRY RUN -- NO RELEASES WILL BE GRABBED")

    print("Daily interactive-search budget:", DAILY_SEARCH_BUDGET + DAILY_EXTRA_BUDGET, "(base %d + today override %d)" % (DAILY_SEARCH_BUDGET, DAILY_EXTRA_BUDGET))
    print("Allowed saving window: %.1f%% to %.1f%%" % (MIN_SAVING_PERCENT, MAX_SAVING_PERCENT))
    print()

    used = searches_used_today(state)

    # Maximum interactive searches in one execution.
    PER_RUN_SEARCH_BUDGET = max(1, SEARCHES_PER_RUN)

    if MANUAL_TARGET_MODE:
        # Exact Manual Optimizer runs are independent of the scheduled
        # persistent daily-search allowance.
        remaining = PER_RUN_SEARCH_BUDGET
    elif LIVE:
        remaining = min(
            PER_RUN_SEARCH_BUDGET,
            max(0, DAILY_SEARCH_BUDGET + DAILY_EXTRA_BUDGET - used)
        )
    else:
        # Dry run does NOT consume persistent budget.
        remaining = min(
            PER_RUN_SEARCH_BUDGET,
            DAILY_SEARCH_BUDGET
        )

    print(
        "Persistent searches already used today:",
        used
    )

    print(
        "Searches available this run:",
        remaining
    )

    if remaining <= 0 and not MANUAL_TARGET_MODE:
        print()
        print("Daily search budget exhausted.")
        print("Nothing to do.")
        return

    print()
    print("Reading Radarr queue...")

    queued_ids = active_movie_ids()
    movies = get("/movie")
    movies_by_id = {int(m["id"]): m for m in movies if m.get("id")}

    print("Movies currently represented in queue:", len(queued_ids))
    print("Movies in Radarr:", len(movies))
    print("Movies with files:", sum(1 for m in movies if m.get("hasFile")))

    if not state.get("queue_initialized") or not state.get("movie_queue"):
        print("Creating persistent A-Z movie queue...", flush=True)
        initialize_movie_queue(state, movies)

    append_new_movies(state, movies)

    print("Persistent optimizer queue:", len(state.get("movie_queue", [])))
    print("Saved queue cursor:", state.get("movie_cursor", 0))
    print()

    searches = 0
    grabs = 0
    no_match = 0
    errors = 0

    number = 0
    targeted_done = False

    while searches < remaining and (TARGET_GRABS <= 0 or grabs < TARGET_GRABS):

        if TARGET_MOVIE_ID:
            if targeted_done:
                break

            targeted_done = True

            movie = movies_by_id.get(TARGET_MOVIE_ID)

            if not movie:
                print(
                    "TARGETED RETRY: movie ID %d not found."
                    % TARGET_MOVIE_ID,
                    flush=True
                )
                break

            if TARGET_MOVIE_ID in optimizer_excluded_ids("radarr"):
                print(
                    "TARGETED RETRY: movie ID %d is excluded."
                    % TARGET_MOVIE_ID,
                    flush=True
                )
                break

            # Targeted retry deliberately bypasses only the normal
            # search-cycle cooldown and A-Z cursor. All media/release
            # safety checks still happen through choose_best() and
            # evaluate_release().
            targeted_state = dict(state)
            targeted_state["movies"] = dict(state.get("movies", {}))
            targeted_state["auto_processed_movie_ids"] = list(
                state.get("auto_processed_movie_ids", [])
            )

            # Only an explicit Manual Optimizer request may bypass the
            # permanent one-shot gate. Automatic/internal targeted retries
            # remain blocked and require the user to retry manually.
            if MANUAL_TARGET_MODE:
                targeted_state["movies"].pop(str(TARGET_MOVIE_ID), None)
                targeted_state["auto_processed_movie_ids"] = [
                    x
                    for x in targeted_state["auto_processed_movie_ids"]
                    if int(x) != TARGET_MOVIE_ID
                ]

            item = movie_item(
                movie,
                targeted_state,
                set()
            )

            if item is None:
                print(
                    "TARGETED RETRY: movie is not optimizer-eligible.",
                    flush=True
                )
                break

            print(
                "TARGETED RETRY: restricting run to movie ID %d"
                % TARGET_MOVIE_ID,
                flush=True
            )

        else:
            item = next_movie_item(state, movies_by_id, queued_ids)

            if item is None:
                print(
                    "Reached the genuine end of the persistent movie queue.",
                    flush=True
                )
                break

        number += 1
        print("-" * 68)

        describe_item(number, item)
        print("    NOW CHECKING: %s (%s)" % (item.get("title", "Unknown"), item.get("year", "?")), flush=True)

        movie_id = item["movie_id"]

        try:
            # THIS is the expensive interactive indexer search.
            releases = get(
                "/release?movieId=%d"
                % movie_id
            )

            searches += 1
            print("    SEARCH PROGRESS: %d / %d" % (searches, remaining), flush=True)

            if LIVE:
                if not MANUAL_TARGET_MODE:
                    increment_search_count(state)

                # Any successful interactive search permanently closes this
                # movie to future AUTOMATIC optimizer passes. Manual Optimizer
                # still bypasses this gate explicitly.
                mark_movie_searched(
                    state,
                    movie_id
                )

                # Manual targeted runs do not consume the scheduled counter,
                # but they do keep the movie closed to future automatic runs.
                save_state(state)

        except Exception as e:
            errors += 1
            print("    SEARCH ERROR:", e)
            print()
            continue

        choice = choose_best(
            item,
            releases,
            state
        )

        if not choice:
            no_match += 1
            print("    KEEP CURRENT: no qualifying replacement.")
            print()
            continue

        describe_choice(choice)

        if not LIVE:
            print("    DRY RUN: WOULD GRAB")
            print()
            continue

        # ----------------------------------------------------
        # SAFETY CHECK AGAIN immediately before grabbing.
        # ----------------------------------------------------

        try:
            fresh_queue = active_movie_ids()

            if movie_id in fresh_queue:
                print(
                    "    SKIP: movie entered Radarr queue "
                    "while we were evaluating it."
                )
                print()
                continue

        except Exception as e:
            print(
                "    SKIP: could not perform final queue safety check:",
                e
            )
            print()
            continue

        try:
            # The ONLY Radarr write operation used to initiate
            # replacement.
            #
            # NO DELETE.
            # NO filesystem manipulation.
            # NO direct Deluge manipulation.
            # Snapshot the exact existing Radarr file BEFORE this
            # optimizer-owned replacement can be imported.
            #
            # Candidate selection has already passed Smart Optimizer's
            # saving-window, resolution, HDR/DV, codec, audio and other
            # safety rules.  This record does not approve anything new;
            # it only lets the persistent UI worker identify OUR grab.
            current_movie = get("/movie/%d" % movie_id)

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
            old_file_size = int(current_file.get("size") or 0)
            old_relative_path = str(
                current_file.get("relativePath") or ""
            ).strip()

            if (
                not old_file_id
                or old_file_size <= 0
                or not old_relative_path
            ):
                raise RuntimeError(
                    "Cannot record optimizer replacement: "
                    "current Radarr movie file is unavailable"
                )

            approved_release = choice["release"]
            approved_title = str(approved_release.get("title") or "")
            approved_size = int(approved_release.get("size") or 0)
            source_indexer = str(
                approved_release.get("indexer") or ""
            ).strip()
            tracker_policy = tracker_policy_from_indexer(
                source_indexer
            )

            # Snapshot exact queue IDs BEFORE sending the release to Radarr.
            # If a release has no hash, a newly appeared queue row is the only
            # fallback ownership proof we allow.
            pre_grab_queue_ids = sorted(queue_ids_for_movie(movie_id))

            state.setdefault("pending_replacements", {})
            state.setdefault("tracker_jobs", {})

            pending_key = str(movie_id)

            if state["tracker_jobs"].get(pending_key):
                raise RuntimeError(
                    "Tracker retention job already exists for movie %d"
                    % int(movie_id)
                )

            previous_pending = (
                state["pending_replacements"].get(pending_key) or {}
            )
            dead_retry_count = int(
                previous_pending.get("dead_retry_count") or 0
            )

            # Persist intent BEFORE Radarr receives the release. This closes
            # the old crash window where Radarr could accept a torrent before
            # Smart Optimizer had recorded ownership.
            state["pending_replacements"][pending_key] = {
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

            try:
                post(
                    "/release",
                    choice["release"]
                )
            except Exception:
                # Radarr did not successfully accept the release call.
                # Remove only our pre-grab intent record. Existing movie file
                # is untouched.
                state.setdefault("pending_replacements", {}).pop(
                    pending_key,
                    None
                )
                state.setdefault("tracker_jobs", {}).pop(
                    pending_key,
                    None
                )
                save_state(state)
                raise

            state["pending_replacements"][pending_key]["status"] = "grabbed"
            state["pending_replacements"][pending_key]["grabbed_at"] = now_ts()
            state["tracker_jobs"][pending_key]["status"] = "grabbed"
            state["tracker_jobs"][pending_key]["grabbed_at"] = now_ts()

            # Bind this optimizer-owned grab to Radarr's exact queue/download
            # identity. This makes source-tier-blocked imports fully automatic
            # without relying on release-title equality.
            ownership = bind_grabbed_download(
                movie_id,
                approved_release,
                pre_grab_queue_ids
            )

            if ownership:
                state["pending_replacements"][pending_key]["download_id"] = (
                    ownership["download_id"]
                )
                state["pending_replacements"][pending_key]["queue_id"] = (
                    ownership["queue_id"]
                )
                state["pending_replacements"][pending_key]["bound_at"] = now_ts()
                state["tracker_jobs"][pending_key]["download_id"] = (
                    ownership["download_id"]
                )
                state["tracker_jobs"][pending_key]["queue_id"] = (
                    ownership["queue_id"]
                )
                state["tracker_jobs"][pending_key]["bound_at"] = now_ts()

                print(
                    "    OWNERSHIP BOUND:",
                    ownership["download_id"]
                )
            else:
                print(
                    "    OWNERSHIP PENDING: background worker will bind "
                    "the Radarr queue entry."
                )

            # Mark it attempted immediately after Radarr accepted the grab.
            # This also prevents a dead torrent from being selected again by
            # a subsequent targeted retry.
            mark_release_attempted(
                state,
                approved_release
            )

            save_state(state)

            grabs += 1

            if LIVE and TARGET_GRABS > 0:
                print(
                    "    UPGRADE GRABBED: %d / %d"
                    % (grabs, TARGET_GRABS),
                    flush=True
                )

            print("    LIVE: RELEASE SENT TO RADARR")
            print(
                "    Existing movie remains until Radarr "
                "successfully downloads and imports replacement."
            )


        except Exception as e:
            errors += 1
            print("    GRAB ERROR:", e)

        print()

    print("=" * 68)
    print("SUMMARY")
    print("=" * 68)

    print("Interactive searches this run:", searches)

    if LIVE:
        print("Releases sent to Radarr:", grabs)
        print(
            "Persistent searches used today:",
            searches_used_today(state)
        )
    else:
        print("Downloads started: 0")
        print("State changes: 0")

    print("No qualifying replacement:", no_match)
    print("Errors:", errors)

    if not LIVE:
        print()
        print(
            "DRY RUN COMPLETE -- no releases were grabbed and "
            "no persistent cooldown changes were made."
        )


if __name__ == "__main__":
    main()
