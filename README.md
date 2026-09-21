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
- Manual amount represents successful upgrades/grabs rather than merely attempted searches
- STOP control for UI-started runs
- Adjustable minimum/maximum downsize window
- Daily search budget plus date-scoped temporary extra searches
- Current download radar and recent file-change history
- Optimizer-only exclusions:
  - Radarr: exclude individual movies
  - Sonarr: exclude entire series
  - search the current library, add/remove exclusions, recent exclusions and full exclusion history
- Exclusions never delete media; they only prevent the optimizer from searching/replacing excluded content
- Settings/status panels use each application's own independent values
- Dedicated Settings page for Radarr and Sonarr connection configuration

## Different Radarr and Sonarr policies

The two optimizers intentionally do **not** use identical replacement rules.

**Radarr is storage-first.** Its replacements must remain inside the configured saving window. It does not inherit Sonarr's low-resolution size-growth exception.

**Sonarr** has a special low-resolution rule for episodes currently below 1080p: a higher-resolution candidate up to the profile target may be smaller, equal-sized or at most **50% larger**. This exception does not apply to 1080p → 2160p.

Both engines include conservative dynamic-range rules, reject AV1 candidates, reject Dolby Vision-only candidates without HDR fallback, and apply audio safety/ranking logic. See the standalone READMEs for the detailed engine policies.

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

Native platform packages such as Synology `.spk`, Debian/Ubuntu `.deb`, RPM `.rpm`, QNAP `.qpkg` and other vendor-specific packages are **not currently published**. They may be added in the future after they have dedicated packaging, installation/update handling and testing. For now, Docker is the supported cross-platform installation method.

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

Manual runs use the same persistent optimizer queue as scheduled runs. The requested manual number is a target for successful releases sent to Radarr/Sonarr; unsuccessful searches do not satisfy that target. Search budgets still cap how much work a run may perform.

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
- Sonarr low-resolution upgrade support up to +50% size growth for existing content below 1080p
- HDR/Dolby Vision safety rules
- AV1 rejection
- Audio/channel protection and Atmos-aware ranking
- Persistent state, daily search budgets and temporary extra searches

Radarr and Sonarr intentionally use different replacement policies. See their standalone repositories for the detailed rules.
