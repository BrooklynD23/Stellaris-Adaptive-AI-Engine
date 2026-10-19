use std::io::Write;
use std::path::PathBuf;
use std::process::Command;

fn run(args: &[&str]) -> (i32, String) {
    let o = Command::new(env!("CARGO_BIN_EXE_sae-parse"))
        .args(args)
        .output()
        .unwrap();
    (
        o.status.code().unwrap(),
        String::from_utf8(o.stdout).unwrap(),
    )
}

/// Temp file removed on drop.
struct Tmp(PathBuf);

impl std::ops::Deref for Tmp {
    type Target = PathBuf;
    fn deref(&self) -> &PathBuf {
        &self.0
    }
}

impl Drop for Tmp {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.0);
    }
}

fn tmp(name: &str, data: &[u8]) -> Tmp {
    let p = std::env::temp_dir().join(format!("sae_cli_{}_{name}", std::process::id()));
    std::fs::write(&p, data).unwrap();
    Tmp(p)
}

fn zip_bytes(entries: &[(&str, &[u8])]) -> Vec<u8> {
    let mut w = zip::ZipWriter::new(std::io::Cursor::new(Vec::new()));
    let opts =
        zip::write::SimpleFileOptions::default().compression_method(zip::CompressionMethod::Stored);
    for (n, d) in entries {
        w.start_file(*n, opts).unwrap();
        w.write_all(d).unwrap();
    }
    w.finish().unwrap().into_inner()
}

fn expect_error(args: &[&str], code: i32, kind: &str) {
    let (c, out) = run(args);
    assert_eq!(c, code, "{args:?}: {out}");
    assert!(
        out.ends_with('\n') && out.matches('\n').count() == 1,
        "one line + LF: {out:?}"
    );
    let v: serde_json::Value = serde_json::from_str(&out).unwrap();
    assert_eq!(v["status"], "error");
    assert_eq!(v["error"], kind);
    assert_eq!(v["exit_code"], code);
    assert_eq!(v["schema_version"], 1);
    assert!(v["cost"]["rss_peak_bytes"].as_u64().is_some());
    assert!(v["cost"]["cpu_s"].as_f64().unwrap() >= 0.0);
    assert!(v["cost"]["wall_s"].as_f64().unwrap() >= 0.0);
    assert!(v["detail"].as_str().unwrap().chars().count() <= 2000);
}

#[test]
fn version() {
    assert_eq!(run(&["--version"]), (0, "sae-parse 0.1.0\n".to_owned()));
}

#[test]
fn usage_errors() {
    for args in [
        vec![],
        vec!["bogus"],
        vec!["parse", "--save", "x", "--bogus"],
        vec!["parse", "--save", "x", "--sections", "nope"],
        vec!["parse", "--save", "x", "--threads", "0"],
        vec!["parse", "--save", "x", "--threads", "abc"],
        vec!["parse"],
    ] {
        expect_error(&args, 2, "usage");
    }
}

#[test]
fn missing_file_is_input() {
    expect_error(
        &["parse", "--save", "/nonexistent/sae/save.sav"],
        3,
        "input",
    );
}

#[test]
fn binary_is_format() {
    let mut data: Vec<u8> = (0..2000u32)
        .map(|i| (i.wrapping_mul(2654435761) >> 24) as u8)
        .collect();
    data[10] = 0;
    data[0] = 0x7f;
    let p = tmp("bin", &data);
    expect_error(&["parse", "--save", p.to_str().unwrap()], 4, "format");
}

#[test]
fn zip_without_gamestate_is_format() {
    let p = tmp("nogs", &zip_bytes(&[("meta", b"ironman=no\n")]));
    expect_error(&["parse", "--save", p.to_str().unwrap()], 4, "format");
}

#[test]
fn truncated_zip_is_unstable() {
    let z = zip_bytes(&[("meta", b"x=1\n"), ("gamestate", b"date=\"2231.04.01\"\n")]);
    let p = tmp("trunc", &z[..z.len() / 2]);
    expect_error(&["parse", "--save", p.to_str().unwrap()], 6, "unstable");
}

