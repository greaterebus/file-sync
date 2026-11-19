#!/usr/bin/env bash
# Build dist/FileSync-macOS.zip: a standalone, ad-hoc signed FileSync.app.
#
# Usage:  ./build.sh
#
# Build with the python.org "macOS 64-bit universal2" Python 3.12 installer.
# Its binaries support macOS 10.13+ on Intel and Apple Silicon, so the app runs
# on every Mac with macOS 14 or newer. Homebrew Python is compiled for the Mac
# it was installed on, so an app built from it may not open on older macOS;
# this script refuses to package such a build unless ALLOW_HOST_ONLY_BUILD=1.
#
# Environment:
#   PYTHON                 Interpreter used to create .venv (default: python.org 3.12)
#   ALLOW_HOST_ONLY_BUILD  Set to 1 to package a build that only runs on newer macOS
set -euo pipefail
cd "$(dirname "$0")"

APP="dist/FileSync.app"
ZIP="dist/FileSync-macOS.zip"
VENV=".venv"
TARGET_MACOS="14.0"
PYTHON_ORG="/Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12"
PYTHON="${PYTHON:-$([[ -x "$PYTHON_ORG" ]] && echo "$PYTHON_ORG" || command -v python3.12 || true)}"

step() { printf '\n==> %s\n' "$1"; }
fail() { printf '\nERROR: %s\n' "$1" >&2; exit 1; }

[[ -n "$PYTHON" && -x "$PYTHON" ]] || fail "Python 3.12 not found. Install it from https://www.python.org/downloads/macos/ or set PYTHON."

step "Preparing virtual environment ($PYTHON)"
base_prefix() { "$1" -c 'import sys; print(sys.base_prefix)'; }
if [[ -x "$VENV/bin/python" && "$(base_prefix "$VENV/bin/python")" != "$(base_prefix "$PYTHON")" ]]; then
    echo "Recreating $VENV because it was made with a different Python."
    rm -rf "$VENV"
fi
[[ -x "$VENV/bin/python" ]] || "$PYTHON" -m venv "$VENV"
"$VENV/bin/python" -c 'import sys; sys.exit(sys.version_info < (3, 12))' || fail "Python 3.12 or newer is required."
"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/python" -m pip install --quiet -r requirements.txt

step "Running tests"
"$VENV/bin/python" -m pytest -q

step "Building $APP"
rm -rf build dist
"$VENV/bin/python" setup.py py2app >build.log 2>&1 || { tail -40 build.log; fail "py2app failed; see build.log"; }

step "Checking macOS compatibility"
newest=$(find "$APP" -type f \( -name '*.so' -o -name '*.dylib' -o -name Python -o -path '*/Contents/MacOS/*' \) \
    -exec otool -arch all -l {} + 2>/dev/null | awk '/ minos /{print $2}' | sort -V | tail -1)
echo "Newest macOS required by bundled code: $newest (target: $TARGET_MACOS)"
if [[ "$(printf '%s\n' "$TARGET_MACOS" "$newest" | sort -V | tail -1)" != "$TARGET_MACOS" ]]; then
    message="This build needs macOS $newest, so it will not open on macOS $TARGET_MACOS. Build with the python.org universal2 Python 3.12 (see README)."
    [[ "${ALLOW_HOST_ONLY_BUILD:-0}" == "1" ]] || fail "$message"
    echo "WARNING: $message"
fi

step "Ad-hoc code signing"
# Not a Developer ID signature; it only satisfies Apple Silicon's requirement
# that code be signed. Recipients still approve the app on first launch.
codesign --force --deep --sign - "$APP"
codesign --verify --deep --strict "$APP"

step "Packaging $ZIP"
ditto -c -k --sequesterRsrc --keepParent "$APP" "$ZIP"

printf '\nBuilt %s (%s, architectures: %s, requires macOS %s+)\n' \
    "$ZIP" "$(du -h "$ZIP" | cut -f1)" "$(lipo -archs "$APP/Contents/MacOS/FileSync")" "$newest"
