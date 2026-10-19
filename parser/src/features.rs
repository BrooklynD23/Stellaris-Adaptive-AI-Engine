//! Feature computation (C4 section 3): pure functions from [`SaveModel`] to the output sections.
//!
//! Section semantics (plan section 3): `self_state` and `god` need all six sections; `legal` needs
//! `countries` and nulls every feature whose section did not run. The legal view is the v0
//! "diplomacy-screen legal" approximation, UNVERIFIED A-19 (M0-11).

use std::collections::{BTreeMap, BTreeSet};

use crate::cli::SECTIONS;
use crate::extract::{CountryRec, Need, SaveModel};
use crate::log::log;
use crate::output::{CountryInfo, GodView, LegalView, SelfState};

/// The sections that ran.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Ran {
    pub countries: bool,
    pub starbases: bool,
    pub fleets: bool,
    pub wars: bool,
    pub tech: bool,
}

impl Ran {
    /// From section names (`meta` always runs and carries no flag).
    pub fn from_names(names: &[String]) -> Self {
        let has = |n: &str| names.iter().any(|s| s == n);
        Ran {
            countries: has("countries"),
            starbases: has("starbases"),
            fleets: has("fleets"),
            wars: has("wars"),
            tech: has("tech"),
        }
    }

    /// All six sections ran.
    pub fn all(self) -> bool {
        self.countries && self.starbases && self.fleets && self.wars && self.tech
    }

    /// What the extractor must read.
    pub fn need(self) -> Need {
        Need {
            countries: self.countries || self.fleets || self.tech,
            starbases: self.starbases,
            wars: self.wars,
        }
    }

    /// Requested sections plus `meta`, in canonical C4 order.
    pub fn names(self) -> Vec<String> {
        let on = [
            true,
            self.countries,
            self.starbases,
            self.fleets,
            self.wars,
            self.tech,
        ];
        SECTIONS
            .iter()
            .zip(on)
            .filter(|(_, o)| *o)
            .map(|(n, _)| (*n).to_owned())
            .collect()
    }
}

/// The computed output sections.
#[derive(Debug, Clone, PartialEq)]
pub struct Features {
    pub countries: Vec<CountryInfo>,
    pub self_state: BTreeMap<String, SelfState>,
    pub legal: Vec<LegalView>,
    pub god: Option<GodView>,
}

/// Relative category from a player/observer ratio (C4 section 3).
pub fn category(player: f64, observer: f64) -> i8 {
    if observer <= 0.0 {
        return if player > 0.0 { 2 } else { 0 };
    }
    let r = player / observer;
    if r < 0.5 {
        -2
    } else if r < 0.8 {
        -1
    } else if r <= 1.25 {
        0
    } else if r <= 2.0 {
        1
    } else {
        2
    }
}

/// [`category`] only when both sides are known: a missing key is not a zero, so it yields `None`.
pub fn category_opt(player: Option<f64>, observer: Option<f64>) -> Option<i8> {
    Some(category(player?, observer?))
}

/// Starbase and system ownership derived once per model.
struct World {
    /// system id -> owner (owner of its starbase).
    system_owner: BTreeMap<u64, u64>,
    /// system id -> neighbours, both directions.
    adj: BTreeMap<u64, BTreeSet<u64>>,
}

fn world(m: &SaveModel) -> World {
    let mut system_owner = BTreeMap::new();
    let mut adj: BTreeMap<u64, BTreeSet<u64>> = BTreeMap::new();
    for (sys, rec) in &m.systems {
        if let Some(owner) = rec
            .starbases
            .iter()
            .find_map(|sb| m.starbases.get(sb).and_then(|s| s.owner))
        {
            system_owner.insert(*sys, owner);
        }
        for to in &rec.lanes {
            adj.entry(*sys).or_default().insert(*to);
            adj.entry(*to).or_default().insert(*sys);
        }
    }
    World { system_owner, adj }
}

impl World {
    fn systems_of(&self, owner: u64) -> impl Iterator<Item = u64> + '_ {
        self.system_owner
            .iter()
            .filter(move |(_, o)| **o == owner)
            .map(|(s, _)| *s)
    }
}

