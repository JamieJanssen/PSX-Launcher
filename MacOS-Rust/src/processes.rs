use crate::config::{LaunchTarget, LauncherItem};
use plist::Value;
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::io;
use std::thread;

const GENERIC_IDS: [&str; 3] = ["org.pythonmac.unspecified", "org.python.python", "com.apple.scripteditor.id"];

#[derive(Default)]
pub struct Snapshot {
    visibility: HashMap<String, bool>,
    commands: Vec<String>,
}

pub fn snapshot() -> Snapshot {
    let mut result = Snapshot::default();
    let script = "tell application \"System Events\"\nset output to \"\"\nrepeat with p in application processes\ntry\nset output to output & (bundle identifier of p as text) & tab & (visible of p as text) & linefeed\nend try\nend repeat\nreturn output\nend tell";
    if let Ok(output) = Command::new("/usr/bin/nice").args(["-n","15","/usr/bin/osascript","-e",script]).output() {
        if output.status.success() {
            for line in String::from_utf8_lossy(&output.stdout).lines() {
                if let Some((id, visible))=line.split_once('\t') {
                    let id=id.trim().to_lowercase();
                    if !id.is_empty() { let active=visible.trim().eq_ignore_ascii_case("true"); result.visibility.entry(id).and_modify(|v|*v|=active).or_insert(active); }
                }
            }
        }
    }
    if let Ok(output)=Command::new("/usr/bin/nice").args(["-n","15","/bin/ps","-axo","command="]).output() {
        if output.status.success() { result.commands=String::from_utf8_lossy(&output.stdout).lines().map(str::trim).filter(|v|!v.is_empty()).map(str::to_string).collect(); }
    }
    result
}

fn app_info(path: &Path) -> (String,String,PathBuf) {
    let fallback=path.file_stem().and_then(|v|v.to_str()).unwrap_or_default().to_string();
    let mut id=String::new(); let mut executable=fallback;
    if let Ok(Value::Dictionary(dict))=Value::from_file(path.join("Contents/Info.plist")) {
        id=dict.get("CFBundleIdentifier").and_then(Value::as_string).unwrap_or_default().trim().to_string();
        if let Some(value)=dict.get("CFBundleExecutable").and_then(Value::as_string).filter(|v|!v.trim().is_empty()) { executable=value.trim().to_string(); }
    }
    let executable_path=path.join("Contents/MacOS").join(&executable);
    (id,executable,executable_path)
}

pub fn is_running(target:&LaunchTarget,snapshot:&Snapshot)->bool {
    if !target.detection.is_empty() { return snapshot.commands.iter().any(|c|c.contains(&target.detection)); }
    if !target.path.exists() { return false; }
    if target.path.extension().and_then(|v|v.to_str()).map(|v|v.eq_ignore_ascii_case("app"))!=Some(true) {
        let target=target.path.canonicalize().unwrap_or_else(|_|target.path.clone()).to_string_lossy().into_owned();
        return snapshot.commands.iter().any(|c|c==&target||c.starts_with(&(target.clone()+" ")));
    }
    let (id,executable,exe_path)=app_info(&target.path);
    let normalized=id.to_lowercase();
    if !normalized.is_empty()&&!GENERIC_IDS.contains(&normalized.as_str()) {
        if let Some(visible)=snapshot.visibility.get(&normalized) {
            if target.path.file_stem().and_then(|v|v.to_str()).map(|v|v.eq_ignore_ascii_case("volanta")).unwrap_or(false) { return *visible; }
            return true;
        }
    }
    let exe=exe_path.to_string_lossy(); let bundle=target.path.to_string_lossy();
    snapshot.commands.iter().any(|c|c==exe.as_ref()||c.starts_with(&(exe.to_string()+" "))||c.contains(bundle.as_ref())||c.split_whitespace().next().map(|v|v.ends_with(&format!("/{executable}"))).unwrap_or(false))
}

pub fn item_state(item:&LauncherItem,snapshot:&Snapshot)->(bool,bool) {
    let states:Vec<_>=item.targets.iter().map(|t|is_running(t,snapshot)).collect();
    (states.iter().any(|v|*v),!states.is_empty()&&states.iter().all(|v|*v))
}

pub fn launch_item(item:&LauncherItem)->Result<(),String> {
    if item.targets.is_empty() { return Err("Nothing has been configured for this button.".into()); }
    let current=snapshot(); let mut errors=Vec::new();
    for target in &item.targets {
        if !target.path.exists() { errors.push(format!("Not found: {}",target.path.display())); continue; }
        if is_running(target,&current) { continue; }
        let result=if target.path.extension().and_then(|v|v.to_str()).map(|v|v.eq_ignore_ascii_case("app")).unwrap_or(false)||!target.hidden {
            spawn_reaped(Command::new("/usr/bin/open").arg(&target.path).stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null()))
        } else {
            let mut command=Command::new(&target.path); spawn_reaped(command.current_dir(target.path.parent().unwrap_or(Path::new("/"))).stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null()))
        };
        if let Err(error)=result { errors.push(format!("Cannot launch {}: {error}",target.path.display())); }
    }
    if errors.is_empty(){Ok(())}else{Err(errors.join("\n\n"))}
}

pub fn quit_item(item:&LauncherItem)->Result<(),String> {
    let mut errors=Vec::new();
    for target in &item.targets {
        if !target.path.exists(){continue;}
        if target.path.extension().and_then(|v|v.to_str()).map(|v|v.eq_ignore_ascii_case("app"))==Some(true) {
            let(id,_,exe)=app_info(&target.path);
            if !id.is_empty()&&!GENERIC_IDS.contains(&id.to_lowercase().as_str()) {
                let escaped=id.replace('\\',"\\\\").replace('"',"\\\"");
                // Address the application itself, not a System Events process object.
                // The running guard prevents Quit from launching an app that is closed.
                let script = format!(
                    "if application id \"{escaped}\" is running then\n tell application id \"{escaped}\" to quit\nend if"
                );
                match Command::new("/usr/bin/osascript").args(["-e", &script]).output() {
                    Ok(output) if output.status.success() => {}
                    Ok(output) => errors.push(format!(
                        "Cannot quit {}:\n{}",
                        target.path.display(),
                        String::from_utf8_lossy(&output.stderr).trim(),
                    )),
                    Err(error) => errors.push(format!(
                        "Cannot quit {}: {error}", target.path.display(),
                    )),
                }
            } else { terminate_exact(&exe); }
        } else { terminate_exact(&target.path); }
    }
    if errors.is_empty(){Ok(())}else{Err(errors.join("\n"))}
}

fn terminate_exact(path:&Path) {
    let target=path.canonicalize().unwrap_or_else(|_|path.to_path_buf()).to_string_lossy().into_owned();
    if let Ok(output)=Command::new("/bin/ps").args(["-axo","pid=,command="]).output() {
        for line in String::from_utf8_lossy(&output.stdout).lines() {
            let line=line.trim(); let Some((pid,command))=line.split_once(char::is_whitespace) else{continue;}; let command=command.trim();
            if (command==target||command.starts_with(&(target.clone()+" ")))&&let Ok(pid)=pid.parse::<i32>() { unsafe{libc::kill(pid,libc::SIGTERM);} }
        }
    }
}

/// Keep ownership of each child until its exit status has been collected.
/// Waiting is blocking in the OS, not polling, and never blocks the UI thread.
pub fn spawn_reaped(command: &mut Command) -> io::Result<()> {
    let mut child = command.spawn()?;
    thread::spawn(move || {
        loop {
            match child.wait() {
                Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
                _ => break,
            }
        }
    });
    Ok(())
}
