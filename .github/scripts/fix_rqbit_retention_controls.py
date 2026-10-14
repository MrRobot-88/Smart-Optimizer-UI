from pathlib import Path

p = Path('smart-optimizer-ui.py')
s = p.read_text(encoding='utf-8')

# Preserve explicit zero-hour/day retention instead of falling back to 11 days.
s = s.replace(
    '            "retention_value": float(rqbit.get("retention_value") or 11),\n'
    '            "retention_unit": str(rqbit.get("retention_unit") or "days").strip().lower(),\n'
    '            "retention_seconds": int(rqbit.get("retention_seconds") or (11 * 24 * 60 * 60)),\n',
    '            "retention_value": float(11 if rqbit.get("retention_value") is None else rqbit.get("retention_value")),\n'
    '            "retention_unit": str(rqbit.get("retention_unit") or "days").strip().lower(),\n'
    '            "retention_seconds": int((11 * 24 * 60 * 60) if rqbit.get("retention_seconds") is None else rqbit.get("retention_seconds")),\n',
    1,
)

# Avoid rewriting rqbit metadata JSON on every worker pass when policy already matches.
old = '''def rqbit_apply_job_policy(app, policy, download_id):
    settings = rqbit_policy_settings(app, policy)
    rqbit_set_exact_label(download_id, settings["label"])
    rqbit_set_exact_retention(
        download_id,
        settings["seconds"],
        settings["delete_after_verified_import"],
    )
    return settings
'''
new = '''def rqbit_apply_job_policy(app, policy, download_id):
    settings = rqbit_policy_settings(app, policy)
    status = rqbit_torrent_status(download_id) or {}

    if str(status.get("label") or "") != settings["label"]:
        rqbit_set_exact_label(download_id, settings["label"])
        # Setting a known native label may apply rqbit's built-in default
        # policy, so re-read before checking our user-selected retention.
        status = rqbit_torrent_status(download_id) or {}

    if (
        int(status.get("min_seed_seconds") or 0) != int(settings["seconds"])
        or bool(status.get("delete_after_verified_import"))
        != bool(settings["delete_after_verified_import"])
    ):
        rqbit_set_exact_retention(
            download_id,
            settings["seconds"],
            settings["delete_after_verified_import"],
        )

    return settings
'''
if old not in s:
    raise SystemExit('rqbit apply policy anchor missing')
s = s.replace(old, new, 1)

# Restore a real function docstring, then apply the Deluge-only backfill guard.
old = '''def _ensure_radarr_tracker_jobs(
    state,
):
    if download_client_settings()["selected"] != "deluge":
        return False

    """
    Backfill old pending Radarr grabs created before tracker metadata existed.

    Ownership still comes exclusively from the exact persisted download hash.
    """
    changed = False
'''
new = '''def _ensure_radarr_tracker_jobs(
    state,
):
    """
    Backfill old pending Radarr grabs created before tracker metadata existed.

    Ownership still comes exclusively from the exact persisted download hash.
    """
    if download_client_settings()["selected"] != "deluge":
        return False

    changed = False
'''
if old not in s:
    raise SystemExit('backfill docstring anchor missing')
s = s.replace(old, new, 1)

p.write_text(s, encoding='utf-8')
