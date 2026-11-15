use crate::config::{LaunchTarget, LauncherItem};
use crate::processes::{cached_inventory, inventory, matching_processes};
use crate::win::wide;
use std::ffi::OsString;
use std::collections::{HashMap, HashSet};
use std::sync::{Mutex, OnceLock};
use std::time::{Duration, Instant};
use windows_sys::Win32::Foundation::{CloseHandle, GetLastError, ERROR_INVALID_PARAMETER, HANDLE, WAIT_OBJECT_0};
use windows_sys::Win32::System::Threading::{OpenProcess, WaitForSingleObject, PROCESS_SYNCHRONIZE};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::os::windows::process::CommandExt;
use windows_sys::Win32::System::Threading::{CREATE_NEW_CONSOLE, CREATE_NO_WINDOW};
use windows_sys::Win32::UI::Shell::ShellExecuteW;
use windows_sys::Win32::UI::WindowsAndMessaging::{SW_HIDE, SW_SHOWNORMAL};

static PENDING: OnceLock<Mutex<HashMap<PathBuf, Instant>>> = OnceLock::new();
fn pending() -> &'static Mutex<HashMap<PathBuf, Instant>> {
    PENDING.get_or_init(|| Mutex::new(HashMap::new()))
}

pub fn launch_item(item: &LauncherItem) -> Result<(), String> {
    if item.targets.is_empty() {
        return Err("Nothing has been configured for this button.".to_string());
    }

    let processes = cached_inventory();
    let mut errors = Vec::new();
    for target in &item.targets {
        if !target.path.exists() {
            errors.push(format!("Not found: {}", target.path.display()));
            continue;
        }
        if crate::processes::target_is_running(target, &processes) {
            continue;
        }
        let path_key = target.path.canonicalize().unwrap_or_else(|_| target.path.clone());
        {
            let mut pending = pending().lock().unwrap_or_else(|e| e.into_inner());
            let now = Instant::now();
            pending.retain(|_, until| *until > now);
            if pending.contains_key(&path_key) { continue; }
            pending.insert(path_key.clone(), now + Duration::from_secs(12));
        }
        let result = if item.section.eq_ignore_ascii_case("psx") && target.path.is_dir() {
            launch_psx(&target.path, target.hidden)
        } else {
            launch_path(target)
        };
        if let Err(error) = result {
            pending().lock().unwrap_or_else(|e| e.into_inner()).remove(&path_key);
            errors.push(error);
        }
    }

    if errors.is_empty() {
        Ok(())
    } else {
        Err(errors.join("\n\n"))
    }
}

fn launch_psx(root: &Path, hidden: bool) -> Result<(), String> {
    let mut command = Command::new("java.exe");
    command
        .args([
            "-Xmx500m",
            "-Djava.library.path=Interfaces",
            "-jar",
            "Aerowinx.jar",
        ])
        .current_dir(root)
        .stdin(Stdio::null());
    if hidden {
        command
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .creation_flags(CREATE_NO_WINDOW);
    }
    command
        .spawn()
        .map(|_| ())
        .map_err(|error| format!("Cannot start PSX:\n{}\n\n{error}", root.display()))
}

fn launch_path(target: &LaunchTarget) -> Result<(), String> {
    let path = &target.path;
    let extension = path
        .extension()
        .and_then(|value| value.to_str())
        .unwrap_or_default()
        .to_ascii_lowercase();

    match extension.as_str() {
        "lnk" => launch_shortcut(path),
        "exe" => shell_execute(path, path.parent(), target.hidden),
        "bat" | "cmd" => launch_batch(path, target.hidden),
        _ => shell_execute(path, path.parent(), false),
    }
}

fn launch_shortcut(path: &Path) -> Result<(), String> {
    let working_directory = shortcut_working_directory(path)
        .or_else(|| path.parent().map(Path::to_path_buf));
    let mut command = Command::new(
        std::env::var_os("COMSPEC").unwrap_or_else(|| OsString::from("cmd.exe")),
    );
    command
        .arg("/d")
        .arg("/c")
        .arg("start")
        .arg("")
        .arg(path)
        .creation_flags(CREATE_NO_WINDOW)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());

    if let Some(directory) = &working_directory {
        command.current_dir(directory);
        let interfaces = directory.join("Interfaces");
        if interfaces.is_dir() {
            let mut path_value = OsString::from(interfaces.as_os_str());
            if let Some(old_path) = std::env::var_os("PATH") {
                path_value.push(";");
                path_value.push(old_path);
            }
            command.env("PATH", path_value);
        }
    }

    command
        .spawn()
        .map(|_| ())
        .map_err(|error| format!("Cannot start shortcut:\n{}\n\n{error}", path.display()))
}

