//! Fixture renderer: `FixtureSpec` (C4 section 5) to a synthetic save.
//!
//! One implementation of the save layout. Every key and shape below is
//! UNVERIFIED A-19/A-30 (M0-11): M0 compares a real autosave against this layout.
//! Output is deterministic: stable ordering, fixed zip timestamp (1980-01-01 00:00), Deflate.

use std::collections::{BTreeMap, BTreeSet};
use std::fs::File;
use std::io::{self, BufWriter, Cursor, Write};
use std::path::Path;

use serde::Deserialize;

use crate::error::{Failure, Kind};

const PAD_CHUNK: usize = 64 * 1024;
const DEFENSE_MODULES: [&str; 3] = ["gun_battery", "missile_battery", "hangar_bay"];

/// Largest `pad_bytes` the renderer accepts (2 GiB).
const MAX_PAD_BYTES: u64 = 2 * 1024 * 1024 * 1024;
/// Largest tech / module / platform count the renderer accepts.
const MAX_COUNT: u64 = 1_000_000;

/// Strings written into quoted values must not contain a backslash or control characters.
fn check_text(what: &str, v: &str) -> Result<(), Failure> {
    if v.contains('\\') || v.chars().any(char::is_control) {
        Err(usage(format!(
            "{what} contains a backslash or control character"
        )))
    } else {
        Ok(())
    }
}

/// `YYYY.MM.DD`, digits only, zero-padded, month 01-12, day 01-30 (the `GameDate` rule).
fn is_game_date(d: &str) -> bool {
    let b = d.as_bytes();
    let shaped = b.len() == 10
        && b.iter().enumerate().all(|(i, c)| {
            if i == 4 || i == 7 {
                *c == b'.'
            } else {
                c.is_ascii_digit()
            }
        });
    let n = |r: std::ops::Range<usize>| d.get(r).and_then(|s| s.parse::<u8>().ok()).unwrap_or(0);
    shaped && (1..=12).contains(&n(5..7)) && (1..=30).contains(&n(8..10))
}

/// Pydantic `FixtureCountry` parity: name non-empty, sid 1..=1_000_000, code caps, finite floats, `ge=0` floats.
fn check_country_ranges(c: &Country) -> Result<(), Failure> {
    if c.name.is_empty() {
        return Err(usage("country name must not be empty"));
    }
    if c.sid.is_some_and(|s| !(1..=MAX_COUNT).contains(&s)) {
        return Err(usage(format!("sid must be in 1..={MAX_COUNT}")));
    }
    if c.applied_seq > MAX_COUNT || c.pending_seq > MAX_COUNT {
        return Err(usage(
            "applied_seq and pending_seq must be <= 1000000 (proposal 007)",
        ));
    }
    if c.applied_econ > 4 || c.applied_mil > 3 {
        return Err(usage("applied_econ must be <= 4 and applied_mil <= 3"));
    }
    let non_negative = [
        ("fleet_power", c.fleet_power),
        ("naval_cap_used", c.naval_cap_used),
        ("naval_cap", c.naval_cap),
    ];
    for (what, v) in non_negative {
        if !v.is_finite() || v < 0.0 {
            return Err(usage(format!("{what} must be finite and >= 0")));
        }
    }
    for (what, v) in [
        ("energy", c.energy),
        ("minerals", c.minerals),
        ("alloys", c.alloys),
        ("alloys_income", c.alloys_income),
    ] {
        if !v.is_finite() {
            return Err(usage(format!("{what} must be finite")));
        }
    }
    Ok(())
}

fn default_one() -> u32 {
    1
}
fn default_game_version() -> String {
    "Cepheus v4.4.4".to_owned()
}

/// Output envelope.
#[derive(Debug, Clone, Copy, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Format {
    /// Zip with `meta` and `gamestate` entries.
    ZipText,
    /// Bare gamestate text.
    PlainText,
}

