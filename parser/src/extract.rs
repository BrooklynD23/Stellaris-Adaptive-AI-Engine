//! Extraction: gamestate bytes to a plain-struct [`SaveModel`].
//!
//! The splitter yields spans; each span is parsed by jomini into a flat token tape, read into plain
//! structs, and the tape is dropped before the next span. Our walkers are loops over the flat tape
//! (no recursion), so nesting depth is bounded by the tape, not the stack.
//!
//! Every key path here is UNVERIFIED A-19/A-30 (M0-11 compares a real autosave); see `README.md`.

use std::collections::{BTreeMap, BTreeSet};

use jomini::text::TextToken;
use jomini::TextTape;

use crate::error::{Failure, Kind};
use crate::log::log;
use crate::output::SaveMeta;
use crate::split::{inner, split, Entry};

/// `Sid` range in the C4 contract (`ge=1, le=1_000_000`).
const MAX_SID: u64 = 1_000_000;
/// `CountryInfo.name` max length in characters.
const MAX_NAME_CHARS: usize = 256;
/// Largest span handed to jomini. The token tape is about 10x the span, so 32 MiB bounds the tape near
/// 320 MiB; with the 400 MiB text buffer that stays under the 1 GiB commit cap.
pub const MAX_SPAN: usize = 32 * 1024 * 1024;

const DEFENSE_MODULES: [&[u8]; 3] = [b"gun_battery", b"missile_battery", b"hangar_bay"];

/// Which top-level keys to read, derived from the requested sections.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct Need {
    /// `player` and `country` (sections countries, fleets or tech).
    pub countries: bool,
    /// `galactic_object` and `starbase_mgr`.
    pub starbases: bool,
    /// `war`.
    pub wars: bool,
}

/// One country the model keeps: the player or any country with `sae_sid`.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct CountryRec {
    pub id: u64,
    pub name: String,
    pub sid: Option<u64>,
    pub managed: bool,
    pub seq_applied: Option<u64>,
    pub seq_pending: Option<u64>,
    pub applied_econ: Option<u64>,
    pub applied_mil: Option<u64>,
    pub military_power: Option<f64>,
    pub naval_used: Option<f64>,
    pub naval_cap: Option<f64>,
    pub energy: Option<f64>,
    pub minerals: Option<f64>,
    pub alloys: Option<f64>,
    pub alloys_income: Option<f64>,
    /// `None` when the country has no `tech_status`.
    pub tech_count: Option<u64>,
    /// Partners this country's relation blocks mark `contact=yes`.
    pub contacts: BTreeSet<u64>,
}

/// A system: hyperlane targets and starbase ids.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct SystemRec {
    pub lanes: Vec<u64>,
    pub starbases: Vec<u64>,
}

/// A starbase: owner and defense modules plus platforms.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct StarbaseRec {
    pub owner: Option<u64>,
    pub defenses: u64,
}

/// A war: country ids per side.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct WarRec {
    pub attackers: Vec<u64>,
    pub defenders: Vec<u64>,
}

/// Everything the feature functions need.
#[derive(Debug, Clone, PartialEq)]
pub struct SaveModel {
    pub meta: SaveMeta,
    pub player: Option<u64>,
    pub countries: Vec<CountryRec>,
    pub systems: BTreeMap<u64, SystemRec>,
    pub starbases: BTreeMap<u64, StarbaseRec>,
    pub wars: Vec<WarRec>,
}

fn parse_err(msg: impl AsRef<str>) -> Failure {
    Failure::new(Kind::Parse, msg)
}

// ---- flat-tape helpers -------------------------------------------------------------------------

type Toks<'t, 'a> = &'t [TextToken<'a>];

/// Index after the value starting at `i` (containers jump over their body).
fn after(t: Toks<'_, '_>, i: usize) -> usize {
    match t.get(i) {
        Some(TextToken::Object { end, .. } | TextToken::Array { end, .. }) => end.saturating_add(1),
        _ => i + 1,
    }
}

