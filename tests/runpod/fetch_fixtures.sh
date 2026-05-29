#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FIX="$DIR/fixtures"
BASE="https://github.com/facefusion/facefusion-assets/releases/download/examples-3.0.0"

mkdir -p "$FIX"

if [ ! -f "$FIX/source.jpg" ]; then
  echo "Downloading source.jpg"
  curl -fSL "$BASE/source.jpg" -o "$FIX/source.jpg"
fi

if [ ! -f "$FIX/target-240p.mp4" ]; then
  echo "Downloading target-240p.mp4"
  curl -fSL "$BASE/target-240p.mp4" -o "$FIX/target-240p.mp4"
fi

if [ ! -f "$FIX/source.b64" ]; then
  echo "Encoding source.b64"
  if base64 --help 2>&1 | grep -q -- '-w'; then
    base64 -w0 "$FIX/source.jpg" > "$FIX/source.b64"
  else
    base64 -i "$FIX/source.jpg" | tr -d '\n' > "$FIX/source.b64"
  fi
fi

echo "Fixtures ready in $FIX"
