#!/usr/bin/env bash
# arm64 / Python 3.12 Lambda용 단일 zip을 만든다. 모든 핸들러가 같은 zip을 사용한다.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/build"
PKG="$OUT/pkg"

rm -rf "$PKG" "$OUT/lambda.zip"
mkdir -p "$PKG"

DEPS=("aws-lambda-powertools>=3.0" "slack_sdk>=3.33" "PyYAML>=6.0")

if command -v uv >/dev/null 2>&1; then
  uv pip install --quiet --target "$PKG" \
    --python-platform aarch64-manylinux2014 --python-version 3.12 --only-binary=:all: \
    "${DEPS[@]}"
else
  python3 -m pip install --quiet --target "$PKG" \
    --platform manylinux2014_aarch64 --implementation cp --python-version 3.12 --only-binary=:all: \
    "${DEPS[@]}"
fi

cp -R "$ROOT/src/aha" "$PKG/"
find "$PKG" -name "__pycache__" -type d -prune -exec rm -rf {} +
(cd "$PKG" && zip -qr "$OUT/lambda.zip" .)
echo "built $OUT/lambda.zip"
