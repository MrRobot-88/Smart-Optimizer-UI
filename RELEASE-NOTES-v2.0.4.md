# Smart Optimizer UI v2.0.4

## Performance and source-cleanup checkpoint

This release freezes the tested Smart Optimizer UI state before the next feature-development cycle.

### Changes

- Added RAM-only stale-while-revalidate caching for Radarr and Sonarr history, queue, and API-status requests.
- Reduced page navigation latency by avoiding repeated synchronous API waits.
- Moved large embedded PNG artwork out of `smart-optimizer-ui.py` and into `assets/`.
- Reduced the primary UI Python source from roughly 12 MB to roughly 423 KB.
- Deduplicated the shared Update Status/Favicon and Admin Update Mode blocks.
- Shared UI fragments now live under `assets/fragments/`.
- Updated Docker packaging to ship the external assets.
- Updated native Synology SPK packaging to ship the external assets.
- Preserved existing Radarr/Sonarr optimizer behavior and current UI appearance.

### Privacy

Local API keys, connection configuration, optimizer state files, and Smart Optimizer control files are not included.
