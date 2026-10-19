//! Hand-rolled argv parser (no clap).

use std::ffi::OsString;
use std::path::PathBuf;

use crate::error::{Failure, Kind};

pub const SECTIONS: [&str; 6] = ["meta", "countries", "starbases", "fleets", "wars", "tech"];

/// A parsed command line.
#[derive(Debug, PartialEq, Eq)]
pub enum Command {
    Version,
    Parse {
        save: PathBuf,
        sections: Vec<String>,
        threads: usize,
        background: bool,
    },
    Fixture {
        spec: PathBuf,
        out: PathBuf,
    },
}

fn usage(msg: impl AsRef<str>) -> Failure {
    Failure::new(Kind::Usage, msg)
}

/// Parse argv (without program name).
pub fn parse_args(args: &[OsString]) -> Result<Command, Failure> {
    let mut it = args.iter();
    let Some(first) = it.next() else {
        return Err(usage(
            "missing subcommand; expected parse|fixture|--version",
        ));
    };
    match first.to_str() {
        Some("--version") if args.len() == 1 => Ok(Command::Version),
        Some("--version") => Err(usage("--version takes no arguments")),
        Some("parse") => parse_cmd(it),
        Some("fixture") => fixture_cmd(it),
        _ => Err(usage(format!("unknown subcommand {first:?}"))),
    }
}

fn value<'a>(it: &mut std::slice::Iter<'a, OsString>, flag: &str) -> Result<&'a OsString, Failure> {
    it.next()
        .ok_or_else(|| usage(format!("{flag} needs a value")))
}

fn parse_cmd(mut it: std::slice::Iter<'_, OsString>) -> Result<Command, Failure> {
    let (mut save, mut sections, mut threads, mut background) = (None, None, 1usize, false);
    while let Some(a) = it.next() {
        match a.to_str() {
            Some("--save") => save = Some(PathBuf::from(value(&mut it, "--save")?)),
            Some("--sections") => sections = Some(sections_of(value(&mut it, "--sections")?)?),
            Some("--threads") => {
                let v = value(&mut it, "--threads")?;
                threads = v
                    .to_str()
                    .and_then(|s| s.parse::<usize>().ok())
                    .filter(|n| *n >= 1)
                    .ok_or_else(|| usage("--threads must be an integer >= 1"))?;
            }
            Some("--background") => background = true,
            _ => return Err(usage(format!("unknown argument {a:?}"))),
        }
    }
    let save = save.ok_or_else(|| usage("--save is required"))?;
    let sections = sections.unwrap_or_else(|| SECTIONS.iter().map(|s| (*s).to_owned()).collect());
    Ok(Command::Parse {
        save,
        sections,
        threads,
        background,
    })
}

fn sections_of(v: &OsString) -> Result<Vec<String>, Failure> {
    let s = v
        .to_str()
        .ok_or_else(|| usage("--sections is not valid UTF-8"))?;
    let mut out: Vec<String> = Vec::new();
    for name in s.split(',') {
        if !SECTIONS.contains(&name) {
            return Err(usage(format!("unknown section {name:?}")));
        }
        if !out.iter().any(|o| o == name) {
            out.push(name.to_owned());
        }
    }
    Ok(out)
}

fn fixture_cmd(mut it: std::slice::Iter<'_, OsString>) -> Result<Command, Failure> {
    let (mut spec, mut out) = (None, None);
    while let Some(a) = it.next() {
        match a.to_str() {
            Some("--spec") => spec = Some(PathBuf::from(value(&mut it, "--spec")?)),
            Some("--out") => out = Some(PathBuf::from(value(&mut it, "--out")?)),
            _ => return Err(usage(format!("unknown argument {a:?}"))),
        }
    }
    Ok(Command::Fixture {
        spec: spec.ok_or_else(|| usage("--spec is required"))?,
        out: out.ok_or_else(|| usage("--out is required"))?,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn a(v: &[&str]) -> Vec<OsString> {
        v.iter().map(OsString::from).collect()
    }

    #[test]
    fn parses_full_parse_command() {
        let c = parse_args(&a(&[
            "parse",
            "--save",
            "x",
            "--sections",
            "meta,meta,wars",
            "--threads",
            "2",
            "--background",
        ]));
        assert_eq!(
            c,
            Ok(Command::Parse {
                save: "x".into(),
                sections: vec!["meta".into(), "wars".into()],
                threads: 2,
                background: true
            })
        );
    }

    #[test]
    fn rejects_bad_input() {
        for bad in [
            vec![],
            vec!["nope"],
            vec!["parse"],
            vec!["parse", "--save"],
            vec!["parse", "--save", "x", "--threads", "0"],
            vec!["parse", "--save", "x", "--sections", "bogus"],
            vec!["fixture", "--spec", "s"],
        ] {
            assert_eq!(
                parse_args(&a(&bad)).map_err(|f| f.kind),
                Err(Kind::Usage),
                "{bad:?}"
            );
        }
    }
}