/// One country (mirrors `FixtureCountry`; the renderer replicates the pydantic range/regex checks).
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Country {
    pub country_id: u64,
    pub name: String,
    #[serde(default)]
    pub sid: Option<u64>,
    #[serde(default)]
    pub is_player: bool,
    #[serde(default)]
    pub managed: bool,
    #[serde(default)]
    pub fleet_power: f64,
    #[serde(default)]
    pub naval_cap_used: f64,
    #[serde(default)]
    pub naval_cap: f64,
    #[serde(default)]
    pub energy: f64,
    #[serde(default)]
    pub minerals: f64,
    #[serde(default)]
    pub alloys: f64,
    #[serde(default)]
    pub alloys_income: f64,
    #[serde(default)]
    pub tech_researched: u64,
    #[serde(default)]
    pub applied_seq: u64,
    #[serde(default)]
    pub pending_seq: u64,
    #[serde(default)]
    pub applied_econ: u64,
    #[serde(default)]
    pub applied_mil: u64,
    #[serde(default)]
    pub systems: Vec<u64>,
}

/// A spec starbase (mirrors `FixtureStarbase`).
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Starbase {
    pub system: u64,
    pub owner: u64,
    #[serde(default)]
    pub defense_modules: u64,
    #[serde(default)]
    pub platforms: u64,
}

/// A spec war (mirrors `FixtureWar`).
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct War {
    pub attackers: Vec<u64>,
    pub defenders: Vec<u64>,
}

/// The whole spec (mirrors `FixtureSpec`).
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Spec {
    #[serde(default = "default_one")]
    pub schema_version: u32,
    pub name: String,
    #[serde(default = "default_game_version")]
    pub game_version: String,
    pub date: String,
    #[serde(default)]
    pub ironman: bool,
    #[serde(default = "default_format")]
    pub format: Format,
    pub countries: Vec<Country>,
    #[serde(default)]
    pub hyperlanes: Vec<(u64, u64)>,
    #[serde(default)]
    pub contacts: Vec<(u64, u64)>,
    #[serde(default)]
    pub starbases: Vec<Starbase>,
    #[serde(default)]
    pub wars: Vec<War>,
    #[serde(default)]
    pub pad_bytes: u64,
}

fn default_format() -> Format {
    Format::ZipText
}

fn usage(msg: impl AsRef<str>) -> Failure {
    Failure::new(
        Kind::Usage,
        format!("invalid fixture spec: {}", msg.as_ref()),
    )
}

/// Parse JSON and run the semantic checks. Any problem is a usage failure (exit 2).
pub fn parse_spec(bytes: &[u8]) -> Result<Spec, Failure> {
    let spec: Spec = serde_json::from_slice(bytes).map_err(|e| usage(e.to_string()))?;
    validate(&spec)?;
    Ok(spec)
}

