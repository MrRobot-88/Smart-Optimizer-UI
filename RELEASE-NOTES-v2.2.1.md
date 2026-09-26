# Smart Optimizer UI v2.2.1

## First one-click in-app update test

v2.2.1 is intentionally a small follow-up release whose main purpose is to prove the new v2.2.0 Radarr-style updater on a real Synology install.

If you already installed the v2.2.0 SPK, this release should be installable entirely from Smart Optimizer:

1. Open **Settings > Updates**.
2. Press **Update now**.
3. Smart Optimizer downloads the official v2.2.1 application bundle from GitHub.
4. The GitHub SHA-256 digest is verified.
5. The stable package supervisor verifies and stages the bundle again.
6. Smart Optimizer restarts automatically on v2.2.1.
7. If startup validation fails, the supervisor rolls back to the previous working release.

No DSM Package Center step should be required for this application update.

## What changed

This is a validation release for the new self-update architecture. There are no intended changes to optimizer ranking, RULES V2, Radarr/Sonarr connection settings, authentication, or local state.

The normal SPK is still published for fresh installs and package-level upgrades, while existing v2.2.0 installs should use the in-app application bundle.

## Security

- The updater remains unprivileged.
- Only the Smart Optimizer application payload is replaced.
- SHA-256 is verified before activation.
- Unsafe archive paths and symlinks are rejected.
- Local API keys, passwords, control JSON, connection files, exclusions, and runtime state are not part of the update bundle.
