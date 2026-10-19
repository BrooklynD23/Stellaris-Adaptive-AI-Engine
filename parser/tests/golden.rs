//! Golden tests: for every `fixtures/<name>`, parse(save.sav) and parse(render(spec)) equal expected.json.
//! No byte comparison of zips (dependency versions may change deflate output).

mod common;

use std::path::PathBuf;

use common::{parse, render, strip, Dir, FOUR_MIB};

fn fixtures() -> Vec<PathBuf> {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("fixtures");
    let mut v: Vec<PathBuf> = std::fs::read_dir(root)
        .unwrap()
        .map(|e| e.unwrap().path())
        .filter(|p| p.is_dir())
        .collect();
    v.sort();
    v
}

#[test]
fn every_fixture_matches_expected() {
    let dirs = fixtures();
    assert!(
        dirs.len() >= 9,
        "expected the 9 committed fixtures, found {}",
        dirs.len()
    );
    let tmp = Dir::new("golden");
    for d in dirs {
        let name = d.file_name().unwrap().to_string_lossy().into_owned();
        let expected: serde_json::Value =
            serde_json::from_slice(&std::fs::read(d.join("expected.json")).unwrap()).unwrap();

        let committed = parse(&d.join("save.sav"), &[]);
        assert_eq!(committed.code, 0, "{name}: {}", committed.stdout);
        assert!(committed.stdout.len() <= FOUR_MIB);
        assert_eq!(
            strip(committed.json),
            expected,
            "{name}: committed save.sav"
        );

        let fresh = tmp.path(&format!("{name}.sav"));
        assert_eq!(render(&d.join("spec.json"), &fresh), 0, "{name}: render");
        let rendered = parse(&fresh, &[]);
        assert_eq!(strip(rendered.json), expected, "{name}: render(spec)");
        std::fs::remove_file(fresh).unwrap();
    }
}

#[test]
fn section_subset_and_threads_flag() {
    let save = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("fixtures/turtle_small/save.sav");
    let r = parse(&save, &["--sections", "countries", "--threads", "4"]);
    assert_eq!(r.code, 0);
    assert_eq!(r.json["sections"], serde_json::json!(["meta", "countries"]));
    assert_eq!(r.json["self_state"], serde_json::json!({}));
    assert!(r.json["god"].is_null());
    assert!(r.json["legal"][0].get("systems_owned").is_none());
    let r = parse(&save, &["--sections", "wars"]);
    assert_eq!(
        (
            r.json["countries"].as_array().unwrap().len(),
            r.json["legal"].as_array().unwrap().len()
        ),
        (0, 0)
    );
}
