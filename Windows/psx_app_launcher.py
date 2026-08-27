#!/usr/bin/env python3
"""
PSX App Launcher for Windows
Version 1.2f

Compact frameless launcher for Aerowinx PSX and related applications.
Launches configured application paths only; no arbitrary command execution.
"""

from __future__ import annotations

import configparser
import ctypes
import json
import os
import re
import subprocess
import sys
import time
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import messagebox

APP_NAME = "PSX App Launcher"
APP_VERSION = "1.2f"

BG = "#17191c"
PANEL = "#22252a"
PANEL_HOVER = "#2d3137"
TEXT = "#f2f3f5"
MUTED = "#969ca5"
OFF_DOT = "#626870"
GREEN = "#54c878"
ORANGE = "#e7a84b"
RED = "#df6464"

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
CREATE_NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010)


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


BASE_DIR = app_dir()
CONFIG_PATH = BASE_DIR / "psx_app_launcher.ini"


DEFAULT_CONFIG = {
    "Launcher": {
        "always_on_top": "true",
        "x": "80",
        "y": "80",
    },
    "PSX": {
        "enabled": "true",
        "hidden1": "false",
        "hidden2": "false",
        "path1": r"C:\Games\Aerowinx",
        "detect1": "Aerowinx.jar",
        "path2": "",
        "detect2": "",
    },
}


@dataclass
class LauncherItem:
    section: str
    label: str
    paths: list[tuple[Path, bool, str]]


@dataclass
class WindowsProcess:
    pid: int
    name: str
    executable_path: str
    command_line: str


class LaunchError(RuntimeError):
    pass


def _safe_getboolean(
    config: configparser.ConfigParser,
    section: str,
    option: str,
    fallback: bool,
) -> bool:
    try:
        return config.getboolean(section, option, fallback=fallback)
    except (ValueError, configparser.Error):
        return fallback


def _new_config_parser() -> configparser.ConfigParser:
    # Disable interpolation so Windows values containing %NAME% remain valid.
    config = configparser.ConfigParser(interpolation=None)
    config.optionxform = str
    return config


def ensure_config() -> configparser.ConfigParser:
    config = _new_config_parser()

    # Only create an INI when none exists. Existing INI files are never
    # rewritten, migrated, normalized or supplemented automatically.
    if not CONFIG_PATH.exists():
        for section, values in DEFAULT_CONFIG.items():
            config[section] = values
        try:
            with CONFIG_PATH.open("x", encoding="utf-8") as handle:
                config.write(handle)
        except FileExistsError:
            pass
        except OSError as exc:
            raise RuntimeError(
                f"Kan configuratie niet aanmaken:\n{CONFIG_PATH}\n\n{exc}"
            ) from exc

    config = _new_config_parser()
    try:
        with CONFIG_PATH.open("r", encoding="utf-8-sig") as handle:
            config.read_file(handle)
    except (OSError, UnicodeError, configparser.Error) as exc:
        raise RuntimeError(f"Kan configuratie niet lezen:\n{CONFIG_PATH}\n\n{exc}") from exc

    return config