fn shortcut_working_directory(path: &Path) -> Option<PathBuf> {
    let root = std::env::var_os("SystemRoot").unwrap_or_else(|| "C:\\Windows".into());
    let powershell = PathBuf::from(root)
        .join("System32")
        .join("WindowsPowerShell")
        .join("v1.0")
        .join("powershell.exe");
    let script = "$shortcut=(New-Object -ComObject WScript.Shell).CreateShortcut($env:PSX_LAUNCHER_SHORTCUT);[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;$shortcut.WorkingDirectory";
    let output = Command::new(powershell)
        .args([
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ])
        .env("PSX_LAUNCHER_SHORTCUT", path)
        .creation_flags(CREATE_NO_WINDOW)
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    let directory = PathBuf::from(String::from_utf8_lossy(&output.stdout).trim());
    directory.is_dir().then_some(directory)
}

fn launch_batch(path: &Path, hidden: bool) -> Result<(), String> {
    let mut command = Command::new(
        std::env::var_os("COMSPEC").unwrap_or_else(|| OsString::from("cmd.exe")),
    );
    command
        .args(["/d", "/c"])
        .arg(path)
        .current_dir(path.parent().unwrap_or_else(|| Path::new(".")))
        .stdin(Stdio::null())
        .creation_flags(if hidden { CREATE_NO_WINDOW } else { CREATE_NEW_CONSOLE });
    if hidden {
        command.stdout(Stdio::null()).stderr(Stdio::null());
    }
    command
        .spawn()
        .map(|_| ())
        .map_err(|error| format!("Cannot start:\n{}\n\n{error}", path.display()))
}

pub fn shell_execute(path: &Path, directory: Option<&Path>, hidden: bool) -> Result<(), String> {
    let operation = wide("open");
    let file = wide(path.as_os_str());
    let directory_wide = directory.map(|value| wide(value.as_os_str()));
    let directory_pointer = directory_wide
        .as_ref()
        .map(|value| value.as_ptr())
        .unwrap_or(std::ptr::null());
    let result = unsafe {
        ShellExecuteW(
            0,
            operation.as_ptr(),
            file.as_ptr(),
            std::ptr::null(),
            directory_pointer,
            if hidden { SW_HIDE } else { SW_SHOWNORMAL },
        )
    };
    if result as isize <= 32 {
        Err(format!("Cannot open:\n{}\n\nShellExecuteW returned {}", path.display(), result as isize))
    } else {
        Ok(())
    }
}

// Retain the process handle across taskkill to verify the same process, even
// if Windows reuses its PID. Drop releases the kernel handle in every branch.
struct ProcessHandle(HANDLE);
impl Drop for ProcessHandle {
    fn drop(&mut self) { unsafe { CloseHandle(self.0); } }
}

pub fn quit_item(item: &LauncherItem) -> Result<(), String> {
    // Quit uses a fresh inventory so it never targets a stale cached PID.
    let processes = inventory();
    let own_pid = std::process::id();
    let mut errors = Vec::new();
    let mut seen = HashSet::new();
    for target in &item.targets {
        let key = target.path.canonicalize().unwrap_or_else(|_| target.path.clone());
        pending().lock().unwrap_or_else(|e| e.into_inner()).remove(&key);
        for process in matching_processes(target, &processes) {
            if process.pid == 0 || process.pid == own_pid || !seen.insert(process.pid) {
                continue;
            }
            let raw_handle = unsafe { OpenProcess(PROCESS_SYNCHRONIZE, 0, process.pid) };
            if raw_handle == 0 && unsafe { GetLastError() } == ERROR_INVALID_PARAMETER {
                continue; // Already exited, possibly via another target's process tree.
            }
            let handle = (raw_handle != 0).then(|| ProcessHandle(raw_handle));
            if handle.as_ref().is_some_and(|h| unsafe { WaitForSingleObject(h.0, 0) } == WAIT_OBJECT_0) {
                continue;
            }
            let pid = process.pid.to_string();
            let result = Command::new("taskkill.exe")
                .args(["/PID", &pid, "/T", "/F"])
                .creation_flags(CREATE_NO_WINDOW)
                .stdin(Stdio::null())
                .output();
            match result {
                Ok(output) if output.status.success() => {}
                other => {
                    // An error is harmless only if this exact process has exited.
                    let stopped = handle.as_ref().is_some_and(|h| unsafe {
                        WaitForSingleObject(h.0, 2000) == WAIT_OBJECT_0
                    });
                    if !stopped {
                        let detail = match other {
                            Ok(output) => format!("{} {}", String::from_utf8_lossy(&output.stderr).trim(),
                                String::from_utf8_lossy(&output.stdout).trim()),
                            Err(error) => error.to_string(),
                        };
                        errors.push(format!("Cannot stop PID {} ({}):\n{}", process.pid, process.name, detail.trim()));
                    }
                }
            }
        }
    }
    if errors.is_empty() { Ok(()) } else { Err(errors.join("\n")) }
}
