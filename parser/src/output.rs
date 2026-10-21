//! JSON documents mirroring `sae.contracts.c4_parse` field for field.
// U3 constructs ParseResult and friends; until then they are unused.
#![allow(dead_code)]

use std::collections::BTreeMap;

use serde::Serialize;

use crate::error::Failure;

pub const SCHEMA_VERSION: u8 = 1;
pub const MAX_STDOUT_BYTES: usize = 4 * 1024 * 1024;
pub const PARSER_VERSION: &str = env!("CARGO_PKG_VERSION");

#[derive(Debug, Clone, Copy, PartialEq, Serialize)]
pub struct Cost {
    pub rss_peak_bytes: u64,
    pub cpu_s: f64,
    pub wall_s: f64,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct SaveInfo {
    pub path: String,
    pub size_bytes: u64,
    pub mtime_ns: u64,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct SaveMeta {
    pub game_version: String,
    pub date: String,
    pub ironman: Option<bool>,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct CountryInfo {
    pub country_id: u64,
    pub name: String,
    pub sid: Option<u64>,
    pub is_player: bool,
    pub managed: bool,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct SelfState {
    pub fleet_power: f64,
    pub naval_cap_used: f64,
    pub naval_cap: f64,
    pub energy: f64,
    pub minerals: f64,
    pub alloys: f64,
    pub alloys_income: f64,
    pub systems_owned: u64,
    pub wars: u64,
    pub applied_seq: u64,
    pub pending_seq: u64,
    pub applied_econ: u8,
    pub applied_mil: u8,
}

/// `None` fields are omitted (the Python model defaults them to null).
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct LegalView {
    pub observer_sid: u64,
    pub in_contact: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub systems_owned: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub frontier_systems: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub fortified_border_starbases: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub fleet_power_cat: Option<i8>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tech_cat: Option<i8>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub economy_cat: Option<i8>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub at_war_with_observer: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub player_war_count: Option<u64>,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct GodView {
    pub fleet_power: f64,
    pub systems_owned: u64,
    pub tech_researched: u64,
    pub alloys_income: f64,
    pub fortified_starbases: u64,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct ParseResult {
    pub schema_version: u8,
    pub status: &'static str,
    pub parser_version: &'static str,
    pub save: SaveInfo,
    pub meta: SaveMeta,
    pub countries: Vec<CountryInfo>,
    pub self_state: BTreeMap<String, SelfState>,
    pub legal: Vec<LegalView>,
    pub god: Option<GodView>,
    pub sections: Vec<String>,
    pub cost: Cost,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct ParseError {
    pub schema_version: u8,
    pub status: &'static str,
    pub parser_version: &'static str,
    pub exit_code: u8,
    pub error: &'static str,
    pub detail: String,
    pub cost: Option<Cost>,
}

impl ParseError {
    /// Build the error document for a failure.
    pub fn from_failure(f: &Failure, cost: Cost) -> Self {
        ParseError {
            schema_version: SCHEMA_VERSION,
            status: "error",
            parser_version: PARSER_VERSION,
            exit_code: f.kind.code(),
            error: f.kind.name(),
            detail: f.detail.clone(),
            cost: Some(cost),
        }
    }
}

/// Serialize a document to one line plus LF. Over 4 MiB becomes a parse error.
pub fn render<T: Serialize>(doc: &T, cost: Cost) -> (String, u8) {
    let too_big = Failure::new(crate::error::Kind::Parse, "output exceeds 4 MiB");
    match serde_json::to_string(doc) {
        Ok(s) if s.len() < MAX_STDOUT_BYTES => (s + "\n", 0),
        Ok(_) => render_failure(&too_big, cost),
        Err(e) => render_failure(
            &Failure::new(crate::error::Kind::Parse, format!("serialize: {e}")),
            cost,
        ),
    }
}

/// Serialize a failure document; returns (stdout text, exit code).
pub fn render_failure(f: &Failure, cost: Cost) -> (String, u8) {
    let doc = ParseError::from_failure(f, cost);
    let line = serde_json::to_string(&doc).unwrap_or_else(|_| {
        // Cannot happen for this plain struct; keep a valid document anyway.
        format!(
            "{{\"schema_version\":1,\"status\":\"error\",\"parser_version\":\"{PARSER_VERSION}\",\"exit_code\":{},\"error\":\"{}\",\"detail\":\"\"}}",
            f.kind.code(),
            f.kind.name()
        )
    });
    (line + "\n", f.kind.code())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn legal_none_fields_absent() {
        let v = LegalView {
            observer_sid: 4,
            in_contact: false,
            systems_owned: None,
            frontier_systems: None,
            fortified_border_starbases: None,
            fleet_power_cat: None,
            tech_cat: None,
            economy_cat: None,
            at_war_with_observer: None,
            player_war_count: None,
        };
        assert_eq!(
            serde_json::to_string(&v).unwrap_or_default(),
            r#"{"observer_sid":4,"in_contact":false}"#
        );
    }
}
