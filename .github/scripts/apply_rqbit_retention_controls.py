from pathlib import Path

p = Path('smart-optimizer-ui.py')
s = p.read_text(encoding='utf-8')

# 1) Extend rqbit settings and preserve extension fields on save.
old = '''        "rqbit": {
            "scheme": str(rqbit.get("scheme") or "http").lower(),
            "host": str(rqbit.get("host") or "127.0.0.1").strip(),
            "port": int(rqbit.get("port") or 3030),
        },
'''
new = '''        "rqbit": {
            "scheme": str(rqbit.get("scheme") or "http").lower(),
            "host": str(rqbit.get("host") or "127.0.0.1").strip(),
            "port": int(rqbit.get("port") or 3030),
            "movie_label": str(rqbit.get("movie_label") or "torrentleech-movies").strip(),
            "tv_label": str(rqbit.get("tv_label") or "torrentleech-tv").strip(),
            "other_movie_label": str(rqbit.get("other_movie_label") or "other-indexer-movies").strip(),
            "other_tv_label": str(rqbit.get("other_tv_label") or "other-indexer-tv").strip(),
            "retention_enabled": bool(rqbit.get("retention_enabled", True)),
            "retention_value": float(rqbit.get("retention_value") or 11),
            "retention_unit": str(rqbit.get("retention_unit") or "days").strip().lower(),
            "retention_seconds": int(rqbit.get("retention_seconds") or (11 * 24 * 60 * 60)),
        },
'''
if old not in s:
    raise SystemExit('rqbit settings anchor missing')
s = s.replace(old, new, 1)

start = s.index('def update_download_client_settings(')
end = s.index('\n\ndef test_download_client(', start)
replacement = '''def update_download_client_settings(
    selected,
    deluge_scheme,
    deluge_host,
    deluge_port,
    deluge_password,
    rqbit_scheme,
    rqbit_host,
    rqbit_port,
    rqbit_movie_label=None,
    rqbit_tv_label=None,
    rqbit_other_movie_label=None,
    rqbit_other_tv_label=None,
    rqbit_retention_enabled=True,
    rqbit_retention_value=11,
    rqbit_retention_unit="days",
):
    selected = str(selected or "").strip().lower()
    if selected not in ("deluge", "rqbit"):
        raise ValueError("Choose Deluge or rqbit")

    d_scheme, d_host, d_port = _validate_download_client_endpoint(
        deluge_scheme, deluge_host, deluge_port
    )
    r_scheme, r_host, r_port = _validate_download_client_endpoint(
        rqbit_scheme, rqbit_host, rqbit_port
    )

    current = download_client_settings()
    password = str(deluge_password or "").strip() or current["deluge"]["password"]

    movie_label = str(rqbit_movie_label or current["rqbit"]["movie_label"]).strip()
    tv_label = str(rqbit_tv_label or current["rqbit"]["tv_label"]).strip()
    other_movie_label = str(
        rqbit_other_movie_label or current["rqbit"]["other_movie_label"]
    ).strip()
    other_tv_label = str(
        rqbit_other_tv_label or current["rqbit"]["other_tv_label"]
    ).strip()

    for label in (movie_label, tv_label, other_movie_label, other_tv_label):
        if not label:
            raise ValueError("rqbit label names cannot be empty")
        if len(label) > 120:
            raise ValueError("rqbit label names must be 120 characters or shorter")

    unit = str(rqbit_retention_unit or "days").strip().lower()
    if unit not in ("hours", "days"):
        raise ValueError("rqbit retention unit must be hours or days")
    value = float(rqbit_retention_value)
    if value < 0:
        raise ValueError("rqbit retention time cannot be negative")
    multiplier = 3600 if unit == "hours" else 86400
    seconds = int(round(value * multiplier))
    if seconds > 10 * 365 * 86400:
        raise ValueError("rqbit retention time is too large")

    data = load_controls()
    data["download_client"] = {"selected": selected}

    deluge_data = data.setdefault("deluge", {})
    deluge_data.update({
        "scheme": d_scheme,
        "host": d_host,
        "port": d_port,
        "password": password,
    })

    rqbit_data = data.setdefault("rqbit", {})
    rqbit_data.update({
        "scheme": r_scheme,
        "host": r_host,
        "port": r_port,
        "movie_label": movie_label,
        "tv_label": tv_label,
        "other_movie_label": other_movie_label,
        "other_tv_label": other_tv_label,
        "retention_enabled": bool(rqbit_retention_enabled),
        "retention_value": value,
        "retention_unit": unit,
        "retention_seconds": seconds,
    })

    save_controls(data)
    return download_client_settings()
'''
s = s[:start] + replacement + s[end:]

