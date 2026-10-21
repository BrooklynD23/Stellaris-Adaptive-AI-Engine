//! Fuzz-style tests: arbitrary and mutated saves never panic, exit in {0,4,5,6}, stdout is one JSON line.

mod common;

use std::io::Read;
use std::path::PathBuf;

use common::{parse, Dir};
use proptest::prelude::*;
use proptest::test_runner::{Config, RngAlgorithm, RngSeed};

fn cfg() -> ProptestConfig {
    ProptestConfig {
        cases: 128,
        failure_persistence: None,
        rng_seed: RngSeed::Fixed(0x5AE),
        ..Config::default()
    }
}

fn turtle_zip() -> Vec<u8> {
    std::fs::read(PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("fixtures/turtle_small/save.sav"))
        .unwrap()
}

fn turtle_gamestate() -> Vec<u8> {
    let mut z = zip::ZipArchive::new(std::io::Cursor::new(turtle_zip())).unwrap();
    let mut gs = Vec::new();
    z.by_name("gamestate")
        .unwrap()
        .read_to_end(&mut gs)
        .unwrap();
    gs
}

/// Parse `bytes` as a save and require a clean outcome.
fn check(dir: &Dir, bytes: &[u8]) {
    let p = dir.path("case.sav");
    std::fs::write(&p, bytes).unwrap();
    let r = parse(&p, &[]);
    assert!(
        [0, 4, 5, 6].contains(&r.code),
        "exit {}: {}",
        r.code,
        r.stdout
    );
    if r.code == 0 {
        check_ok_output(&r.json);
    }
}

fn is_game_date(d: &str) -> bool {
    let b = d.as_bytes();
    b.len() == 10
        && [(0, 4), (5, 7), (8, 10)]
            .iter()
            .all(|(a, z)| b[*a..*z].iter().all(u8::is_ascii_digit))
        && b[4] == b'.'
        && b[7] == b'.'
        && (1..=12).contains(&d[5..7].parse::<u8>().unwrap())
        && (1..=30).contains(&d[8..10].parse::<u8>().unwrap())
}

/// Output invariants for every exit-0 run.
fn check_ok_output(j: &serde_json::Value) {
    assert!(
        is_game_date(j["meta"]["date"].as_str().expect("meta.date string")),
        "date {}",
        j["meta"]["date"]
    );
    for l in j["legal"].as_array().expect("legal array") {
        let o = l.as_object().unwrap();
        assert!(o["observer_sid"].is_u64() && o["in_contact"].is_boolean());
        if o["in_contact"] == false {
            assert_eq!(
                o.len(),
                2,
                "no-contact entry carries only observer_sid and in_contact: {l}"
            );
        }
        for k in ["fleet_power_cat", "tech_cat", "economy_cat"] {
            assert!(
                o.get(k)
                    .is_none_or(|v| v.as_i64().is_some_and(|n| (-2..=2).contains(&n))),
                "{k}: {l}"
            );
        }
    }
    let nonneg = |v: &serde_json::Value, ks: &[&str]| {
        for k in ks {
            assert!(
                v[*k].as_f64().is_some_and(|x| x.is_finite() && x >= 0.0),
                "{k} in {v}"
            );
        }
    };
    let any = |v: &serde_json::Value, ks: &[&str]| {
        for k in ks {
            assert!(v[*k].as_f64().is_some_and(f64::is_finite), "{k} in {v}");
        }
    };
    for s in j["self_state"]
        .as_object()
        .expect("self_state object")
        .values()
    {
        nonneg(
            s,
            &[
                "fleet_power",
                "naval_cap_used",
                "naval_cap",
                "systems_owned",
                "wars",
                "applied_seq",
                "pending_seq",
            ],
        );
        any(s, &["energy", "minerals", "alloys", "alloys_income"]);
    }
    if j["god"].is_object() {
        nonneg(
            &j["god"],
            &[
                "fleet_power",
                "systems_owned",
                "tech_researched",
                "fortified_starbases",
            ],
        );
        any(&j["god"], &["alloys_income"]);
    }
}

