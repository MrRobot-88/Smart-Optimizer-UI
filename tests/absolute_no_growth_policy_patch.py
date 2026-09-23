#!/usr/bin/env python3
import hashlib
import re
import sys
from pathlib import Path

if len(sys.argv) != 4:
    raise SystemExit(
        "usage: absolute_no_growth_policy_patch.py RADARR SONARR UI"
    )

rad_path=Path(sys.argv[1])
son_path=Path(sys.argv[2])
ui_path=Path(sys.argv[3])

rad=rad_path.read_text(encoding="utf-8")
son=son_path.read_text(encoding="utf-8")
ui=ui_path.read_text(encoding="utf-8")

EXPECTED={
    "Radarr":(
        rad,
        "6594cbb61efb9cf1d62b798a7847c39bee86e64c8f03e16ed3517ab10edee85c",
    ),
    "Sonarr":(
        son,
        "e4abbefadc5922e9cc2c25b7096bd062ad012850ec21764cb0690fb3f2f80789",
    ),
    "UI":(
        ui,
        "0da64f80fbfeb809b551f0e3ed915e79306d4e69fb0096deb490f70e6c8fae67",
    ),
}

def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

for label,(text,expected) in EXPECTED.items():
    actual=sha(text)
    if actual != expected:
        raise SystemExit(
            "STOP: %s source hash mismatch: %s"
            % (label,actual)
        )

def once(text,old,new,label):
    n=text.count(old)
    if n != 1:
        raise SystemExit(
            "STOP: %s: expected exactly 1 match, found %d"
            % (label,n)
        )
    return text.replace(old,new,1)

# ----------------------------------------------------------------------
# RADARR
#
# Existing policy already requires positive storage savings, but make the
# invariant explicit and future-proof:
#   * NO candidate may ever be larger than the current movie file.
#   * 1080p candidates have an additional hard 10 GiB ceiling.
# This applies regardless of monitored/unmonitored state.
# ----------------------------------------------------------------------
rad=once(
    rad,
    '''    candidate_mib = mib(size_bytes)
    current_mib = item["size_mib"]

    saving = ((current_mib - candidate_mib) / current_mib) * 100.0
''',
    '''    candidate_mib = mib(size_bytes)
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
''',
    "Radarr absolute no-growth rule",
)

# ----------------------------------------------------------------------
# SONARR
#
# Remove the old +40% low-resolution exception. From now on every episode
# replacement / resolution upgrade must be <= current episode size and must
# still satisfy the normal configured saving window.
# ----------------------------------------------------------------------
son_old='''    # LOW-RESOLUTION UPGRADE RULE -- SONARR ONLY
    #
    # Existing SD/480p/720p episodes may upgrade toward the resolution wanted
    # by their Sonarr profile. The replacement may be smaller, equal-sized,
    # or at most 40% larger than the existing episode.
    #
    # Example:
    #   720p 1.2 GiB -> 1080p 700 MiB  = PASS
    #   720p 1.2 GiB -> 1080p 1.8 GiB  = PASS
    #   720p 1.2 GiB -> 1080p 3.5 GiB  = REJECT
    #
    # This exception does NOT apply to 1080p -> 2160p.
    is_lowres_upgrade = (
        old_res < 1080
        and new_res > old_res
        and new_res <= target
    )

    if is_lowres_upgrade:
        MAX_LOWRES_UPGRADE_INCREASE_PERCENT = 40.0
        max_upgrade_size = old_size * (
            1.0 + MAX_LOWRES_UPGRADE_INCREASE_PERCENT / 100.0
        )

        increase = (
            ((new_size - old_size) / old_size) * 100.0
        )

        if new_size > max_upgrade_size + SAVING_EPSILON:
            print(
                "    LOW-RES UPGRADE SIZE RULE: %.3f%% size change | "
                "maximum +%.1f%% | REJECT"
                % (increase, MAX_LOWRES_UPGRADE_INCREASE_PERCENT),
                flush=True
            )
            return None

        print(
            "    LOW-RES UPGRADE SIZE RULE: %.3f%% size change | "
            "maximum +%.1f%% | PASS"
            % (increase, MAX_LOWRES_UPGRADE_INCREASE_PERCENT),
            flush=True
        )

    else:
        if saving < MIN_SAVING_PERCENT - SAVING_EPSILON:
            print(
                "    SIZE RULE: %.3f%% saving | allowed %.1f%%-%.1f%% | REJECT: below minimum"
                % (saving, MIN_SAVING_PERCENT, MAX_SAVING_PERCENT),
                flush=True
            )
            return None

        if saving > MAX_SAVING_PERCENT + SAVING_EPSILON:
            print(
                "    SIZE RULE: %.3f%% saving | allowed %.1f%%-%.1f%% | REJECT: above maximum"
                % (saving, MIN_SAVING_PERCENT, MAX_SAVING_PERCENT),
                flush=True
            )
            return None

        print(
            "    SIZE RULE: %.3f%% saving | allowed %.1f%%-%.1f%% | PASS"
            % (saving, MIN_SAVING_PERCENT, MAX_SAVING_PERCENT),
            flush=True
        )
'''

