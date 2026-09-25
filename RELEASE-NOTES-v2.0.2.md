# Smart Optimizer UI v2.0.2

## True native Synology package

Smart Optimizer UI now installs and runs as a native DSM package.

It no longer requires or deploys Smart Optimizer through Container Manager.

The package includes:

- Smart Optimizer UI
- Radarr Smart Optimizer
- Sonarr Smart Optimizer V3C
- current UI backgrounds, icons and visual effects
- Updates page
- authentication and settings
- a bundled portable Python runtime

The application runs directly from:

`/var/packages/smartoptimizerui/target`

Persistent configuration and optimizer data remain under:

`/var/packages/smartoptimizerui/var`

A fresh installation does not require a Docker image pull and does not create a Smart Optimizer container.

## Package Center icon

The native package is designed to use the Smart Optimizer `pixel128.png` artwork as its DSM Package Center icon.

## Offline installation

The Python runtime and complete application are physically included in the SPK.

Internet access is not required to install or run Smart Optimizer. Internet is only required when the user explicitly checks GitHub for future releases or downloads an update.

## UI

Includes the current Smart Optimizer interface and merged Radarr/Sonarr optimizer engines.

## Privacy

The public SPK does not contain configured API keys, passwords, Deluge credentials or optimizer runtime state.
