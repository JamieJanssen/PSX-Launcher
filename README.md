# PSX Launcher

A compact, frameless launcher for Aerowinx PSX and related applications on Windows and macOS.

## Current versions

- Windows: 1.3a
- macOS: 1.3d

macOS 1.3d restores the original 1.3 window initialization. The first background status check is scheduled only after macOS has displayed the borderless launcher, then starts two seconds later.

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

Windows stores `psx_app_launcher.ini` beside the launcher.

macOS stores it at:

```text
~/Library/Application Support/PSX Launcher/psx_app_launcher.ini
```

The launcher supports up to two configured paths per button. Use `enabled`, `path1`, `path2`, `hidden1`, `hidden2`, `detect1` and `detect2` in each application section.
