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
# SONARR SMART OPTIMIZER
#
# Default = DRY RUN
# Live    = --live
#
# IMPORTANT:
# - This script NEVER calls Sonarr DELETE endpoints directly
# - In live mode Sonarr may replace an existing file after importing a selected release
# - NEVER touches Deluge directly
# - Sonarr performs normal Completed Download Handling/import
# - Search budgets are configurable; defaults are conservative for scheduled use
# ============================================================

# ============================================================
# QUICK SETUP
# ============================================================
# API keys are intentionally NOT stored in this source file.
# Set SONARR_KEY in your environment or use a protected wrapper/key file.
# Check the URL if Sonarr is not on the same machine, then review
# SEARCHES_PER_RUN plus NORMAL_PROFILE_ID and UHD_PROFILE_ID below.
SONARR_URL_DEFAULT = "http://127.0.0.1:8989"
SEARCHES_PER_RUN = 10

SONARR_URL = os.environ.get("SONARR_URL", SONARR_URL_DEFAULT).rstrip("/")
API_KEY = os.environ.get("SONARR_KEY", "").strip()
SEARCHES_PER_RUN = int(os.environ.get("SONARR_SEARCHES_PER_RUN", SEARCHES_PER_RUN))

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.environ.get(
    "SONARR_OPTIMIZER_STATE",
    os.path.join(SCRIPT_DIR, "sonarr-smart-optimizer-state.json")
)

NORMAL_PROFILE_ID = 4
UHD_PROFILE_ID = 5

DAILY_SEARCH_BUDGET = 400
MIN_SEEDERS = 1
MIN_SAVING_PERCENT = float(os.environ.get("SONARR_MIN_SAVING_PERCENT", "5.0"))
MAX_SAVING_PERCENT = float(os.environ.get("SONARR_MAX_SAVING_PERCENT", "50.0"))
CONTROL_FILE = os.environ.get("SMART_OPTIMIZER_CONTROL", os.path.join(SCRIPT_DIR, "smart-optimizer-control.json"))

def load_runtime_controls():
    controls = {}
    try:
        with open(CONTROL_FILE, "r", encoding="utf-8") as f:
            controls = (json.load(f) or {}).get("sonarr", {})
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

# Prevent one large series from consuming the whole daily budget.
MAX_SEARCHES_PER_SERIES_PER_RUN = 3

LIVE = "--live" in sys.argv

if not API_KEY:
    print("ERROR: Sonarr API key is not configured.")
    print()
    print("Set SONARR_KEY in your environment or protected wrapper/key file.")
    print("Then run: python3 sonarr-smart-optimizer.py")
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


def api(method, path, data=None, timeout=120):
    url = SONARR_URL + "/api/v3" + path

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
        with urllib.request.urlopen(req, timeout=timeout) as response:
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


def get(path, timeout=120):
    return api("GET", path, timeout=timeout)


def post(path, data):
    return api("POST", path, data)


# ============================================================
# STATE
# ============================================================

def blank_state():
    return {
        "version": 1,
        "episodes": {},
        "attempted_releases": {},
        "daily": {},
        "work_queue": [],
        "work_cursor": 0,
        "known_series_ids": [],
        "series_queue": [],
        "series_cursor": 0,
        "queue_initialized": False
    }


def load_state():
    if not os.path.exists(STATE_FILE):
        return blank_state()

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)

        state.setdefault("version", 1)
        state.setdefault("episodes", {})
        state.setdefault("attempted_releases", {})
        state.setdefault("daily", {})
        state.setdefault("work_queue", [])
        state.setdefault("work_cursor", 0)
        state.setdefault("known_series_ids", [])
        state.setdefault("series_queue", [])
        state.setdefault("series_cursor", 0)
        state.setdefault("queue_initialized", False)

        return state

    except Exception as e:
        print("WARNING: Could not read state file:")
        print(" ", e)
        print("Using empty state for this run.")
        return blank_state()


def save_state(state):
    if not LIVE:
        return

    # STATE_FILE is commonly a single-file Docker bind mount.
    # Replacing its inode with os.replace() can fail with EBUSY.
    # Write the mounted file in-place and fsync it instead.
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