/// Semantic validation (replicates the pydantic range/regex checks).
pub fn validate(s: &Spec) -> Result<(), Failure> {
    if s.schema_version != 1 {
        return Err(usage(format!(
            "schema_version must be 1, got {}",
            s.schema_version
        )));
    }
    if s.countries.is_empty() {
        return Err(usage("countries must not be empty"));
    }
    for (what, v) in [
        ("name", &s.name),
        ("game_version", &s.game_version),
        ("date", &s.date),
    ] {
        check_text(what, v)?;
    }
    if !is_game_date(&s.date) {
        return Err(usage(
            "date must match YYYY.MM.DD (zero-padded, month 01-12, day 01-30)",
        ));
    }
    if s.name.is_empty()
        || !s
            .name
            .bytes()
            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == b'_')
    {
        return Err(usage("name must match ^[a-z0-9_]+$"));
    }
    if s.pad_bytes > MAX_PAD_BYTES {
        return Err(usage(format!("pad_bytes exceeds {MAX_PAD_BYTES}")));
    }
    let too_big = |v: u64, what: &str| -> Result<(), Failure> {
        if v > MAX_COUNT {
            Err(usage(format!("{what} exceeds {MAX_COUNT}")))
        } else {
            Ok(())
        }
    };
    let mut ids = BTreeSet::new();
    let mut sids = BTreeSet::new();
    let mut owner_of: BTreeMap<u64, u64> = BTreeMap::new();
    for c in &s.countries {
        check_text("country name", &c.name)?;
        check_country_ranges(c)?;
        too_big(c.tech_researched, "tech_researched")?;
        if !ids.insert(c.country_id) {
            return Err(usage(format!("duplicate country_id {}", c.country_id)));
        }
        if let Some(sid) = c.sid {
            if !sids.insert(sid) {
                return Err(usage(format!("duplicate sid {sid}")));
            }
        }
        for sys in &c.systems {
            if owner_of.insert(*sys, c.country_id).is_some() {
                return Err(usage(format!("system {sys} owned more than once")));
            }
        }
    }
    if s.countries.iter().filter(|c| c.is_player).count() > 1 {
        return Err(usage("at most one player country"));
    }
    let known = |id: &u64, what: &str| -> Result<(), Failure> {
        if ids.contains(id) {
            Ok(())
        } else {
            Err(usage(format!("{what} references unknown country {id}")))
        }
    };
    for (a, b) in &s.contacts {
        known(a, "contact")?;
        known(b, "contact")?;
        if a == b {
            return Err(usage(format!("contact pair [{a}, {a}] is a self-contact")));
        }
    }
    let mut sb_systems = BTreeSet::new();
    for sb in &s.starbases {
        known(&sb.owner, "starbase")?;
        too_big(sb.defense_modules, "defense_modules")?;
        too_big(sb.platforms, "platforms")?;
        if owner_of.get(&sb.system) != Some(&sb.owner) {
            return Err(usage(format!(
                "starbase system {} is not owned by country {}",
                sb.system, sb.owner
            )));
        }
        if !sb_systems.insert(sb.system) {
            return Err(usage(format!(
                "more than one starbase in system {}",
                sb.system
            )));
        }
    }
    for (a, b) in &s.hyperlanes {
        if a == b {
            return Err(usage(format!("hyperlane [{a}, {a}] is a self-loop")));
        }
    }
    for w in &s.wars {
        if w.attackers.is_empty() || w.defenders.is_empty() {
            return Err(usage(
                "war attackers and defenders must each have at least one country",
            ));
        }
        for id in w.attackers.iter().chain(&w.defenders) {
            known(id, "war")?;
        }
    }
    if s.countries
        .iter()
        .map(|c| c.country_id)
        .max()
        .and_then(|m| m.checked_add(1))
        .is_none()
    {
        return Err(usage("country_id too large"));
    }
    Ok(())
}

/// Escape a string for a quoted Clausewitz value.
fn q(s: &str) -> String {
    let mut o = String::with_capacity(s.len() + 2);
    o.push('"');
    for ch in s.chars() {
        match ch {
            '"' => o.push_str("\\\""),
            '\\' => o.push_str("\\\\"),
            c if c.is_control() => o.push(' '),
            c => o.push(c),
        }
    }
    o.push('"');
    o
}

/// Fixed-point (3 decimals) like the game; round-trips the values the specs use.
fn fx(v: f64) -> String {
    format!("{v:.3}")
}

fn fxi(v: u64) -> String {
    format!("{v}.000")
}

/// Write `meta`: `version`, `date`, `name`, `ironman` (UNVERIFIED A-18/A-20, M0-11/M0-14).
fn write_meta<W: Write>(s: &Spec, w: &mut W) -> io::Result<()> {
    writeln!(
        w,
        "version={}\ndate={}\nname={}\nironman={}",
        q(&s.game_version),
        q(&s.date),
        q(&s.name),
        if s.ironman { "yes" } else { "no" }
    )
}

fn write_country<W: Write>(s: &Spec, c: &Country, w: &mut W) -> io::Result<()> {
    // UNVERIFIED A-19/A-30 (M0-11): every key below.
    write!(
        w,
        "  {}={{ name={{ key={} }} military_power={}",
        c.country_id,
        q(&c.name),
        fx(c.fleet_power)
    )?;
    write!(
        w,
        " used_naval_capacity={} naval_capacity={}",
        fx(c.naval_cap_used),
        fx(c.naval_cap)
    )?;
    write!(
        w,
        " modules={{ standard_economy_module={{ resources={{ energy={} minerals={} alloys={} }} }} }}",
        fx(c.energy),
        fx(c.minerals),
        fx(c.alloys)
    )?;
    write!(
        w,
        " budget={{ last_month={{ income={{ country_base={{ alloys={} }} }} }} }}",
        fx(c.alloys_income)
    )?;
    write!(w, " tech_status={{")?;
    for i in 0..c.tech_researched {
        write!(w, " technology=\"tech_sae_{i}\" level=1")?;
    }
    write!(w, " }}")?;
    if c.managed {
        write!(w, " flags={{ sae_managed=1 }}")?;
    }
    if let Some(sid) = c.sid {
        write!(
            w,
            " variables={{ sae_sid={} sae_seq_applied={} sae_seq_pending={} sae_applied_econ={} sae_applied_mil={} }}",
            fxi(sid),
            fxi(c.applied_seq),
            fxi(c.pending_seq),
            fxi(c.applied_econ),
            fxi(c.applied_mil)
        )?;
    }
    let partners: Vec<u64> = s
        .contacts
        .iter()
        .filter_map(|(a, b)| {
            if *a == c.country_id {
                Some(*b)
            } else if *b == c.country_id {
                Some(*a)
            } else {
                None
            }
        })
        .collect();
    if !partners.is_empty() {
        write!(w, " relations_manager={{")?;
        for p in partners {
            write!(
                w,
                " relation={{ owner={} country={p} contact=yes }}",
                c.country_id
            )?;
        }
        write!(w, " }}")?;
    }
    writeln!(w, " }}")
}