fn clamp_code(v: Option<u64>, max: u64) -> u8 {
    u8::try_from(v.unwrap_or(0).min(max)).unwrap_or(0)
}

fn war_count(m: &SaveModel, id: u64) -> u64 {
    m.wars
        .iter()
        .filter(|w| w.attackers.contains(&id) || w.defenders.contains(&id))
        .count() as u64
}

fn at_war_between(m: &SaveModel, a: u64, b: u64) -> bool {
    m.wars.iter().any(|w| {
        (w.attackers.contains(&a) && w.defenders.contains(&b))
            || (w.attackers.contains(&b) && w.defenders.contains(&a))
    })
}

fn in_contact(a: &CountryRec, b: &CountryRec) -> bool {
    a.contacts.contains(&b.id) || b.contacts.contains(&a.id)
}

/// Sids claimed by two or more listed countries; each is logged once at warn level.
fn duplicate_sids(recs: &[&CountryRec]) -> BTreeSet<u64> {
    let mut seen = BTreeSet::new();
    let mut dup = BTreeSet::new();
    for sid in recs.iter().filter_map(|c| c.sid) {
        if !seen.insert(sid) && dup.insert(sid) {
            log("warn", &format!("sae_sid {sid} claimed by more than one country; all of them are excluded from self_state and legal"));
        }
    }
    dup
}

/// Proposal 007 (A-17, M0-5): seq and `LegalView` counts must be <= 1,000,000 (inclusive).
pub const CLAUSEWITZ_INT_MAX: u64 = 1_000_000;

/// Reject (never clamp) out-of-range ints before emitting JSON: the caller maps this to exit 5.
pub fn check_bounds(f: &Features) -> Result<(), crate::error::Failure> {
    let over = |what: &str, v: u64| {
        crate::error::Failure::new(
            crate::error::Kind::Parse,
            format!("{what}={v} exceeds {CLAUSEWITZ_INT_MAX}"),
        )
    };
    for (sid, s) in &f.self_state {
        for (what, v) in [
            ("applied_seq", s.applied_seq),
            ("pending_seq", s.pending_seq),
        ] {
            if v > CLAUSEWITZ_INT_MAX {
                return Err(over(&format!("self_state[{sid}].{what}"), v));
            }
        }
    }
    for l in &f.legal {
        let counts = [
            ("systems_owned", l.systems_owned),
            ("frontier_systems", l.frontier_systems),
            ("fortified_border_starbases", l.fortified_border_starbases),
            ("player_war_count", l.player_war_count),
        ];
        for (what, v) in counts {
            if let Some(v) = v.filter(|v| *v > CLAUSEWITZ_INT_MAX) {
                return Err(over(&format!("legal[{}].{what}", l.observer_sid), v));
            }
        }
    }
    Ok(())
}

/// All C4 section 3 features for managed countries; see the module docs for section gating.
pub fn compute(m: &SaveModel, ran: Ran) -> Features {
    if !ran.countries {
        return Features {
            countries: Vec::new(),
            self_state: BTreeMap::new(),
            legal: Vec::new(),
            god: None,
        };
    }
    let w = world(m);
    let mut recs: Vec<&CountryRec> = m
        .countries
        .iter()
        .filter(|c| c.sid.is_some() || Some(c.id) == m.player)
        .collect();
    recs.sort_by_key(|c| c.id);
    // Every listed country is reported as in the save; one whose sid another listed country also claims
    // has no trustworthy identity and feeds neither self_state nor legal.
    let dup = duplicate_sids(&recs);
    let is_dup = |c: &CountryRec| c.sid.is_some_and(|s| dup.contains(&s));
    let countries = recs
        .iter()
        .map(|c| CountryInfo {
            country_id: c.id,
            name: c.name.clone(),
            sid: c.sid,
            is_player: Some(c.id) == m.player,
            managed: c.managed,
        })
        .collect();
    let player = m
        .player
        .and_then(|p| recs.iter().copied().find(|c| c.id == p));
    let mut managed: Vec<(u64, &CountryRec)> = recs
        .iter()
        .filter(|c| c.managed && !is_dup(c))
        .filter_map(|c| Some((c.sid?, *c)))
        .collect();
    managed.sort_by_key(|(sid, _)| *sid);

    let self_state = if ran.all() {
        managed
            .iter()
            .map(|(sid, c)| (sid.to_string(), self_state_of(m, &w, c)))
            .collect()
    } else {
        BTreeMap::new()
    };
    let legal = match player {
        Some(p) => managed
            .iter()
            .filter(|(_, o)| o.id != p.id)
            .map(|(sid, o)| legal_of(m, &w, ran, p, *sid, o))
            .collect(),
        None => Vec::new(),
    };
    let god = player.filter(|_| ran.all()).map(|p| god_of(m, &w, p));
    Features {
        countries,
        self_state,
        legal,
        god,
    }
}

