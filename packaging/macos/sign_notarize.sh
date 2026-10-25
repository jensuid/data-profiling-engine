#!/usr/bin/env bash
# M7-T3 macOS — codesign (hardened runtime) + notarize + staple (SECURITY §6.3).
#
# Runs ONLY in a release environment that holds a real "Developer ID Application"
# identity. On a machine without one this aborts cleanly rather than producing a
# half-signed artifact. Credentials are read from the environment and are NEVER
# written to the repo, logged, or embedded in a build artifact (AGENTS §7).
#
# Usage:
#     packaging/macos/sign_notarize.sh <path-to-.app-or-.dmg>
#
# Required environment:
#     DPE_SIGN_IDENTITY      e.g. "Developer ID Application: Acme Ltd (TEAMID)"
#     DPE_TEAM_ID            Apple Team ID (10 chars) for notarytool
#     DPE_NOTARIZE_API_KEY   path to an AppStoreConnect .p8  (key-based auth), OR
#     DPE_NOTARIZE_API_KEY_ID / DPE_NOTARIZE_API_ISSUER_ID
#     DPE_NOTARIZE_USER / DPE_NOTARIZE_PWD / DPE_NOTARIZE_ACCOUNT  (Apple-ID auth)
#
# Both auth styles are supported by `notarytool`; the script picks key-based when
# DPE_NOTARIZE_API_KEY is set, otherwise Apple-ID. It never prints their values.
set -euo pipefail

TARGET="${1:?usage: sign_notarize.sh <path-to-.app-or-.dmg>}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENTITLEMENTS="$SCRIPT_DIR/entitlements.plist"

: "${DPE_SIGN_IDENTITY:?DPE_SIGN_IDENTITY must be set to a Developer ID Application identity}"

# Guard against the common CI mistake of running with an env var but no matching
# keychain identity — fail here, not halfway through a deep sign.
if ! security find-identity -v -p codesigning | grep -qF "$DPE_SIGN_IDENTITY"; then
  echo "ERROR: '$DPE_SIGN_IDENTITY' not found in the keychain (codesigning)." >&2
  echo "       Import the Developer ID cert first; nothing was modified." >&2
  exit 1
fi

sign_app() {
  # Deep-sign inner binaries first, then the bundle, with hardened runtime +
  # entitlements (SECURITY §6.3 Gatekeeper requirement).
  echo "==> Codesigning $1"
  find "$1/Contents" \( -name "*.dylib" -o -name "*.so" -o -name "*.framework" \) -print0 \
    | xargs -0 -I{} codesign --force --options runtime --timestamp \
        --sign "$DPE_SIGN_IDENTITY" {}
  codesign --force --deep --options runtime --timestamp \
    --entitlements "$ENTITLEMENTS" \
    --sign "$DPE_SIGN_IDENTITY" "$1"
}

case "$TARGET" in
  *.app)
    APP="$TARGET"
    DMG=""
    ;;
  *.dmg)
    DMG="$TARGET"
    # Extract the .app from the dmg to sign it, then rebuild the signed dmg.
    MOUNT="$(mktemp -d)"
    trap 'rm -rf "$MOUNT"' EXIT
    hdiutil attach -nobrowse -mountpoint "$MOUNT" "$DMG" >/dev/null
    APP="$(find "$MOUNT" -maxdepth 1 -name '*.app' -print -quit)"
    [[ -n "$APP" ]] || { echo "ERROR: no .app inside $DMG" >&2; exit 1; }
    ;;
  *) echo "ERROR: expected a .app or .dmg, got: $TARGET" >&2; exit 1 ;;
esac

sign_app "$APP"
codesign --verify --deep --strict --verbose=2 "$APP"

# Notarize (async submission + wait), then staple the ticket so the artifact
# passes Gatekeeper offline.
notary_creds=()
if [[ -n "${DPE_NOTARIZE_API_KEY:-}" ]]; then
  notary_creds=(--key "${DPE_NOTARIZE_API_KEY}" \
                --key-id "${DPE_NOTARIZE_API_KEY_ID:?needs DPE_NOTARIZE_API_KEY_ID}" \
                --issuer "${DPE_NOTARIZE_API_ISSUER_ID:?needs DPE_NOTARIZE_API_ISSUER_ID}")
else
  notary_creds=(--apple-id "${DPE_NOTARIZE_USER:?}" \
                --password "${DPE_NOTARIZE_PWD:?}" \
                --team-id "${DPE_TEAM_ID:?}")
fi

NOTARY_TARGET="${DMG:-$APP}"
echo "==> Submitting $(basename "$NOTARY_TARGET") for notarization (credentials not logged)"
xcrun notarytool submit "$NOTARY_TARGET" "${notary_creds[@]}" --wait

echo "==> Stapling ticket"
xcrun stapler staple "$APP"
[[ -n "$DMG" ]] && xcrun stapler staple "$DMG"

echo "Done — signed + notarized: $APP${DMG:+ , $DMG}"
