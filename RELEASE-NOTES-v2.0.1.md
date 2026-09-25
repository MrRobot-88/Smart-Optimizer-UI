# Smart Optimizer UI v2.0.1

## Full offline Synology SPK

Smart Optimizer UI v2.0.1 is the first full self-contained offline Synology package.

- The complete Smart Optimizer x86_64 container image is physically bundled inside the SPK.
- A clean DSM installation does not need GitHub Container Registry, Docker Hub or internet access to obtain the application.
- Synology Container Manager uses DSM's official `docker-project` `preload-image` mechanism to load the bundled image.
- The SPK includes the complete UI, Radarr optimizer and Sonarr optimizer.
- CI refuses to publish an implausibly small image/SPK, preventing another bootstrap-only ~20 KB package.
- User configuration and optimizer state remain persistent across application upgrades.

## New Updates page

A new **Updates** section is available from Settings.

It shows:

- installed Smart Optimizer version
- latest GitHub release
- update availability
- release changelog
- release package information
- package download/update status

The UI can download the latest release package when internet access is available.

When installed as the Synology package, the package-update workflow can hand the verified SPK to the Synology update path and restart Smart Optimizer after upgrading.

The application itself remains fully usable without internet access. Internet is needed only when the user chooses to check for or obtain a newer release.

## Full current UI

The package contains the exact current live Smart Optimizer UI, including:

- current home background
- Radarr background
- Sonarr background
- Radarr/Sonarr/Settings icons
- glass styling and current visual effects
- authentication
- connection settings
- cached library search
- Manual Optimizer
- exclusions
- current downloads
- history
- update page

## Radarr optimizer

- exact optimizer-owned torrent/queue binding
- guarded root-file safety
- protected-cut handling
- native-import reconciliation
- safe duplicate cleanup
- exact old-file recovery
- movieFile ID-churn recovery

## Sonarr V3C

- TorrentLeech-first valid candidate pool
- fallback indexers only when no valid TorrentLeech candidate exists
- smallest valid normal 1080p release wins
- HDR/Atmos cannot make a larger normal 1080p release win
- 720p may upgrade only to 1080p
- 720p -> 1080p may grow by at most 40%
- 1080p and 2160p replacements must always shrink
- normal downsizing hard-capped at 40%
- UHD requires 2160p + HDR
- UHD prefers DV+HDR, then HDR, then Atmos, then smaller size
- AV1 rejected
- season/multi-episode packs rejected
- episode-file ID/size/resolution/profile revalidated before live grab
- optimizer-owned 0.00% dead-download recovery after five minutes

## Validation

Sonarr policy verification:

- PASS=10
- WARN=0
- FAIL=0

## Privacy

The public package does not contain:

- configured Radarr/Sonarr API keys
- Smart Optimizer passwords
- Deluge credentials
- private connection files
- Radarr optimizer runtime state
- Sonarr optimizer runtime state
- control JSON
