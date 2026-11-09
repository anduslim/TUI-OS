#!/usr/bin/env bash
# Verify an APK the way Android will before CI publishes it.
#
#   tools/verify_apk.sh APP.apk [EXPECTED_CERT_SHA256] [--allow-debug-cert]
#
# Checks, using the SDK build-tools Buildozer downloaded:
#   * zipalign -c -p 4            (page-aligned, required for install)
#   * apksigner verify            (v1/v2 signature valid; v2 required on Android 11)
#   * signer is not the throwaway "Android Debug" certificate
#   * the app is not marked debuggable (aapt2)
#   * optional: the signer is EXACTLY the expected certificate (pinning)
# Prints cert_sha256=<hex> (and appends it to $GITHUB_OUTPUT when set).
set -euo pipefail

APK="${1:?usage: verify_apk.sh APP.apk [EXPECTED_CERT_SHA256] [--allow-debug-cert]}"
EXPECTED="${2:-}"
ALLOW_DEBUG=0
[[ "${3:-}" == "--allow-debug-cert" || "${2:-}" == "--allow-debug-cert" ]] && ALLOW_DEBUG=1
[[ "$EXPECTED" == "--allow-debug-cert" ]] && EXPECTED=""

SDK_BT="${ANDROID_BUILD_TOOLS:-$HOME/.buildozer/android/platform/android-sdk/build-tools}"
BT="$(ls -d "$SDK_BT"/*/ 2>/dev/null | sort -V | tail -1)"
[[ -x "$BT/apksigner" ]] || { echo "::error::apksigner not found under $SDK_BT" >&2; exit 1; }

fail() { echo "::error::$*" >&2; exit 1; }

"$BT/zipalign" -c -p 4 "$APK" || fail "APK is not zip-aligned"

out="$("$BT/apksigner" verify --verbose --print-certs "$APK" 2>&1)" || { echo "$out"; fail "apksigner verify failed"; }
echo "$out"
grep -q "Verified using v2 scheme (APK Signature Scheme v2): true" <<<"$out" \
  || fail "missing APK Signature Scheme v2 (Android 11 requires it)"

# The line prefix varies across build-tools versions ("Signer #1 ...",
# "Signer (minSdkVersion=...) ..."), so match the stable suffixes.
dn="$(grep -m1 -oE 'certificate DN: .*' <<<"$out" | sed 's/^certificate DN: //' || true)"
cert="$(grep -m1 -oiE 'certificate SHA-256 digest: [0-9a-f:]+' <<<"$out" | sed 's/^.*: //' | tr -d ':' | tr 'A-F' 'a-f' || true)"
if [[ -z "$cert" ]]; then
  # Fallback: ask keytool (JDK) for the certificate in the APK.
  cert="$(keytool -printcert -jarfile "$APK" 2>/dev/null | grep -m1 -oE 'SHA256: [0-9A-F:]+' | sed 's/^SHA256: //' | tr -d ':' | tr 'A-F' 'a-f' || true)"
  dn="${dn:-$(keytool -printcert -jarfile "$APK" 2>/dev/null | sed -n 's/^Owner: //p' | head -1 || true)}"
fi
[[ -n "$cert" ]] || fail "could not read the signing certificate"
echo "signer: $dn"
if [[ "$ALLOW_DEBUG" -eq 0 && "$dn" == *"Android Debug"* ]]; then
  fail "signed with the throwaway Android Debug certificate"
fi

if "$BT/aapt2" dump badging "$APK" 2>/dev/null | grep -q "application-debuggable"; then
  fail "APK is debuggable (expected a release build)"
fi

if [[ -n "$EXPECTED" ]]; then
  want="$(tr -d ': ' <<<"$EXPECTED" | tr 'A-F' 'a-f')"
  [[ "$cert" == "$want" ]] || fail "signed by $cert, expected $want (wrong keystore in secrets?)"
  echo "certificate matches ANDROID_CERT_SHA256"
fi

echo "cert_sha256=$cert"
[[ -n "${GITHUB_OUTPUT:-}" ]] && echo "cert_sha256=$cert" >> "$GITHUB_OUTPUT"
echo "OK: $APK is installable and release-signed"
