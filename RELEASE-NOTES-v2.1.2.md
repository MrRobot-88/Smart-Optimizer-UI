# Smart Optimizer UI v2.1.2

## DSM 7 updater fix

This release fixes the GitHub SPK update flow introduced in v2.0.4.

The previous updater correctly downloaded the latest SPK and verified its SHA-256 digest, but then wrote an install-request file for a host updater that was never present in the public package. The UI therefore claimed that installation would continue automatically even though no installer was running.

On DSM 7, third-party packages run as an unprivileged package user, so a normal GitHub-installed SPK cannot safely replace itself through the privileged Package Center installer.

v2.1.2 changes the flow to:

- download the latest GitHub SPK to package storage
- verify its SHA-256 digest
- expose that verified SPK back to the authenticated browser
- let the user download the verified package
- instruct the user to install it over the existing version using DSM Package Center > Manual Install
- preserve existing Smart Optimizer configuration/state during the package upgrade

The release build also fails if the packaged UI ever again claims that automatic self-installation will continue.

## Also retained

- Release SPKs identify themselves as release builds, not the development/master build.
- RULES V2 behavior is unchanged.
- No local API keys, control JSON, passwords, or runtime state are included.
