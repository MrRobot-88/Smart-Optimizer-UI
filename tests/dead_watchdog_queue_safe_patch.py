#!/usr/bin/env python3
import hashlib
import re
import sys
from pathlib import Path

if len(sys.argv) != 2:
    raise SystemExit("usage: dead_watchdog_queue_safe_patch.py UI_PATH")

path=Path(sys.argv[1])
text=path.read_text(encoding="utf-8")

EXPECTED="efa39ed73754fd7bc95e3e031ff6a9061d60142e32140bb27a9de4ea941dc4dc"

def sha(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

actual=sha(text)
if actual != EXPECTED:
    raise SystemExit("STOP: UI source hash mismatch: "+actual)

old_pattern=r'''def _dead_watchdog_is_strict_dead\(status, minimum_age=300\):.*?(?=def _dead_watchdog_optimizer_hashes\(\):)'''

replacement=r'''# Continuous-zero timers are intentionally in-memory. A UI restart resets
# them, which is safer than deleting a torrent too early after a restart.
_dead_watchdog_zero_since = {}


def _dead_watchdog_is_strict_dead(
    status,
    torrent_hash=None,
    minimum_age=300,
):
    """
    True only after a torrent has spent minimum_age seconds continuously in
    Deluge's Downloading state with literally zero transfer/activity evidence.

    Paused/Queued torrents NEVER accumulate this timer. Any data, peers, seeds,
    or download rate resets it. This makes Deluge active-download limits safe.
    """
    key = str(torrent_hash or "").strip().upper()

    if not isinstance(status, dict) or not status:
        if key:
            _dead_watchdog_zero_since.pop(key, None)
        return False

    try:
        state = str(status.get("state") or "").strip().lower()

        zero_now = (
            state == "downloading"
            and float(status.get("progress") or 0) <= 0
            and int(status.get("total_done") or 0) <= 0
            and int(status.get("num_seeds") or 0) <= 0
            and int(status.get("num_peers") or 0) <= 0
            and int(status.get("download_payload_rate") or 0) <= 0
        )
    except (TypeError, ValueError):
        zero_now = False

    if not zero_now:
        if key:
            _dead_watchdog_zero_since.pop(key, None)
        return False

    # The production watchdog always supplies an exact infohash. Fail closed
    # if no identity is available rather than sharing a timer accidentally.
    if not key:
        return False

    now = time.time()
    started = _dead_watchdog_zero_since.setdefault(key, now)

    return (now - started) >= float(minimum_age)


'''

new,count=re.subn(
    old_pattern,
    replacement,
    text,
    count=1,
    flags=re.S,
)

if count != 1:
    raise SystemExit(
        "STOP: strict-dead function replacement count=%d" % count
    )

# Every production call must pass the exact torrent hash so timers are
# per-download, not shared.
new=new.replace(
    '''if not _dead_watchdog_is_strict_dead(status):
            continue
''',
    '''if not _dead_watchdog_is_strict_dead(
            status,
            download_id,
        ):
            continue
''',
    1,
)

new=new.replace(
    '''if not _dead_watchdog_is_strict_dead(status):
            continue

        result["strict_dead"] += 1
        download_id = str(torrent_hash or "").strip().upper()
''',
    '''download_id = str(torrent_hash or "").strip().upper()

        if not _dead_watchdog_is_strict_dead(
            status,
            download_id,
        ):
            continue

        result["strict_dead"] += 1
''',
    1,
)

new=new.replace(
    '''if not _dead_watchdog_is_strict_dead(status):
                        continue

                    media_id, exact_hash = _dead_watchdog_remove_exact_queue(
''',
    '''if not _dead_watchdog_is_strict_dead(
                        status,
                        download_id,
                    ):
                        continue

                    media_id, exact_hash = _dead_watchdog_remove_exact_queue(
''',
    1,
)

# Guard against a partial patch: all three worker/snapshot call sites should
# now pass identity, plus the function definition itself.
if new.count("_dead_watchdog_is_strict_dead(") != 4:
    raise SystemExit(
        "STOP: unexpected strict-dead call count after patch: %d"
        % new.count("_dead_watchdog_is_strict_dead(")
    )

path.write_text(new,encoding="utf-8")

print("QUEUE-SAFE WATCHDOG PATCH COMPLETE")
print("UI SHA256",sha(new))