def mark_episode_searched(state, episode_id):
    key = str(episode_id)
    state["episodes"].setdefault(key, {})
    entry = state["episodes"][key]
    entry["last_search"] = now_ts()
    entry["search_cycles"] = min(2, int(entry.get("search_cycles", 0)) + 1)


def release_key(release):
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

    ts = state.get("attempted_releases", {}).get(key)

    if not ts:
        return False

    return age_days(ts) < ATTEMPT_COOLDOWN_DAYS


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
        (7.1, r"\b7[\s._-]?1\b"),
        (5.1, r"\b5[\s._-]?1\b"),
        (2.1, r"\b2[\s._-]?1\b"),
        (2.0, r"\b2[\s._-]?0\b"),
        (1.0, r"\b1[\s._-]?0\b"),
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


def dynamic_range_from_text(text):
    """
    Conservative release-title classification.

    Returns:
      SDR_UNKNOWN
      HDR
      DV_ONLY
      DV_HDR

    IMPORTANT:
    DV-only is never acceptable.
    Dolby Vision must explicitly also advertise HDR/HDR10/HDR10+.
    """
    text = (text or "").lower()

    has_dv = bool(
        re.search(
            r"\b(dv|dovi|dolby[\s._-]?vision)\b",
            text
        )
    )

    has_hdr = bool(
        re.search(
            r"\b(hdr10\+?|hdr|hlg)\b",
            text
        )
    )

    if has_dv and has_hdr:
        return "DV_HDR"

    if has_dv:
        return "DV_ONLY"

    if has_hdr:
        return "HDR"

    return "SDR_UNKNOWN"


def dynamic_range_allowed(existing_hdr, candidate_range):
    """
    Dynamic-range replacement policy.

    Candidate SDR_UNKNOWN means the release title does not explicitly
    advertise HDR/DV. For selection purposes it is allowed exactly like
    ordinary SDR UNLESS the existing file is positively known to be HDR.

    Rules:
      existing SDR/unknown -> SDR_UNKNOWN : ALLOW
      existing SDR/unknown -> HDR         : ALLOW
      existing SDR/unknown -> DV_HDR      : ALLOW

      existing HDR         -> SDR_UNKNOWN : BLOCK
      existing HDR         -> HDR         : ALLOW
      existing HDR         -> DV_HDR      : ALLOW

      DV_ONLY is ALWAYS blocked.
    """

    if candidate_range == "DV_ONLY":
        return False

    if existing_hdr and candidate_range == "SDR_UNKNOWN":
        return False

    return True



def hdr_from_media_info(media):
    if not media:
        return False

    pieces = []

    for key in (
        "videoDynamicRange",
        "videoDynamicRangeType",
        "videoCodec",
        "videoProfile"
    ):
        value = media.get(key)

        if value:
            pieces.append(str(value))

    text = " ".join(pieces).lower()

    return bool(
        re.search(
            r"(dolby|dovi|\bdv\b|hdr|hlg|pq)",
            text
        )
    )


def current_audio_channels(media):
    if not media:
        return None

    value = media.get("audioChannels")

    try:
        if value is not None:
            return float(value)
    except Exception:
        pass

    return None


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

def active_episode_ids():
    ids = set()

    page = 1

    while True:
        path = (
            "/queue?page=%d&pageSize=100"
            "&includeUnknownSeriesItems=true"
        ) % page

        data = get(path)

        if not data:
            break

        records = data.get("records", [])

        for item in records:
            episode_id = item.get("episodeId")

            if episode_id:
                ids.add(int(episode_id))

        total = int(data.get("totalRecords", len(records)))

        if page * 100 >= total:
            break

        page += 1

    return ids


# ============================================================
# LOCAL LIBRARY CANDIDATES
# ============================================================

