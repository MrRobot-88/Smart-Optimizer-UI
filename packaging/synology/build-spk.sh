#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SRC="$ROOT/packaging/synology"
OUT="${1:-$ROOT/dist}"
VERSION="${VERSION:-2.2.0}"
RUNTIME_ARCHIVE="${PORTABLE_PYTHON_ARCHIVE:-}"

if [ -z "$RUNTIME_ARCHIVE" ]; then
    echo "ERROR: PORTABLE_PYTHON_ARCHIVE is required."
    exit 1
fi

if [ ! -s "$RUNTIME_ARCHIVE" ]; then
    echo "ERROR: Portable Python archive missing: $RUNTIME_ARCHIVE"
    exit 1
fi

for FILE in     "$ROOT/smart-optimizer-ui.py"     "$ROOT/radarr-smart-optimizer.py"     "$ROOT/sonarr-smart-optimizer.py"     "$SRC/smart-optimizer-supervisor.py"     "$SRC/pixel128.png" "$SRC/PACKAGE_ICON.PNG" "$SRC/PACKAGE_ICON_256.PNG"
do
    if [ ! -s "$FILE" ]; then
        echo "ERROR: required file missing: $FILE"
        exit 1
    fi
done

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

mkdir -p "$OUT" "$STAGE/scripts" "$STAGE/conf" "$STAGE/payload/app" "$STAGE/payload/runtime"

sed "s/__VERSION__/$VERSION/g" "$SRC/INFO.template" > "$STAGE/INFO"

cp -a "$SRC/scripts/." "$STAGE/scripts/"
cp -a "$SRC/conf/." "$STAGE/conf/"

cp -p "$ROOT/smart-optimizer-ui.py" "$STAGE/payload/app/smart-optimizer-ui.py"
cp -p "$ROOT/radarr-smart-optimizer.py" "$STAGE/payload/app/radarr-smart-optimizer.py"
cp -p "$ROOT/sonarr-smart-optimizer.py" "$STAGE/payload/app/sonarr-smart-optimizer.py"
cp -a "$ROOT/assets" "$STAGE/payload/app/assets"
cp -p "$SRC/smart-optimizer-supervisor.py" "$STAGE/payload/smart-optimizer-supervisor.py"

# Public SPK builds are release packages, not the development/master UI.
# Keep the source checkout in development mode, but disable that override
# inside the staged package that users install from GitHub Releases.
ADMIN_UPDATE_FRAGMENT="$STAGE/payload/app/assets/fragments/admin-update-mode.html"

if [ ! -f "$ADMIN_UPDATE_FRAGMENT" ]; then
    echo "ERROR: admin update-mode fragment missing from staged package."
    exit 1
fi

sed -i \
    's/const SMART_ADMIN_DEVELOPMENT_BUILD = true;/const SMART_ADMIN_DEVELOPMENT_BUILD = false;/' \
    "$ADMIN_UPDATE_FRAGMENT"

grep -q \
    'const SMART_ADMIN_DEVELOPMENT_BUILD = false;' \
    "$ADMIN_UPDATE_FRAGMENT"

echo "Extracting bundled portable Python..."
tar -xzf "$RUNTIME_ARCHIVE" -C "$STAGE/payload/runtime"

PYTHON="$STAGE/payload/runtime/python/bin/python3"

if [ ! -x "$PYTHON" ]; then
    echo "ERROR: expected bundled Python executable missing: $PYTHON"
    exit 1
fi

"$PYTHON" -c 'import hashlib, http.server, json, ssl, subprocess, threading, urllib.request; print("Bundled Python runtime OK")'

"$PYTHON" -m py_compile     "$STAGE/payload/app/smart-optimizer-ui.py"     "$STAGE/payload/app/radarr-smart-optimizer.py"     "$STAGE/payload/app/sonarr-smart-optimizer.py"     "$STAGE/payload/smart-optimizer-supervisor.py"

find "$STAGE/payload/app" -type d -name __pycache__ -prune -exec rm -rf {} +

chmod 755 "$STAGE/scripts/"*

# DSM Package Center icons are committed, reviewed assets.
cp -p "$SRC/PACKAGE_ICON.PNG" "$STAGE/PACKAGE_ICON.PNG"
cp -p "$SRC/PACKAGE_ICON_256.PNG" "$STAGE/PACKAGE_ICON_256.PNG"

tar -C "$STAGE/payload" -czf "$STAGE/package.tgz" .
rm -rf "$STAGE/payload"

SPK="$OUT/SmartOptimizerUI-${VERSION}-DSM7.2.1-NATIVE-OFFLINE-x86_64.spk"

tar -C "$STAGE" -cf "$SPK"     INFO     package.tgz     scripts     conf     PACKAGE_ICON.PNG     PACKAGE_ICON_256.PNG

SPK_BYTES="$(wc -c < "$SPK" | tr -d ' ')"

if [ "$SPK_BYTES" -lt 20000000 ]; then
    echo "ERROR: Native offline SPK suspiciously small: $SPK_BYTES bytes"
    exit 1
fi

echo "Built: $SPK"
echo "Size: $SPK_BYTES bytes"
tar -tf "$SPK"
