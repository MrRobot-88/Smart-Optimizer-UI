# Smart Optimizer UI v2.0.3

## Package Center icon hotfix

This release fixes the DSM Package Center artwork without changing Smart Optimizer behavior.

- Replaces the package icon source with the exact supplied 128x128 Smart Optimizer logo.
- Generates DSM 7's required 64x64 `PACKAGE_ICON.PNG`.
- Generates the matching 256x256 `PACKAGE_ICON_256.PNG`.
- Preserves transparency and the original logo composition.
- Adds build validation so future SPKs fail if either icon has the wrong dimensions.

All native/offline functionality from v2.0.2 is unchanged.
