from pathlib import Path

p = Path('smart-optimizer-ui.py')
s = p.read_text(encoding='utf-8')

# 1) BOM-safe control-file reader.
start = s.index('def load_controls():')
end = s.index('\n\ndef save_controls(', start)
block = s[start:end]
if 'encoding="utf-8"' not in block:
    raise SystemExit('load_controls encoding anchor missing')
block = block.replace('encoding="utf-8"', 'encoding="utf-8-sig"', 1)
s = s[:start] + block + s[end:]

# 2) Backward-compatible downloader settings loader.
start = s.index('def download_client_settings():')
end = s.index('\n\ndef _validate_download_client_endpoint(', start)
replacement = '''def download_client_settings():
    data = load_controls()

    raw_selected = data.get("download_client")
    selected = ""

    if isinstance(raw_selected, dict):
        selected = str(raw_selected.get("selected") or "").strip().lower()
    elif isinstance(raw_selected, str):
        selected = raw_selected.strip().lower()

    if selected not in ("deluge", "rqbit"):
        legacy_downloader = data.get("downloader") or {}
        if isinstance(legacy_downloader, dict):
            selected = str(legacy_downloader.get("selected") or "").strip().lower()

    if selected not in ("deluge", "rqbit"):
        selected = "rqbit"

    deluge = data.get("deluge", {}) or {}
    if not isinstance(deluge, dict):
        deluge = {}

    rqbit = data.get("rqbit", {}) or {}
    if not isinstance(rqbit, dict):
        rqbit = {}

    return {
        "selected": selected,
        "deluge": {
            "scheme": str(deluge.get("scheme") or "http").lower(),
            "host": str(deluge.get("host") or "127.0.0.1").strip(),
            "port": int(deluge.get("port") or 8112),
            "password": str(deluge.get("password") or ""),
        },
        "rqbit": {
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
    }
'''
s = s[:start] + replacement + s[end:]

# 3) Save both the historic scalar key and the legacy downloader mirror.
start = s.index('def update_download_client_settings(')
end = s.index('\n\ndef test_download_client(', start)
block = s[start:end]
old = '    data = load_controls()\n    data["download_client"] = {"selected": selected}\n'
new = '''    data = load_controls()\n    # Keep the long-lived scalar schema for backward compatibility.\n    data["download_client"] = selected\n    downloader_data = data.setdefault("downloader", {})\n    if not isinstance(downloader_data, dict):\n        downloader_data = {}\n        data["downloader"] = downloader_data\n    downloader_data["selected"] = selected\n    downloader_data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")\n'''
if old not in block:
    raise SystemExit('download-client save anchor missing')
block = block.replace(old, new, 1)
s = s[:start] + block + s[end:]

# 4) Persist the owning client on every tracker job as soon as the worker sees it.
start = s.index('def _process_tracker_jobs(\n')
end = s.index('\n\ndef update_connection(', start)
block = s[start:end]

old = '''    jobs = state.setdefault("tracker_jobs", {})\n    changed = False\n    client = download_client_settings()["selected"]\n\n    if app == "radarr" and client == "deluge":\n'''
new = '''    jobs = state.setdefault("tracker_jobs", {})\n    changed = False\n    selected_client = download_client_settings()["selected"]\n\n    if app == "radarr" and selected_client == "deluge":\n'''
if old not in block:
    raise SystemExit('tracker worker client anchor missing')
block = block.replace(old, new, 1)

old = '''            policy = str(job.get("tracker_policy") or "").strip()\n\n            if client == "deluge":\n'''
new = '''            policy = str(job.get("tracker_policy") or "").strip()\n\n            job_client = str(job.get("download_client") or "").strip().lower()\n            if job_client not in ("deluge", "rqbit"):\n                job_client = selected_client\n                job["download_client"] = job_client\n                changed = True\n\n            if job_client == "deluge":\n'''
if old not in block:
    raise SystemExit('tracker worker per-job anchor missing')
block = block.replace(old, new, 1)
block = block.replace('            elif client == "rqbit":', '            elif job_client == "rqbit":')
block = block.replace('            if client == "rqbit":', '            if job_client == "rqbit":')

s = s[:start] + block + s[end:]

p.write_text(s, encoding='utf-8')
print('config compatibility and per-job downloader ownership patch applied')
