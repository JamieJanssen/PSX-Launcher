# PSX Launcher

A compact, frameless launcher for Aerowinx PSX and related applications on Windows and macOS.

## Current versions

- Windows (Python): 1.3a
- Windows (Rust): 2.0.0-alpha.1 — experimental
- macOS: 1.4
- macOS (Rust): 2.0.0-alpha.1 — experimental

The separate native Rust port lives in [`Windows-Rust/`](Windows-Rust/). The
existing Python version in `Windows/` remains the stable Windows release.

The separate macOS Rust port lives in [`MacOS-Rust/`](MacOS-Rust/). The
existing Python/Tk version in `MacOS/` remains the stable macOS release.

On macOS, the launcher builds while hidden, applies the native Tk `plain` window style with `noTitleBar` and `texturedSquareCorners` before the first mapping, and then shows the completed launcher immediately. The first background status check starts two seconds after the window is displayed.

macOS 1.4 reduces interface font sizes to 8 pt and adjusts label and settings-control spacing.

## Status monitoring

- Shows whether configured applications are running, including applications started outside the launcher.
- Performs an initial status check when the launcher starts.
- Refreshes process status at most once every five seconds.
- Runs status collection in a background thread so the launcher interface remains responsive.
- Prevents overlapping status checks.
- Uses one shared process snapshot per refresh instead of starting a separate check for every configured application.
- Runs the temporary status commands at reduced CPU priority.

On Windows, one low-priority PowerShell/CIM snapshot supplies the executable paths and command lines for all configured applications.

On macOS, one low-priority `osascript` snapshot supplies bundle identifiers and visibility, while one low-priority `ps` snapshot supplies command lines for loose executables, Java applications and custom `detect=` values.

## Configuration

Both Windows launchers store `psx_app_launcher.ini` beside their executable.

Both macOS launchers store it at:

```text
~/Library/Application Support/PSX Launcher/psx_app_launcher.ini
```

The launcher supports up to two configured paths per button. Use `enabled`, `path1`, `path2`, `hidden1`, `hidden2`, `detect1` and `detect2` in each application section.
