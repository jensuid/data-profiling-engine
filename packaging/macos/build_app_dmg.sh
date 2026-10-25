#!/usr/bin/env bash
# M7-T3 macOS packaging track — build the windowed .app, then wrap it in a .dmg
# (SECURITY §6.3, §6.6; ADR-009). No third-party dmg tool is required: the image
# is assembled with the system `hdiutil`, so this runs on a stock dev Mac.
#
# Usage (from anywhere):
#     packaging/macos/build_app_dmg.sh
#
# Optional environment:
#     DPE_APP_NAME      bundle/executable name  (default DataProfilerEngine)
#     DPE_APP_VERSION   version stamp for .dmg naming (default 0.1.0)
#     DPE_SKIP_FRONTEND set 1 to skip the `npm run build` step (reuses an
#                       existing frontend/dist; the spec still fails fast if the
#                       assets are missing).
#
# Output:
#     dist/<APP>.app                 (unsigned, unbundled-by-this-script)
#     dist/<APP>-<VERSION>-macos.dmg (unsigned — sign/notarize via sign_notarize.sh)
#
# This script deliberately does NOT codesign or notarize — credentials stay out
# of the repo/build path (AGENTS §7). Run sign_notarize.sh against the produced
# .dmg in a release environment that has a Developer ID identity.
set -euo pipefail

APP="${DPE_APP_NAME:-DataProfilerEngine}"
VERSION="${DPE_APP_VERSION:-0.1.0}"

# Resolve the repo root from this script's location (packaging/macos/ -> root).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$ROOT"

# 1. Frontend assets must exist before PyInstaller runs (SECURITY §6.6: React
#    builds first). The spec also fails fast on this, but doing it here gives a
#    clear, single build log.
if [[ "${DPE_SKIP_FRONTEND:-0}" != "1" ]]; then
  echo "==> Building React frontend"
  ( cd frontend && npm ci && npm run build )
else
  echo "==> DPE_SKIP_FRONTEND=1 — reusing existing frontend/dist"
fi

# 2. Frozen build (per-OS spec, ADR-009). On macOS this emits a windowed .app.
echo "==> Running PyInstaller"
.venv/bin/python -m PyInstaller --clean --noconfirm data_profiling_engine.spec

APP_PATH="dist/${APP}.app"
if [[ ! -d "$APP_PATH" ]]; then
  echo "ERROR: expected bundle at $APP_PATH not found" >&2
  exit 1
fi

# 3. Assemble a .dmg with hdiutil: a staging volume holding the .app plus a
#    symlink to /Applications so the user can drag-install (SECURITY §6.3).
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
echo "==> Staging dmg contents in $STAGE"
cp -R "$APP_PATH" "$STAGE/"
ln -s /Applications "$STAGE/Applications"

DMG="dist/${APP}-${VERSION}-macos.dmg"
rm -f "$DMG"
echo "==> Creating disk image $DMG"
hdiutil create \
  -volname "$APP" \
  -srcfolder "$STAGE" \
  -fs HFS+ \
  -format UDZO \
  -ov \
  "$DMG"

echo
echo "Done."
echo "  .app  : $APP_PATH"
echo "  .dmg  : $DMG   (UNSIGNED — for local packaged-§5.1 testing only)"
echo
echo "Next for a distributable artifact:"
echo "  packaging/macos/sign_notarize.sh \"$DMG\""
