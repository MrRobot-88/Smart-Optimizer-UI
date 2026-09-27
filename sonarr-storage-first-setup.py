#!/usr/bin/env python3
"""
Configure a Sonarr v4 quality profile for storage-first fresh 1080p downloads.

Why this exists
---------------
Sonarr normally compares quality rank before custom-format score, indexer/peer
tie-breakers, and size. If HDTV-1080p, WEBRip-1080p, WEBDL-1080p and
Bluray-1080p are separate quality tiers, a much larger Bluray/WEB release can
beat a small healthy 1080p release.

This helper makes those four normal 1080p qualities one equal quality group and
adds 1080p-only size Custom Formats. Smaller size bands receive higher scores,
so storage size is considered before indexer/seeder tie-breakers.

Safety
------
- DRY RUN by default.
- Use --apply to write changes.
- Backs up the current quality profile and Custom Formats before applying.
- Preserves all existing Custom Format scores.
- Does not touch queues, downloads, episode files, download clients, or 4K
  profiles.
- Idempotent: re-running reconciles the same named size formats instead of
  creating duplicates.

Environment
-----------
SONARR_URL                default: http://127.0.0.1:8989
SONARR_KEY                required
SONARR_NORMAL_PROFILE_ID  default: 4
"""

import argparse
import copy
import datetime as _dt
import json
import os
import sys
import urllib.error
import urllib.request


SONARR_URL = os.environ.get("SONARR_URL", "http://127.0.0.1:8989").rstrip("/")
API_KEY = os.environ.get("SONARR_KEY", "").strip()
PROFILE_ID = int(os.environ.get("SONARR_NORMAL_PROFILE_ID", "4"))

PREFIX = "SO 1080p Size "
GROUP_NAME = "1080p Storage"

TIERS = [
    (0.00, 0.35, 1400),
    (0.35, 0.45, 1300),
    (0.45, 0.60, 1200),
    (0.60, 0.80, 1100),
    (0.80, 1.00, 1000),
    (1.00, 1.30, 900),
    (1.30, 1.60, 800),
    (1.60, 2.00, 700),
    (2.00, 2.50, 600),
    (2.50, 3.00, 500),
    (3.00, 4.00, 400),
    (4.00, 6.00, 300),
    (6.00, 10.00, 200),
    (10.00, 100.00, 100),
]

NORMAL_1080_NAMES = {
    "HDTV-1080p",
    "WEBRip-1080p",
    "WEBDL-1080p",
    "Bluray-1080p",
}


