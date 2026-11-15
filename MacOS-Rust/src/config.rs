use std::collections::BTreeMap;
use std::env;
use std::fs;
use std::io;
use std::path::{Path, PathBuf};

pub const DEFAULT_CONFIG: &str = "[Launcher]\nalways_on_top = true\nx = 80\ny = 80\n\n[PSX]\nenabled = true\npath1 = /Applications/Aerowinx.app\ndetect1 = -jar Aerowinx.jar\npath2 =\ndetect2 =\nlabel = PSX\nhidden1 = false\nhidden2 = false\n\n[SimLink]\nenabled = true\npath1 = /Applications/Simlink.app\ndetect1 =\npath2 = /Applications/PSX SimLink Bridge.app\ndetect2 =\nlabel = SimLink\nhidden1 = false\nhidden2 = false\n";

#[derive(Clone, Debug)]
pub struct LaunchTarget {
    pub path: PathBuf,
    pub hidden: bool,
    pub detection: String,
}

#[derive(Clone, Debug)]
pub struct LauncherItem {
    pub label: String,
    pub targets: Vec<LaunchTarget>,
}

#[derive(Clone, Debug)]
pub struct AppConfig {
    pub path: PathBuf,
    pub always_on_top: bool,
    pub x: i32,
    pub y: i32,
    pub items: Vec<LauncherItem>,
}

type Sections = BTreeMap<String, BTreeMap<String, String>>;

pub fn config_dir() -> Result<PathBuf, String> {
    let home = env::var_os("HOME").ok_or("HOME is not available")?;
    Ok(PathBuf::from(home).join("Library/Application Support/PSX Launcher"))
}

pub fn load() -> Result<AppConfig, String> {
    let directory = config_dir()?;
    fs::create_dir_all(&directory).map_err(|e| format!("Cannot create configuration directory:\n{}\n\n{e}", directory.display()))?;
    let path = directory.join("psx_app_launcher.ini");
    if !path.exists() {
        if let Ok(executable) = env::current_exe() {
            if let Some(macos_dir) = executable.parent() {
                let bundled = macos_dir.join("../Resources/psx_app_launcher.ini");
                if bundled.is_file() { let _ = fs::copy(bundled, &path); }
            }
        }
    }
    if !path.exists() {
        fs::write(&path, DEFAULT_CONFIG).map_err(|e| format!("Cannot create configuration:\n{}\n\n{e}", path.display()))?;
    }
    let text = fs::read_to_string(&path).map_err(|e| format!("Cannot read configuration:\n{}\n\n{e}", path.display()))?;
    let (values, order) = parse_ini(text.trim_start_matches('\u{feff}'));
    let launcher = values.get("launcher");
    let always_on_top = launcher.and_then(|v| v.get("always_on_top")).map(|v| parse_bool(v, true)).unwrap_or(true);
    let x = launcher.and_then(|v| v.get("x")).and_then(|v| v.parse().ok()).unwrap_or(80);
    let y = launcher.and_then(|v| v.get("y")).and_then(|v| v.parse().ok()).unwrap_or(80);
    let mut items = Vec::new();
    for (normalized, original) in order {
        if normalized == "launcher" { continue; }
        let Some(section) = values.get(&normalized) else { continue; };
        if !parse_bool(section.get("enabled").map(String::as_str).unwrap_or("true"), true) { continue; }
        let mut targets = Vec::new();
        for index in 1..=2 {
            let raw = section.get(&format!("path{index}")).map(String::as_str).unwrap_or("").trim().trim_matches('"');
            if raw.is_empty() { continue; }
            targets.push(LaunchTarget {
                path: PathBuf::from(expand_path(raw)),
                hidden: parse_bool(section.get(&format!("hidden{index}")).map(String::as_str).unwrap_or("false"), false),
                detection: section.get(&format!("detect{index}")).cloned().unwrap_or_default().trim().to_string(),
            });
        }
        let label = section.get("label").filter(|v| !v.trim().is_empty()).cloned().unwrap_or(original);
        items.push(LauncherItem { label, targets });
    }
    Ok(AppConfig { path, always_on_top, x, y, items })
}