fn container_end(t: Toks<'_, '_>, i: usize) -> Option<usize> {
    match t.get(i) {
        Some(TextToken::Object { end, .. } | TextToken::Array { end, .. }) => Some(*end),
        _ => None,
    }
}

fn scalar<'a>(t: Toks<'_, 'a>, i: usize) -> Option<&'a [u8]> {
    t.get(i)?.as_scalar().map(|s| s.as_bytes())
}

/// `(key, value index)` pairs between `pos` and `end`; non-scalar keys and mixed markers are skipped.
struct Pairs<'t, 'a> {
    t: Toks<'t, 'a>,
    pos: usize,
    end: usize,
}

impl<'a> Iterator for Pairs<'_, 'a> {
    type Item = (&'a [u8], usize);

    fn next(&mut self) -> Option<Self::Item> {
        while self.pos < self.end {
            let Some(key) = scalar(self.t, self.pos) else {
                self.pos = after(self.t, self.pos);
                continue;
            };
            let mut v = self.pos + 1;
            if matches!(self.t.get(v), Some(TextToken::Operator(_))) {
                v += 1;
            }
            if v >= self.end {
                self.pos = self.end;
                return None;
            }
            self.pos = after(self.t, v);
            return Some((key, v));
        }
        None
    }
}

/// Pairs of the whole tape (a wrapped `key=value` span has no root container).
fn root<'t, 'a>(t: Toks<'t, 'a>) -> Pairs<'t, 'a> {
    Pairs {
        t,
        pos: 0,
        end: t.len(),
    }
}

/// Pairs of the container at `i` (empty if `i` is not a container).
fn pairs<'t, 'a>(t: Toks<'t, 'a>, i: usize) -> Pairs<'t, 'a> {
    match container_end(t, i) {
        Some(end) => Pairs { t, pos: i + 1, end },
        None => Pairs { t, pos: 0, end: 0 },
    }
}

/// Value indices of the container at `i`: pair values of an object, elements of an array.
fn values(t: Toks<'_, '_>, i: usize) -> Vec<usize> {
    let Some(end) = container_end(t, i) else {
        return Vec::new();
    };
    if matches!(t.get(i), Some(TextToken::Object { .. })) {
        return pairs(t, i).map(|(_, v)| v).collect();
    }
    let mut out = Vec::new();
    let mut p = i + 1;
    while p < end {
        if !matches!(t.get(p), Some(TextToken::MixedContainer)) {
            out.push(p);
        }
        p = after(t, p);
    }
    out
}

/// First pair named `key` in the container at `i`.
fn find(t: Toks<'_, '_>, i: usize, key: &[u8]) -> Option<usize> {
    pairs(t, i).find(|(k, _)| *k == key).map(|(_, v)| v)
}

fn path(t: Toks<'_, '_>, mut i: usize, keys: &[&[u8]]) -> Option<usize> {
    for k in keys {
        i = find(t, i, k)?;
    }
    Some(i)
}

fn num(t: Toks<'_, '_>, i: usize) -> Option<f64> {
    let v: f64 = std::str::from_utf8(scalar(t, i)?)
        .ok()?
        .trim()
        .parse()
        .ok()?;
    v.is_finite().then_some(v)
}

/// Fixed-point ints (`3.000`) are accepted; a non-integral or negative value counts as missing.
fn uint(t: Toks<'_, '_>, i: usize) -> Option<u64> {
    let v = num(t, i)?;
    // 2^53: f64 holds integers exactly up to here.
    (v >= 0.0 && v.fract() == 0.0 && v <= 9_007_199_254_740_992.0).then_some(v as u64)
}

/// Quoted value with `\"` and `\\` unescaped.
fn text(t: Toks<'_, '_>, i: usize) -> Option<String> {
    let raw = String::from_utf8_lossy(scalar(t, i)?);
    if !raw.contains('\\') {
        return Some(raw.into_owned());
    }
    let mut out = String::with_capacity(raw.len());
    let mut chars = raw.chars();
    while let Some(c) = chars.next() {
        match (c, chars.clone().next()) {
            ('\\', Some(n @ ('"' | '\\'))) => {
                out.push(n);
                chars.next();
            }
            _ => out.push(c),
        }
    }
    Some(out)
}