# 2) Native rqbit smart-retention API helpers.
anchor = '\n\ndef deluge_connection():'
if anchor not in s:
    raise SystemExit('deluge helper anchor missing')
rqbit_helpers = r'''


def rqbit_connection():
    return download_client_settings()["rqbit"]


def rqbit_base_url():
    cfg = rqbit_connection()
    return "%s://%s:%d" % (cfg["scheme"], cfg["host"], cfg["port"])


def _rqbit_request(path, method="GET", payload=None):
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        rqbit_base_url() + path,
        data=data,
        method=method,
        headers=headers,
    )
    with urllib.request.urlopen(req, timeout=12) as response:
        raw = response.read().decode("utf-8")
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"raw": raw, "http_status": response.status}


def _rqbit_torrent_path(download_id, suffix):
    download_id = str(download_id or "").strip()
    if not download_id:
        raise RuntimeError("exact rqbit torrent hash is required")
    return "/torrents/%s/%s" % (
        urllib.parse.quote(download_id, safe=""),
        suffix.lstrip("/"),
    )


def rqbit_torrent_status(download_id):
    return _rqbit_request(_rqbit_torrent_path(download_id, "smart"))


def rqbit_set_exact_label(download_id, label):
    label = str(label or "").strip()
    if not label:
        raise RuntimeError("rqbit label is required")
    result = _rqbit_request(
        _rqbit_torrent_path(download_id, "label"),
        method="PUT",
        payload={"label": label},
    )
    check = rqbit_torrent_status(download_id)
    if str(check.get("label") or "") != label:
        raise RuntimeError("rqbit label verification failed")
    return result


def rqbit_set_exact_retention(
    download_id,
    min_seed_seconds,
    delete_after_verified_import=True,
):
    seconds = max(0, int(min_seed_seconds))
    result = _rqbit_request(
        _rqbit_torrent_path(download_id, "retention"),
        method="PUT",
        payload={
            "min_seed_seconds": seconds,
            "delete_after_verified_import": bool(delete_after_verified_import),
        },
    )
    check = rqbit_torrent_status(download_id)
    if int(check.get("min_seed_seconds") or 0) != seconds:
        raise RuntimeError("rqbit retention verification failed")
    if bool(check.get("delete_after_verified_import")) != bool(delete_after_verified_import):
        raise RuntimeError("rqbit cleanup-policy verification failed")
    return result


def rqbit_mark_verified_import(download_id):
    result = _rqbit_request(
        _rqbit_torrent_path(download_id, "verify_import"),
        method="POST",
    )
    check = rqbit_torrent_status(download_id)
    if not bool(check.get("verified_import")):
        raise RuntimeError("rqbit verified-import flag was not persisted")
    return result


def rqbit_policy_settings(app, policy):
    cfg = rqbit_connection()
    if app not in ("radarr", "sonarr"):
        raise ValueError("Unknown app")

    if policy == "keep_seed":
        label = cfg["movie_label"] if app == "radarr" else cfg["tv_label"]
        return {
            "label": label,
            "seconds": int(cfg["retention_seconds"]),
            "delete_after_verified_import": bool(cfg["retention_enabled"]),
        }

    label = cfg["other_movie_label"] if app == "radarr" else cfg["other_tv_label"]
    return {
        "label": label,
        "seconds": 0,
        "delete_after_verified_import": True,
    }


def rqbit_apply_job_policy(app, policy, download_id):
    settings = rqbit_policy_settings(app, policy)
    rqbit_set_exact_label(download_id, settings["label"])
    rqbit_set_exact_retention(
        download_id,
        settings["seconds"],
        settings["delete_after_verified_import"],
    )
    return settings
'''
s = s.replace(anchor, rqbit_helpers + anchor, 1)

# 3) Old Deluge-only backfill must not query Deluge when rqbit is active.
anchor = '''def _ensure_radarr_tracker_jobs(
    state,
):
'''
if anchor not in s:
    raise SystemExit('radarr tracker backfill anchor missing')
s = s.replace(anchor, anchor + '''    if download_client_settings()["selected"] != "deluge":
        return False

''', 1)