fn parse_ini(text: &str) -> (Sections, Vec<(String, String)>) {
    let mut values = Sections::new();
    let mut order = Vec::new();
    let mut current = String::new();
    for raw in text.lines() {
        let line = raw.trim();
        if line.is_empty() || line.starts_with(';') || line.starts_with('#') { continue; }
        if line.starts_with('[') && line.ends_with(']') {
            let original = line[1..line.len()-1].trim().to_string();
            current = original.to_lowercase();
            if !values.contains_key(&current) { order.push((current.clone(), original)); }
            values.entry(current.clone()).or_default();
        } else if let Some((key, value)) = line.split_once('=') {
            if !current.is_empty() { values.entry(current.clone()).or_default().insert(key.trim().to_lowercase(), value.trim().to_string()); }
        }
    }
    (values, order)
}

fn parse_bool(value: &str, fallback: bool) -> bool {
    match value.trim().to_ascii_lowercase().as_str() {
        "1" | "true" | "yes" | "on" => true,
        "0" | "false" | "no" | "off" => false,
        _ => fallback,
    }
}

fn expand_path(value: &str) -> String {
    if value == "~" { return env::var("HOME").unwrap_or_else(|_| value.to_string()); }
    if let Some(rest) = value.strip_prefix("~/") {
        if let Ok(home) = env::var("HOME") { return PathBuf::from(home).join(rest).to_string_lossy().into_owned(); }
    }
    let mut output = value.to_string();
    for (key, val) in env::vars() { output = output.replace(&format!("${key}"), &val).replace(&format!("${{{key}}}"), &val); }
    output
}

pub fn save_position(path: &Path, x: i32, y: i32) -> io::Result<()> {
    let text = fs::read_to_string(path)?;
    let newline = if text.contains("\r\n") { "\r\n" } else { "\n" };
    let mut lines: Vec<String> = text.split_inclusive('\n').map(str::to_string).collect();
    if text.is_empty() { lines.clear(); }
    let mut start = None;
    let mut end = lines.len();
    for (index, line) in lines.iter().enumerate() {
        let trimmed = line.trim();
        if trimmed.starts_with('[') && trimmed.ends_with(']') {
            if trimmed[1..trimmed.len()-1].trim().eq_ignore_ascii_case("launcher") {
                start = Some(index);
                end = lines[index+1..].iter().position(|candidate| { let c=candidate.trim(); c.starts_with('[')&&c.ends_with(']') }).map(|offset| index+1+offset).unwrap_or(lines.len());
                break;
            }
        }
    }
    if let Some(start) = start {
        let mut found_x=false; let mut found_y=false;
        for line in &mut lines[start+1..end] {
            let clean=line.trim_end_matches(['\r','\n']);
            let Some((key,_))=clean.split_once('=') else { continue; };
            let ending=if line.ends_with("\r\n") {"\r\n"} else if line.ends_with('\n') {"\n"} else {newline};
            if key.trim().eq_ignore_ascii_case("x") { *line=format!("x = {x}{ending}"); found_x=true; }
            else if key.trim().eq_ignore_ascii_case("y") { *line=format!("y = {y}{ending}"); found_y=true; }
        }
        let mut additions=Vec::new();
        if !found_x { additions.push(format!("x = {x}{newline}")); }
        if !found_y { additions.push(format!("y = {y}{newline}")); }
        lines.splice(end..end, additions);
    } else {
        if !text.is_empty() && !text.ends_with('\n') { lines.push(newline.to_string()); }
        lines.extend([format!("[Launcher]{newline}"),format!("x = {x}{newline}"),format!("y = {y}{newline}")]);
    }
    fs::write(path, lines.concat())
}