/// Finite and non-negative (fields the output model declares `ge=0`); anything else counts as missing.
fn num_nn_at(t: Toks<'_, '_>, i: usize, keys: &[&[u8]]) -> Option<f64> {
    num_at(t, i, keys).filter(|v| *v >= 0.0)
}

fn uint_at(t: Toks<'_, '_>, i: usize, keys: &[&[u8]]) -> Option<u64> {
    uint(t, path(t, i, keys)?)
}

fn num_at(t: Toks<'_, '_>, i: usize, keys: &[&[u8]]) -> Option<f64> {
    num(t, path(t, i, keys)?)
}

// ---- spans -------------------------------------------------------------------------------------

/// Parse one span with jomini and run `f` on its tokens; the tape is dropped on return.
fn with_tape<T>(data: &[u8], what: &str, f: impl FnOnce(Toks<'_, '_>) -> T) -> Result<T, Failure> {
    if data.len() > MAX_SPAN {
        return Err(Failure::new(
            Kind::Format,
            format!("{what}: span too large ({} bytes > 32 MiB)", data.len()),
        ));
    }
    let tape = TextTape::from_slice(data).map_err(|e| parse_err(format!("{what}: {e}")))?;
    Ok(f(tape.tokens()))
}

fn span<'a>(data: &'a [u8], e: &Entry<'_>) -> &'a [u8] {
    data.get(e.start..e.end).unwrap_or_default()
}

/// Call `f` for every `<id>={...}` entry inside the block value of `e`; non-block values yield nothing.
fn each_inner<'a>(
    data: &'a [u8],
    e: &Entry<'_>,
    mut f: impl FnMut(&Entry<'a>, &'a [u8]) -> Result<(), Failure>,
) -> Result<(), Failure> {
    let value = data.get(e.value_start..e.end).unwrap_or_default();
    let Some(body) = inner(value) else {
        return Ok(());
    };
    for sub in split(body)? {
        f(&sub, body)?;
    }
    Ok(())
}

fn id_of(key: &[u8]) -> Option<u64> {
    std::str::from_utf8(key).ok()?.parse().ok()
}

// ---- readers ------------------------------------------------------------------------------------

fn read_country(t: Toks<'_, '_>, id: u64) -> CountryRec {
    // Entry span is `<id>={ ... }`: the block is token 1.
    let b = 1;
    let name = path(t, b, &[b"name"]).and_then(|n| {
        if container_end(t, n).is_some() {
            text(t, find(t, n, b"key")?)
        } else {
            text(t, n)
        }
    });
    let mut contacts = BTreeSet::new();
    if let Some(rm) = find(t, b, b"relations_manager") {
        for (k, rel) in pairs(t, rm) {
            if k == b"relation"
                && find(t, rel, b"contact").and_then(|c| scalar(t, c)) == Some(b"yes")
            {
                if let Some(p) = find(t, rel, b"country").and_then(|c| uint(t, c)) {
                    contacts.insert(p);
                }
            }
        }
    }
    let tech_count = find(t, b, b"tech_status")
        .map(|ts| pairs(t, ts).filter(|(k, _)| *k == b"technology").count() as u64);
    let res = |k: &[u8]| {
        num_at(
            t,
            b,
            &[b"modules", b"standard_economy_module", b"resources", k],
        )
    };
    let var = |k: &[u8]| uint_at(t, b, &[b"variables", k]);
    let sid = var(b"sae_sid").filter(|s| {
        let ok = (1..=MAX_SID).contains(s);
        if !ok {
            log(
                "warn",
                &format!("country {id}: sae_sid {s} outside 1..={MAX_SID}, ignored"),
            );
        }
        ok
    });
    CountryRec {
        id,
        name: name
            .unwrap_or_default()
            .chars()
            .take(MAX_NAME_CHARS)
            .collect(),
        sid,
        managed: path(t, b, &[b"flags", b"sae_managed"]).is_some(),
        seq_applied: var(b"sae_seq_applied"),
        seq_pending: var(b"sae_seq_pending"),
        applied_econ: var(b"sae_applied_econ"),
        applied_mil: var(b"sae_applied_mil"),
        military_power: num_nn_at(t, b, &[b"military_power"]),
        naval_used: num_nn_at(t, b, &[b"used_naval_capacity"]),
        naval_cap: num_nn_at(t, b, &[b"naval_capacity"]),
        energy: res(b"energy"),
        minerals: res(b"minerals"),
        alloys: res(b"alloys"),
        alloys_income: alloys_income(t, b),
        tech_count,
        contacts,
    }
}