def priority_score(item):
    """
    Higher = search earlier.

    IMPORTANT:
    This only decides WHICH existing episodes deserve one of
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
    # PROFILE 5 / 4K-PREFERRED SERIES
    # --------------------------------------------------------
    if item["profile_id"] == UHD_PROFILE_ID:

        # Missing target resolution is important, but don't give
        # every 1080p episode an identical gigantic score.
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

def _queue_entry(series, ep):
    return {
        "series_id": int(series["id"]),
        "series_title": series.get("title", ""),
        "episode_id": int(ep["id"]),
        "season": int(ep.get("seasonNumber", 0)),
        "episode": int(ep.get("episodeNumber", 0)),
    }


def _episode_sort_key(ep):
    return (int(ep.get("seasonNumber", 0)), int(ep.get("episodeNumber", 0)), int(ep.get("id", 0)))


def initialize_work_queue(state):
    """Save the complete A-Z SERIES order immediately; load episodes only when reached."""
    series_list = get("/series")
    ordered = sorted(series_list, key=lambda s: ((s.get("title") or "").casefold(), int(s.get("id", 0))))
    state["series_queue"] = [
        {"series_id": int(s["id"]), "series_title": s.get("title", "")}
        for s in ordered if s.get("id")
    ]
    state["series_cursor"] = 0
    state["work_queue"] = []
    state["work_cursor"] = 0
    state["known_series_ids"] = [x["series_id"] for x in state["series_queue"]]
    state["queue_initialized"] = True
    save_state(state)
    print("PERMANENT A-Z SERIES QUEUE READY:", len(state["series_queue"]), "series", flush=True)


def append_new_series(state):
    """New Sonarr series go to the bottom; normal Sonarr downloading is untouched."""
    series_list = get("/series")
    known = set(int(x) for x in state.get("known_series_ids", []))
    added = 0
    for series in series_list:
        sid = int(series.get("id", 0) or 0)
        if not sid or sid in known:
            continue
        state.setdefault("series_queue", []).append({
            "series_id": sid,
            "series_title": series.get("title", "")
        })
        known.add(sid)
        added += 1
        print("APPENDED NEW SERIES TO BOTTOM:", series.get("title"), flush=True)
    state["known_series_ids"] = sorted(known)
    if added:
        save_state(state)
    return added


def load_next_series_episodes(state):
    """Expand only the next series into episode work when the cursor reaches it."""
    sq = state.get("series_queue", [])
    sc = int(state.get("series_cursor", 0))
    if sc >= len(sq):
        return False

    sref = sq[sc]
    sid = int(sref["series_id"])
    try:
        episodes = get("/episode?seriesId=%d" % sid)
    except Exception as e:
        print("WARNING: Could not load:", sref.get("series_title"), e, flush=True)
        return False

    entries = [
        {
            "series_id": sid,
            "series_title": sref.get("series_title", ""),
            "episode_id": int(ep["id"]),
            "season": int(ep.get("seasonNumber", 0)),
            "episode": int(ep.get("episodeNumber", 0)),
        }
        for ep in sorted(episodes, key=_episode_sort_key)
        if ep.get("hasFile") and ep.get("episodeFileId")
    ]

    state.setdefault("work_queue", []).extend(entries)
    state["series_cursor"] = sc + 1
    save_state(state)
    print("LOADED NEXT SERIES:", sref.get("series_title"), "·", len(entries), "episodes", flush=True)
    return True


def item_from_queue_entry(entry, queued_ids, state):
    """Load metadata only for the next queued episode, never the whole library."""
    episode_id = int(entry["episode_id"])
    if episode_id in queued_ids:
        return None
    hist = state.get("episodes", {}).get(str(episode_id), {})
    cycles = int(hist.get("search_cycles", 0))
    last_search = hist.get("last_search")
    if cycles >= 2 or (cycles == 1 and last_search and age_days(last_search) < 180):
        return None
    try:
        series = get("/series/%d" % int(entry["series_id"]))
        ep = get("/episode/%d" % episode_id)
    except Exception as e:
        print("    SKIP metadata error:", entry.get("series_title"), "S%02dE%02d" % (entry.get("season",0),entry.get("episode",0)), e, flush=True)
        return None
    if not ep.get("hasFile") or not ep.get("episodeFileId"):
        return None
    profile_id = int(series.get("qualityProfileId", 0))
    if profile_id not in (NORMAL_PROFILE_ID, UHD_PROFILE_ID):
        return None
    try:
        file_obj = get("/episodefile/%d" % int(ep["episodeFileId"]))
    except Exception as e:
        print("    SKIP file metadata error:", e, flush=True)
        return None
    resolution = file_resolution(file_obj)
    if not resolution:
        return None
    media = file_obj.get("mediaInfo") or {}
    target = 2160 if profile_id == UHD_PROFILE_ID else 1080
    return {
        "series_id": int(series["id"]),
        "series_title": series.get("title", entry.get("series_title", "")),
        "episode_id": episode_id,
        "season": int(ep.get("seasonNumber", entry.get("season", 0))),
        "episode": int(ep.get("episodeNumber", entry.get("episode", 0))),
        "episode_title": ep.get("title", ""),
        "profile_id": profile_id,
        "target_resolution": target,
        "file": file_obj,
        "resolution": resolution,
        "size_mib": mib(file_obj.get("size", 0)),
        "codec": current_codec(file_obj),
        "audio_channels": current_audio_channels(media),
        "hdr": hdr_from_media_info(media),
        "cooldown_days": 180,
        "priority": 0,
    }


def next_work_items(state, queued_ids, limit):
    if not state.get("queue_initialized") or not state.get("series_queue"):
        print("Creating persistent A-Z series queue...", flush=True)
        initialize_work_queue(state)

    append_new_series(state)
    selected = []

    while len(selected) < limit:
        queue = state.get("work_queue", [])
        cursor = int(state.get("work_cursor", 0))

        if cursor >= len(queue):
            if not load_next_series_episodes(state):
                break
            continue

        entry = queue[cursor]
        state["work_cursor"] = cursor + 1
        if LIVE:
            save_state(state)

        item = item_from_queue_entry(entry, queued_ids, state)
        if item is not None:
            selected.append(item)

    return selected


# ============================================================
# RELEASE EVALUATION
# ============================================================

def rejection_allowed(rejection):
    """
    We ONLY ignore Sonarr's cutoff rejection because the
    optimizer intentionally evaluates replacements beyond
    Sonarr's normal cutoff.

    Every other Sonarr rejection remains respected.
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

    return "existing file meets cutoff" in reason


