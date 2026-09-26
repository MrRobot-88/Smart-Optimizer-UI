# Smart Optimizer UI v2.2.2

## Self-update hotfix

v2.2.2 fixes the first real-world issue found while testing the new v2.2.0 Radarr-style updater.

The v2.2.0 application downloaded update bundles under the configuration directory while the stable supervisor only accepted bundles from the package update directory. The supervisor therefore rejected a correctly downloaded and SHA-256 verified bundle with:

`Update bundle is outside package storage.`

v2.2.2 aligns both sides on the same persistent package update directory.

## Update page improvements

- Successful in-app update submissions now redirect back to the normal Updates page instead of leaving the browser on the POST endpoint.
- Refreshing the browser can no longer re-submit the update form and accidentally queue the same update repeatedly.
- While an update is queued or installing, the button displays **Updating…** and is disabled.
- When already current, the button displays **Up to date**.
- The favicon response is no longer cached for a day and the favicon URL is cache-busted once so Synology SPK installs pick up the bundled Smart Optimizer icon reliably.

## Architecture

The v2.2.x application updater remains unprivileged. It updates only the writable Smart Optimizer application payload; the stable Synology package shell remains in place.

Fresh installs and package-level changes still use the normal SPK.
