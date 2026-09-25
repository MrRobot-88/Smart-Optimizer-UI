#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SRC="$ROOT/packaging/synology"
OUT="${1:-$ROOT/dist}"
VERSION="${VERSION:-2.0.1}"
PKG="smartoptimizerui"
OFFLINE_IMAGE_ARCHIVE="${OFFLINE_IMAGE_ARCHIVE:-}"
OFFLINE_IMAGE_NAME="smart-optimizer-ui-image.tar.gz"

if [ -z "$OFFLINE_IMAGE_ARCHIVE" ]; then
    echo "ERROR: OFFLINE_IMAGE_ARCHIVE is required."
    echo "This package is intentionally offline/self-contained and will not build a bootstrap-only SPK."
    exit 1
fi

if [ ! -s "$OFFLINE_IMAGE_ARCHIVE" ]; then
    echo "ERROR: bundled image archive is missing or empty:"
    echo "$OFFLINE_IMAGE_ARCHIVE"
    exit 1
fi

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

mkdir -p "$OUT" "$STAGE/scripts" "$STAGE/conf" "$STAGE/payload"

sed "s/__VERSION__/$VERSION/g" "$SRC/INFO.template" > "$STAGE/INFO"

cp -a "$SRC/scripts/." "$STAGE/scripts/"
cp -a "$SRC/conf/." "$STAGE/conf/"
cp -a "$SRC/payload/." "$STAGE/payload/"

cp -f "$OFFLINE_IMAGE_ARCHIVE" "$STAGE/payload/$OFFLINE_IMAGE_NAME"

chmod 755 "$STAGE/scripts/"*

# Fail closed: the offline image must actually be substantial.
IMAGE_BYTES="$(wc -c < "$STAGE/payload/$OFFLINE_IMAGE_NAME" | tr -d ' ')"
if [ "$IMAGE_BYTES" -lt 10000000 ]; then
    echo "ERROR: bundled image archive is unexpectedly small: $IMAGE_BYTES bytes"
    exit 1
fi

# package.tgz is the payload DSM extracts to /var/packages/<package>/target.
tar -C "$STAGE/payload" -czf "$STAGE/package.tgz" .
rm -rf "$STAGE/payload"

# DSM 7 Package Center icons.
python3 - "$STAGE" <<'PY'
import pathlib, struct, zlib, sys
stage = pathlib.Path(sys.argv[1])

def png(path, n):
    raw = b''.join(b'\x00' + bytes([42,48,60,255]) * n for _ in range(n))
    def chunk(t,d):
        return (
            struct.pack(">I",len(d))
            + t
            + d
            + struct.pack(">I",zlib.crc32(t+d)&0xffffffff)
        )
    data = b'\x89PNG\r\n\x1a\n'
    data += chunk(b'IHDR', struct.pack(">IIBBBBB",n,n,8,6,0,0,0))
    data += chunk(b'IDAT', zlib.compress(raw,9))
    data += chunk(b'IEND', b'')
    path.write_bytes(data)

png(stage/"PACKAGE_ICON.PNG", 64)
png(stage/"PACKAGE_ICON_256.PNG", 256)
PY

SPK="$OUT/SmartOptimizerUI-$VERSION-DSM7.2.1-OFFLINE-x86_64.spk"

tar -C "$STAGE" -cf "$SPK"     INFO     package.tgz     scripts     conf     PACKAGE_ICON.PNG     PACKAGE_ICON_256.PNG

SPK_BYTES="$(wc -c < "$SPK" | tr -d ' ')"

if [ "$SPK_BYTES" -lt 10000000 ]; then
    echo "ERROR: final offline SPK is unexpectedly small: $SPK_BYTES bytes"
    exit 1
fi

echo "Built: $SPK"
echo "Size: $SPK_BYTES bytes"
tar -tf "$SPK"