/// Gross alloys income summed over sources; `last_month`, falling back to `current_month`.
fn alloys_income(t: Toks<'_, '_>, b: usize) -> Option<f64> {
    let income = path(t, b, &[b"budget", b"last_month", b"income"])
        .or_else(|| path(t, b, &[b"budget", b"current_month", b"income"]))?;
    // Each term is finite, but the sum can still overflow to infinity: that counts as missing.
    Some(
        values(t, income)
            .into_iter()
            .filter_map(|src| num_at(t, src, &[b"alloys"]))
            .sum::<f64>(),
    )
    .filter(|v| v.is_finite())
}

/// Country ids found in a `player` value (first wins); also counts how many there were.
fn read_player(t: Toks<'_, '_>) -> Vec<u64> {
    let Some((_, v)) = root(t).next() else {
        return Vec::new();
    };
    if let Some(c) = find(t, v, b"country").and_then(|c| uint(t, c)) {
        return vec![c];
    }
    values(t, v)
        .into_iter()
        .filter_map(|item| find(t, item, b"country").and_then(|c| uint(t, c)))
        .collect()
}

fn read_system(t: Toks<'_, '_>) -> SystemRec {
    let b = 1;
    let lanes = find(t, b, b"hyperlane")
        .map(|h| {
            values(t, h)
                .into_iter()
                .filter_map(|l| find(t, l, b"to").and_then(|x| uint(t, x)))
                .collect()
        })
        .unwrap_or_default();
    let starbases = find(t, b, b"starbases")
        .map(|s| {
            values(t, s)
                .into_iter()
                .filter_map(|x| uint(t, x))
                .collect()
        })
        .unwrap_or_default();
    SystemRec { lanes, starbases }
}

fn read_starbase(t: Toks<'_, '_>) -> StarbaseRec {
    let b = 1;
    let modules = find(t, b, b"modules").map_or(0, |m| {
        values(t, m)
            .into_iter()
            .filter(|v| scalar(t, *v).is_some_and(|n| DEFENSE_MODULES.contains(&n)))
            .count()
    });
    let platforms = find(t, b, b"orbitals").map_or(0, |o| values(t, o).len());
    StarbaseRec {
        owner: find(t, b, b"owner").and_then(|o| uint(t, o)),
        defenses: (modules + platforms) as u64,
    }
}

fn read_war(t: Toks<'_, '_>) -> WarRec {
    let b = 1;
    let side = |k: &[u8]| -> Vec<u64> {
        find(t, b, k)
            .map(|s| {
                values(t, s)
                    .into_iter()
                    .filter_map(|c| find(t, c, b"country").and_then(|x| uint(t, x)))
                    .collect()
            })
            .unwrap_or_default()
    };
    WarRec {
        attackers: side(b"attackers"),
        defenders: side(b"defenders"),
    }
}

fn scalar_string(data: &[u8], what: &str, e: &Entry<'_>) -> Result<Option<String>, Failure> {
    with_tape(span(data, e), what, |t| text(t, 1))
}

/// `version`, `date`, `ironman` from the zip `meta` entry (small; taped whole).
fn read_meta(meta: &[u8]) -> Result<(Option<String>, Option<String>, bool), Failure> {
    with_tape(meta, "meta entry", |t| {
        let get = |k: &[u8]| root(t).find(|(key, _)| *key == k).map(|(_, v)| v);
        (
            get(b"version").and_then(|v| text(t, v)),
            get(b"date").and_then(|v| text(t, v)),
            get(b"ironman").and_then(|v| scalar(t, v)) == Some(b"yes"),
        )
    })
}