fn write_galaxy<W: Write>(
    systems: &BTreeSet<u64>,
    lanes: &BTreeMap<u64, BTreeSet<u64>>,
    sb_of: &BTreeMap<u64, u64>,
    w: &mut W,
) -> io::Result<()> {
    writeln!(w, "galactic_object={{")?;
    for sys in systems {
        write!(w, "  {sys}={{")?;
        if let Some(to) = lanes.get(sys) {
            write!(w, " hyperlane={{")?;
            for t in to {
                write!(w, " {{ to={t} }}")?;
            }
            write!(w, " }}")?;
        }
        if let Some(sb) = sb_of.get(sys) {
            write!(w, " starbases={{ {sb} }}")?;
        }
        writeln!(w, " }}")?;
    }
    writeln!(w, "}}")
}

/// Stream the whole gamestate into `w` (padding in chunks, never a big String).
pub fn write_gamestate<W: Write>(s: &Spec, w: &mut W) -> io::Result<()> {
    // UNVERIFIED A-19/A-30 (M0-11): top-level version/date first.
    writeln!(w, "version={}\ndate={}", q(&s.game_version), q(&s.date))?;
    // UNVERIFIED A-19 (M0-11): player block.
    match s.countries.iter().find(|c| c.is_player) {
        Some(p) => writeln!(
            w,
            "player={{ {{ name={} country={} }} }}",
            q(&p.name),
            p.country_id
        )?,
        None => writeln!(w, "player={{ }}")?,
    }
    writeln!(w, "country={{")?;
    for c in &s.countries {
        write_country(s, c, w)?;
    }
    let dead = s
        .countries
        .iter()
        .map(|c| c.country_id)
        .max()
        .map_or(0, |m| m.saturating_add(1));
    writeln!(w, "  {dead}=none\n}}")?; // UNVERIFIED A-19 (M0-11): dead country shape.

    let mut owner_of: BTreeMap<u64, u64> = BTreeMap::new();
    for c in &s.countries {
        for sys in &c.systems {
            owner_of.insert(*sys, c.country_id);
        }
    }
    let mut systems: BTreeSet<u64> = owner_of.keys().copied().collect();
    let mut lanes: BTreeMap<u64, BTreeSet<u64>> = BTreeMap::new();
    for (a, b) in &s.hyperlanes {
        systems.insert(*a);
        systems.insert(*b);
        lanes.entry(*a).or_default().insert(*b);
        lanes.entry(*b).or_default().insert(*a);
    }
    // One outpost starbase per owned system; ids follow ascending system id.
    let sb_of: BTreeMap<u64, u64> = owner_of
        .keys()
        .zip(1u64..)
        .map(|(sys, id)| (*sys, id))
        .collect();
    // UNVERIFIED A-19 (M0-11): galactic_object + starbase_mgr shapes.
    write_galaxy(&systems, &lanes, &sb_of, w)?;
    writeln!(w, "starbase_mgr={{\n  starbases={{")?;
    let mut next_orbital: u64 = 1_000_000; // one counter across all starbases: ids never collide
    for (sys, id) in &sb_of {
        let owner = owner_of.get(sys).copied().unwrap_or(0);
        let spec_sb = s.starbases.iter().find(|b| b.system == *sys);
        let (mods, plats) = spec_sb.map_or((0, 0), |b| (b.defense_modules, b.platforms));
        write!(
            w,
            "    {id}={{ owner={owner} level=\"starbase_level_outpost\" modules={{"
        )?;
        for (i, name) in (0..mods).zip(DEFENSE_MODULES.iter().cycle()) {
            write!(w, " {i}={name}")?;
        }
        write!(w, " }} orbitals={{")?;
        for _ in 0..plats {
            write!(w, " {next_orbital}")?;
            next_orbital += 1;
        }
        writeln!(w, " }} }}")?;
    }
    writeln!(w, "  }}\n}}")?;
    if !s.wars.is_empty() {
        writeln!(w, "war={{")?; // UNVERIFIED A-19 (M0-11): war shape.
        for (i, war) in s.wars.iter().enumerate() {
            write!(w, "  {i}={{ attackers={{")?;
            for a in &war.attackers {
                write!(w, " {{ country={a} }}")?;
            }
            write!(w, " }} defenders={{")?;
            for d in &war.defenders {
                write!(w, " {{ country={d} }}")?;
            }
            writeln!(w, " }} }}")?;
        }
        writeln!(w, "}}")?;
    }
    write_pad(s.pad_bytes, w)
}

