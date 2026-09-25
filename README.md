# Smart Optimizer UI

One web dashboard for **Sonarr Smart Optimizer + Radarr Smart Optimizer**.

The project provides a combined UI for configuring connections, controlling optimizer runs, viewing live progress, inspecting downloads/history and managing optimizer-only exclusions. The container image bundles both optimizer engines, so the UI is self-contained and does not require separate Radarr/Sonarr optimizer script downloads.

The standalone projects remain available for users who prefer to run the engines without the UI:

- [Sonarr Smart Optimizer](https://github.com/MrRobot-88/Sonarr-Smart-Optimizer)
- [Radarr Smart Optimizer](https://github.com/MrRobot-88/Radarr-Smart-Optimizer)

## Features

- Separate Radarr and Sonarr dashboards in one UI
- Web-based Radarr/Sonarr host, port and API-key setup
- Test Connection before saving
- Online/offline API status
- Persistent optimizer queues and state
- Manual optimizer runs with live **current item** and progress
- Dedicated **Manual Optimizer** mode with cached Radarr movie / Sonarr series search
- Target one movie or series without consuming the normal persistent A-Z queue
- Explicit Manual Optimizer runs are separated from normal daily-search accounting
- Manual result buttons report **Starting**, **Searching**, **No Upgrade** or **Downloading**
- Manual amount represents successful upgrades/grabs rather than merely attempted searches
- STOP control for UI-started runs
- Adjustable minimum/maximum downsize window
- Daily search budget plus date-scoped temporary extra searches
- Current download radar and recent file-change history
- Radarr/Sonarr library cache refreshed in the background, so library search does not query the Arr API on every keystroke
- Optimizer-owned Radarr downloads can recover safely from source-tier-only import rejection without globally weakening Radarr quality profiles
- Optimizer-only exclusions:
  - Radarr: exclude individual movies
  - Sonarr: exclude entire series
  - search the current library, add/remove exclusions, recent exclusions and full exclusion history
- Exclusions never delete media; they only prevent the optimizer from searching/replacing excluded content
- Settings/status panels use each application's own independent values
- Dedicated Settings page for Radarr and Sonarr connection configuration

## Different Radarr and Sonarr policies

The two optimizers intentionally do **not** use identical replacement rules.

**Radarr is storage-first.** Its replacements must remain inside the configured saving window. It does not inherit Sonarr's low-resolution size-growth exception. Current movie files below **5 GiB** are skipped before an interactive optimizer search.

**Sonarr** uses a stricter v2 selection policy. TorrentLeech is the primary valid candidate pool from each Sonarr release search; other indexers are fallback only when no valid TorrentLeech result exists. Normal 1080p optimization is storage-first: the smallest valid 1080p release wins, and HDR/Atmos do not outrank a smaller file. Existing 720p may upgrade only to 1080p and may grow by at most **40%**. Existing 1080p/2160p replacements must be strictly smaller, with an absolute **40% maximum reduction per pass**. The UHD profile requires 2160p with at least HDR and prefers DV+HDR, then HDR, then Atmos, then smaller size. AV1 and single-episode season/multi-episode packs are rejected.

Current episode files below **400 MiB** are skipped before an interactive optimizer search. See the standalone READMEs for the detailed engine policies.

## Supported platforms

The prebuilt Docker image is officially published for:

- `linux/amd64` — Intel/AMD 64-bit systems
- `linux/arm64` — 64-bit ARM systems
- `linux/arm/v7` — 32-bit ARMv7 systems

This covers Docker-capable systems across many common platforms, including Linux servers, Synology, QNAP, ASUSTOR, Unraid, TrueNAS SCALE, Raspberry Pi and other NAS/home-server systems, provided the device supports one of the architectures above and can run Docker/containers.

Everyone uses the same image:

```text
ghcr.io/mrrobot-88/smart-optimizer-ui:latest
```

Docker or Portainer automatically selects the matching architecture.

### Native packages

Starting with **v2.0.1**, the Synology DSM 7.2.1+ package is a **full offline x86_64 SPK** for Intel/AMD Synology systems that support **Container Manager**. It bundles the complete Smart Optimizer container image inside the SPK, including the current UI, Radarr engine and Sonarr engine.

A fresh SPK installation therefore does **not** need to contact GitHub Container Registry or Docker Hub to obtain Smart Optimizer. DSM's official Container Manager `docker-project` resource worker preloads the bundled image from the package and then deploys the project. Configuration and optimizer state live in the package's persistent `var` directory, separate from the replaceable application image.

The offline SPK is architecture-specific because it contains a real `linux/amd64` container image. Its `INFO` metadata is therefore `arch="x86_64"`, which covers Synology x86_64 platform families including apollolake systems such as the DS918+. ARM NAS models should continue to use the multi-architecture GHCR image until separate offline ARM SPKs are published.

GitHub releases include the versioned offline package:

`SmartOptimizerUI-VERSION-DSM7.2.1-OFFLINE-x86_64.spk`

The build fails if the bundled image is missing or if the resulting SPK is implausibly small, preventing accidental publication of a bootstrap-only package.

Native Debian/Ubuntu `.deb`, RPM `.rpm`, QNAP `.qpkg` and other vendor-specific packages are not currently published.

## Recommended install: Portainer Stack

The easiest way to run Smart Optimizer UI is with the prebuilt container from GitHub Container Registry. **No cloning, Python installation or local Docker build is required.**

In Portainer, open **Stacks → Add stack**, give it a name such as `smart-optimizer-ui`, and paste:

```yaml
services:
  smart-optimizer-ui:
    image: ghcr.io/mrrobot-88/smart-optimizer-ui:latest
    container_name: smart-optimizer-ui
    network_mode: host
    restart: unless-stopped

    environment:
      # Change this to your local timezone, for example:
      # Europe/London, Europe/Stockholm, America/New_York
      TZ: Etc/UTC
      SMART_UI_HOST: 0.0.0.0
      SMART_UI_PORT: 8788

    volumes:
      - ./config:/config
      - ./data:/data
```

Before deploying, change `TZ` from `Etc/UTC` to your local IANA timezone if you want local timestamps. Leaving it as `Etc/UTC` is also valid.

Click **Deploy the stack**. Docker/Portainer will pull the correct image automatically for supported `linux/amd64`, `linux/arm64` or `linux/arm/v7` systems.

A ready-made copy is also included in this repository as **`portainer-stack.yml`**.

Then open:

`http://YOUR-SERVER-IP:8788`

Open **Settings** and enter your Radarr and Sonarr connection details:

- Radarr IP/hostname and port (default: 7878)
- Radarr API key
- Sonarr IP/hostname and port (default: 8989)
- Sonarr API key

Use **Test connection**, then **Save**. The connection settings persist in `/config`; optimizer state persists in `/data`.

### Docker Compose

The same stack works with Docker Compose. Save the example above as `compose.yml` and run:

```bash
docker compose up -d
```

To update later:

```bash
docker compose pull
docker compose up -d
```

### Build from source

If you prefer to build the image yourself instead of using the prebuilt GHCR image:

```bash
git clone https://github.com/MrRobot-88/Smart-Optimizer-UI.git
cd Smart-Optimizer-UI
docker compose up -d --build
```

API keys are stored in the persistent `/config` volume and are not displayed again by the UI. Protect that directory and do not publish it.

## Dashboard controls

The Radarr and Sonarr dashboards have independent optimizer controls and status. Changing one application's downsize settings does not change the other application's values.

The normal manual-count control uses the same persistent optimizer queue as scheduled runs, and the requested number is a target for successful releases sent to Radarr/Sonarr.

The separate **Manual Optimizer** tab is different: it searches the cached library, targets one selected movie or series directly, does not consume the normal A-Z queue, and does not add its searches to the normal daily optimizer counter.

The exclusion search is separate from the normal current-download search. Radarr exclusions operate on movies; Sonarr exclusions operate on whole series. Removing an exclusion only removes the optimizer rule—it does not remove anything from Radarr/Sonarr.

## Networking

The example uses `network_mode: host` because it makes connecting to Sonarr/Radarr on the same NAS/server straightforward.

If you use bridge networking, enter an address reachable **from inside the Smart Optimizer container**. In bridge mode, `127.0.0.1` refers to the Smart Optimizer container itself.

## Persistent data

`/config`

- Radarr/Sonarr connection settings
- optimizer controls
- temporary daily allowances
- optimizer exclusions

`/data`

- Radarr optimizer state/queue
- Sonarr optimizer state/queue

Retaining these volumes prevents container recreation/update from resetting the saved configuration and optimizer state.

## Defaults

- Smart Optimizer UI port: `8788`
- Radarr default port: `7878`
- Sonarr default port: `8989`
- UI Radarr daily optimizer budget: `400`
- UI Sonarr daily optimizer budget: `400`

The standalone Radarr project has its own documented defaults; do not assume every standalone default is identical to the combined UI.

## Security

This UI is intended for a **trusted LAN**. It does not currently provide built-in authentication. Do not expose port 8788 directly to the public internet.

API keys are secrets. Keep the persistent config directory private, do not commit it, and do not hardcode real API keys or private network addresses into public source files.

## Related projects

- [Radarr Smart Optimizer](https://github.com/MrRobot-88/Radarr-Smart-Optimizer)
- [Sonarr Smart Optimizer](https://github.com/MrRobot-88/Sonarr-Smart-Optimizer)
- [Deluge Smart Cleanup](https://github.com/MrRobot-88/Deluge-Smart-Cleanup)

## Release highlights

The current release includes:

- Combined Radarr + Sonarr web dashboard
- Browser-based connection configuration
- Independent Radarr and Sonarr optimizer settings/status
- Persistent A-Z optimizer queues
- Manual runs targeting successful grabs rather than search attempts
- Live current-item and progress reporting
- Download radar and recent file-change history
- Optimizer-only exclusions with library search, recent exclusions and full exclusion management
- Radarr storage-first replacement policy
- Sonarr TorrentLeech-first candidate pool, smallest-first normal 1080p ranking, and 720p→1080p-only +40% upgrade exception
- Dedicated cached-library Manual Optimizer for one Radarr movie or Sonarr series
- Radarr 5 GiB current-file search floor and Sonarr 400 MiB current-episode search floor
- Radarr optimizer-owned safe import handling for source-tier-only import blocks
- Sonarr UHD preference for DV+HDR / HDR / Atmos with smaller-file tie breaking
- AV1 rejection
- Single-episode season-pack/multi-episode protection
- Persistent state, daily search budgets and temporary extra searches

Radarr and Sonarr intentionally use different replacement policies. See their standalone repositories for the detailed rules.