def save_launcher_position(x: int, y: int) -> None:
    """Update only x and y in [Launcher], preserving the existing INI."""
    try:
        with CONFIG_PATH.open("r+", encoding="utf-8-sig", newline="") as handle:
            text = handle.read()
            newline = "\r\n" if "\r\n" in text else "\n"
            lines = text.splitlines(keepends=True)

            section_pattern = re.compile(
                r"^\s*\[([^]]+)\]\s*(?:[;#].*)?(?:\r?\n)?$",
                re.IGNORECASE,
            )
            value_pattern = re.compile(
                r"^(\s*)(x|y)(\s*=\s*).*(\r?\n)?$",
                re.IGNORECASE,
            )

            launcher_start = None
            launcher_end = len(lines)
            for index, line in enumerate(lines):
                match = section_pattern.match(line)
                if not match:
                    continue
                if match.group(1).strip().lower() == "launcher":
                    launcher_start = index
                    for later in range(index + 1, len(lines)):
                        if section_pattern.match(lines[later]):
                            launcher_end = later
                            break
                    break

            if launcher_start is None:
                prefix = "" if not text or text.endswith(("\n", "\r")) else newline
                lines.extend([
                    prefix + "[Launcher]" + newline,
                    f"x = {x}{newline}",
                    f"y = {y}{newline}",
                ])
            else:
                found = {"x": False, "y": False}
                for index in range(launcher_start + 1, launcher_end):
                    match = value_pattern.match(lines[index])
                    if not match:
                        continue
                    key = match.group(2).lower()
                    ending = match.group(4) or newline
                    value = x if key == "x" else y
                    lines[index] = (
                        f"{match.group(1)}{match.group(2)}"
                        f"{match.group(3)}{value}{ending}"
                    )
                    found[key] = True

                additions = []
                if not found["x"]:
                    additions.append(f"x = {x}{newline}")
                if not found["y"]:
                    additions.append(f"y = {y}{newline}")
                if additions:
                    lines[launcher_end:launcher_end] = additions

            handle.seek(0)
            handle.write("".join(lines))
            handle.truncate()
            handle.flush()
            os.fsync(handle.fileno())
    except (OSError, UnicodeError):
        pass



def configured_items(config: configparser.ConfigParser) -> list[LauncherItem]:
    items: list[LauncherItem] = []

    for section in config.sections():
        if section.lower() == "launcher":
            continue
        if not _safe_getboolean(config, section, "enabled", True):
            continue

        label = config.get(section, "label", fallback=section).strip() or section
        paths: list[tuple[Path, bool, str]] = []

        for index, key in enumerate(("path1", "path2"), start=1):
            try:
                value = config.get(section, key, fallback="").strip().strip('"')
                if not value:
                    continue
                expanded = os.path.expandvars(os.path.expanduser(value))
                hidden = _safe_getboolean(config, section, f"hidden{index}", False)
                detection = config.get(
                    section, f"detect{index}", fallback=""
                ).strip()
                paths.append((Path(expanded), hidden, detection))
            except Exception:
                continue

        items.append(LauncherItem(section, label, paths))

    return items


def _powershell_executable() -> str:
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    preferred = Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    return str(preferred) if preferred.exists() else "powershell.exe"


_PROCESS_CACHE: tuple[float, list[WindowsProcess]] = (0.0, [])


