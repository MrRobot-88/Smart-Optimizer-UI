#!/usr/bin/env python3
import importlib.util
import os
import py_compile
import sys

if len(sys.argv) != 4:
    raise SystemExit(
        "usage: tracker_retention_fixture.py RADARR SONARR UI"
    )

rad_path,son_path,ui_path=sys.argv[1:4]

for path in (rad_path,son_path,ui_path):
    py_compile.compile(
        path,
        doraise=True,
    )

print("COMPILE PASS: Radarr + Sonarr + UI")

rad=open(rad_path,encoding="utf-8").read()
son=open(son_path,encoding="utf-8").read()
ui_text=open(ui_path,encoding="utf-8").read()

for text,label,needles in (
    (
        rad,
        "Radarr",
        [
            '"tracker_jobs": {}',
            "source_indexer",
            "torrentleech-movies",
            "tracker_policy_from_indexer",
            'state["tracker_jobs"][pending_key]["download_id"]',
            "auto_processed_movie_ids",
            "Automatic optimizer is strictly one-shot per movie forever.",
            "mark_movie_searched",
        ],
    ),
    (
        son,
        "Sonarr",
        [
            '"tracker_jobs": {}',
            "bind_grabbed_download",
            "queue_ids_for_episode",
            "torrentleech-tv",
            "tracker_policy_from_indexer",
            '"media_type": "sonarr"',
            "auto_processed_series_ids",
            "AUTO ONE-SHOT SERIES SKIP:",
            "mark_episode_searched",
        ],
    ),
    (
        ui_text,
        "UI",
        [
            "deluge_set_exact_label",
            "deluge_remove_exact_torrent",
            "_process_tracker_jobs",
            "sonarr_tracker_retention_worker",
            "remove_after_verified_success",
            "torrentleech-movies",
            "torrentleech-tv",
        ],
    ),
):
    for needle in needles:
        assert needle in text, (
            "%s missing %r"
            % (label,needle)
        )

print("SOURCE STRUCTURE PASS")
print("ONE-SHOT PRESERVATION PASS")

os.environ.setdefault(
    "RADARR_KEY",
    "fixture-not-a-real-key",
)
os.environ.setdefault(
    "SONARR_KEY",
    "fixture-not-a-real-key",
)

spec=importlib.util.spec_from_file_location(
    "tracker_retention_ui_fixture",
    ui_path,
)

ui=importlib.util.module_from_spec(spec)
spec.loader.exec_module(ui)

# ------------------------------------------------------------------
# Backfill classification: only tracker evidence may migrate old jobs.
# ------------------------------------------------------------------
assert (
    ui.tracker_policy_from_deluge_status(
        {
            "tracker":
                "https://tracker.torrentleech.org/announce",
            "trackers":[],
            "label":"movies",
        }
    )
    == "keep_seed"
)

assert (
    ui.tracker_policy_from_deluge_status(
        {
            "tracker":
                "udp://tracker.opentrackr.org:1337/announce",
            "trackers":[],
            "label":"movies",
        }
    )
    == "remove_after_verified_success"
)

assert (
    ui.tracker_policy_from_deluge_status(
        {}
    )
    == ""
)

print("POLICY CLASSIFICATION PASS")

calls={
    "labels":[],
    "removes":[],
}

torrent_status={}

def fake_status(download_id):
    return dict(
        torrent_status.get(
            str(download_id).upper(),
            {}
        )
    )

def fake_label(download_id,label):
    key=str(download_id).upper()
    calls["labels"].append(
        (key,label)
    )
    torrent_status.setdefault(
        key,
        {}
    )["label"]=label
    return True

def fake_remove(download_id):
    key=str(download_id).upper()
    calls["removes"].append(key)
    torrent_status.pop(
        key,
        None
    )
    return "removed"

ui.deluge_torrent_status=fake_status
ui.deluge_set_exact_label=fake_label
ui.deluge_remove_exact_torrent=fake_remove