# 4) Replace tracker worker with client-aware retention routing.
start = s.index('def _process_tracker_jobs(\n')
end = s.index('\n\ndef update_connection(', start)
tracker_worker = r'''def _process_tracker_jobs(
    app,
    state,
):
    jobs = state.setdefault("tracker_jobs", {})
    changed = False
    client = download_client_settings()["selected"]

    if app == "radarr" and client == "deluge":
        if _ensure_radarr_tracker_jobs(state):
            changed = True

    pending = state.get("pending_replacements") or {}

    for key, job in list(jobs.items()):
        try:
            media_id = int(job.get("media_id") or key)
            download_id = str(job.get("download_id") or "").strip()

            if app == "sonarr" and not download_id:
                if _bind_sonarr_tracker_job(job):
                    changed = True
                    download_id = str(job.get("download_id") or "").strip()

            if not download_id:
                continue

            policy = str(job.get("tracker_policy") or "").strip()

            if client == "deluge":
                try:
                    status = deluge_torrent_status(download_id)
                except Exception as exc:
                    job["last_error"] = str(exc)
                    continue

                if not policy and status:
                    policy = tracker_policy_from_deluge_status(status)
                    if policy:
                        job["tracker_policy"] = policy
                        job["policy_source"] = "deluge_trackers"
                        changed = True

                if policy == "keep_seed":
                    desired = str(
                        job.get("desired_label")
                        or ("torrentleech-movies" if app == "radarr" else "torrentleech-tv")
                    )
                    if status and str(status.get("label") or "") != desired:
                        deluge_set_exact_label(download_id, desired)
                        job["labeled_at"] = int(time.time())
                        changed = True

            elif client == "rqbit":
                # New optimizer jobs already persist their tracker policy from
                # the exact selected Arr indexer. Unknown legacy jobs fail closed.
                if policy not in ("keep_seed", "remove_after_verified_success"):
                    job["last_error"] = "rqbit legacy job has no proven tracker policy"
                    continue

                settings = rqbit_apply_job_policy(app, policy, download_id)
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

            else:
                continue

            if not policy:
                continue

            imported = _tracker_import_proven(app, media_id, download_id)
            if not imported:
                continue

            if app == "radarr" and str(media_id) in pending:
                # Radarr import worker still owns final one-clean-movie proof.
                continue

            if app == "sonarr":
                episode = sonarr_get("/episode/%d" % media_id) or {}
                if not bool(episode.get("hasFile")):
                    continue

            if client == "rqbit":
                settings = rqbit_apply_job_policy(app, policy, download_id)
                rqbit_mark_verified_import(download_id)
                jobs.pop(key, None)
                changed = True
                print(
                    "[tracker-retention] RQBIT VERIFIED:",
                    app,
                    media_id,
                    download_id,
                    settings["label"],
                    settings["seconds"],
                    "cleanup" if settings["delete_after_verified_import"] else "retain",
                )
                continue

            if policy == "keep_seed":
                status = deluge_torrent_status(download_id)
                if not status:
                    job["retention_issue"] = (
                        "TorrentLeech torrent missing after verified import"
                    )
                    changed = True
                    continue

                desired = str(
                    job.get("desired_label")
                    or ("torrentleech-movies" if app == "radarr" else "torrentleech-tv")
                )
                if str(status.get("label") or "") != desired:
                    deluge_set_exact_label(download_id, desired)

                jobs.pop(key, None)
                changed = True
                print(
                    "[tracker-retention] KEEP SEEDING:",
                    app,
                    media_id,
                    download_id,
                    desired,
                )

            elif policy == "remove_after_verified_success":
                result = deluge_remove_exact_torrent(download_id)
                jobs.pop(key, None)
                changed = True
                print(
                    "[tracker-retention] CLEANED:",
                    app,
                    media_id,
                    download_id,
                    result,
                )

        except Exception as exc:
            job["last_error"] = str(exc)
            print(
                "[tracker-retention] job error %s/%s: %s"
                % (app, key, exc)
            )

    return changed
'''
s = s[:start] + tracker_worker + s[end:]

# 5) Deluge dead watchdog is disabled whenever rqbit owns downloads.
old = '''def dead_download_watchdog_worker():
    """
'''
if old not in s:
    raise SystemExit('dead watchdog function anchor missing')
# Insert guard at the first worker loop after the docstring terminates.
func_start = s.index('def dead_download_watchdog_worker():')
loop_pos = s.index('    while True:\n', func_start)
s = s[:loop_pos] + '''    if download_client_settings()["selected"] != "deluge":
        print("[dead-watchdog] disabled while rqbit is selected", flush=True)
        return

''' + s[loop_pos:]

# 6) Extend Settings card with labels + editable retention timer.
old = '''  <div class="cfg-row"><label class="cfg-label">rqbit port</label><input name="rqbit_port" type="number" min="1" max="65535" value="%d"></div>
  <div class="cfg-help">rqbit has no username/password in this setup.</div>
  <div class="cfg-row"><label class="cfg-label">Deluge protocol</label>'''
