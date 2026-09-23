#!/usr/bin/env python3
from pathlib import Path

FILES = {
    "RADARR": Path("/volume1/WDBLACK/radarr-smart-optimizer.py"),
    "SONARR": Path("/volume1/WDBLACK/sonarr-smart-optimizer.py"),
    "UI": Path("/volume1/WDBLACK/ContainerConfigs/Smart-Optimizer-UI/smart-optimizer-ui.py"),
}

PATTERNS = {
    "RADARR": [
        "pending_replacements",
        "download_id",
        "queue_id",
        "approved_title",
        "approved_release",
        "old_file_id",
        "old_relative_path",
        "bind_grabbed_download",
        "mark_movie_auto_processed",
        "auto_processed_movie_ids",
        "interactive_search",
        "release/",
    ],
    "SONARR": [
        "pending_replacements",
        "download_id",
        "queue_id",
        "approved_title",
        "approved_release",
        "bind_grabbed_download",
        "auto_processed_series_ids",
        "mark_series_auto_processed",
        "interactive_search",
        "release/",
        "grab",
    ],
    "UI": [
        "deluge_torrent_status",
        "downloadFolderImported",
        "pending_replacements",
        "radarr_self_heal_owned_old_copy",
        "verified_new_file_id",
        "verified_new_size",
        "pending.pop",
        "del pending",
        "optimizer_import_worker",
    ],
}

WINDOW = 18

for label, path in FILES.items():
    print()
    print("=" * 78)
    print(label, path)
    print("=" * 78)

    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    hits = set()

    for i, line in enumerate(lines):
        low = line.lower()

        if any(p.lower() in low for p in PATTERNS[label]):
            start = max(0, i - WINDOW)
            end = min(len(lines), i + WINDOW + 1)

            # Expand to nearby def/class boundary when practical.
            for j in range(i, max(-1, i - 80), -1):
                stripped = lines[j].lstrip()
                if stripped.startswith("def ") or stripped.startswith("class "):
                    start = min(start, j)
                    break

            for n in range(start, end):
                hits.add(n)

    if not hits:
        print("NO MATCHES")
        continue

    last = None

    for n in sorted(hits):
        if last is not None and n > last + 1:
            print("...")
        line = lines[n]

        # Never dump likely literal secrets even if one somehow entered source.
        low = line.lower()
        if (
            ("password" in low or "api_key" in low or "token" in low)
            and ("=" in line)
            and ('"' in line or "'" in line)
        ):
            # Keep structural lines, redact only literal RHS-ish content.
            head = line.split("=", 1)[0]
            line = head + "= <redacted>"

        print("%5d: %s" % (n + 1, line))
        last = n

print()
print("=" * 78)
print("TRACKER RETENTION CONTEXT DUMP COMPLETE")
print("Nothing modified")
print("=" * 78)