def sonarr_rejections_ok(release):
    rejected = release.get("rejections") or []

    for rejection in rejected:
        if not rejection_allowed(rejection):
            return False

    return True


def candidate_resolution(release):
    return quality_resolution(release.get("quality"))


def evaluate_release(item, release, state):
    title = str(release.get("title", ""))

    # HARD SAFETY BLOCK:
    # Never grab executable/script payloads.
    if dangerous_release_title(title):
        return None

    if not sonarr_rejections_ok(release):
        return None

    if release_recently_attempted(state, release):
        return None

    # Require positive seeder evidence before grabbing.
    # Unknown, missing, invalid or zero seeders are rejected.
    seeders = release.get("seeders")

    try:
        seeders = int(seeders)
    except (TypeError, ValueError):
        return None

    if seeders < MIN_SEEDERS:
        return None

    new_res = candidate_resolution(release)

    if not new_res:
        return None

    old_res = item["resolution"]
    target = item["target_resolution"]

    # NEVER exceed profile target.
    if new_res > target:
        return None

    # NEVER resolution downgrade.
    if new_res < old_res:
        return None

    new_size = mib(release.get("size", 0))

    if new_size <= 0:
        return None

    codec = codec_from_text(title)
    candidate_audio = audio_channels_from_text(title)

    dynamic_range = dynamic_range_from_text(title)
    candidate_hdr = dynamic_range in ("HDR", "DV_HDR")

    # HARD RULE:
    # Dolby Vision without an explicit HDR fallback is rejected.
    if not dynamic_range_allowed(item["hdr"], dynamic_range):
        return None

    old_size = item["size_mib"]

    if old_size <= 0:
        return None

    saving = (
        (old_size - new_size)
        / old_size
        * 100.0
    )

    # Same-resolution replacements must save 5-50%.
    # The only size-growth exception is an explicit UHD-profile upgrade
    # from an existing 1080p file to a 2160p candidate: that candidate may
    # be the same size or at most 10% larger. Smaller 4K candidates remain
    # subject to the 50% maximum-saving guardrail.
    is_uhd_upgrade = (
        item["profile_id"] == UHD_PROFILE_ID
        and old_res == 1080
        and new_res == 2160
    )

    if is_uhd_upgrade:
        if saving < -10.0:
            return None
        if saving > MAX_SAVING_PERCENT:
            return None
    else:
        if saving < MIN_SAVING_PERCENT:
            return None
        if saving > MAX_SAVING_PERCENT:
            return None

    # Audio protection.
    #
    # If current file has known channel count, candidate must
    # ALSO tell us its channel count and it cannot be lower.
    old_audio = item["audio_channels"]

    if old_audio is not None:
        if candidate_audio is None:
            return None

        if candidate_audio < old_audio:
            return None

    # HDR/DV protection at BOTH 1080p and 2160p.
    #
    # Existing HDR/DV -> SDR/unknown = NEVER for space saving.
    #
    # Existing SDR -> HDR/DV is allowed.
    

    return {
        "release": release,
        "resolution": new_res,
        "size_mib": new_size,
        "codec": codec,
        "audio": candidate_audio,
        "hdr": candidate_hdr,
        "dynamic_range": dynamic_range,
        "saving_percent": saving,
        "reason": "smaller same-resolution file"
    }


