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


# SMART SELECTABLE SONARR RULES START

SONARR_RULE_KEYS = (
    "storage_optimization",
    "upgrade_720_to_1080",
    "uhd_upgrade",
    "require_hdr_uhd",
    "prefer_dynamic_range",
    "prefer_atmos",
    "prefer_torrentleech",
    "prefer_x265",
    "block_av1",
)

# SMART RUNTIME RULES LKG V1 START

_LAST_GOOD_RULES = None


def runtime_rules():

    global _LAST_GOOD_RULES

    try:

        with open(
            CONTROL_FILE,
            "r",
            encoding="utf-8",
        ) as f:

            data = (
                json.load(f)
                or {}
            )


        raw = (
            (
                data.get(
                    "sonarr",
                    {}
                )
                or {}
            ).get(
                "rules"
            )
            or {}
        )


        if not isinstance(
            raw,
            dict
        ):
            raise ValueError(
                "rules is not an object"
            )


        rules = {
            key: bool(
                raw.get(
                    key,
                    False
                )
            )
            for key
            in SONARR_RULE_KEYS
        }


        _LAST_GOOD_RULES = dict(
            rules
        )

        return rules


    except Exception:

        if (
            _LAST_GOOD_RULES
            is not None
        ):

            return dict(
                _LAST_GOOD_RULES
            )


        # Fresh install / first unreadable read:
        # optional rules safely default OFF.
        return {
            key: False
            for key
            in SONARR_RULE_KEYS
        }


# SMART RUNTIME RULES LKG V1 END


# SMART SONARR ADVANCED PREFERENCES V2 START

SONARR_ADVANCED_BOOL_KEYS = (
    "prefer_remux",
    "prefer_bluray",
    "prefer_webdl",
    "prefer_webrip",
    "prefer_hdtv",
    "prefer_hdr10plus",
    "prefer_10bit",
    "prefer_dtsx",
    "prefer_lossless_audio",
    "prefer_eac3",
    "prefer_proper_repack",
    "prefer_freeleech",
    "prefer_smaller",
    "prefer_seeders",
)

SONARR_ADVANCED_CODEC_VALUES = (
    "none",
    "x265",
    "x264",
    "av1",
)

_LAST_GOOD_SONARR_ADVANCED = None


def runtime_advanced_preferences():

    global _LAST_GOOD_SONARR_ADVANCED

    defaults = {
        key: False
        for key
        in SONARR_ADVANCED_BOOL_KEYS
    }

    defaults["codec_preference"] = "none"
    defaults["indexer_priority"] = []

    try:

        with open(
            CONTROL_FILE,
            "r",
            encoding="utf-8",
        ) as f:

            data = (
                json.load(f)
                or {}
            )

        raw = (
            (
                data.get(
                    "sonarr",
                    {}
                )
                or {}
            ).get(
                "advanced_preferences",
                {}
            )
            or {}
        )

        if not isinstance(
            raw,
            dict
        ):
            raise ValueError(
                "advanced_preferences is not an object"
            )

        result = dict(
            defaults
        )

        for key in (
            SONARR_ADVANCED_BOOL_KEYS
        ):

            result[key] = bool(
                raw.get(
                    key,
                    defaults[key]
                )
            )

        codec = str(
            raw.get(
                "codec_preference",
                "none"
            )
            or "none"
        ).lower()

        if codec not in (
            SONARR_ADVANCED_CODEC_VALUES
        ):
            codec = "none"

        result[
            "codec_preference"
        ] = codec

        names = (
            raw.get(
                "indexer_priority"
            )
            or []
        )

        if not isinstance(
            names,
            list
        ):
            names = []

        cleaned = []

        for name in names:

            value = str(
                name
                or ""
            ).strip()

            if (
                value
                and value not in cleaned
            ):

                cleaned.append(
                    value[:200]
                )

            if len(cleaned) >= 100:
                break

        result[
            "indexer_priority"
        ] = cleaned

        _LAST_GOOD_SONARR_ADVANCED = {
            **result,

            "indexer_priority":
                list(
                    result[
                        "indexer_priority"
                    ]
                ),
        }

        return result

    except Exception:

        if (
            _LAST_GOOD_SONARR_ADVANCED
            is not None
        ):

            return {
                **_LAST_GOOD_SONARR_ADVANCED,

                "indexer_priority":
                    list(
                        _LAST_GOOD_SONARR_ADVANCED[
                            "indexer_priority"
                        ]
                    ),
            }

        return defaults


def sonarr_v2_choice_title(
    choice
):

    direct = str(
        choice.get(
            "title"
        )
        or ""
    ).strip()

    if direct:
        return direct

    release = (
        choice.get(
            "release"
        )
        or {}
    )

    return str(
        release.get(
            "title"
        )
        or ""
    ).strip()


def sonarr_v2_choice_indexer(
    choice
):

    direct = str(
        choice.get(
            "indexer"
        )
        or ""
    ).strip()

    if direct:
        return direct

    release = (
        choice.get(
            "release"
        )
        or {}
    )

    return str(
        release.get(
            "indexer"
        )
        or ""
    ).strip()


def sonarr_v2_choice_seeders(
    choice
):

    release = (
        choice.get(
            "release"
        )
        or {}
    )

    value = (
        choice.get(
            "seeders"
        )
    )

    if value is None:

        value = release.get(
            "seeders"
        )

    try:

        return int(
            value
            or 0
        )

    except (
        TypeError,
        ValueError,
    ):

        return 0


def sonarr_v2_normalize_indexer(
    value
):

    value = str(
        value
        or ""
    ).strip().casefold()

    if value.endswith(
        "(prowlarr)"
    ):

        value = value[
            :-len("(prowlarr)")
        ].strip()

    return "".join(
        char
        for char in value
        if char.isalnum()
    )


def sonarr_v2_indexer_rank(
    choice,
    preferences,
):

    wanted = []

    for name in (
        preferences.get(
            "indexer_priority"
        )
        or []
    ):

        normalized = (
            sonarr_v2_normalize_indexer(
                name
            )
        )

        if (
            normalized
            and normalized not in wanted
        ):

            wanted.append(
                normalized
            )

    if not wanted:
        return 0

    actual = (
        sonarr_v2_normalize_indexer(
            sonarr_v2_choice_indexer(
                choice
            )
        )
    )

    for position, name in enumerate(
        wanted
    ):

        if actual == name:
            return position

    return len(
        wanted
    )


def sonarr_v2_source_type(
    choice
):

    title = (
        sonarr_v2_choice_title(
            choice
        ).upper()
    )

    if "REMUX" in title:
        return "remux"

    if any(
        token in title
        for token in (
            "BLURAY",
            "BLU-RAY",
            "BDRIP",
            "BD-RIP",
        )
    ):
        return "bluray"

    if any(
        token in title
        for token in (
            "WEB-DL",
            "WEBDL",
            "WEB.DL",
        )
    ):
        return "webdl"

    if any(
        token in title
        for token in (
            "WEBRIP",
            "WEB-RIP",
            "WEB.RIP",
        )
    ):
        return "webrip"

    if "HDTV" in title:
        return "hdtv"

    return "other"


