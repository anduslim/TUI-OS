#!/usr/bin/env bash
# Create the permanent release signing key for the APK and (optionally)
# upload it to GitHub Actions secrets.
#
# Run this ONCE, on your own computer (needs a JDK for `keytool`; the GitHub
# CLI `gh` only if you use --upload). The private key never leaves your
# machine except as encrypted GitHub secrets.
#
#   tools/create_signing_key.sh                 # create ./signing/ files, print next steps
#   tools/create_signing_key.sh --upload        # create and `gh secret set` the 4 secrets
#   tools/create_signing_key.sh --upload --repo anduslim/TUI-OS
#
# KEEP A BACKUP of signing/release.keystore and signing/keystore-password.txt
# (e.g. in a password manager). If the key is lost, installed copies of the
# app can never be updated in place again; you'd have to uninstall first.
set -euo pipefail

ALIAS="olares-workspace-tui"
OUT_DIR="signing"
UPLOAD=0
REPO=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --upload) UPLOAD=1 ;;
    --repo) REPO="$2"; shift ;;
    --out) OUT_DIR="$2"; shift ;;
    -h|--help) sed -n '2,17p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

command -v keytool >/dev/null || { echo "keytool not found: install a JDK (e.g. Temurin 17)" >&2; exit 1; }

KEYSTORE="$OUT_DIR/release.keystore"
PASSFILE="$OUT_DIR/keystore-password.txt"
if [[ -e "$KEYSTORE" ]]; then
  echo "$KEYSTORE already exists; refusing to overwrite your release key." >&2
  echo "Move it away first if you really want a new key (installed apps won't update across keys)." >&2
  exit 1
fi

mkdir -p "$OUT_DIR"
chmod 700 "$OUT_DIR"
umask 077

# PKCS12 keystores use one password for the store and the key.
# (pipefail off here: `head` closing the pipe early is expected, not an error)
PASSWORD="$(set +o pipefail; LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 32)"
[[ ${#PASSWORD} -eq 32 ]] || { echo "could not generate a password" >&2; exit 1; }
printf '%s' "$PASSWORD" > "$PASSFILE"

keytool -genkeypair -v \
  -keystore "$KEYSTORE" -storetype PKCS12 \
  -storepass:file "$PASSFILE" -keypass:file "$PASSFILE" \
  -alias "$ALIAS" -keyalg RSA -keysize 4096 -validity 10000 \
  -dname "CN=Olares Workspace TUI, O=TUI-OS" >/dev/null

FINGERPRINT="$(keytool -list -v -keystore "$KEYSTORE" -storepass:file "$PASSFILE" -alias "$ALIAS" \
  | sed -n 's/^[[:space:]]*SHA256: //p')"

echo "Created $KEYSTORE (alias $ALIAS, RSA 4096, valid ~27 years)"
echo "Password in $PASSFILE"
echo "Certificate SHA-256: $FINGERPRINT"
echo

if [[ "$UPLOAD" -eq 1 ]]; then
  command -v gh >/dev/null || { echo "gh not found: install the GitHub CLI or add the secrets by hand (below)" >&2; exit 1; }
  R=(); [[ -n "$REPO" ]] && R=(--repo "$REPO")
  base64 < "$KEYSTORE" | tr -d '\n' | gh secret set ANDROID_KEYSTORE_BASE64 "${R[@]}"
  gh secret set ANDROID_KEYSTORE_PASSWORD "${R[@]}" < "$PASSFILE"
  printf '%s' "$ALIAS" | gh secret set ANDROID_KEY_ALIAS "${R[@]}"
  gh secret set ANDROID_KEY_PASSWORD "${R[@]}" < "$PASSFILE"
  printf '%s' "$FINGERPRINT" | gh secret set ANDROID_CERT_SHA256 "${R[@]}"
  echo "Uploaded 5 secrets. The next CI build on main is signed with this key."
else
  cat <<EOF
Next: add these repository secrets (GitHub → Settings → Secrets and variables → Actions),
or rerun with --upload to do it with the GitHub CLI:

  ANDROID_KEYSTORE_BASE64    contents of:  base64 < $KEYSTORE | tr -d '\\n'
  ANDROID_KEYSTORE_PASSWORD  contents of:  $PASSFILE
  ANDROID_KEY_ALIAS          $ALIAS
  ANDROID_KEY_PASSWORD       contents of:  $PASSFILE   (same as the store password)
  ANDROID_CERT_SHA256        $FINGERPRINT   (optional: CI refuses APKs signed by any other key)
EOF
fi
echo
echo "Back up $OUT_DIR/ now. It is git-ignored and must never be committed."
