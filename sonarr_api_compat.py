#!/usr/bin/env python3

import json
import os
import re
import threading
import urllib.request

_CACHE = {}
_LOCK = threading.Lock()


def _major(value):
    text = str(value or "").strip().lower()
    match = re.search(r"(\d+)", text)
    return int(match.group(1)) if match else None


def _get_json(url, api_key, timeout=8):
    req = urllib.request.Request(
        url,
        headers={
            "X-Api-Key": str(api_key or "").strip(),
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8") or "{}")


def sonarr_api_prefix(base_url, api_key, mode=None):
    """Return the best Sonarr API prefix for this server.

    Modes:
      auto (default): detect v5, otherwise fall back to v3
      v5 / 5: force /api/v5
      v3 / 3 / compat: force /api/v3
    """
    base_url = str(base_url or "").rstrip("/")
    api_key = str(api_key or "").strip()
    selected = str(
        mode
        or os.environ.get("SONARR_API_MODE", "auto")
        or "auto"
    ).strip().lower()

    if selected in ("v5", "5"):
        return "/api/v5"
    if selected in ("v3", "3", "compat", "legacy"):
        return "/api/v3"

    cache_key = (base_url, api_key)
    with _LOCK:
        cached = _CACHE.get(cache_key)
    if cached:
        return cached

    prefix = "/api/v3"

    # Sonarr v5 exposes GET /api with its current API generation.
    try:
        info = _get_json(base_url + "/api", api_key)
        current_major = _major(info.get("current"))
        if current_major is not None:
            prefix = "/api/v5" if current_major >= 5 else "/api/v3"
        else:
            deprecated = info.get("deprecated") or []
            deprecated_majors = {
                _major(item)
                for item in deprecated
                if _major(item) is not None
            }
            if 3 in deprecated_majors:
                prefix = "/api/v5"
    except Exception:
        pass

    # Some builds do not populate `current`; probe the v5 status endpoint.
    if prefix == "/api/v3":
        try:
            _get_json(base_url + "/api/v5/system/status", api_key)
            prefix = "/api/v5"
        except Exception:
            prefix = "/api/v3"

    with _LOCK:
        _CACHE[cache_key] = prefix

    return prefix


def clear_sonarr_api_cache():
    with _LOCK:
        _CACHE.clear()
