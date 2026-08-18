#!/usr/bin/env python3
"""
PSX App Launcher
Version 1.2c

Compact frameless launcher for Aerowinx PSX and related applications.
Launches configured application paths only; no command-line execution.
"""

from __future__ import annotations

import configparser
from functools import lru_cache
import os
import platform
import plistlib
import re
import subprocess
import sys
import time
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import messagebox

APP_NAME = "PSX Launcher"
APP_VERSION = "1.2c"
CONFIG_FILENAME = "psx_app_launcher.ini"

BG = "#17191c"
PANEL = "#22252a"
PANEL_HOVER = "#2d3137"
TEXT = "#f2f3f5"
MUTED = "#969ca5"
OFF_DOT = "#626870"
GREEN = "#54c878"
ORANGE = "#e7a84b"
RED = "#df6464"

# Default/generic identifiers are reused by many Python/PyInstaller apps and
# therefore cannot identify one specific configured application.
GENERIC_MAC_BUNDLE_IDS = {
    "org.pythonmac.unspecified",
    "org.python.python",
    "com.apple.ScriptEditor.id",
}


def app_dir() -> Path:
    """Return the directory beside the .app bundle for frozen macOS builds."""
    if getattr(sys, "frozen", False):
        executable = Path(sys.executable).resolve()
        for parent in executable.parents:
            if parent.suffix.lower() == ".app":
                return parent.parent
        return executable.parent
    return Path(__file__).resolve().parent


def bundled_config_path() -> Path | None:
    """Return an INI bundled beside the frozen executable, when present."""
    if not getattr(sys, "frozen", False):
        return None
    candidate = Path(sys.executable).resolve().parent / CONFIG_FILENAME
    return candidate if candidate.is_file() else None


BASE_DIR = app_dir()
CONFIG_DIR = Path.home() / "Library" / "Application Support" / APP_NAME
CONFIG_PATH = CONFIG_DIR / CONFIG_FILENAME
LEGACY_CONFIG_PATH = BASE_DIR / CONFIG_FILENAME


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
        "path1": "/Applications/Aerowinx.app",
        "detect1": "-jar Aerowinx.jar",
        "path2": "",
        "detect2": "",
    },
    "SimLink": {
        "enabled": "true",
        "hidden1": "false",
        "hidden2": "false",
        "path1": "/Applications/Simlink.app",
        "detect1": "",
        "path2": "/Applications/PSX SimLink Bridge.app",
        "detect2": "",
    },
    "Volanta": {
        "enabled": "true",
        "hidden1": "false",
        "hidden2": "false",
        "path1": "/Applications/Volanta.app",
        "detect1": "",
        "path2": "/Applications/PSX Volanta Bridge.app",
        "detect2": "",
    },
    "xPilot": {
        "enabled": "true",
        "hidden1": "false",
        "hidden2": "false",
        "path1": "/Applications/xPilot.app",
        "detect1": "",
        "path2": "/Applications/PSX xPilot Bridge.app",
        "detect2": "",
    },
    "PFPx": {
        "enabled": "true",
        "hidden1": "false",
        "hidden2": "false",
        "path1": "/Applications/Aerowinx PFPx GUI.app",
        "detect1": "",
        "path2": "",
        "detect2": "",
    },
    "GPS": {
        "enabled": "true",
        "hidden1": "false",
        "hidden2": "false",
        "path1": "/Applications/PSX GPS Interference.app",
        "detect1": "",
        "path2": "",
        "detect2": "",
    },
}


@dataclass
class LauncherItem:
    section: str
    label: str
    paths: list[tuple[Path, bool, str]]


class LaunchError(RuntimeError):
    pass


def _safe_getboolean(config: configparser.ConfigParser, section: str, option: str, fallback: bool) -> bool:
    try:
        return config.getboolean(section, option, fallback=fallback)
    except (ValueError, configparser.Error):
        return fallback


