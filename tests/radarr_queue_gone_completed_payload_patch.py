#!/usr/bin/env python3
import hashlib
import sys
from pathlib import Path

if len(sys.argv)!=2:
    raise SystemExit("usage: radarr_queue_gone_completed_payload_patch.py UI")

ui_path=Path(sys.argv[1])
ui=ui_path.read_text(encoding="utf-8")

EXPECTED="28d014e0115334c43dff0801b3dd771c1919301e5be1d6468d6b3e7d1df8b41d"
actual=hashlib.sha256(ui.encode("utf-8")).hexdigest()
if actual!=EXPECTED:
    raise SystemExit("STOP: UI hash mismatch: "+actual)

old='''                            if len(matches) != 1:
                                continue

                            row = matches[0]
'''

new='''                            if len(matches) > 1:
                                print(
                                    "[radarr-import] exact ownership ambiguous; "
                                    "refusing:",
                                    movie_id,
                                    "count",
                                    len(matches)
                                )
                                continue

                            if len(matches) == 1:
                                row = matches[0]
                            else:
                                # Radarr may drop a completed download from its
                                # queue after rejecting the native automatic
                                # import ("not a quality revision upgrade").
                                #
                                # The exact Smart Optimizer transaction must not
                                # be abandoned in that case. Recover ONLY when:
                                #   * ownership is already bound to one exact hash,
                                #   * that exact Deluge torrent still exists,
                                #   * Deluge reports the payload 100% complete,
                                #   * positive downloaded bytes exist,
                                #   * Radarr can still resolve exactly one safe
                                #     ManualImport candidate by that exact hash.
                                #
                                # No title guessing and no alternate torrent is
                                # ever accepted here.
                                if not bound_download_id:
                                    continue

                                health = deluge_torrent_status(
                                    bound_download_id
                                )

                                try:
                                    recovered_progress = float(
                                        health.get("progress") or 0
                                    )
                                except (TypeError, ValueError):
                                    recovered_progress = 0.0

                                try:
                                    recovered_done = int(
                                        health.get("total_done") or 0
                                    )
                                except (TypeError, ValueError):
                                    recovered_done = 0

                                if (
                                    not health
                                    or recovered_progress < 100.0
                                    or recovered_done <= 0
                                ):
                                    continue

                                recovered_items = radarr_get(
                                    "/manualimport?downloadId=%s&movieId=%d"
                                    "&filterExistingFiles=false"
                                    % (
                                        urllib.parse.quote(
                                            bound_download_id
                                        ),
                                        movie_id
                                    )
                                )

                                recovered_usable = []
                                recovered_allowed_rejections = (
                                    "existing file meets cutoff",
                                    "not an upgrade for existing movie file",
                                    "quality for existing file on disk is of equal or higher preference",
                                )

                                for recovered_item in (
                                    recovered_items
                                    if isinstance(recovered_items, list)
                                    else []
                                ):
                                    recovered_bad = []

                                    for rejection in (
                                        recovered_item.get("rejections")
                                        or []
                                    ):
                                        reason = str(
                                            (
                                                rejection.get("reason")
                                                or rejection.get("message")
                                                or ""
                                            )
                                            if isinstance(
                                                rejection,
                                                dict
                                            )
                                            else rejection
                                        ).lower()

                                        if not any(
                                            allowed in reason
                                            for allowed in
                                            recovered_allowed_rejections
                                        ):
                                            recovered_bad.append(
                                                rejection
                                            )

                                    if (
                                        not recovered_bad
                                        and recovered_item.get("path")
                                    ):
                                        recovered_usable.append(
                                            recovered_item
                                        )

                                if len(recovered_usable) != 1:
                                    print(
                                        "[radarr-import] queue gone; exact "
                                        "completed payload found but safe "
                                        "ManualImport candidate count:",
                                        len(recovered_usable),
                                        "movie:",
                                        movie_id
                                    )
                                    continue

                                recovered_item = recovered_usable[0]

                                try:
                                    recovered_size = int(
                                        recovered_item.get("size")
                                        or approved_size
                                        or 0
                                    )
                                except (TypeError, ValueError):
                                    recovered_size = 0

                                if recovered_size <= 0:
                                    continue

                                recovered_queue_id = int(
                                    txn.get("queue_id") or 0
                                )

                                row = {
                                    "id": recovered_queue_id,
                                    "movieId": movie_id,
                                    "downloadId": bound_download_id,
                                    "status": "completed",
                                    "trackedDownloadState": "importPending",
                                    "sizeleft": 0,
                                    "size": recovered_size,
                                    "title": approved_title,
                                    "languages": (
                                        recovered_item.get("languages")
                                        or []
                                    ),
                                }

                                print(
                                    "[radarr-import] RECOVERED completed exact "
                                    "Deluge payload after Radarr queue vanished:",
                                    movie_id,
                                    bound_download_id,
                                    "%.2f GiB"
                                    % (
                                        recovered_size
                                        / 1073741824
                                    )
                                )
'''

if ui.count(old)!=1:
    raise SystemExit(
        "STOP: queue-match anchor count=%d"
        % ui.count(old)
    )

ui=ui.replace(old,new,1)

old_size='''                            new_size = int(
                                row.get("size")
                                or approved_size
                                or 0
                            )
'''

new_size='''                            # Prefer the ManualImport candidate's actual
                            # filesystem size over an indexer/queue estimate.
                            new_size = int(
                                item.get("size")
                                or row.get("size")
                                or approved_size
                                or 0
                            )
'''

if ui.count(old_size)!=1:
    raise SystemExit(
        "STOP: actual-size anchor count=%d"
        % ui.count(old_size)
    )

ui=ui.replace(old_size,new_size,1)

ui_path.write_text(ui,encoding="utf-8")

print("QUEUE-GONE COMPLETED-PAYLOAD PATCH COMPLETE")
print(
    "UI SHA256",
    hashlib.sha256(ui.encode("utf-8")).hexdigest()
)