/// Normalise like the C4 `GameDate` (pydantic `normalize_game_date`): `Y{1,4}.M{1,2}.D{1,2}` with month 1-12
/// and day 1-30 (Stellaris months have 30 days), re-rendered as zero-padded `YYYY.MM.DD`.
fn normalize_date(d: &str) -> Option<String> {
    let mut it = d.split('.');
    let (y, m, day) = (it.next()?, it.next()?, it.next()?);
    if it.next().is_some() {
        return None;
    }
    let num = |s: &str, max_len: usize| -> Option<u32> {
        if s.is_empty() || s.len() > max_len || !s.bytes().all(|c| c.is_ascii_digit()) {
            return None;
        }
        s.parse().ok()
    };
    let (y, m, day) = (num(y, 4)?, num(m, 2)?, num(day, 2)?);
    ((1..=12).contains(&m) && (1..=30).contains(&day)).then(|| format!("{y:04}.{m:02}.{day:02}"))
}

/// Extract the model from the gamestate (and the optional zip `meta` entry, which overrides version/date).
pub fn extract(gamestate: &[u8], meta: Option<&[u8]>, need: Need) -> Result<SaveModel, Failure> {
    let entries = split(gamestate)?;
    let mut version = None;
    let mut date = None;
    for e in entries
        .iter()
        .filter(|e| e.key == b"version" || e.key == b"date")
    {
        let slot = if e.key == b"version" {
            &mut version
        } else {
            &mut date
        };
        if slot.is_none() {
            *slot = scalar_string(gamestate, "gamestate version/date", e)?;
        }
    }
    let mut ironman = None;
    if let Some(m) = meta {
        let (v, d, iron) = read_meta(m)?;
        version = v.or(version);
        date = d.or(date);
        ironman = Some(iron);
    }
    let date =
        date.ok_or_else(|| parse_err("save has no date (neither in meta nor in gamestate)"))?;
    let Some(date) = normalize_date(&date) else {
        return Err(parse_err(format!(
            "save date {date:?} is not YYYY.MM.DD (month 1-12, day 1-30)"
        )));
    };

    let mut model = SaveModel {
        meta: SaveMeta {
            game_version: version.unwrap_or_else(|| "unknown".to_owned()),
            date,
            ironman,
        },
        player: None,
        countries: Vec::new(),
        systems: BTreeMap::new(),
        starbases: BTreeMap::new(),
        wars: Vec::new(),
    };
    if need.countries {
        read_countries(gamestate, &entries, &mut model)?;
    }
    if need.starbases {
        for e in entries.iter().filter(|e| e.key == b"galactic_object") {
            each_inner(gamestate, e, |sub, body| {
                if let Some(id) = id_of(sub.key) {
                    let rec = with_tape(
                        body.get(sub.start..sub.end).unwrap_or_default(),
                        "galactic_object entry",
                        read_system,
                    )?;
                    model.systems.insert(id, rec);
                }
                Ok(())
            })?;
        }
        for e in entries.iter().filter(|e| e.key == b"starbase_mgr") {
            each_inner(gamestate, e, |mgr, mgr_body| {
                if mgr.key != b"starbases" {
                    return Ok(());
                }
                let value = mgr_body.get(mgr.value_start..mgr.end).unwrap_or_default();
                let Some(body) = inner(value) else {
                    return Ok(());
                };
                for sb in split(body)? {
                    if let Some(id) = id_of(sb.key) {
                        let rec = with_tape(
                            body.get(sb.start..sb.end).unwrap_or_default(),
                            "starbase entry",
                            read_starbase,
                        )?;
                        model.starbases.insert(id, rec);
                    }
                }
                Ok(())
            })?;
        }
    }
    if need.wars {
        for e in entries.iter().filter(|e| e.key == b"war") {
            each_inner(gamestate, e, |sub, body| {
                if id_of(sub.key).is_some() {
                    let rec = with_tape(
                        body.get(sub.start..sub.end).unwrap_or_default(),
                        "war entry",
                        read_war,
                    )?;
                    model.wars.push(rec);
                }
                Ok(())
            })?;
        }
    }
    Ok(model)
}

