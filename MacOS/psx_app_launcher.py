#!/usr/bin/env python3
"""
PSX App Launcher
Version 1.0

Compact frameless launcher for Aerowinx PSX and related applications.
Launches configured application paths only; no command-line execution.
"""

from __future__ import annotations

import configparser
from functools import lru_cache
import os
import platform
import plistlib
import subprocess
import sys
import time
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import messagebox

APP_NAME = "PSX App Launcher"
APP_VERSION = "1.0"

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
        "path1": "/Applications/Aerowinx PSX.app",
        "path2": "",
    },
    "SimLink": {
        "enabled": "true",
        "hidden1": "false",
        "hidden2": "false",
        "path1": "/Applications/Simlink.app",
        "path2": "/Applications/PSX SimLink Bridge.app",
    },
    "Volanta": {
        "enabled": "true",
        "hidden1": "false",
        "hidden2": "false",
        "path1": "/Applications/Volanta.app",
        "path2": "/Applications/PSX Volanta Bridge.app",
    },
    "xPilot": {
        "enabled": "true",
        "hidden1": "false",
        "hidden2": "false",
        "path1": "/Applications/xPilot.app",
        "path2": "/Applications/PSX xPilot Bridge.app",
    },
    "PFPx": {
        "enabled": "true",
        "hidden1": "false",
        "hidden2": "false",
        "path1": "/Applications/Aerowinx PFPx GUI.app",
        "path2": "",
    },
    "GPS": {
        "enabled": "true",
        "hidden1": "false",
        "hidden2": "false",
        "path1": "/Applications/PSX GPS Interference.app",
        "path2": "",
    },
}


