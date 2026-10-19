//! Memory/size tests: the 2x padded fixture and an in-test token-heavy stress spec.

mod common;

use std::io::Write;
use std::path::PathBuf;
use std::time::Instant;

use common::{parse, render, rss_peak, Dir, FOUR_MIB, MIB};
use serde_json::{json, Value};

const RSS_CAP: u64 = 768 * MIB;

#[test]
fn pathological_2x_parses_within_rss_cap() {
    let save = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("fixtures/pathological_2x/save.sav");
    let t = Instant::now();
    let r = parse(&save, &[]);
    eprintln!(
        "pathological_2x: rss_peak_bytes={} wall_s={} test_s={:.2}",
        rss_peak(&r),
        r.json["cost"]["wall_s"],
        t.elapsed().as_secs_f64()
    );
    assert_eq!(r.code, 0, "{}", r.stdout);
    assert!(rss_peak(&r) <= RSS_CAP, "rss {}", rss_peak(&r));
}

/// 2000 countries (1 player, 200 managed with sids), 20000 systems on a hyperlane chain, 2000 starbases
/// with modules, 5000 wars.
fn stress_spec() -> Value {
    let countries: Vec<Value> = (0..2000u64)
        .map(|i| {
            let mut c = json!({
                "country_id": i, "name": format!("Country {i}"), "fleet_power": 1000 + i, "tech_researched": 100,
                "alloys_income": 10 + i % 7, "energy": 5.5, "minerals": 6.25, "alloys": 7,
                "systems": (i * 10..i * 10 + 10).collect::<Vec<u64>>(),
            });
            if i == 0 {
                c["is_player"] = json!(true);
            }
            if i <= 200 {
                c["sid"] = json!(i + 1);
            }
            if (1..=200).contains(&i) {
                c["managed"] = json!(true);
            }
            c
        })
        .collect();
    json!({
        "name": "stress", "date": "2231.04.01", "countries": countries,
        "hyperlanes": (0..19_999u64).map(|i| [i, i + 1]).collect::<Vec<_>>(),
        "contacts": (1..=200u64).map(|i| [0, i]).collect::<Vec<_>>(),
        "starbases": (0..2000u64).map(|i| json!({"system": i * 10, "owner": i, "defense_modules": 3, "platforms": 2})).collect::<Vec<_>>(),
        "wars": (0..5000u64).map(|i| json!({"attackers": [i % 2000], "defenders": [(i + 1) % 2000]})).collect::<Vec<_>>(),
    })
}

#[test]
fn entity_count_stress() {
    let d = Dir::new("stress");
    let spec = d.path("stress.json");
    std::fs::write(&spec, stress_spec().to_string()).unwrap();
    let save = d.path("stress.sav");
    assert_eq!(render(&spec, &save), 0, "render");
    let t = Instant::now();
    let r = parse(&save, &[]);
    eprintln!(
        "stress: save_bytes={} rss_peak_bytes={} wall_s={} stdout_bytes={} test_s={:.2}",
        std::fs::metadata(&save).unwrap().len(),
        rss_peak(&r),
        r.json["cost"]["wall_s"],
        r.stdout.len(),
        t.elapsed().as_secs_f64()
    );
    assert_eq!(r.code, 0, "{}", r.stdout);
    assert!(rss_peak(&r) <= RSS_CAP, "rss {}", rss_peak(&r));
    assert!(r.stdout.len() <= FOUR_MIB);
    assert_eq!(r.json["legal"].as_array().unwrap().len(), 200);
    assert_eq!(r.json["self_state"].as_object().unwrap().len(), 200);
}

const BULK_COUNTRIES: u64 = 2000;
const BULK_TECHS: u64 = 4500;
const BULK_MIN_COUNTRY_BYTES: u64 = 256 * MIB;

