#!/usr/bin/env python3
import hashlib
import re
import sys
from pathlib import Path

if len(sys.argv) != 2:
    raise SystemExit(
        "usage: tracker_retention_deluge_status_fix.py UI_PATH"
    )

path=Path(sys.argv[1])
text=path.read_text(encoding="utf-8")

EXPECTED="84232b6b6fecaad66b9b77f73e735b58066e5514e11e09ee012d11d69cabdaab"

def sha(s):
    return hashlib.sha256(
        s.encode("utf-8")
    ).hexdigest()

actual=sha(text)

if actual != EXPECTED:
    raise SystemExit(
        "STOP: UI source hash mismatch: "+actual
    )

replacement=r'''def deluge_torrent_status(download_id):
    """
    Read one exact torrent by hash.

    Deluge's single-torrent RPC can reject plugin-provided status fields such
    as Label. The all-torrents status RPC is already proven on this NAS and
    returns the same data keyed by exact infohash, including Label fields.
    """
    wanted = str(
        download_id or ""
    ).strip().lower()

    if not wanted:
        return {}

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

    try:
        result = _deluge_rpc(
            "core.get_torrents_status",
            [
                {},
                fields,
            ],
        )
    except Exception:
        # Fail over without the optional Label status key. Tracker identity
        # still remains available and no destructive action is guessed.
        result = _deluge_rpc(
            "core.get_torrents_status",
            [
                {},
                [
                    x
                    for x in fields
                    if x != "label"
                ],
            ],
        )

    if not isinstance(result, dict):
        return {}

    for torrent_hash, status in result.items():
        if str(
            torrent_hash or ""
        ).strip().lower() == wanted:
            return (
                status
                if isinstance(status, dict)
                else {}
            )

    return {}


'''

new,count=re.subn(
    r'''def deluge_torrent_status\(download_id\):.*?(?=def deluge_torrent_is_dead\(status\):)''',
    replacement,
    text,
    count=1,
    flags=re.S,
)

if count != 1:
    raise SystemExit(
        "STOP: expected exactly one deluge_torrent_status function, found %d"
        % count
    )

path.write_text(
    new,
    encoding="utf-8"
)

print("DELUGE STATUS FIX PATCHED")
print("UI SHA256",sha(new))
