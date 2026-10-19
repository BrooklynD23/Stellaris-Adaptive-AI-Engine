use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::Command;

const TURTLE: &str = r#"{
  "name": "turtle_small", "date": "2231.04.01",
  "countries": [
    {"country_id": 0, "name": "United Nations of Earth", "sid": 1, "is_player": true,
     "fleet_power": 12010, "tech_researched": 58, "alloys_income": 40, "systems": [1, 2, 3, 4]},
    {"country_id": 7, "name": "Tzynn Empire", "sid": 3, "managed": true, "fleet_power": 8450,
     "naval_cap_used": 110, "naval_cap": 140, "energy": 412.5, "minerals": 1180, "alloys": 96.25,
     "alloys_income": 14.5, "tech_researched": 50, "systems": [10, 11, 12]}],
  "hyperlanes": [[4, 10], [1, 2], [2, 3], [3, 4], [10, 11], [11, 12]],
  "contacts": [[0, 7]],
  "starbases": [{"system": 4, "owner": 0, "defense_modules": 2, "platforms": 6}],
  "wars": []
}"#;

/// Temp dir removed on drop.
struct Dir(PathBuf);

impl Dir {
    fn new(tag: &str) -> Self {
        let p = std::env::temp_dir().join(format!("sae_fx_{}_{tag}", std::process::id()));
        std::fs::create_dir_all(&p).unwrap();
        Dir(p)
    }
    fn path(&self, n: &str) -> PathBuf {
        self.0.join(n)
    }
}

impl Drop for Dir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

fn exit_of(spec: &Path, out: &Path) -> i32 {
    Command::new(env!("CARGO_BIN_EXE_sae-parse"))
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

fn render(d: &Dir, json: &str, tag: &str) -> (i32, Vec<u8>) {
    let spec = d.path(&format!("{tag}.json"));
    let out = d.path(&format!("{tag}.sav"));
    std::fs::write(&spec, json).unwrap();
    let code = exit_of(&spec, &out);
    (code, std::fs::read(&out).unwrap_or_default())
}

fn gamestate_of_zip(bytes: &[u8]) -> Vec<u8> {
    let mut z = zip::ZipArchive::new(std::io::Cursor::new(bytes)).unwrap();
    let names: Vec<String> = (0..z.len())
        .map(|i| z.by_index(i).unwrap().name().to_owned())
        .collect();
    assert_eq!(names, ["meta", "gamestate"]);
    let mut meta = String::new();
    z.by_name("meta")
        .unwrap()
        .read_to_string(&mut meta)
        .unwrap();
    assert!(meta.contains("ironman=no") && meta.contains("name=\"turtle_small\""));
    let mut gs = Vec::new();
    z.by_name("gamestate")
        .unwrap()
        .read_to_end(&mut gs)
        .unwrap();
    gs
}

#[test]
fn turtle_zip_and_plain_parse_and_are_deterministic() {
    let d = Dir::new("turtle");
    let (c1, a) = render(&d, TURTLE, "a");
    let (c2, b) = render(&d, TURTLE, "b");
    assert_eq!((c1, c2), (0, 0));
    assert_eq!(a, b, "deterministic bytes");
    let gs = gamestate_of_zip(&a);
    jomini::TextTape::from_slice(&gs).unwrap();

    let plain = TURTLE.replacen(
        "\"name\": \"turtle_small\",",
        "\"name\": \"turtle_small\", \"format\": \"plain_text\",",
        1,
    );
    let (c, p) = render(&d, &plain, "p");
    assert_eq!(c, 0);
    assert_eq!(p, gs, "plain text equals zip gamestate");
    jomini::TextTape::from_slice(&p).unwrap();
}

#[test]
fn pad_bytes_exact_in_gamestate() {
    let d = Dir::new("pad");
    let json = TURTLE.replacen("\"wars\": []", "\"wars\": [], \"pad_bytes\": 1000000", 1);
    let (c, z) = render(&d, &json, "pad");
    assert_eq!(c, 0);
    let gs = String::from_utf8(gamestate_of_zip(&z)).unwrap();
    let at = gs.find("sae_pad=\"").unwrap() + 9;
    assert_eq!(gs[at..].find('"').unwrap(), 1_000_000);
    jomini::TextTape::from_slice(gs.as_bytes()).unwrap();
}

fn with(json_edit: impl Fn(&mut serde_json::Value)) -> String {
    let mut v: serde_json::Value = serde_json::from_str(TURTLE).unwrap();
    json_edit(&mut v);
    v.to_string()
}

#[test]
fn invalid_specs_exit_2() {
    let d = Dir::new("bad");
    let cases: Vec<(&str, String)> = vec![
        ("dup_cid", with(|v| v["countries"][1]["country_id"] = 0.into())),
        ("two_players", with(|v| v["countries"][1]["is_player"] = true.into())),
        ("dup_sid", with(|v| v["countries"][1]["sid"] = 1.into())),
        ("no_countries", with(|v| v["countries"] = serde_json::json!([]))),
        ("system_twice", with(|v| v["countries"][1]["systems"] = serde_json::json!([4]))),
        ("sb_wrong_owner", with(|v| v["starbases"][0]["owner"] = 7.into())),
        ("sb_twice", with(|v| {
            v["starbases"] = serde_json::json!([
                {"system": 4, "owner": 0}, {"system": 4, "owner": 0}]);
        })),
        ("war_unknown", with(|v| v["wars"] = serde_json::json!([{"attackers": [0], "defenders": [99]}]))),
        ("contact_unknown", with(|v| v["contacts"] = serde_json::json!([[0, 99]]))),
        ("sb_unknown_owner", with(|v| v["starbases"][0]["owner"] = 99.into())),
        ("schema_v2", with(|v| v["schema_version"] = 2.into())),
        ("unknown_field", with(|v| v["bogus"] = 1.into())),
        ("unknown_nested", with(|v| v["countries"][0]["bogus"] = 1.into())),
        ("malformed", "{not json".to_owned()),
        (
            "c4_two_players",
            r#"{"name": "bad", "date": "2231.04.01", "countries": [
              {"country_id": 0, "name": "A", "is_player": true}, {"country_id": 1, "name": "B", "is_player": true}]}"#
                .to_owned(),
        ),
    ];
    for (tag, json) in cases {
        assert_eq!(render(&d, &json, tag).0, 2, "{tag}");
    }
}