fn read_countries(
    data: &[u8],
    entries: &[Entry<'_>],
    model: &mut SaveModel,
) -> Result<(), Failure> {
    let mut players: Vec<u64> = Vec::new();
    for e in entries.iter().filter(|e| e.key == b"player") {
        players.extend(with_tape(span(data, e), "player", read_player)?);
    }
    if players.len() > 1 {
        log(
            "warn",
            &format!(
                "{} player entries; first wins (country {:?})",
                players.len(),
                players.first()
            ),
        );
    }
    model.player = players.first().copied();
    for e in entries.iter().filter(|e| e.key == b"country") {
        each_inner(data, e, |sub, body| {
            let Some(id) = id_of(sub.key) else {
                return Ok(());
            };
            // `<id>=none` (dead country) is a scalar value, not a block: nothing to read.
            let value = body.get(sub.value_start..sub.end).unwrap_or_default();
            if inner(value).is_none() {
                return Ok(());
            }
            let rec = with_tape(
                body.get(sub.start..sub.end).unwrap_or_default(),
                "country entry",
                |t| read_country(t, id),
            )?;
            if rec.sid.is_some() || Some(id) == model.player {
                model.countries.push(rec);
            }
            Ok(())
        })?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    const ALL: Need = Need {
        countries: true,
        starbases: true,
        wars: true,
    };

    #[test]
    fn reads_every_shape() {
        let gs = br#"version="Cepheus \"x\" v4"
date="2231.04.01"
player={ { name="P" country=0 } { name="Q" country=1 } }
country={
  0={ name={ key="Earth" } military_power=10.500 flags={ sae_managed=1 } variables={ sae_sid=3.000 sae_seq_applied=2.000 }
      tech_status={ technology="a" level=1 technology="b" level=2 }
      relations_manager={ relation={ owner=0 country=7 contact=yes } relation={ owner=0 country=8 contact=no } }
      budget={ current_month={ income={ x={ alloys=2.000 } y={ alloys=3.500 } } } } }
  1={ name="NoSid" }
  2={ variables={ sae_sid=1.500 } }
  3=none
}
galactic_object={ 5={ hyperlane={ { to=6 } { to=7 } } starbases={ 9 } } 6=none }
starbase_mgr={ starbases={ 9={ owner=0 modules={ 0=gun_battery 1=other } orbitals={ 100 101 } } } }
war={ 0={ attackers={ { country=0 } } defenders={ { country=7 } { country=8 } } } }
"#;
        let m = extract(gs, None, ALL).unwrap();
        assert_eq!(m.meta.game_version, "Cepheus \"x\" v4");
        assert_eq!(m.meta.ironman, None);
        assert_eq!(m.player, Some(0));
        assert_eq!(
            m.countries.len(),
            1,
            "id 1 has no sid and is not the player; id 2 sid is non-integral"
        );
        let c = &m.countries[0];
        assert_eq!(
            (c.sid, c.seq_applied, c.managed, c.tech_count),
            (Some(3), Some(2), true, Some(2))
        );
        assert_eq!(c.name, "Earth");
        assert_eq!(c.alloys_income, Some(5.5));
        assert_eq!(c.contacts.iter().copied().collect::<Vec<_>>(), [7]);
        assert_eq!(m.systems[&5].lanes, [6, 7]);
        assert_eq!(m.systems[&5].starbases, [9]);
        assert_eq!(
            m.starbases[&9],
            StarbaseRec {
                owner: Some(0),
                defenses: 3
            }
        );
        assert_eq!(
            m.wars,
            [WarRec {
                attackers: vec![0],
                defenders: vec![7, 8]
            }]
        );
    }

    #[test]
    fn meta_overrides_and_missing_date_is_parse_error() {
        let m = extract(
            b"version=\"a\"\ndate=\"2200.01.01\"",
            Some(b"version=\"b\"\ndate=\"2201.02.03\"\nironman=yes"),
            Need::default(),
        )
        .unwrap();
        assert_eq!(
            (
                m.meta.game_version.as_str(),
                m.meta.date.as_str(),
                m.meta.ironman
            ),
            ("b", "2201.02.03", Some(true))
        );
        assert_eq!(
            extract(b"version=\"a\"", None, Need::default())
                .unwrap_err()
                .kind,
            Kind::Parse
        );
        assert_eq!(
            extract(b"date=\"x\"", Some(b"ironman=no"), Need::default())
                .unwrap_err()
                .kind,
            Kind::Parse
        );
        for bad in [
            "2231.13.01",
            "2231.00.01",
            "2231.01.31",
            "2231.01.00",
            "x",
            "2231-01-01",
            "２２３１.01.01",
            "",
        ] {
            let gs = format!("date=\"{bad}\"");
            assert_eq!(
                extract(gs.as_bytes(), None, Need::default())
                    .unwrap_err()
                    .kind,
                Kind::Parse,
                "{bad}"
            );
        }
        let m = extract(b"date=\"2231.4.1\"", None, Need::default()).unwrap();
        assert_eq!(m.meta.date, "2231.04.01");
        assert_eq!(normalize_date("0001.12.30").as_deref(), Some("0001.12.30"));
        assert_eq!(normalize_date("2231.4.1").as_deref(), Some("2231.04.01"));
        for bad in [
            "2231.01.011",
            "2231.13.01",
            "2231.01.31",
            "2231.00.01",
            "2231.01.00",
            "22311.01.01",
            "2231.01",
            "2231.01.01.01",
            "+231.01.01",
            "",
        ] {
            assert_eq!(normalize_date(bad), None, "{bad}");
        }
    }

    #[test]
    fn negative_and_non_finite_values_are_missing() {
        let gs = br#"date="2231.04.01"
player={ country=0 }
country={ 0={ military_power=-5.000 used_naval_capacity=1e999 naval_capacity=4.000
  modules={ standard_economy_module={ resources={ energy=1e999 minerals=-3.000 } } }
  budget={ current_month={ income={ x={ alloys=1e308 } y={ alloys=1e308 } } } } } }
"#;
        let c = &extract(
            gs,
            None,
            Need {
                countries: true,
                ..Need::default()
            },
        )
        .unwrap()
        .countries[0];
        assert_eq!(
            (
                c.military_power,
                c.naval_used,
                c.naval_cap,
                c.energy,
                c.minerals
            ),
            (None, None, Some(4.0), None, Some(-3.0))
        );
        assert_eq!(c.alloys_income, None, "overflowing sum");
    }

    #[test]
    fn jomini_error_is_parse() {
        let e = extract(b"date=\"2231.04.01\"\nplayer={ a=1 =", None, ALL).unwrap_err();
        assert_eq!(e.kind, Kind::Parse);
    }

    #[test]
    fn sid_range_and_name_cap() {
        let long = "é".repeat(300);
        let gs = format!(
            "date=\"2231.04.01\"\nplayer={{ country=0 }}\ncountry={{ 0={{ name=\"{long}\" variables={{ sae_sid=0 }} }} \
             1={{ name=\"b\" variables={{ sae_sid=1000001 }} }} 2={{ name=\"c\" variables={{ sae_sid=1000000 }} }} }}"
        );
        let m = extract(gs.as_bytes(), None, ALL).unwrap();
        let sids: Vec<_> = m.countries.iter().map(|c| c.sid).collect();
        assert_eq!(
            sids,
            [None, Some(1_000_000)],
            "out-of-range sids are dropped (non-player country 1 is not kept)"
        );
        assert_eq!(m.countries[0].name.chars().count(), 256);
    }

    #[test]
    fn oversize_span_is_format() {
        let big = vec![b' '; MAX_SPAN + 1];
        let e = with_tape(&big, "x", |_| ()).unwrap_err();
        assert_eq!(e.kind, Kind::Format);
        assert!(e.detail.contains("span too large"), "{}", e.detail);
    }
}
