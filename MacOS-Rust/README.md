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
