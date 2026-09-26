# Smart Optimizer UI v2.1.0

## RULES V2

This release adds the new configurable Radarr and Sonarr RULES system.

### New RULES controls

- Dedicated RULES pages for Radarr and Sonarr.
- Configurable resolution paths and per-path growth limits.
- Codec preference selector: none, x265, x264 or AV1.
- Source preferences including Remux, BluRay, WEB-DL, WEBRip and HDTV.
- Optional HDR10+, 10-bit, DTS:X, lossless audio and E-AC-3/DD+ preferences.
- Optional PROPER/REPACK and Freeleech preferences.
- Optional smaller-file and seeder tie-breaking.
- Dynamic ordered preferred-indexer lists populated from the configured Arr indexers.
- Preferred indexers do not remove fallback releases from consideration.

### Ranking

- Highest permitted resolution remains the first hard selection boundary.
- Radarr and Sonarr now use configurable V2 release ranking instead of the old hard-coded TorrentLeech/x265 preference logic.
- Nonpreferred indexers remain valid fallbacks.
- Existing hard safety checks remain in force.
- Sonarr's unintended hidden 40% reduction cap has been removed.

### UI

- Larger RULES controls on both dashboards.
- Radarr RULES uses the Radarr red hover treatment.
- Sonarr RULES uses the Sonarr blue hover treatment.
- Restored the dedicated 32x32 browser favicon.

### Synology

- 64x64 and 256x256 DSM package icons are now retained as explicit package assets.
- Native offline x86_64 SPK packaging remains self-contained.

### Fresh-install defaults

Public releases do not contain the maintainer's local RULES configuration.

- Optional RULES preferences default to OFF.
- Percentage/growth defaults are 0%.
- Preferred-indexer lists start empty.
- Local Smart Optimizer control JSON is not included.

### Privacy

The public release does not contain:

- Radarr API keys
- Sonarr API keys
- passwords or Deluge credentials
- private LAN addresses
- smart-optimizer-control.json
- optimizer runtime state
