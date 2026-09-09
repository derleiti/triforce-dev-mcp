#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
DEB="${1:?usage: render-arch-pkgbuild.sh BACKEND_DEB [OUTPUT_DIR] [SOURCE_URL]}"
OUT="${2:-$ROOT/dist/arch}"
SOURCE_URL="${3:-$(basename "$DEB")}"
mkdir -p "$OUT"
SHA="$(sha256sum "$DEB" | awk '{print $1}')"
sed -e "s#@SOURCE@#$SOURCE_URL#g" -e "s#@SHA256@#$SHA#g" \
  "$ROOT/packaging/arch/PKGBUILD.in" > "$OUT/PKGBUILD"
cp "$ROOT/packaging/arch/triforce.install" "$OUT/triforce.install"
printf 'Rendered %s (sha256=%s)\n' "$OUT/PKGBUILD" "$SHA"
