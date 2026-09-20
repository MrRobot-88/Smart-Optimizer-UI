# Smart Optimizer UI

One container for **Sonarr Smart Optimizer + Radarr Smart Optimizer + the combined web UI**.

The Sonarr and Radarr optimizer engines are embedded directly in `smart-optimizer-ui.py`. The container does not require the standalone optimizer repositories or separate optimizer-script mounts.

The standalone projects remain available for users who do not want the UI:

- [Sonarr Smart Optimizer](https://github.com/MrRobot-88/Sonarr-Smart-Optimizer)
- [Radarr Smart Optimizer](https://github.com/MrRobot-88/Radarr-Smart-Optimizer)

You do **not** need to edit either optimizer script or put Radarr/Sonarr API keys in the stack. Open the web UI, go to **Settings**, enter the connection details, test them, and save. The UI passes the saved connection to the bundled optimizer whenever it launches a run.

## Features

- One dashboard for Radarr and Sonarr
- Radarr and Sonarr optimizer engines built directly into the UI application
- Web-based Radarr/Sonarr host, port and API-key setup
- Test Connection before saving
- Online/offline API status on each dashboard
- Persistent optimizer queues/state
- Manual search amount
- Live search counter and current item
- STOP button for UI-launched runs
- Adjustable downsize percentage window
- Daily search count and temporary manual allowance
- Current download queue and recent file changes

## Quick install with Docker Compose / Portainer Stack

```yaml
services:
  smart-optimizer:
    build: https://github.com/MrRobot-88/Smart-Optimizer-UI.git
    container_name: smart-optimizer
    network_mode: host
    restart: unless-stopped
    environment:
      TZ: ${TZ:-UTC}
      SMART_UI_HOST: 0.0.0.0
      SMART_UI_PORT: 8788
      RADARR_DAILY_SEARCH_BUDGET: 400
      SONARR_DAILY_SEARCH_BUDGET: 400
    volumes:
      - ./config:/config
      - ./data:/data
```

Set `TZ` to your local IANA timezone if desired (for example `Europe/London`, `America/New_York`, or `Australia/Sydney`). If it is not set, the container uses `UTC`.

Then open:

`http://YOUR-SERVER-IP:8788`

Choose **Settings** and configure:

- Radarr IP/hostname (default port 7878)
- Radarr API key
- Sonarr IP/hostname (default port 8989)
- Sonarr API key

Use **Test connection**, then **Save**.

The API keys are stored in the persistent `/config` volume and are not displayed again by the UI. Protect that directory and do not publish it.

### Networking note

The example uses `network_mode: host` because it makes connecting to Sonarr/Radarr on the same NAS/server straightforward. If you use bridge networking, enter an address that is reachable **from inside this container**; `127.0.0.1` would refer to the Smart Optimizer container itself.

## Persistent data

`/config`
- connection settings
- downsize controls and temporary daily allowances

`/data`
- Radarr optimizer state/queue
- Sonarr optimizer state/queue

Updating/recreating the container does not reset these when the volumes are retained.

## Existing standalone optimizer users

The standalone repositories still work independently and continue to accept `RADARR_URL`/`RADARR_KEY` and `SONARR_URL`/`SONARR_KEY`.

When using this combined container, however, you configure those values once in the Smart Optimizer UI instead. The UI injects the saved connection values into the bundled optimizer process, so no API-key editing is needed in the Sonarr/Radarr scripts.

## Defaults

- Smart Optimizer UI: port `8788`
- Radarr: port `7878`
- Sonarr: port `8989`
- Radarr daily optimizer budget: `400`
- Sonarr daily optimizer budget: `400`

The Radarr/Sonarr host and ports can be changed from the Settings page.

## Security

This UI is intended for a trusted LAN. It does not currently provide built-in authentication. Do not expose port 8788 directly to the public internet. API keys are secrets; keep the persistent config directory private.
