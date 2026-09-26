# Smart Optimizer UI v2.2.4

## Rate-limit-safe updater metadata

v2.2.4 removes the normal dependency on GitHub's unauthenticated REST API for update checks.

The previous updater queried `api.github.com/releases/latest`. During the live v2.2.2 test, the earlier browser refresh loop repeatedly forced fresh release checks and exhausted GitHub's unauthenticated API quota for the NAS public IP, producing HTTP 403 rate-limit errors even though newer releases existed.

Smart Optimizer now checks a small release asset named `SmartOptimizerUI-update.json` through GitHub's normal release-download path. Each release publishes this manifest with:

- release version and notes
- application bundle filename, size and direct download URL
- application bundle SHA-256
- SPK filename, size and direct download URL
- SPK SHA-256

The GitHub API remains only as a compatibility fallback.

## Additional updater fixes retained

- the Updates page no longer loops after a successful update
- successful update POSTs redirect back to the normal Updates page
- queued/installing updates cannot be submitted repeatedly
- update payload and supervisor use the same persistent package update directory
- release assets without a trusted SHA-256 digest are rejected
- an unavailable update server now shows **Unavailable** rather than incorrectly showing **Up to date**
- favicon caching is corrected for SPK installs

No optimizer ranking, RULES, connection, authentication, or local-state behavior changed.
