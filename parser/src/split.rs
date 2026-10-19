//! Iterative top-level splitter for Clausewitz text.
//!
//! Returns the byte ranges of `key = value` entries at brace depth 0 of a slice. Brace-depth and quote
//! aware (`\"` inside quotes does not close the string, like jomini), comments (`#` to end of line)
//! are skipped, and the scan is a flat loop: never recursive, so nesting depth cannot overflow the stack.
//! The same function splits the inside of a `{ ... }` block (see [`inner`]), which lets the extractor
//! hand jomini one small span at a time.

use crate::error::{Failure, Kind};

/// Most entries one `split` call may return; bounds the entry vector (about 40 bytes each).
pub const MAX_ENTRIES: usize = 4_000_000;

/// One `key = value` entry; `start..end` covers the key through the end of the value.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Entry<'a> {
    /// Key bytes (quotes stripped when the key was quoted).
    pub key: &'a [u8],
    /// Offset of the key's first byte.
    pub start: usize,
    /// Offset one past the last byte of the value.
    pub end: usize,
    /// Offset of the first byte of the value.
    pub value_start: usize,
}

fn parse_err(msg: impl AsRef<str>) -> Failure {
    Failure::new(Kind::Parse, msg)
}

fn is_ws(b: u8) -> bool {
    b.is_ascii_whitespace()
}

/// Index of the byte after the quoted string whose opening quote is at `open`.
fn skip_quote(data: &[u8], open: usize) -> Result<usize, Failure> {
    let mut i = open + 1;
    loop {
        let rest = data.get(i..).unwrap_or_default();
        match rest.iter().position(|b| *b == b'"' || *b == b'\\') {
            None => {
                return Err(parse_err(format!(
                    "unterminated string starting at byte {open}"
                )))
            }
            Some(p) => {
                let at = i + p;
                if data.get(at) == Some(&b'"') {
                    return Ok(at + 1);
                }
                i = at + 2; // backslash escapes the next byte
            }
        }
    }
}

/// Index of the byte after the block whose `{` is at `open` (`depth` stays flat, no recursion).
fn skip_block(data: &[u8], open: usize) -> Result<usize, Failure> {
    let mut depth: usize = 0;
    let mut i = open;
    while let Some(rest) = data.get(i..) {
        let Some(p) = rest
            .iter()
            .position(|b| matches!(b, b'{' | b'}' | b'"' | b'#'))
        else {
            break;
        };
        i += p;
        match data.get(i) {
            Some(b'{') => {
                depth += 1;
                i += 1;
            }
            Some(b'}') => {
                depth -= 1; // depth >= 1 here: the scan starts on `{`
                i += 1;
                if depth == 0 {
                    return Ok(i);
                }
            }
            Some(b'"') => i = skip_quote(data, i)?,
            _ => i = skip_comment(data, i),
        }
    }
    Err(parse_err(format!(
        "unbalanced braces: block opened at byte {open} never closes"
    )))
}

fn skip_comment(data: &[u8], at: usize) -> usize {
    data.get(at..)
        .and_then(|r| r.iter().position(|b| *b == b'\n'))
        .map_or(data.len(), |p| at + p + 1)
}

/// Skip whitespace and comments.
fn skip_trivia(data: &[u8], mut i: usize) -> usize {
    loop {
        match data.get(i) {
            Some(b) if is_ws(*b) => i += 1,
            Some(b'#') => i = skip_comment(data, i),
            _ => return i,
        }
    }
}

/// Length of the operator at `i`: `=`, `>`, `<`, `>=`, `<=`, `!=`, `?=`; 0 if there is none.
fn op_len(data: &[u8], i: usize) -> usize {
    let next_eq = data.get(i + 1) == Some(&b'=');
    match data.get(i) {
        Some(b'=') => 1,
        Some(b'>' | b'<') => 1 + usize::from(next_eq),
        Some(b'!' | b'?') if next_eq => 2,
        _ => 0,
    }
}

/// End (exclusive) of a bare token starting at `i`; an operator (`>=`, `?=`, ...) ends the token.
fn bare_end(data: &[u8], i: usize) -> usize {
    let mut e = i;
    while let Some(&b) = data.get(e) {
        if is_ws(b)
            || matches!(b, b'=' | b'{' | b'}' | b'"' | b'#' | b'<' | b'>')
            || (e > i && op_len(data, e) > 0)
        {
            break;
        }
        e += 1;
    }
    e
}

/// Split `data` into its depth-0 `key = value` entries.
///
/// Stray tokens that are not followed by `=` are ignored. A `}` at depth 0, an unclosed block or an
/// unterminated string is a parse error.
pub fn split(data: &[u8]) -> Result<Vec<Entry<'_>>, Failure> {
    split_capped(data, MAX_ENTRIES)
}

