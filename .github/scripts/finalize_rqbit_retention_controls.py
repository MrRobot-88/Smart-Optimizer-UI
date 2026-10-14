from pathlib import Path

p = Path('smart-optimizer-ui.py')
s = p.read_text(encoding='utf-8')

# Keep the Deluge watchdog thread alive so switching back from rqbit to Deluge
# in Settings works without restarting Smart Optimizer.
old = '''    if download_client_settings()["selected"] != "deluge":
        print("[dead-watchdog] disabled while rqbit is selected", flush=True)
        return

    while True:
        try:
            torrents = _dead_watchdog_deluge_statuses()
'''
new = '''    rqbit_notice_shown = False

    while True:
        if download_client_settings()["selected"] != "deluge":
            if not rqbit_notice_shown:
                print("[dead-watchdog] paused while rqbit is selected", flush=True)
                rqbit_notice_shown = True
            time.sleep(15)
            continue

        rqbit_notice_shown = False

        try:
            torrents = _dead_watchdog_deluge_statuses()
'''
if old not in s:
    raise SystemExit('watchdog rqbit guard anchor missing')
s = s.replace(old, new, 1)

# Only persist tracker state when rqbit metadata reflected in the state changed.
old = '''                settings = rqbit_apply_job_policy(app, policy, download_id)
                desired = settings["label"]
                if str(job.get("desired_label") or "") != desired:
                    job["desired_label"] = desired
                    changed = True
                job["rqbit_retention_seconds"] = int(settings["seconds"])
                job["rqbit_retention_enabled"] = bool(
                    settings["delete_after_verified_import"]
                )
                job["labeled_at"] = int(job.get("labeled_at") or time.time())
                job.pop("last_error", None)
                changed = True
'''
new = '''                settings = rqbit_apply_job_policy(app, policy, download_id)
                desired = settings["label"]
                if str(job.get("desired_label") or "") != desired:
                    job["desired_label"] = desired
                    changed = True

                wanted_seconds = int(settings["seconds"])
                if int(job.get("rqbit_retention_seconds") or 0) != wanted_seconds:
                    job["rqbit_retention_seconds"] = wanted_seconds
                    changed = True

                wanted_cleanup = bool(settings["delete_after_verified_import"])
                if job.get("rqbit_retention_enabled") is not wanted_cleanup:
                    job["rqbit_retention_enabled"] = wanted_cleanup
                    changed = True

                if not job.get("labeled_at"):
                    job["labeled_at"] = int(time.time())
                    changed = True

                if "last_error" in job:
                    job.pop("last_error", None)
                    changed = True
'''
if old not in s:
    raise SystemExit('rqbit tracker state anchor missing')
s = s.replace(old, new, 1)

p.write_text(s, encoding='utf-8')
