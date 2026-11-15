# PSX Launcher — Rust for Windows

This is the separate experimental Rust port of the Windows PSX Launcher. The
existing Python version in `../Windows/` remains the stable release.

## Goals

- one native Windows executable without Python, Tkinter or PyInstaller;
- the same `psx_app_launcher.ini` format and location beside the executable;
- compact borderless always-on-top interface;
- hamburger click to collapse and hold/drag to move;
- window position persistence and monitor-boundary clamping;
- one running launcher instance;
- `.exe`, `.lnk`, `.jar`, `.bat`, `.cmd` and PSX-directory launching;
- hidden launch flags, process status, context menus and **Quit apps**;
- one low-priority process inventory at most every five seconds.

## Behavior updates

- Menus use the launcher's dark background, white text and a subtle hover color.
- Menus close 250 ms after the pointer leaves both the triggering button and menu.
- App buttons and the collapsed launcher are narrower; text size and height are unchanged.
- Launch clicks reuse the last background snapshot instead of waiting for a new CIM scan.
- A 12-second per-path guard prevents repeated clicks from starting duplicates.
- Cached status can lag external starts/stops by one refresh round.
- Quit still obtains fresh PIDs, deduplicates matches and verifies the original process
  handle for up to two seconds after a taskkill error before showing that error.
- Permission failures remain visible if process exit cannot be confirmed.
- Windows does not need Unix child reaping. Rust drops the handles returned by
  Command, and explicit verification handles are closed by an RAII guard.
- Shortcuts retain their working-directory lookup; that lookup may still add latency.

## Build

Install Rust with the MSVC toolchain and Microsoft C++ Build Tools, then run:

```powershell
cd Windows-Rust
cargo build --release
```

The executable is written to:

```text
target\release\psx-launcher-rust.exe
```

Copy or rename it to `PSX Launcher.exe`, place `psx_app_launcher.ini` beside
it, and start it normally. If the INI is missing, a minimal default is created.

## Test status

Version `2.0.0-alpha.1` is the first parity port. It must be built and tested on
Windows before it can replace the Python release.

