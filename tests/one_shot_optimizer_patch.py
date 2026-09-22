#!/usr/bin/env python3
import hashlib, sys
from pathlib import Path

if len(sys.argv) != 3:
    raise SystemExit('usage: one_shot_patcher.py RADARR_PATH SONARR_PATH')

rad_path=Path(sys.argv[1]); son_path=Path(sys.argv[2])
rad=rad_path.read_text(encoding='utf-8'); son=son_path.read_text(encoding='utf-8')

EXPECTED_RAD='c5790a7acff822a921e05573247a03d0a5bdf2b62fbe67b76de3b0c33c497cd1'
EXPECTED_SON='5c4a18e6898f1a4e4b87d7049ceb0b3141b129ae12a048a18d529a4f7b3d8507'

def sha(s): return hashlib.sha256(s.encode()).hexdigest()
if sha(rad)!=EXPECTED_RAD: raise SystemExit('STOP: Radarr source hash mismatch: '+sha(rad))
if sha(son)!=EXPECTED_SON: raise SystemExit('STOP: Sonarr source hash mismatch: '+sha(son))

def once(text, old, new, label):
    n=text.count(old)
    if n!=1: raise SystemExit(f'STOP: {label}: expected 1 match, found {n}')
    return text.replace(old,new,1)

rad=once(rad,
'''        "queue_initialized": False,
        "pending_replacements": {}
''',
'''        "queue_initialized": False,
        "pending_replacements": {},
        "auto_processed_movie_ids": []
''','rad blank state')
rad=once(rad,
'''        state.setdefault("queue_initialized", False)
        state.setdefault("pending_replacements", {})
''',
'''        state.setdefault("queue_initialized", False)
        state.setdefault("pending_replacements", {})
        state.setdefault("auto_processed_movie_ids", [])
''','rad load state')
rad=once(rad,
'''def mark_movie_searched(state, movie_id):
    key = str(movie_id)
    state["movies"].setdefault(key, {})
    entry = state["movies"][key]
    entry["last_search"] = now_ts()
    entry["search_cycles"] = min(2, int(entry.get("search_cycles", 0)) + 1)


''',
'''def auto_processed_movie_ids(state):
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


''','rad mark helper')
rad=once(rad,
'''    history = state.get("movies", {}).get(str(movie_id), {})
    cycles = int(history.get("search_cycles", 0))
    last_search = history.get("last_search")
    if cycles >= 2:
        return None
    if cycles == 1 and last_search and age_days(last_search) < 180:
        return None

''',
'''    # Automatic optimizer is strictly one-shot per movie forever.
    # Manual Optimizer creates a temporary state with this ID removed, so the
    # user can explicitly retry as many times as desired.
    if int(movie_id) in auto_processed_movie_ids(state):
        return None

''','rad one-shot eligibility')
rad=once(rad,
'''            targeted_state = dict(state)
            targeted_state["movies"] = dict(state.get("movies", {}))
            targeted_state["movies"].pop(str(TARGET_MOVIE_ID), None)

''',
'''            targeted_state = dict(state)
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

''','rad manual bypass')
rad=once(rad,
'''            if LIVE:
                if not MANUAL_TARGET_MODE:
                    increment_search_count(state)
                    mark_movie_searched(
                        state,
                        movie_id
                    )

                # Targeted runs still persist optimizer state, but do not
                # consume the scheduled counter or normal search cycle.
                save_state(state)
''',
'''            if LIVE:
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
''','rad search mark')