def sonarr_v2_source_rank(
    choice,
    preferences,
):

    source = (
        sonarr_v2_source_type(
            choice
        )
    )

    enabled = []

    for key, name in (
        (
            "prefer_remux",
            "remux"
        ),
        (
            "prefer_bluray",
            "bluray"
        ),
        (
            "prefer_webdl",
            "webdl"
        ),
        (
            "prefer_webrip",
            "webrip"
        ),
        (
            "prefer_hdtv",
            "hdtv"
        ),
    ):

        if preferences.get(key):

            enabled.append(
                name
            )

    if not enabled:
        return 0

    try:

        return enabled.index(
            source
        )

    except ValueError:

        return len(
            enabled
        )


def sonarr_v2_title_has(
    choice,
    tokens,
):

    title = (
        sonarr_v2_choice_title(
            choice
        ).upper()
    )

    return any(
        token in title
        for token in tokens
    )


def sonarr_v2_is_hdr10plus(
    choice
):

    return sonarr_v2_title_has(
        choice,
        (
            "HDR10+",
            "HDR10PLUS",
            "HDR10 PLUS",
        )
    )


def sonarr_v2_is_10bit(
    choice
):

    return sonarr_v2_title_has(
        choice,
        (
            "10BIT",
            "10-BIT",
            "10.BIT",
        )
    )


def sonarr_v2_is_dtsx(
    choice
):

    return sonarr_v2_title_has(
        choice,
        (
            "DTS:X",
            "DTS-X",
            "DTS.X",
        )
    )


def sonarr_v2_is_lossless_audio(
    choice
):

    return sonarr_v2_title_has(
        choice,
        (
            "TRUEHD",
            "TRUE-HD",
            "DTS-HD MA",
            "DTS.HD.MA",
            "DTSHDMA",
        )
    )


def sonarr_v2_is_eac3(
    choice
):

    return sonarr_v2_title_has(
        choice,
        (
            "EAC3",
            "E-AC-3",
            "E.AC3",
            "DD+",
            "DDP",
        )
    )


def sonarr_v2_is_proper_repack(
    choice
):

    return sonarr_v2_title_has(
        choice,
        (
            "PROPER",
            "REPACK",
        )
    )


def sonarr_v2_is_freeleech(
    choice
):

    release = (
        choice.get(
            "release"
        )
        or {}
    )

    value = release.get(
        "downloadVolumeFactor"
    )

    if value is None:
        return False

    try:

        return float(
            value
        ) == 0.0

    except (
        TypeError,
        ValueError,
    ):

        return False


def sonarr_v2_codec_rank(
    choice,
    preferences,
):

    wanted = str(
        preferences.get(
            "codec_preference",
            "none"
        )
        or "none"
    ).lower()

    if wanted == "none":
        return 0

    actual = str(
        choice.get(
            "codec"
        )
        or ""
    ).lower()

    aliases = {
        "hevc": "x265",
        "h265": "x265",
        "h.265": "x265",
        "avc": "x264",
        "h264": "x264",
        "h.264": "x264",
    }

    actual = aliases.get(
        actual,
        actual
    )

    return (
        0
        if actual == wanted
        else 1
    )


def sonarr_v2_boolean_rank(
    enabled,
    matches,
):

    if not enabled:
        return 0

    return (
        0
        if matches
        else 1
    )


# SMART SONARR ADVANCED PREFERENCES V2 END



def sonarr_policy_upgrade_targets(
    item,
    policy=None,
):
    policy = (
        policy
        or runtime_resolution_policy()
    )

    paths = policy["paths"]

    resolution = int(
        item.get("resolution")
        or 0
    )

    profile_id = int(
        item.get("profile_id")
        or 0
    )

    targets = []


    if (
        resolution == 720
        and paths.get("720_to_1080")
    ):
        targets.append(1080)


    # Keep 2160p upgrades on the UHD profile.
    if (
        resolution == 720
        and profile_id == UHD_PROFILE_ID
        and paths.get("720_to_2160")
    ):
        targets.append(2160)


    if (
        resolution == 1080
        and profile_id == UHD_PROFILE_ID
        and paths.get("1080_to_2160")
    ):
        targets.append(2160)


    return tuple(
        sorted(
            set(targets)
        )
    )


def sonarr_min_current_mib(
    policy=None,
):
    policy = (
        policy
        or runtime_resolution_policy()
    )

    setting = (
        policy["limits"]
        ["minimum_current_size"]
    )

    if not setting.get("enabled"):
        return None

    return policy_size_mib(
        setting
    )


def sonarr_has_target_rule(rules=None):
    rules = (
        rules
        or runtime_rules()
    )

    policy = runtime_resolution_policy()
    paths = policy["paths"]

    return bool(
        rules.get(
            "storage_optimization"
        )
        or paths.get(
            "720_to_1080"
        )
        or paths.get(
            "720_to_2160"
        )
        or paths.get(
            "1080_to_2160"
        )
    )


def sonarr_item_enabled_by_rules(item):
    rules = runtime_rules()
    policy = runtime_resolution_policy()

    resolution = int(
        item.get("resolution")
        or 0
    )

    if resolution not in (
        720,
        1080,
        2160,
    ):
        return False


    upgrade_targets = (
        sonarr_policy_upgrade_targets(
            item,
            policy
        )
    )


    if (
        resolution in (
            1080,
            2160,
        )
        and rules.get(
            "storage_optimization"
        )
    ):
        return True


    # 720p is only considered through an enabled upgrade path.
    return bool(
        upgrade_targets
    )


# SMART SONARR FLEXIBLE ELIGIBILITY V1


def sonarr_target_rule_signature(rules=None):
    rules = (
        rules
        or runtime_rules()
    )

    policy = runtime_resolution_policy()
    paths = policy["paths"]

    minimum = (
        policy["limits"]
        ["minimum_current_size"]
    )

    minimum_mib = (
        policy_size_mib(minimum)
        if minimum.get("enabled")
        else 0.0
    )

    return (
        "storage=%d|"
        "720_1080=%d|"
        "720_2160=%d|"
        "1080_2160=%d|"
        "min_enabled=%d|"
        "min_mib=%.3f"
        % (
            int(
                bool(
                    rules.get(
                        "storage_optimization"
                    )
                )
            ),

            int(
                bool(
                    paths.get(
                        "720_to_1080"
                    )
                )
            ),

            int(
                bool(
                    paths.get(
                        "720_to_2160"
                    )
                )
            ),

            int(
                bool(
                    paths.get(
                        "1080_to_2160"
                    )
                )
            ),

            int(
                bool(
                    minimum.get(
                        "enabled"
                    )
                )
            ),

            minimum_mib,
        )
    )


def sync_sonarr_target_rules(state):
    rules = runtime_rules()
    signature = sonarr_target_rule_signature(rules)
    previous = state.get("target_rules_signature")

    if previous is None:
        state["target_rules_signature"] = signature

        if LIVE:
            save_state(state)

    elif previous != signature:
        state["target_rules_signature"] = signature
        state["series_cursor"] = 0
        state["work_queue"] = []
        state["work_cursor"] = 0
        state["rule_skipped_series_ids"] = []

        if LIVE:
            save_state(state)

        print(
            "RULE CHANGE: Sonarr A-Z queue reset.",
            flush=True
        )

    return rules