/// `sae_pad="<exactly n 'x' bytes>"`: n counts only the x characters, not the key, quotes or LF.
fn write_pad<W: Write>(n: u64, w: &mut W) -> io::Result<()> {
    if n == 0 {
        return Ok(());
    }
    w.write_all(b"sae_pad=\"")?;
    let chunk = [b'x'; PAD_CHUNK];
    let mut left = n;
    while left > 0 {
        let take = usize::try_from(left).unwrap_or(PAD_CHUNK).min(PAD_CHUNK);
        w.write_all(chunk.get(..take).unwrap_or(&chunk))?;
        left -= take as u64;
    }
    w.write_all(b"\"\n")
}

/// Render the save into `w`. Zip needs a seekable sink, so it goes through an in-memory buffer
/// (compressed size only; padding streams through the deflater in chunks).
pub fn render<W: Write>(s: &Spec, w: &mut W) -> io::Result<()> {
    match s.format {
        Format::PlainText => write_gamestate(s, w),
        Format::ZipText => {
            let mut zw = zip::ZipWriter::new(Cursor::new(Vec::new()));
            let opts = zip::write::SimpleFileOptions::default()
                .compression_method(zip::CompressionMethod::Deflated)
                .last_modified_time(zip::DateTime::default()); // 1980-01-01 00:00:00
            let zerr = |e: zip::result::ZipError| io::Error::other(e.to_string());
            zw.start_file("meta", opts).map_err(zerr)?;
            write_meta(s, &mut zw)?;
            zw.start_file("gamestate", opts).map_err(zerr)?;
            write_gamestate(s, &mut zw)?;
            let bytes = zw.finish().map_err(zerr)?.into_inner();
            w.write_all(&bytes)
        }
    }
}

