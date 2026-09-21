#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SRC="$ROOT/packaging/synology"
OUT="${1:-$ROOT/dist}"
VERSION="${VERSION:-1.0.1}"
PKG="smartoptimizerui"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

mkdir -p "$OUT" "$STAGE/scripts" "$STAGE/conf" "$STAGE/payload"
sed "s/__VERSION__/$VERSION/g" "$SRC/INFO.template" > "$STAGE/INFO"
cp -a "$SRC/scripts/." "$STAGE/scripts/"
cp -a "$SRC/conf/." "$STAGE/conf/"
cp -a "$SRC/payload/." "$STAGE/payload/"
chmod 755 "$STAGE/scripts/"*

# package.tgz is the payload DSM extracts to /var/packages/<package>/target.
tar -C "$STAGE/payload" -czf "$STAGE/package.tgz" .
rm -rf "$STAGE/payload"

# DSM 7 Package Center icons. Generated here so the repository stays text-only.
python3 - "$STAGE" <<'PY'
import base64, pathlib, struct, zlib, sys
stage = pathlib.Path(sys.argv[1])
def png(path, n):
    # Simple neutral RGBA icon generated with the Python standard library.
    raw = b''.join(b'\x00' + bytes([42,48,60,255]) * n for _ in range(n))
    def chunk(t,d):
        return struct.pack(">I",len(d))+t+d+struct.pack(">I",zlib.crc32(t+d)&0xffffffff)
    data = b'\x89PNG\r\n\x1a\n'
    data += chunk(b'IHDR', struct.pack(">IIBBBBB",n,n,8,6,0,0,0))
    data += chunk(b'IDAT', zlib.compress(raw,9))
    data += chunk(b'IEND', b'')
    path.write_bytes(data)
png(stage/"PACKAGE_ICON.PNG", 64)
png(stage/"PACKAGE_ICON_256.PNG", 256)
PY

SPK="$OUT/SmartOptimizerUI-$VERSION-DSM7.2.1.spk"
tar -C "$STAGE" -cf "$SPK" INFO package.tgz scripts conf PACKAGE_ICON.PNG PACKAGE_ICON_256.PNG

echo "Built: $SPK"
tar -tf "$SPK"