new = '''  <div class="cfg-row"><label class="cfg-label">rqbit port</label><input name="rqbit_port" type="number" min="1" max="65535" value="%d"></div>
  <div class="cfg-help">rqbit has no username/password in this setup.</div>
  <div class="cfg-row"><label class="cfg-label">rqbit movie label</label><input name="rqbit_movie_label" value="%s" required></div>
  <div class="cfg-row"><label class="cfg-label">rqbit TV label</label><input name="rqbit_tv_label" value="%s" required></div>
  <div class="cfg-row"><label class="cfg-label">Other-indexer movie label</label><input name="rqbit_other_movie_label" value="%s" required></div>
  <div class="cfg-row"><label class="cfg-label">Other-indexer TV label</label><input name="rqbit_other_tv_label" value="%s" required></div>
  <div class="cfg-row cfg-row-switch"><label class="cfg-label">rqbit auto cleanup after retention</label><label class="cfg-check"><input name="rqbit_retention_enabled" type="checkbox" value="1"%s><span>Enabled</span></label></div>
  <div class="cfg-row"><label class="cfg-label">rqbit retention time / ETA</label><input name="rqbit_retention_value" type="number" min="0" step="0.25" value="%s"><select name="rqbit_retention_unit"><option value="hours"%s>hours</option><option value="days"%s>days</option></select></div>
  <div class="cfg-help">The countdown starts when rqbit marks the torrent complete. Default is 11 days. Smart Optimizer writes the exact label and retention policy into rqbit for each managed torrent.</div>
  <div class="cfg-row"><label class="cfg-label">Deluge protocol</label>'''
if old not in s:
    raise SystemExit('settings rqbit card anchor missing')
s = s.replace(old, new, 1)

old = '''    html.escape(dcfg["rqbit"]["host"], quote=True),
    int(dcfg["rqbit"]["port"]),
    " selected" if dcfg["deluge"]["scheme"] == "http" else "",
'''
new = '''    html.escape(dcfg["rqbit"]["host"], quote=True),
    int(dcfg["rqbit"]["port"]),
    html.escape(dcfg["rqbit"]["movie_label"], quote=True),
    html.escape(dcfg["rqbit"]["tv_label"], quote=True),
    html.escape(dcfg["rqbit"]["other_movie_label"], quote=True),
    html.escape(dcfg["rqbit"]["other_tv_label"], quote=True),
    " checked" if dcfg["rqbit"]["retention_enabled"] else "",
    ("%g" % float(dcfg["rqbit"]["retention_value"])),
    " selected" if dcfg["rqbit"]["retention_unit"] == "hours" else "",
    " selected" if dcfg["rqbit"]["retention_unit"] == "days" else "",
    " selected" if dcfg["deluge"]["scheme"] == "http" else "",
'''
if old not in s:
    raise SystemExit('settings format args anchor missing')
s = s.replace(old, new, 1)

# 7) Parse and save new rqbit Settings fields.
old = '''            r_scheme = (form.get("rqbit_scheme") or ["http"])[0]
            r_host = (form.get("rqbit_host") or ["127.0.0.1"])[0]
            r_port = (form.get("rqbit_port") or ["3030"])[0]
'''
new = '''            r_scheme = (form.get("rqbit_scheme") or ["http"])[0]
            r_host = (form.get("rqbit_host") or ["127.0.0.1"])[0]
            r_port = (form.get("rqbit_port") or ["3030"])[0]
            r_movie_label = (form.get("rqbit_movie_label") or ["torrentleech-movies"])[0]
            r_tv_label = (form.get("rqbit_tv_label") or ["torrentleech-tv"])[0]
            r_other_movie_label = (form.get("rqbit_other_movie_label") or ["other-indexer-movies"])[0]
            r_other_tv_label = (form.get("rqbit_other_tv_label") or ["other-indexer-tv"])[0]
            r_retention_enabled = (form.get("rqbit_retention_enabled") or [""])[0] == "1"
            r_retention_value = (form.get("rqbit_retention_value") or ["11"])[0]
            r_retention_unit = (form.get("rqbit_retention_unit") or ["days"])[0]
'''
if old not in s:
    raise SystemExit('handler parse anchor missing')
s = s.replace(old, new, 1)

old = '''                    update_download_client_settings(selected, d_scheme, d_host, d_port, d_password, r_scheme, r_host, r_port)
                    results, errors = apply_download_client_selection(selected)
'''
new = '''                    update_download_client_settings(
                        selected,
                        d_scheme,
                        d_host,
                        d_port,
                        d_password,
                        r_scheme,
                        r_host,
                        r_port,
                        r_movie_label,
                        r_tv_label,
                        r_other_movie_label,
                        r_other_tv_label,
                        r_retention_enabled,
                        r_retention_value,
                        r_retention_unit,
                    )
                    results, errors = apply_download_client_selection(selected)
'''
if old not in s:
    raise SystemExit('handler save anchor missing')
s = s.replace(old, new, 1)

p.write_text(s, encoding='utf-8')