son=once(son,
'''        "series_cursor": 0,
        "queue_initialized": False
''',
'''        "series_cursor": 0,
        "queue_initialized": False,
        "auto_processed_series_ids": []
''','son blank state')
son=once(son,
'''        state.setdefault("series_cursor", 0)
        state.setdefault("queue_initialized", False)
''',
'''        state.setdefault("series_cursor", 0)
        state.setdefault("queue_initialized", False)
        state.setdefault("auto_processed_series_ids", [])
''','son load state')
son=once(son,
'''def mark_episode_searched(state, episode_id):
    key = str(episode_id)
    state["episodes"].setdefault(key, {})
    entry = state["episodes"][key]
    entry["last_search"] = now_ts()
    entry["search_cycles"] = min(2, int(entry.get("search_cycles", 0)) + 1)


''',
'''def mark_episode_searched(state, episode_id):
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
    processed.update(loaded - unconsumed)
    state["auto_processed_series_ids"] = sorted(processed)

    if LIVE and processed != before:
        save_state(state)
        print(
            "AUTO ONE-SHOT: permanently closed %d completed series"
            % len(processed),
            flush=True
        )


''','son mark helpers')
old_load='''def load_next_series_episodes(state):
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
'''
new_load='''def load_next_series_episodes(state):
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
'''
son=once(son,old_load,new_load,'son load next series')
son=once(son,
'''def item_from_queue_entry(entry, queued_ids, state):
    """Load metadata only for the next queued episode, never the whole library."""
    episode_id = int(entry["episode_id"])
    if episode_id in queued_ids:
        return None
    hist = state.get("episodes", {}).get(str(episode_id), {})
    cycles = int(hist.get("search_cycles", 0))
    last_search = hist.get("last_search")
    if cycles >= 2 or (cycles == 1 and last_search and age_days(last_search) < 180):
        return None
''',
'''def item_from_queue_entry(entry, queued_ids, state, ignore_search_history=False):
    """Load metadata only for the next queued episode, never the whole library."""
    episode_id = int(entry["episode_id"])
    if episode_id in queued_ids:
        return None
    hist = state.get("episodes", {}).get(str(episode_id), {})
    cycles = int(hist.get("search_cycles", 0))
    if not ignore_search_history and (bool(hist.get("auto_processed")) or cycles >= 1):
        return None
''','son episode one-shot')
son=once(son,
'''    return item_from_queue_entry(entry, queued_ids, state)
''',
'''    return item_from_queue_entry(
        entry,
        queued_ids,
        state,
        ignore_search_history=MANUAL_TARGET_MODE
    )
''','son targeted episode bypass')
son=once(son,
'''            item = item_from_queue_entry(
                entry,
                queued_ids,
                state
            )
''',
'''            item = item_from_queue_entry(
                entry,
                queued_ids,
                state,
                ignore_search_history=MANUAL_TARGET_MODE
            )
''','son targeted series bypass')
son=once(son,
'''def next_work_items(state, queued_ids, limit):
    if not state.get("queue_initialized") or not state.get("series_queue"):
''',
'''def next_work_items(state, queued_ids, limit):
    reconcile_auto_processed_series(state)

    if not state.get("queue_initialized") or not state.get("series_queue"):
''','son reconcile work')
son=once(son,
'''            if LIVE:
                if not MANUAL_TARGET_MODE:
                    increment_search_count(state)
                    mark_episode_searched(state, episode_id)

                save_state(state)
''',
'''            if LIVE:
                if not MANUAL_TARGET_MODE:
                    increment_search_count(state)

                # A real successful /release search is one-shot forever for
                # automatic runs. Explicit Manual Optimizer retries bypass it.
                mark_episode_searched(state, episode_id)
                save_state(state)
''','son search mark')
son=once(son,
'''    print("=" * 68)
    print("SUMMARY")
''',
'''    if LIVE and MANUAL_TARGET_MODE and TARGET_SERIES_ID > 0 and searches > 0:
        # If the user manually handled a never-processed series first, do not
        # later surprise them with another automatic optimizer pass.
        mark_series_auto_processed(state, TARGET_SERIES_ID)
        save_state(state)

    print("=" * 68)
    print("SUMMARY")
''','son manual series close')

rad_path.write_text(rad,encoding='utf-8')
son_path.write_text(son,encoding='utf-8')
print('PATCH COMPLETE')
print('RADARR SHA256',sha(rad))
print('SONARR SHA256',sha(son))