# SMART SONARR TARGET RULE SYNC

# SMART SELECTABLE SONARR RULES END

# SMART SONARR RESOLUTION POLICY START

_LAST_GOOD_RESOLUTION_POLICY = None


def runtime_resolution_policy():
    global _LAST_GOOD_RESOLUTION_POLICY


    def clean_size_limit(
        raw,
        default_value,
        default_unit,
    ):
        raw = (
            raw
            if isinstance(raw, dict)
            else {}
        )

        try:
            value = float(
                raw.get(
                    "value",
                    default_value
                )
            )

            if value < 0:
                raise ValueError

        except (
            TypeError,
            ValueError,
        ):
            value = float(
                default_value
            )

        unit = str(
            raw.get(
                "unit",
                default_unit
            )
        ).strip()

        if unit not in (
            "MiB",
            "GiB",
        ):
            unit = default_unit

        return {
            "enabled": bool(
                raw.get(
                    "enabled",
                    False
                )
            ),

            "value": value,

            "unit": unit,
        }


    def clean_growth_range(
        raw,
        default_min,
        default_max,
    ):
        raw = (
            raw
            if isinstance(raw, dict)
            else {}
        )

        try:
            minimum = float(
                raw.get(
                    "min_percent",
                    default_min
                )
            )

            maximum = float(
                raw.get(
                    "max_percent",
                    default_max
                )
            )

            if (
                minimum < 0
                or maximum < 0
                or minimum > maximum
            ):
                raise ValueError

        except (
            TypeError,
            ValueError,
        ):
            minimum = float(
                default_min
            )

            maximum = float(
                default_max
            )

        return {
            "enabled": bool(
                raw.get(
                    "enabled",
                    False
                )
            ),

            "min_percent": minimum,

            "max_percent": maximum,
        }


    try:

        with open(
            CONTROL_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            data = json.load(f) or {}


        raw = (
            (data.get("sonarr", {}) or {})
            .get(
                "resolution_policy",
                {}
            )
            or {}
        )


        raw_paths = (
            raw.get("paths")
            or {}
        )

        raw_limits = (
            raw.get("limits")
            or {}
        )

        raw_growth = (
            raw.get("upgrade_growth")
            or {}
        )


        policy = {

            "paths": {

                "720_to_1080": bool(
                    raw_paths.get(
                        "720_to_1080",
                        False
                    )
                ),

                "720_to_2160": bool(
                    raw_paths.get(
                        "720_to_2160",
                        False
                    )
                ),

                "1080_to_2160": bool(
                    raw_paths.get(
                        "1080_to_2160",
                        False
                    )
                ),
            },


            "limits": {

                "minimum_current_size":
                    clean_size_limit(
                        raw_limits.get(
                            "minimum_current_size"
                        ),
                        0,
                        "MiB",
                    ),

                "maximum_1080_size":
                    clean_size_limit(
                        raw_limits.get(
                            "maximum_1080_size"
                        ),
                        10,
                        "GiB",
                    ),

                "maximum_2160_size":
                    clean_size_limit(
                        raw_limits.get(
                            "maximum_2160_size"
                        ),
                        20,
                        "GiB",
                    ),
            },


            "upgrade_growth": {

                "720_to_1080":
                    clean_growth_range(
                        raw_growth.get(
                            "720_to_1080"
                        ),
                        0,
                        40,
                    ),

                "720_to_2160":
                    clean_growth_range(
                        raw_growth.get(
                            "720_to_2160"
                        ),
                        0,
                        100,
                    ),

                "1080_to_2160":
                    clean_growth_range(
                        raw_growth.get(
                            "1080_to_2160"
                        ),
                        0,
                        0,
                    ),
            },
        }


        _LAST_GOOD_RESOLUTION_POLICY = policy

        return policy


    except Exception:

        if (
            _LAST_GOOD_RESOLUTION_POLICY
            is not None
        ):
            return (
                _LAST_GOOD_RESOLUTION_POLICY
            )


        return {

            "paths": {
                "720_to_1080": False,
                "720_to_2160": False,
                "1080_to_2160": False,
            },

            "limits": {

                "minimum_current_size": {
                    "enabled": False,
                    "value": 0.0,
                    "unit": "MiB",
                },

                "maximum_1080_size": {
                    "enabled": False,
                    "value": 10.0,
                    "unit": "GiB",
                },

                "maximum_2160_size": {
                    "enabled": False,
                    "value": 20.0,
                    "unit": "GiB",
                },
            },

            "upgrade_growth": {

                "720_to_1080": {
                    "enabled": False,
                    "min_percent": 0.0,
                    "max_percent": 40.0,
                },

                "720_to_2160": {
                    "enabled": False,
                    "min_percent": 0.0,
                    "max_percent": 100.0,
                },

                "1080_to_2160": {
                    "enabled": False,
                    "min_percent": 0.0,
                    "max_percent": 0.0,
                },
            },
        }


def policy_size_mib(setting):

    value = float(
        setting.get(
            "value",
            0
        )
        or 0
    )

    if (
        setting.get("unit")
        == "GiB"
    ):
        return (
            value
            * 1024.0
        )

    return value


def upgrade_growth_percent(
    old_size_mib,
    new_size_mib,
):

    old_size_mib = float(
        old_size_mib
    )

    new_size_mib = float(
        new_size_mib
    )

    if old_size_mib <= 0:
        return None

    return (
        (
            new_size_mib
            - old_size_mib
        )
        / old_size_mib
        * 100.0
    )


# SMART SONARR RESOLUTION POLICY END





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

try:
    TARGET_EPISODE_ID = max(
        0,
        int(os.environ.get("SMART_OPTIMIZER_EPISODE_ID", "0"))
    )
except (TypeError, ValueError):
    TARGET_EPISODE_ID = 0

try:
    TARGET_SERIES_ID = max(
        0,
        int(os.environ.get("SMART_OPTIMIZER_SERIES_ID", "0"))
    )
except (TypeError, ValueError):
    TARGET_SERIES_ID = 0

TARGETED_MODE = bool(
    TARGET_EPISODE_ID > 0 or TARGET_SERIES_ID > 0
)

MANUAL_TARGET_MODE = (
    str(os.environ.get("SMART_OPTIMIZER_MANUAL_TARGET", "0"))
    .strip()
    .lower()
    in ("1", "true", "yes", "on")
)

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


def all_sonarr_queue_records():
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
        "queue_initialized": False,
        "auto_processed_series_ids": [],
        "tracker_jobs": {}
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
        state.setdefault("auto_processed_series_ids", [])
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
    """Each episode receives at most one automatic /release search."""
    key = str(episode_id)
    state["episodes"].setdefault(key, {})
    entry = state["episodes"][key]
    entry["last_search"] = now_ts()
    entry["search_cycles"] = max(1, int(entry.get("search_cycles", 0)))
    entry["auto_processed"] = True


def auto_processed_series_ids(state):
    return {
        int(x)
        for x in state.get("auto_processed_series_ids", [])
        if str(x).isdigit()
    }


def mark_series_auto_processed(state, series_id):
    processed = auto_processed_series_ids(state)
    processed.add(int(series_id))
    state["auto_processed_series_ids"] = sorted(processed)


def reconcile_auto_processed_series(state):
    """Migrate/close every fully consumed automatic series exactly once."""
    sq = state.get("series_queue", []) or []
    sc = min(int(state.get("series_cursor", 0)), len(sq))
    wq = state.get("work_queue", []) or []
    wc = min(int(state.get("work_cursor", 0)), len(wq))

    loaded = {
        int(x.get("series_id", 0) or 0)
        for x in sq[:sc]
        if int(x.get("series_id", 0) or 0) > 0
    }
    unconsumed = {
        int(x.get("series_id", 0) or 0)
        for x in wq[wc:]
        if int(x.get("series_id", 0) or 0) > 0
    }

    processed = auto_processed_series_ids(state)
    before = set(processed)

    rule_skipped = {
        int(x)
        for x in state.get(
            "rule_skipped_series_ids",
            []
        )
        if str(x).isdigit()
    }

    processed.update(
        (loaded - unconsumed)
        - rule_skipped
    )
    state["auto_processed_series_ids"] = sorted(processed)

    if LIVE and processed != before:
        save_state(state)
        print(
            "AUTO ONE-SHOT: permanently closed %d completed series"
            % len(processed),
            flush=True
        )


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



def atmos_from_text(text):
    """Return True when a release title explicitly identifies Dolby Atmos."""
    text = str(text or "")
    return bool(re.search(r"(?i)(?<![A-Za-z0-9])atmos(?![A-Za-z0-9])", text))

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


def dynamic_range_allowed(existing_range, candidate_range):
    """
    Shared Sonarr/Radarr dynamic-range policy.

    SDR/unknown -> SDR/HDR/DV+HDR : ALLOW
    HDR         -> HDR/DV+HDR     : ALLOW
    DV+HDR      -> DV+HDR only    : ALLOW
    DV-only candidate             : NEVER
    """

    if candidate_range == "DV_ONLY":
        return False

    if existing_range == "DV_HDR":
        return candidate_range == "DV_HDR"

    if existing_range == "HDR":
        return candidate_range in ("HDR", "DV_HDR")

    return candidate_range in ("SDR_UNKNOWN", "HDR", "DV_HDR")


def current_dynamic_range(file_obj):
    """
    Determine the existing file's dynamic range conservatively using
    Sonarr MediaInfo plus scene name/path fallback.
    """
    media = file_obj.get("mediaInfo") or {}

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

    pieces.append(str(file_obj.get("sceneName", "")))
    pieces.append(str(file_obj.get("relativePath", "")))

    return dynamic_range_from_text(" ".join(pieces))


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
    """Expand the next never-processed series into one automatic work pass."""
    sq = state.get("series_queue", [])
    sc = int(state.get("series_cursor", 0))
    processed = auto_processed_series_ids(state)

    while sc < len(sq):
        sref = sq[sc]
        sid = int(sref["series_id"])

        if sid in processed:
            state["series_cursor"] = sc + 1
            sc += 1
            if LIVE:
                save_state(state)
            print(
                "AUTO ONE-SHOT SERIES SKIP:",
                sref.get("series_title"),
                "-- already processed",
                flush=True
            )
            continue

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

        state["series_cursor"] = sc + 1

        if not entries:
            mark_series_auto_processed(state, sid)
            processed.add(sid)
            if LIVE:
                save_state(state)
            print(
                "AUTO ONE-SHOT SERIES COMPLETE:",
                sref.get("series_title"),
                "-- no searchable episode files",
                flush=True
            )
            sc += 1
            continue

        state.setdefault("work_queue", []).extend(entries)
        if LIVE:
            save_state(state)
        print("LOADED NEXT SERIES:", sref.get("series_title"), "·", len(entries), "episodes", flush=True)
        return True

    return False


def item_from_queue_entry(entry, queued_ids, state, ignore_search_history=False):
    """Load metadata only for the next queued episode, never the whole library."""
    episode_id = int(entry["episode_id"])
    if episode_id in queued_ids:
        return None
    hist = state.get("episodes", {}).get(str(episode_id), {})
    cycles = int(hist.get("search_cycles", 0))
    if not ignore_search_history and (bool(hist.get("auto_processed")) or cycles >= 1):
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
    policy = runtime_resolution_policy()

    current_size_mib = mib(
        file_obj.get(
            "size",
            0
        )
    )

    minimum_mib = sonarr_min_current_mib(
        policy
    )

    if (
        minimum_mib is not None
        and current_size_mib < minimum_mib
    ):
        return None


    resolution = file_resolution(
        file_obj
    )

    if resolution not in (
        720,
        1080,
        2160,
    ):
        return None


    upgrade_targets = (
        sonarr_policy_upgrade_targets(
            {
                "resolution": resolution,
                "profile_id": profile_id,
            },
            policy
        )
    )


    target = (
        max(upgrade_targets)
        if upgrade_targets
        else resolution
    )


    media = file_obj.get(
        "mediaInfo"
    ) or {}
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
        "dynamic_range": current_dynamic_range(file_obj),
        "hdr": current_dynamic_range(file_obj) in ("HDR", "DV_HDR"),
        "cooldown_days": 180,
        "priority": 0,
    }


