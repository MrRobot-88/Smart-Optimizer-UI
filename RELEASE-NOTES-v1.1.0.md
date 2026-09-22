# Smart Optimizer UI v1.1.0

## Highlights

- Added **Manual Optimizer** as a third dashboard mode beside Current downloads and Exclusions.
- Added fast cached Radarr movie and Sonarr series library search; keystrokes do not query the Arr APIs directly.
- Added targeted one-title optimization outside the normal persistent A-Z queue.
- Explicit Manual Optimizer runs do not consume the normal daily optimizer search counter.
- Added result lifecycle: **Starting → Searching → No Upgrade / Downloading**, with No Upgrade returning to a reusable Optimize button after the cooldown.
- Added targeted Sonarr series execution.
- Added Radarr optimizer-owned download binding and guarded recovery from source-tier-only import rejection.
- Improved Radarr completion verification using actual registered movie-file state and duplicate-safe exact old-file cleanup.
- Radarr skips current movie files below **5 GiB**.
- Sonarr skips current episode files below **400 MiB**.
- Sonarr low-resolution upgrade growth ceiling is now **+40%**; same-resolution 1080p/2160p replacements never grow, and 1080p → 2160p does not get the low-resolution growth exception.
- Preserved HDR/Dolby Vision, AV1, audio/channel and Atmos-aware safety/ranking rules.
- Synology DSM 7.2.1+ SPK release version updated to **1.1.0**.

The public release contains default connection settings only. API keys and runtime state/configuration are not included.
