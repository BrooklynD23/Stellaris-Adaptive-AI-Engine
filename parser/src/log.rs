//! Diagnostics to stderr in the C4 `level=<lvl>|msg=<text>` format. A stderr failure never panics.

use std::io::Write;

/// Write one diagnostic line to stderr; errors are ignored by design.
pub fn log(level: &str, msg: &str) {
    let _ = writeln!(std::io::stderr().lock(), "level={level}|msg={msg}");
}
