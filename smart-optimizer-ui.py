#!/usr/bin/env python3
"""Optional lightweight web UI for Radarr Smart Optimizer.

Standard library only. The optimizer remains fully usable without this file.
"""

import html
import json
import os
import re
import signal
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BUNDLED_DIR = os.path.join(BASE_DIR, ".bundled-optimizers")
BUNDLED_RADARR = os.path.join(BUNDLED_DIR, "radarr-smart-optimizer.py")
BUNDLED_SONARR = os.path.join(BUNDLED_DIR, "sonarr-smart-optimizer.py")
_EMBEDDED_RADARR = "#!/usr/bin/env python3\n\nimport os\nimport sys\nimport json\nimport re\nimport time\nimport urllib.request\nimport urllib.parse\nimport urllib.error\nfrom datetime import datetime, timezone\n\n# ============================================================\n# RADARR SMART OPTIMIZER\n#\n# Default = DRY RUN\n# Live    = --live\n#\n# IMPORTANT:\n# - This script NEVER calls Radarr DELETE endpoints directly\n# - In live mode Radarr may replace an existing file after importing a selected release\n# - NEVER touches Deluge directly\n# - Radarr performs normal Completed Download Handling/import\n# - Search budgets are configurable; defaults are conservative for scheduled use\n# ============================================================\n\n# ============================================================\n# QUICK SETUP\n# ============================================================\n# API keys are intentionally NOT stored in this source file.\n# Set RADARR_KEY in your environment or use a protected wrapper/key file.\n# Check the URL if Radarr is not on the same machine, then review\n# SEARCHES_PER_RUN plus NORMAL_PROFILE_ID and UHD_PROFILE_ID below.\nRADARR_URL_DEFAULT = \"http://127.0.0.1:7878\"\nSEARCHES_PER_RUN = 10\n\nRADARR_URL = os.environ.get(\"RADARR_URL\", RADARR_URL_DEFAULT).rstrip(\"/\")\nAPI_KEY = os.environ.get(\"RADARR_KEY\", \"\").strip()\nSEARCHES_PER_RUN = int(os.environ.get(\"RADARR_SEARCHES_PER_RUN\", SEARCHES_PER_RUN))\n\nSCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))\nSTATE_FILE = os.environ.get(\n    \"RADARR_OPTIMIZER_STATE\",\n    os.path.join(SCRIPT_DIR, \"radarr-smart-optimizer-state.json\")\n)\n\nNORMAL_PROFILE_ID = 4\nUHD_PROFILE_ID = 5\n\nDAILY_SEARCH_BUDGET = 300\nMIN_SEEDERS = 1\nMIN_SAVING_PERCENT = float(os.environ.get(\"RADARR_MIN_SAVING_PERCENT\", \"5.0\"))\nMAX_SAVING_PERCENT = float(os.environ.get(\"RADARR_MAX_SAVING_PERCENT\", \"50.0\"))\nCONTROL_FILE = os.environ.get(\"SMART_OPTIMIZER_CONTROL\", os.path.join(SCRIPT_DIR, \"smart-optimizer-control.json\"))\n\ndef load_runtime_controls():\n    controls = {}\n    try:\n        with open(CONTROL_FILE, \"r\", encoding=\"utf-8\") as f:\n            controls = (json.load(f) or {}).get(\"radarr\", {})\n    except Exception:\n        pass\n    today = datetime.now().strftime(\"%Y-%m-%d\")\n    try:\n        minimum = float(controls.get(\"min_saving_percent\", MIN_SAVING_PERCENT))\n        maximum = float(controls.get(\"max_saving_percent\", MAX_SAVING_PERCENT))\n        if not (0 <= minimum <= maximum <= 100):\n            raise ValueError\n    except (TypeError, ValueError):\n        minimum, maximum = MIN_SAVING_PERCENT, MAX_SAVING_PERCENT\n    try:\n        extra = int((controls.get(\"daily_extra\") or {}).get(today, 0))\n    except (TypeError, ValueError):\n        extra = 0\n    return minimum, maximum, max(0, extra)\n\nMIN_SAVING_PERCENT, MAX_SAVING_PERCENT, DAILY_EXTRA_BUDGET = load_runtime_controls()\n\n\n# Don't deliberately grab the exact same release again for this long\nATTEMPT_COOLDOWN_DAYS = 365\n\n# These are NOT hard quality limits.\n# They are only used for PRIORITY.\nLARGE_1080P_MIB = 1800\nCOMPACT_1080P_X265_MIB = 1200\nLARGE_2160P_MIB = 6000\n\nLIVE = \"--live\" in sys.argv\n\nif not API_KEY:\n    print(\"ERROR: Radarr API key is not configured.\")\n    print()\n    print(\"Set RADARR_KEY in your environment or protected wrapper/key file.\")\n    print(\"Then run: python3 radarr-smart-optimizer.py\")\n    sys.exit(1)\n\n\n# ============================================================\n# BASIC HELPERS\n# ============================================================\n\ndef now_ts():\n    return int(time.time())\n\n\ndef age_days(timestamp):\n    if not timestamp:\n        return 999999\n    return (now_ts() - int(timestamp)) / 86400.0\n\n\ndef mib(value):\n    try:\n        return float(value) / 1024 / 1024\n    except Exception:\n        return 0.0\n\n\ndef api(method, path, data=None):\n    url = RADARR_URL + \"/api/v3\" + path\n\n    headers = {\n        \"X-Api-Key\": API_KEY,\n        \"Accept\": \"application/json\"\n    }\n\n    body = None\n\n    if data is not None:\n        body = json.dumps(data).encode(\"utf-8\")\n        headers[\"Content-Type\"] = \"application/json\"\n\n    req = urllib.request.Request(\n        url,\n        data=body,\n        headers=headers,\n        method=method\n    )\n\n    try:\n        with urllib.request.urlopen(req, timeout=120) as response:\n            raw = response.read()\n\n            if not raw:\n                return None\n\n            return json.loads(raw.decode(\"utf-8\"))\n\n    except urllib.error.HTTPError as e:\n        detail = e.read().decode(\"utf-8\", errors=\"replace\")\n        raise RuntimeError(\n            \"%s %s -> HTTP %s\\n%s\" %\n            (method, path, e.code, detail)\n        )\n\n    except Exception as e:\n        raise RuntimeError(\n            \"%s %s -> %s\" %\n            (method, path, e)\n        )\n\n\ndef get(path):\n    return api(\"GET\", path)\n\n\ndef post(path, data):\n    return api(\"POST\", path, data)\n\ndef put(path, data):\n    return api(\"PUT\", path, data)\n\nADD_OPTIMIZED_TAG = os.environ.get(\"SMART_OPTIMIZER_ADD_TAG\", \"0\").lower() in (\"1\", \"true\", \"yes\", \"on\")\nOPTIMIZED_TAG_LABEL = \"smart-optimized\"\n\ndef add_optimized_tag_to_movie(movie_id):\n    if not ADD_OPTIMIZED_TAG:\n        return\n    try:\n        tags = get(\"/tag\") or []\n        tag = next((x for x in tags if str(x.get(\"label\") or \"\").lower() == OPTIMIZED_TAG_LABEL), None)\n        if not tag:\n            tag = post(\"/tag\", {\"label\": OPTIMIZED_TAG_LABEL})\n        tag_id = int(tag[\"id\"])\n        movie = get(\"/movie/%d\" % int(movie_id))\n        current = [int(x) for x in (movie.get(\"tags\") or [])]\n        if tag_id not in current:\n            movie[\"tags\"] = current + [tag_id]\n            put(\"/movie/%d\" % int(movie_id), movie)\n            print(\"    TAGGED: %s\" % OPTIMIZED_TAG_LABEL, flush=True)\n    except Exception as e:\n        # Tagging is optional metadata and must never turn a successful grab into a failure.\n        print(\"    TAG WARNING:\", e, flush=True)\n\n\n# ============================================================\n# STATE\n# ============================================================\n\ndef blank_state():\n    return {\n        \"version\": 1,\n        \"movies\": {},\n        \"attempted_releases\": {},\n        \"daily\": {},\n        \"movie_queue\": [],\n        \"movie_cursor\": 0,\n        \"known_movie_ids\": [],\n        \"queue_initialized\": False\n    }\n\n\ndef load_state():\n    if not os.path.exists(STATE_FILE):\n        return blank_state()\n\n    try:\n        with open(STATE_FILE, \"r\", encoding=\"utf-8\") as f:\n            state = json.load(f)\n\n        state.setdefault(\"version\", 1)\n        state.setdefault(\"movies\", {})\n        state.setdefault(\"attempted_releases\", {})\n        state.setdefault(\"daily\", {})\n        state.setdefault(\"movie_queue\", [])\n        state.setdefault(\"movie_cursor\", 0)\n        state.setdefault(\"known_movie_ids\", [])\n        state.setdefault(\"queue_initialized\", False)\n\n        return state\n\n    except Exception as e:\n        print(\"WARNING: Could not read state file:\")\n        print(\" \", e)\n        print(\"Using empty state for this run.\")\n        return blank_state()\n\n\ndef save_state(state):\n    if not LIVE:\n        return\n\n    # STATE_FILE may be a Docker single-file bind mount. Replacing the inode\n    # with os.replace() can fail with EBUSY. Write in place instead, matching\n    # the production-safe Sonarr implementation.\n    with open(STATE_FILE, \"w\", encoding=\"utf-8\") as f:\n        json.dump(state, f, indent=2, sort_keys=True)\n        f.flush()\n        os.fsync(f.fileno())\n\n\ndef today_key():\n    return datetime.now().strftime(\"%Y-%m-%d\")\n\n\ndef searches_used_today(state):\n    return int(\n        state.get(\"daily\", {})\n             .get(today_key(), {})\n             .get(\"searches\", 0)\n    )\n\n\ndef increment_search_count(state):\n    day = today_key()\n\n    state.setdefault(\"daily\", {})\n    state[\"daily\"].setdefault(day, {\"searches\": 0})\n\n    state[\"daily\"][day][\"searches\"] += 1\n\n    # Remove ancient daily counters.\n    keys = sorted(state[\"daily\"].keys())\n\n    if len(keys) > 60:\n        for old in keys[:-60]:\n            state[\"daily\"].pop(old, None)\n\n\ndef mark_movie_searched(state, movie_id):\n    key = str(movie_id)\n    state[\"movies\"].setdefault(key, {})\n    entry = state[\"movies\"][key]\n    entry[\"last_search\"] = now_ts()\n    entry[\"search_cycles\"] = min(2, int(entry.get(\"search_cycles\", 0)) + 1)\n\n\ndef release_key(release):\n    for field in (\n        \"guid\",\n        \"downloadUrl\",\n        \"infoUrl\",\n        \"title\"\n    ):\n        value = release.get(field)\n        if value:\n            return str(value)\n\n    return str(release.get(\"title\", \"UNKNOWN\"))\n\n\ndef release_recently_attempted(state, release):\n    key = release_key(release)\n\n    ts = state.get(\"attempted_releases\", {}).get(key)\n\n    if not ts:\n        return False\n\n    return age_days(ts) < ATTEMPT_COOLDOWN_DAYS\n\n\ndef mark_release_attempted(state, release):\n    key = release_key(release)\n    state.setdefault(\"attempted_releases\", {})\n    state[\"attempted_releases\"][key] = now_ts()\n\n\ndef clean_old_attempts(state):\n    cutoff = ATTEMPT_COOLDOWN_DAYS + 30\n\n    remove = []\n\n    for key, ts in state.get(\"attempted_releases\", {}).items():\n        if age_days(ts) > cutoff:\n            remove.append(key)\n\n    for key in remove:\n        state[\"attempted_releases\"].pop(key, None)\n\n\n# ============================================================\n# MEDIA PARSING\n# ============================================================\n\ndef codec_from_text(text):\n    \"\"\"\n    Detect VIDEO codec from a release title.\n\n    Normalized values:\n      x265\n      x264\n      av1\n      vp9\n      vp8\n      vc1\n      unknown\n\n    MKV/MP4 are containers and intentionally do NOT determine codec.\n    \"\"\"\n    text = text or \"\"\n\n    # H.265 / HEVC\n    if re.search(\n        r'(?i)(?:\\bx[ ._-]?265\\b|\\bh[ ._-]?265\\b|\\bhevc\\b)',\n        text\n    ):\n        return \"x265\"\n\n    # H.264 / AVC\n    if re.search(\n        r'(?i)(?:\\bx[ ._-]?264\\b|\\bh[ ._-]?264\\b|\\bavc\\b)',\n        text\n    ):\n        return \"x264\"\n\n    # AV1 / AV01\n    if re.search(\n        r'(?i)(?:\\bav[ ._-]?1\\b|\\bav01\\b)',\n        text\n    ):\n        return \"av1\"\n\n    # VP9 / VP09\n    if re.search(\n        r'(?i)(?:\\bvp[ ._-]?9\\b|\\bvp09\\b)',\n        text\n    ):\n        return \"vp9\"\n\n    # VP8 / VP08\n    if re.search(\n        r'(?i)(?:\\bvp[ ._-]?8\\b|\\bvp08\\b)',\n        text\n    ):\n        return \"vp8\"\n\n    # VC-1 / VC1\n    if re.search(\n        r'(?i)\\bvc[ ._-]?1\\b',\n        text\n    ):\n        return \"vc1\"\n\n    return \"unknown\"\n\n\ndef audio_channels_from_text(text):\n    text = (text or \"\").lower()\n\n    patterns = [\n        (7.1, r\"\\b7[\\s._-]?1\\b\"),\n        (5.1, r\"\\b5[\\s._-]?1\\b\"),\n        (2.1, r\"\\b2[\\s._-]?1\\b\"),\n        (2.0, r\"\\b2[\\s._-]?0\\b\"),\n        (1.0, r\"\\b1[\\s._-]?0\\b\"),\n    ]\n\n    for channels, pattern in patterns:\n        if re.search(pattern, text):\n            return channels\n\n    if \"stereo\" in text:\n        return 2.0\n\n    return None\n\n\nDANGEROUS_EXTENSIONS = (\n    \"exe\", \"scr\", \"bat\", \"cmd\", \"msi\", \"com\",\n    \"pif\", \"vbs\", \"js\", \"jar\", \"ps1\"\n)\n\n\ndef dangerous_release_title(text):\n    \"\"\"\n    Hard-block executable/script payloads.\n    MKV, MP4 and other normal media containers are unaffected.\n    \"\"\"\n    text = text or \"\"\n\n    pattern = (\n        r'(?i)\\.(?:'\n        + '|'.join(re.escape(ext) for ext in DANGEROUS_EXTENSIONS)\n        + r')(?=$|[\\s._\\-\\[\\]\\(\\)])'\n    )\n\n    return bool(re.search(pattern, text))\n\n\ndef current_codec(file_obj):\n    media = file_obj.get(\"mediaInfo\") or {}\n\n    codec = codec_from_text(\n        \" \".join([\n            str(media.get(\"videoCodec\", \"\")),\n            str(file_obj.get(\"sceneName\", \"\")),\n            str(file_obj.get(\"relativePath\", \"\"))\n        ])\n    )\n\n    return codec\n\n\ndef quality_resolution(quality_obj):\n    q = quality_obj or {}\n\n    if \"quality\" in q:\n        q = q.get(\"quality\") or {}\n\n    resolution = q.get(\"resolution\")\n\n    try:\n        if resolution:\n            return int(resolution)\n    except Exception:\n        pass\n\n    name = str(q.get(\"name\", \"\"))\n\n    match = re.search(r\"(2160|1080|720|480)\", name)\n\n    if match:\n        return int(match.group(1))\n\n    return 0\n\n\ndef file_resolution(file_obj):\n    media = file_obj.get(\"mediaInfo\") or {}\n\n    width = media.get(\"width\")\n    height = media.get(\"height\")\n\n    try:\n        height = int(height or 0)\n    except Exception:\n        height = 0\n\n    if height >= 2000:\n        return 2160\n\n    if height >= 1000:\n        return 1080\n\n    if height >= 700:\n        return 720\n\n    return quality_resolution(file_obj.get(\"quality\"))\n\n\n# ============================================================\n# QUEUE\n# ============================================================\n\ndef active_movie_ids():\n    ids = set()\n\n    page = 1\n\n    while True:\n        path = (\n            \"/queue?page=%d&pageSize=100\"\n            \"&includeUnknownMovieItems=true\"\n        ) % page\n\n        data = get(path)\n\n        if not data:\n            break\n\n        records = data.get(\"records\", [])\n\n        for item in records:\n            movie_id = item.get(\"movieId\")\n\n            if movie_id:\n                ids.add(int(movie_id))\n\n        total = int(data.get(\"totalRecords\", len(records)))\n\n        if page * 100 >= total:\n            break\n\n        page += 1\n\n    return ids\n\n\n# ============================================================\n# LOCAL LIBRARY CANDIDATES\n# ============================================================\n\ndef priority_score(item):\n    \"\"\"\n    Higher = search earlier.\n\n    IMPORTANT:\n    This only decides WHICH existing movies deserve one of\n    our scarce interactive searches.\n\n    It does NOT decide which release wins after searching.\n    Profile 5 can still prefer a valid <=8 GiB 2160p release.\n    If no valid 2160p exists, smaller qualifying 1080p releases\n    remain fully eligible.\n    \"\"\"\n\n    res = item[\"resolution\"]\n    target = item[\"target_resolution\"]\n    size = item[\"size_mib\"]\n    codec = item[\"codec\"]\n\n    score = 0.0\n\n    # --------------------------------------------------------\n    # PROFILE 5 / 4K-PREFERRED MOVIES\n    # --------------------------------------------------------\n    if item[\"profile_id\"] == UHD_PROFILE_ID:\n\n        # Missing target resolution is important, but don't give\n        # every 1080p movie an identical gigantic score.\n        if res < 2160:\n            score += 600000\n\n            # Lower-than-1080p files are much more urgent.\n            if res < 1080:\n                score += 300000\n\n            # Larger existing files have more optimization\n            # potential if no acceptable 4K release exists.\n            score += min(size * 100, 300000)\n\n            # x264 gets extra attention because x265 often gives\n            # worthwhile space savings.\n            if codec == \"x264\":\n                score += 100000\n            elif codec == \"unknown\":\n                score += 50000\n\n            # Already tiny 1080p x265 files can still eventually\n            # be searched for 4K, but should not steal today's\n            # scarce slots from much larger files.\n            if (\n                res == 1080\n                and codec == \"x265\"\n                and size <= COMPACT_1080P_X265_MIB\n            ):\n                score -= 150000\n\n        else:\n            # Already 2160p: only optimization potential matters.\n            score += min(size * 25, 200000)\n\n            if codec == \"x264\":\n                score += 75000\n\n            if size >= LARGE_2160P_MIB:\n                score += 100000\n\n        return score\n\n    # --------------------------------------------------------\n    # NORMAL 1080P PROFILE\n    # --------------------------------------------------------\n\n    # Resolution deficiency has highest priority.\n    if res < target:\n        score += 800000\n        score += (target - res) * 500\n\n    # x264 generally has greater compression-saving potential.\n    if codec == \"x264\":\n        score += 200000\n    elif codec == \"unknown\":\n        score += 100000\n\n    # Large files deserve attention.\n    if size >= LARGE_1080P_MIB:\n        score += 200000\n\n    score += min(size * 50, 250000)\n\n    # Compact 1080p x265 is already in a very good state.\n    if (\n        res >= 1080\n        and codec == \"x265\"\n        and size <= COMPACT_1080P_X265_MIB\n    ):\n        score -= 200000\n\n    return score\n\ndef initialize_movie_queue(state, movies):\n    \"\"\"Create the persistent A-Z optimizer queue once.\"\"\"\n    with_files = [m for m in movies if m.get(\"id\") and m.get(\"hasFile\")]\n    ordered = sorted(\n        with_files,\n        key=lambda m: ((m.get(\"title\") or \"\").casefold(), int(m.get(\"id\", 0)))\n    )\n    state[\"movie_queue\"] = [\n        {\n            \"movie_id\": int(m[\"id\"]),\n            \"title\": m.get(\"title\") or \"Unknown movie\",\n            \"year\": m.get(\"year\"),\n        }\n        for m in ordered\n    ]\n    state[\"movie_cursor\"] = 0\n    state[\"known_movie_ids\"] = [x[\"movie_id\"] for x in state[\"movie_queue\"]]\n    state[\"queue_initialized\"] = True\n    save_state(state)\n    print(\"PERMANENT A-Z MOVIE QUEUE READY:\", len(state[\"movie_queue\"]), \"movies\", flush=True)\n\n\ndef append_new_movies(state, movies):\n    \"\"\"Append newly downloaded movies to the END; never reorder the existing queue.\"\"\"\n    known = set(int(x) for x in state.get(\"known_movie_ids\", []))\n    added = 0\n    for movie in movies:\n        mid = int(movie.get(\"id\", 0) or 0)\n        if not mid or mid in known or not movie.get(\"hasFile\"):\n            continue\n        state.setdefault(\"movie_queue\", []).append({\n            \"movie_id\": mid,\n            \"title\": movie.get(\"title\") or \"Unknown movie\",\n            \"year\": movie.get(\"year\"),\n        })\n        known.add(mid)\n        added += 1\n        print(\"APPENDED NEW MOVIE TO BOTTOM:\", movie.get(\"title\"), flush=True)\n    state[\"known_movie_ids\"] = sorted(known)\n    if added:\n        save_state(state)\n    return added\n\n\ndef movie_item(movie, state, queued_ids):\n    \"\"\"Return an optimizer-searchable movie item, or None without consuming search quota.\"\"\"\n    movie_id = movie.get(\"id\")\n    if not movie_id or not movie.get(\"hasFile\") or movie_id in queued_ids:\n        return None\n\n    history = state.get(\"movies\", {}).get(str(movie_id), {})\n    cycles = int(history.get(\"search_cycles\", 0))\n    last_search = history.get(\"last_search\")\n    if cycles >= 2:\n        return None\n    if cycles == 1 and last_search and age_days(last_search) < 180:\n        return None\n\n    movie_file = movie.get(\"movieFile\") or {}\n    size_bytes = movie_file.get(\"size\") or 0\n    if size_bytes <= 0:\n        return None\n\n    resolution = file_resolution(movie_file)\n    if resolution not in (1080, 2160):\n        return None\n\n    profile_id = movie.get(\"qualityProfileId\")\n    target_resolution = 2160 if profile_id == UHD_PROFILE_ID else resolution\n\n    return {\n        \"movie_id\": movie_id,\n        \"tmdb_id\": movie.get(\"tmdbId\"),\n        \"title\": movie.get(\"title\") or \"Unknown movie\",\n        \"year\": movie.get(\"year\"),\n        \"profile_id\": profile_id,\n        \"resolution\": resolution,\n        \"target_resolution\": target_resolution,\n        \"size_bytes\": size_bytes,\n        \"size_mib\": mib(size_bytes),\n        \"codec\": current_codec(movie_file),\n        \"audio_channels\": radarr_current_audio_channels(movie_file),\n        \"atmos\": radarr_current_atmos(movie_file),\n        \"dynamic_range\": radarr_current_dynamic_range(movie_file),\n        \"movie_file\": movie_file,\n    }\n\n\ndef next_movie_item(state, movies_by_id, queued_ids):\n    \"\"\"Advance the one persistent cursor until an eligible movie is found or queue ends.\"\"\"\n    queue = state.get(\"movie_queue\", [])\n    while int(state.get(\"movie_cursor\", 0)) < len(queue):\n        cursor = int(state.get(\"movie_cursor\", 0))\n        ref = queue[cursor]\n        state[\"movie_cursor\"] = cursor + 1\n        if LIVE:\n            save_state(state)\n\n        movie = movies_by_id.get(int(ref.get(\"movie_id\", 0)))\n        if not movie:\n            continue\n\n        item = movie_item(movie, state, queued_ids)\n        if item is not None:\n            return item\n    return None\n\n\ndef collect_candidates(state, queued_ids):\n    movies = get(\"/movie\")\n    items = []\n\n    stats = {\n        \"movies\": len(movies),\n        \"with_file\": 0,\n        \"queued\": 0,\n        \"eligible\": 0,\n    }\n\n    for movie in movies:\n        movie_id = movie.get(\"id\")\n        tmdb_id = movie.get(\"tmdbId\")\n\n        if not movie.get(\"hasFile\"):\n            continue\n\n        stats[\"with_file\"] += 1\n\n        if movie_id in queued_ids:\n            stats[\"queued\"] += 1\n            continue\n\n        # Optimizer search-cycle policy:\n        #   cycle 0: eligible now\n        #   cycle 1: wait at least 180 days\n        #   cycle 2: permanently excluded from this optimizer\n        history = state.get(\"movies\", {}).get(str(movie_id), {})\n        cycles = int(history.get(\"search_cycles\", 0))\n        last_search = history.get(\"last_search\")\n\n        if cycles >= 2:\n            continue\n\n        if cycles == 1 and last_search and age_days(last_search) < 180:\n            continue\n\n        movie_file = movie.get(\"movieFile\") or {}\n        if not movie_file:\n            continue\n\n        size_bytes = movie_file.get(\"size\") or 0\n        if size_bytes <= 0:\n            continue\n\n        resolution = file_resolution(movie_file)\n        if not resolution:\n            continue\n\n        # Optimize actual 1080p and 2160p files regardless of\n        # the Radarr quality profile assigned to the movie.\n        # Ignore 720p and lower.\n        if resolution not in (1080, 2160):\n            continue\n\n        profile_id = movie.get(\"qualityProfileId\")\n\n        # Normal profiles keep their current resolution.\n        # UHD-profile movies may upgrade an existing 1080p file to 2160p.\n        target_resolution = 2160 if profile_id == UHD_PROFILE_ID else resolution\n\n        media = movie_file.get(\"mediaInfo\") or {}\n\n        item = {\n            \"movie_id\": movie_id,\n            \"tmdb_id\": tmdb_id,\n            \"title\": movie.get(\"title\") or \"Unknown movie\",\n            \"year\": movie.get(\"year\"),\n            \"profile_id\": profile_id,\n            \"resolution\": resolution,\n            \"target_resolution\": target_resolution,\n            \"size_bytes\": size_bytes,\n            \"size_mib\": mib(size_bytes),\n            \"codec\": current_codec(movie_file),\n            \"audio_channels\": radarr_current_audio_channels(movie_file),\n            \"atmos\": radarr_current_atmos(movie_file),\n            \"dynamic_range\": radarr_current_dynamic_range(movie_file),\n            \"movie_file\": movie_file,\n        }\n\n        items.append(item)\n        stats[\"eligible\"] += 1\n\n    return items, stats\n\n\ndef rejection_allowed(rejection):\n    \"\"\"\n    We ONLY ignore Radarr's cutoff rejection because the\n    optimizer intentionally evaluates replacements beyond\n    Radarr's normal cutoff.\n\n    Every other Radarr rejection remains respected.\n    \"\"\"\n\n    reason = \"\"\n\n    if isinstance(rejection, dict):\n        reason = str(\n            rejection.get(\"reason\")\n            or rejection.get(\"message\")\n            or \"\"\n        )\n    else:\n        reason = str(rejection)\n\n    reason = reason.lower()\n\n    # The optimizer has its own conservative quality gate below. Radarr may\n    # call a smaller same-resolution BluRay release a downgrade when the current\n    # file is a Remux. That is not automatically a downgrade for this project:\n    # resolution/HDR/DV/audio protections and storage efficiency decide.\n    optimizer_quality_rejections = (\n        \"existing file meets cutoff\",\n        \"not an upgrade for existing movie file\",\n        \"quality for existing file on disk is of equal or higher preference\",\n    )\n    return any(text in reason for text in optimizer_quality_rejections)\n\n\ndef radarr_rejections_ok(release):\n    rejected = release.get(\"rejections\") or []\n\n    for rejection in rejected:\n        if not rejection_allowed(rejection):\n            return False\n\n    return True\n\n\n\ndef candidate_resolution_from_release(release):\n    return quality_resolution(release.get(\"quality\"))\n\n\ndef evaluate_release(item, release, state):\n    title = release.get(\"title\") or \"\"\n\n    if dangerous_release_title(title):\n        return None, \"dangerous\"\n\n    if not radarr_rejections_ok(release):\n        return None, \"radarr rejection\"\n\n    if release_recently_attempted(state, release):\n        return None, \"recently attempted\"\n\n    seeders = release.get(\"seeders\")\n    try:\n        seeders = int(seeders)\n    except (TypeError, ValueError):\n        return None, \"unknown seeders\"\n\n    if seeders < MIN_SEEDERS:\n        return None, \"not enough seeders\"\n\n    candidate_resolution = candidate_resolution_from_release(release)\n    if not candidate_resolution:\n        return None, \"unknown resolution\"\n\n    current_resolution = item[\"resolution\"]\n\n    # Never downgrade resolution.\n    if candidate_resolution < current_resolution:\n        return None, \"resolution downgrade\"\n\n    # Higher resolution is only allowed toward the UHD target.\n    if candidate_resolution > current_resolution:\n        if item[\"target_resolution\"] != 2160 or candidate_resolution != 2160:\n            return None, \"resolution upgrade not allowed\"\n\n    size_bytes = release.get(\"size\") or 0\n    try:\n        size_bytes = int(size_bytes)\n    except (TypeError, ValueError):\n        return None, \"unknown size\"\n\n    if size_bytes <= 0:\n        return None, \"unknown size\"\n\n    candidate_mib = mib(size_bytes)\n    current_mib = item[\"size_mib\"]\n\n    saving = ((current_mib - candidate_mib) / current_mib) * 100.0\n\n    # Storage-first policy applies to EVERY replacement, including 1080p -> 2160p.\n    # A candidate must save meaningful space, but an extreme reduction is rejected\n    # as a compression/quality-risk guardrail.\n    if saving < MIN_SAVING_PERCENT:\n        return None, \"candidate does not save enough space\"\n    if saving > MAX_SAVING_PERCENT:\n        return None, \"candidate saves too much space (quality-risk guardrail)\"\n\n    candidate_codec = codec_from_text(title)\n    candidate_channels = audio_channels_from_text(title)\n    candidate_atmos = radarr_candidate_atmos(title)\n    candidate_dr = radarr_candidate_dynamic_range(title)\n\n    # 4K Dolby Vision must explicitly include HDR fallback.\n    # Size safety is handled by the relative MIN/MAX saving window above,\n    # rather than a fixed GiB range that cannot scale with the current file.\n    if candidate_resolution == 2160 and candidate_dr == \"DV_ONLY\":\n        return None, \"4K DV without HDR fallback\"\n\n    if not radarr_dynamic_range_allowed(item[\"dynamic_range\"], candidate_dr):\n        return None, \"dynamic range protection\"\n\n    if not radarr_audio_allowed(\n        item[\"audio_channels\"],\n        item[\"atmos\"],\n        candidate_channels,\n        candidate_atmos\n    ):\n        return None, \"audio protection\"\n\n    return {\n        \"release\": release,\n        \"title\": title,\n        \"resolution\": candidate_resolution,\n        \"size_bytes\": size_bytes,\n        \"size_mib\": candidate_mib,\n        \"saving_percent\": saving,\n        \"codec\": candidate_codec,\n        \"audio_channels\": candidate_channels,\n        \"atmos\": candidate_atmos,\n        \"dynamic_range\": candidate_dr,\n        \"seeders\": seeders,\n    }, None\n\n\ndef choose_best(item, releases, state):\n    accepted = []\n\n    for release in releases:\n        choice, reason = evaluate_release(item, release, state)\n        if choice:\n            accepted.append(choice)\n\n    if not accepted:\n        return None\n\n    current_res = item[\"resolution\"]\n\n    # UHD profile: a valid higher-resolution candidate wins over\n    # same-resolution storage optimization.\n    higher = [x for x in accepted if x[\"resolution\"] > current_res]\n    pool = higher if higher else [\n        x for x in accepted if x[\"resolution\"] == current_res\n    ]\n\n    if not pool:\n        return None\n\n    # Within an accepted resolution tier, storage efficiency is primary.\n    # All candidates here already passed the no-resolution-downgrade,\n    # HDR/DV, audio, Atmos, seeder and size gates. x265/HEVC is only a\n    # secondary preference and never justifies a larger same-resolution file.\n    pool.sort(key=lambda x: (\n        x[\"size_bytes\"],\n        0 if x[\"codec\"] == \"x265\" else 1,\n        -x[\"seeders\"],\n        x[\"title\"].lower()\n    ))\n\n    return pool[0]\n\n\ndef describe_item(number, item):\n    print(\n        \"%2d. %s (%s)\" %\n        (\n            number,\n            item.get(\"title\", \"Unknown\"),\n            item.get(\"year\", \"?\")\n        )\n    )\n\n    print(\n        \"    Current: %sp | %s | %.0f MiB | %.1fch | Atmos=%s | DR=%s\" %\n        (\n            item.get(\"resolution\", 0),\n            item.get(\"codec\") or \"unknown\",\n            item.get(\"size_mib\", 0),\n            item.get(\"audio_channels\", 0),\n            item.get(\"atmos\", False),\n            item.get(\"dynamic_range\", \"SDR_UNKNOWN\")\n        )\n    )\n\n    print(\n        \"    Target: %sp | profile %s\" %\n        (\n            item.get(\"target_resolution\", 0),\n            item.get(\"profile_id\", \"?\")\n        )\n    )\n\n\ndef describe_choice(choice):\n    print(\n        \"    FOUND: %s\" %\n        choice.get(\"title\", choice[\"release\"].get(\"title\", \"Unknown\"))\n    )\n\n    print(\n        \"    New: %sp | %s | %.0f MiB | %.1fch | Atmos=%s | DR=%s | Seeders=%s\" %\n        (\n            choice.get(\"resolution\", 0),\n            choice.get(\"codec\") or \"unknown\",\n            choice.get(\"size_mib\", 0),\n            choice.get(\"audio_channels\") or 0,\n            choice.get(\"atmos\", False),\n            choice.get(\"dynamic_range\", \"SDR_UNKNOWN\"),\n            choice.get(\"seeders\", \"?\")\n        )\n    )\n\n    if choice.get(\"saving_percent\") is not None:\n        print(\n            \"    Saving: %.1f%%\" %\n            choice[\"saving_percent\"]\n        )\n\n\n\n# ============================================================\n# RADARR MEDIA PROTECTION\n# ============================================================\n\ndef radarr_current_dynamic_range(movie_file):\n    media=(movie_file or {}).get(\"mediaInfo\") or {}\n\n    dr=str(media.get(\"videoDynamicRange\") or \"\").lower()\n    typ=str(media.get(\"videoDynamicRangeType\") or \"\").lower()\n\n    extra=\" \".join([\n        str((movie_file or {}).get(\"sceneName\") or \"\"),\n        str((movie_file or {}).get(\"relativePath\") or \"\")\n    ]).lower()\n\n    if (\n        \"dolby vision\" in typ\n        or \"dovi\" in typ\n        or \" dv\" in (\" \" + typ)\n        or \"dv \" in (typ + \" \")\n        or \"dolby vision\" in extra\n        or \"dovi\" in extra\n    ):\n        return \"DV_HDR\"\n\n    if any(x in dr or x in typ or x in extra for x in (\n        \"hdr10+\",\n        \"hdr10plus\",\n        \"hdr10\",\n        \"hdr\",\n        \"hlg\"\n    )):\n        return \"HDR\"\n\n    return \"SDR_UNKNOWN\"\n\n\ndef radarr_current_atmos(movie_file):\n    media=(movie_file or {}).get(\"mediaInfo\") or {}\n\n    text=\" \".join([\n        str(media.get(\"audioCodec\") or \"\"),\n        str((movie_file or {}).get(\"sceneName\") or \"\"),\n        str((movie_file or {}).get(\"relativePath\") or \"\")\n    ]).lower()\n\n    return \"atmos\" in text\n\n\ndef radarr_current_audio_channels(movie_file):\n    media=(movie_file or {}).get(\"mediaInfo\") or {}\n\n    try:\n        return float(media.get(\"audioChannels\") or 0)\n    except (TypeError, ValueError):\n        return 0.0\n\n\ndef radarr_candidate_atmos(title):\n    return \"atmos\" in str(title or \"\").lower()\n\n\ndef radarr_candidate_dynamic_range(title):\n    t=str(title or \"\").lower()\n\n    dv=(\n        \"dolby vision\" in t\n        or \"dovi\" in t\n        or bool(re.search(\n            r\"(?<![a-z0-9])dv(?![a-z0-9])\",\n            t\n        ))\n    )\n\n    hdr=any(x in t for x in (\n        \"hdr10+\",\n        \"hdr10plus\",\n        \"hdr10\",\n        \"hdr\",\n        \"hlg\"\n    ))\n\n    if dv and hdr:\n        return \"DV_HDR\"\n\n    if dv:\n        return \"DV_ONLY\"\n\n    if hdr:\n        return \"HDR\"\n\n    return \"SDR_UNKNOWN\"\n\n\ndef radarr_dynamic_range_allowed(current, candidate):\n    # DV without HDR fallback is never accepted.\n    if candidate == \"DV_ONLY\":\n        return False\n\n    # Existing DV+HDR must remain DV+HDR.\n    if current == \"DV_HDR\":\n        return candidate == \"DV_HDR\"\n\n    # Existing HDR may remain HDR or become DV+HDR.\n    if current == \"HDR\":\n        return candidate in (\"HDR\", \"DV_HDR\")\n\n    # SDR/unknown may move to HDR/DV or remain SDR/unknown.\n    return candidate in (\n        \"SDR_UNKNOWN\",\n        \"HDR\",\n        \"DV_HDR\"\n    )\n\n\ndef radarr_audio_allowed(\n    current_channels,\n    current_atmos,\n    candidate_channels,\n    candidate_atmos\n):\n    # Known 5.1/7.1 can never become lower-channel audio.\n    if current_channels >= 5.0:\n        if (\n            not candidate_channels\n            or candidate_channels < current_channels\n        ):\n            return False\n\n    # Atmos may never disappear.\n    if current_atmos and not candidate_atmos:\n        return False\n\n    return True\n\n\n\n\ndef main():\n    state = load_state()\n\n    if LIVE:\n        clean_old_attempts(state)\n\n    print()\n    print(\"=\" * 68)\n    print(\"RADARR SMART OPTIMIZER\")\n    print(\"=\" * 68)\n\n    if LIVE:\n        print(\"MODE: LIVE\")\n    else:\n        print(\"MODE: DRY RUN -- NO RELEASES WILL BE GRABBED\")\n\n    print(\"Daily interactive-search budget:\", DAILY_SEARCH_BUDGET + DAILY_EXTRA_BUDGET, \"(base %d + today override %d)\" % (DAILY_SEARCH_BUDGET, DAILY_EXTRA_BUDGET))\n    print(\"Allowed saving window: %.1f%% to %.1f%%\" % (MIN_SAVING_PERCENT, MAX_SAVING_PERCENT))\n    print()\n\n    used = searches_used_today(state)\n\n    # Maximum interactive searches in one execution.\n    PER_RUN_SEARCH_BUDGET = max(1, SEARCHES_PER_RUN)\n\n    if LIVE:\n        remaining = min(\n            PER_RUN_SEARCH_BUDGET,\n            max(0, DAILY_SEARCH_BUDGET + DAILY_EXTRA_BUDGET - used)\n        )\n    else:\n        # Dry run does NOT consume persistent budget.\n        remaining = min(\n            PER_RUN_SEARCH_BUDGET,\n            DAILY_SEARCH_BUDGET\n        )\n\n    print(\n        \"Persistent searches already used today:\",\n        used\n    )\n\n    print(\n        \"Searches available this run:\",\n        remaining\n    )\n\n    if remaining <= 0:\n        print()\n        print(\"Daily search budget exhausted.\")\n        print(\"Nothing to do.\")\n        return\n\n    print()\n    print(\"Reading Radarr queue...\")\n\n    queued_ids = active_movie_ids()\n    movies = get(\"/movie\")\n    movies_by_id = {int(m[\"id\"]): m for m in movies if m.get(\"id\")}\n\n    print(\"Movies currently represented in queue:\", len(queued_ids))\n    print(\"Movies in Radarr:\", len(movies))\n    print(\"Movies with files:\", sum(1 for m in movies if m.get(\"hasFile\")))\n\n    if not state.get(\"queue_initialized\") or not state.get(\"movie_queue\"):\n        print(\"Creating persistent A-Z movie queue...\", flush=True)\n        initialize_movie_queue(state, movies)\n\n    append_new_movies(state, movies)\n\n    print(\"Persistent optimizer queue:\", len(state.get(\"movie_queue\", [])))\n    print(\"Saved queue cursor:\", state.get(\"movie_cursor\", 0))\n    print()\n\n    searches = 0\n    grabs = 0\n    no_match = 0\n    errors = 0\n\n    number = 0\n    while searches < remaining:\n        item = next_movie_item(state, movies_by_id, queued_ids)\n        if item is None:\n            print(\"Reached the genuine end of the persistent movie queue.\", flush=True)\n            break\n\n        number += 1\n        print(\"-\" * 68)\n\n        describe_item(number, item)\n        print(\"    NOW CHECKING: %s (%s)\" % (item.get(\"title\", \"Unknown\"), item.get(\"year\", \"?\")), flush=True)\n\n        movie_id = item[\"movie_id\"]\n\n        try:\n            # THIS is the expensive interactive indexer search.\n            releases = get(\n                \"/release?movieId=%d\"\n                % movie_id\n            )\n\n            searches += 1\n            print(\"    SEARCH PROGRESS: %d / %d\" % (searches, remaining), flush=True)\n\n            if LIVE:\n                increment_search_count(state)\n                mark_movie_searched(\n                    state,\n                    movie_id\n                )\n\n                # Save immediately so a crash/restart does not\n                # accidentally reset our search budget.\n                save_state(state)\n\n        except Exception as e:\n            errors += 1\n            print(\"    SEARCH ERROR:\", e)\n            print()\n            continue\n\n        choice = choose_best(\n            item,\n            releases,\n            state\n        )\n\n        if not choice:\n            no_match += 1\n            print(\"    KEEP CURRENT: no qualifying replacement.\")\n            print()\n            continue\n\n        describe_choice(choice)\n\n        if not LIVE:\n            print(\"    DRY RUN: WOULD GRAB\")\n            print()\n            continue\n\n        # ----------------------------------------------------\n        # SAFETY CHECK AGAIN immediately before grabbing.\n        # ----------------------------------------------------\n\n        try:\n            fresh_queue = active_movie_ids()\n\n            if movie_id in fresh_queue:\n                print(\n                    \"    SKIP: movie entered Radarr queue \"\n                    \"while we were evaluating it.\"\n                )\n                print()\n                continue\n\n        except Exception as e:\n            print(\n                \"    SKIP: could not perform final queue safety check:\",\n                e\n            )\n            print()\n            continue\n\n        try:\n            # The ONLY Radarr write operation used to initiate\n            # replacement.\n            #\n            # NO DELETE.\n            # NO filesystem manipulation.\n            # NO direct Deluge manipulation.\n            post(\n                \"/release\",\n                choice[\"release\"]\n            )\n\n            grabs += 1\n\n            mark_release_attempted(\n                state,\n                choice[\"release\"]\n            )\n\n            save_state(state)\n            add_optimized_tag_to_movie(movie_id)\n\n            print(\"    LIVE: RELEASE SENT TO RADARR\")\n            print(\n                \"    Existing movie remains until Radarr \"\n                \"successfully downloads and imports replacement.\"\n            )\n\n\n        except Exception as e:\n            errors += 1\n            print(\"    GRAB ERROR:\", e)\n\n        print()\n\n    print(\"=\" * 68)\n    print(\"SUMMARY\")\n    print(\"=\" * 68)\n\n    print(\"Interactive searches this run:\", searches)\n\n    if LIVE:\n        print(\"Releases sent to Radarr:\", grabs)\n        print(\n            \"Persistent searches used today:\",\n            searches_used_today(state)\n        )\n    else:\n        print(\"Downloads started: 0\")\n        print(\"State changes: 0\")\n\n    print(\"No qualifying replacement:\", no_match)\n    print(\"Errors:\", errors)\n\n    if not LIVE:\n        print()\n        print(\n            \"DRY RUN COMPLETE -- no releases were grabbed and \"\n            \"no persistent cooldown changes were made.\"\n        )\n\n\nif __name__ == \"__main__\":\n    main()\n"
_EMBEDDED_SONARR = "#!/usr/bin/env python3\n\nimport os\nimport sys\nimport json\nimport re\nimport time\nimport urllib.request\nimport urllib.parse\nimport urllib.error\nfrom datetime import datetime, timezone\n\n# ============================================================\n# SONARR SMART OPTIMIZER\n#\n# Default = DRY RUN\n# Live    = --live\n#\n# IMPORTANT:\n# - This script NEVER calls Sonarr DELETE endpoints directly\n# - In live mode Sonarr may replace an existing file after importing a selected release\n# - NEVER touches Deluge directly\n# - Sonarr performs normal Completed Download Handling/import\n# - Search budgets are configurable; defaults are conservative for scheduled use\n# ============================================================\n\n# ============================================================\n# QUICK SETUP\n# ============================================================\n# API keys are intentionally NOT stored in this source file.\n# Set SONARR_KEY in your environment or use a protected wrapper/key file.\n# Check the URL if Sonarr is not on the same machine, then review\n# SEARCHES_PER_RUN plus NORMAL_PROFILE_ID and UHD_PROFILE_ID below.\nSONARR_URL_DEFAULT = \"http://127.0.0.1:8989\"\nSEARCHES_PER_RUN = 10\n\nSONARR_URL = os.environ.get(\"SONARR_URL\", SONARR_URL_DEFAULT).rstrip(\"/\")\nAPI_KEY = os.environ.get(\"SONARR_KEY\", \"\").strip()\nSEARCHES_PER_RUN = int(os.environ.get(\"SONARR_SEARCHES_PER_RUN\", SEARCHES_PER_RUN))\n\nSCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))\nSTATE_FILE = os.environ.get(\n    \"SONARR_OPTIMIZER_STATE\",\n    os.path.join(SCRIPT_DIR, \"sonarr-smart-optimizer-state.json\")\n)\n\nNORMAL_PROFILE_ID = 4\nUHD_PROFILE_ID = 5\n\nDAILY_SEARCH_BUDGET = 400\nMIN_SEEDERS = 1\nMIN_SAVING_PERCENT = float(os.environ.get(\"SONARR_MIN_SAVING_PERCENT\", \"5.0\"))\nMAX_SAVING_PERCENT = float(os.environ.get(\"SONARR_MAX_SAVING_PERCENT\", \"50.0\"))\nCONTROL_FILE = os.environ.get(\"SMART_OPTIMIZER_CONTROL\", os.path.join(SCRIPT_DIR, \"smart-optimizer-control.json\"))\n\ndef load_runtime_controls():\n    controls = {}\n    try:\n        with open(CONTROL_FILE, \"r\", encoding=\"utf-8\") as f:\n            controls = (json.load(f) or {}).get(\"sonarr\", {})\n    except Exception:\n        pass\n    today = datetime.now().strftime(\"%Y-%m-%d\")\n    try:\n        minimum = float(controls.get(\"min_saving_percent\", MIN_SAVING_PERCENT))\n        maximum = float(controls.get(\"max_saving_percent\", MAX_SAVING_PERCENT))\n        if not (0 <= minimum <= maximum <= 100):\n            raise ValueError\n    except (TypeError, ValueError):\n        minimum, maximum = MIN_SAVING_PERCENT, MAX_SAVING_PERCENT\n    try:\n        extra = int((controls.get(\"daily_extra\") or {}).get(today, 0))\n    except (TypeError, ValueError):\n        extra = 0\n    return minimum, maximum, max(0, extra)\n\nMIN_SAVING_PERCENT, MAX_SAVING_PERCENT, DAILY_EXTRA_BUDGET = load_runtime_controls()\n\n\n# Don't deliberately grab the exact same release again for this long\nATTEMPT_COOLDOWN_DAYS = 365\n\n# These are NOT hard quality limits.\n# They are only used for PRIORITY.\nLARGE_1080P_MIB = 1800\nCOMPACT_1080P_X265_MIB = 1200\nLARGE_2160P_MIB = 6000\n\n# Prevent one large series from consuming the whole daily budget.\nMAX_SEARCHES_PER_SERIES_PER_RUN = 3\n\nLIVE = \"--live\" in sys.argv\n\nif not API_KEY:\n    print(\"ERROR: Sonarr API key is not configured.\")\n    print()\n    print(\"Set SONARR_KEY in your environment or protected wrapper/key file.\")\n    print(\"Then run: python3 sonarr-smart-optimizer.py\")\n    sys.exit(1)\n\n\n# ============================================================\n# BASIC HELPERS\n# ============================================================\n\ndef now_ts():\n    return int(time.time())\n\n\ndef age_days(timestamp):\n    if not timestamp:\n        return 999999\n    return (now_ts() - int(timestamp)) / 86400.0\n\n\ndef mib(value):\n    try:\n        return float(value) / 1024 / 1024\n    except Exception:\n        return 0.0\n\n\ndef api(method, path, data=None, timeout=120):\n    url = SONARR_URL + \"/api/v3\" + path\n\n    headers = {\n        \"X-Api-Key\": API_KEY,\n        \"Accept\": \"application/json\"\n    }\n\n    body = None\n\n    if data is not None:\n        body = json.dumps(data).encode(\"utf-8\")\n        headers[\"Content-Type\"] = \"application/json\"\n\n    req = urllib.request.Request(\n        url,\n        data=body,\n        headers=headers,\n        method=method\n    )\n\n    try:\n        with urllib.request.urlopen(req, timeout=timeout) as response:\n            raw = response.read()\n\n            if not raw:\n                return None\n\n            return json.loads(raw.decode(\"utf-8\"))\n\n    except urllib.error.HTTPError as e:\n        detail = e.read().decode(\"utf-8\", errors=\"replace\")\n        raise RuntimeError(\n            \"%s %s -> HTTP %s\\n%s\" %\n            (method, path, e.code, detail)\n        )\n\n    except Exception as e:\n        raise RuntimeError(\n            \"%s %s -> %s\" %\n            (method, path, e)\n        )\n\n\ndef get(path, timeout=120):\n    return api(\"GET\", path, timeout=timeout)\n\n\ndef post(path, data):\n    return api(\"POST\", path, data)\n\ndef put(path, data):\n    return api(\"PUT\", path, data)\n\nADD_OPTIMIZED_TAG = os.environ.get(\"SMART_OPTIMIZER_ADD_TAG\", \"0\").lower() in (\"1\", \"true\", \"yes\", \"on\")\nOPTIMIZED_TAG_LABEL = \"smart-optimized\"\n\ndef add_optimized_tag_to_series(series_id):\n    if not ADD_OPTIMIZED_TAG:\n        return\n    try:\n        tags = get(\"/tag\") or []\n        tag = next((x for x in tags if str(x.get(\"label\") or \"\").lower() == OPTIMIZED_TAG_LABEL), None)\n        if not tag:\n            tag = post(\"/tag\", {\"label\": OPTIMIZED_TAG_LABEL})\n        tag_id = int(tag[\"id\"])\n        series = get(\"/series/%d\" % int(series_id))\n        current = [int(x) for x in (series.get(\"tags\") or [])]\n        if tag_id not in current:\n            series[\"tags\"] = current + [tag_id]\n            put(\"/series/%d\" % int(series_id), series)\n            print(\"    TAGGED: %s\" % OPTIMIZED_TAG_LABEL, flush=True)\n    except Exception as e:\n        print(\"    TAG WARNING:\", e, flush=True)\n\n\n# ============================================================\n# STATE\n# ============================================================\n\ndef blank_state():\n    return {\n        \"version\": 1,\n        \"episodes\": {},\n        \"attempted_releases\": {},\n        \"daily\": {},\n        \"work_queue\": [],\n        \"work_cursor\": 0,\n        \"known_series_ids\": [],\n        \"series_queue\": [],\n        \"series_cursor\": 0,\n        \"queue_initialized\": False\n    }\n\n\ndef load_state():\n    if not os.path.exists(STATE_FILE):\n        return blank_state()\n\n    try:\n        with open(STATE_FILE, \"r\", encoding=\"utf-8\") as f:\n            state = json.load(f)\n\n        state.setdefault(\"version\", 1)\n        state.setdefault(\"episodes\", {})\n        state.setdefault(\"attempted_releases\", {})\n        state.setdefault(\"daily\", {})\n        state.setdefault(\"work_queue\", [])\n        state.setdefault(\"work_cursor\", 0)\n        state.setdefault(\"known_series_ids\", [])\n        state.setdefault(\"series_queue\", [])\n        state.setdefault(\"series_cursor\", 0)\n        state.setdefault(\"queue_initialized\", False)\n\n        return state\n\n    except Exception as e:\n        print(\"WARNING: Could not read state file:\")\n        print(\" \", e)\n        print(\"Using empty state for this run.\")\n        return blank_state()\n\n\ndef save_state(state):\n    if not LIVE:\n        return\n\n    # STATE_FILE is commonly a single-file Docker bind mount.\n    # Replacing its inode with os.replace() can fail with EBUSY.\n    # Write the mounted file in-place and fsync it instead.\n    with open(STATE_FILE, \"w\", encoding=\"utf-8\") as f:\n        json.dump(state, f, indent=2, sort_keys=True)\n        f.flush()\n        os.fsync(f.fileno())\n\n\ndef today_key():\n    return datetime.now().strftime(\"%Y-%m-%d\")\n\n\ndef searches_used_today(state):\n    return int(\n        state.get(\"daily\", {})\n             .get(today_key(), {})\n             .get(\"searches\", 0)\n    )\n\n\ndef increment_search_count(state):\n    day = today_key()\n\n    state.setdefault(\"daily\", {})\n    state[\"daily\"].setdefault(day, {\"searches\": 0})\n\n    state[\"daily\"][day][\"searches\"] += 1\n\n    # Remove ancient daily counters.\n    keys = sorted(state[\"daily\"].keys())\n\n    if len(keys) > 60:\n        for old in keys[:-60]:\n            state[\"daily\"].pop(old, None)\n\n\ndef mark_episode_searched(state, episode_id):\n    key = str(episode_id)\n    state[\"episodes\"].setdefault(key, {})\n    entry = state[\"episodes\"][key]\n    entry[\"last_search\"] = now_ts()\n    entry[\"search_cycles\"] = min(2, int(entry.get(\"search_cycles\", 0)) + 1)\n\n\ndef release_key(release):\n    for field in (\n        \"guid\",\n        \"downloadUrl\",\n        \"infoUrl\",\n        \"title\"\n    ):\n        value = release.get(field)\n        if value:\n            return str(value)\n\n    return str(release.get(\"title\", \"UNKNOWN\"))\n\n\ndef release_recently_attempted(state, release):\n    key = release_key(release)\n\n    ts = state.get(\"attempted_releases\", {}).get(key)\n\n    if not ts:\n        return False\n\n    return age_days(ts) < ATTEMPT_COOLDOWN_DAYS\n\n\ndef mark_release_attempted(state, release):\n    key = release_key(release)\n    state.setdefault(\"attempted_releases\", {})\n    state[\"attempted_releases\"][key] = now_ts()\n\n\ndef clean_old_attempts(state):\n    cutoff = ATTEMPT_COOLDOWN_DAYS + 30\n\n    remove = []\n\n    for key, ts in state.get(\"attempted_releases\", {}).items():\n        if age_days(ts) > cutoff:\n            remove.append(key)\n\n    for key in remove:\n        state[\"attempted_releases\"].pop(key, None)\n\n\n# ============================================================\n# MEDIA PARSING\n# ============================================================\n\ndef codec_from_text(text):\n    \"\"\"\n    Detect VIDEO codec from a release title.\n\n    Normalized values:\n      x265\n      x264\n      av1\n      vp9\n      vp8\n      vc1\n      unknown\n\n    MKV/MP4 are containers and intentionally do NOT determine codec.\n    \"\"\"\n    text = text or \"\"\n\n    # H.265 / HEVC\n    if re.search(\n        r'(?i)(?:\\bx[ ._-]?265\\b|\\bh[ ._-]?265\\b|\\bhevc\\b)',\n        text\n    ):\n        return \"x265\"\n\n    # H.264 / AVC\n    if re.search(\n        r'(?i)(?:\\bx[ ._-]?264\\b|\\bh[ ._-]?264\\b|\\bavc\\b)',\n        text\n    ):\n        return \"x264\"\n\n    # AV1 / AV01\n    if re.search(\n        r'(?i)(?:\\bav[ ._-]?1\\b|\\bav01\\b)',\n        text\n    ):\n        return \"av1\"\n\n    # VP9 / VP09\n    if re.search(\n        r'(?i)(?:\\bvp[ ._-]?9\\b|\\bvp09\\b)',\n        text\n    ):\n        return \"vp9\"\n\n    # VP8 / VP08\n    if re.search(\n        r'(?i)(?:\\bvp[ ._-]?8\\b|\\bvp08\\b)',\n        text\n    ):\n        return \"vp8\"\n\n    # VC-1 / VC1\n    if re.search(\n        r'(?i)\\bvc[ ._-]?1\\b',\n        text\n    ):\n        return \"vc1\"\n\n    return \"unknown\"\n\n\ndef audio_channels_from_text(text):\n    text = (text or \"\").lower()\n\n    patterns = [\n        (7.1, r\"\\b7[\\s._-]?1\\b\"),\n        (5.1, r\"\\b5[\\s._-]?1\\b\"),\n        (2.1, r\"\\b2[\\s._-]?1\\b\"),\n        (2.0, r\"\\b2[\\s._-]?0\\b\"),\n        (1.0, r\"\\b1[\\s._-]?0\\b\"),\n    ]\n\n    for channels, pattern in patterns:\n        if re.search(pattern, text):\n            return channels\n\n    if \"stereo\" in text:\n        return 2.0\n\n    return None\n\n\nDANGEROUS_EXTENSIONS = (\n    \"exe\", \"scr\", \"bat\", \"cmd\", \"msi\", \"com\",\n    \"pif\", \"vbs\", \"js\", \"jar\", \"ps1\"\n)\n\n\ndef dangerous_release_title(text):\n    \"\"\"\n    Hard-block executable/script payloads.\n    MKV, MP4 and other normal media containers are unaffected.\n    \"\"\"\n    text = text or \"\"\n\n    pattern = (\n        r'(?i)\\.(?:'\n        + '|'.join(re.escape(ext) for ext in DANGEROUS_EXTENSIONS)\n        + r')(?=$|[\\s._\\-\\[\\]\\(\\)])'\n    )\n\n    return bool(re.search(pattern, text))\n\n\ndef dynamic_range_from_text(text):\n    \"\"\"\n    Conservative release-title classification.\n\n    Returns:\n      SDR_UNKNOWN\n      HDR\n      DV_ONLY\n      DV_HDR\n\n    IMPORTANT:\n    DV-only is never acceptable.\n    Dolby Vision must explicitly also advertise HDR/HDR10/HDR10+.\n    \"\"\"\n    text = (text or \"\").lower()\n\n    has_dv = bool(\n        re.search(\n            r\"\\b(dv|dovi|dolby[\\s._-]?vision)\\b\",\n            text\n        )\n    )\n\n    has_hdr = bool(\n        re.search(\n            r\"\\b(hdr10\\+?|hdr|hlg)\\b\",\n            text\n        )\n    )\n\n    if has_dv and has_hdr:\n        return \"DV_HDR\"\n\n    if has_dv:\n        return \"DV_ONLY\"\n\n    if has_hdr:\n        return \"HDR\"\n\n    return \"SDR_UNKNOWN\"\n\n\ndef dynamic_range_allowed(existing_hdr, candidate_range):\n    \"\"\"\n    Dynamic-range replacement policy.\n\n    Candidate SDR_UNKNOWN means the release title does not explicitly\n    advertise HDR/DV. For selection purposes it is allowed exactly like\n    ordinary SDR UNLESS the existing file is positively known to be HDR.\n\n    Rules:\n      existing SDR/unknown -> SDR_UNKNOWN : ALLOW\n      existing SDR/unknown -> HDR         : ALLOW\n      existing SDR/unknown -> DV_HDR      : ALLOW\n\n      existing HDR         -> SDR_UNKNOWN : BLOCK\n      existing HDR         -> HDR         : ALLOW\n      existing HDR         -> DV_HDR      : ALLOW\n\n      DV_ONLY is ALWAYS blocked.\n    \"\"\"\n\n    if candidate_range == \"DV_ONLY\":\n        return False\n\n    if existing_hdr and candidate_range == \"SDR_UNKNOWN\":\n        return False\n\n    return True\n\n\n\ndef hdr_from_media_info(media):\n    if not media:\n        return False\n\n    pieces = []\n\n    for key in (\n        \"videoDynamicRange\",\n        \"videoDynamicRangeType\",\n        \"videoCodec\",\n        \"videoProfile\"\n    ):\n        value = media.get(key)\n\n        if value:\n            pieces.append(str(value))\n\n    text = \" \".join(pieces).lower()\n\n    return bool(\n        re.search(\n            r\"(dolby|dovi|\\bdv\\b|hdr|hlg|pq)\",\n            text\n        )\n    )\n\n\ndef current_audio_channels(media):\n    if not media:\n        return None\n\n    value = media.get(\"audioChannels\")\n\n    try:\n        if value is not None:\n            return float(value)\n    except Exception:\n        pass\n\n    return None\n\n\ndef current_codec(file_obj):\n    media = file_obj.get(\"mediaInfo\") or {}\n\n    codec = codec_from_text(\n        \" \".join([\n            str(media.get(\"videoCodec\", \"\")),\n            str(file_obj.get(\"sceneName\", \"\")),\n            str(file_obj.get(\"relativePath\", \"\"))\n        ])\n    )\n\n    return codec\n\n\ndef quality_resolution(quality_obj):\n    q = quality_obj or {}\n\n    if \"quality\" in q:\n        q = q.get(\"quality\") or {}\n\n    resolution = q.get(\"resolution\")\n\n    try:\n        if resolution:\n            return int(resolution)\n    except Exception:\n        pass\n\n    name = str(q.get(\"name\", \"\"))\n\n    match = re.search(r\"(2160|1080|720|480)\", name)\n\n    if match:\n        return int(match.group(1))\n\n    return 0\n\n\ndef file_resolution(file_obj):\n    media = file_obj.get(\"mediaInfo\") or {}\n\n    width = media.get(\"width\")\n    height = media.get(\"height\")\n\n    try:\n        height = int(height or 0)\n    except Exception:\n        height = 0\n\n    if height >= 2000:\n        return 2160\n\n    if height >= 1000:\n        return 1080\n\n    if height >= 700:\n        return 720\n\n    return quality_resolution(file_obj.get(\"quality\"))\n\n\n# ============================================================\n# QUEUE\n# ============================================================\n\ndef active_episode_ids():\n    ids = set()\n\n    page = 1\n\n    while True:\n        path = (\n            \"/queue?page=%d&pageSize=100\"\n            \"&includeUnknownSeriesItems=true\"\n        ) % page\n\n        data = get(path)\n\n        if not data:\n            break\n\n        records = data.get(\"records\", [])\n\n        for item in records:\n            episode_id = item.get(\"episodeId\")\n\n            if episode_id:\n                ids.add(int(episode_id))\n\n        total = int(data.get(\"totalRecords\", len(records)))\n\n        if page * 100 >= total:\n            break\n\n        page += 1\n\n    return ids\n\n\n# ============================================================\n# LOCAL LIBRARY CANDIDATES\n# ============================================================\n\ndef priority_score(item):\n    \"\"\"\n    Higher = search earlier.\n\n    IMPORTANT:\n    This only decides WHICH existing episodes deserve one of\n    our scarce interactive searches.\n\n    It does NOT decide which release wins after searching.\n    Profile 5 can still prefer a valid <=8 GiB 2160p release.\n    If no valid 2160p exists, smaller qualifying 1080p releases\n    remain fully eligible.\n    \"\"\"\n\n    res = item[\"resolution\"]\n    target = item[\"target_resolution\"]\n    size = item[\"size_mib\"]\n    codec = item[\"codec\"]\n\n    score = 0.0\n\n    # --------------------------------------------------------\n    # PROFILE 5 / 4K-PREFERRED SERIES\n    # --------------------------------------------------------\n    if item[\"profile_id\"] == UHD_PROFILE_ID:\n\n        # Missing target resolution is important, but don't give\n        # every 1080p episode an identical gigantic score.\n        if res < 2160:\n            score += 600000\n\n            # Lower-than-1080p files are much more urgent.\n            if res < 1080:\n                score += 300000\n\n            # Larger existing files have more optimization\n            # potential if no acceptable 4K release exists.\n            score += min(size * 100, 300000)\n\n            # x264 gets extra attention because x265 often gives\n            # worthwhile space savings.\n            if codec == \"x264\":\n                score += 100000\n            elif codec == \"unknown\":\n                score += 50000\n\n            # Already tiny 1080p x265 files can still eventually\n            # be searched for 4K, but should not steal today's\n            # scarce slots from much larger files.\n            if (\n                res == 1080\n                and codec == \"x265\"\n                and size <= COMPACT_1080P_X265_MIB\n            ):\n                score -= 150000\n\n        else:\n            # Already 2160p: only optimization potential matters.\n            score += min(size * 25, 200000)\n\n            if codec == \"x264\":\n                score += 75000\n\n            if size >= LARGE_2160P_MIB:\n                score += 100000\n\n        return score\n\n    # --------------------------------------------------------\n    # NORMAL 1080P PROFILE\n    # --------------------------------------------------------\n\n    # Resolution deficiency has highest priority.\n    if res < target:\n        score += 800000\n        score += (target - res) * 500\n\n    # x264 generally has greater compression-saving potential.\n    if codec == \"x264\":\n        score += 200000\n    elif codec == \"unknown\":\n        score += 100000\n\n    # Large files deserve attention.\n    if size >= LARGE_1080P_MIB:\n        score += 200000\n\n    score += min(size * 50, 250000)\n\n    # Compact 1080p x265 is already in a very good state.\n    if (\n        res >= 1080\n        and codec == \"x265\"\n        and size <= COMPACT_1080P_X265_MIB\n    ):\n        score -= 200000\n\n    return score\n\ndef _queue_entry(series, ep):\n    return {\n        \"series_id\": int(series[\"id\"]),\n        \"series_title\": series.get(\"title\", \"\"),\n        \"episode_id\": int(ep[\"id\"]),\n        \"season\": int(ep.get(\"seasonNumber\", 0)),\n        \"episode\": int(ep.get(\"episodeNumber\", 0)),\n    }\n\n\ndef _episode_sort_key(ep):\n    return (int(ep.get(\"seasonNumber\", 0)), int(ep.get(\"episodeNumber\", 0)), int(ep.get(\"id\", 0)))\n\n\ndef initialize_work_queue(state):\n    \"\"\"Save the complete A-Z SERIES order immediately; load episodes only when reached.\"\"\"\n    series_list = get(\"/series\")\n    ordered = sorted(series_list, key=lambda s: ((s.get(\"title\") or \"\").casefold(), int(s.get(\"id\", 0))))\n    state[\"series_queue\"] = [\n        {\"series_id\": int(s[\"id\"]), \"series_title\": s.get(\"title\", \"\")}\n        for s in ordered if s.get(\"id\")\n    ]\n    state[\"series_cursor\"] = 0\n    state[\"work_queue\"] = []\n    state[\"work_cursor\"] = 0\n    state[\"known_series_ids\"] = [x[\"series_id\"] for x in state[\"series_queue\"]]\n    state[\"queue_initialized\"] = True\n    save_state(state)\n    print(\"PERMANENT A-Z SERIES QUEUE READY:\", len(state[\"series_queue\"]), \"series\", flush=True)\n\n\ndef append_new_series(state):\n    \"\"\"New Sonarr series go to the bottom; normal Sonarr downloading is untouched.\"\"\"\n    series_list = get(\"/series\")\n    known = set(int(x) for x in state.get(\"known_series_ids\", []))\n    added = 0\n    for series in series_list:\n        sid = int(series.get(\"id\", 0) or 0)\n        if not sid or sid in known:\n            continue\n        state.setdefault(\"series_queue\", []).append({\n            \"series_id\": sid,\n            \"series_title\": series.get(\"title\", \"\")\n        })\n        known.add(sid)\n        added += 1\n        print(\"APPENDED NEW SERIES TO BOTTOM:\", series.get(\"title\"), flush=True)\n    state[\"known_series_ids\"] = sorted(known)\n    if added:\n        save_state(state)\n    return added\n\n\ndef load_next_series_episodes(state):\n    \"\"\"Expand only the next series into episode work when the cursor reaches it.\"\"\"\n    sq = state.get(\"series_queue\", [])\n    sc = int(state.get(\"series_cursor\", 0))\n    if sc >= len(sq):\n        return False\n\n    sref = sq[sc]\n    sid = int(sref[\"series_id\"])\n    try:\n        episodes = get(\"/episode?seriesId=%d\" % sid)\n    except Exception as e:\n        print(\"WARNING: Could not load:\", sref.get(\"series_title\"), e, flush=True)\n        return False\n\n    entries = [\n        {\n            \"series_id\": sid,\n            \"series_title\": sref.get(\"series_title\", \"\"),\n            \"episode_id\": int(ep[\"id\"]),\n            \"season\": int(ep.get(\"seasonNumber\", 0)),\n            \"episode\": int(ep.get(\"episodeNumber\", 0)),\n        }\n        for ep in sorted(episodes, key=_episode_sort_key)\n        if ep.get(\"hasFile\") and ep.get(\"episodeFileId\")\n    ]\n\n    state.setdefault(\"work_queue\", []).extend(entries)\n    state[\"series_cursor\"] = sc + 1\n    save_state(state)\n    print(\"LOADED NEXT SERIES:\", sref.get(\"series_title\"), \"·\", len(entries), \"episodes\", flush=True)\n    return True\n\n\ndef item_from_queue_entry(entry, queued_ids, state):\n    \"\"\"Load metadata only for the next queued episode, never the whole library.\"\"\"\n    episode_id = int(entry[\"episode_id\"])\n    if episode_id in queued_ids:\n        return None\n    hist = state.get(\"episodes\", {}).get(str(episode_id), {})\n    cycles = int(hist.get(\"search_cycles\", 0))\n    last_search = hist.get(\"last_search\")\n    if cycles >= 2 or (cycles == 1 and last_search and age_days(last_search) < 180):\n        return None\n    try:\n        series = get(\"/series/%d\" % int(entry[\"series_id\"]))\n        ep = get(\"/episode/%d\" % episode_id)\n    except Exception as e:\n        print(\"    SKIP metadata error:\", entry.get(\"series_title\"), \"S%02dE%02d\" % (entry.get(\"season\",0),entry.get(\"episode\",0)), e, flush=True)\n        return None\n    if not ep.get(\"hasFile\") or not ep.get(\"episodeFileId\"):\n        return None\n    profile_id = int(series.get(\"qualityProfileId\", 0))\n    if profile_id not in (NORMAL_PROFILE_ID, UHD_PROFILE_ID):\n        return None\n    try:\n        file_obj = get(\"/episodefile/%d\" % int(ep[\"episodeFileId\"]))\n    except Exception as e:\n        print(\"    SKIP file metadata error:\", e, flush=True)\n        return None\n    resolution = file_resolution(file_obj)\n    if not resolution:\n        return None\n    media = file_obj.get(\"mediaInfo\") or {}\n    target = 2160 if profile_id == UHD_PROFILE_ID else 1080\n    return {\n        \"series_id\": int(series[\"id\"]),\n        \"series_title\": series.get(\"title\", entry.get(\"series_title\", \"\")),\n        \"episode_id\": episode_id,\n        \"season\": int(ep.get(\"seasonNumber\", entry.get(\"season\", 0))),\n        \"episode\": int(ep.get(\"episodeNumber\", entry.get(\"episode\", 0))),\n        \"episode_title\": ep.get(\"title\", \"\"),\n        \"profile_id\": profile_id,\n        \"target_resolution\": target,\n        \"file\": file_obj,\n        \"resolution\": resolution,\n        \"size_mib\": mib(file_obj.get(\"size\", 0)),\n        \"codec\": current_codec(file_obj),\n        \"audio_channels\": current_audio_channels(media),\n        \"hdr\": hdr_from_media_info(media),\n        \"cooldown_days\": 180,\n        \"priority\": 0,\n    }\n\n\ndef next_work_items(state, queued_ids, limit):\n    if not state.get(\"queue_initialized\") or not state.get(\"series_queue\"):\n        print(\"Creating persistent A-Z series queue...\", flush=True)\n        initialize_work_queue(state)\n\n    append_new_series(state)\n    selected = []\n\n    while len(selected) < limit:\n        queue = state.get(\"work_queue\", [])\n        cursor = int(state.get(\"work_cursor\", 0))\n\n        if cursor >= len(queue):\n            if not load_next_series_episodes(state):\n                break\n            continue\n\n        entry = queue[cursor]\n        state[\"work_cursor\"] = cursor + 1\n        if LIVE:\n            save_state(state)\n\n        item = item_from_queue_entry(entry, queued_ids, state)\n        if item is not None:\n            selected.append(item)\n\n    return selected\n\n\n# ============================================================\n# RELEASE EVALUATION\n# ============================================================\n\ndef rejection_allowed(rejection):\n    \"\"\"\n    We ONLY ignore Sonarr's cutoff rejection because the\n    optimizer intentionally evaluates replacements beyond\n    Sonarr's normal cutoff.\n\n    Every other Sonarr rejection remains respected.\n    \"\"\"\n\n    reason = \"\"\n\n    if isinstance(rejection, dict):\n        reason = str(\n            rejection.get(\"reason\")\n            or rejection.get(\"message\")\n            or \"\"\n        )\n    else:\n        reason = str(rejection)\n\n    reason = reason.lower()\n\n    return \"existing file meets cutoff\" in reason\n\n\ndef sonarr_rejections_ok(release):\n    rejected = release.get(\"rejections\") or []\n\n    for rejection in rejected:\n        if not rejection_allowed(rejection):\n            return False\n\n    return True\n\n\ndef candidate_resolution(release):\n    return quality_resolution(release.get(\"quality\"))\n\n\ndef evaluate_release(item, release, state):\n    title = str(release.get(\"title\", \"\"))\n\n    # HARD SAFETY BLOCK:\n    # Never grab executable/script payloads.\n    if dangerous_release_title(title):\n        return None\n\n    if not sonarr_rejections_ok(release):\n        return None\n\n    if release_recently_attempted(state, release):\n        return None\n\n    # Require positive seeder evidence before grabbing.\n    # Unknown, missing, invalid or zero seeders are rejected.\n    seeders = release.get(\"seeders\")\n\n    try:\n        seeders = int(seeders)\n    except (TypeError, ValueError):\n        return None\n\n    if seeders < MIN_SEEDERS:\n        return None\n\n    new_res = candidate_resolution(release)\n\n    if not new_res:\n        return None\n\n    old_res = item[\"resolution\"]\n    target = item[\"target_resolution\"]\n\n    # NEVER exceed profile target.\n    if new_res > target:\n        return None\n\n    # NEVER resolution downgrade.\n    if new_res < old_res:\n        return None\n\n    new_size = mib(release.get(\"size\", 0))\n\n    if new_size <= 0:\n        return None\n\n    codec = codec_from_text(title)\n    candidate_audio = audio_channels_from_text(title)\n\n    dynamic_range = dynamic_range_from_text(title)\n    candidate_hdr = dynamic_range in (\"HDR\", \"DV_HDR\")\n\n    # HARD RULE:\n    # Dolby Vision without an explicit HDR fallback is rejected.\n    if not dynamic_range_allowed(item[\"hdr\"], dynamic_range):\n        return None\n\n    old_size = item[\"size_mib\"]\n\n    if old_size <= 0:\n        return None\n\n    saving = (\n        (old_size - new_size)\n        / old_size\n        * 100.0\n    )\n\n    # Same-resolution replacements must save 5-50%.\n    # The only size-growth exception is an explicit UHD-profile upgrade\n    # from an existing 1080p file to a 2160p candidate: that candidate may\n    # be the same size or at most 10% larger. Smaller 4K candidates remain\n    # subject to the 50% maximum-saving guardrail.\n    is_uhd_upgrade = (\n        item[\"profile_id\"] == UHD_PROFILE_ID\n        and old_res == 1080\n        and new_res == 2160\n    )\n\n    if is_uhd_upgrade:\n        if saving < -10.0:\n            return None\n        if saving > MAX_SAVING_PERCENT:\n            return None\n    else:\n        if saving < MIN_SAVING_PERCENT:\n            return None\n        if saving > MAX_SAVING_PERCENT:\n            return None\n\n    # Audio protection.\n    #\n    # If current file has known channel count, candidate must\n    # ALSO tell us its channel count and it cannot be lower.\n    old_audio = item[\"audio_channels\"]\n\n    if old_audio is not None:\n        if candidate_audio is None:\n            return None\n\n        if candidate_audio < old_audio:\n            return None\n\n    # HDR/DV protection at BOTH 1080p and 2160p.\n    #\n    # Existing HDR/DV -> SDR/unknown = NEVER for space saving.\n    #\n    # Existing SDR -> HDR/DV is allowed.\n    \n\n    return {\n        \"release\": release,\n        \"resolution\": new_res,\n        \"size_mib\": new_size,\n        \"codec\": codec,\n        \"audio\": candidate_audio,\n        \"hdr\": candidate_hdr,\n        \"dynamic_range\": dynamic_range,\n        \"saving_percent\": saving,\n        \"reason\": \"smaller same-resolution file\"\n    }\n\n\ndef choose_best(item, releases, state):\n    valid = []\n\n    for release in releases:\n        result = evaluate_release(\n            item,\n            release,\n            state\n        )\n\n        if result:\n            valid.append(result)\n\n    if not valid:\n        return None\n\n    old_res = item[\"resolution\"]\n\n    higher = [\n        x for x in valid\n        if x[\"resolution\"] > old_res\n    ]\n\n    if higher:\n        # Higher resolution wins.\n        #\n        # Within that resolution:\n        # x265 preferred, then smaller file.\n        higher.sort(\n            key=lambda x: (\n                x[\"resolution\"],\n                1 if x[\"codec\"] == \"x265\" else 0,\n                -x[\"size_mib\"]\n            ),\n            reverse=True\n        )\n\n        return higher[0]\n\n    # Same resolution:\n    #\n    # Smaller file is the main purpose.\n    # x265 breaks close/equal choices rather than allowing a\n    # larger x265 to beat a smaller x264.\n    valid.sort(\n        key=lambda x: (\n            x[\"size_mib\"],\n            0 if x[\"codec\"] == \"x265\" else 1\n        )\n    )\n\n    return valid[0]\n\n\n# ============================================================\n# OUTPUT\n# ============================================================\n\ndef describe_item(number, item):\n    audio = (\n        \"%.1f\" % item[\"audio_channels\"]\n        if item[\"audio_channels\"] is not None\n        else \"unknown\"\n    )\n\n    print(\n        \"%2d. %s S%02dE%02d\"\n        % (\n            number,\n            item[\"series_title\"],\n            item[\"season\"],\n            item[\"episode\"]\n        )\n    )\n\n    print(\n        \"    Current: %dp | %s | %.0f MiB | %sch | HDR=%s\"\n        % (\n            item[\"resolution\"],\n            item[\"codec\"],\n            item[\"size_mib\"],\n            audio,\n            item[\"hdr\"]\n        )\n    )\n\n    print(\n        \"    Target: %dp | priority %.0f | cooldown %d days\"\n        % (\n            item[\"target_resolution\"],\n            item[\"priority\"],\n            item[\"cooldown_days\"]\n        )\n    )\n\n\ndef describe_choice(choice):\n    release = choice[\"release\"]\n\n    audio = (\n        \"%.1f\" % choice[\"audio\"]\n        if choice[\"audio\"] is not None\n        else \"unknown\"\n    )\n\n    print(\"    FOUND:\", release.get(\"title\", \"\"))\n\n    print(\n        \"    New: %dp | %s | %.0f MiB | %sch | HDR=%s\"\n        % (\n            choice[\"resolution\"],\n            choice[\"codec\"],\n            choice[\"size_mib\"],\n            audio,\n            choice[\"hdr\"]\n        )\n    )\n\n    if choice[\"saving_percent\"] is not None:\n        print(\n            \"    Saving: %.1f%%\"\n            % choice[\"saving_percent\"]\n        )\n\n    print(\n        \"    Indexer:\",\n        release.get(\"indexer\", \"unknown\")\n    )\n\n    print(\n        \"    Seeders:\",\n        release.get(\"seeders\", \"unknown\")\n    )\n\n\n# ============================================================\n# MAIN\n# ============================================================\n\ndef main():\n    state = load_state()\n\n    if LIVE:\n        clean_old_attempts(state)\n\n    print()\n    print(\"=\" * 68)\n    print(\"SONARR SMART OPTIMIZER\")\n    print(\"=\" * 68)\n\n    if LIVE:\n        print(\"MODE: LIVE\")\n    else:\n        print(\"MODE: DRY RUN -- NO RELEASES WILL BE GRABBED\")\n\n    print(\"Daily interactive-search budget:\", DAILY_SEARCH_BUDGET + DAILY_EXTRA_BUDGET, \"(base %d + today override %d)\" % (DAILY_SEARCH_BUDGET, DAILY_EXTRA_BUDGET))\n    print(\"Same-resolution saving window: %.1f%% to %.1f%%\" % (MIN_SAVING_PERCENT, MAX_SAVING_PERCENT))\n    print(\"UHD-profile 1080p -> 2160p exception: candidate may be up to 10% larger\")\n    print()\n\n    used = searches_used_today(state)\n\n    # Maximum interactive searches in one execution.\n    PER_RUN_SEARCH_BUDGET = max(1, SEARCHES_PER_RUN)\n\n    if LIVE:\n        remaining = min(\n            PER_RUN_SEARCH_BUDGET,\n            max(0, DAILY_SEARCH_BUDGET + DAILY_EXTRA_BUDGET - used)\n        )\n    else:\n        # Dry run does NOT consume persistent budget.\n        remaining = min(\n            PER_RUN_SEARCH_BUDGET,\n            DAILY_SEARCH_BUDGET\n        )\n\n    print(\n        \"Persistent searches already used today:\",\n        used\n    )\n\n    print(\n        \"Searches available this run:\",\n        remaining\n    )\n\n    if remaining <= 0:\n        print()\n        print(\"Daily search budget exhausted.\")\n        print(\"Nothing to do.\")\n        return\n\n    print()\n    print(\"Reading Sonarr queue...\")\n\n    queued_ids = active_episode_ids()\n\n    print(\n        \"Episodes currently represented in queue:\",\n        len(queued_ids)\n    )\n\n    print()\n\n    print(\"Loading persistent A-Z queue...\")\n    if not state.get(\"queue_initialized\"):\n        initialize_work_queue(state)\n    append_new_series(state)\n\n    target_searches = remaining\n    searches = 0\n    grabs = 0\n    no_match = 0\n    errors = 0\n    number = 0\n\n    # The requested count is the number of ACTUAL interactive searches.\n    # Scheduled and manual runs consume the same persistent queue/cursor.\n    # Ineligible queue entries may be skipped, but each /release lookup counts\n    # exactly once toward this run's requested search quota.\n    while searches < target_searches:\n        actual_left = max(0, DAILY_SEARCH_BUDGET + DAILY_EXTRA_BUDGET - searches_used_today(state))\n        if LIVE and actual_left <= 0:\n            print(\"Daily interactive-search budget exhausted.\", flush=True)\n            break\n\n        selected = next_work_items(state, queued_ids, 1)\n        if not selected:\n            break\n\n        item = selected[0]\n        number += 1\n        print(\"-\" * 68)\n        describe_item(number, item)\n        episode_id = item[\"episode_id\"]\n\n        try:\n            print(\"    SEARCHING SONARR NOW...\", flush=True)\n            releases = get(\"/release?episodeId=%d\" % episode_id, timeout=45)\n            searches += 1\n            print(\"    SEARCH PROGRESS: %d / %d\" % (searches, target_searches), flush=True)\n            if LIVE:\n                increment_search_count(state)\n                mark_episode_searched(state, episode_id)\n                save_state(state)\n        except Exception as e:\n            errors += 1\n            print(\"    SEARCH ERROR:\", e, flush=True)\n            print()\n            continue\n\n        choice = choose_best(item, releases, state)\n        if not choice:\n            no_match += 1\n            print(\"    KEEP CURRENT: no qualifying replacement.\", flush=True)\n            print()\n            continue\n\n        describe_choice(choice)\n\n        if not LIVE:\n            grabs += 1\n            print(\"    DRY RUN: WOULD GRAB\", flush=True)\n            print(\"    QUALIFYING REPLACEMENTS FOUND: %d\" % grabs, flush=True)\n            print()\n            continue\n\n        try:\n            fresh_queue = active_episode_ids()\n            if episode_id in fresh_queue:\n                print(\"    SKIP: episode entered Sonarr queue while we were evaluating it.\", flush=True)\n                print()\n                continue\n        except Exception as e:\n            print(\"    SKIP: could not perform final queue safety check:\", e, flush=True)\n            print()\n            continue\n\n        try:\n            post(\"/release\", choice[\"release\"])\n            grabs += 1\n            mark_release_attempted(state, choice[\"release\"])\n            save_state(state)\n            add_optimized_tag_to_series(item[\"series_id\"])\n            print(\"    LIVE: RELEASE SENT TO SONARR\", flush=True)\n            print(\"    QUALIFYING REPLACEMENTS FOUND: %d\" % grabs, flush=True)\n            print(\"    Existing episode remains until Sonarr successfully downloads and imports replacement.\")\n        except Exception as e:\n            errors += 1\n            print(\"    GRAB ERROR:\", e, flush=True)\n        print()\n\n    print(\"=\" * 68)\n    print(\"SUMMARY\")\n    print(\"=\" * 68)\n\n    print(\"Interactive searches this run:\", searches)\n\n    if LIVE:\n        print(\"Releases sent to Sonarr:\", grabs)\n        print(\n            \"Persistent searches used today:\",\n            searches_used_today(state)\n        )\n    else:\n        print(\"Downloads started: 0\")\n        print(\"State changes: 0\")\n\n    print(\"No qualifying replacement:\", no_match)\n    print(\"Errors:\", errors)\n\n    if not LIVE:\n        print()\n        print(\n            \"DRY RUN COMPLETE -- no releases were grabbed and \"\n            \"no persistent cooldown changes were made.\"\n        )\n\n\nif __name__ == \"__main__\":\n    main()\n"
def ensure_bundled_optimizers():
    os.makedirs(BUNDLED_DIR, exist_ok=True)
    for path, source in ((BUNDLED_RADARR, _EMBEDDED_RADARR), (BUNDLED_SONARR, _EMBEDDED_SONARR)):
        data = source.encode("utf-8")
        try:
            with open(path, "rb") as f:
                if f.read() == data:
                    continue
        except Exception:
            pass
        with open(path, "wb") as f:
            f.write(data)
            f.flush(); os.fsync(f.fileno())