def ensure_config() -> configparser.ConfigParser:
    config = configparser.ConfigParser()
    config.optionxform = str

    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RuntimeError(
            f"Kan configuratiemap niet aanmaken:\n{CONFIG_DIR}\n\n{exc}"
        ) from exc

    # Keep the working INI in the user's Application Support directory. On the
    # first run, preserve an existing legacy INI beside the .app; otherwise use
    # the bundled INI. Existing configurations are never replaced or rewritten.
    if not CONFIG_PATH.exists():
        candidates = [LEGACY_CONFIG_PATH, bundled_config_path()]
        for candidate in candidates:
            if candidate is None or candidate == CONFIG_PATH or not candidate.is_file():
                continue
            try:
                with candidate.open("r", encoding="utf-8") as source:
                    content = source.read()
                with CONFIG_PATH.open("x", encoding="utf-8") as target:
                    target.write(content)
                break
            except FileExistsError:
                break
            except OSError as exc:
                raise RuntimeError(
                    f"Kan configuratie niet kopiëren:\n{CONFIG_PATH}\n\n{exc}"
                ) from exc

    # Create defaults only when neither an external nor bundled configuration
    # supplied a file. Never rewrite or migrate an existing INI.
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

    config.clear()
    config.optionxform = str
    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as handle:
            config.read_file(handle)
    except (OSError, UnicodeError, configparser.Error) as exc:
        raise RuntimeError(f"Kan configuratie niet lezen:\n{CONFIG_PATH}\n\n{exc}") from exc

    return config


def save_launcher_position(x: int, y: int) -> None:
    """Update only x and y in [Launcher], preserving all other INI text."""
    try:
        text = CONFIG_PATH.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return

    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines(keepends=True)
    section_pattern = re.compile(r"^\s*\[([^]]+)\]\s*(?:[;#].*)?(?:\r?\n)?$", re.IGNORECASE)
    value_pattern = re.compile(r"^(\s*)(x|y)(\s*=\s*).*(\r?\n)?$", re.IGNORECASE)

    launcher_start: int | None = None
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
            lines[index] = f"{match.group(1)}{match.group(2)}{match.group(3)}{value}{ending}"
            found[key] = True

        additions: list[str] = []
        if not found["x"]:
            additions.append(f"x = {x}{newline}")
        if not found["y"]:
            additions.append(f"y = {y}{newline}")
        if additions:
            lines[launcher_end:launcher_end] = additions

    # Rewrite the existing file in place. Replacing it with a temporary file
    # would create a new inode and discard macOS metadata such as the Finder
    # hidden flag.
    try:
        with CONFIG_PATH.open("r+", encoding="utf-8", newline="") as handle:
            handle.seek(0)
            handle.write("".join(lines))
            handle.truncate()
            handle.flush()
            os.fsync(handle.fileno())
    except OSError:
        pass


def configured_items(config: configparser.ConfigParser) -> list[LauncherItem]:
    items: list[LauncherItem] = []

    # ConfigParser preserves INI section insertion order. Every section except
    # [Launcher] becomes a button in exactly that order.
    for section in config.sections():
        if section == "Launcher":
            continue
        if not _safe_getboolean(config, section, "enabled", True):
            continue

        label = config.get(section, "label", fallback=section).strip() or section
        paths: list[tuple[Path, bool, str]] = []

        for index, key in enumerate(("path1", "path2"), start=1):
            try:
                value = config.get(section, key, fallback="").strip()
                if value:
                    expanded = os.path.expandvars(os.path.expanduser(value))
                    hidden = _safe_getboolean(config, section, f"hidden{index}", False)
                    detection = config.get(section, f"detect{index}", fallback="").strip()
                    paths.append((Path(expanded), hidden, detection))
            except Exception:
                # A malformed single value must never prevent the launcher opening.
                continue

        items.append(LauncherItem(section, label, paths))

    return items


