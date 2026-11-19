//! Meta-only fast path (C4 section 1, proposal 032): `--sections meta` must equal the forced full path.
//! The full path is forced with the debug-only `SAE_PARSE_FORCE_FULL` test hook (no CLI flag exists).
//! UNVERIFIED on real saves (A-47): M0-11 checks that a real `meta` entry holds `date` and `version`.

mod common;

use std::io::Write;
use std::path::{Path, PathBuf};

use common::{bin, parse, render, rss_peak, Dir, Run};
use serde_json::{json, Value};

fn run(path: &Path, full: bool) -> Run {
    // Same checks as `parse`, plus the hook env on the full path.
    if !full {
        return parse(path, &["--sections", "meta"]);
    }
    let o = bin()
        .args(["parse", "--sections", "meta", "--save"])
        .arg(path)
        .env("SAE_PARSE_FORCE_FULL", "1")
        .output()
        .unwrap();
    let stdout = String::from_utf8(o.stdout).unwrap();
    let json: Value = serde_json::from_str(&stdout).unwrap();
    Run {
        code: o.status.code().unwrap(),
        stdout,
        json,
    }
}

fn no_cost(mut v: Value) -> Value {
    v.as_object_mut().unwrap().remove("cost");
    v
}

fn zip_of(entries: &[(&str, &[u8])]) -> Vec<u8> {
    let mut w = zip::ZipWriter::new(std::io::Cursor::new(Vec::new()));
    let o =
        zip::write::SimpleFileOptions::default().compression_method(zip::CompressionMethod::Stored);
    for (n, d) in entries {
        w.start_file(*n, o).unwrap();
        w.write_all(d).unwrap();
    }
    w.finish().unwrap().into_inner()
}

type Entries<'a> = Vec<(&'a str, &'a [u8])>;

const META: &[u8] = b"version=\"Stellaris v1\"\ndate=\"2231.04.01\"\nironman=yes\n";
const GS: &[u8] = b"version=\"Stellaris v1\"\ndate=\"2231.04.01\"\n";

fn assert_same(path: &Path, label: &str) {
    let (fast, full) = (run(path, false), run(path, true));
    assert_eq!(fast.code, full.code, "{label}: exit code");
    assert_eq!(no_cost(fast.json), no_cost(full.json), "{label}: json");
}

fn fixtures() -> Vec<PathBuf> {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("fixtures");
    let mut v: Vec<PathBuf> = std::fs::read_dir(root)
        .unwrap()
        .map(|e| e.unwrap().path().join("save.sav"))
        .collect();
    v.sort();
    v
}

#[test]
fn every_fixture_fast_equals_full() {
    let all = fixtures();
    assert!(all.len() >= 9);
    for p in all {
        assert_same(&p, &p.display().to_string());
    }
}

fn synthetic_spec(pad: u64) -> Value {
    json!({
        "name": "synth", "date": "2231.04.01", "pad_bytes": pad,
        "countries": (0..50u64).map(|i| {
            let mut c = json!({"country_id": i, "name": format!("C{i}"), "fleet_power": 100 + i});
            if i == 0 { c["is_player"] = json!(true); }
            c
        }).collect::<Vec<_>>(),
    })
}

fn synthetic_save(d: &Dir, pad: u64) -> PathBuf {
    let (spec, save) = (d.path("synth.json"), d.path("synth.sav"));
    std::fs::write(&spec, synthetic_spec(pad).to_string()).unwrap();
    assert_eq!(render(&spec, &save), 0);
    save
}

#[test]
fn synthetic_save_fast_equals_full() {
    let d = Dir::new("mfp_synth");
    assert_same(&synthetic_save(&d, 0), "synthetic");
}

#[test]
fn meta_only_time_and_rss_vs_full() {
    let d = Dir::new("mfp_cost");
    let save = synthetic_save(&d, 200 * 1024 * 1024);
    let fast = run(&save, false);
    let full = parse(&save, &["--sections", "meta,wars"]); // any other section set takes the full path
    assert_eq!((fast.code, full.code), (0, 0));
    eprintln!(
        "meta-only: wall_s={} rss_peak_bytes={} | full: wall_s={} rss_peak_bytes={}",
        fast.json["cost"]["wall_s"],
        rss_peak(&fast),
        full.json["cost"]["wall_s"],
        rss_peak(&full)
    );
    assert!(rss_peak(&fast) < rss_peak(&full));
}

#[test]
fn corrupt_gamestate_is_the_one_accepted_divergence() {
    let d = Dir::new("mfp_corrupt");
    let p = d.path("c.sav");
    std::fs::write(&p, zip_of(&[("meta", META), ("gamestate", b"x={{{ \"")])).unwrap();
    let (fast, full) = (run(&p, false), run(&p, true));
    assert_eq!(fast.code, 0, "{}", fast.stdout);
    assert_ne!(full.code, 0, "{}", full.stdout);
}

#[test]
fn meta_missing_date_or_version_uses_full_path() {
    let d = Dir::new("mfp_partial");
    let p = d.path("p.sav");
    for (label, meta) in [
        ("no date", &b"version=\"V\"\nironman=no\n"[..]),
        ("no version", &b"date=\"2231.04.01\"\nironman=no\n"[..]),
    ] {
        // corrupt gamestate: only the full path (which reads it) can fail
        std::fs::write(&p, zip_of(&[("meta", meta), ("gamestate", b"x={{{ \"")])).unwrap();
        assert_ne!(run(&p, false).code, 0, "{label}: must take full path");
        // valid gamestate supplies the missing field; results equal
        std::fs::write(&p, zip_of(&[("meta", meta), ("gamestate", GS)])).unwrap();
        assert_same(&p, label);
        assert_eq!(run(&p, false).code, 0, "{label}");
    }
}

#[test]
fn malformed_date_and_missing_gamestate_same_code_on_both_paths() {
    let d = Dir::new("mfp_errs");
    let p = d.path("e.sav");
    let bad_date = b"version=\"V\"\ndate=\"2231.13.40\"\n";
    let cases: [(&str, Entries); 3] = [
        ("bad date", vec![("meta", bad_date), ("gamestate", GS)]),
        ("no gamestate", vec![("meta", META)]),
        ("no gamestate, no meta", vec![("other", b"x")]),
    ];
    for (label, entries) in cases {
        std::fs::write(&p, zip_of(&entries)).unwrap();
        let (fast, full) = (run(&p, false), run(&p, true));
        assert_ne!(fast.code, 0, "{label}");
        assert_eq!(fast.code, full.code, "{label}");
        assert_eq!(fast.json["error"], full.json["error"], "{label}");
    }
}
