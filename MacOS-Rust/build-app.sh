#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CARGO_TARGET_DIR="${CARGO_TARGET_DIR:-$HOME/Library/Caches/PSX-Launcher-Rust/target}"
APP_DIR="${PSX_LAUNCHER_APP_DIR:-$HOME/Applications/PSX Launcher Rust.app}"
MODE="${1:-native}"

export CARGO_TARGET_DIR
cd "$SCRIPT_DIR"

if [[ "$MODE" == "--universal" ]]; then
    rustup target add aarch64-apple-darwin x86_64-apple-darwin
    cargo build --release --target aarch64-apple-darwin
    cargo build --release --target x86_64-apple-darwin
    BINARY_DIR="$(mktemp -d)"
    trap 'rm -rf "$BINARY_DIR"' EXIT
    lipo -create \
        "$CARGO_TARGET_DIR/aarch64-apple-darwin/release/psx-launcher-macos-rust" \
        "$CARGO_TARGET_DIR/x86_64-apple-darwin/release/psx-launcher-macos-rust" \
        -output "$BINARY_DIR/PSX Launcher"
    BINARY="$BINARY_DIR/PSX Launcher"
else
    cargo build --release
    BINARY="$CARGO_TARGET_DIR/release/psx-launcher-macos-rust"
fi

rm -rf "$APP_DIR"
mkdir -p "$APP_DIR/Contents/MacOS" "$APP_DIR/Contents/Resources"
cp "$BINARY" "$APP_DIR/Contents/MacOS/PSX Launcher"
cp "$SCRIPT_DIR/Info.plist" "$APP_DIR/Contents/Info.plist"
cp "$SCRIPT_DIR/psx.icns" "$APP_DIR/Contents/Resources/psx.icns"
cp "$SCRIPT_DIR/psx_app_launcher.ini" "$APP_DIR/Contents/Resources/psx_app_launcher.ini"
chmod +x "$APP_DIR/Contents/MacOS/PSX Launcher"

echo "Built: $APP_DIR"