def optimizer_excluded_series_ids():
    try:
        with open(CONTROL_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        raw = (data.get("sonarr", {}) or {}).get("exclusions") or []
        return {
            int(x.get("id"))
            for x in raw
            if isinstance(x, dict) and x.get("id") is not None
        }
    except Exception:
        return set()


def targeted_episode_item(episode_id, queued_ids, state):
    ep = get("/episode/%d" % int(episode_id))

    entry = {
        "episode_id": int(ep["id"]),
        "series_id": int(ep["seriesId"]),
        "season": int(ep.get("seasonNumber", 0)),
        "episode": int(ep.get("episodeNumber", 0)),
    }

    item = item_from_queue_entry(
        entry,
        queued_ids,
        state,
        ignore_search_history=MANUAL_TARGET_MODE
    )

    if (
        item is not None
        and not sonarr_item_enabled_by_rules(item)
    ):
        return None

    return item


def targeted_series_items(series_id, queued_ids, state):
    """
    Temporary work list for one manually selected Sonarr series.

    Does not consume or advance the persistent A-Z queue.
    Existing item_from_queue_entry() remains the eligibility gate.
    """
    series_id = int(series_id)

    if series_id in optimizer_excluded_series_ids():
        print(
            "TARGETED SERIES IS EXCLUDED: %d" % series_id,
            flush=True
        )
        return []

    series = get("/series/%d" % series_id)
    episodes = get("/episode?seriesId=%d" % series_id)

    if not isinstance(episodes, list):
        return []

    items = []

    for ep in episodes:
        try:
            episode_id = int(ep.get("id") or 0)

            if episode_id <= 0:
                continue

            entry = {
                "episode_id": episode_id,
                "series_id": series_id,
                "season": int(ep.get("seasonNumber", 0)),
                "episode": int(ep.get("episodeNumber", 0)),
            }

            item = item_from_queue_entry(
                entry,
                queued_ids,
                state,
                ignore_search_history=MANUAL_TARGET_MODE
            )

            if (
                item is not None
                and sonarr_item_enabled_by_rules(item)
            ):
                items.append(item)

        except Exception as exc:
            print(
                "TARGETED SERIES EPISODE SKIP:",
                ep.get("id"),
                exc,
                flush=True
            )

    items.sort(
        key=lambda x: (
            int(x.get("season", 0)),
            int(x.get("episode", 0))
        )
    )

    print(
        "TARGETED SERIES:",
        series.get("title") or series_id,
        "| eligible episodes:",
        len(items),
        flush=True
    )

    return items


def next_work_items(state, queued_ids, limit):
    reconcile_auto_processed_series(state)

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

        if (
            item is not None
            and not sonarr_item_enabled_by_rules(item)
        ):
            series_id = int(
                item.get("series_id")
                or 0
            )

            skipped = {
                int(x)
                for x in state.get(
                    "rule_skipped_series_ids",
                    []
                )
                if str(x).isdigit()
            }

            if series_id > 0:
                skipped.add(series_id)

                state["rule_skipped_series_ids"] = sorted(
                    skipped
                )

                if LIVE:
                    save_state(state)

            continue

        if item is not None:
            # Exclusion applies to the ENTIRE Sonarr series.
            # Skip every episode before any interactive /release search.
            if int(item.get("series_id", 0)) in optimizer_excluded_series_ids():
                print(
                    "    EXCLUDED SERIES: %s -- episode skipped without searching"
                    % (item.get("series_title") or "Unknown series"),
                    flush=True
                )
                continue

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

    allowed_rejection_substrings = (
        "existing file meets cutoff",
        "upgrade for existing episode file",
    )

    return any(
        token in reason
        for token in allowed_rejection_substrings
    )


def sonarr_rejections_ok(release):
    rejected = release.get("rejections") or []

    for rejection in rejected:
        if not rejection_allowed(rejection):
            return False

    return True


def candidate_resolution(release):
    return quality_resolution(release.get("quality"))


# SONARR POLICY V3C START

def optimizer_is_torrentleech(release):
    raw = str(
        (release or {}).get("indexer")
        or ""
    ).lower()

    normalized = re.sub(
        r"[^a-z0-9]+",
        "",
        raw
    )

    return "torrentleech" in normalized


def optimizer_release_is_pack(release):
    release = release or {}

    for field in (
        "fullSeason",
        "seasonPack",
    ):
        value = release.get(field)

        if (
            value is True
            or str(value).strip().lower()
            in ("1", "true", "yes")
        ):
            return True


    for field in (
        "episodeNumbers",
        "absoluteEpisodeNumbers",
    ):
        values = release.get(field)

        if isinstance(
            values,
            (list, tuple, set)
        ):
            values = {
                str(x).strip()
                for x in values
                if str(x).strip()
            }

            if len(values) > 1:
                return True


    title = str(
        release.get("title")
        or ""
    )


    # Complete Season / Full Season.
    if re.search(
        r"(?i)\b(?:complete|full)\s+season\b",
        title
    ):
        return True


    # S01 COMPLETE / S01 PACK.
    if re.search(
        r"(?i)\bS\d{1,2}\b.*\b(?:complete|pack)\b",
        title
    ):
        return True


    # S01 without an episode number is normally a season pack.
    if (
        re.search(
            r"(?i)\bS\d{1,2}\b",
            title
        )
        and not re.search(
            r"(?i)\bS\d{1,2}E\d{1,3}\b",
            title
        )
    ):
        return True


    # S01E01-E03 / S01E01+E02 / S01E01E02.
    if re.search(
        r"(?i)\bS\d{1,2}E\d{1,3}"
        r"(?:\s*[-+]\s*E?\d{1,3}|E\d{1,3})\b",
        title
    ):
        return True


    tokens = re.findall(
        r"(?i)\bS\d{1,2}E\d{1,3}\b",
        title
    )

    if len(
        {
            token.upper()
            for token in tokens
        }
    ) > 1:
        return True


    return False





def sonarr_upgrade_path_key(
    old_resolution,
    new_resolution,
):
    return {
        (720, 1080): "720_to_1080",
        (720, 2160): "720_to_2160",
        (1080, 2160): "1080_to_2160",
    }.get(
        (
            int(old_resolution),
            int(new_resolution),
        )
    )


def sonarr_candidate_max_mib(
    resolution,
    policy=None,
):
    policy = (
        policy
        or runtime_resolution_policy()
    )

    key = {
        1080: "maximum_1080_size",
        2160: "maximum_2160_size",
    }.get(
        int(resolution)
    )

    if not key:
        return None

    setting = (
        policy["limits"]
        [key]
    )

    if not setting.get("enabled"):
        return None

    return policy_size_mib(
        setting
    )


def sonarr_upgrade_growth_range(
    old_resolution,
    new_resolution,
    policy=None,
):
    policy = (
        policy
        or runtime_resolution_policy()
    )

    key = sonarr_upgrade_path_key(
        old_resolution,
        new_resolution,
    )

    if not key:
        return None

    return (
        policy["upgrade_growth"]
        .get(key)
    )


# SMART SONARR FLEXIBLE CANDIDATE POLICY V1


def evaluate_release(item, release, state):
    title = str(
        release.get("title")
        or ""
    )


    # ========================================================
    # EXISTING SAFETY GATES
    # ========================================================

    if dangerous_release_title(title):
        return None


    if not sonarr_rejections_ok(release):
        return None


    if release_recently_attempted(
        state,
        release
    ):
        return None


    # Smart Optimizer works ONE EPISODE -> ONE FILE.
    # Never compare a season pack against one episode file.
    if optimizer_release_is_pack(release):

        print(
            "    PACK RULE: "
            "season/multi-episode release | REJECT",
            flush=True
        )

        return None


    try:
        seeders = int(
            release.get("seeders")
        )
    except (
        TypeError,
        ValueError,
    ):
        return None


    if seeders < MIN_SEEDERS:
        return None


    # ========================================================
    # RESOLUTION
    # ========================================================

    new_res = candidate_resolution(
        release
    )

    if not new_res:
        return None


    new_res = int(
        new_res
    )

    old_res = int(
        item["resolution"]
    )

    profile_id = int(
        item.get("profile_id")
        or 0
    )

    rules = runtime_rules()
    policy = runtime_resolution_policy()


    # Never downgrade resolution.
    if new_res < old_res:

        print(
            "    RESOLUTION RULE: "
            "%dp -> %dp downgrade | REJECT"
            % (
                old_res,
                new_res,
            ),
            flush=True
        )

        return None


    # Same-resolution storage optimization.
    if new_res == old_res:

        # 720p is handled only through an explicit upgrade path.
        if old_res == 720:

            print(
                "    RESOLUTION RULE: "
                "720p same-resolution optimization disabled | REJECT",
                flush=True
            )

            return None


        if not rules.get(
            "storage_optimization"
        ):

            print(
                "    RESOLUTION RULE: "
                "same-resolution storage optimization disabled | REJECT",
                flush=True
            )

            return None


    # Resolution upgrade.
    else:

        allowed_targets = (
            sonarr_policy_upgrade_targets(
                item,
                policy
            )
        )

        if new_res not in allowed_targets:

            print(
                "    RESOLUTION RULE: "
                "%dp -> %dp path disabled | REJECT"
                % (
                    old_res,
                    new_res,
                ),
                flush=True
            )

            return None


        print(
            "    RESOLUTION RULE: "
            "%dp -> %dp path enabled | PASS"
            % (
                old_res,
                new_res,
            ),
            flush=True
        )


    # ========================================================
    # CODEC
    # ========================================================

    codec = codec_from_text(
        title
    )


    # NO AV1 for series.
    if (
        codec == "av1"
        and rules.get("block_av1")
    ):

        print(
            "    CODEC RULE: AV1 | REJECT",
            flush=True
        )

        return None


    # ========================================================
    # HDR / DV / ATMOS
    # ========================================================

    dynamic_range = (
        dynamic_range_from_text(
            title
        )
    )

    candidate_audio = (
        audio_channels_from_text(
            title
        )
    )

    candidate_atmos = (
        atmos_from_text(
            title
        )
    )

    candidate_hdr = (
        dynamic_range
        in (
            "HDR",
            "DV_HDR",
        )
    )


    # DV must also advertise HDR fallback.
    if dynamic_range == "DV_ONLY":

        print(
            "    HDR/DV RULE: "
            "DV without HDR fallback | REJECT",
            flush=True
        )

        return None


    # Normal 1080:
    # HDR / Atmos do NOT get priority over file size.
    #
    # UHD:
    # require at least HDR.
    if (
        rules.get("require_hdr_uhd")
        and profile_id == UHD_PROFILE_ID
        and new_res == 2160
        and dynamic_range
        not in (
            "HDR",
            "DV_HDR",
        )
    ):

        print(
            "    UHD RULE: "
            "2160p must advertise HDR | REJECT",
            flush=True
        )

        return None


    # ========================================================
    # SIZE
    # ========================================================

    old_size = float(
        item["size_mib"]
    )

    new_size = float(
        mib(
            release.get(
                "size",
                0
            )
        )
    )


    if (
        old_size <= 0
        or new_size <= 0
    ):
        return None


    EPS = 0.000001
    PERCENT_EPSILON = 0.000001


    # --------------------------------------------------------
    # Optional absolute ceiling by TARGET resolution.
    #
    # Example:
    #   max 1080p = 8 GiB
    #   max 2160p = 20 GiB
    #
    # OFF means no absolute target-size ceiling.
    # --------------------------------------------------------

    maximum_mib = sonarr_candidate_max_mib(
        new_res,
        policy
    )

    if (
        maximum_mib is not None
        and new_size
        > maximum_mib + EPS
    ):

        print(
            "    TARGET SIZE CEILING: "
            "%.1f MiB > %.1f MiB for %dp | REJECT"
            % (
                new_size,
                maximum_mib,
                new_res,
            ),
            flush=True
        )

        return None


    is_upgrade = (
        new_res > old_res
    )


    # --------------------------------------------------------
    # Candidate is SMALLER.
    #
    # Downsize rules apply regardless of whether this is
    # same-resolution or a resolution upgrade.
    # --------------------------------------------------------

    if new_size < old_size - EPS:

        saving = (
            (
                old_size
                - new_size
            )
            / old_size
            * 100.0
        )


        if (
            saving
            < MIN_SAVING_PERCENT
            - PERCENT_EPSILON
        ):

            print(
                "    DOWNSIZE RULE: "
                "%.2f%% saving below %.1f%% | REJECT"
                % (
                    saving,
                    MIN_SAVING_PERCENT,
                ),
                flush=True
            )

            return None


        # Use the configured RULES maximum directly.
        effective_max_saving = float(
            MAX_SAVING_PERCENT
        )


        if (
            saving
            > effective_max_saving
            + PERCENT_EPSILON
        ):

            print(
                "    DOWNSIZE RULE: "
                "%.2f%% saving above %.1f%% "
                "configured maximum | REJECT"
                % (
                    saving,
                    effective_max_saving,
                ),
                flush=True
            )

            return None


        print(
            "    DOWNSIZE RULE: "
            "%.2f%% saving | allowed %.1f%%-%.1f%% | PASS"
            % (
                saving,
                MIN_SAVING_PERCENT,
                effective_max_saving,
            ),
            flush=True
        )


        reason = (
            "%dp to %dp downsize"
            % (
                old_res,
                new_res,
            )
            if is_upgrade
            else "strictly smaller replacement"
        )


    # --------------------------------------------------------
    # Equal or LARGER.
    #
    # Only an enabled resolution upgrade can do this.
    # Its own Growth range decides the limit.
    # --------------------------------------------------------

    else:

        saving = (
            (
                old_size
                - new_size
            )
            / old_size
            * 100.0
        )


        if not is_upgrade:

            print(
                "    SIZE RULE: "
                "same-resolution replacement is not smaller | REJECT",
                flush=True
            )

            return None


        growth_setting = (
            sonarr_upgrade_growth_range(
                old_res,
                new_res,
                policy
            )
        )


        if (
            not growth_setting
            or not growth_setting.get(
                "enabled"
            )
        ):

            print(
                "    UPGRADE GROWTH: "
                "growth disabled for %dp -> %dp | REJECT"
                % (
                    old_res,
                    new_res,
                ),
                flush=True
            )

            return None


        growth = max(
            0.0,
            upgrade_growth_percent(
                old_size,
                new_size,
            )
        )


        minimum_growth = float(
            growth_setting.get(
                "min_percent",
                0.0
            )
        )

        maximum_growth = float(
            growth_setting.get(
                "max_percent",
                0.0
            )
        )


        if (
            growth
            < minimum_growth
            - PERCENT_EPSILON
            or growth
            > maximum_growth
            + PERCENT_EPSILON
        ):

            print(
                "    UPGRADE GROWTH: "
                "%dp -> %dp | %.2f%% "
                "outside %.1f%%-%.1f%% | REJECT"
                % (
                    old_res,
                    new_res,
                    growth,
                    minimum_growth,
                    maximum_growth,
                ),
                flush=True
            )

            return None


        print(
            "    UPGRADE GROWTH: "
            "%dp -> %dp | %.2f%% "
            "allowed %.1f%%-%.1f%% | PASS"
            % (
                old_res,
                new_res,
                growth,
                minimum_growth,
                maximum_growth,
            ),
            flush=True
        )


        reason = (
            "%dp to %dp upgrade"
            % (
                old_res,
                new_res,
            )
        )


    return {
        "release": release,
        "resolution": new_res,
        "size_mib": new_size,
        "codec": codec,
        "audio": candidate_audio,
        "atmos": candidate_atmos,
        "hdr": candidate_hdr,
        "dynamic_range": dynamic_range,
        "saving_percent": saving,
        "reason": reason,
        "torrentleech": (
            optimizer_is_torrentleech(
                release
            )
        ),
    }



def choose_best(item, releases, state):
    valid = []


    for release in releases:

        candidate = evaluate_release(
            item,
            release,
            state
        )

        if candidate:

            valid.append(
                candidate
            )


    if not valid:
        return None

    rules = runtime_rules()

    # SMART SONARR RANKING V2A
    #
    # All releases that passed evaluate_release() remain
    # eligible. Preferred indexers are now an ORDERED ranking
    # preference instead of a hard TorrentLeech-only pool.
    #
    # Nonpreferred indexers remain valid fallbacks.

    preferences = (
        runtime_advanced_preferences()
    )

    pool = list(
        valid
    )


    old_res = int(
        item["resolution"]
    )

    profile_id = int(
        item.get("profile_id")
        or 0
    )


    # Defensive resolution filter using the live policy.

    policy = runtime_resolution_policy()
    rules = runtime_rules()

    allowed_targets = (
        sonarr_policy_upgrade_targets(
            item,
            policy
        )
    )


    higher = [
        candidate
        for candidate in pool
        if (
            int(
                candidate.get(
                    "resolution",
                    0
                )
            )
            > old_res
            and int(
                candidate.get(
                    "resolution",
                    0
                )
            )
            in allowed_targets
        )
    ]


    if higher:

        highest_resolution = max(
            int(
                candidate["resolution"]
            )
            for candidate in higher
        )

        pool = [
            candidate
            for candidate in higher
            if int(
                candidate["resolution"]
            )
            == highest_resolution
        ]


    elif (
        old_res in (
            1080,
            2160,
        )
        and rules.get(
            "storage_optimization"
        )
    ):

        pool = [
            candidate
            for candidate in pool
            if int(
                candidate["resolution"]
            )
            == old_res
        ]


    else:

        pool = []


    if not pool:
        return None


    # ========================================================
    # 4K PROFILE
    # ========================================================
    #
    # Within the primary indexer pool:
    #
    # 1. DV + HDR
    # 2. HDR
    # 3. Atmos
    # 4. Smallest file
    #
    # Every release has ALREADY passed strict size safety.

    if (
        profile_id == UHD_PROFILE_ID
        and pool[0]["resolution"] == 2160
    ):

        dr_rank = {
            "DV_HDR": 2,
            "HDR": 1,
        }


        # SMART SONARR RANKING V2B
        #
        # UHD ranking:
        #   1. Preferred indexer order
        #   2. Enabled source preference
        #   3. Dynamic range
        #   4. Atmos
        #   5. Advanced quality preferences
        #   6. Smaller file
        #   7. Codec preference
        #   8. Seeders
        #   9. Title

        pool.sort(
            key=lambda x: (

                sonarr_v2_indexer_rank(
                    x,
                    preferences
                ),

                sonarr_v2_source_rank(
                    x,
                    preferences
                ),

                (
                    -dr_rank.get(
                        x.get(
                            "dynamic_range",
                            "SDR_UNKNOWN"
                        ),
                        0
                    )
                    if rules.get(
                        "prefer_dynamic_range"
                    )
                    else 0
                ),

                (
                    -int(
                        bool(
                            x.get(
                                "atmos"
                            )
                        )
                    )
                    if rules.get(
                        "prefer_atmos"
                    )
                    else 0
                ),

                sonarr_v2_boolean_rank(
                    preferences.get(
                        "prefer_hdr10plus"
                    ),
                    sonarr_v2_is_hdr10plus(
                        x
                    )
                ),

                sonarr_v2_boolean_rank(
                    preferences.get(
                        "prefer_10bit"
                    ),
                    sonarr_v2_is_10bit(
                        x
                    )
                ),

                sonarr_v2_boolean_rank(
                    preferences.get(
                        "prefer_dtsx"
                    ),
                    sonarr_v2_is_dtsx(
                        x
                    )
                ),

                sonarr_v2_boolean_rank(
                    preferences.get(
                        "prefer_lossless_audio"
                    ),
                    sonarr_v2_is_lossless_audio(
                        x
                    )
                ),

                sonarr_v2_boolean_rank(
                    preferences.get(
                        "prefer_eac3"
                    ),
                    sonarr_v2_is_eac3(
                        x
                    )
                ),

                sonarr_v2_boolean_rank(
                    preferences.get(
                        "prefer_proper_repack"
                    ),
                    sonarr_v2_is_proper_repack(
                        x
                    )
                ),

                sonarr_v2_boolean_rank(
                    preferences.get(
                        "prefer_freeleech"
                    ),
                    sonarr_v2_is_freeleech(
                        x
                    )
                ),

                (
                    float(
                        x.get(
                            "size_mib"
                        )
                        or 0
                    )
                    if preferences.get(
                        "prefer_smaller"
                    )
                    else 0
                ),

                sonarr_v2_codec_rank(
                    x,
                    preferences
                ),

                (
                    -sonarr_v2_choice_seeders(
                        x
                    )
                    if preferences.get(
                        "prefer_seeders"
                    )
                    else 0
                ),

                sonarr_v2_choice_title(
                    x
                ).casefold(),
            )
        )


    # ========================================================
    # NORMAL 1080 / 720->1080
    # ========================================================
    #
    # STORAGE IS KING.
    #
    # HDR, Atmos and audio channel count may NOT make a larger
    # release win.

    else:

        # SMART SONARR NORMAL RANKING V2B
        #
        # Normal 720/1080 ranking deliberately does NOT add
        # HDR/Atmos priority. Those remain UHD-specific here.

        pool.sort(
            key=lambda x: (

                sonarr_v2_indexer_rank(
                    x,
                    preferences
                ),

                sonarr_v2_source_rank(
                    x,
                    preferences
                ),

                sonarr_v2_boolean_rank(
                    preferences.get(
                        "prefer_hdr10plus"
                    ),
                    sonarr_v2_is_hdr10plus(
                        x
                    )
                ),

                sonarr_v2_boolean_rank(
                    preferences.get(
                        "prefer_10bit"
                    ),
                    sonarr_v2_is_10bit(
                        x
                    )
                ),

                sonarr_v2_boolean_rank(
                    preferences.get(
                        "prefer_dtsx"
                    ),
                    sonarr_v2_is_dtsx(
                        x
                    )
                ),

                sonarr_v2_boolean_rank(
                    preferences.get(
                        "prefer_lossless_audio"
                    ),
                    sonarr_v2_is_lossless_audio(
                        x
                    )
                ),

                sonarr_v2_boolean_rank(
                    preferences.get(
                        "prefer_eac3"
                    ),
                    sonarr_v2_is_eac3(
                        x
                    )
                ),

                sonarr_v2_boolean_rank(
                    preferences.get(
                        "prefer_proper_repack"
                    ),
                    sonarr_v2_is_proper_repack(
                        x
                    )
                ),

                sonarr_v2_boolean_rank(
                    preferences.get(
                        "prefer_freeleech"
                    ),
                    sonarr_v2_is_freeleech(
                        x
                    )
                ),

                (
                    float(
                        x.get(
                            "size_mib"
                        )
                        or 0
                    )
                    if preferences.get(
                        "prefer_smaller"
                    )
                    else 0
                ),

                sonarr_v2_codec_rank(
                    x,
                    preferences
                ),

                (
                    -sonarr_v2_choice_seeders(
                        x
                    )
                    if preferences.get(
                        "prefer_seeders"
                    )
                    else 0
                ),

                sonarr_v2_choice_title(
                    x
                ).casefold(),
            )
        )


    return pool[0]


# SONARR POLICY V3C END

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

    active_rules = sync_sonarr_target_rules(state)

    # SMART SONARR TARGET RULE SYNC MAIN

    if not sonarr_has_target_rule(active_rules):
        print("Selectable target rules: none")
        print("Nothing will be searched.")
        return

    print("Daily interactive-search budget:", DAILY_SEARCH_BUDGET + DAILY_EXTRA_BUDGET, "(base %d + today override %d)" % (DAILY_SEARCH_BUDGET, DAILY_EXTRA_BUDGET))
    print("Same-resolution saving window: %.1f%% to %.1f%%" % (MIN_SAVING_PERCENT, MAX_SAVING_PERCENT))
    print("Resolution rule: ONLY 720p->1080p may grow up to +40%; all 1080p/2160p replacements must shrink")
    print()

    used = searches_used_today(state)

    # Maximum interactive searches in one execution.
    PER_RUN_SEARCH_BUDGET = max(1, SEARCHES_PER_RUN)

    if MANUAL_TARGET_MODE:
        # Targeted Manual Optimizer runs are intentionally independent
        # of the scheduled persistent daily-search allowance.
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
    print("Reading Sonarr queue...")

    queued_ids = active_episode_ids()

    print(
        "Episodes currently represented in queue:",
        len(queued_ids)
    )

    print()

    targeted_series = []

    if TARGET_EPISODE_ID > 0:
        print("TARGETED EPISODE MODE:", TARGET_EPISODE_ID)
        print("Persistent A-Z queue/cursor will not be consumed.")

    elif TARGET_SERIES_ID > 0:
        print("TARGETED SERIES MODE:", TARGET_SERIES_ID)
        print("Persistent A-Z queue/cursor will not be consumed.")
        print("Scheduled daily search allowance will not be consumed.")

        targeted_series = targeted_series_items(
            TARGET_SERIES_ID,
            queued_ids,
            state
        )

    else:
        print("Loading persistent A-Z queue...")
        if not state.get("queue_initialized"):
            initialize_work_queue(state)
        append_new_series(state)

    if TARGET_EPISODE_ID > 0:
        target_searches = 1
    elif TARGET_SERIES_ID > 0:
        target_searches = min(100, len(targeted_series))
    else:
        target_searches = remaining

    searches = 0
    grabs = 0
    no_match = 0
    errors = 0
    number = 0

    # Scheduled and manual runs consume the same persistent queue/cursor.
    # Normal/scheduled runs remain search-budget based.
    # When SMART_OPTIMIZER_TARGET_GRABS is set by the UI, the requested number
    # represents successful releases sent to Sonarr. Unsuccessful searches do
    # not satisfy that target, but every /release lookup still consumes the
    # normal daily/per-run search budget.
    while searches < target_searches and (TARGET_GRABS <= 0 or grabs < TARGET_GRABS):
        active_rules = sync_sonarr_target_rules(state)

        if not sonarr_has_target_rule(active_rules):
            print(
                "RULE CHANGE: no Sonarr target rules enabled.",
                flush=True
            )
            break

        if not MANUAL_TARGET_MODE:
            actual_left = max(
                0,
                DAILY_SEARCH_BUDGET
                + DAILY_EXTRA_BUDGET
                - searches_used_today(state)
            )
            if LIVE and actual_left <= 0:
                print(
                    "Daily interactive-search budget exhausted.",
                    flush=True
                )
                break

        if TARGET_EPISODE_ID > 0:
            if number > 0:
                break

            targeted = targeted_episode_item(
                TARGET_EPISODE_ID,
                queued_ids,
                state
            )
            selected = [targeted] if targeted else []

        elif TARGET_SERIES_ID > 0:
            if number >= len(targeted_series):
                break

            targeted_item = targeted_series[number]

            if not sonarr_item_enabled_by_rules(
                targeted_item
            ):
                number += 1
                continue

            selected = [targeted_item]

        else:
            selected = next_work_items(
                state,
                queued_ids,
                1
            )

        if not selected:
            if TARGET_EPISODE_ID > 0:
                print(
                    "TARGETED EPISODE SKIPPED: unavailable, queued, "
                    "below the configured minimum current size, or otherwise ineligible.",
                    flush=True
                )
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
                if not MANUAL_TARGET_MODE:
                    increment_search_count(state)

                # A real successful /release search is one-shot forever for
                # automatic runs. Explicit Manual Optimizer retries bypass it.
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

        # SONARR V3C FINAL FILE GUARD START
        #
        # Search/evaluation can take time.
        # Immediately before /release, prove the episode still has
        # the exact file that choose_best() evaluated.

        try:

            fresh_ep = get(
                "/episode/%d"
                % episode_id
            ) or {}


            original_file = (
                item.get("file")
                or {}
            )


            expected_id = int(
                original_file.get("id")
                or 0
            )

            fresh_id = int(
                fresh_ep.get(
                    "episodeFileId"
                )
                or 0
            )


            if (
                not fresh_ep.get("hasFile")
                or expected_id <= 0
                or fresh_id != expected_id
            ):

                print(
                    "    FINAL FILE GUARD: "
                    "episodeFileId changed | SKIP",
                    flush=True
                )

                print()
                continue


            fresh_file = get(
                "/episodefile/%d"
                % fresh_id
            ) or {}


            expected_size = int(
                original_file.get("size")
                or 0
            )

            fresh_size = int(
                fresh_file.get("size")
                or 0
            )


            if (
                expected_size <= 0
                or fresh_size != expected_size
            ):

                print(
                    "    FINAL FILE GUARD: "
                    "file size changed | SKIP",
                    flush=True
                )

                print()
                continue


            fresh_resolution = int(
                file_resolution(
                    fresh_file
                )
                or 0
            )


            if (
                fresh_resolution
                != int(
                    item["resolution"]
                )
            ):

                print(
                    "    FINAL FILE GUARD: "
                    "resolution changed | SKIP",
                    flush=True
                )

                print()
                continue


            fresh_series = get(
                "/series/%d"
                % int(
                    item["series_id"]
                )
            ) or {}


            if int(
                fresh_series.get(
                    "qualityProfileId"
                )
                or 0
            ) != int(
                item["profile_id"]
            ):

                print(
                    "    FINAL FILE GUARD: "
                    "quality profile changed | SKIP",
                    flush=True
                )

                print()
                continue


        except Exception as exc:

            print(
                "    FINAL FILE GUARD: "
                "revalidation failed | SKIP:",
                exc,
                flush=True
            )

            print()
            continue

        # SONARR V3C FINAL FILE GUARD END

        try:
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
        print()

    if LIVE and MANUAL_TARGET_MODE and TARGET_SERIES_ID > 0 and searches > 0:
        # If the user manually handled a never-processed series first, do not
        # later surprise them with another automatic optimizer pass.
        mark_series_auto_processed(state, TARGET_SERIES_ID)
        save_state(state)

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