fn self_state_of(m: &SaveModel, w: &World, c: &CountryRec) -> SelfState {
    SelfState {
        fleet_power: c.military_power.unwrap_or(0.0).max(0.0),
        naval_cap_used: c.naval_used.unwrap_or(0.0).max(0.0),
        naval_cap: c.naval_cap.unwrap_or(0.0).max(0.0),
        energy: c.energy.unwrap_or(0.0),
        minerals: c.minerals.unwrap_or(0.0),
        alloys: c.alloys.unwrap_or(0.0),
        alloys_income: c.alloys_income.unwrap_or(0.0),
        systems_owned: w.systems_of(c.id).count() as u64,
        wars: war_count(m, c.id),
        applied_seq: c.seq_applied.unwrap_or(0),
        pending_seq: c.seq_pending.unwrap_or(0),
        applied_econ: clamp_code(c.applied_econ, 4),
        applied_mil: clamp_code(c.applied_mil, 3),
    }
}

fn fortified(m: &SaveModel, player: u64, systems: &BTreeSet<u64>) -> u64 {
    let in_scope = |sb: &u64| {
        m.starbases
            .get(sb)
            .is_some_and(|s| s.owner == Some(player) && s.defenses > 0)
    };
    systems
        .iter()
        .filter_map(|s| m.systems.get(s))
        .flat_map(|rec| rec.starbases.iter())
        .filter(|sb| in_scope(sb))
        .count() as u64
}

fn god_of(m: &SaveModel, w: &World, p: &CountryRec) -> GodView {
    let mine: BTreeSet<u64> = w.systems_of(p.id).collect();
    GodView {
        fleet_power: p.military_power.unwrap_or(0.0).max(0.0),
        systems_owned: mine.len() as u64,
        tech_researched: p.tech_count.unwrap_or(0),
        alloys_income: p.alloys_income.unwrap_or(0.0),
        fortified_starbases: m
            .starbases
            .values()
            .filter(|s| s.owner == Some(p.id) && s.defenses > 0)
            .count() as u64,
    }
}