@dataclass
class LauncherItem:
    section: str
    label: str
    paths: list[tuple[Path, bool]]


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

    # The launcher only creates the INI when it does not exist. An existing
    # configuration is read exactly as-is and is never rewritten or migrated.
    if not CONFIG_PATH.exists():
        for section, values in DEFAULT_CONFIG.items():
            config[section] = values
        try:
            with CONFIG_PATH.open("x", encoding="utf-8") as handle:
                config.write(handle)
        except FileExistsError:
            # Another instance may have created it between the existence check
            # and opening the file. Fall through and read that file unchanged.
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
        paths: list[tuple[Path, bool]] = []

        for index, key in enumerate(("path1", "path2"), start=1):
            try:
                value = config.get(section, key, fallback="").strip()
                if value:
                    expanded = os.path.expandvars(os.path.expanduser(value))
                    hidden = _safe_getboolean(config, section, f"hidden{index}", False)
                    paths.append((Path(expanded), hidden))
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
    """Return True only for an app bundle that actually contains Aerowinx.jar.

    This intentionally does not use the INI section name, button label or the
    position of the button. The configured bundle itself determines whether the
    special long-lived Java-process fallback is applicable.
    """
    app_path = Path(app_path_text)
    if app_path.suffix.lower() != ".app" or not app_path.is_dir():
        return False

    contents = app_path / "Contents"
    if not contents.is_dir():
        return False

    # Aerowinx launchers may place the JAR in different subfolders. Search the
    # bundle once and cache the result; this is not repeated on every 5 s poll.
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
    # System Events identifies the actual app process by bundle identifier. This
    # remains reliable for Java/PyInstaller apps whose Unix process name may vary.
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
        # First compare against the full command line. This avoids collisions
        # between similarly named apps and catches wrapper-launched processes.
        result = subprocess.run(
            ["ps", "-axo", "command="],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode == 0:
            executable_target = str(executable_path) if executable_path else ""
            bundle_target = str(app_path) if app_path.suffix.lower() == ".app" else ""
            app_name = app_path.stem.strip().lower()

            for line in result.stdout.splitlines():
                command = line.strip()
                command_lower = command.lower()

                # Native apps normally keep their executable path as argv[0].
                if executable_target and (
                    command == executable_target
                    or command.startswith(executable_target + " ")
                    or executable_target in command
                ):
                    return True

                # Java/PyInstaller wrapper apps may exit after spawning their real
                # process. The spawned command commonly still contains a JAR,
                # resource, working path or argument inside the original bundle.
                if bundle_target and bundle_target in command:
                    return True

                # Aerowinx PSX can continue as a plain Java process after
                # its macOS wrapper has exited. Apply this fallback only when the
                # configured .app bundle actually contains Aerowinx.jar. This
                # prevents similarly named apps such as Aerowinx PFPx GUI.app
                # from becoming green when PSX is running.
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


def is_probably_running_path(path: Path) -> bool:
    """Best-effort exact process check for a configured application path."""
    if not path.exists():
        return False

    system = platform.system()
    try:
        if system == "Darwin":
            # Loose executables have no bundle identifier. Never fall back to a
            # process-name match, because that can mistake another process with
            # the same filename for this configured executable.
            if path.suffix.lower() != ".app":
                return _mac_loose_executable_is_running(path)

            bundle_id, executable, executable_path = mac_app_info(path)

            # Volanta can leave updater/helper processes alive after its main UI
            # has been closed. Count Volanta itself only while the real app
            # process is visible; path2 (the Aerowinx bridge) is checked
            # independently as its own application.
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
    """Quit exactly the configured app or executable on macOS."""
    if platform.system() != "Darwin":
        raise LaunchError("Quit apps is momenteel alleen voor macOS geïmplementeerd.")

    if path.suffix.lower() == ".app":
        bundle_id, _executable, executable_path = mac_app_info(path)
        normalized = bundle_id.strip().lower()
        if bundle_id and normalized not in GENERIC_MAC_BUNDLE_IDS:
            escaped = bundle_id.replace('\\', '\\\\').replace('"', '\\"')
            script = f'tell application id "{escaped}" to quit' 
            subprocess.run(["osascript", "-e", script], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=3)
        # Also terminate an exact bundle executable/wrapper if it remains.
        if executable_path is not None:
            for pid in _mac_exact_pids(executable_path):
                try:
                    os.kill(pid, 15)
                except (OSError, ProcessLookupError):
                    pass
        return

    pids = _mac_exact_pids(path)
    for pid in pids:
        try:
            os.kill(pid, 15)
        except (OSError, ProcessLookupError):
            pass


class UtilityButton(tk.Frame):
    def __init__(self, master: tk.Misc, item: LauncherItem, command, context_command) -> None:
        super().__init__(master, bg=PANEL, cursor="hand2", highlightthickness=0)
        self.item = item
        self.command = command
        self.context_command = context_command
        self.state = "off"

        # Slightly larger status bullet and identical top/bottom padding for the
        # bullet and label keep them visually centered on the same baseline.
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
            widget.bind("<Button-2>", self._context)
            widget.bind("<Button-3>", self._context)
            widget.bind("<Control-Button-1>", self._context)
            widget.bind("<Enter>", self._enter)
            widget.bind("<Leave>", self._leave)

    def _clicked(self, _event=None) -> None:
        self.command(self.item)

    def _context(self, event=None) -> str:
        self.context_command(self.item, event)
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
        self.item_menus: dict[str, tk.Menu] = {}
        self._closing = False
        self._status_after_id: str | None = None
        self._active_menu: tk.Menu | None = None
        self._menu_open = False
        self.about_window: tk.Toplevel | None = None
        # Paths remain launch-locked briefly while macOS registers the app.
        self.launching_paths: dict[str, float] = {}
        self.always_on_top = _safe_getboolean(
            self.config_data, "Launcher", "always_on_top", True
        )

        self.title(f"{APP_NAME} {APP_VERSION}")
        self.configure(bg=BG)
        self.resizable(False, False)

        # Frameless must be set before the persistent topmost handling.
        self.overrideredirect(True)

        self.shell = tk.Frame(self, bg=BG, highlightthickness=1, highlightbackground="#30343a")
        self.shell.pack(fill="both", expand=True)

        self._drag_origin_x = 0
        self._drag_origin_y = 0
        self._drag_moved = False
        self.collapsed = False

        # Wider handle makes dragging easier. Double-click collapses the bar.
        self.drag_handle = tk.Frame(self.shell, bg=BG, width=26, cursor="fleur")
        self.drag_handle.pack(side="left", fill="y")
        self.drag_handle.pack_propagate(False)
        self.drag_handle.bind("<ButtonPress-1>", self._start_drag)
        self.drag_handle.bind("<B1-Motion>", self._drag_window)
        self.drag_handle.bind("<Double-Button-1>", self.collapse_bar)

        self.content = tk.Frame(self.shell, bg=BG, padx=2, pady=0)
        self.content.pack(side="left", fill="both", expand=True)

        for index, item in enumerate(self.items):
            button = UtilityButton(self.content, item, self.launch_item, self.show_item_menu)
            button.grid(row=0, column=index, padx=(0 if index == 0 else 2, 0), sticky="nsew")
            self.buttons[item.section] = button
            menu = tk.Menu(self, tearoff=0)
            menu.add_command(
                label="Quit apps",
                command=lambda current=item, current_menu=menu: self._run_menu_command(
                    current_menu, lambda: self.quit_item(current)
                ),
            )
            menu.bind("<Unmap>", self._menu_unmapped, add="+")
            self.item_menus[item.section] = menu

        settings = tk.Label(
            self.content,
            text="⋯",
            bg=BG,
            fg=MUTED,
            font=("Helvetica Neue", 12),
            cursor="hand2",
            padx=5,
        )
        settings.grid(row=0, column=len(self.items), padx=(2, 0), sticky="ns")
        settings.bind("<Button-1>", self.show_settings_menu)
        settings.bind("<Button-2>", self.show_settings_menu)
        settings.bind("<Button-3>", self.show_settings_menu)
        settings.bind("<Control-Button-1>", self.show_settings_menu)
        settings.bind("<Enter>", lambda _event: settings.configure(fg=TEXT))
        settings.bind("<Leave>", lambda _event: settings.configure(fg=MUTED))

        self.settings_menu = tk.Menu(self, tearoff=0)
        self.settings_menu.add_command(
            label="Edit INI",
            command=lambda: self._run_menu_command(self.settings_menu, self.open_config),
        )
        self.settings_menu.add_command(
            label=f"About ({APP_VERSION})",
            command=lambda: self._run_menu_command(self.settings_menu, self.show_about),
        )
        self.settings_menu.add_separator()
        self.settings_menu.add_command(
            label="Quit",
            command=lambda: self._run_menu_command(self.settings_menu, self.close),
        )
        self.settings_menu.bind("<Unmap>", self._menu_unmapped, add="+")

        # Compact collapsed view. A single click restores the full bar; it can
        # also be dragged without accidentally expanding.
        self.collapsed_view = tk.Frame(
            self, bg=PANEL, highlightthickness=1, highlightbackground="#30343a", cursor="hand2"
        )
        self.collapsed_dot = tk.Canvas(
            self.collapsed_view, width=12, height=12, bg=PANEL, highlightthickness=0, bd=0
        )
        self.collapsed_dot_id = self.collapsed_dot.create_oval(1, 1, 11, 11, fill=OFF_DOT, outline="")
        self.collapsed_dot.pack(side="left", padx=(9, 5), pady=7)
        self.collapsed_label = tk.Label(
            self.collapsed_view, text="PSX", bg=PANEL, fg=TEXT,
            font=("Helvetica Neue", 10, "normal"), padx=0, pady=0
        )
        self.collapsed_label.pack(side="left", padx=(0, 9), pady=(5, 7))
        for widget in (self.collapsed_view, self.collapsed_dot, self.collapsed_label):
            widget.bind("<ButtonPress-1>", self._start_collapsed_action)
            widget.bind("<B1-Motion>", self._drag_collapsed)
            widget.bind("<ButtonRelease-1>", self._finish_collapsed_action)

        x = self.config_data.getint("Launcher", "x", fallback=80)
        y = self.config_data.getint("Launcher", "y", fallback=80)
        self.geometry(f"+{x}+{y}")

        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Escape>", lambda _event: self.close())
        self.bind("<Command-q>", lambda _event: self.close())
        self.bind("<Map>", lambda _event: self._apply_topmost())
        self.bind("<FocusIn>", lambda _event: self._apply_topmost())

        # Tk on macOS can drop -topmost after overrideredirect/map changes.
        # Re-apply it after the window exists and then periodically.
        self.after_idle(self._apply_topmost)
        self.after(200, self._apply_topmost)
        self.after(1000, self._maintain_topmost)
        self._schedule_status_poll(500)

    def _apply_topmost(self) -> None:
        if not self.always_on_top:
            return
        try:
            self.attributes("-topmost", True)
            self.lift()
        except tk.TclError:
            pass

    def _maintain_topmost(self) -> None:
        self._apply_topmost()
        self.after(2000, self._maintain_topmost)

    def _start_drag(self, event) -> None:
        self._drag_origin_x = event.x_root - self.winfo_x()
        self._drag_origin_y = event.y_root - self.winfo_y()
        self._drag_start_root_x = event.x_root
        self._drag_start_root_y = event.y_root
        self._drag_moved = False

    def _drag_window(self, event) -> None:
        if abs(event.x_root - self._drag_start_root_x) > 2 or abs(event.y_root - self._drag_start_root_y) > 2:
            self._drag_moved = True
        x = event.x_root - self._drag_origin_x
        y = event.y_root - self._drag_origin_y
        self.geometry(f"+{x}+{y}")

    def collapse_bar(self, _event=None) -> str:
        if self.collapsed:
            return "break"
        self.collapsed = True
        self.shell.pack_forget()
        self.collapsed_view.pack(fill="both", expand=True)
        self.update_idletasks()
        self.geometry(f"{self.collapsed_view.winfo_reqwidth()}x{self.collapsed_view.winfo_reqheight()}+{self.winfo_x()}+{self.winfo_y()}")
        self._apply_topmost()
        return "break"

    def expand_bar(self) -> None:
        if not self.collapsed:
            return
        x, y = self.winfo_x(), self.winfo_y()
        self.collapsed = False
        self.collapsed_view.pack_forget()
        self.shell.pack(fill="both", expand=True)
        self.geometry(f"+{x}+{y}")
        self.update_idletasks()
        self.geometry(f"{self.shell.winfo_reqwidth()}x{self.shell.winfo_reqheight()}+{x}+{y}")
        self._apply_topmost()

    def _start_collapsed_action(self, event) -> None:
        self._start_drag(event)

    def _drag_collapsed(self, event) -> None:
        self._drag_window(event)

    def _finish_collapsed_action(self, _event=None) -> None:
        if not self._drag_moved:
            self.expand_bar()

    def _show_nonfatal_error(self, text: str) -> None:
        """Show an error without ever allowing a Tk dialog failure to close the launcher."""
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

    def launch_item(self, item: LauncherItem) -> None:
        """Start one configured item; no configuration/start error may escape Tk."""
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

        for path, hidden in item.paths:
            if not path.exists():
                errors.append(f"Niet gevonden: {path}")
                continue
            # Block duplicate clicks both for apps already running and during
            # the short macOS registration window immediately after launch.
            if self._is_launch_locked(path) or is_probably_running_path(path):
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

            for path, hidden in item.paths:
                running = is_probably_running_path(path)
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

        # Central collapsed status ignores utilities that are off (grey).
        # Green means every currently active utility is fully running. Orange
        # means at least one currently active utility is only partially running.
        # If nothing is active, the central indicator remains grey.
        active_states = [state for state in aggregate_states if state != "off"]
        if any(state == "error" for state in active_states):
            aggregate = "error"
        elif any(state == "partial" for state in active_states):
            aggregate = "partial"
        elif active_states and all(state == "running" for state in active_states):
            aggregate = "running"
        else:
            aggregate = "off"
        colour = {"off": OFF_DOT, "partial": ORANGE, "running": GREEN, "error": RED}[aggregate]
        self.collapsed_dot.itemconfigure(self.collapsed_dot_id, fill=colour)

        self._schedule_status_poll(5000)

    def _pause_status_poll(self) -> None:
        if self._status_after_id is not None:
            try:
                self.after_cancel(self._status_after_id)
            except tk.TclError:
                pass
            self._status_after_id = None

    def _schedule_status_poll(self, delay_ms: int = 5000) -> None:
        if self._closing or self._menu_open:
            return
        self._pause_status_poll()
        try:
            self._status_after_id = self.after(delay_ms, self.refresh_status)
        except tk.TclError:
            self._status_after_id = None

    def _post_menu(self, menu: tk.Menu, x: int, y: int) -> None:
        if self._closing or not self.winfo_exists():
            return
        self._pause_status_poll()
        self._menu_open = True
        self._active_menu = menu
        try:
            menu.post(x, y)
        except tk.TclError:
            self._finish_menu()

    def _finish_menu(self) -> None:
        if not self._menu_open and self._active_menu is None:
            return
        self._active_menu = None
        self._menu_open = False
        if not self._closing:
            self._apply_topmost()
            self._schedule_status_poll(250)

    def _menu_unmapped(self, _event=None) -> None:
        self.after_idle(self._finish_menu)

    def _run_menu_command(self, menu: tk.Menu, callback) -> None:
        try:
            menu.unpost()
        except tk.TclError:
            pass
        self._finish_menu()
        if not self._closing:
            self.after_idle(callback)

    def show_item_menu(self, item: LauncherItem, event=None) -> None:
        if self._closing or not self.winfo_exists():
            return
        menu = self.item_menus.get(item.section)
        if menu is None:
            return
        x = event.x_root if event is not None else self.winfo_pointerx()
        y = event.y_root if event is not None else self.winfo_pointery()
        # Post after the current mouse event has completed. This avoids the
        # sluggish/stuck context-menu behaviour seen with tk_popup on macOS.
        self.after_idle(lambda: self._post_menu(menu, x, y))

    def quit_item(self, item: LauncherItem) -> None:
        errors: list[str] = []
        for path, hidden in item.paths:
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

    def show_about(self) -> None:
        if self._closing or not self.winfo_exists():
            return

        try:
            self.settings_menu.unpost()
            self.settings_menu.grab_release()
        except tk.TclError:
            pass

        # Reuse the existing About window instead of creating duplicates.
        if self.about_window is not None:
            try:
                if self.about_window.winfo_exists():
                    self.about_window.lift()
                    return
            except tk.TclError:
                pass
            self.about_window = None

        try:
            window = tk.Toplevel(self)
            self.about_window = window
            window.overrideredirect(True)
            window.configure(bg=BG)
            window.attributes("-topmost", True)

            frame = tk.Frame(
                window,
                bg=PANEL,
                highlightthickness=1,
                highlightbackground="#40454d",
                padx=22,
                pady=16,
            )
            frame.pack(fill="both", expand=True)

            tk.Label(
                frame,
                text=APP_NAME,
                bg=PANEL,
                fg=TEXT,
                font=("Helvetica Neue", 12, "bold"),
            ).pack()
            tk.Label(
                frame,
                text=f"Version {APP_VERSION}",
                bg=PANEL,
                fg=MUTED,
                font=("Helvetica Neue", 10),
                pady=4,
            ).pack()

            close_button = tk.Label(
                frame,
                text="Close",
                bg=PANEL_HOVER,
                fg=TEXT,
                font=("Helvetica Neue", 10),
                cursor="hand2",
                padx=14,
                pady=4,
            )
            close_button.pack(pady=(8, 0))
            close_button.bind("<Button-1>", lambda _event: self.close_about())
            window.bind("<Escape>", lambda _event: self.close_about())

            window.update_idletasks()
            x = self.winfo_rootx() + (self.winfo_width() - window.winfo_reqwidth()) // 2
            y = self.winfo_rooty() + self.winfo_height() + 6
            window.geometry(f"+{max(0, x)}+{max(0, y)}")
            window.lift()
        except tk.TclError:
            self.about_window = None

    def close_about(self) -> None:
        window = self.about_window
        self.about_window = None
        if window is not None:
            try:
                window.destroy()
            except tk.TclError:
                pass
        self._apply_topmost()

    def open_config(self) -> None:
        if self._closing or not self.winfo_exists():
            return
        try:
            try:
                self.settings_menu.unpost()
                self.settings_menu.grab_release()
            except tk.TclError:
                pass

            system = platform.system()
            if system == "Darwin":
                subprocess.Popen(
                    ["open", str(CONFIG_PATH)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            elif system == "Windows":
                os.startfile(str(CONFIG_PATH))  # type: ignore[attr-defined]
            else:
                subprocess.Popen(
                    ["xdg-open", str(CONFIG_PATH)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        except Exception as exc:
            self._show_nonfatal_error(f"Kan INI niet openen:\n{CONFIG_PATH}\n\n{exc}")
        finally:
            self._apply_topmost()

    def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._pause_status_poll()
        if self._active_menu is not None:
            try:
                self._active_menu.unpost()
            except tk.TclError:
                pass
        try:
            self.close_about()
            try:
                self.settings_menu.unpost()
                self.settings_menu.grab_release()
            except (AttributeError, tk.TclError):
                pass
        finally:
            # Never rewrite an existing INI, including on shutdown.
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