def api(method, path, payload=None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        SONARR_URL + "/api/v3" + path,
        data=data,
        headers={
            "X-Api-Key": API_KEY,
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            raw = response.read()
            if not raw:
                return None
            return json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        raise RuntimeError(
            "HTTP %s %s: %s" % (exc.code, path, body[:2000])
        ) from exc


def get(path):
    return api("GET", path)


def quality_name(item):
    q = item.get("quality")
    if not isinstance(q, dict):
        return ""
    return str(q.get("name") or "")


def schema_template(schema, implementation):
    for item in schema:
        if str(item.get("implementation") or "") == implementation:
            return copy.deepcopy(item)
    raise RuntimeError("Sonarr schema %s not found" % implementation)


def make_spec(schema, implementation, condition_name, values):
    spec = schema_template(schema, implementation)
    spec["name"] = condition_name
    spec["negate"] = False
    spec["required"] = True

    found = set()
    for field in spec.get("fields") or []:
        name = str(field.get("name") or "")
        if name in values:
            field["value"] = values[name]
            found.add(name)

    missing = set(values) - found
    if missing:
        raise RuntimeError(
            "%s schema fields missing: %s"
            % (implementation, sorted(missing))
        )
    return spec


def format_name(index, minimum, maximum):
    return "%s%02d %.2f-%.2f GB" % (
        PREFIX,
        index,
        minimum,
        maximum,
    )


def desired_cf_payload(schema, index, minimum, maximum):
    return {
        "name": format_name(index, minimum, maximum),
        "includeCustomFormatWhenRenaming": False,
        "specifications": [
            make_spec(
                schema,
                "ResolutionSpecification",
                "1080p only",
                {"value": 1080},
            ),
            make_spec(
                schema,
                "SizeSpecification",
                "Size range",
                {"min": minimum, "max": maximum},
            ),
        ],
    }


def write_backup(profile, formats):
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = "sonarr-storage-first-backup-%s.json" % stamp
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(
            {
                "profile": profile,
                "custom_formats": formats,
            },
            handle,
            indent=2,
            ensure_ascii=False,
        )
        handle.write("\n")
    return path


def collect_normal_1080(items):
    found = {}

    def walk(item):
        qname = quality_name(item)
        if qname in NORMAL_1080_NAMES:
            found[qname] = copy.deepcopy(item)
        for child in item.get("items") or []:
            walk(child)

    for item in items:
        walk(item)

    missing = NORMAL_1080_NAMES - set(found)
    if missing:
        raise RuntimeError(
            "Required normal 1080p qualities missing: %s"
            % sorted(missing)
        )
    return found


def build_profile(profile, formats, score_by_id):
    result = copy.deepcopy(profile)
    items = copy.deepcopy(result.get("items") or [])
    found = collect_normal_1080(items)

    # Reuse the existing WEB 1080p group (or existing storage group) so the
    # group keeps a valid Sonarr-assigned group ID.
    storage_group = None
    for item in items:
        if item.get("quality") is not None:
            continue
        names = {
            quality_name(child)
            for child in (item.get("items") or [])
        }
        if str(item.get("name") or "") == GROUP_NAME:
            storage_group = item
            break
        if {"WEBRip-1080p", "WEBDL-1080p"}.issubset(names):
            storage_group = item

    if storage_group is None:
        raise RuntimeError(
            "No reusable WEB 1080p quality group found; refusing to invent a group ID."
        )

    group_id = int(storage_group.get("id") or 0)
    if group_id <= 0:
        raise RuntimeError("1080p group has invalid ID")

    # Remove the four target qualities from their old top-level positions/groups.
    rebuilt = []
    for item in items:
        if item is storage_group:
            continue

        if quality_name(item) in NORMAL_1080_NAMES:
            continue

        if item.get("quality") is None:
            children = [
                child
                for child in (item.get("items") or [])
                if quality_name(child) not in NORMAL_1080_NAMES
            ]
            # Sonarr groups must contain multiple qualities. The only group we
            # expect to lose children from is the reused WEB 1080p group above.
            if len(children) != len(item.get("items") or []):
                if len(children) >= 2:
                    item["items"] = children
                elif children:
                    raise RuntimeError(
                        "Unexpected quality-group shape while rebuilding profile."
                    )

        rebuilt.append(item)

    storage_group = copy.deepcopy(storage_group)
    storage_group["name"] = GROUP_NAME
    storage_group["allowed"] = True
    storage_group["items"] = [
        found["WEBRip-1080p"],
        found["WEBDL-1080p"],
        found["HDTV-1080p"],
        found["Bluray-1080p"],
    ]

    for child in storage_group["items"]:
        child["allowed"] = True

    # True 1080p-only initial-download profile.
    for item in rebuilt:
        item["allowed"] = False
        for child in item.get("items") or []:
            child["allowed"] = False

    rebuilt.append(storage_group)
    result["items"] = rebuilt
    result["cutoff"] = group_id

    existing_scores = {
        int(item.get("format") or 0): int(item.get("score") or 0)
        for item in (result.get("formatItems") or [])
    }

    result["formatItems"] = [
        {
            "format": int(cf["id"]),
            "name": cf.get("name"),
            "score": int(
                score_by_id.get(
                    int(cf["id"]),
                    existing_scores.get(int(cf["id"]), 0),
                )
            ),
        }
        for cf in formats
    ]

    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write the storage-first configuration; default is dry-run",
    )
    args = parser.parse_args()

    if not API_KEY:
        raise SystemExit("SONARR_KEY is required")

    status = get("/system/status")
    version = str(status.get("version") or "")
    if version and not version.startswith("4."):
        raise SystemExit(
            "This helper was validated against Sonarr v4; detected %s" % version
        )

    profile = get("/qualityprofile/%d" % PROFILE_ID)
    formats = get("/customformat")
    schema = get("/customformat/schema")

    print("Sonarr:", version or "unknown")
    print("Profile:", profile.get("name"), "(ID %d)" % PROFILE_ID)
    print("Mode:", "APPLY" if args.apply else "DRY RUN")
    print()

    wanted = []
    for index, (minimum, maximum, score) in enumerate(TIERS, 1):
        wanted.append(
            (
                format_name(index, minimum, maximum),
                desired_cf_payload(schema, index, minimum, maximum),
                score,
            )
        )

    existing_by_name = {
        str(cf.get("name") or ""): cf
        for cf in formats
    }

    missing_names = [
        name for name, _payload, _score in wanted
        if name not in existing_by_name
    ]

    print("Storage-first size formats:")
    for name, _payload, score in wanted:
        print(
            "  %s | score %d | %s"
            % (
                name,
                score,
                "present" if name in existing_by_name else "CREATE",
            )
        )

    if not args.apply:
        print()
        print("DRY RUN COMPLETE - nothing changed.")
        print(
            "Run again with --apply to create/reconcile the profile and scores."
        )
        return 0

    backup_path = write_backup(profile, formats)
    print()
    print("Backup:", backup_path)

    # Create only missing named formats. Existing tested formats are reused.
    for name, payload, _score in wanted:
        if name in existing_by_name:
            continue
        api("POST", "/customformat", payload)
        print("CREATED:", name)

    formats = get("/customformat")
    by_name = {
        str(cf.get("name") or ""): cf
        for cf in formats
    }

    score_by_id = {}
    for name, _payload, score in wanted:
        cf = by_name.get(name)
        if not cf:
            raise RuntimeError("Created Custom Format not found: %s" % name)
        score_by_id[int(cf["id"])] = int(score)

    proposed = build_profile(
        get("/qualityprofile/%d" % PROFILE_ID),
        formats,
        score_by_id,
    )

    api(
        "PUT",
        "/qualityprofile/%d" % PROFILE_ID,
        proposed,
    )

    verified = get("/qualityprofile/%d" % PROFILE_ID)
    group = next(
        (
            item
            for item in (verified.get("items") or [])
            if str(item.get("name") or "") == GROUP_NAME
        ),
        None,
    )
    if not group:
        raise RuntimeError("Verification failed: 1080p Storage group missing")

    names = {
        quality_name(child)
        for child in (group.get("items") or [])
    }
    if names != NORMAL_1080_NAMES:
        raise RuntimeError(
            "Verification failed: wrong group members: %s" % sorted(names)
        )

    if int(verified.get("cutoff") or 0) != int(group.get("id") or 0):
        raise RuntimeError("Verification failed: cutoff is not 1080p Storage")

    scores = {
        int(item.get("format") or 0): int(item.get("score") or 0)
        for item in (verified.get("formatItems") or [])
    }
    for cf_id, wanted_score in score_by_id.items():
        if scores.get(cf_id) != wanted_score:
            raise RuntimeError(
                "Verification failed: size score mismatch for CF %d" % cf_id
            )

    print()
    print("SONARR STORAGE-FIRST PROFILE = PASS")
    print(
        "Equal quality tier: %s"
        % ", ".join(sorted(NORMAL_1080_NAMES))
    )
    print(
        "Smaller size bands now outrank indexer/seeder tie-breakers."
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