# ------------------------------------------------------------------
# Radarr non-TL: NEVER delete while pending still exists.
# ------------------------------------------------------------------
rad_hash="A"*40

torrent_status[rad_hash]={
    "tracker":
        "udp://tracker.opentrackr.org:1337/announce",
    "trackers":[],
    "label":"movies",
}

ui._all_arr_history_records=lambda app:[
    {
        "eventType":"downloadFolderImported",
        "movieId":101,
        "downloadId":rad_hash,
        "data":{},
    }
] if app=="radarr" else []

state={
    "pending_replacements":{
        "101":{
            "movie_id":101,
            "download_id":rad_hash,
        }
    },
    "tracker_jobs":{
        "101":{
            "media_type":"radarr",
            "media_id":101,
            "download_id":rad_hash,
            "tracker_policy":
                "remove_after_verified_success",
        }
    },
}

changed=ui._process_tracker_jobs(
    "radarr",
    state,
)

assert rad_hash not in calls["removes"]
assert "101" in state["tracker_jobs"]

print(
    "RADARR SAFETY PASS: pending media transaction blocks torrent removal"
)

# Once Radarr pending is gone, exact verified import may clean exact hash.
state["pending_replacements"]={}

changed=ui._process_tracker_jobs(
    "radarr",
    state,
)

assert changed is True
assert calls["removes"] == [rad_hash]
assert "101" not in state["tracker_jobs"]

print(
    "RADARR CLEANUP PASS: exact non-TL hash removed only after verified success"
)

# ------------------------------------------------------------------
# TorrentLeech: label exact hash; never remove; keep seeding.
# ------------------------------------------------------------------
tl_hash="B"*40

torrent_status[tl_hash]={
    "tracker":
        "https://tracker.torrentleech.org/announce",
    "trackers":[],
    "label":"movies",
}

ui._all_arr_history_records=lambda app:[
    {
        "eventType":"downloadFolderImported",
        "movieId":202,
        "downloadId":tl_hash,
        "data":{},
    }
] if app=="radarr" else []

tl_state={
    "pending_replacements":{},
    "tracker_jobs":{
        "202":{
            "media_type":"radarr",
            "media_id":202,
            "download_id":tl_hash,
            "tracker_policy":"keep_seed",
            "desired_label":
                "torrentleech-movies",
        }
    },
}

changed=ui._process_tracker_jobs(
    "radarr",
    tl_state,
)

assert changed is True
assert (
    tl_hash,
    "torrentleech-movies",
) in calls["labels"]

assert tl_hash not in calls["removes"]
assert tl_hash in torrent_status
assert "202" not in tl_state["tracker_jobs"]

print(
    "TORRENTLEECH PASS: exact hash labeled and retained for seeding"
)

# ------------------------------------------------------------------
# Sonarr: verified exact import + current episode file -> non-TL cleanup.
# ------------------------------------------------------------------
son_hash="C"*40

torrent_status[son_hash]={
    "tracker":
        "udp://tracker.example.invalid/announce",
    "trackers":[],
    "label":"tv-sonarr",
}

ui._all_arr_history_records=lambda app:[
    {
        "eventType":"downloadFolderImported",
        "episodeId":303,
        "downloadId":son_hash,
        "data":{},
    }
] if app=="sonarr" else []

ui.sonarr_get=lambda path:(
    {
        "id":303,
        "hasFile":True,
        "episodeFileId":9001,
    }
    if path=="/episode/303"
    else {}
)

son_state={
    "tracker_jobs":{
        "303":{
            "media_type":"sonarr",
            "media_id":303,
            "download_id":son_hash,
            "tracker_policy":
                "remove_after_verified_success",
        }
    },
}

changed=ui._process_tracker_jobs(
    "sonarr",
    son_state,
)

assert changed is True
assert son_hash in calls["removes"]
assert "303" not in son_state["tracker_jobs"]

print(
    "SONARR CLEANUP PASS: exact non-TL hash removed after verified import"
)

print(
    "ALL TRACKER RETENTION FIXTURES PASSED"
)
