# Smart Optimizer UI v2.0.1

## Full offline Synology package

- The Synology DSM 7.2.1+ x86_64 SPK is now a **full self-contained offline installer**, not a tiny bootstrap package.
- The complete Smart Optimizer container image is bundled inside the SPK.
- A fresh installation does not need GitHub Container Registry, Docker Hub or internet access to obtain Smart Optimizer itself.
- Synology Container Manager uses the official `docker-project` preload-image mechanism to load the bundled image before deploying the project.
- The build fails closed if the image archive or resulting SPK is suspiciously small, preventing accidental publication of another ~20 KB bootstrap package.
- Configuration, authentication and optimizer state remain in DSM's persistent package `var` directory so application upgrades do not overwrite user data.

## New Updates page

- Added an **Updates** entry from Settings.
- Added a dedicated Updates page showing:
  - installed version
  - latest GitHub release
  - update status
  - full release changelog
  - release package name and size
- Added update-package download support from the UI.
- The release package is SHA-256 verified before installation when a checksum is available.
- When Smart Optimizer is installed as the Synology SPK, the updater can hand the downloaded package to the package update path and restart Smart Optimizer after the upgrade.
- Normal Smart Optimizer operation and first-time offline installation do not require internet access. Internet is only needed when the user explicitly checks for or downloads a newer release.
- If GitHub is unavailable, the installed version continues to work normally.

## Smart Optimizer UI

- Includes the exact current live UI used on the verified NAS.
- Includes the current home, Radarr and Sonarr backgrounds, icons, glass styling and UI effects.
- Includes current Settings, authentication, cached library search, Manual Optimizer, exclusions, download radar, history and status views.
- Includes the full merged Radarr + Sonarr optimizer engines inside the same container image.

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

The public package/repository does not include configured API keys, authentication passwords, Deluge credentials, connection files or runtime optimizer state.
