//! Helpers shared by the integration tests (each test file includes this module).
#![allow(dead_code)]

use std::path::{Path, PathBuf};
use std::process::Command;

use serde_json::Value;

pub const FOUR_MIB: usize = 4 * 1024 * 1024;

/// Temp dir removed on drop.
pub struct Dir(pub PathBuf);

impl Dir {
    pub fn new(tag: &str) -> Self {
        let p = std::env::temp_dir().join(format!("sae_{tag}_{}", std::process::id()));
        std::fs::create_dir_all(&p).unwrap();
        Dir(p)
    }

    pub fn path(&self, n: &str) -> PathBuf {
        self.0.join(n)
    }
}

impl Drop for Dir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

pub fn bin() -> Command {
    Command::new(env!("CARGO_BIN_EXE_sae-parse"))
}

/// Result of one `parse` run.
pub struct Run {
    pub code: i32,
    pub stdout: String,
    pub json: Value,
}

/// Run `parse --save <path>` and check the stdout contract (one JSON line + LF, <= 4 MiB).
pub fn parse(path: &Path, extra: &[&str]) -> Run {
    let o = bin()
        .args(["parse", "--save"])
        .arg(path)
        .args(extra)
        .output()
        .unwrap();
    let code = o.status.code().expect("killed by signal");
    let stdout = String::from_utf8(o.stdout).expect("stdout is UTF-8");
    assert!(stdout.len() <= FOUR_MIB, "stdout {} bytes", stdout.len());
    assert!(
        stdout.ends_with('\n') && stdout.matches('\n').count() == 1,
        "one JSON line + LF"
    );
    let json: Value = serde_json::from_str(&stdout).expect("stdout is valid JSON");
    assert_eq!(json["schema_version"], 1);
    if code == 0 {
        assert_eq!(json["status"], "ok");
    } else {
        assert_eq!(
            (json["status"].as_str(), json["exit_code"].as_i64()),
            (Some("error"), Some(i64::from(code)))
        );
    }
    Run { code, stdout, json }
}

/// Render a spec file to a save with the binary; returns the exit code.
pub fn render(spec: &Path, out: &Path) -> i32 {
    bin()
        .args(["fixture", "--spec"])
        .arg(spec)
        .arg("--out")
        .arg(out)
        .output()
        .unwrap()
        .status
        .code()
        .unwrap()
}

/// Drop `cost`, `save` and `parser_version` (the keys expected.json omits).
pub fn strip(mut v: Value) -> Value {
    if let Some(o) = v.as_object_mut() {
        for k in ["cost", "save", "parser_version"] {
            o.remove(k);
        }
    }
    v
}

pub const MIB: u64 = 1024 * 1024;

pub fn rss_peak(r: &Run) -> u64 {
    r.json["cost"]["rss_peak_bytes"].as_u64().unwrap()
}