#[test]
fn io_errors_exit_3() {
    let d = Dir::new("io");
    assert_eq!(exit_of(&d.path("missing.json"), &d.path("o.sav")), 3);
    let spec = d.path("s.json");
    std::fs::write(&spec, TURTLE).unwrap();
    // --out is a directory.
    assert_eq!(exit_of(&spec, &d.0), 3);
}

#[test]
fn u2_review_validation_rules_exit_2() {
    let d = Dir::new("u2rules");
    let cases: Vec<(&str, String)> = vec![
        ("backslash_name", with(|v| v["name"] = "a\\b".into())),
        (
            "control_game_version",
            with(|v| v["game_version"] = "v\u{7}1".into()),
        ),
        (
            "newline_country_name",
            with(|v| v["countries"][0]["name"] = "a\nb".into()),
        ),
        ("date_unpadded", with(|v| v["date"] = "2231.4.1".into())),
        ("date_garbage", with(|v| v["date"] = "2231.04.0x".into())),
        ("date_backslash", with(|v| v["date"] = "2231.04.0\\".into())),
        (
            "pad_over_2gib",
            with(|v| v["pad_bytes"] = 2_147_483_649u64.into()),
        ),
        (
            "tech_over_cap",
            with(|v| v["countries"][0]["tech_researched"] = 1_000_001.into()),
        ),
        (
            "modules_over_cap",
            with(|v| v["starbases"][0]["defense_modules"] = 1_000_001.into()),
        ),
        (
            "platforms_over_cap",
            with(|v| v["starbases"][0]["platforms"] = 1_000_001.into()),
        ),
        (
            "self_contact",
            with(|v| v["contacts"] = serde_json::json!([[7, 7]])),
        ),
        (
            "self_lane",
            with(|v| v["hyperlanes"] = serde_json::json!([[4, 4]])),
        ),
    ];
    for (tag, json) in cases {
        assert_eq!(render(&d, &json, tag).0, 2, "{tag}");
    }
    // Boundary values are accepted.
    assert_eq!(
        render(
            &d,
            &with(|v| v["countries"][0]["tech_researched"] = 1_000_000.into()),
            "cap_ok"
        )
        .0,
        0
    );
}

#[test]
fn quote_and_non_ascii_names_read_back_unchanged() {
    let d = Dir::new("names");
    let name = "Zy\"nn \u{c9}mpire \u{5e1f}\u{56fd}";
    let json = with(|v| v["countries"][1]["name"] = name.into());
    let (c, _) = render(&d, &json, "n");
    assert_eq!(c, 0);
    let out = Command::new(env!("CARGO_BIN_EXE_sae-parse"))
        .args(["parse", "--save"])
        .arg(d.path("n.sav"))
        .output()
        .unwrap();
    assert_eq!(out.status.code(), Some(0));
    let v: serde_json::Value = serde_json::from_slice(&out.stdout).unwrap();
    assert_eq!(v["countries"][1]["name"], name);
}

#[test]
fn orbital_ids_are_unique_across_starbases() {
    let d = Dir::new("orbitals");
    let json = with(|v| {
        v["countries"][1]["systems"] = serde_json::json!([10, 11, 12]);
        v["starbases"] = serde_json::json!([
            {"system": 4, "owner": 0, "platforms": 3}, {"system": 10, "owner": 7, "platforms": 3}]);
        v["format"] = "plain_text".into();
    });
    let (c, bytes) = render(&d, &json, "o");
    assert_eq!(c, 0);
    let text = String::from_utf8(bytes).unwrap();
    let mut ids: Vec<&str> = Vec::new();
    for part in text.split("orbitals={").skip(1) {
        ids.extend(part.split('}').next().unwrap().split_whitespace());
    }
    let n = ids.len();
    ids.sort_unstable();
    ids.dedup();
    assert_eq!((n, ids.len()), (6, 6), "{text}");
}

/// `/dev/full` accepts opens and fails every write: the render fails midway, and the `--out` path
/// (a symlink to it) must be removed rather than left behind.
#[cfg(unix)]
#[test]
fn failed_write_removes_partial_out_file() {
    if !Path::new("/dev/full").exists() {
        return;
    }
    let d = Dir::new("partial");
    let spec = d.path("s.json");
    std::fs::write(&spec, TURTLE).unwrap();
    let out = d.path("o.sav");
    std::os::unix::fs::symlink("/dev/full", &out).unwrap();
    assert_eq!(exit_of(&spec, &out), 3);
    assert!(
        std::fs::symlink_metadata(&out).is_err(),
        "partial out path was not removed"
    );
}
