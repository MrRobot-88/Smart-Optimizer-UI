# Smart Optimizer UI v2.1.1

## Packaging hotfix

This release fixes the update/status page in the downloadable Synology SPK.

GitHub release packages now identify themselves as installed release builds instead of the development/master build. The package build also validates this before publishing so the development-only status override cannot accidentally ship again.

No optimizer ranking, RULES V2, API, or local configuration behavior changed from v2.1.0.