def launch_path(path: Path, hidden: bool = False) -> None:
    if not path.exists():
        raise LaunchError(f"Niet gevonden:\n{path}")

    system = platform.system()
    try:
        if system == "Darwin":
            if path.suffix.lower() == ".app":
                subprocess.Popen(
                    ["open", str(path)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                if not os.access(path, os.X_OK):
                    raise LaunchError(f"Bestand is niet uitvoerbaar:\n{path}")
                if hidden:
                    subprocess.Popen(
                        [str(path)],
                        cwd=str(path.parent),
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                    )
                else:
                    # LaunchServices behaves like opening the executable in Finder
                    # and gives it its normal visible Terminal window.
                    subprocess.Popen(
                        ["open", str(path)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
        elif system == "Windows":
            os.startfile(str(path))  # type: ignore[attr-defined]
        else:
            subprocess.Popen(
                [str(path)],
                cwd=str(path.parent),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    except OSError as exc:
        raise LaunchError(f"Kan niet starten:\n{path}\n\n{exc}") from exc


@lru_cache(maxsize=64)
def _mac_app_contains_aerowinx_jar(app_path_text: str) -> bool:
    """Return True only for an app bundle that actually contains Aerowinx.jar."""
    app_path = Path(app_path_text)
    if app_path.suffix.lower() != ".app" or not app_path.is_dir():
        return False

    contents = app_path / "Contents"
    if not contents.is_dir():
        return False

    try:
        return any(candidate.is_file() for candidate in contents.rglob("Aerowinx.jar"))
    except OSError:
        return False


def mac_app_info(path: Path) -> tuple[str, str, Path | None]:
    """Return bundle identifier, executable name and executable path."""
    if path.suffix.lower() != ".app":
        return "", path.name, path

    info_plist = path / "Contents" / "Info.plist"
    bundle_id = ""
    executable = path.stem.strip()
    try:
        with info_plist.open("rb") as handle:
            info = plistlib.load(handle)
        bundle_id = str(info.get("CFBundleIdentifier", "")).strip()
        executable = str(info.get("CFBundleExecutable", executable)).strip() or executable
    except (OSError, ValueError, plistlib.InvalidFileException):
        pass

    executable_path = path / "Contents" / "MacOS" / executable if executable else None
    return bundle_id, executable, executable_path


def _mac_bundle_is_running(bundle_id: str, require_visible: bool = False) -> bool:
    normalized = bundle_id.strip().lower()
    if not normalized or normalized in GENERIC_MAC_BUNDLE_IDS:
        return False
    escaped = bundle_id.replace('\\', '\\\\').replace('"', '\\"')
    if require_visible:
        script = (
            'tell application "System Events" to '
            f'return exists (first application process whose bundle identifier is "{escaped}" and visible is true)'
        )
    else:
        script = (
            'tell application "System Events" to '
            f'return exists (first application process whose bundle identifier is "{escaped}")'
        )
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=2,
        )
        return result.returncode == 0 and result.stdout.strip().lower() == "true"
    except (OSError, subprocess.SubprocessError):
        return False


def _mac_loose_executable_is_running(path: Path) -> bool:
    """Match a non-.app executable only by its exact argv[0] path."""
    try:
        target = str(path.resolve())
    except OSError:
        target = str(path.absolute())

    try:
        result = subprocess.run(
            ["ps", "-axo", "command="],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode != 0:
            return False
        for line in result.stdout.splitlines():
            command = line.strip()
            if command == target or command.startswith(target + " "):
                return True
    except (OSError, subprocess.SubprocessError):
        pass
    return False


def _mac_executable_is_running(app_path: Path, executable: str, executable_path: Path | None) -> bool:
    try:
        result = subprocess.run(
            ["ps", "-axo", "command="],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode == 0:
            executable_target = str(executable_path) if executable_path else ""
            bundle_target = str(app_path) if app_path.suffix.lower() == ".app" else ""

            for line in result.stdout.splitlines():
                command = line.strip()
                command_lower = command.lower()

                if executable_target and (
                    command == executable_target
                    or command.startswith(executable_target + " ")
                    or executable_target in command
                ):
                    return True

                if bundle_target and bundle_target in command:
                    return True

                if _mac_app_contains_aerowinx_jar(str(app_path)):
                    if "java" in command_lower and "aerowinx.jar" in command_lower:
                        return True

        if executable:
            result = subprocess.run(
                ["pgrep", "-x", executable],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=1,
            )
            return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        pass
    return False


def _process_command_contains(text: str) -> bool:
    """Return True when a running process command line contains literal text."""
    if not text:
        return False
    try:
        if platform.system() == "Windows":
            result = subprocess.run(
                ["wmic", "process", "get", "CommandLine"],
                capture_output=True,
                text=True,
                timeout=3,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        else:
            result = subprocess.run(
                ["ps", "-axo", "command="],
                capture_output=True,
                text=True,
                timeout=2,
            )
        if result.returncode != 0:
            return False
        return any(text in line for line in result.stdout.splitlines())
    except (OSError, subprocess.SubprocessError):
        return False


def is_probably_running_path(path: Path, detection: str = "") -> bool:
    """Best-effort process check, optionally using literal INI detection text."""
    if detection:
        return _process_command_contains(detection)

    if not path.exists():
        return False

    system = platform.system()
    try:
        if system == "Darwin":
            if path.suffix.lower() != ".app":
                return _mac_loose_executable_is_running(path)

            bundle_id, executable, executable_path = mac_app_info(path)

            if path.stem.strip().lower() == "volanta":
                return _mac_bundle_is_running(bundle_id, require_visible=True)

            if _mac_bundle_is_running(bundle_id):
                return True
            return _mac_executable_is_running(path, executable, executable_path)

        name = path.stem.strip()
        if not name:
            return False

        if system == "Windows":
            result = subprocess.run(
                ["tasklist"],
                capture_output=True,
                text=True,
                timeout=2,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return path.name.lower() in result.stdout.lower() or name.lower() in result.stdout.lower()

        result = subprocess.run(
            ["pgrep", "-x", name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=1,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _mac_exact_pids(path: Path) -> list[int]:
    """Return PIDs whose command line starts with the exact configured path."""
    try:
        target = str(path.resolve())
    except OSError:
        target = str(path.absolute())
    pids: list[int] = []
    try:
        result = subprocess.run(
            ["ps", "-axo", "pid=,command="],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                stripped = line.strip()
                if not stripped:
                    continue
                pid_text, _, command = stripped.partition(" ")
                command = command.strip()
                if command == target or command.startswith(target + " "):
                    try:
                        pids.append(int(pid_text))
                    except ValueError:
                        pass
    except (OSError, subprocess.SubprocessError):
        pass
    return pids


def quit_path(path: Path) -> None:
    """Quit exactly the configured app or executable without broad name kills."""
    if not path.exists():
        return

    system = platform.system()
    if system == "Darwin":
        if path.suffix.lower() == ".app":
            bundle_id, executable, executable_path = mac_app_info(path)
            if bundle_id and bundle_id.lower() not in GENERIC_MAC_BUNDLE_IDS:
                escaped = bundle_id.replace('\\', '\\\\').replace('"', '\\"')
                script = (
                    'tell application "System Events" to '
                    f'tell (first application process whose bundle identifier is "{escaped}") to quit'
                )
                subprocess.run(
                    ["osascript", "-e", script],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=3,
                )
            else:
                for pid in _mac_exact_pids(executable_path or path):
                    try:
                        os.kill(pid, 15)
                    except (OSError, ProcessLookupError):
                        pass
        else:
            for pid in _mac_exact_pids(path):
                try:
                    os.kill(pid, 15)
                except (OSError, ProcessLookupError):
                    pass
        return

    if system == "Windows":
        subprocess.run(
            ["taskkill", "/IM", path.name, "/T"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return

    for pid in _mac_exact_pids(path):
        try:
            os.kill(pid, 15)
        except (OSError, ProcessLookupError):
            pass


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
            font=("Helvetica Neue", 10, "normal"),
            padx=0,
            pady=0,
            anchor="center",
        )
        self.label.pack(side="left", padx=(0, 10), pady=(5, 7))

        for widget in (self, self.dot, self.label):
            widget.bind("<Button-1>", self._clicked)
            widget.bind("<Button-2>", self._menu_clicked)
            widget.bind("<Button-3>", self._menu_clicked)
            widget.bind("<Control-Button-1>", self._menu_clicked)
            widget.bind("<Enter>", self._enter)
            widget.bind("<Leave>", self._leave)

    def _clicked(self, event=None) -> None:
        if event is not None and (event.state & 0x0004):
            self._menu_clicked(event)
            return
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

        self.drag_handle = tk.Canvas(
            self.shell,
            width=38,
            height=38,
            bg=BG,
            highlightthickness=0,
            bd=0,
            cursor="fleur",
        )
        for y in (12, 19, 26):
            self.drag_handle.create_line(
                9,
                y,
                29,
                y,
                fill=MUTED,
                width=3,
                capstyle=tk.ROUND,
                tags=("hamburger",),
            )
        self.drag_handle.pack(side="left")
        self.drag_handle.bind("<ButtonPress-1>", self._start_drag)
        self.drag_handle.bind("<B1-Motion>", self._drag_window)
        self.drag_handle.bind("<ButtonRelease-1>", self._hamburger_released)
        self.drag_handle.bind(
            "<Enter>",
            lambda _event: self.drag_handle.itemconfigure("hamburger", fill=TEXT),
        )
        self.drag_handle.bind(
            "<Leave>",
            lambda _event: self.drag_handle.itemconfigure("hamburger", fill=MUTED),
        )

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
            font=("Helvetica Neue", 12),
            cursor="hand2",
            padx=5,
        )
        self.settings.grid(row=0, column=len(self.items), padx=(2, 0), sticky="ns")
        self.settings.bind("<Button-1>", self.show_settings_menu)
        self.settings.bind("<Button-2>", self.show_settings_menu)
        self.settings.bind("<Button-3>", self.show_settings_menu)
        self.settings.bind("<Control-Button-1>", self.show_settings_menu)
        self.settings.bind("<Enter>", lambda _event: self.settings.configure(fg=TEXT))
        self.settings.bind("<Leave>", lambda _event: self.settings.configure(fg=MUTED))

        self.item_menus: dict[str, tk.Menu] = {}
        for item in self.items:
            menu = tk.Menu(self, tearoff=0)
            menu.add_command(label="Quit apps", command=lambda current=item: self._run_menu_action(lambda: self.quit_item(current)))
            self.item_menus[item.section] = menu

        self.settings_menu = tk.Menu(self, tearoff=0)
        self.settings_menu.add_command(label="Edit INI", command=lambda: self._run_menu_action(self.open_config))
        self.settings_menu.add_command(label=f"About ({APP_VERSION})", command=lambda: self._run_menu_action(self.show_about))
        self.settings_menu.add_separator()
        self.settings_menu.add_command(label="Quit", command=lambda: self._run_menu_action(self.request_close))

        self.mini = tk.Frame(self.shell, bg=PANEL, cursor="hand2")
        self.mini_dot = tk.Canvas(self.mini, width=10, height=10, bg=PANEL, highlightthickness=0, bd=0)
        self.mini_dot_id = self.mini_dot.create_oval(1, 1, 9, 9, fill=OFF_DOT, outline="")
        self.mini_dot.pack(side="left", padx=(9, 5), pady=7)
        self.mini_label = tk.Label(
            self.mini,
            text="PSX",
            bg=PANEL,
            fg=TEXT,
            font=("Helvetica Neue", 10),
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
        self.bind("<Command-q>", lambda _event: self.request_close())
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

    def _hamburger_released(self, _event=None) -> None:
        if not self._drag_moved:
            self.collapse()

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
        self.drag_handle.pack(side="left")
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
        try:
            return str(path.resolve())
        except OSError:
            return str(path.absolute())

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

        for path, hidden, detection in item.paths:
            if not path.exists():
                errors.append(f"Niet gevonden: {path}")
                continue
            if self._is_launch_locked(path) or is_probably_running_path(path, detection):
                already_running = True
                continue
            try:
                self._mark_launching(path)
                launch_path(path, hidden=hidden)
                launched = True
            except Exception as exc:
                errors.append(f"Kan niet starten: {path}\n{exc}")

        if errors:
            button.set_status("partial" if launched else "error")
            self._show_nonfatal_error(
                "\n\n".join(errors) + f"\n\nPas de configuratie aan in:\n{CONFIG_PATH}"
            )
        elif launched or already_running:
            button.set_status("running")

        self._schedule_status_poll(1200)
        self._apply_topmost()

    def refresh_status(self) -> None:
        self._status_after_id = None
        if self._closing or self._menu_open:
            return
        aggregate_states: list[str] = []
        for item in self.items:
            button = self.buttons[item.section]
            states: list[bool] = []

            for path, hidden, detection in item.paths:
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

        if any(state == "error" for state in aggregate_states):
            mini_colour = RED
        else:
            active_states = [state for state in aggregate_states if state != "off"]
            if not active_states:
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
        if self._closing or not self.winfo_exists():
            return
        menu = self.item_menus.get(item.section)
        if menu is None:
            return
        x = event.x_root if event is not None else self.winfo_pointerx()
        y = event.y_root if event is not None else self.winfo_pointery()
        self.after_idle(lambda: self._post_menu(menu, x, y))

    def quit_item(self, item: LauncherItem) -> None:
        errors: list[str] = []
        for path, hidden, detection in item.paths:
            try:
                quit_path(path)
                self.launching_paths.pop(self._path_key(path), None)
            except Exception as exc:
                errors.append(f"Kan niet afsluiten: {path}\n{exc}")
        if errors:
            self._show_nonfatal_error("\n\n".join(errors))
        self._schedule_status_poll(2000)

    def show_settings_menu(self, event=None) -> None:
        if self._closing or not self.winfo_exists():
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
            if platform.system() == "Darwin":
                subprocess.Popen(
                    ["open", str(CONFIG_PATH)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            elif platform.system() == "Windows":
                os.startfile(str(CONFIG_PATH))  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", str(CONFIG_PATH)])
        except Exception as exc:
            self._show_nonfatal_error(f"Kan configuratie niet openen:\n{CONFIG_PATH}\n\n{exc}")
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

        shell = tk.Frame(about, bg=BG, highlightthickness=1, highlightbackground="#3a3f46")
        shell.pack(fill="both", expand=True)
        tk.Label(
            shell,
            text=APP_NAME,
            bg=BG,
            fg=TEXT,
            font=("Helvetica Neue", 12, "bold"),
            padx=22,
            pady=0,
        ).pack(pady=(16, 3))
        tk.Label(
            shell,
            text=f"Version {APP_VERSION}",
            bg=BG,
            fg=MUTED,
            font=("Helvetica Neue", 10),
        ).pack(pady=(0, 13))
        close_button = tk.Label(
            shell,
            text="Close",
            bg=PANEL,
            fg=TEXT,
            font=("Helvetica Neue", 10),
            cursor="hand2",
            padx=16,
            pady=6,
        )
        close_button.pack(pady=(0, 14))
        close_button.bind("<Button-1>", lambda _event: about.destroy())
        about.bind("<Escape>", lambda _event: about.destroy())

        about.update_idletasks()
        x = self.winfo_rootx() + max(0, (self.winfo_width() - about.winfo_width()) // 2)
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