fn legal_of(
    m: &SaveModel,
    w: &World,
    ran: Ran,
    p: &CountryRec,
    sid: u64,
    o: &CountryRec,
) -> LegalView {
    let mut v = LegalView {
        observer_sid: sid,
        in_contact: in_contact(p, o),
        systems_owned: None,
        frontier_systems: None,
        fortified_border_starbases: None,
        fleet_power_cat: None,
        tech_cat: None,
        economy_cat: None,
        at_war_with_observer: None,
        player_war_count: None,
    };
    if !v.in_contact {
        return v;
    }
    if ran.starbases {
        let mine: BTreeSet<u64> = w.systems_of(p.id).collect();
        let frontier: BTreeSet<u64> = mine
            .iter()
            .copied()
            .filter(|s| {
                w.adj
                    .get(s)
                    .is_some_and(|ns| ns.iter().any(|n| w.system_owner.get(n) == Some(&o.id)))
            })
            .collect();
        v.systems_owned = Some(mine.len() as u64);
        v.frontier_systems = Some(frontier.len() as u64);
        v.fortified_border_starbases = Some(fortified(m, p.id, &frontier));
    }
    if ran.fleets {
        v.fleet_power_cat = category_opt(p.military_power, o.military_power);
    }
    if ran.tech {
        v.tech_cat = category_opt(
            p.tech_count.map(|n| n as f64),
            o.tech_count.map(|n| n as f64),
        );
    }
    v.economy_cat = category_opt(p.alloys_income, o.alloys_income);
    if ran.wars {
        v.at_war_with_observer = Some(at_war_between(m, p.id, o.id));
        v.player_war_count = Some(war_count(m, p.id));
    }
    v
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::extract::{StarbaseRec, SystemRec, WarRec};
    use crate::output::SaveMeta;

    #[test]
    fn legal_counts_above_cap_are_rejected_not_clamped() {
        let lv = |v: u64| LegalView {
            observer_sid: 3,
            in_contact: true,
            systems_owned: Some(1),
            frontier_systems: Some(v),
            fortified_border_starbases: Some(0),
            fleet_power_cat: None,
            tech_cat: None,
            economy_cat: None,
            at_war_with_observer: None,
            player_war_count: Some(v),
        };
        let f = |v| Features {
            countries: Vec::new(),
            self_state: BTreeMap::new(),
            legal: vec![lv(v)],
            god: None,
        };
        assert!(check_bounds(&f(CLAUSEWITZ_INT_MAX)).is_ok());
        let e = check_bounds(&f(CLAUSEWITZ_INT_MAX + 1)).unwrap_err();
        assert_eq!(e.kind, crate::error::Kind::Parse);
    }

    const ALL: [&str; 5] = ["countries", "starbases", "fleets", "wars", "tech"];

    fn ran(names: &[&str]) -> Ran {
        Ran::from_names(&names.iter().map(|s| (*s).to_owned()).collect::<Vec<_>>())
    }

    fn country(id: u64, sid: Option<u64>, managed: bool) -> CountryRec {
        CountryRec {
            id,
            name: format!("c{id}"),
            sid,
            managed,
            ..CountryRec::default()
        }
    }

    /// Player 0 and managed observer 7 (sid 3) in contact; player owns systems 1,2; observer owns 10.
    fn model() -> SaveModel {
        let mut p = country(0, Some(1), false);
        p.military_power = Some(100.0);
        p.tech_count = Some(10);
        p.alloys_income = Some(10.0);
        p.contacts.insert(7);
        let mut o = country(7, Some(3), true);
        o.military_power = Some(100.0);
        o.tech_count = Some(10);
        o.alloys_income = Some(10.0);
        let sys = |lanes: &[u64], sb: u64| SystemRec {
            lanes: lanes.to_vec(),
            starbases: vec![sb],
        };
        SaveModel {
            meta: SaveMeta {
                game_version: "v".into(),
                date: "2231.04.01".into(),
                ironman: None,
            },
            player: Some(0),
            countries: vec![o, p],
            systems: BTreeMap::from([(1, sys(&[2], 1)), (2, sys(&[], 2)), (10, sys(&[2], 3))]),
            starbases: BTreeMap::from([
                (
                    1,
                    StarbaseRec {
                        owner: Some(0),
                        defenses: 0,
                    },
                ),
                (
                    2,
                    StarbaseRec {
                        owner: Some(0),
                        defenses: 8,
                    },
                ),
                (
                    3,
                    StarbaseRec {
                        owner: Some(7),
                        defenses: 1,
                    },
                ),
            ]),
            wars: vec![
                WarRec {
                    attackers: vec![0],
                    defenders: vec![7],
                },
                WarRec {
                    attackers: vec![0],
                    defenders: vec![5],
                },
            ],
        }
    }

    #[test]
    fn category_boundaries() {
        for (p, o, want) in [
            (0.49, 1.0, -2),
            (0.5, 1.0, -1),
            (0.79, 1.0, -1),
            (4.0, 5.0, 0),
            (1.0, 1.0, 0),
            (1.25, 1.0, 0),
            (1.26, 1.0, 1),
            (2.0, 1.0, 1),
            (2.01, 1.0, 2),
            (0.0, 1.0, -2),
            (5.0, 0.0, 2),
            (0.0, 0.0, 0),
            (-3.0, 0.0, 0),
            (5.0, -2.0, 2),
        ] {
            assert_eq!(category(p, o), want, "{p}/{o}");
        }
    }

    #[test]
    fn full_features() {
        let f = compute(&model(), ran(&ALL));
        assert_eq!(
            f.countries.iter().map(|c| c.country_id).collect::<Vec<_>>(),
            [0, 7]
        );
        let l = &f.legal[0];
        assert_eq!(
            (
                l.observer_sid,
                l.in_contact,
                l.systems_owned,
                l.frontier_systems
            ),
            (3, true, Some(2), Some(1))
        );
        assert_eq!(
            l.fortified_border_starbases,
            Some(1),
            "only system 2 is on the frontier"
        );
        assert_eq!(
            (l.fleet_power_cat, l.tech_cat, l.economy_cat),
            (Some(0), Some(0), Some(0))
        );
        assert_eq!(
            (l.at_war_with_observer, l.player_war_count),
            (Some(true), Some(2))
        );
        let g = f.god.unwrap();
        assert_eq!(
            (g.systems_owned, g.tech_researched, g.fortified_starbases),
            (2, 10, 1)
        );
        let s = &f.self_state["3"];
        assert_eq!((s.systems_owned, s.wars), (1, 1));
    }

    #[test]
    fn frontier_works_in_both_lane_directions() {
        let mut m = model();
        // Lane declared only on the player side, then only on the observer side.
        m.systems.insert(
            2,
            SystemRec {
                lanes: vec![10],
                starbases: vec![2],
            },
        );
        m.systems.insert(
            10,
            SystemRec {
                lanes: vec![],
                starbases: vec![3],
            },
        );
        assert_eq!(compute(&m, ran(&ALL)).legal[0].frontier_systems, Some(1));
        m.systems.insert(
            2,
            SystemRec {
                lanes: vec![],
                starbases: vec![2],
            },
        );
        m.systems.insert(
            1,
            SystemRec {
                lanes: vec![],
                starbases: vec![1],
            },
        );
        m.systems.insert(
            10,
            SystemRec {
                lanes: vec![2],
                starbases: vec![3],
            },
        );
        assert_eq!(compute(&m, ran(&ALL)).legal[0].frontier_systems, Some(1));
    }

    #[test]
    fn contact_is_symmetric_and_absent_means_nulls() {
        let mut m = model();
        m.countries.iter_mut().for_each(|c| c.contacts.clear());
        let l = &compute(&m, ran(&ALL)).legal[0];
        assert_eq!(
            (
                l.in_contact,
                l.systems_owned,
                l.fleet_power_cat,
                l.economy_cat,
                l.player_war_count
            ),
            (false, None, None, None, None)
        );
        assert_eq!(
            serde_json::to_string(l).unwrap(),
            r#"{"observer_sid":3,"in_contact":false}"#
        );
        m.countries
            .iter_mut()
            .find(|c| c.id == 7)
            .unwrap()
            .contacts
            .insert(0);
        assert!(
            compute(&m, ran(&ALL)).legal[0].in_contact,
            "observer-side contact counts"
        );
    }

    #[test]
    fn observer_mode() {
        let mut m = model();
        m.player = None;
        let f = compute(&m, ran(&ALL));
        assert!(f.legal.is_empty() && f.god.is_none());
        assert_eq!(f.countries.len(), 2, "sid countries still reported");
        assert_eq!(f.self_state.len(), 1);
    }

    #[test]
    fn section_subsets() {
        let f = compute(&model(), ran(&["countries"]));
        assert!(f.self_state.is_empty() && f.god.is_none());
        let l = &f.legal[0];
        assert!(l.in_contact);
        assert_eq!(
            (
                l.systems_owned,
                l.fleet_power_cat,
                l.tech_cat,
                l.at_war_with_observer
            ),
            (None, None, None, None)
        );
        assert_eq!(l.economy_cat, Some(0));
        let f = compute(&model(), ran(&["wars", "fleets"]));
        assert!(
            f.countries.is_empty()
                && f.legal.is_empty()
                && f.self_state.is_empty()
                && f.god.is_none()
        );
        let l = &compute(&model(), ran(&["countries", "tech"])).legal[0];
        assert_eq!((l.tech_cat, l.fleet_power_cat), (Some(0), None));
        assert_eq!(
            ran(&["tech", "countries"]).names(),
            ["meta", "countries", "tech"]
        );
        assert!(ran(&ALL).all() && !ran(&["countries"]).all());
        assert_eq!(
            ran(&["tech"]).need(),
            Need {
                countries: true,
                starbases: false,
                wars: false
            }
        );
    }

    #[test]
    fn category_thresholds_in_legal() {
        let mut m = model();
        m.countries
            .iter_mut()
            .find(|c| c.id == 7)
            .unwrap()
            .military_power = Some(400.0);
        assert_eq!(compute(&m, ran(&ALL)).legal[0].fleet_power_cat, Some(-2));
        m.countries
            .iter_mut()
            .find(|c| c.id == 7)
            .unwrap()
            .military_power = Some(0.0);
        assert_eq!(compute(&m, ran(&ALL)).legal[0].fleet_power_cat, Some(2));
        m.countries
            .iter_mut()
            .find(|c| c.id == 0)
            .unwrap()
            .military_power = Some(0.0);
        assert_eq!(compute(&m, ran(&ALL)).legal[0].fleet_power_cat, Some(0));
    }

    #[test]
    fn missing_values_make_categories_null_not_zero() {
        let set = |m: &mut SaveModel, id: u64, f: fn(&mut CountryRec)| {
            m.countries.iter_mut().find(|c| c.id == id).map(f).unwrap()
        };
        let clear = |c: &mut CountryRec| {
            c.military_power = None;
            c.tech_count = None;
            c.alloys_income = None;
        };
        let cats = |m: &SaveModel| {
            let l = &compute(m, ran(&ALL)).legal[0];
            (l.fleet_power_cat, l.tech_cat, l.economy_cat)
        };
        let mut m = model();
        set(&mut m, 7, clear);
        assert_eq!(cats(&m), (None, None, None), "observer missing");
        let mut m = model();
        set(&mut m, 0, clear);
        assert_eq!(cats(&m), (None, None, None), "player missing");
        let mut m = model();
        set(&mut m, 0, clear);
        set(&mut m, 7, clear);
        assert_eq!(cats(&m), (None, None, None), "both missing");
        assert_eq!(cats(&model()), (Some(0), Some(0), Some(0)));
    }

    #[test]
    fn duplicate_sids_are_listed_but_excluded() {
        let mut m = model();
        m.countries.push(country(8, Some(3), true));
        m.countries.push(country(9, Some(4), true));
        let f = compute(&m, ran(&ALL));
        assert_eq!(f.self_state.keys().collect::<Vec<_>>(), ["4"]);
        assert_eq!(f.legal.len(), 1);
        let ids: Vec<u64> = f.countries.iter().map(|c| c.country_id).collect();
        assert!(ids.contains(&8) && ids.contains(&9));
        let dups: Vec<_> = f.countries.iter().filter(|c| c.sid == Some(3)).collect();
        assert_eq!(dups.len(), 2, "both listed with the sid as in the save");
        assert!(dups.iter().all(|c| c.managed));
        assert!(f.legal.iter().all(|l| l.observer_sid == 4));
    }

    #[test]
    fn unmanaged_sharing_managed_sid_excludes_managed() {
        let mut m = model();
        m.countries.push(country(8, Some(4), true));
        m.countries.push(country(9, Some(4), false));
        let f = compute(&m, ran(&ALL));
        assert!(!f.self_state.contains_key("4"));
        assert!(f.legal.iter().all(|l| l.observer_sid != 4));
        assert_eq!(f.countries.iter().filter(|c| c.sid == Some(4)).count(), 2);
    }

    #[test]
    fn duplicate_sid_player_keeps_sid_in_countries() {
        let mut m = model();
        m.countries.iter_mut().find(|c| c.id == 0).unwrap().managed = true;
        m.countries.iter_mut().find(|c| c.id == 0).unwrap().sid = Some(3);
        let f = compute(&m, ran(&ALL));
        let p = f.countries.iter().find(|c| c.country_id == 0).unwrap();
        assert_eq!((p.sid, p.managed), (Some(3), true));
        assert!(f.self_state.is_empty());
    }
}
