# Smart Optimizer UI v2.0.0

## Highlights

- Synced the exact tested live NAS versions of Smart Optimizer UI, Radarr Smart Optimizer and Sonarr Smart Optimizer.
- Major UI refresh with dedicated Radarr/Sonarr styling, cached library search and Manual Optimizer workflows.
- Radarr optimizer hardening:
  - exact optimizer-owned queue binding
  - one-shot automatic searches
  - guarded root-file checks
  - protected-cut handling
  - native-import reconciliation
  - exact old-file identity recovery and safe duplicate cleanup
  - recovery across Radarr movieFile ID churn
- Sonarr Smart Optimizer V3C:
  - TorrentLeech-first valid candidate pool
  - fallback to other indexers only when no valid TorrentLeech result exists
  - smallest valid normal 1080p release wins
  - 720p may upgrade only to 1080p, with at most +40% growth
  - 1080p/2160p replacements must always shrink
  - normal downsizing hard-capped at 40%
  - UHD requires 2160p + HDR and prefers DV+HDR, then HDR, then Atmos, then smaller size
  - AV1 and season/multi-episode packs rejected
  - final episode-file ID/size/resolution/profile revalidation before grab
  - optimizer-owned dead-download recovery for 0.00% downloads after five minutes
- Sonarr v2 policy validated with a real Ted Lasso S04E08 replacement and synthetic policy tests: PASS=10, WARN=0, FAIL=0.
- Synology DSM 7.2.1+ SPK version updated to 2.0.0.

## Privacy

API keys, connection files, Deluge credentials, optimizer runtime state and control JSON are not included in the public release.
