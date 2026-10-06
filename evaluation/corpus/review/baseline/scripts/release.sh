#!/bin/sh
# Build the wheel into a private temporary directory and copy it to dist/.
set -eu
BUILD_DIR="$(mktemp -d)"
trap 'rm -rf "$BUILD_DIR"' EXIT
python -m build --outdir "$BUILD_DIR"
mkdir -p dist
cp "$BUILD_DIR"/*.whl dist/