def choose_best(item, releases, state):
    valid = []

    for release in releases:
        result = evaluate_release(
            item,
            release,
            state
        )

        if result:
            valid.append(result)

    if not valid:
        return None

    old_res = item["resolution"]

    higher = [
        x for x in valid
        if x["resolution"] > old_res
    ]

    if higher:
        # Higher resolution wins.
        #
        # Within that resolution:
        # x265 preferred, then smaller file.
        higher.sort(
            key=lambda x: (
                x["resolution"],
                1 if x["codec"] == "x265" else 0,
                -x["size_mib"]
            ),
            reverse=True
        )

        return higher[0]

    # Same resolution:
    #
    # Smaller file is the main purpose.
    # x265 breaks close/equal choices rather than allowing a
    # larger x265 to beat a smaller x264.
    valid.sort(
        key=lambda x: (
            x["size_mib"],
            0 if x["codec"] == "x265" else 1
        )
    )

    return valid[0]


# ============================================================
# OUTPUT
# ============================================================

def describe_item(number, item):
    audio = (
        "%.1f" % item["audio_channels"]
        if item["audio_channels"] is not None
        else "unknown"
    )

    print(
        "%2d. %s S%02dE%02d"
        % (
            number,
            item["series_title"],
            item["season"],
            item["episode"]
        )
    )

    print(
        "    Current: %dp | %s | %.0f MiB | %sch | HDR=%s"
        % (
            item["resolution"],
            item["codec"],
            item["size_mib"],
            audio,
            item["hdr"]
        )
    )

    print(
        "    Target: %dp | priority %.0f | cooldown %d days"
        % (
            item["target_resolution"],
            item["priority"],
            item["cooldown_days"]
        )
    )


def describe_choice(choice):
    release = choice["release"]

    audio = (
        "%.1f" % choice["audio"]
        if choice["audio"] is not None
        else "unknown"
    )

    print("    FOUND:", release.get("title", ""))

    print(
        "    New: %dp | %s | %.0f MiB | %sch | HDR=%s"
        % (
            choice["resolution"],
            choice["codec"],
            choice["size_mib"],
            audio,
            choice["hdr"]
        )
    )

    if choice["saving_percent"] is not None:
        print(
            "    Saving: %.1f%%"
            % choice["saving_percent"]
        )

    print(
        "    Indexer:",
        release.get("indexer", "unknown")
    )

    print(
        "    Seeders:",
        release.get("seeders", "unknown")
    )


# ============================================================
# MAIN
# ============================================================

