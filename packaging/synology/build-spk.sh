#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SRC="$ROOT/packaging/synology"
OUT="${1:-$ROOT/dist}"
VERSION="${VERSION:-2.0.3}"
RUNTIME_ARCHIVE="${PORTABLE_PYTHON_ARCHIVE:-}"

if [ -z "$RUNTIME_ARCHIVE" ]; then
    echo "ERROR: PORTABLE_PYTHON_ARCHIVE is required."
    exit 1
fi

if [ ! -s "$RUNTIME_ARCHIVE" ]; then
    echo "ERROR: Portable Python archive missing: $RUNTIME_ARCHIVE"
    exit 1
fi

for FILE in     "$ROOT/smart-optimizer-ui.py"     "$ROOT/radarr-smart-optimizer.py"     "$ROOT/sonarr-smart-optimizer.py"     "$SRC/pixel128.png"
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

echo "Extracting bundled portable Python..."
tar -xzf "$RUNTIME_ARCHIVE" -C "$STAGE/payload/runtime"

PYTHON="$STAGE/payload/runtime/python/bin/python3"

if [ ! -x "$PYTHON" ]; then
    echo "ERROR: expected bundled Python executable missing: $PYTHON"
    exit 1
fi

"$PYTHON" -c 'import hashlib, http.server, json, ssl, subprocess, threading, urllib.request; print("Bundled Python runtime OK")'

"$PYTHON" -m py_compile     "$STAGE/payload/app/smart-optimizer-ui.py"     "$STAGE/payload/app/radarr-smart-optimizer.py"     "$STAGE/payload/app/sonarr-smart-optimizer.py"

find "$STAGE/payload/app" -type d -name __pycache__ -prune -exec rm -rf {} +

chmod 755 "$STAGE/scripts/"*

# DSM 7 expects exact 64x64 and 256x256 package icons.
# Keep pixel128.png as the canonical source artwork and generate both required sizes.
python3 - "$SRC/pixel128.png" "$STAGE/PACKAGE_ICON.PNG" "$STAGE/PACKAGE_ICON_256.PNG" <<'PY'
from pathlib import Path
from PIL import Image
import sys

source, small, large = map(Path, sys.argv[1:4])

with Image.open(source) as image:
    if image.size != (128, 128):
        raise SystemExit(f"Expected 128x128 source icon, got {image.size}")
    image = image.convert("RGBA")
    image.resize((64, 64), Image.Resampling.LANCZOS).save(small, "PNG", optimize=True)
    image.resize((256, 256), Image.Resampling.LANCZOS).save(large, "PNG", optimize=True)

for path, expected in ((small, (64, 64)), (large, (256, 256))):
    with Image.open(path) as image:
        if image.size != expected:
            raise SystemExit(f"{path.name}: expected {expected}, got {image.size}")
        if image.mode != "RGBA":
            raise SystemExit(f"{path.name}: expected RGBA, got {image.mode}")

print("DSM icon validation OK: 64x64 + 256x256")
PY

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
