//! `sae-parse`: C4 save parser CLI (contracts/C4_sae_parse.md).
#![cfg_attr(
    not(test),
    deny(clippy::unwrap_used, clippy::expect_used, clippy::indexing_slicing)
)]

mod background;
mod cli;
mod cost;
mod error;
mod extract;
mod features;
mod fixture;
mod input;
mod log;
mod output;
mod split;

use std::io::Write;
use std::process::ExitCode;
use std::time::Instant;

use cli::Command;
use error::Failure;

fn main() -> ExitCode {
    let start = Instant::now();
    let args: Vec<_> = std::env::args_os().skip(1).collect();
    match cli::parse_args(&args).and_then(|c| run(c, start)) {
        Ok(code) => ExitCode::from(code),
        Err(f) => emit_failure(&f, start),
    }
}

fn emit_failure(f: &Failure, start: Instant) -> ExitCode {
    let (text, code) = output::render_failure(f, cost::measure(start));
    write_stdout(&text);
    ExitCode::from(code)
}

fn write_stdout(text: &str) {
    let mut out = std::io::stdout().lock();
    if let Err(e) = out.write_all(text.as_bytes()).and_then(|()| out.flush()) {
        log::log("warn", &format!("stdout write failed: {e}"));
    }
}

/// Run a command; the `Ok` value is the process exit code (non-zero only for an oversize-output failure).
fn run(cmd: Command, start: Instant) -> Result<u8, Failure> {
    match cmd {
        Command::Version => {
            write_stdout(&format!("sae-parse {}\n", output::PARSER_VERSION));
            Ok(0)
        }
        Command::Parse {
            save,
            sections,
            threads,
            background,
        } => {
            if background {
                background::enter();
            }
            // Single-threaded in v1: `threads` is validated by the CLI and never exceeded (C4 section 1).
            let _ = threads;
            run_parse(&save, &sections, start)
        }
        Command::Fixture { spec, out } => {
            fixture::run(&spec, &out)?;
            log::log("info", &format!("fixture written to {}", out.display()));
            Ok(0)
        }
    }
}

fn run_parse(save: &std::path::Path, sections: &[String], start: Instant) -> Result<u8, Failure> {
    let ran = features::Ran::from_names(sections);
    let loaded = input::load(save)?;
    let input::Loaded {
        gamestate,
        meta,
        info,
    } = loaded;
    let model = extract::extract(&gamestate, meta.as_deref(), ran.need())?;
    drop(gamestate); // free the decompressed text before building output
    let feats = features::compute(&model, ran);
    features::check_bounds(&feats)?;
    if !ran.all() {
        log::log(
            "warn",
            "not all six sections ran: self_state is {} and god is null",
        );
    }
    let cost = cost::measure(start);
    let result = output::ParseResult {
        schema_version: output::SCHEMA_VERSION,
        status: "ok",
        parser_version: output::PARSER_VERSION,
        save: info,
        meta: model.meta,
        countries: feats.countries,
        self_state: feats.self_state,
        legal: feats.legal,
        god: feats.god,
        sections: ran.names(),
        cost,
    };
    let (text, code) = output::render(&result, cost);
    write_stdout(&text);
    Ok(code)
}