def main():
    state = load_state()

    if LIVE:
        clean_old_attempts(state)

    print()
    print("=" * 68)
    print("SONARR SMART OPTIMIZER")
    print("=" * 68)

    if LIVE:
        print("MODE: LIVE")
    else:
        print("MODE: DRY RUN -- NO RELEASES WILL BE GRABBED")

    print("Daily interactive-search budget:", DAILY_SEARCH_BUDGET + DAILY_EXTRA_BUDGET, "(base %d + today override %d)" % (DAILY_SEARCH_BUDGET, DAILY_EXTRA_BUDGET))
    print("Same-resolution saving window: %.1f%% to %.1f%%" % (MIN_SAVING_PERCENT, MAX_SAVING_PERCENT))
    print("UHD-profile 1080p -> 2160p exception: candidate may be up to 10% larger")
    print()

    used = searches_used_today(state)

    # Maximum interactive searches in one execution.
    PER_RUN_SEARCH_BUDGET = max(1, SEARCHES_PER_RUN)

    if LIVE:
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

    if remaining <= 0:
        print()
        print("Daily search budget exhausted.")
        print("Nothing to do.")
        return

    print()
    print("Reading Sonarr queue...")

    queued_ids = active_episode_ids()

    print(
        "Episodes currently represented in queue:",
        len(queued_ids)
    )

    print()

    print("Loading persistent A-Z queue...")
    if not state.get("queue_initialized"):
        initialize_work_queue(state)
    append_new_series(state)

    target_searches = remaining
    searches = 0
    grabs = 0
    no_match = 0
    errors = 0
    number = 0

    # The requested count is the number of ACTUAL interactive searches.
    # Scheduled and manual runs consume the same persistent queue/cursor.
    # Ineligible queue entries may be skipped, but each /release lookup counts
    # exactly once toward this run's requested search quota.
    while searches < target_searches:
        actual_left = max(0, DAILY_SEARCH_BUDGET + DAILY_EXTRA_BUDGET - searches_used_today(state))
        if LIVE and actual_left <= 0:
            print("Daily interactive-search budget exhausted.", flush=True)
            break

        selected = next_work_items(state, queued_ids, 1)
        if not selected:
            break

        item = selected[0]
        number += 1
        print("-" * 68)
        describe_item(number, item)
        episode_id = item["episode_id"]

        try:
            print("    SEARCHING SONARR NOW...", flush=True)
            releases = get("/release?episodeId=%d" % episode_id, timeout=45)
            searches += 1
            print("    SEARCH PROGRESS: %d / %d" % (searches, target_searches), flush=True)
            if LIVE:
                increment_search_count(state)
                mark_episode_searched(state, episode_id)
                save_state(state)
        except Exception as e:
            errors += 1
            print("    SEARCH ERROR:", e, flush=True)
            print()
            continue

        choice = choose_best(item, releases, state)
        if not choice:
            no_match += 1
            print("    KEEP CURRENT: no qualifying replacement.", flush=True)
            print()
            continue

        describe_choice(choice)

        if not LIVE:
            grabs += 1
            print("    DRY RUN: WOULD GRAB", flush=True)
            print("    QUALIFYING REPLACEMENTS FOUND: %d" % grabs, flush=True)
            print()
            continue

        try:
            fresh_queue = active_episode_ids()
            if episode_id in fresh_queue:
                print("    SKIP: episode entered Sonarr queue while we were evaluating it.", flush=True)
                print()
                continue
        except Exception as e:
            print("    SKIP: could not perform final queue safety check:", e, flush=True)
            print()
            continue

        try:
            post("/release", choice["release"])
            grabs += 1
            mark_release_attempted(state, choice["release"])
            save_state(state)
            print("    LIVE: RELEASE SENT TO SONARR", flush=True)
            print("    QUALIFYING REPLACEMENTS FOUND: %d" % grabs, flush=True)
            print("    Existing episode remains until Sonarr successfully downloads and imports replacement.")
        except Exception as e:
            errors += 1
            print("    GRAB ERROR:", e, flush=True)
        print()

    print("=" * 68)
    print("SUMMARY")
    print("=" * 68)

    print("Interactive searches this run:", searches)

    if LIVE:
        print("Releases sent to Sonarr:", grabs)
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