ensure_bundled_optimizers()
OPTIMIZER = os.environ.get("RADARR_OPTIMIZER_SCRIPT", BUNDLED_RADARR)
STATE_FILE = os.environ.get("RADARR_OPTIMIZER_STATE", os.path.join(BASE_DIR, "radarr-smart-optimizer-state.json"))
RADARR_URL = os.environ.get("RADARR_URL", "http://127.0.0.1:7878").rstrip("/")
SONARR_URL = os.environ.get("SONARR_URL", "http://127.0.0.1:8989").rstrip("/")
SONARR_KEY = os.environ.get("SONARR_KEY", "").strip()
SONARR_STATE_FILE = os.environ.get("SONARR_OPTIMIZER_STATE", os.path.join(BASE_DIR, "sonarr-smart-optimizer-state.json"))
API_KEY = os.environ.get("RADARR_KEY", "").strip()
HOST = os.environ.get("SMART_UI_HOST", os.environ.get("RADARR_UI_HOST", "127.0.0.1"))
PORT = int(os.environ.get("SMART_UI_PORT", os.environ.get("RADARR_UI_PORT", "8788")))
ENABLE_ACTIONS = os.environ.get("SMART_UI_ENABLE_ACTIONS", "1").lower() in ("1", "true", "yes")
HISTORY_PAGES = max(1, min(20, int(os.environ.get("RADARR_UI_HISTORY_PAGES", "5"))))
MAX_OUTPUT = 50000
CONTROL_FILE = os.environ.get("SMART_OPTIMIZER_CONTROL", os.path.join(BASE_DIR, "smart-optimizer-control.json"))
SONARR_OPTIMIZER = os.environ.get("SONARR_OPTIMIZER_SCRIPT", BUNDLED_SONARR)
RADARR_BASE_BUDGET = int(os.environ.get("RADARR_DAILY_SEARCH_BUDGET", "400"))
SONARR_BASE_BUDGET = int(os.environ.get("SONARR_DAILY_SEARCH_BUDGET", "400"))
MAX_MANUAL = max(1, int(os.environ.get("SMART_UI_MAX_MANUAL_SEARCHES", "10000")))
CONNECTION_FILE = os.environ.get("SMART_OPTIMIZER_CONNECTIONS", os.path.join(BASE_DIR, "smart-optimizer-connections.json"))