#[test]
fn corrupt_crc_is_unstable() {
    let mut z = zip_bytes(&[("gamestate", b"QQQQQQQQQQQQQQQQQQQQ")]);
    let at = z.windows(4).position(|w| w == b"QQQQ").unwrap();
    z[at + 2] ^= 0xff;
    let p = tmp("crc", &z);
    expect_error(&["parse", "--save", p.to_str().unwrap()], 6, "unstable");
}

#[test]
fn valid_save_parses_with_subset_sections() {
    let p = tmp("ok", &zip_bytes(&[("gamestate", b"date=\"2231.04.01\"\n")]));
    let (c, out) = run(&[
        "parse",
        "--save",
        p.to_str().unwrap(),
        "--sections",
        "meta,wars",
    ]);
    assert_eq!(c, 0, "{out}");
    let v: serde_json::Value = serde_json::from_str(&out).unwrap();
    assert_eq!(v["status"], "ok");
    assert_eq!(v["sections"], serde_json::json!(["meta", "wars"]));
    assert_eq!(v["meta"]["date"], "2231.04.01");
    assert!(
        v["meta"]["ironman"].is_null(),
        "no meta entry: ironman unknown"
    );
}

#[test]
fn zip_with_100_entries_is_format() {
    let names: Vec<String> = (0..100).map(|i| format!("e{i}")).collect();
    let entries: Vec<(&str, &[u8])> = names.iter().map(|n| (n.as_str(), &b""[..])).collect();
    let p = tmp("many", &zip_bytes(&entries));
    expect_error(&["parse", "--save", p.to_str().unwrap()], 4, "format");
}

#[test]
fn out_of_range_sid_is_ignored_and_output_sids_are_in_range() {
    let gs = b"date=\"2231.04.01\"\nplayer={ { name=\"P\" country=0 } }\ncountry={\n\
        0={ name=\"P\" variables={ sae_sid=0 } }\n\
        1={ name=\"A\" variables={ sae_sid=1000001 } }\n\
        2={ name=\"B\" flags={ sae_managed=1 } variables={ sae_sid=5 } }\n}\n";
    let p = tmp("sid", gs);
    let (c, out) = run(&["parse", "--save", p.to_str().unwrap()]);
    assert_eq!(c, 0, "{out}");
    let v: serde_json::Value = serde_json::from_str(&out).unwrap();
    let sids: Vec<_> = v["countries"]
        .as_array()
        .unwrap()
        .iter()
        .map(|c| c["sid"].as_u64())
        .collect();
    assert_eq!(sids, [None, Some(5)]);
    assert!(sids.iter().flatten().all(|s| (1..=1_000_000).contains(s)));
}

#[test]
fn unpadded_date_is_normalised_and_bad_date_is_parse() {
    let ok = tmp("date_ok", b"date=\"2231.4.1\"\n");
    let (c, out) = run(&[
        "parse",
        "--save",
        ok.to_str().unwrap(),
        "--sections",
        "meta",
    ]);
    assert_eq!(c, 0, "{out}");
    assert_eq!(
        serde_json::from_str::<serde_json::Value>(&out).unwrap()["meta"]["date"],
        "2231.04.01"
    );
    let bad = tmp("date_bad", b"date=\"2231.13.1\"\n");
    expect_error(&["parse", "--save", bad.to_str().unwrap()], 5, "parse");
}

fn seq_save(applied: u64) -> Vec<u8> {
    format!(
        "date=\"2231.04.01\"\nplayer={{ {{ name=\"P\" country=0 }} }}\ncountry={{\n\
         0={{ name=\"P\" }}\n\
         1={{ name=\"A\" flags={{ sae_managed=1 }} variables={{ sae_sid=3.000 sae_seq_applied={applied}.000 sae_seq_pending=0.000 }} }}\n}}\n"
    )
    .into_bytes()
}

#[test]
fn seq_at_cap_is_ok_and_above_cap_is_parse_error() {
    let at = tmp("seq_at", &seq_save(1_000_000));
    let (c, out) = run(&["parse", "--save", at.to_str().unwrap()]);
    assert_eq!(c, 0, "{out}");
    let v: serde_json::Value = serde_json::from_str(&out).unwrap();
    assert_eq!(v["self_state"]["3"]["applied_seq"], 1_000_000);
    let over = tmp("seq_over", &seq_save(1_000_001));
    expect_error(&["parse", "--save", over.to_str().unwrap()], 5, "parse");
}
