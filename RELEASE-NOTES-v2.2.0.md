# Smart Optimizer UI v2.2.0

## Radarr-style in-app updates for Synology SPK installs

v2.2.0 changes the Synology package architecture so normal Smart Optimizer application updates no longer require DSM Package Center after the one-time v2.2.0 package installation.

The SPK now contains a small stable, unprivileged supervisor. The supervisor launches Smart Optimizer from writable package storage and handles verified application updates without replacing the Synology package itself.

### Update flow

- Smart Optimizer checks the official GitHub release.
- The UI downloads the versioned Smart Optimizer application bundle.
- GitHub's SHA-256 release digest is verified before installation.
- The stable supervisor re-verifies the downloaded bundle.
- The bundle is extracted only inside Smart Optimizer's package storage.
- Required files are validated and Python sources are compiled before activation.
- The supervisor switches to the new application release and restarts the web UI.
- If the new application exits during startup validation, the supervisor automatically rolls back to the previous working release.

This is similar to Radarr's in-app update model: normal application files can update themselves while the package/service shell remains stable.

### Package updates

A new SPK is only needed when package-level components change, such as the supervisor, bundled Python runtime, Synology service integration, package permissions, or DSM compatibility.

### Security

- The supervisor runs as the normal Synology package user, not root.
- Application update bundles are restricted to the official Smart Optimizer GitHub release.
- SHA-256 is checked before activation.
- Unsafe archive paths and symlinks are rejected.
- API keys, passwords, local control JSON, connection files, and runtime state are not included in release bundles.
- Release bundles identify themselves as release builds rather than the development/master instance.

### Migration

Install the v2.2.0 SPK over the currently installed Smart Optimizer package once. After that, normal Smart Optimizer application releases can update from inside the Updates page automatically.
