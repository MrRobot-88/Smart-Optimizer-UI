# Smart Optimizer UI v2.0.1

## Full offline Synology package

- Reworked the Synology SPK into a **self-contained offline installer** for x86_64 DSM 7.2.1+ systems.
- The SPK now bundles the complete Smart Optimizer container image instead of acting as a tiny bootstrap package.
- A fresh NAS installation no longer needs GitHub Container Registry or internet access to obtain the application image.
- Synology Container Manager preloads the bundled image through the official DSM `docker-project` resource worker before starting the project.
- The offline package is intentionally fail-closed during CI/build if the bundled image or final SPK is suspiciously small.
- Package config and optimizer state now use the persistent DSM package `var` directory so upgrades do not replace user settings/state with application files.

## Smart Optimizer UI

- Includes the full modern Smart Optimizer UI: current backgrounds, icons, glass styling, Radarr/Sonarr themes, Manual Optimizer views, caching, authentication and settings.
- Includes the current Updates page/changelog experience from the live UI.
- The Updates page checks GitHub Releases when internet is available; normal Smart Optimizer operation and offline installation do not require internet access.
- Future update releases can be downloaded from the Updates page while the installed app remains usable when GitHub is unavailable.

## Radarr engine

- Exact optimizer-owned queue/download binding.
- Guarded root-file validation.
- Protected-cut handling.
- Native-import reconciliation.
- Exact old-file recovery and safe duplicate cleanup.
- Recovery across Radarr movieFile ID churn.

## Sonarr V3C engine

- TorrentLeech-first valid candidate pool.
- Falls back to other indexers only when no valid TorrentLeech candidate exists.
- Normal 1080p is storage-first: the smallest valid release wins.
- HDR/Atmos do not make a larger normal 1080p release win.
- 720p may upgrade only to 1080p and may grow by at most 40%.
- 1080p and 2160p replacements must always be strictly smaller.
- Normal downsizing is hard-capped at 40%.
- UHD requires 2160p + HDR and prefers DV+HDR, then HDR, then Atmos, then smaller size.
- AV1 and season/multi-episode packs are rejected.
- Episode file ID, byte size, resolution and profile are revalidated immediately before a live grab.
- Optimizer-owned dead-download recovery handles downloads that remain at 0.00% for five minutes.

## Validation

Sonarr v2 policy validation:

- PASS=10
- WARN=0
- FAIL=0

The public package/repository does not include configured API keys, authentication passwords, Deluge credentials, connection files or runtime state.