/// `sae-parse fixture`: read spec, validate, render to `out`.
pub fn run(spec_path: &Path, out: &Path) -> Result<(), Failure> {
    let bytes = std::fs::read(spec_path).map_err(|e| {
        Failure::new(
            Kind::Input,
            format!("cannot read spec {}: {e}", spec_path.display()),
        )
    })?;
    let spec = parse_spec(&bytes)?;
    let io_fail =
        |e: io::Error| Failure::new(Kind::Input, format!("cannot write {}: {e}", out.display()));
    let file = File::create(out).map_err(io_fail)?;
    let mut w = BufWriter::new(file);
    let res = render(&spec, &mut w).and_then(|()| w.flush());
    drop(w);
    if let Err(e) = res {
        // Never leave a partial save behind.
        let _ = std::fs::remove_file(out);
        return Err(io_fail(e));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    const TURTLE: &str = r#"{"name":"turtle_small","date":"2231.04.01","countries":[
      {"country_id":0,"name":"United Nations of Earth","sid":1,"is_player":true,"fleet_power":12010,
       "tech_researched":3,"alloys_income":40,"systems":[1,2,3,4]},
      {"country_id":7,"name":"Tzynn Empire","sid":3,"managed":true,"energy":412.5,"alloys":96.25,"systems":[10,11,12]}],
      "hyperlanes":[[4,10],[1,2]],"contacts":[[0,7]],
      "starbases":[{"system":4,"owner":0,"defense_modules":2,"platforms":6}]}"#;

    fn text(json: &str) -> String {
        let spec = parse_spec(json.as_bytes()).unwrap();
        let mut v = Vec::new();
        write_gamestate(&spec, &mut v).unwrap();
        String::from_utf8(v).unwrap()
    }

    #[test]
    fn layout_has_key_shapes() {
        let t = text(TURTLE);
        assert!(t.starts_with("version=\"Cepheus v4.4.4\"\ndate=\"2231.04.01\"\n"));
        assert!(t.contains("player={ { name=\"United Nations of Earth\" country=0 } }"));
        assert!(t.contains("energy=412.500"));
        assert!(t.contains("8=none") || t.contains("  8=none"));
        assert!(t.contains("sae_sid=3.000"));
        assert!(t.contains("flags={ sae_managed=1 }"));
        assert!(t.contains("relation={ owner=7 country=0 contact=yes }"));
        assert!(t.contains("0=gun_battery 1=missile_battery"));
        assert!(t.contains("{ to=4 }") && t.contains("{ to=10 }"));
        assert_eq!(t.matches("technology=").count(), 3);
    }

    #[test]
    fn pad_is_exact() {
        let t = text(&TURTLE.replace("\"countries\"", "\"pad_bytes\":150000,\"countries\""));
        let at = t.find("sae_pad=\"").unwrap() + 9;
        let rest = t.get(at..).unwrap();
        assert_eq!(rest.find('"').unwrap(), 150_000);
    }

    #[test]
    fn quotes_are_escaped() {
        assert_eq!(q("a\"b\\c\nd"), "\"a\\\"b\\\\c d\"");
    }

    #[test]
    fn pydantic_parity_checks_reject() {
        let cases: [(&str, &str, &str); 14] = [
            (
                "\"name\":\"turtle_small\"",
                "\"name\":\"Turtle\"",
                "name upper",
            ),
            ("\"name\":\"turtle_small\"", "\"name\":\"\"", "name empty"),
            (
                "\"name\":\"United Nations of Earth\"",
                "\"name\":\"\"",
                "country name empty",
            ),
            ("\"sid\":1,", "\"sid\":0,", "sid 0"),
            ("\"sid\":1,", "\"sid\":1000001,", "sid high"),
            (
                "\"tech_researched\":3,",
                "\"applied_econ\":5,\"tech_researched\":3,",
                "econ",
            ),
            (
                "\"tech_researched\":3,",
                "\"applied_mil\":4,\"tech_researched\":3,",
                "mil",
            ),
            (
                "\"tech_researched\":3,",
                "\"applied_seq\":1000001,\"tech_researched\":3,",
                "seq high",
            ),
            (
                "\"tech_researched\":3,",
                "\"pending_seq\":1000001,\"tech_researched\":3,",
                "pending high",
            ),
            (
                "\"fleet_power\":12010",
                "\"fleet_power\":-1",
                "negative fleet",
            ),
            (
                "\"tech_researched\":3,",
                "\"naval_cap\":-0.5,\"tech_researched\":3,",
                "negative cap",
            ),
            ("\"2231.04.01\"", "\"2231.4.1\"", "unpadded date"),
            ("\"2231.04.01\"", "\"2231.13.01\"", "month 13"),
            (
                "\"contacts\":[[0,7]],",
                "\"wars\":[{\"attackers\":[],\"defenders\":[7]}],",
                "empty war side",
            ),
        ];
        assert!(parse_spec(TURTLE.as_bytes()).is_ok());
        let at_cap = TURTLE.replacen(
            "\"tech_researched\":3,",
            "\"applied_seq\":1000000,\"tech_researched\":3,",
            1,
        );
        assert!(parse_spec(at_cap.as_bytes()).is_ok(), "seq == cap is valid");
        for (from, to, tag) in cases {
            assert!(TURTLE.contains(from), "{tag}: fixture text changed");
            let e = parse_spec(TURTLE.replacen(from, to, 1).as_bytes()).unwrap_err();
            assert_eq!(e.kind, Kind::Usage, "{tag}");
        }
    }
}