son_new='''    # ABSOLUTE STORAGE INVARIANT -- SONARR:
    # No episode replacement may ever be larger than its current file.
    # This applies to same-resolution replacements AND resolution upgrades,
    # regardless of monitored/unmonitored state.
    if new_size > old_size + SAVING_EPSILON:
        print(
            "    ABSOLUTE SIZE RULE: candidate %.1f MiB > current %.1f MiB | REJECT"
            % (new_size, old_size),
            flush=True
        )
        return None

    if saving < MIN_SAVING_PERCENT - SAVING_EPSILON:
        print(
            "    SIZE RULE: %.3f%% saving | allowed %.1f%%-%.1f%% | REJECT: below minimum"
            % (saving, MIN_SAVING_PERCENT, MAX_SAVING_PERCENT),
            flush=True
        )
        return None

    if saving > MAX_SAVING_PERCENT + SAVING_EPSILON:
        print(
            "    SIZE RULE: %.3f%% saving | allowed %.1f%%-%.1f%% | REJECT: above maximum"
            % (saving, MIN_SAVING_PERCENT, MAX_SAVING_PERCENT),
            flush=True
        )
        return None

    print(
        "    SIZE RULE: %.3f%% saving | allowed %.1f%%-%.1f%% | PASS"
        % (saving, MIN_SAVING_PERCENT, MAX_SAVING_PERCENT),
        flush=True
    )
'''

son=once(
    son,
    son_old,
    son_new,
    "Sonarr remove +40% growth exception",
)

# ----------------------------------------------------------------------
# WATCHDOG RESEARCH
#
# The old backlog watchdog used Arr's unrestricted native MoviesSearch /
# EpisodeSearch. That bypassed Smart Optimizer size/audio/HDR rules and caused
# examples such as 23.54 GiB -> 27.41 GiB.
#
# If the item already has a current library file, route the retry through the
# exact Smart Optimizer target mode. Monitored status is intentionally ignored.
# Missing-file acquisition has no current-size baseline, so it remains a normal
# Arr missing-media search rather than pretending a comparison exists.
# ----------------------------------------------------------------------
old_ui='''def _dead_watchdog_native_search(app, media_id):
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
'''

new_ui='''def _dead_watchdog_research(app, media_id):
    """
    Re-search one exact failed Arr item.

    Existing library file:
        Route through Smart Optimizer so absolute size, codec, audio, HDR/DV
        and source rules are enforced. Monitored/unmonitored does not matter.

    Missing library file:
        There is no current-size baseline to compare against, so leave that
        acquisition to the native Arr missing-media search.
    """
    media_id = int(media_id)

    if app == "radarr":
        movie = radarr_get(
            "/movie/%d" % media_id
        ) or {}

        if (
            bool(movie.get("hasFile"))
            and int(movie.get("movieFileId") or 0) > 0
        ):
            return run_optimizer(
                True,
                app="radarr",
                searches_per_run=None,
                target_movie_id=media_id,
                manual_target=True,
            )

        return radarr_request(
            "/command",
            method="POST",
            payload={
                "name": "MoviesSearch",
                "movieIds": [media_id],
            },
        )

    episode = sonarr_get(
        "/episode/%d" % media_id
    ) or {}

    if (
        bool(episode.get("hasFile"))
        and int(episode.get("episodeFileId") or 0) > 0
    ):
        return run_optimizer(
            True,
            app="sonarr",
            searches_per_run=None,
            target_episode_id=media_id,
            manual_target=True,
        )

    return sonarr_request(
        "/command",
        method="POST",
        payload={
            "name": "EpisodeSearch",
            "episodeIds": [media_id],
        },
    )
'''

ui=once(
    ui,
    old_ui,
    new_ui,
    "Watchdog optimizer-routed research",
)

ui=once(
    ui,
    '''                    _dead_watchdog_native_search(
                        app,
                        media_id,
                    )
''',
    '''                    _dead_watchdog_research(
                        app,
                        media_id,
                    )
''',
    "Watchdog call site",
)

rad_path.write_text(rad,encoding="utf-8")
son_path.write_text(son,encoding="utf-8")
ui_path.write_text(ui,encoding="utf-8")

print("PATCH COMPLETE")
print("RADARR SHA256",sha(rad))
print("SONARR SHA256",sha(son))
print("UI SHA256",sha(ui))