def windows_processes(force: bool = False) -> list[WindowsProcess]:
    """Read Windows processes using CIM, including path and command line."""
    global _PROCESS_CACHE

    now = time.monotonic()
    cached_at, cached = _PROCESS_CACHE
    if not force and now - cached_at < 0.75:
        return cached

    script = (
        "$ErrorActionPreference='SilentlyContinue';"
        "Get-CimInstance Win32_Process | "
        "Select-Object ProcessId,Name,ExecutablePath,CommandLine | "
        "ConvertTo-Json -Compress"
    )

    try:
        result = subprocess.run(
            [
                _powershell_executable(),
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            creationflags=CREATE_NO_WINDOW,
        )
        if result.returncode != 0 or not result.stdout.strip():
            _PROCESS_CACHE = (now, [])
            return []

        raw = json.loads(result.stdout)
        if isinstance(raw, dict):
            raw = [raw]

        processes: list[WindowsProcess] = []
        for entry in raw if isinstance(raw, list) else []:
            try:
                processes.append(
                    WindowsProcess(
                        pid=int(entry.get("ProcessId", 0)),
                        name=str(entry.get("Name") or ""),
                        executable_path=str(entry.get("ExecutablePath") or ""),
                        command_line=str(entry.get("CommandLine") or ""),
                    )
                )
            except (TypeError, ValueError, AttributeError):
                continue

        _PROCESS_CACHE = (now, processes)
        return processes
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        _PROCESS_CACHE = (now, [])
        return []


def _normalized_windows_path(path: Path | str) -> str:
    try:
        text = str(Path(path).resolve())
    except OSError:
        text = os.path.abspath(str(path))
    return os.path.normcase(os.path.normpath(text))


def _contains_casefold(haystack: str, needle: str) -> bool:
    return needle.casefold() in haystack.casefold()


def matching_processes(path: Path, detection: str = "") -> list[WindowsProcess]:
    processes = windows_processes()

    if detection:
        return [
            process
            for process in processes
            if _contains_casefold(process.command_line, detection)
        ]

    target = _normalized_windows_path(path)
    suffix = path.suffix.lower()

    if suffix == ".exe":
        exact = [
            process
            for process in processes
            if process.executable_path
            and _normalized_windows_path(process.executable_path) == target
        ]
        if exact:
            return exact

        # ExecutablePath may be unavailable for elevated processes. Fall back to
        # the exact executable filename, never a substring search.
        return [
            process
            for process in processes
            if process.name.casefold() == path.name.casefold()
        ]

    # Scripts, JAR files and shortcuts normally run under another host process.
    # Match the complete configured path in that host's command line.
    matches = []
    for process in processes:
        command = process.command_line
        if not command:
            continue
        normalized_command = os.path.normcase(command.replace("/", "\\"))
        if target in normalized_command:
            matches.append(process)
    return matches


def is_probably_running_path(path: Path, detection: str = "") -> bool:
    if not detection and not path.exists():
        return False
    return bool(matching_processes(path, detection))


def launch_psx(psx_root: Path, hidden: bool = False) -> None:
    """Start Aerowinx.jar with its PSX root as the working directory."""
    if not psx_root.is_dir():
        raise LaunchError(f"PSX-map niet gevonden:\n{psx_root}")

    try:
        subprocess.Popen(
            [
                "java.exe",
                "-Xmx500m",
                "-Djava.library.path=Interfaces",
                "-jar",
                "Aerowinx.jar",
            ],
            cwd=str(psx_root),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL if hidden else None,
            stderr=subprocess.DEVNULL if hidden else None,
            creationflags=CREATE_NO_WINDOW if hidden else 0,
        )
    except OSError as exc:
        raise LaunchError(f"Kan PSX niet starten:\n{psx_root}\n\n{exc}") from exc



def _shortcut_working_directory(path: Path) -> Path | None:
    """Read the shortcut's Start in directory without changing the shortcut."""
    script = (
        "$shortcut = (New-Object -ComObject WScript.Shell)"
        ".CreateShortcut($env:PSX_LAUNCHER_SHORTCUT);"
        "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8;"
        "$shortcut.WorkingDirectory"
    )
    environment = os.environ.copy()
    environment["PSX_LAUNCHER_SHORTCUT"] = str(path)

    try:
        result = subprocess.run(
            [
                _powershell_executable(),
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            creationflags=CREATE_NO_WINDOW,
            env=environment,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    working_directory = os.path.expandvars(result.stdout.strip())
    if result.returncode != 0 or not working_directory:
        return None

    directory = Path(working_directory)
    return directory if directory.is_dir() else None


def _launch_windows_shortcut(path: Path) -> None:
    """Open a shortcut with its Start in directory and inherited PSX environment."""
    working_directory = _shortcut_working_directory(path)
    old_path = os.environ.get("PATH", "")

    try:
        if working_directory is not None:
            interfaces_directory = working_directory / "Interfaces"
            if interfaces_directory.is_dir():
                os.environ["PATH"] = (
                    str(interfaces_directory)
                    + (os.pathsep + old_path if old_path else "")
                )

        result = ctypes.windll.shell32.ShellExecuteW(
            None,
            "open",
            str(path),
            None,
            str(working_directory) if working_directory is not None else None,
            1,
        )
        if result <= 32:
            raise OSError(f"ShellExecuteW failed with code {result}")
    finally:
        os.environ["PATH"] = old_path

def launch_path(path: Path, hidden: bool = False) -> None:
    if not path.exists():
        raise LaunchError(f"Niet gevonden:\n{path}")

    suffix = path.suffix.lower()
    try:
        if suffix == ".lnk":
            _launch_windows_shortcut(path)
            return

        if suffix == ".exe":
            if hidden:
                result = ctypes.windll.shell32.ShellExecuteW(
                    None,
                    "open",
                    str(path),
                    None,
                    str(path.parent),
                    0,
                )
                if result <= 32:
                    raise OSError(f"ShellExecuteW failed with code {result}")
            else:
                os.startfile(str(path))  # type: ignore[attr-defined]
            return

        if suffix in {".bat", ".cmd"}:
            subprocess.Popen(
                [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", str(path)],
                cwd=str(path.parent),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL if hidden else None,
                stderr=subprocess.DEVNULL if hidden else None,
                creationflags=CREATE_NO_WINDOW if hidden else CREATE_NEW_CONSOLE,
            )
            return

        if suffix == ".jar":
            os.startfile(
                str(path),
                cwd=str(path.parent),
            )
            return

        # Shortcuts, documents and other registered file types are opened using
        # their normal Windows file association.
        os.startfile(str(path))  # type: ignore[attr-defined]
    except OSError as exc:
        raise LaunchError(f"Kan niet starten:\n{path}\n\n{exc}") from exc


def quit_path(path: Path, detection: str = "") -> None:
    """Terminate only PIDs matching this exact path or custom detection text."""
    matches = matching_processes(path, detection)
    own_pid = os.getpid()

    for process in matches:
        if process.pid <= 0 or process.pid == own_pid:
            continue
        subprocess.run(
            ["taskkill.exe", "/PID", str(process.pid), "/T"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=4,
            creationflags=CREATE_NO_WINDOW,
        )

    # Prevent a stale cached process list from keeping the status green.
    windows_processes(force=True)


class UtilityButton(tk.Frame):
    def __init__(self, master: tk.Misc, item: LauncherItem, command, menu_command) -> None:
        super().__init__(master, bg=PANEL, cursor="hand2", highlightthickness=0)
        self.item = item
        self.command = command
        self.menu_command = menu_command
        self.state = "off"

        self.dot = tk.Canvas(self, width=10, height=10, bg=PANEL, highlightthickness=0, bd=0)
        self.dot_id = self.dot.create_oval(1, 1, 9, 9, fill=OFF_DOT, outline="")
        self.dot.pack(side="left", padx=(10, 6), pady=7)

        self.label = tk.Label(
            self,
            text=item.label,
            bg=PANEL,
            fg=TEXT,
            font=("Segoe UI", 9),
            padx=0,
            pady=0,
            anchor="center",
        )
        self.label.pack(side="left", padx=(0, 10), pady=(5, 7))

        for widget in (self, self.dot, self.label):
            widget.bind("<Button-1>", self._clicked)
            widget.bind("<Button-3>", self._menu_clicked)
            widget.bind("<Enter>", self._enter)
            widget.bind("<Leave>", self._leave)

    def _clicked(self, _event=None) -> None:
        self.command(self.item)

    def _menu_clicked(self, event=None) -> str:
        self.menu_command(self.item, event)
        return "break"

    def _enter(self, _event=None) -> None:
        self.configure(bg=PANEL_HOVER)
        self.dot.configure(bg=PANEL_HOVER)
        self.label.configure(bg=PANEL_HOVER)

    def _leave(self, _event=None) -> None:
        self.configure(bg=PANEL)
        self.dot.configure(bg=PANEL)
        self.label.configure(bg=PANEL)

    def set_status(self, state: str) -> None:
        self.state = state
        colour = {
            "off": OFF_DOT,
            "partial": ORANGE,
            "running": GREEN,
            "error": RED,
        }.get(state, OFF_DOT)
        self.dot.itemconfigure(self.dot_id, fill=colour)


class PSXLauncher(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.config_data = ensure_config()
        self.items = configured_items(self.config_data)
        self.buttons: dict[str, UtilityButton] = {}
        self.launching_paths: dict[str, float] = {}
        self.always_on_top = _safe_getboolean(
            self.config_data, "Launcher", "always_on_top", True
        )
        self.collapsed = False
        self._closing = False
        self._menu_open = False
        self._status_after_id: str | None = None

        self.title(f"{APP_NAME} {APP_VERSION}")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.overrideredirect(True)

        self.shell = tk.Frame(self, bg=BG, highlightthickness=1, highlightbackground="#30343a")
        self.shell.pack(fill="both", expand=True)

        self._drag_origin_x = 0
        self._drag_origin_y = 0
        self._drag_moved = False

        self.drag_handle = tk.Frame(self.shell, bg=BG, width=26, cursor="fleur")
        self.drag_handle.pack(side="left", fill="y")
        self.drag_handle.pack_propagate(False)
        self.drag_handle.bind("<ButtonPress-1>", self._start_drag)
        self.drag_handle.bind("<B1-Motion>", self._drag_window)
        self.drag_handle.bind("<Double-Button-1>", lambda _event: self.collapse())

        self.content = tk.Frame(self.shell, bg=BG)
        self.content.pack(side="left", fill="both", expand=True)

        for index, item in enumerate(self.items):
            button = UtilityButton(self.content, item, self.launch_item, self.show_item_menu)
            button.grid(row=0, column=index, padx=(0 if index == 0 else 2, 0), sticky="nsew")
            self.buttons[item.section] = button

        self.settings = tk.Label(
            self.content,
            text="⋯",
            bg=BG,
            fg=MUTED,
            font=("Segoe UI", 11),
            cursor="hand2",
            padx=5,
        )
        self.settings.grid(row=0, column=len(self.items), padx=(2, 0), sticky="ns")
        self.settings.bind("<Button-1>", self.show_settings_menu)
        self.settings.bind("<Button-3>", self.show_settings_menu)
        self.settings.bind("<Enter>", lambda _event: self.settings.configure(fg=TEXT))
        self.settings.bind("<Leave>", lambda _event: self.settings.configure(fg=MUTED))

        self.item_menus: dict[str, tk.Menu] = {}
        for item in self.items:
            menu = tk.Menu(self, tearoff=0)
            menu.add_command(
                label="Quit apps",
                command=lambda current=item: self._run_menu_action(
                    lambda: self.quit_item(current)
                ),
            )
            self.item_menus[item.section] = menu

        self.settings_menu = tk.Menu(self, tearoff=0)
        self.settings_menu.add_command(
            label="Edit INI",
            command=lambda: self._run_menu_action(self.open_config),
        )
        self.settings_menu.add_command(
            label=f"About ({APP_VERSION})",
            command=lambda: self._run_menu_action(self.show_about),
        )
        self.settings_menu.add_separator()
        self.settings_menu.add_command(
            label="Quit",
            command=lambda: self._run_menu_action(self.request_close),
        )

        self.mini = tk.Frame(self.shell, bg=PANEL, cursor="hand2")
        self.mini_dot = tk.Canvas(
            self.mini, width=10, height=10, bg=PANEL, highlightthickness=0, bd=0
        )
        self.mini_dot_id = self.mini_dot.create_oval(
            1, 1, 9, 9, fill=OFF_DOT, outline=""
        )
        self.mini_dot.pack(side="left", padx=(9, 5), pady=7)
        self.mini_label = tk.Label(
            self.mini,
            text="PSX",
            bg=PANEL,
            fg=TEXT,
            font=("Segoe UI", 9),
            padx=0,
            pady=0,
        )
        self.mini_label.pack(side="left", padx=(0, 9), pady=(5, 7))
        for widget in (self.mini, self.mini_dot, self.mini_label):
            widget.bind("<ButtonPress-1>", self._start_drag)
            widget.bind("<B1-Motion>", self._drag_window)
            widget.bind("<ButtonRelease-1>", self._mini_released)

        x = self.config_data.getint("Launcher", "x", fallback=80)
        y = self.config_data.getint("Launcher", "y", fallback=80)
        self.geometry(f"+{x}+{y}")

        self.protocol("WM_DELETE_WINDOW", self.request_close)
        self.bind("<Escape>", lambda _event: self.request_close())
        self.bind("<Control-q>", lambda _event: self.request_close())
        self.bind("<Map>", lambda _event: self._apply_topmost())
        self.bind("<FocusIn>", lambda _event: self._apply_topmost())

        self.after_idle(self._apply_topmost)
        self.after(200, self._apply_topmost)
        self.after(1000, self._maintain_topmost)
        self._schedule_status_poll(500)

    def _apply_topmost(self) -> None:
        if not self.always_on_top or self._closing:
            return
        try:
            self.attributes("-topmost", True)
            self.lift()
        except tk.TclError:
            pass

    def _maintain_topmost(self) -> None:
        if self._closing:
            return
        self._apply_topmost()
        self.after(2000, self._maintain_topmost)

    def _start_drag(self, event) -> None:
        self._drag_origin_x = event.x_root - self.winfo_x()
        self._drag_origin_y = event.y_root - self.winfo_y()
        self._drag_moved = False

    def _drag_window(self, event) -> None:
        x = event.x_root - self._drag_origin_x
        y = event.y_root - self._drag_origin_y
        if abs(x - self.winfo_x()) > 2 or abs(y - self.winfo_y()) > 2:
            self._drag_moved = True
        self.geometry(f"+{x}+{y}")

    def _mini_released(self, _event=None) -> None:
        if not self._drag_moved:
            self.expand()

    def collapse(self) -> None:
        if self.collapsed:
            return
        self.collapsed = True
        self.drag_handle.pack_forget()
        self.content.pack_forget()
        self.mini.pack(side="left", fill="both")
        self.update_idletasks()
        self.geometry("")
        self._apply_topmost()

    def expand(self) -> None:
        if not self.collapsed:
            return
        self.collapsed = False
        self.mini.pack_forget()
        self.drag_handle.pack(side="left", fill="y")
        self.content.pack(side="left", fill="both", expand=True)
        self.update_idletasks()
        self.geometry("")
        self._apply_topmost()

    def _show_nonfatal_error(self, text: str) -> None:
        if self._closing:
            return
        try:
            messagebox.showerror(APP_NAME, text, parent=self)
        except Exception:
            print(f"[{APP_NAME}] {text}", file=sys.stderr)
        finally:
            self._apply_topmost()

    def _path_key(self, path: Path) -> str:
        return _normalized_windows_path(path)

    def _is_launch_locked(self, path: Path) -> bool:
        key = self._path_key(path)
        started_at = self.launching_paths.get(key)
        if started_at is None:
            return False
        if time.monotonic() - started_at <= 12.0:
            return True
        self.launching_paths.pop(key, None)
        return False

    def _mark_launching(self, path: Path) -> None:
        self.launching_paths[self._path_key(path)] = time.monotonic()

    def _cancel_status_poll(self) -> None:
        if self._status_after_id is None:
            return
        try:
            self.after_cancel(self._status_after_id)
        except tk.TclError:
            pass
        self._status_after_id = None

    def _schedule_status_poll(self, delay_ms: int = 5000) -> None:
        if self._closing:
            return
        self._cancel_status_poll()
        self._status_after_id = self.after(delay_ms, self.refresh_status)

    def launch_item(self, item: LauncherItem) -> None:
        try:
            self._launch_item_safe(item)
        except Exception as exc:
            button = self.buttons.get(item.section)
            if button is not None:
                button.set_status("error")
            self._show_nonfatal_error(
                f"Kan {item.label} niet starten.\n\n{exc}\n\n"
                f"Controleer de configuratie in:\n{CONFIG_PATH}"
            )

    def _launch_item_safe(self, item: LauncherItem) -> None:
        button = self.buttons[item.section]

        if not item.paths:
            button.set_status("error")
            self._show_nonfatal_error(
                "Voor deze knop is nog niets ingesteld.\n\n"
                f"Vul path1 of path2 in:\n{CONFIG_PATH}"
            )
            return

        errors: list[str] = []
        launched = False
        already_running = False

        windows_processes(force=True)
        for path, hidden, detection in item.paths:
            if not path.exists():
                errors.append(f"Niet gevonden: {path}")
                continue
            if self._is_launch_locked(path) or is_probably_running_path(path, detection):
                already_running = True
                continue
            try:
                self._mark_launching(path)
                if item.section.casefold() == "psx" and path.is_dir():
                    launch_psx(path, hidden=hidden)
                else:
                    launch_path(path, hidden=hidden)
                launched = True
            except Exception as exc:
                errors.append(f"Kan niet starten: {path}\n{exc}")

        if errors:
            button.set_status("partial" if launched else "error")
            self._show_nonfatal_error(
                "\n\n".join(errors)
                + f"\n\nPas de configuratie aan in:\n{CONFIG_PATH}"
            )
        elif launched or already_running:
            button.set_status("running")

        self._schedule_status_poll(1200)
        self._apply_topmost()

    def refresh_status(self) -> None:
        self._status_after_id = None
        if self._closing or self._menu_open:
            return

        windows_processes(force=True)
        aggregate_states: list[str] = []

        for item in self.items:
            button = self.buttons[item.section]
            states: list[bool] = []

            for path, _hidden, detection in item.paths:
                running = is_probably_running_path(path, detection)
                if running:
                    self.launching_paths.pop(self._path_key(path), None)
                states.append(running or self._is_launch_locked(path))

            if states and all(states):
                state = "running"
            elif any(states):
                state = "partial"
            else:
                state = "off"

            button.set_status(state)
            aggregate_states.append(state)

        active_states = [state for state in aggregate_states if state != "off"]
        if any(state == "error" for state in aggregate_states):
            mini_colour = RED
        elif not active_states:
            mini_colour = OFF_DOT
        elif any(state == "partial" for state in active_states):
            mini_colour = ORANGE
        else:
            mini_colour = GREEN

        self.mini_dot.itemconfigure(self.mini_dot_id, fill=mini_colour)
        self._schedule_status_poll(5000)

    def _finish_menu(self) -> None:
        if self._closing:
            return
        self._menu_open = False
        self._schedule_status_poll(250)
        self._apply_topmost()

    def _run_menu_action(self, callback) -> None:
        if self._closing:
            return
        self._menu_open = False
        try:
            callback()
        finally:
            if not self._closing:
                self.after_idle(self._finish_menu)

    def _post_menu(self, menu: tk.Menu, x: int, y: int) -> None:
        if self._closing or not self.winfo_exists():
            return
        self._cancel_status_poll()
        self._menu_open = True
        try:
            menu.post(x, y)
        except tk.TclError:
            self._menu_open = False
            self._schedule_status_poll(250)
            return

        def poll_menu_closed() -> None:
            if self._closing:
                return
            try:
                posted = bool(menu.winfo_ismapped())
            except tk.TclError:
                posted = False
            if posted:
                self.after(100, poll_menu_closed)
            else:
                self._finish_menu()

        self.after(100, poll_menu_closed)

    def show_item_menu(self, item: LauncherItem, event=None) -> None:
        menu = self.item_menus.get(item.section)
        if menu is None or self._closing:
            return
        x = event.x_root if event is not None else self.winfo_pointerx()
        y = event.y_root if event is not None else self.winfo_pointery()
        self.after_idle(lambda: self._post_menu(menu, x, y))

    def quit_item(self, item: LauncherItem) -> None:
        errors: list[str] = []
        windows_processes(force=True)

        for path, _hidden, detection in item.paths:
            try:
                quit_path(path, detection)
                self.launching_paths.pop(self._path_key(path), None)
            except Exception as exc:
                errors.append(f"Kan niet afsluiten: {path}\n{exc}")

        if errors:
            self._show_nonfatal_error("\n\n".join(errors))
        self._schedule_status_poll(2000)

    def show_settings_menu(self, event=None) -> None:
        if self._closing:
            return
        if event is not None:
            x = event.widget.winfo_rootx()
            y = event.widget.winfo_rooty() + event.widget.winfo_height()
        else:
            x = self.winfo_rootx() + self.winfo_width() - 20
            y = self.winfo_rooty() + 20
        self.after_idle(lambda: self._post_menu(self.settings_menu, x, y))

    def open_config(self) -> None:
        try:
            os.startfile(str(CONFIG_PATH))  # type: ignore[attr-defined]
        except Exception as exc:
            self._show_nonfatal_error(
                f"Kan configuratie niet openen:\n{CONFIG_PATH}\n\n{exc}"
            )
        self._apply_topmost()

    def show_about(self) -> None:
        if self._closing:
            return
        about = tk.Toplevel(self)
        about.overrideredirect(True)
        about.configure(bg=BG)
        try:
            about.attributes("-topmost", True)
        except tk.TclError:
            pass

        shell = tk.Frame(
            about, bg=BG, highlightthickness=1, highlightbackground="#3a3f46"
        )
        shell.pack(fill="both", expand=True)
        tk.Label(
            shell,
            text=APP_NAME,
            bg=BG,
            fg=TEXT,
            font=("Segoe UI", 11, "bold"),
            padx=22,
        ).pack(pady=(16, 3))
        tk.Label(
            shell,
            text=f"Windows version {APP_VERSION}",
            bg=BG,
            fg=MUTED,
            font=("Segoe UI", 9),
        ).pack(pady=(0, 13))
        close_button = tk.Label(
            shell,
            text="Close",
            bg=PANEL,
            fg=TEXT,
            font=("Segoe UI", 9),
            cursor="hand2",
            padx=16,
            pady=6,
        )
        close_button.pack(pady=(0, 14))
        close_button.bind("<Button-1>", lambda _event: about.destroy())
        about.bind("<Escape>", lambda _event: about.destroy())

        about.update_idletasks()
        x = self.winfo_rootx() + max(
            0, (self.winfo_width() - about.winfo_width()) // 2
        )
        y = self.winfo_rooty() + self.winfo_height() + 5
        about.geometry(f"+{x}+{y}")
        about.focus_force()

    def request_close(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._cancel_status_poll()
        for menu in list(self.item_menus.values()) + [self.settings_menu]:
            try:
                menu.unpost()
            except tk.TclError:
                pass
        self.after_idle(self.close)

    def close(self) -> None:
        if not self.winfo_exists():
            return
        try:
            self.update_idletasks()
            save_launcher_position(self.winfo_x(), self.winfo_y())
        except tk.TclError:
            pass
        try:
            self.destroy()
        except tk.TclError:
            pass


def main() -> int:
    if os.name != "nt":
        print(f"{APP_NAME} Windows version can only run on Windows.", file=sys.stderr)
        return 1

    try:
        app = PSXLauncher()
        app.mainloop()
        return 0
    except Exception as exc:
        try:
            messagebox.showerror(APP_NAME, f"Onverwachte fout:\n\n{exc}")
        except Exception:
            print(f"[{APP_NAME}] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