fn mutate(mut data: Vec<u8>, flips: &[(usize, u8)], cut: Option<usize>) -> Vec<u8> {
    if !data.is_empty() {
        let n = data.len();
        for (at, v) in flips {
            data[at % n] = *v;
        }
    }
    if let Some(c) = cut {
        data.truncate(c % (data.len() + 1));
    }
    data
}

proptest! {
    #![proptest_config(cfg())]

    #[test]
    fn random_bytes_as_plain_text(bytes in proptest::collection::vec(any::<u8>(), 0..2048)) {
        check(&Dir::new("fz_rand"), &bytes);
    }

    #[test]
    fn random_clausewitz_like_text(s in "[a-z_=\"{} \\n0-9.#\\\\]{0,400}") {
        check(&Dir::new("fz_text"), s.as_bytes());
    }

    #[test]
    fn mutated_zip(flips in proptest::collection::vec((any::<usize>(), any::<u8>()), 0..8), cut in proptest::option::of(any::<usize>())) {
        check(&Dir::new("fz_zip"), &mutate(turtle_zip(), &flips, cut));
    }

    #[test]
    fn mutated_gamestate_text(flips in proptest::collection::vec((any::<usize>(), any::<u8>()), 0..12), cut in proptest::option::of(any::<usize>())) {
        check(&Dir::new("fz_gs"), &mutate(turtle_gamestate(), &flips, cut));
    }

    #[test]
    fn mutated_gamestate_with_brace_noise(at in any::<usize>(), noise in "[{}\"=#]{1,6}") {
        let mut gs = turtle_gamestate();
        let n = gs.len();
        let tail = gs.split_off(at % n);
        gs.extend_from_slice(noise.as_bytes());
        gs.extend(tail);
        check(&Dir::new("fz_brace"), &gs);
    }
}

#[test]
fn deep_nesting_does_not_overflow() {
    let d = Dir::new("fz_deep");
    let p = d.path("deep.sav");
    let head = "date=\"2231.04.01\"\n";
    let open = "{".repeat(1_000_000);
    let close = "}".repeat(1_000_000);
    for (tag, text, want) in [
        ("open_top", format!("{head}x={open}"), vec![5]),
        (
            "open_in_country",
            format!("{head}country={{ 1={open}"),
            vec![5],
        ),
        ("balanced_top", format!("{head}x={open}{close}"), vec![0, 5]),
        (
            "balanced_in_country",
            format!("{head}country={{ 1={{ a={open}{close} }} }}"),
            vec![0, 5],
        ),
        (
            "balanced_in_war",
            format!("{head}war={{ 1={{ attackers={open}{close} }} }}"),
            vec![0, 5],
        ),
    ] {
        std::fs::write(&p, text).unwrap();
        let r = parse(&p, &[]);
        assert!(
            want.contains(&r.code),
            "{tag}: exit {} {}",
            r.code,
            r.stdout
        );
    }
}

#[test]
fn unbalanced_braces_exit_5() {
    let d = Dir::new("fz_unbal");
    let p = d.path("u.sav");
    for text in [
        "date=\"2231.04.01\"\ncountry={ 1={ name=\"x\" }\n",
        "date=\"2231.04.01\"\n}\n",
        "date=\"2231.04.01\"\ncountry={ 1={ } } }\n",
        "date=\"2231.04.01\"\nx=\"never closed\n",
    ] {
        std::fs::write(&p, text).unwrap();
        assert_eq!(parse(&p, &[]).code, 5, "{text:?}");
    }
}

#[test]
fn missing_date_exit_5() {
    let d = Dir::new("fz_nodate");
    let p = d.path("n.sav");
    std::fs::write(&p, "version=\"x\"\nplayer={ }\n").unwrap();
    assert_eq!(parse(&p, &[]).code, 5);
}

#[test]
fn rng_algorithm_import_is_used() {
    // Keeps the deterministic-config imports honest: the algorithm is ChaCha by default.
    assert_eq!(Config::default().rng_algorithm, RngAlgorithm::ChaCha);
}
