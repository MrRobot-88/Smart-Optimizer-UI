# Smart Optimizer UI

Optional combined web UI for:

- [Sonarr Smart Optimizer](https://github.com/MrRobot-88/Sonarr-Smart-Optimizer)
- [Radarr Smart Optimizer](https://github.com/MrRobot-88/Radarr-Smart-Optimizer)

The optimizers remain fully standalone. This project is only an optional sidecar.

## Features

- One dashboard for Radarr and Sonarr
- Manual search amount: choose 5, 50, 1000, etc.
- Live run counter refreshed every 10 seconds
- STOP button that only stops the optimizer process launched by this UI
- Adjustable downsize percentage window
- Today's optimizer search count and temporary manual allowance
- Current download queue

A manual request adds that amount to the temporary allowance for the current day and launches a live optimizer run capped to that number. The temporary allowance resets naturally the next day because controls are date-keyed.

## Runtime inputs

The UI expects the two optimizer scripts, their state files, and one shared control JSON file to be mounted into the container.

Environment variables:

- `RADARR_URL`, `RADARR_KEY`
- `SONARR_URL`, `SONARR_KEY`
- `RADARR_OPTIMIZER_SCRIPT`
- `SONARR_OPTIMIZER_SCRIPT`
- `RADARR_OPTIMIZER_STATE`
- `SONARR_OPTIMIZER_STATE`
- `SMART_OPTIMIZER_CONTROL`
- `RADARR_DAILY_SEARCH_BUDGET` (default 400)
- `SONARR_DAILY_SEARCH_BUDGET` (default 400)
- `SMART_UI_PORT` (default 8788)
- `SMART_UI_MAX_MANUAL_SEARCHES` (default 10000)

## Security

This is intended for a trusted LAN. Do not expose it directly to the public internet without authentication/reverse-proxy protection.
