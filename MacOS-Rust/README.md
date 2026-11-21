# PSX Launcher — Rust for macOS

This is the separate experimental native Rust port. The stable Python/Tk macOS
version in `../MacOS/` remains untouched.

## Build on Apple silicon

Install Rust and the Xcode Command Line Tools, then run:

```bash
cd MacOS-Rust
chmod +x build-app.sh
./build-app.sh
```

The app is written to `~/Applications/PSX Launcher Rust.app`. Cargo's build
cache is kept outside the repository in
`~/Library/Caches/PSX-Launcher-Rust/target`.

For one universal Apple-silicon + Intel bundle:

```bash
./build-app.sh --universal
```

The working INI remains compatible with the Python launcher and is stored at:

```text
~/Library/Application Support/PSX Launcher/psx_app_launcher.ini
```

Version `2.0.0-alpha.1` must be built and tested on macOS before it can replace
the stable Python release.

## Menu bar mode

The menu bar uses the repository's `psx.icns` with a small status badge.
Click the hamburger to hide the full bar, without switching to compact mode.
Dragging the hamburger still moves the launcher. Click the menu bar icon to
restore the full bar at its saved position.

- Green: every configured target in enabled sections is running.
- Orange: at least one target runs, but the set is incomplete.
- Gray: none are running, or the first status check has not finished.
- Red: an application action failed.
- Empty/disabled sections do not affect the aggregate status.

Status collection continues in one non-overlapping background worker while
hidden, at the existing five-second minimum interval. Starting applications
still uses cached process information. The initial check starts after two seconds.

The build converts `psx.icns` to an embedded PNG using macOS's built-in
`/usr/bin/sips`; no additional image tools are required. No INI migration is
needed. Windows and the Python launchers are unchanged.

Run `cargo test` on macOS to test the badge aggregation rules.
