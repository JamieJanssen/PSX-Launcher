use std::{env, path::PathBuf, process::Command};

fn main() {
    println!("cargo:rerun-if-changed=psx.icns");
    println!("cargo:rerun-if-changed=build.rs");
    let output = PathBuf::from(env::var_os("OUT_DIR").expect("OUT_DIR")).join("status-icon.png");
    let status = Command::new("/usr/bin/sips")
        .args(["-s", "format", "png", "psx.icns", "--out"])
        .arg(&output)
        .output()
        .expect("sips is required to convert psx.icns for the menu bar");
    assert!(status.status.success(), "Cannot convert psx.icns: {}", String::from_utf8_lossy(&status.stderr));
}