def load_connections():
    try:
        with open(CONNECTION_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def save_connections(data):
    os.makedirs(os.path.dirname(CONNECTION_FILE), exist_ok=True)
    with open(CONNECTION_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.flush(); os.fsync(f.fileno())

def connection(app):
    data = load_connections().get(app, {})
    if app == "radarr":
        fallback_url, fallback_key, default_port = RADARR_URL, API_KEY, 7878
    else:
        fallback_url, fallback_key, default_port = SONARR_URL, SONARR_KEY, 8989
    parsed = urllib.parse.urlparse(fallback_url if "://" in fallback_url else "http://" + fallback_url)
    scheme = str(data.get("scheme") or parsed.scheme or "http").lower()
    host = str(data.get("host") or parsed.hostname or "127.0.0.1").strip()
    port = int(data.get("port") or parsed.port or default_port)
    key = str(data.get("api_key") or fallback_key or "").strip()
    return {"scheme": scheme if scheme in ("http", "https") else "http", "host": host, "port": port, "api_key": key}

def connection_url(app):
    cfg = connection(app)
    return "%s://%s:%d" % (cfg["scheme"], cfg["host"], cfg["port"])

def update_connection(app, scheme, host, port, api_key):
    if app not in ("radarr", "sonarr"): raise ValueError("Unknown app")
    scheme = scheme.lower().strip()
    host = host.strip().rstrip("/")
    if scheme not in ("http", "https"): raise ValueError("Scheme must be http or https")
    if not host or "/" in host: raise ValueError("Enter only the hostname or IP address")
    port = int(port)
    if not 1 <= port <= 65535: raise ValueError("Port must be 1-65535")
    current = connection(app)
    key = api_key.strip() or current["api_key"]
    if not key: raise ValueError("API key is required")
    data = load_connections()
    data[app] = {"scheme": scheme, "host": host, "port": port, "api_key": key}
    save_connections(data)

def test_connection(app, scheme=None, host=None, port=None, api_key=None):
    cfg = connection(app)
    scheme = (scheme or cfg["scheme"]).strip().lower()
    host = (host or cfg["host"]).strip().rstrip("/")
    port = int(port or cfg["port"])
    key = (api_key or "").strip() or cfg["api_key"]
    if not key: raise RuntimeError("API key is missing")
    url = "%s://%s:%d/api/v3/system/status" % (scheme, host, port)
    req = urllib.request.Request(url, headers={"X-Api-Key": key, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=8) as response:
        data = json.loads(response.read().decode("utf-8") or "{}")
    return str(data.get("version") or "connected")

def api_online(app):
    try:
        test_connection(app)
        return True
    except Exception:
        return False

def load_controls():
    try:
        with open(CONTROL_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def save_controls(data):
    os.makedirs(os.path.dirname(CONTROL_FILE), exist_ok=True)
    # CONTROL_FILE may be bind-mounted as a single file in Docker.
    # Replacing the inode fails with EBUSY on such mounts, so update it in place.
    with open(CONTROL_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())

def app_controls(app):
    data = load_controls()
    c = data.get(app, {})
    today = time.strftime("%Y-%m-%d")
    return float(c.get("min_saving_percent", 5.0)), float(c.get("max_saving_percent", 50.0)), int((c.get("daily_extra") or {}).get(today, 0))

def optimized_tag_enabled(app):
    return bool((load_controls().get(app, {}) or {}).get("optimized_tag", False))

def update_optimized_tag(app, enabled):
    data = load_controls(); c = data.setdefault(app, {})
    c["optimized_tag"] = bool(enabled)
    save_controls(data)

def update_saving_window(app, minimum, maximum):
    if not (0 <= minimum <= maximum <= 100):
        raise ValueError("Use 0-100%, and minimum cannot be greater than maximum.")
    data = load_controls(); c = data.setdefault(app, {})
    c["min_saving_percent"] = minimum; c["max_saving_percent"] = maximum
    save_controls(data)

def add_daily_extra(app, amount=50):
    data = load_controls(); c = data.setdefault(app, {}); extras = c.setdefault("daily_extra", {})
    today = time.strftime("%Y-%m-%d")
    extras[today] = int(extras.get(today, 0)) + amount
    # Old overrides are irrelevant; prune them so the file stays tiny.
    c["daily_extra"] = {today: extras[today]}
    save_controls(data)
    return extras[today]

job_lock = threading.Lock()
jobs = {a: {"running": False, "requested": 0, "start": 0, "proc": None, "stopped": False, "started": None, "finished": None, "output": "", "current": "", "last": "", "display_searched": 0, "display_item": "", "returncode": None} for a in ("radarr", "sonarr")}


def radarr_request(path, method="GET", payload=None):
    cfg = connection("radarr")
    if not cfg["api_key"]:
        raise RuntimeError("Radarr API key is not configured")
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        connection_url("radarr") + "/api/v3" + path,
        data=data,
        method=method,
        headers={"X-Api-Key": cfg["api_key"], "Accept": "application/json",
                 "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        raw = response.read().decode("utf-8")
        return json.loads(raw) if raw else {}

def radarr_get(path):
    return radarr_request(path)


def sonarr_get(path):
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


def load_sonarr_state():
    try:
        with open(SONARR_STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"daily": {}, "episodes": {}}


def sonarr_history_records():
    records = []
    for page_num in range(1, HISTORY_PAGES + 1):
        data = sonarr_get("/history?page=%d&pageSize=100&sortKey=date&sortDirection=descending" % page_num)
        batch = data.get("records", [])
        records.extend(batch)
        if len(batch) < 100:
            break
    return records


def sonarr_queue_records():
    data = sonarr_get("/queue?page=1&pageSize=100&sortKey=timeleft&sortDirection=ascending")
    return data.get("records", [])


def sonarr_completed_upgrades(records):
    pending = {}
    upgrades = []
    for event in reversed(records):
        episode_id = event.get("episodeId")
        etype = event.get("eventType")
        data = event.get("data") or {}
        if etype == "episodeFileDeleted" and data.get("reason") == "Upgrade":
            try:
                pending[episode_id] = int(data.get("size") or 0)
            except (TypeError, ValueError):
                pass
        elif etype == "downloadFolderImported" and episode_id in pending:
            try:
                new_size = int(data.get("size") or 0)
            except (TypeError, ValueError):
                new_size = 0
            old_size = pending.pop(episode_id)
            if old_size > 0 and new_size > 0:
                upgrades.append({
                    "date": event.get("date") or "",
                    "title": event.get("sourceTitle") or ("Episode ID %s" % episode_id),
                    "old": old_size, "new": new_size, "saved": old_size - new_size,
                })
    return sorted(upgrades, key=lambda x: x["date"], reverse=True)


def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"daily": {}, "movies": {}}


def history_records():
    records = []
    for page in range(1, HISTORY_PAGES + 1):
        data = radarr_get("/history?page=%d&pageSize=100&sortKey=date&sortDirection=descending" % page)
        batch = data.get("records", [])
        records.extend(batch)
        if len(batch) < 100:
            break
    return records


def queue_records():
    """Return current Radarr download queue rows for the dashboard."""
    data = radarr_get("/queue?page=1&pageSize=100&sortKey=timeleft&sortDirection=ascending")
    return data.get("records", [])


def queue_health(rows):
    """Classify queue rows conservatively from Radarr's own status fields."""
    result = []
    for row in rows:
        status = str(row.get("status") or "").lower()
        tracked = str(row.get("trackedDownloadStatus") or "").lower()
        messages = row.get("statusMessages") or []
        message_parts = []
        for m in messages:
            if not isinstance(m, dict):
                continue
            title = str(m.get("title") or "")
            details = m.get("messages") or []
            if isinstance(details, list):
                detail_text = " ".join(
                    str(x.get("message") or "") if isinstance(x, dict) else str(x)
                    for x in details
                )
            elif isinstance(details, dict):
                detail_text = str(details.get("message") or details)
            else:
                detail_text = str(details)
            message_parts.append((title + " " + detail_text).strip())
        message_text = " ".join(x for x in message_parts if x).strip()
        size = float(row.get("size") or 0)
        left = float(row.get("sizeleft") or 0)
        progress = max(0.0, min(100.0, ((size - left) / size * 100.0) if size else 0.0))
        attention = tracked in ("warning", "error") or status in ("warning", "failed")
        lower_message = message_text.lower()
        if "not an upgrade for existing movie file" in lower_message:
            health = "Optimizer import blocked"
            health_kind = "optimizer_blocked"
        elif tracked == "error" or status == "failed":
            health = "Failed"
            health_kind = "failed"
        elif tracked == "warning" or status == "warning":
            health = "Needs attention"
            health_kind = "warning"
        else:
            health = row.get("status") or row.get("trackedDownloadStatus") or "unknown"
            health_kind = "normal"
        result.append({
            "title": row.get("title") or ("Movie ID %s" % row.get("movieId")),
            "movie_id": row.get("movieId"),
            "status": row.get("status") or row.get("trackedDownloadStatus") or "unknown",
            "tracked": row.get("trackedDownloadStatus") or "",
            "progress": progress,
            "timeleft": row.get("timeleft") or "—",
            "message": message_text,
            "attention": attention,
            "health": health,
            "health_kind": health_kind,
            "queue_id": row.get("id"),
        })
    return result



def ui_dynamic_range_from_text(text):
    """Conservative HDR/DV classifier used by forced-import safety checks."""
    t = str(text or "").lower()
    dv = bool(re.search(r"(?<![a-z0-9])(dv|dovi)(?![a-z0-9])|dolby[\s._-]?vision", t))
    hdr = bool(re.search(r"(?<![a-z0-9])(hdr10\+?|hdr|hlg)(?![a-z0-9])", t))
    if dv and hdr:
        return "DV_HDR"
    if dv:
        return "DV_ONLY"
    if hdr:
        return "HDR"
    return "SDR_UNKNOWN"

def ui_dynamic_range_allowed(current, candidate):
    if candidate == "DV_ONLY":
        return False
    if current == "DV_HDR":
        return candidate == "DV_HDR"
    if current == "HDR":
        return candidate in ("HDR", "DV_HDR")
    return candidate in ("SDR_UNKNOWN", "HDR", "DV_HDR")

def ui_audio_channels_from_text(text):
    t = str(text or "").lower()
    for channels, pattern in (
        (7.1, r"\b7[\s._-]?1\b"),
        (5.1, r"\b5[\s._-]?1\b"),
        (2.1, r"\b2[\s._-]?1\b"),
        (2.0, r"\b2[\s._-]?0\b"),
        (1.0, r"\b1[\s._-]?0\b"),
    ):
        if re.search(pattern, t):
            return channels
    return 2.0 if "stereo" in t else None

def ui_current_media_traits(movie_file):
    media = (movie_file or {}).get("mediaInfo") or {}
    text = " ".join([
        str(media.get("videoDynamicRange") or ""),
        str(media.get("videoDynamicRangeType") or ""),
        str(media.get("audioCodec") or ""),
        str((movie_file or {}).get("sceneName") or ""),
        str((movie_file or {}).get("relativePath") or ""),
    ])
    try:
        channels = float(media.get("audioChannels") or 0) or None
    except (TypeError, ValueError):
        channels = None
    return {
        "dynamic_range": ui_dynamic_range_from_text(text),
        "channels": channels or ui_audio_channels_from_text(text),
        "atmos": "atmos" in text.lower(),
    }


def repair_import(queue_id):
    """Reprocess one completed Radarr queue item using COPY mode.

    This is intentionally user-triggered. It only handles the specific
    quality-hierarchy rejection the optimizer understands; all other queue
    failures remain untouched.
    """
    rows = queue_records()
    row = next((x for x in rows if str(x.get("id")) == str(queue_id)), None)
    if not row:
        raise RuntimeError("Queue item is no longer present")

    classified = queue_health([row])[0]
    if classified.get("health_kind") != "optimizer_blocked":
        raise RuntimeError("This item is not an optimizer import block")
    if str(row.get("status") or "").lower() != "completed":
        raise RuntimeError("Download is not completed")
    if str(row.get("trackedDownloadState") or "").lower() != "importpending":
        raise RuntimeError("Download is not waiting for import")
    if int(row.get("sizeleft") or 0) != 0:
        raise RuntimeError("Download still has data remaining")

    movie_id = int(row.get("movieId"))
    movie = radarr_get("/movie/%d" % movie_id)
    old_file = movie.get("movieFile") or {}
    old_size = int(old_file.get("size") or 0)
    new_size = int(row.get("size") or 0)
    old_res = (((old_file.get("quality") or {}).get("quality") or {}).get("resolution") or 0)
    new_res = (((row.get("quality") or {}).get("quality") or {}).get("resolution") or 0)
    if not old_size or not new_size or not old_res or not new_res:
        raise RuntimeError("Cannot safely compare current and downloaded file")
    if int(new_res) < int(old_res):
        raise RuntimeError("Refusing resolution downgrade")
    if int(new_res) == int(old_res) and new_size >= old_size:
        raise RuntimeError("Refusing same-resolution replacement that is not smaller")

    saving = ((old_size - new_size) / float(old_size)) * 100.0
    rule_min, rule_max, _ = app_controls("radarr")
    if saving < rule_min or saving > rule_max:
        raise RuntimeError("Refusing import outside %.1f%%-%.1f%% saving window (%.1f%%)" %
                           (rule_min, rule_max, saving))

    current_traits = ui_current_media_traits(old_file)

    download_id = str(row.get("downloadId") or "")
    if not download_id:
        raise RuntimeError("Queue item has no downloadId")

    items = radarr_get("/manualimport?downloadId=%s&movieId=%d&filterExistingFiles=true" %
                       (urllib.parse.quote(download_id), movie_id))
    usable = []
    for item in items if isinstance(items, list) else []:
        rejections = item.get("rejections") or []
        reasons = " ".join(str((r.get("reason") or r.get("message") or "")) if isinstance(r, dict) else str(r)
                           for r in rejections).lower()
        # Only override Radarr's source-quality hierarchy. Anything else stays blocked.
        bad = [r for r in rejections if "not an upgrade for existing movie file" not in
               str((r.get("reason") or r.get("message") or "")) .lower()]
        if not bad:
            usable.append(item)
    if len(usable) != 1:
        raise RuntimeError("Expected exactly one safely reprocessable video file, found %d" % len(usable))

    item = usable[0]

    candidate_text = " ".join([
        str(row.get("title") or ""),
        str(item.get("path") or ""),
        str(item.get("relativePath") or ""),
        str(item.get("releaseGroup") or ""),
    ])
    candidate_dr = ui_dynamic_range_from_text(candidate_text)
    candidate_channels = ui_audio_channels_from_text(candidate_text)
    candidate_atmos = "atmos" in candidate_text.lower()

    if not ui_dynamic_range_allowed(current_traits["dynamic_range"], candidate_dr):
        raise RuntimeError("Refusing HDR/Dolby Vision downgrade (%s -> %s)" %
                           (current_traits["dynamic_range"], candidate_dr))
    if current_traits["channels"] and current_traits["channels"] >= 5.0:
        if not candidate_channels or candidate_channels < current_traits["channels"]:
            raise RuntimeError("Refusing audio channel downgrade")
    if current_traits["atmos"] and not candidate_atmos:
        raise RuntimeError("Refusing Atmos downgrade")

    payload = {
        "name": "ManualImport",
        "files": [{
            "path": item.get("path"),
            "folderName": item.get("folderName"),
            "quality": item.get("quality"),
            "languages": item.get("languages") or row.get("languages") or [],
            "releaseGroup": item.get("releaseGroup"),
            "indexerFlags": item.get("indexerFlags") or 0,
            "downloadId": download_id,
            "movieId": movie_id,
        }],
        "importMode": 2
    }
    if not payload["files"][0]["path"]:
        raise RuntimeError("Radarr did not return an importable file path")
    return radarr_request("/command", method="POST", payload=payload)


def completed_upgrades(records):
    """Pair Upgrade deletion -> subsequent import for the same movie.

    This reports observed Radarr history, not predicted optimizer savings.
    """
    pending = {}
    upgrades = []
    # API records are newest first; process oldest first.
    for event in reversed(records):
        movie_id = event.get("movieId")
        etype = event.get("eventType")
        data = event.get("data") or {}
        if etype == "movieFileDeleted" and data.get("reason") == "Upgrade":
            try:
                pending[movie_id] = {
                    "old": int(data.get("size") or 0),
                    "deleted": event.get("date"),
                    "old_path": event.get("sourceTitle") or "",
                }
            except (TypeError, ValueError):
                pass
        elif etype == "downloadFolderImported" and movie_id in pending:
            try:
                new_size = int(data.get("size") or 0)
            except (TypeError, ValueError):
                new_size = 0
            old = pending.pop(movie_id)
            if old["old"] > 0 and new_size > 0:
                upgrades.append({
                    "movie_id": movie_id,
                    "date": event.get("date") or "",
                    "title": event.get("sourceTitle") or ("Movie ID %s" % movie_id),
                    "old": old["old"],
                    "new": new_size,
                    "saved": old["old"] - new_size,
                })
    return sorted(upgrades, key=lambda x: x["date"], reverse=True)


def gib(n):
    return n / (1024.0 ** 3)


def search_count(app):
    state = load_state() if app == "radarr" else load_sonarr_state()
    today = time.strftime("%Y-%m-%d")
    return int((state.get("daily") or {}).get(today, {}).get("searches", 0))

def manual_status(app):
    with job_lock:
        snap = dict(jobs[app])
    searched = max(0, search_count(app) - int(snap.get("start") or 0)) if snap.get("started") else 0
    requested = int(snap.get("requested") or 0)
    if not snap.get("started"):
        state, detail = "idle", ""
    elif snap.get("running"):
        state, detail = ("stopping" if snap.get("stopped") else "running"), ""
    elif snap.get("stopped"):
        state, detail = "stopped", ""
    elif snap.get("returncode") not in (None, 0):
        state, detail = "failed", "Optimizer exited with code %s" % snap.get("returncode")
    else:
        state = "finished"
        detail = "No eligible items" if requested and searched == 0 else ""
    display_searched = int(snap.get("display_searched") or 0)
    display_item = str(snap.get("display_item") or "")
    if not snap.get("running") and searched > display_searched:
        display_searched = searched
        display_item = str(snap.get("last") or display_item)
    return {"state": state, "searched": display_searched if snap.get("started") else searched,
            "requested": requested, "running": bool(snap.get("running")), "detail": detail,
            "current": str(snap.get("current") or "") if snap.get("running") else "",
            "last": display_item}

def run_optimizer(live, app="radarr", searches_per_run=None, daily_extra=0):
    with job_lock:
        other = "sonarr" if app == "radarr" else "radarr"
        if jobs[app]["running"] or jobs[other]["running"]: return False
        start = search_count(app)
        if daily_extra: add_daily_extra(app, daily_extra)
        jobs[app].update(running=True, requested=int(searches_per_run or 0), start=start, proc=None, stopped=False, started=time.time(), finished=None, output="", current="", last="", display_searched=0, display_item="", returncode=None)
    def worker():
        script = OPTIMIZER if app == "radarr" else SONARR_OPTIMIZER
        cmd = ["python3", "-u", script] + (["--live"] if live else [])
        env = os.environ.copy(); env["SMART_OPTIMIZER_CONTROL"] = CONTROL_FILE
        rcfg, scfg = connection("radarr"), connection("sonarr")
        env["RADARR_URL"] = connection_url("radarr"); env["RADARR_KEY"] = rcfg["api_key"]
        env["SONARR_URL"] = connection_url("sonarr"); env["SONARR_KEY"] = scfg["api_key"]
        env["SMART_OPTIMIZER_ADD_TAG"] = "1" if optimized_tag_enabled(app) else "0"
        if searches_per_run: env["RADARR_SEARCHES_PER_RUN" if app == "radarr" else "SONARR_SEARCHES_PER_RUN"] = str(searches_per_run)
        output = ""
        returncode = None
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env, start_new_session=True)
            with job_lock:
                jobs[app]["proc"] = proc; stop_now = jobs[app]["stopped"]
            if stop_now: os.killpg(proc.pid, signal.SIGTERM)
            chunks = []
            for line in proc.stdout:
                chunks.append(line)
                if len(chunks) > 2000:
                    chunks = chunks[-1000:]
                clean = line.strip()
                # Sonarr item lines look like: 20. 'Allo 'Allo! S04E01
                # Avoid whitespace-regex escaping issues: locate the SxxExx token,
                # then derive the series title from the text before it.
                match = re.search(r"S([0-9]{2})E([0-9]{2})", clean)
                if app == "sonarr" and match and ". " in clean[:match.start()]:
                    before = clean[:match.start()].strip()
                    title = before.split(". ", 1)[1].strip()
                    current = "%s · S%sE%s" % (title, match.group(1), match.group(2))
                    with job_lock:
                        jobs[app]["current"] = current
                        jobs[app]["last"] = current
                elif app == "radarr" and "NOW CHECKING:" in clean:
                    current = clean.split("NOW CHECKING:", 1)[1].strip()
                    with job_lock:
                        jobs[app]["current"] = current
                        jobs[app]["last"] = current
                # Pair completed-search count with the episode that produced it.
                # Parse without regex so this cannot fail because of escaping.
                if "SEARCH PROGRESS:" in clean:
                    try:
                        done_text = clean.split("SEARCH PROGRESS:", 1)[1].strip().split("/", 1)[0].strip()
                        done = int(done_text)
                        with job_lock:
                            jobs[app]["display_searched"] = done
                            jobs[app]["display_item"] = str(jobs[app].get("current") or jobs[app].get("last") or "")
                    except (ValueError, IndexError):
                        pass
                with job_lock:
                    jobs[app]["output"] = "".join(chunks)[-MAX_OUTPUT:]
            proc.wait()
            output = "".join(chunks)
            returncode = proc.returncode
        except Exception as exc:
            output = "ERROR: %s" % exc
            returncode = -1
        with job_lock: jobs[app].update(running=False, finished=time.time(), proc=None, output=(output or "")[-MAX_OUTPUT:], returncode=returncode)
    threading.Thread(target=worker, daemon=True).start(); return True

def stop_optimizer(app):
    with job_lock:
        item = jobs[app]
        if not item["running"]: return False
        item["stopped"] = True; proc = item.get("proc")
    if proc and proc.poll() is None:
        try: os.killpg(proc.pid, signal.SIGTERM)
        except Exception:
            try: proc.terminate()
            except Exception: pass
    return True


CSS = """
*{box-sizing:border-box}
:root{color-scheme:dark;--bg:#0b0e13;--panel:#121720;--panel2:#161c26;--line:#242b36;--text:#f3f4f6;--muted:#8993a4;--accent:#7dd3fc;--accent2:#a78bfa;--good:#86efac;--bad:#fda4af;--warn:#fde68a}
html,body{margin:0;min-height:100%;background:var(--bg);color:var(--text)}
body{font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:radial-gradient(circle at 18% -10%,rgba(59,130,246,.10),transparent 32rem),radial-gradient(circle at 90% 0%,rgba(168,85,247,.08),transparent 28rem),var(--bg)}
a{color:inherit}
.shell{max-width:1220px;margin:0 auto;padding:30px 28px 54px}
.topbar{display:flex;align-items:center;justify-content:space-between;gap:20px;margin-bottom:28px}
.brand{display:flex;align-items:center;gap:13px}.mark{width:42px;height:42px;border-radius:12px;display:grid;place-items:center;font-size:20px;font-weight:800;background:linear-gradient(145deg,#2563eb,#7c3aed);box-shadow:inset 0 1px rgba(255,255,255,.22),0 8px 24px rgba(37,99,235,.18)}
.brandcopy h1{margin:0;font-size:1.15rem;letter-spacing:-.025em}.brandcopy div{font-size:.76rem;color:var(--muted);margin-top:2px}
.nav{display:flex;align-items:center;gap:8px}.appswitch{display:flex;gap:6px;padding:4px;border:1px solid var(--line);background:#0e131a;border-radius:11px}.appswitch a{text-decoration:none;padding:7px 12px;border-radius:8px;color:#8f9bad;font-size:.75rem;font-weight:700}.appswitch a:hover{color:#fff;background:#17202c}.appswitch a.active{color:#fff;background:#1d2939}.homewrap{min-height:70vh;display:grid;place-items:center}.homecard{text-align:center;max-width:720px}.homecard h1{font-size:2.25rem;margin:0 0 8px;letter-spacing:-.05em}.homecard p{color:var(--muted);margin:0 0 28px}.chooser{display:grid;grid-template-columns:1fr 1fr;gap:14px}.choice{text-decoration:none;text-align:left;padding:24px;border-radius:16px;border:1px solid var(--line);background:linear-gradient(180deg,var(--panel2),var(--panel));transition:.15s}.choice:hover{transform:translateY(-2px);border-color:#3b4758}.choice b{display:block;font-size:1.15rem;margin-bottom:6px}.choice span{font-size:.78rem;color:var(--muted)}.navchip,.status{height:34px;display:inline-flex;align-items:center;gap:8px;padding:0 11px;border-radius:9px;border:1px solid var(--line);background:#10151d;color:#b8c0cc;font-size:.76rem}
.dot{width:7px;height:7px;border-radius:999px;background:var(--good);box-shadow:0 0 10px rgba(134,239,172,.55)}
.hero{display:flex;justify-content:space-between;align-items:flex-end;gap:24px;margin-bottom:18px}
.hero h2{font-size:1.75rem;line-height:1.1;letter-spacing:-.04em;margin:0 0 7px}.hero p{margin:0;color:var(--muted);font-size:.86rem}
.actions{display:flex;gap:8px;flex-wrap:wrap}.actions form{margin:0}
button{height:36px;padding:0 13px;border-radius:9px;border:1px solid #334155;background:#172033;color:#e5e7eb;font-weight:700;font-size:.78rem;cursor:pointer}
button:hover:not(:disabled){background:#1d2940}button.live{background:#2a1720;border-color:#5f2437;color:#fecdd3}button.live:hover:not(:disabled){background:#351b27}button:disabled{opacity:.38;cursor:not-allowed}
.notice{margin:0 0 16px;padding:10px 12px;border-radius:10px;border:1px solid var(--line);background:#10151d;color:var(--muted);font-size:.78rem}.notice.bad{color:var(--bad)}
.grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-bottom:16px}
.stat{background:linear-gradient(180deg,var(--panel2),var(--panel));border:1px solid var(--line);border-radius:13px;padding:16px;min-height:120px}
.stathead{display:flex;align-items:center;justify-content:space-between;gap:12px;color:#aab3c1;font-size:.75rem}.stathead span:first-child{display:flex;align-items:center;gap:8px}.mini{width:24px;height:24px;border-radius:7px;display:grid;place-items:center;background:#0f141c;border:1px solid #222a35;color:#cbd5e1;font-size:.74rem}
.value{font-size:1.85rem;font-weight:760;letter-spacing:-.045em;margin-top:18px}.good{color:var(--good)}.bad{color:var(--bad)}.muted{color:var(--muted)}.sub{font-size:.73rem;color:var(--muted);margin-top:6px}
.layout{display:grid;grid-template-columns:minmax(0,1.7fr) minmax(280px,.8fr);gap:16px}
.panel{background:linear-gradient(180deg,#141a23,#10151c);border:1px solid var(--line);border-radius:13px;overflow:hidden}
.panel+.panel{margin-top:16px}.layout .panel+.panel{margin-top:0}
.panelhead{display:flex;justify-content:space-between;align-items:flex-start;gap:14px;padding:16px 17px 13px;border-bottom:1px solid var(--line)}.panelhead h3{font-size:.9rem;margin:0;letter-spacing:-.015em}.panelhead p{font-size:.73rem;color:var(--muted);margin:4px 0 0}.badge{font-size:.64rem;padding:5px 7px;border-radius:999px;border:1px solid #2a3340;color:#93a4b8;background:#0e131a;white-space:nowrap}
table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:12px 17px;border-bottom:1px solid #1f2630;font-size:.79rem}th{font-size:.62rem;color:#667085;text-transform:uppercase;letter-spacing:.09em;background:#0f141b}tr:last-child td{border-bottom:0}td:first-child{max-width:520px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.sidecontent{padding:16px}.metricline{display:flex;align-items:center;justify-content:space-between;padding:11px 0;border-bottom:1px solid #202731;font-size:.78rem}.metricline:last-child{border-bottom:0}.metricline span:first-child{color:var(--muted)}.metricline b{font-size:.8rem}
pre{margin:0;white-space:pre-wrap;word-break:break-word;max-height:305px;overflow:auto;background:#0c1117;padding:15px 17px;color:#bbc5d3;font:11.5px/1.55 ui-monospace,SFMono-Regular,Menlo,monospace}
.footer{padding-top:22px;text-align:center;font-size:.68rem;color:#4c5667}
.toolbar{display:flex;gap:10px;align-items:center;margin-bottom:16px}.searchbox{position:relative;flex:1}.searchbox input{width:100%;height:40px;border-radius:10px;border:1px solid var(--line);background:#0f141b;color:var(--text);padding:0 14px 0 38px;outline:none;font-size:.8rem}.searchbox input:focus{border-color:#3b82f6;box-shadow:0 0 0 3px rgba(59,130,246,.10)}.searchicon{position:absolute;left:13px;top:10px;color:#64748b}.queueitem{padding:14px 17px;border-bottom:1px solid #1f2630}.queueitem.extra{display:none}.queueitem:last-child{border-bottom:0}.qtop{display:flex;justify-content:space-between;gap:12px;align-items:center}.qtitle{font-size:.8rem;font-weight:650;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.qmeta{font-size:.7rem;color:var(--muted);margin-top:5px}.progress{height:5px;background:#0b1016;border-radius:99px;overflow:hidden;margin-top:10px}.progress span{display:block;height:100%;background:linear-gradient(90deg,#3b82f6,#8b5cf6);border-radius:99px}.attention{color:var(--warn)}.empty{padding:22px 17px;color:var(--muted);font-size:.78rem}.expandbar{width:100%;height:38px;border:0;border-top:1px solid var(--line);border-radius:0;background:#10161e;color:#9aa6b7;font-size:.74rem;box-shadow:none}.expandbar:hover:not(:disabled){transform:none;background:#151c26;color:#e5e7eb}.controlbar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:0 0 16px}.controlbox{display:flex;gap:7px;align-items:center;padding:7px 9px;border:1px solid var(--line);border-radius:10px;background:#10151d}.controlbox label{font-size:.7rem;color:var(--muted)}.controlbox input{width:62px;height:32px;border:1px solid #303947;border-radius:7px;background:#0b1016;color:var(--text);padding:0 8px}.controlbox button{height:32px}.sectiontabs{display:flex;gap:5px;margin-bottom:12px}.tab{font-size:.72rem;padding:6px 9px;border-radius:8px;background:#10151d;border:1px solid var(--line);color:#8e99aa}.tab.active{color:#e5e7eb;background:#17202c}.kpi{font-size:.66rem;color:#667085;text-transform:uppercase;letter-spacing:.08em}
.manualstate{font-size:.78rem;font-weight:700;color:#dbeafe}.stopbtn{border-color:#6b2635;color:#fecdd3}.hint{font-size:.7rem;color:var(--muted)}
.grid.five{grid-template-columns:repeat(5,minmax(0,1fr))}
.dashboard2{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:14px;align-items:start}
.dashboard2 .panel{margin-bottom:0;align-self:start}
.topbar.compact{margin-bottom:16px}
.controlbar.primary{margin-bottom:8px}
.manualstate{display:flex;flex-direction:column;align-items:flex-start;justify-content:center;gap:4px;min-width:260px;line-height:1.25}.runmain{display:block;font-weight:700;white-space:nowrap}.runitem{display:block;font-size:.72rem;color:var(--muted);font-weight:500;white-space:nowrap}.ajaxmsg{font-size:.72rem;color:var(--muted)}.queueitem.extra,.changeextra{display:none}.changes{width:100%;table-layout:fixed}.changes .releasecol{width:45%}.changes .sizecol{width:18%}.changes .changecol{width:19%}.changes th,.changes td{overflow:hidden;text-overflow:ellipsis}.changes th:not(:first-child),.changes td:not(:first-child){white-space:nowrap;text-align:right}.changes td:first-child{white-space:nowrap}
@media(max-width:1100px){.grid.five{grid-template-columns:repeat(3,1fr)}}
@media(max-width:900px){.dashboard2{grid-template-columns:1fr}.grid.five{grid-template-columns:repeat(2,1fr)}}
@media(max-width:900px){.grid{grid-template-columns:repeat(2,1fr)}.layout{grid-template-columns:1fr}.hero{align-items:flex-start;flex-direction:column}.topbar{align-items:flex-start;flex-direction:column}.nav{width:100%;justify-content:space-between}}
.status.bad{color:#ff8b8b}.status.bad .dot{background:#ff5f67;box-shadow:0 0 12px rgba(255,95,103,.55)}
@media(max-width:520px){.shell{padding:22px 14px 40px}.grid{grid-template-columns:1fr}.hero h2{font-size:1.45rem}th,td{padding:11px 12px}}
"""

AJAX_SCRIPT = """<script>
(function(){
 const form=document.querySelector('.manualform'); if(!form)return;
 const app=form.querySelector('input[name="app"]').value;
 const state=document.getElementById('runstate-'+app);
 async function refresh(){
  try{const r=await fetch('/status?app='+app,{cache:'no-store'});const x=await r.json();
   const word=x.state.charAt(0).toUpperCase()+x.state.slice(1);
   const main=x.requested?(word+' · '+x.searched+' / '+x.requested+' searched'+(x.detail?' · '+x.detail:'')):'Idle';
   const now=x.running&&x.current?('Now checking: '+x.current):'';
   const last=!x.running&&x.last?('Last searched: '+x.last):'';
   const esc=s=>s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
   state.innerHTML='<span class="runmain">'+esc(main)+'</span>'
     +(now?'<span class="runitem">'+esc(now)+'</span>':'')
     +(last?'<span class="runitem">'+esc(last)+'</span>':'');
   form.querySelector('button:not(.stopbtn)').disabled=!!x.running;
   form.querySelector('.stopbtn').disabled=!x.running;
  }catch(e){}
 }
 form.addEventListener('submit',async function(e){
  e.preventDefault(); const submit=e.submitter; const target=(submit&&submit.classList.contains('stopbtn'))?'/stop':'/manual-search';
  try{const r=await fetch(target,{method:'POST',body:new URLSearchParams(new FormData(form)),headers:{'Content-Type':'application/x-www-form-urlencoded'}});
   if(!r.ok){state.textContent='Error · '+r.status; return;} await refresh();
  }catch(e){state.textContent='Connection error';}
 });
 refresh(); setInterval(refresh,2000);
})();
</script>"""

def page():
    state = load_state()
    today = time.strftime("%Y-%m-%d")
    used = int((state.get("daily") or {}).get(today, {}).get("searches", 0))
    rule_min, rule_max, extra_today = app_controls("radarr")
    error = ""
    try:
        records = history_records()
        upgrades = completed_upgrades(records)
        queue = queue_health(queue_records())
    except Exception as exc:
        upgrades, queue = [], []
        error = str(exc)
    saved = sum(x["saved"] for x in upgrades)
    positive = sum(1 for x in upgrades if x["saved"] > 0)
    total_before = sum(x["old"] for x in upgrades)
    reduction_pct = (saved / total_before * 100.0) if total_before else 0.0
    attention = [x for x in queue if x["attention"] and x.get("health_kind") != "optimizer_blocked"]
    optimizer_blocked = [x for x in queue if x.get("health_kind") == "optimizer_blocked"]
    last_date = upgrades[0]["date"][:10] if upgrades else "—"
    with job_lock:
        snap = dict(jobs["radarr"])

    rows = ""
    for i, x in enumerate(upgrades[:15]):
        extra = " changeextra" if i >= 5 else ""
        delta = gib(x["saved"])
        cls = "good" if delta >= 0 else "bad"
        rows += "<tr class='filterrow%s' data-search='%s'><td>%s</td><td>%.2f GiB</td><td>%.2f GiB</td><td class='%s'>%+.2f GiB</td></tr>" % (
            extra, html.escape(x["title"].lower(), quote=True), html.escape(x["title"]), gib(x["old"]), gib(x["new"]), cls, delta)
    if not rows:
        rows = "<tr><td colspan='4' class='muted'>No completed upgrade pairs found in the loaded history window.</td></tr>"
    elif len(upgrades) > 5:
        rows += "<tr id='changesExpandRow'><td colspan='4'><button type='button' class='expandbar' id='changesExpand' onclick='toggleChanges()'>Show %d more changes ↓</button></td></tr>" % (min(len(upgrades), 15) - 5)

    qrows = ""
    visible_queue = queue[:4]
    for i, x in enumerate(queue[:15]):
        cls = "attention" if x["attention"] else ""
        note = x["message"] or ("Time left: %s" % x["timeleft"])
        extra = " extra" if i >= 4 else ""
        qrows += """<div class="queueitem filterrow%s" data-search="%s"><div class="qtop"><div class="qtitle">%s</div><div class="%s">%s</div></div><div class="qmeta">%.1f%% · %s</div><div class="progress"><span style="width:%.1f%%"></span></div></div>""" % (
            extra, html.escape(x["title"].lower(), quote=True), html.escape(x["title"]), cls,
            html.escape(str(x.get("health") or x["status"])),
            x["progress"], html.escape(note) + ("""<form method="post" action="/repair-import" style="margin-top:9px"><input type="hidden" name="queue_id" value="%s"><button class="live" type="submit">Try safe import</button></form>""" % html.escape(str(x.get("queue_id") or ""), quote=True) if x.get("health_kind") == "optimizer_blocked" else ""), x["progress"])
    if not qrows:
        qrows = "<div class='empty'>Nothing is currently in Radarr's download queue.</div>"
    elif len(queue) > 4:
        qrows += """<button type="button" class="expandbar" id="queueExpand" onclick="toggleQueue()">Show %d more downloads ↓</button>""" % (min(len(queue), 15) - 4)

    runstat = manual_status("radarr")
    runlabel = "Idle" if not runstat["requested"] else ("%s%s · %d / %d searched" % (runstat["state"].capitalize(), (" · Searching " + runstat["current"]) if runstat.get("running") and runstat.get("current") else "", runstat["searched"], runstat["requested"]))
    actions = """<div class="controlbar primary"><form class="controlbox manualform" method="post" action="/manual-search"><input type="hidden" name="app" value="radarr"><label>Manual search</label><input name="count" type="number" min="1" max="%d" value="50"><button %s>Search</button><button class="stopbtn" formaction="/stop" %s>STOP</button></form><span id="runstate-radarr" class="manualstate">%s</span></div>
<form class="controlbar" method="post" action="/settings"><input type="hidden" name="app" value="radarr"><div class="controlbox"><label>Downsize</label><input name="min" type="number" min="0" max="100" step="0.1" value="%.1f"><span>–</span><input name="max" type="number" min="0" max="100" step="0.1" value="%.1f"><span>%%</span><label class="badge"><input type="checkbox" name="optimized_tag" value="1" %s> tag optimized</label><button type="submit">Apply</button></div><span class="badge">%d/%d searches · +%d today</span></form>""" % (MAX_MANUAL, "disabled" if runstat["running"] else "", "" if runstat["running"] else "disabled", html.escape(runlabel), rule_min, rule_max, "checked" if optimized_tag_enabled("radarr") else "", used, RADARR_BASE_BUDGET + extra_today, extra_today)
    output = html.escape(snap.get("output") or "No UI-started run yet.")
    status = "Online" if api_online("radarr") else "Offline"
    warning = "" if ENABLE_ACTIONS else "<div class='notice'>Read-only mode is active. Smart retry controls will only be enabled after we validate queue detection and candidate selection.</div>"
    err = ("<div class='notice bad'>Radarr API error: %s</div>" % html.escape(error)) if error else ""

    return """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="theme-color" content="#0b0e13"><title>Smart Optimizer UI · Radarr</title><style>%s</style></head>
<body><div class="shell">
<div class="topbar compact"><div class="brand"><div class="brandcopy"><h1>Radarr Optimizer</h1><div>Find smaller releases for your movies while keeping quality.</div></div></div><div class="nav"><div class="appswitch"><a class="active" href="/radarr">Radarr</a><a href="/sonarr">Sonarr</a></div><span class="status%s"><span class="dot"></span>%s</span></div></div>
%s
%s%s
<div class="toolbar"><div class="searchbox"><span class="searchicon">⌕</span><input id="librarySearch" autocomplete="off" placeholder="Search releases and current downloads…"></div></div>
<div class="grid five">
<div class="stat"><div class="stathead"><span>Storage saved</span></div><div class="value %s">%+.2f GiB</div><div class="sub">Observed across loaded upgrade history</div></div>
<div class="stat"><div class="stathead"><span>Space reductions</span></div><div class="value">%d</div><div class="sub">Observed upgrades that ended smaller</div></div>
<div class="stat"><div class="stathead"><span>Active downloads</span></div><div class="value">%d</div><div class="sub">%d need attention · %d optimizer import blocked</div></div>
<div class="stat"><div class="stathead"><span>Searches today</span></div><div class="value">%d</div><div class="sub">Optimizer state counter</div></div>
<div class="stat"><div class="stathead"><span>Temporary extra today</span></div><div class="value">+%d</div><div class="sub">Resets tomorrow</div></div>
</div>
<div class="layout"><div>
<div class="panel"><div class="panelhead"><div><h3>Recent file changes</h3><p>Observed Radarr upgrade pairs. These are not all necessarily optimizer-triggered.</p></div><a class="badge" href="/radarr/history">HISTORY</a></div>
<table class="changes"><colgroup><col class="releasecol"><col class="sizecol"><col class="sizecol"><col class="changecol"></colgroup><thead><tr><th>Release</th><th>Before</th><th>After</th><th>Change</th></tr></thead><tbody>%s</tbody></table></div>
<div class="panel"><div class="panelhead"><div><h3>Optimizer activity</h3><p>Output from runs started through this dashboard.</p></div><span class="badge">ACTIVITY</span></div><pre>%s</pre></div>
</div>
<div>
<div class="panel"><div class="panelhead"><div><h3>Download radar</h3><p>Live Radarr queue with problem jobs surfaced automatically.</p></div><span class="badge">%d ACTIVE</span></div>%s</div>
<div class="panel"><div class="panelhead"><div><h3>Optimizer intelligence</h3><p>Useful context without pretending Radarr history equals optimizer success.</p></div><span class="badge">SUMMARY</span></div><div class="sidecontent">
<div class="metricline"><span>Observed net reduction</span><b class="%s">%.1f%%</b></div>
<div class="metricline"><span>Smaller replacements</span><b>%d</b></div>
<div class="metricline"><span>Downloads needing attention</span><b class="%s">%d</b></div>
<div class="metricline"><span>Optimizer imports blocked</span><b class="%s">%d</b></div>
<div class="metricline"><span>Last observed upgrade</span><b>%s</b></div>
<div class="metricline"><span>Engine</span><b>%s</b></div>
<div class="metricline"><span>UI mode</span><b>%s</b></div>
</div></div></div></div>
<div class="footer">Radarr Smart Optimizer · storage intelligence, not another Radarr replacement</div>
</div>
<script>
let queueOpen=false;
function toggleQueue(){const b=document.getElementById('queueExpand');if(queueOpen){location.href='/radarr/history';return;}queueOpen=true;document.querySelectorAll('.queueitem.extra').forEach(el=>el.style.display='block');if(b)b.textContent='History →';}
let changesOpen=false;function toggleChanges(){const b=document.getElementById('changesExpand');if(changesOpen){location.href='/radarr/history';return;}changesOpen=true;document.querySelectorAll('.changeextra').forEach(el=>el.style.display='table-row');if(b)b.textContent='History →';}
const box=document.getElementById('librarySearch');
box.addEventListener('input',()=>{const q=box.value.trim().toLowerCase();document.querySelectorAll('.filterrow').forEach(el=>{const match=!q||((el.dataset.search||'').includes(q));if(el.classList.contains('extra')&&!queueOpen&&!q){el.style.display='none';}else{el.style.display=match?'':'none';}});});
</script><script>
(function(){var el=document.querySelector('[id^="runstate-"]');if(!el)return;var app=el.id.replace('runstate-','');async function tick(){try{var r=await fetch('/status?app='+app,{cache:'no-store'});var x=await r.json();el.textContent=x.requested?(x.state.charAt(0).toUpperCase()+x.state.slice(1)+' · '+x.searched+' / '+x.requested+' searched'):'Idle';}catch(e){}}tick();setInterval(tick,10000);})();
</script>%s</body></html>""" % (
        CSS, "" if status == "Online" else " bad", html.escape(status), actions, warning, err,
        "good" if saved >= 0 else "bad", gib(saved), positive,
        len(queue), len(attention), len(optimizer_blocked), used, extra_today, rows, output, len(queue), qrows,
        "good" if reduction_pct >= 0 else "bad", reduction_pct, positive,
        "bad" if attention else "good", len(attention),
        "bad" if optimizer_blocked else "good", len(optimizer_blocked), html.escape(last_date),
        html.escape(status), "Actions enabled" if ENABLE_ACTIONS else "Read-only", AJAX_SCRIPT)



def home_page():
    return """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Smart Optimizer</title><style>%s</style></head><body><div class="shell homewrap"><div class="homecard"><div class="brand" style="justify-content:center;margin-bottom:24px"><div class="mark">S</div></div><h1>Smart Optimizer</h1><p>Choose the library you want to inspect.</p><div class="chooser"><a class="choice" href="/radarr"><b>Radarr →</b><span>Movies · storage savings · download radar</span></a><a class="choice" href="/sonarr"><b>Sonarr →</b><span>Episodes · storage savings · download radar</span></a></div><div style="margin-top:20px"><a class="badge" href="/settings" style="font-size:1.15rem;padding:14px 26px;border-radius:14px;display:inline-flex;align-items:center;gap:8px">⚙ Settings</a></div></div></div></body></html>""" % CSS


def settings_page(message="", bad=False):
    rcfg, scfg = connection("radarr"), connection("sonarr")
    notice = ""
    if message:
        notice = "<div class='notice%s'>%s</div>" % (" bad" if bad else "", html.escape(message))
    def card(app, cfg, default_port):
        name = app.capitalize()
        masked = "Configured · leave blank to keep current key" if cfg["api_key"] else "Not configured"
        return """<div class="panel"><div class="panelhead"><div><h3>%s connection</h3><p>Configure the %s API used by the dashboard and optimizer.</p></div><span class="badge">%s</span></div>
<form method="post" action="/connection-settings" class="sidecontent">
<input type="hidden" name="app" value="%s">
<div class="metricline"><span>Protocol</span><select name="scheme"><option value="http"%s>http</option><option value="https"%s>https</option></select></div>
<div class="metricline"><span>IP / hostname</span><input name="host" value="%s" placeholder="127.0.0.1" required></div>
<div class="metricline"><span>Port</span><input name="port" type="number" min="1" max="65535" value="%d" placeholder="%d" required></div>
<div class="metricline"><span>API key</span><input name="api_key" type="password" value="" placeholder="%s" autocomplete="new-password"></div>
<div style="display:flex;gap:10px;justify-content:flex-end;margin-top:16px"><button name="action" value="test" type="submit">Test connection</button><button name="action" value="save" type="submit">Save</button></div>
</form></div>""" % (name, name, "CONFIGURED" if cfg["api_key"] else "SETUP", app,
            " selected" if cfg["scheme"] == "http" else "", " selected" if cfg["scheme"] == "https" else "",
            html.escape(cfg["host"], quote=True), cfg["port"], default_port, masked)
    return """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Smart Optimizer · Settings</title><style>%s</style></head><body><div class="shell">
<div class="topbar compact"><div class="brand"><div class="brandcopy"><h1>Connection settings</h1><div>Radarr and Sonarr API connections.</div></div></div><div class="nav"><a class="badge" href="/">← Smart Optimizer</a></div></div>
%s
<div class="dashboard2">%s%s</div>
<div class="notice">Saved settings override Docker environment values. Leaving the API-key field blank keeps the currently configured key. Optimizer queues, cursors, history and downsize rules are not changed here.</div>
</div></body></html>""" % (CSS, notice, card("radarr", rcfg, 7878), card("sonarr", scfg, 8989))


def history_page(app):
    try:
        if app == "radarr":
            upgrades = completed_upgrades(history_records())
            title, noun = "Radarr history", "movies"
        else:
            upgrades = sonarr_completed_upgrades(sonarr_history_records())
            title, noun = "Sonarr history", "episodes"
        rows = ""
        for x in upgrades:
            delta = gib(x["saved"])
            cls = "good" if delta >= 0 else "bad"
            rows += "<tr><td>%s</td><td>%.2f GiB</td><td>%.2f GiB</td><td class='%s'>%+.2f GiB</td><td>%s</td></tr>" % (
                html.escape(x["title"]), gib(x["old"]), gib(x["new"]), cls, delta, html.escape((x.get("date") or "")[:19].replace("T", " ")))
        if not rows:
            rows = "<tr><td colspan='5' class='muted'>No completed upgrade pairs found in the loaded history window.</td></tr>"
        err = ""
    except Exception as exc:
        rows = ""
        err = "<div class='notice bad'>%s API error: %s</div>" % (app.capitalize(), html.escape(str(exc)))
        title, noun = app.capitalize() + " history", "items"
    return """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Smart Optimizer UI · %s</title><style>%s</style></head><body><div class="shell">
<div class="topbar compact"><div class="brand"><div class="brandcopy"><h1>%s</h1><div>Complete observed upgrade history available in the loaded API history window.</div></div></div><div class="nav"><a class="badge" href="/%s">← Back to dashboard</a></div></div>
%s
<div class="panel"><div class="panelhead"><div><h3>All recent observed changes</h3><p>Size changes for %s returned by the configured history window.</p></div><span class="badge">HISTORY</span></div>
<table><thead><tr><th>Release</th><th>Before</th><th>After</th><th>Change</th><th>Date</th></tr></thead><tbody>%s</tbody></table></div>
</div></body></html>""" % (html.escape(title), CSS, html.escape(title), app, err, noun, rows)


def sonarr_page():
    state = load_sonarr_state()
    today = time.strftime("%Y-%m-%d")
    used = int((state.get("daily") or {}).get(today, {}).get("searches", 0))
    rule_min, rule_max, extra_today = app_controls("sonarr")
    error = ""
    try:
        records = sonarr_history_records()
        upgrades = sonarr_completed_upgrades(records)
        queue = sonarr_queue_records()
    except Exception as exc:
        upgrades, queue = [], []
        error = str(exc)
    saved = sum(x["saved"] for x in upgrades)
    positive = sum(1 for x in upgrades if x["saved"] > 0)
    total_before = sum(x["old"] for x in upgrades)
    reduction_pct = (saved / total_before * 100.0) if total_before else 0.0
    last_date = upgrades[0]["date"][:10] if upgrades else "—"
    rows = ""
    for i, x in enumerate(upgrades[:15]):
        delta = gib(x["saved"])
        cls = "good" if delta >= 0 else "bad"
        extra = " changeextra" if i >= 5 else ""
        rows += "<tr class='filterrow%s' data-search='%s'><td>%s</td><td>%.2f GiB</td><td>%.2f GiB</td><td class='%s'>%+.2f GiB</td></tr>" % (
            extra, html.escape(x["title"].lower(), quote=True), html.escape(x["title"]), gib(x["old"]), gib(x["new"]), cls, delta)
    if not rows:
        rows = "<tr><td colspan='4' class='muted'>No completed episode upgrade pairs found in the loaded history window.</td></tr>"
    elif len(upgrades) > 5:
        rows += "<tr id='changesExpandRow'><td colspan='4'><button type='button' class='expandbar' id='changesExpand' onclick='toggleChanges()'>Show %d more changes ↓</button></td></tr>" % (min(len(upgrades), 15) - 5)
    qrows = ""
    for i, x in enumerate(queue[:15]):
        size = float(x.get("size") or 0); left = float(x.get("sizeleft") or 0)
        progress = max(0.0, min(100.0, ((size-left)/size*100.0) if size else 0.0))
        title = x.get("title") or ("Episode ID %s" % x.get("episodeId"))
        status = x.get("status") or x.get("trackedDownloadStatus") or "unknown"
        extra = " extra" if i >= 4 else ""
        qrows += "<div class='queueitem filterrow%s' data-search='%s'><div class='qtop'><div class='qtitle'>%s</div><div>%s</div></div><div class='qmeta'>%.1f%%</div><div class='progress'><span style='width:%.1f%%'></span></div></div>" % (
            extra, html.escape(str(title).lower(), quote=True), html.escape(str(title)), html.escape(str(status)), progress, progress)
    if not qrows:
        qrows = "<div class='empty'>Nothing is currently in Sonarr's download queue.</div>"
    elif len(queue) > 4:
        qrows += "<button type='button' class='expandbar' id='queueExpand' onclick='toggleQueue()'>Show %d more downloads ↓</button>" % (min(len(queue), 15) - 4)
    runstat = manual_status("sonarr")
    runlabel = "Idle" if not runstat["requested"] else ("%s · %d / %d searched" % (runstat["state"].capitalize(), runstat["searched"], runstat["requested"]))
    if runstat.get("running") and runstat.get("current"):
        runlabel += "<span class='runitem'>Now checking: %s</span>" % html.escape(runstat["current"])
    elif not runstat.get("running") and runstat.get("last"):
        runlabel += "<span class='runitem'>Last searched: %s</span>" % html.escape(runstat["last"])
    son_actions = """<div class="controlbar primary"><form class="controlbox manualform" method="post" action="/manual-search"><input type="hidden" name="app" value="sonarr"><label>Manual search</label><input name="count" type="number" min="1" max="%d" value="50"><button %s>Search</button><button class="stopbtn" formaction="/stop" %s>STOP</button></form><span id="runstate-sonarr" class="manualstate">%s</span></div>
<form class="controlbar" method="post" action="/settings"><input type="hidden" name="app" value="sonarr"><div class="controlbox"><label>Downsize</label><input name="min" type="number" min="0" max="100" step="0.1" value="%.1f"><span>–</span><input name="max" type="number" min="0" max="100" step="0.1" value="%.1f"><span>%%</span><label class="badge"><input type="checkbox" name="optimized_tag" value="1" %s> tag optimized</label><button type="submit">Apply</button></div><span class="badge">%d/%d searches · +%d today</span><span class="badge">UHD 1080→2160 exception unchanged</span></form>""" % (MAX_MANUAL, "disabled" if runstat["running"] else "", "" if runstat["running"] else "disabled", runlabel, rule_min, rule_max, "checked" if optimized_tag_enabled("sonarr") else "", used, SONARR_BASE_BUDGET + extra_today, extra_today)
    err = ("<div class='notice bad'>Sonarr API error: %s</div>" % html.escape(error)) if error else ""
    connection_status = "Online" if api_online("sonarr") else "Offline"
    return """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Smart Optimizer UI · Sonarr</title><style>%s</style></head><body><div class="shell">
<div class="topbar compact"><div class="brand"><div class="brandcopy"><h1>Sonarr Optimizer</h1><div>Find smaller releases for your episodes while keeping quality.</div></div></div><div class="nav"><div class="appswitch"><a href="/radarr">Radarr</a><a class="active" href="/sonarr">Sonarr</a></div><span class="status%s"><span class="dot"></span>%s</span></div></div>
%s%s
<div class="toolbar"><div class="searchbox"><span class="searchicon">⌕</span><input id="librarySearch" autocomplete="off" placeholder="Search releases and current downloads…"></div></div>
<div class="grid five">
<div class="stat"><div class="stathead"><span>Storage saved</span></div><div class="value %s">%+.2f GiB</div><div class="sub">Observed across loaded upgrade history</div></div>
<div class="stat"><div class="stathead"><span>Reductions</span></div><div class="value">%d</div><div class="sub">Observed upgrades that ended smaller</div></div>
<div class="stat"><div class="stathead"><span>Active downloads</span></div><div class="value">%d</div><div class="sub">Live Sonarr queue</div></div>
<div class="stat"><div class="stathead"><span>Searches today</span></div><div class="value">%d</div><div class="sub">Optimizer state counter</div></div>
<div class="stat"><div class="stathead"><span>Temporary extra today</span></div><div class="value">+%d</div><div class="sub">Resets tomorrow</div></div>
</div>
<div class="layout"><div>
<div class="panel"><div class="panelhead"><div><h3>Recent changes</h3><p>Latest optimized episodes and their size changes.</p></div><a class="badge" href="/sonarr/history">HISTORY</a></div><table><thead><tr><th>Release</th><th>Before</th><th>After</th><th>Change</th></tr></thead><tbody>%s</tbody></table></div>
<div class="panel"><div class="panelhead"><div><h3>Settings &amp; status</h3><p>Current configuration and system status.</p></div><span class="badge">CONFIG</span></div><div class="sidecontent">
<div class="metricline"><span>Downsize range</span><b>%.1f – %.1f%%</b></div>
<div class="metricline"><span>UHD (1080 → 2160) exception</span><b>Enabled (unchanged)</b></div>
<div class="metricline"><span>Daily search budget</span><b>%d</b></div>
<div class="metricline"><span>Temporary extra searches</span><b>%d</b></div>
<div class="metricline"><span>Status</span><b class="good">Ready</b></div>
</div></div>
</div>
<div>
<div class="panel"><div class="panelhead"><div><h3>Download radar</h3><p>Live Sonarr queue.</p></div><span class="badge">%d ACTIVE</span></div>%s</div>
<div class="panel"><div class="panelhead"><div><h3>Optimizer intelligence</h3><p>Useful context without pretending Sonarr history equals optimizer success.</p></div><span class="badge">SUMMARY</span></div><div class="sidecontent">
<div class="metricline"><span>Observed net reduction</span><b class="%s">%.1f%%</b></div>
<div class="metricline"><span>Total smaller replacements</span><b>%d</b></div>
<div class="metricline"><span>Active downloads</span><b>%d</b></div>
<div class="metricline"><span>Last observed upgrade</span><b>%s</b></div>
</div></div>
</div></div>
<div class="footer"><a href="/">Smart Optimizer</a> · Sonarr dashboard</div></div>
<script>
let queueOpen=false;function toggleQueue(){const b=document.getElementById('queueExpand');if(queueOpen){location.href='/sonarr/history';return;}queueOpen=true;document.querySelectorAll('.queueitem.extra').forEach(el=>el.style.display='block');if(b)b.textContent='History →';}
let changesOpen=false;function toggleChanges(){const b=document.getElementById('changesExpand');if(changesOpen){location.href='/sonarr/history';return;}changesOpen=true;document.querySelectorAll('.changeextra').forEach(el=>el.style.display='table-row');if(b)b.textContent='History →';}
const box=document.getElementById('librarySearch');
box.addEventListener('input',()=>{const q=box.value.trim().toLowerCase();document.querySelectorAll('.filterrow').forEach(el=>{const match=!q||((el.dataset.search||'').includes(q));if(el.classList.contains('extra')&&!queueOpen&&!q){el.style.display='none';}else if(el.classList.contains('changeextra')&&!changesOpen&&!q){el.style.display='none';}else{el.style.display=match?'':'none';}});});
</script>
%s
</body></html>""" % (
        CSS, "" if connection_status == "Online" else " bad", html.escape(connection_status), son_actions, err, "good" if saved >= 0 else "bad", gib(saved), positive, len(queue), used, extra_today,
        rows, rule_min, rule_max, SONARR_BASE_BUDGET, extra_today, len(queue), qrows,
        "good" if reduction_pct >= 0 else "bad", reduction_pct, positive, len(queue),
        html.escape(last_date), AJAX_SCRIPT)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path == "/status":
            app = (urllib.parse.parse_qs(parsed.query).get("app") or [""])[0]
            if app not in ("radarr", "sonarr"):
                self.send_error(400); return
            body = json.dumps(manual_status(app)).encode("utf-8")
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(body); return
        if path == "/":
            rendered = home_page()
        elif path == "/radarr":
            rendered = page()
        elif path == "/sonarr":
            rendered = sonarr_page()
        elif path == "/settings":
            rendered = settings_page()
        elif path == "/radarr/history":
            rendered = history_page("radarr")
        elif path == "/sonarr/history":
            rendered = history_page("sonarr")
        else:
            self.send_error(404); return
        body = rendered.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = min(int(self.headers.get("Content-Length", "0")), 4096)
        form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
        if self.path == "/connection-settings":
            if not ENABLE_ACTIONS:
                self.send_error(403); return
            app = (form.get("app") or [""])[0]
            action = (form.get("action") or [""])[0]
            if app not in ("radarr", "sonarr") or action not in ("test", "save"):
                self.send_error(400); return
            try:
                scheme = (form.get("scheme") or ["http"])[0]
                host = (form.get("host") or [""])[0]
                port = int((form.get("port") or ["0"])[0])
                key = (form.get("api_key") or [""])[0]
                if action == "test":
                    version = test_connection(app, scheme, host, port, key)
                    rendered = settings_page("%s connection successful · version %s" % (app.capitalize(), version))
                else:
                    test_connection(app, scheme, host, port, key)
                    update_connection(app, scheme, host, port, key)
                    rendered = settings_page("%s settings saved and connection verified." % app.capitalize())
            except Exception as exc:
                rendered = settings_page("%s: %s" % (app.capitalize(), str(exc)), True)
            body = rendered.encode("utf-8")
            self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(body))); self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(body); return
        if self.path == "/settings":
            if not ENABLE_ACTIONS:
                self.send_error(403); return
            app = (form.get("app") or [""])[0]
            if app not in ("radarr", "sonarr"):
                self.send_error(400); return
            try:
                update_saving_window(app, float((form.get("min") or [""])[0]), float((form.get("max") or [""])[0]))
                update_optimized_tag(app, (form.get("optimized_tag") or [""])[0].lower() in ("1", "true", "yes", "on"))
            except Exception as exc:
                self.send_error(400, str(exc)); return
            self.send_response(303); self.send_header("Location", "/" + app); self.end_headers(); return
        if self.path == "/manual-search":
            if not ENABLE_ACTIONS: self.send_error(403); return
            app = (form.get("app") or [""])[0]
            try:
                count = int((form.get("count") or [""])[0])
                if app not in ("radarr", "sonarr") or not 1 <= count <= MAX_MANUAL: raise ValueError()
            except Exception:
                self.send_error(400, "Invalid manual search amount"); return
            if not run_optimizer(True, app, count, daily_extra=count):
                self.send_error(409, "Another UI optimizer run is already active"); return
            self.send_response(303); self.send_header("Location", "/" + app); self.end_headers(); return
        if self.path == "/stop":
            if not ENABLE_ACTIONS: self.send_error(403); return
            app = (form.get("app") or [""])[0]
            if app not in ("radarr", "sonarr"): self.send_error(400); return
            stop_optimizer(app)
            self.send_response(303); self.send_header("Location", "/" + app); self.end_headers(); return
        if self.path == "/repair-import":
            if not ENABLE_ACTIONS: self.send_error(403); return
            try:
                repair_import((form.get("queue_id") or [""])[0])
            except Exception as exc:
                print("[ui] safe import failed:", exc)
                self.send_error(409, str(exc)); return
            self.send_response(303); self.send_header("Location", "/radarr"); self.end_headers(); return
        if self.path != "/run" or not ENABLE_ACTIONS:
            self.send_error(403); return
        mode = (form.get("mode") or [""])[0]
        if mode not in ("dry", "live"):
            self.send_error(400); return
        run_optimizer(mode == "live")
        self.send_response(303)
        self.send_header("Location", "/radarr")
        self.end_headers()

    def log_message(self, fmt, *args):
        print("[ui] " + fmt % args)


if __name__ == "__main__":
    print("Radarr Smart Optimizer UI")
    print("Listening on http://%s:%d" % (HOST, PORT))
    print("Actions:", "ENABLED" if ENABLE_ACTIONS else "disabled (read-only)")
    if HOST not in ("127.0.0.1", "localhost", "::1"):
        print("WARNING: UI has no built-in authentication; expose only on a trusted LAN/reverse proxy.")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
