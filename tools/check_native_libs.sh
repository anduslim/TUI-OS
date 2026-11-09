#!/usr/bin/env bash
# Check that every Python extension module in the APK links libpython.
#
#   tools/check_native_libs.sh APP.apk
#
# Android's loader does not let an extension borrow symbols from an already
# loaded libpython: each .so must list libpython3.X.so as NEEDED, or importing
# it fails on the device with
#   dlopen failed: cannot locate symbol "PyExc_TypeError"
# (this is how cryptography's Rust extension broke SSH up to v1.1.4).
#
# Modules the app needs (SSH: cryptography, cffi) fail the check. Others only
# warn: they come in as dependencies of Kivy/requests and have pure-Python
# fallbacks (wcwidth) or are never imported by TUI-OS (chardet).
set -euo pipefail

REQUIRED='site-packages/(cryptography/|_cffi_backend)'

APK="${1:?usage: check_native_libs.sh APP.apk}"
work="$(mktemp -d)"
trap 'chmod -R u+rwX "$work" 2>/dev/null; rm -rf "$work"' EXIT

READELF="$(command -v readelf || command -v llvm-readelf || true)"
if [[ -z "$READELF" ]]; then
  READELF="$(ls "$HOME"/.buildozer/android/platform/android-ndk-*/toolchains/llvm/prebuilt/*/bin/llvm-readelf 2>/dev/null | head -1)"
fi
[[ -n "$READELF" ]] || { echo "::error::readelf not found" >&2; exit 1; }

unzip -q -o "$APK" 'lib/*/libpybundle.so' -d "$work"
bundle="$(ls "$work"/lib/*/libpybundle.so | head -1)"
mkdir "$work/py"
tar -xf "$bundle" -C "$work/py"
# The bundle stores directories without the owner's x bit; as a non-root
# user (CI) find can't enter them until they are made traversable.
chmod -R u+rwX "$work/py"

status=0
checked=0
while IFS= read -r -d '' so; do
  # Only extensions that import Python's C API need the link.
  # Capture first: grep -q exiting early would SIGPIPE readelf (pipefail).
  syms="$("$READELF" --dyn-syms -W "$so")"
  if grep -qE ' UND +(Py|_Py)' <<<"$syms"; then
    checked=$((checked + 1))
    rel="${so#"$work/py/"}"
    if grep -qE 'NEEDED.*libpython3' <<<"$("$READELF" -d "$so")"; then
      echo "ok       $rel"
    elif [[ "$rel" =~ $REQUIRED ]]; then
      echo "::error::$rel uses the Python C API but does not link libpython (dlopen will fail on the device)"
      status=1
    else
      echo "::warning::$rel does not link libpython (not imported by TUI-OS, or has a fallback)"
    fi
  fi
done < <(find "$work/py" -name '*.so' -print0)

echo "checked $checked extension module(s)"
[[ $checked -gt 0 ]] || { echo "::error::no extension modules found in $bundle"; exit 1; }
[[ -n "$(find "$work/py" -path '*cryptography/hazmat/bindings/_rust*.so')" ]] \
  || { echo "::error::cryptography's _rust extension is missing from the APK"; exit 1; }
exit $status