fn split_capped(data: &[u8], cap: usize) -> Result<Vec<Entry<'_>>, Failure> {
    let mut out = Vec::new();
    let mut i = if data.starts_with(b"\xEF\xBB\xBF") {
        3
    } else {
        0
    };
    loop {
        i = skip_trivia(data, i);
        let Some(&b) = data.get(i) else {
            return Ok(out);
        };
        let start = i;
        let key: &[u8] = match b {
            b'}' => {
                return Err(parse_err(format!(
                    "unbalanced braces: stray `}}` at byte {i}"
                )))
            }
            b'{' => {
                i = skip_block(data, i)?; // anonymous block at depth 0: not an entry
                continue;
            }
            b'=' => {
                i += 1; // stray `=`
                continue;
            }
            b'<' | b'>' | b'!' | b'?' if op_len(data, i) > 0 => {
                i += op_len(data, i); // stray operator
                continue;
            }
            b'"' => {
                let after = skip_quote(data, i)?;
                let k = data.get(i + 1..after - 1).unwrap_or_default();
                i = after;
                k
            }
            _ => {
                let e = bare_end(data, i);
                let k = data.get(i..e).unwrap_or_default();
                i = e;
                k
            }
        };
        let j = skip_trivia(data, i);
        let op = op_len(data, j);
        if op == 0 {
            continue; // bare scalar at depth 0
        }
        let value_start = skip_trivia(data, j + op);
        let end = match data.get(value_start) {
            None => return Err(parse_err(format!("key at byte {start} has no value"))),
            Some(b'{') => skip_block(data, value_start)?,
            Some(b'"') => skip_quote(data, value_start)?,
            Some(b'}') => {
                return Err(parse_err(format!(
                    "unbalanced braces: key at byte {start} has no value"
                )))
            }
            Some(_) => {
                let e = bare_end(data, value_start);
                // `rgb { ... }` / `hsv { ... }` header followed by a block: keep the block in the value.
                let k = skip_trivia(data, e);
                let word = data.get(value_start..e).unwrap_or_default();
                if data.get(k) == Some(&b'{') && (word == b"rgb" || word == b"hsv") {
                    skip_block(data, k)?
                } else {
                    e.max(value_start + 1)
                }
            }
        };
        if out.len() >= cap {
            return Err(Failure::new(
                Kind::Format,
                format!("too many entries (more than {cap}) in one block"),
            ));
        }
        out.push(Entry {
            key,
            start,
            end,
            value_start,
        });
        i = end;
    }
}

/// Bytes between the braces of a block value (`{ a=1 }` gives ` a=1 `); `None` if `value` is not a block.
pub fn inner(value: &[u8]) -> Option<&[u8]> {
    let v = value.strip_prefix(b"{")?;
    v.strip_suffix(b"}")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn keys(s: &str) -> Vec<String> {
        split(s.as_bytes())
            .unwrap()
            .iter()
            .map(|e| String::from_utf8_lossy(e.key).into_owned())
            .collect()
    }

    #[test]
    fn splits_top_level_only() {
        let s = "a=1\nb={ c={ d=2 } e=\"}{\" }\n# comment { \nf=\"x\\\"}\"\ng=yes";
        assert_eq!(keys(s), ["a", "b", "f", "g"]);
        let es = split(s.as_bytes()).unwrap();
        assert_eq!(
            &s.as_bytes()[es[1].start..es[1].end],
            b"b={ c={ d=2 } e=\"}{\" }"
        );
        assert_eq!(&s.as_bytes()[es[2].value_start..es[2].end], b"\"x\\\"}\"");
    }

    #[test]
    fn entry_cap_is_format() {
        let e = split_capped(b"a=1 b=2 c=3", 2).unwrap_err();
        assert_eq!(
            (e.kind, e.detail.contains("too many entries")),
            (Kind::Format, true)
        );
        assert_eq!(split_capped(b"a=1 b=2", 2).unwrap().len(), 2);
    }

    #[test]
    fn inner_strips_braces() {
        assert_eq!(inner(b"{ a=1 }"), Some(&b" a=1 "[..]));
        assert_eq!(inner(b"none"), None);
        assert_eq!(keys("x={ 1=none 2={ q=1 } }"), ["x"]);
    }

    #[test]
    fn unbalanced_is_parse_error() {
        for bad in ["a={ b=1", "a=1 }", "a=\"open", "a={ \"x }", "a="] {
            assert_eq!(
                split(bad.as_bytes()).unwrap_err().kind,
                Kind::Parse,
                "{bad}"
            );
        }
    }

    #[test]
    fn deep_nesting_does_not_overflow() {
        let s = format!("a={}", "{".repeat(1_000_000));
        assert_eq!(split(s.as_bytes()).unwrap_err().kind, Kind::Parse);
        let ok = format!("a={}{}", "{".repeat(100_000), "}".repeat(100_000));
        assert_eq!(split(ok.as_bytes()).unwrap().len(), 1);
    }

    #[test]
    fn header_and_quoted_keys() {
        assert_eq!(
            keys("\"q k\"=1 color=rgb { 1 2 3 } z=2"),
            ["q k", "color", "z"]
        );
        // Only the exact words rgb/hsv glue a following block to the value.
        let es = split(b"a=rgbx { 1 } b=hsv { 2 } c=1").unwrap();
        assert_eq!(
            es.iter().map(|e| e.key).collect::<Vec<_>>(),
            [&b"a"[..], b"b", b"c"]
        );
        assert_eq!(es[0].end - es[0].value_start, 4);
    }

    #[test]
    fn operators_are_key_op_value() {
        for s in [
            "a > 1 b=2",
            "a>=1 b=2",
            "a <= 1 b=2",
            "a<1 b=2",
            "a != 1 b=2",
            "a?=1 b=2",
            "a ?= 1 b=2",
            "a >= 1 b=2",
        ] {
            let es = split(s.as_bytes()).unwrap();
            assert_eq!(
                es.iter().map(|e| e.key).collect::<Vec<_>>(),
                [&b"a"[..], b"b"],
                "{s}"
            );
            assert_eq!(&s.as_bytes()[es[0].value_start..es[0].end], b"1", "{s}");
        }
        assert_eq!(keys("> >= ?= != < a=1"), ["a"]);
    }
}
