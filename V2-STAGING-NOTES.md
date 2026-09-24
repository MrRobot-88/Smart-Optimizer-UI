# Smart Optimizer UI v2.0 staging

This branch stages the verified v2 Sonarr engine and Synology SPK versioning.

## Included and verified

- Embedded Sonarr optimizer updated to the verified V3C policy.
- TorrentLeech-first valid candidate pool.
- Smallest-first normal 1080p selection.
- 720p -> 1080p-only upgrade path with a maximum +40% growth allowance.
- 1080p/2160p replacements must always shrink.
- Normal downsizing hard-capped at 40%.
- UHD requires 2160p + HDR and prefers DV+HDR, then HDR, then Atmos, then smaller size.
- AV1 and season/multi-episode packs rejected.
- Final episode-file identity/size/resolution/profile revalidation before grab.
- Synology SPK fallback/build version staged as 2.0.0.

## Validation

The Sonarr v2 policy passed a real Ted Lasso S04E08 replacement test and synthetic policy tests:

- PASS=10
- WARN=0
- FAIL=0

## Release-ready state

The exact live NAS copies of `smart-optimizer-ui.py`, `radarr-smart-optimizer.py`, and `sonarr-smart-optimizer.py` have now been synced into this branch through the GitHub bridge after local syntax, SHA, private-key and SPK-structure checks.

No API keys, connection files, optimizer state, or control JSON are included.
