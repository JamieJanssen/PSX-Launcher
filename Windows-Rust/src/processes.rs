use crate::config::LaunchTarget;
use serde_json::Value;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::os::windows::process::CommandExt;
use windows_sys::Win32::System::Threading::{BELOW_NORMAL_PRIORITY_CLASS, CREATE_NO_WINDOW};

#[derive(Clone, Debug)]
pub struct WindowsProcess {
    pub pid: u32,
    pub name: String,
    pub executable_path: String,
    pub command_line: String,
}

pub fn inventory() -> Vec<WindowsProcess> {
    let script = "$ErrorActionPreference='SilentlyContinue';[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;Get-CimInstance Win32_Process | Select-Object ProcessId,Name,ExecutablePath,CommandLine | ConvertTo-Json -Compress";
    let output = Command::new(powershell())
        .args([
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ])
        .creation_flags(CREATE_NO_WINDOW | BELOW_NORMAL_PRIORITY_CLASS)
        .output();

    let Ok(output) = output else {
        return Vec::new();
    };
    if !output.status.success() {
        return Vec::new();
    }

    let text = String::from_utf8_lossy(&output.stdout);
    let Ok(value) = serde_json::from_str::<Value>(text.trim_start_matches('\u{feff}').trim()) else {
        return Vec::new();
    };
    let entries: Vec<&Value> = match &value {
        Value::Array(values) => values.iter().collect(),
        Value::Object(_) => vec![&value],
        _ => Vec::new(),
    };

    entries
        .into_iter()
        .filter_map(|entry| {
            let pid = entry.get("ProcessId")?.as_u64()? as u32;
            Some(WindowsProcess {
                pid,
                name: json_string(entry.get("Name")),
                executable_path: json_string(entry.get("ExecutablePath")),
                command_line: json_string(entry.get("CommandLine")),
            })
        })
        .collect()
}

fn json_string(value: Option<&Value>) -> String {
    value.and_then(Value::as_str).unwrap_or_default().to_string()
}

fn powershell() -> PathBuf {
    let root = std::env::var_os("SystemRoot").unwrap_or_else(|| "C:\\Windows".into());
    let preferred = PathBuf::from(root)
        .join("System32")
        .join("WindowsPowerShell")
        .join("v1.0")
        .join("powershell.exe");
    if preferred.exists() {
        preferred
    } else {
        PathBuf::from("powershell.exe")
    }
}

pub fn target_is_running(target: &LaunchTarget, processes: &[WindowsProcess]) -> bool {
    if !target.detection.is_empty() {
        let needle = target.detection.to_lowercase();
        return processes
            .iter()
            .any(|process| process.command_line.to_lowercase().contains(&needle));
    }
    if !target.path.exists() {
        return false;
    }

    let target_path = normalized_path(&target.path);
    let extension = target
        .path
        .extension()
        .and_then(|value| value.to_str())
        .unwrap_or_default()
        .to_ascii_lowercase();
    if extension == "exe" {
        if processes.iter().any(|process| {
            !process.executable_path.is_empty()
                && normalized_text(&process.executable_path) == target_path
        }) {
            return true;
        }
        let filename = target
            .path
            .file_name()
            .and_then(|value| value.to_str())
            .unwrap_or_default();
        return processes
            .iter()
            .any(|process| process.name.eq_ignore_ascii_case(filename));
    }

    processes.iter().any(|process| {
        normalized_text(&process.command_line).contains(&target_path)
    })
}

pub fn matching_processes<'a>(
    target: &LaunchTarget,
    processes: &'a [WindowsProcess],
) -> Vec<&'a WindowsProcess> {
    if !target.detection.is_empty() {
        let needle = target.detection.to_lowercase();
        return processes
            .iter()
            .filter(|process| process.command_line.to_lowercase().contains(&needle))
            .collect();
    }

    let target_path = normalized_path(&target.path);
    let extension = target
        .path
        .extension()
        .and_then(|value| value.to_str())
        .unwrap_or_default()
        .to_ascii_lowercase();
    if extension == "exe" {
        let exact: Vec<_> = processes
            .iter()
            .filter(|process| {
                !process.executable_path.is_empty()
                    && normalized_text(&process.executable_path) == target_path
            })
            .collect();
        if !exact.is_empty() {
            return exact;
        }
        let filename = target
            .path
            .file_name()
            .and_then(|value| value.to_str())
            .unwrap_or_default();
        return processes
            .iter()
            .filter(|process| process.name.eq_ignore_ascii_case(filename))
            .collect();
    }

    processes
        .iter()
        .filter(|process| normalized_text(&process.command_line).contains(&target_path))
        .collect()
}

fn normalized_path(path: &Path) -> String {
    let absolute = path.canonicalize().unwrap_or_else(|_| path.to_path_buf());
    normalized_text(&absolute.to_string_lossy())
}

fn normalized_text(value: &str) -> String {
    value.replace('/', "\\").to_lowercase()
}
