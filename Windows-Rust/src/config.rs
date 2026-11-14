use std::collections::BTreeMap;
use std::env;
use std::fs;
use std::io;
use std::path::{Path, PathBuf};

pub const DEFAULT_CONFIG: &str = "[Launcher]\r\nalways_on_top = true\r\nx = 80\r\ny = 80\r\n\r\n[PSX]\r\nenabled = true\r\npath1 = C:\\Games\\Aerowinx\r\ndetect1 = -jar Aerowinx.jar\r\npath2 =\r\ndetect2 =\r\nlabel = PSX\r\nhidden1 = false\r\nhidden2 = false\r\n";

#[derive(Clone, Debug)]
pub struct LaunchTarget {
    pub path: PathBuf,
    pub hidden: bool,
    pub detection: String,
}

#[derive(Clone, Debug)]
pub struct LauncherItem {
    pub section: String,
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

struct ParsedIni {
    values: Sections,
    order: Vec<(String, String)>,
}

pub fn executable_dir() -> io::Result<PathBuf> {
    let exe = env::current_exe()?;
    Ok(exe.parent().unwrap_or_else(|| Path::new(".")).to_path_buf())
}

pub fn load() -> Result<AppConfig, String> {
    let path = executable_dir()
        .map_err(|error| format!("Cannot determine executable directory: {error}"))?
        .join("psx_app_launcher.ini");

    if !path.exists() {
        fs::write(&path, DEFAULT_CONFIG)
            .map_err(|error| format!("Cannot create configuration:\n{}\n\n{error}", path.display()))?;
    }

    let bytes = fs::read(&path)
        .map_err(|error| format!("Cannot read configuration:\n{}\n\n{error}", path.display()))?;
    let text = String::from_utf8(bytes)
        .map_err(|error| format!("Configuration is not valid UTF-8:\n{}\n\n{error}", path.display()))?;
    let parsed = parse_ini(text.trim_start_matches('\u{feff}'));
    let launcher = parsed.values.get("launcher");
    let always_on_top = launcher
        .and_then(|values| values.get("always_on_top"))
        .map(|value| parse_bool(value, true))
        .unwrap_or(true);
    let x = launcher
        .and_then(|values| values.get("x"))
        .and_then(|value| value.parse().ok())
        .unwrap_or(80);
    let y = launcher
        .and_then(|values| values.get("y"))
        .and_then(|value| value.parse().ok())
        .unwrap_or(80);

    let mut items = Vec::new();
    for (section, original_name) in &parsed.order {
        let Some(values) = parsed.values.get(section) else {
            continue;
        };
        if section == "launcher" || !parse_bool(values.get("enabled").map(String::as_str).unwrap_or("true"), true) {
            continue;
        }

        let mut targets = Vec::new();
        for index in 1..=2 {
            let raw_path = values
                .get(&format!("path{index}"))
                .map(String::as_str)
                .unwrap_or("")
                .trim()
                .trim_matches('"');
            if raw_path.is_empty() {
                continue;
            }
            targets.push(LaunchTarget {
                path: PathBuf::from(expand_environment(raw_path)),
                hidden: parse_bool(
                    values
                        .get(&format!("hidden{index}"))
                        .map(String::as_str)
                        .unwrap_or("false"),
                    false,
                ),
                detection: values
                    .get(&format!("detect{index}"))
                    .cloned()
                    .unwrap_or_default()
                    .trim()
                    .to_string(),
            });
        }

        let label = values
            .get("label")
            .filter(|value| !value.trim().is_empty())
            .cloned()
            .unwrap_or_else(|| original_name.clone());
        items.push(LauncherItem {
            section: original_name.clone(),
            label,
            targets,
        });
    }

    Ok(AppConfig {
        path,
        always_on_top,
        x,
        y,
        items,
    })
}

fn parse_ini(text: &str) -> ParsedIni {
    let mut result = Sections::new();
    let mut order = Vec::new();
    let mut section = String::new();
    for raw_line in text.lines() {
        let line = raw_line.trim();
        if line.is_empty() || line.starts_with(';') || line.starts_with('#') {
            continue;
        }
        if line.starts_with('[') && line.ends_with(']') {
            let original_name = line[1..line.len() - 1].trim().to_string();
            section = original_name.to_lowercase();
            if !result.contains_key(&section) {
                order.push((section.clone(), original_name));
            }
            result.entry(section.clone()).or_default();
            continue;
        }
        let Some((key, value)) = line.split_once('=') else {
            continue;
        };
        if section.is_empty() {
            continue;
        }
        result
            .entry(section.clone())
            .or_default()
            .insert(key.trim().to_lowercase(), value.trim().to_string());
    }
    ParsedIni { values: result, order }
}

fn parse_bool(value: &str, fallback: bool) -> bool {
    match value.trim().to_ascii_lowercase().as_str() {
        "1" | "true" | "yes" | "on" => true,
        "0" | "false" | "no" | "off" => false,
        _ => fallback,
    }
}

fn expand_environment(value: &str) -> String {
    let mut output = String::with_capacity(value.len());
    let chars: Vec<char> = value.chars().collect();
    let mut index = 0;
    while index < chars.len() {
        if chars[index] == '%' {
            if let Some(end) = chars[index + 1..].iter().position(|character| *character == '%') {
                let end = index + 1 + end;
                let name: String = chars[index + 1..end].iter().collect();
                if let Ok(expanded) = env::var(&name) {
                    output.push_str(&expanded);
                } else {
                    output.extend(chars[index..=end].iter());
                }
                index = end + 1;
                continue;
            }
        }
        output.push(chars[index]);
        index += 1;
    }
    if output == "~" {
        env::var("USERPROFILE").unwrap_or(output)
    } else if let Some(rest) = output.strip_prefix("~\\").or_else(|| output.strip_prefix("~/")) {
        env::var("USERPROFILE")
            .map(|home| PathBuf::from(home).join(rest).to_string_lossy().into_owned())
            .unwrap_or(output)
    } else {
        output
    }
}

pub fn save_position(path: &Path, x: i32, y: i32) -> io::Result<()> {
    let bytes = fs::read(path)?;
    let had_bom = bytes.starts_with(&[0xef, 0xbb, 0xbf]);
    let text = String::from_utf8_lossy(if had_bom { &bytes[3..] } else { &bytes }).into_owned();
    let newline = if text.contains("\r\n") { "\r\n" } else { "\n" };
    let mut lines: Vec<String> = text
        .split_inclusive('\n')
        .map(ToString::to_string)
        .collect();
    if !text.is_empty() && !text.ends_with('\n') && lines.is_empty() {
        lines.push(text.clone());
    }

    let mut launcher_start = None;
    let mut launcher_end = lines.len();
    for (index, line) in lines.iter().enumerate() {
        let trimmed = line.trim();
        if trimmed.starts_with('[') && trimmed.ends_with(']') {
            if trimmed[1..trimmed.len() - 1].trim().eq_ignore_ascii_case("launcher") {
                launcher_start = Some(index);
                launcher_end = lines[index + 1..]
                    .iter()
                    .position(|candidate| {
                        let candidate = candidate.trim();
                        candidate.starts_with('[') && candidate.ends_with(']')
                    })
                    .map(|offset| index + 1 + offset)
                    .unwrap_or(lines.len());
                break;
            }
        }
    }

    if let Some(start) = launcher_start {
        let mut found_x = false;
        let mut found_y = false;
        for line in &mut lines[start + 1..launcher_end] {
            let without_eol = line.trim_end_matches(['\r', '\n']);
            let Some((key, _)) = without_eol.split_once('=') else {
                continue;
            };
            let ending = if line.ends_with("\r\n") { "\r\n" } else if line.ends_with('\n') { "\n" } else { newline };
            if key.trim().eq_ignore_ascii_case("x") {
                *line = format!("x = {x}{ending}");
                found_x = true;
            } else if key.trim().eq_ignore_ascii_case("y") {
                *line = format!("y = {y}{ending}");
                found_y = true;
            }
        }
        let mut additions = Vec::new();
        if !found_x {
            additions.push(format!("x = {x}{newline}"));
        }
        if !found_y {
            additions.push(format!("y = {y}{newline}"));
        }
        lines.splice(launcher_end..launcher_end, additions);
    } else {
        if !text.is_empty() && !text.ends_with('\r') && !text.ends_with('\n') {
            if let Some(last) = lines.last_mut() {
                last.push_str(newline);
            }
        }
        lines.push(format!("[Launcher]{newline}"));
        lines.push(format!("x = {x}{newline}"));
        lines.push(format!("y = {y}{newline}"));
    }

    let mut output = Vec::new();
    if had_bom {
        output.extend_from_slice(&[0xef, 0xbb, 0xbf]);
    }
    output.extend_from_slice(lines.concat().as_bytes());
    fs::write(path, output)
}