/// Write a plain-text gamestate whose `country` section is >= 256 MiB of real tokens: per country a
/// `tech_status` of `technology=".." level=1` pairs, 150 `relation` blocks and a `ship_names` block.
/// Returns the byte count of the `country` section. Deterministic: depends only on the loop indices.
fn write_bulky_save(path: &std::path::Path) -> u64 {
    let mut w = std::io::BufWriter::new(std::fs::File::create(path).unwrap());
    w.write_all(b"version=\"Stress v1\"\ndate=\"2231.04.01\"\nplayer={ { name=\"P\" country=0 } }\ncountry={\n").unwrap();
    let mut bytes = 0u64;
    for id in 0..BULK_COUNTRIES {
        let mut c = String::with_capacity(160_000);
        c.push_str(&format!(
            "{id}={{ name={{ key=\"Country {id}\" }} military_power={}.500\n",
            1000 + id
        ));
        if id < 50 {
            c.push_str(&format!(
                "  flags={{ sae_managed=1 }} variables={{ sae_sid={}.000 }}\n",
                id + 1
            ));
        }
        c.push_str("  tech_status={ ");
        for t in 0..BULK_TECHS {
            c.push_str(&format!("technology=\"tech_x_{t}\" level=1 "));
        }
        c.push_str("}\n  relations_manager={ ");
        for r in 0..150u64 {
            c.push_str(&format!(
                "relation={{ owner={id} country={} contact={} trust=10 }} ",
                (id + r) % BULK_COUNTRIES,
                if r % 3 == 0 { "yes" } else { "no" }
            ));
        }
        c.push_str("}\n  ship_names={ ");
        for n in 0..200u64 {
            c.push_str(&format!("\"NAME_{n}\"={{ counter={n} used=yes }} "));
        }
        c.push_str("}\n}\n");
        bytes += c.len() as u64;
        w.write_all(c.as_bytes()).unwrap();
    }
    w.write_all(b"}\n").unwrap();
    w.flush().unwrap();
    bytes
}

#[test]
fn bulky_country_section_stays_within_rss_cap() {
    let d = Dir::new("bulky");
    let save = d.path("bulky.sav");
    let t = Instant::now();
    let country_bytes = write_bulky_save(&save);
    let file_bytes = std::fs::metadata(&save).unwrap().len();
    assert!(
        country_bytes >= BULK_MIN_COUNTRY_BYTES,
        "country section only {country_bytes} bytes"
    );
    let r = parse(&save, &[]);
    eprintln!(
        "bulky: file_bytes={file_bytes} country_bytes={country_bytes} rss_peak_bytes={} wall_s={} stdout_bytes={} test_s={:.2}",
        rss_peak(&r),
        r.json["cost"]["wall_s"],
        r.stdout.len(),
        t.elapsed().as_secs_f64()
    );
    assert_eq!(r.code, 0, "{}", r.stdout);
    assert!(rss_peak(&r) <= RSS_CAP, "rss {}", rss_peak(&r));
    assert!(r.stdout.len() <= FOUR_MIB);
    assert_eq!(r.json["countries"].as_array().unwrap().len(), 50);
}

/// One `country` entry whose block is ~64 MiB of `a=1 ` (over the 32 MiB per-span cap).
fn write_oversize_span_save(path: &std::path::Path) {
    let mut w = std::io::BufWriter::new(std::fs::File::create(path).unwrap());
    w.write_all(
        b"version=\"v\"\ndate=\"2231.04.01\"\nplayer={ { name=\"P\" country=0 } }\ncountry={\n0={ ",
    )
    .unwrap();
    let chunk = "a=1 ".repeat(1 << 16);
    for _ in 0..(64 * MIB as usize / chunk.len()) {
        w.write_all(chunk.as_bytes()).unwrap();
    }
    w.write_all(b"}\n}\n").unwrap();
    w.flush().unwrap();
}

fn assert_format_error(code: Option<i32>, stdout: &[u8]) {
    assert_eq!(code, Some(4), "{}", String::from_utf8_lossy(stdout));
    let v: Value = serde_json::from_slice(stdout).expect("valid JSON");
    assert_eq!(
        (
            v["status"].as_str(),
            v["error"].as_str(),
            v["exit_code"].as_i64()
        ),
        (Some("error"), Some("format"), Some(4))
    );
    assert!(
        v["detail"].as_str().unwrap().contains("span too large"),
        "{v}"
    );
}

#[test]
fn oversize_span_is_format_error_not_abort() {
    let d = Dir::new("bigspan");
    let save = d.path("big.sav");
    write_oversize_span_save(&save);
    let r = parse(&save, &[]);
    assert_eq!(r.code, 4, "{}", r.stdout);
    assert_eq!(r.json["error"], "format");
    assert!(r.json["detail"]
        .as_str()
        .unwrap()
        .contains("span too large"));

    // Same input under a 1 GiB address-space limit: clean exit 4, never an allocation abort.
    let have_prlimit = std::process::Command::new("prlimit")
        .arg("--version")
        .output()
        .is_ok_and(|o| o.status.success());
    if have_prlimit {
        let o = std::process::Command::new("prlimit")
            .args([
                "--as=1073741824",
                "--",
                env!("CARGO_BIN_EXE_sae-parse"),
                "parse",
                "--save",
            ])
            .arg(&save)
            .output()
            .unwrap();
        assert_format_error(o.status.code(), &o.stdout);
    } else {
        eprintln!("prlimit not found: skipping the address-space sub-assert");
    }
}
