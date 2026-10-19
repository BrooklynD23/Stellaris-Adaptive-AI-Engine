//! Save loading: stat, read, stat again; envelope detection; size caps.

use std::fs::File;
use std::io::{self, Cursor, Read};
use std::path::Path;
use std::time::UNIX_EPOCH;

use zip::result::ZipError;
use zip::ZipArchive;

use crate::error::{Failure, Kind};
use crate::output::SaveInfo;

const MIB: u64 = 1024 * 1024;
/// Caps sized under the 1 GiB Job commit cap (plan section 1).
pub const MAX_ZIP_FILE: u64 = 256 * MIB;
pub const MAX_GAMESTATE: u64 = 400 * MIB;
const MAX_META: u64 = 16 * MIB;
/// A save zip has `meta` and `gamestate`; anything with more than this many entries is refused before
/// `ZipArchive::new` builds its per-entry table.
pub const MAX_ZIP_ENTRIES: u64 = 64;
const ZIP_MAGIC: &[u8; 4] = b"PK\x03\x04";

/// Size and mtime of the save file at one instant.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Stat {
    pub size: u64,
    pub mtime_ns: u64,
}

/// Result of loading a save.
#[derive(Debug)]
pub struct Loaded {
    pub gamestate: Vec<u8>,
    pub meta: Option<Vec<u8>>,
    pub info: SaveInfo,
}

/// Real filesystem stat. Non-regular files are an input error.
pub fn real_stat(path: &Path) -> io::Result<Stat> {
    let md = std::fs::metadata(path)?;
    if !md.is_file() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "not a regular file",
        ));
    }
    let mtime_ns = md
        .modified()
        .ok()
        .and_then(|t| t.duration_since(UNIX_EPOCH).ok())
        .map_or(0, |d| u64::try_from(d.as_nanos()).unwrap_or(u64::MAX));
    Ok(Stat {
        size: md.len(),
        mtime_ns,
    })
}

/// Load `path` using the real filesystem stat.
pub fn load(path: &Path) -> Result<Loaded, Failure> {
    load_with(path, &real_stat)
}

/// Windows ERROR_SHARING_VIOLATION (32) or ERROR_LOCK_VIOLATION (33).
#[cfg_attr(not(windows), allow(dead_code))]
fn is_sharing_violation(code: Option<i32>) -> bool {
    matches!(code, Some(32 | 33))
}

fn io_failure(what: &str, e: &io::Error) -> Failure {
    // Windows sharing violation (32) / lock violation (33): the game is still writing.
    #[cfg(windows)]
    if is_sharing_violation(e.raw_os_error()) {
        return Failure::new(
            Kind::Unstable,
            format!("{what}: sharing violation, save still being written ({e})"),
        );
    }
    Failure::new(Kind::Input, format!("{what}: {e}"))
}

fn unstable(detail: impl AsRef<str>) -> Failure {
    Failure::new(Kind::Unstable, detail)
}

/// Load with an injectable stat function (tests inject a changing one).
pub fn load_with(path: &Path, stat: &dyn Fn(&Path) -> io::Result<Stat>) -> Result<Loaded, Failure> {
    let before = stat(path).map_err(|e| io_failure("stat", &e))?;
    if before.size > MAX_GAMESTATE {
        return Err(Failure::new(
            Kind::Format,
            format!("file of {} bytes exceeds cap", before.size),
        ));
    }
    let mut file = File::open(path).map_err(|e| io_failure("read", &e))?;
    // Read the 4-byte header first: a zip gets the smaller cap before the whole file is allocated.
    let mut head = Vec::with_capacity(4);
    (&mut file)
        .take(4)
        .read_to_end(&mut head)
        .map_err(|e| io_failure("read", &e))?;
    check_size_cap(&head, before.size)?;
    let mut buf = Vec::with_capacity(usize::try_from(before.size).unwrap_or(0));
    buf.extend_from_slice(&head);
    file.take(
        before
            .size
            .saturating_add(1)
            .saturating_sub(head.len() as u64),
    )
    .read_to_end(&mut buf)
    .map_err(|e| io_failure("read", &e))?;
    let after = stat(path).map_err(|e| io_failure("stat", &e))?;
    if after != before || buf.len() as u64 != before.size {
        return Err(unstable("size or mtime changed while reading"));
    }
    let info = SaveInfo {
        path: path.to_string_lossy().into_owned(),
        size_bytes: before.size,
        mtime_ns: before.mtime_ns,
    };
    if buf.starts_with(b"PK\x03\x04") {
        let (gamestate, meta) = read_zip(&buf)?;
        return Ok(Loaded {
            gamestate,
            meta,
            info,
        });
    }
    if buf.iter().take(4096).any(|b| *b == 0) {
        return Err(Failure::new(
            Kind::Format,
            "not a zip and not text (NUL bytes in header)",
        ));
    }
    Ok(Loaded {
        gamestate: buf,
        meta: None,
        info,
    })
}

/// Size cap for a file whose first bytes are `head`: zips get `MAX_ZIP_FILE`, everything else `MAX_GAMESTATE`.
/// Decides from the header and the stat size alone, so nothing is allocated for an oversize file.
fn check_size_cap(head: &[u8], size: u64) -> Result<(), Failure> {
    if head.starts_with(ZIP_MAGIC) && size > MAX_ZIP_FILE {
        return Err(Failure::new(
            Kind::Format,
            format!("zip file of {size} bytes exceeds 256 MiB cap"),
        ));
    }
    Ok(())
}

fn le16(b: &[u8], at: usize) -> Option<u64> {
    let s = b.get(at..)?.get(..2)?;
    Some(u64::from(u16::from_le_bytes(s.try_into().ok()?)))
}

fn le64(b: &[u8], at: usize) -> Option<u64> {
    let s = b.get(at..)?.get(..8)?;
    Some(u64::from_le_bytes(s.try_into().ok()?))
}

/// Offset of the real EOCD: a `PK\x05\x06` whose comment length runs exactly to the end of the buffer.
/// Candidates are tried from the tail backwards, so a fake signature inside the comment is rejected.
fn find_eocd(buf: &[u8]) -> Option<usize> {
    let tail_start = buf.len().saturating_sub(22 + 65_535);
    let tail = buf.get(tail_start..)?;
    tail.windows(4)
        .enumerate()
        .rev()
        .filter(|(_, w)| *w == b"PK\x05\x06")
        .map(|(i, _)| tail_start + i)
        .find(|&e| {
            le16(buf, e.saturating_add(20))
                .and_then(|c| usize::try_from(c).ok())
                .and_then(|c| e.checked_add(22)?.checked_add(c))
                == Some(buf.len())
        })
}

/// Total entry count from the EOCD (and the zip64 EOCD when the 16-bit field is saturated), read from
/// the buffer tail. `None` when no EOCD is found (the zip crate then reports the damage).
fn zip_entry_count(buf: &[u8]) -> Option<u64> {
    let eocd = find_eocd(buf)?;
    let total = le16(buf, eocd.checked_add(10)?)?;
    if total != 0xFFFF {
        return Some(total);
    }
    // zip64 locator sits right before the EOCD; it points at the zip64 EOCD record. Without one the
    // saturated count itself exceeds the cap.
    let Some(loc) = eocd
        .checked_sub(20)
        .filter(|l| buf.get(*l..).and_then(|s| s.get(..4)) == Some(b"PK\x06\x07"))
    else {
        return Some(0xFFFF);
    };
    let Some(rec) = le64(buf, loc + 8).and_then(|r| usize::try_from(r).ok()) else {
        return Some(u64::MAX);
    };
    if buf.get(rec..).and_then(|s| s.get(..4)) != Some(b"PK\x06\x06") {
        return Some(u64::MAX);
    }
    rec.checked_add(32)
        .and_then(|a| le64(buf, a))
        .or(Some(u64::MAX))
}

fn zip_err(what: &str, e: &ZipError) -> Failure {
    match e {
        ZipError::UnsupportedArchive(_) => Failure::new(Kind::Format, format!("{what}: {e}")),
        _ => unstable(format!("{what}: {e}")),
    }
}

fn read_zip(buf: &[u8]) -> Result<(Vec<u8>, Option<Vec<u8>>), Failure> {
    if let Some(n) = zip_entry_count(buf).filter(|n| *n > MAX_ZIP_ENTRIES) {
        return Err(Failure::new(
            Kind::Format,
            format!("zip has {n} entries (max {MAX_ZIP_ENTRIES})"),
        ));
    }
    let mut archive = ZipArchive::new(Cursor::new(buf)).map_err(|e| zip_err("zip open", &e))?;
    let gamestate = match read_entry(&mut archive, "gamestate", MAX_GAMESTATE)? {
        Some(g) => g,
        None => return Err(Failure::new(Kind::Format, "zip has no gamestate entry")),
    };
    let meta = read_entry(&mut archive, "meta", MAX_META)?;
    Ok((gamestate, meta))
}

fn read_entry(
    archive: &mut ZipArchive<Cursor<&[u8]>>,
    name: &str,
    cap: u64,
) -> Result<Option<Vec<u8>>, Failure> {
    let mut entry = match archive.by_name(name) {
        Ok(e) => e,
        Err(ZipError::FileNotFound) => return Ok(None),
        Err(e) => return Err(zip_err(name, &e)),
    };
    let declared = entry.size();
    if declared > cap {
        return Err(Failure::new(
            Kind::Format,
            format!("{name} declares {declared} bytes, over cap"),
        ));
    }
    let mut out = Vec::with_capacity(usize::try_from(declared).unwrap_or(0));
    (&mut entry)
        .take(declared + 1)
        .read_to_end(&mut out)
        .map_err(|e| unstable(format!("{name}: {e}")))?;
    if out.len() as u64 != declared {
        return Err(unstable(format!(
            "{name}: declared {declared} bytes, got {}",
            out.len()
        )));
    }
    Ok(Some(out))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::Cell;
    use std::io::Write;

    fn tmp(name: &str, data: &[u8]) -> std::path::PathBuf {
        let p = std::env::temp_dir().join(format!("sae_input_{}_{name}", std::process::id()));
        std::fs::write(&p, data).unwrap();
        p
    }

    #[test]
    fn plain_text_loads() {
        let p = tmp("plain", b"date=\"2231.04.01\"\n");
        let l = load(&p).unwrap();
        assert_eq!(l.gamestate, b"date=\"2231.04.01\"\n");
        assert!(l.meta.is_none());
        std::fs::remove_file(p).ok();
    }

    #[test]
    fn changed_stat_is_unstable() {
        let p = tmp("unstable", b"a=1\n");
        for change_size in [true, false] {
            let n = Cell::new(0);
            let stat = |path: &Path| {
                let mut s = real_stat(path)?;
                n.set(n.get() + 1);
                if n.get() > 1 {
                    if change_size {
                        s.size += 1
                    } else {
                        s.mtime_ns += 1
                    }
                }
                Ok(s)
            };
            assert_eq!(load_with(&p, &stat).unwrap_err().kind, Kind::Unstable);
        }
        std::fs::remove_file(p).ok();
    }

    #[test]
    fn plain_text_cap_is_format() {
        let p = tmp("cap", b"a=1\n");
        let stat = |_: &Path| {
            Ok(Stat {
                size: MAX_GAMESTATE + 1,
                mtime_ns: 1,
            })
        };
        assert_eq!(load_with(&p, &stat).unwrap_err().kind, Kind::Format);
        std::fs::remove_file(p).ok();
    }

    #[test]
    fn declared_gamestate_cap_is_format() {
        let mut w = zip::ZipWriter::new(Cursor::new(Vec::new()));
        w.start_file("gamestate", zip::write::SimpleFileOptions::default())
            .unwrap();
        w.write_all(b"abc").unwrap();
        let bytes = w.finish().unwrap().into_inner();
        let mut archive = ZipArchive::new(Cursor::new(&bytes[..])).unwrap();
        assert!(read_entry(&mut archive, "gamestate", 2).is_err_and(|f| f.kind == Kind::Format));
        assert_eq!(
            read_entry(&mut archive, "gamestate", 3).unwrap().as_deref(),
            Some(&b"abc"[..])
        );
    }

    #[test]
    fn sharing_violation_codes() {
        assert!(is_sharing_violation(Some(32)) && is_sharing_violation(Some(33)));
        assert!(!is_sharing_violation(Some(2)) && !is_sharing_violation(None));
    }

    #[test]
    fn unsupported_archive_is_format() {
        assert_eq!(
            zip_err("x", &ZipError::UnsupportedArchive("m")).kind,
            Kind::Format
        );
        assert_eq!(
            zip_err("x", &ZipError::InvalidArchive("m".into())).kind,
            Kind::Unstable
        );
    }

    #[test]
    fn central_directory_size_mismatch_is_unstable() {
        let mut w = zip::ZipWriter::new(Cursor::new(Vec::new()));
        let o = zip::write::SimpleFileOptions::default()
            .compression_method(zip::CompressionMethod::Stored);
        w.start_file("gamestate", o).unwrap();
        w.write_all(b"date=\"2231.04.01\"\n").unwrap();
        let z = w.finish().unwrap().into_inner();
        let n = u32::try_from(b"date=\"2231.04.01\"\n".len()).unwrap();
        let cd = z.windows(4).position(|b| b == b"PK\x01\x02").unwrap();
        for patched in [n - 1, n + 1] {
            let mut bad = z.clone();
            bad[cd + 24..cd + 28].copy_from_slice(&patched.to_le_bytes());
            let err = read_zip(&bad).unwrap_err();
            assert_eq!(err.kind, Kind::Unstable, "{patched}: {}", err.detail);
        }
    }

    #[test]
    fn zip_size_cap_decided_from_header_and_stat() {
        assert!(check_size_cap(b"PK\x03\x04", MAX_ZIP_FILE).is_ok());
        assert_eq!(
            check_size_cap(b"PK\x03\x04", MAX_ZIP_FILE + 1)
                .unwrap_err()
                .kind,
            Kind::Format
        );
        // plain text keeps the larger cap (enforced from the stat size before the header read)
        assert!(check_size_cap(b"date", MAX_ZIP_FILE + 1).is_ok());
        assert!(check_size_cap(b"", u64::MAX).is_ok());
    }

    #[test]
    fn many_entries_is_format() {
        let mut w = zip::ZipWriter::new(Cursor::new(Vec::new()));
        for i in 0..100 {
            w.start_file(format!("e{i}"), zip::write::SimpleFileOptions::default())
                .unwrap();
        }
        let z = w.finish().unwrap().into_inner();
        assert_eq!(zip_entry_count(&z), Some(100));
        assert_eq!(read_zip(&z).unwrap_err().kind, Kind::Format);
        // saturated 16-bit count without a zip64 record counts as too many
        let mut sat = z.clone();
        let eocd = sat.windows(4).rposition(|w| w == b"PK\x05\x06").unwrap();
        sat[eocd + 10..eocd + 12].copy_from_slice(&0xFFFFu16.to_le_bytes());
        assert_eq!(zip_entry_count(&sat), Some(0xFFFF));
        assert_eq!(read_zip(&sat).unwrap_err().kind, Kind::Format);
    }

    fn zip_of(n: usize) -> Vec<u8> {
        let mut w = zip::ZipWriter::new(Cursor::new(Vec::new()));
        for i in 0..n {
            w.start_file(format!("e{i}"), zip::write::SimpleFileOptions::default())
                .unwrap();
        }
        w.finish().unwrap().into_inner()
    }

    #[test]
    fn fake_eocd_in_comment_does_not_bypass_cap() {
        let mut z = zip_of(100);
        // fake EOCD claiming 1 entry, placed in a comment appended after the real EOCD (real comment length is 0)
        let mut fake = b"PK\x05\x06".to_vec();
        fake.extend_from_slice(&[0; 6]);
        fake.extend_from_slice(&1u16.to_le_bytes());
        fake.extend_from_slice(&[0; 8]);
        fake.extend_from_slice(&5u16.to_le_bytes()); // lies about its own comment length
        z.extend_from_slice(&fake);
        // neither candidate is consistent yet, so the fake count is never trusted
        assert_ne!(zip_entry_count(&z), Some(1));
        // with the comment length patched to cover the fake, the real EOCD is found again
        let eocd = z.windows(4).position(|w| w == b"PK\x05\x06").unwrap();
        z[eocd + 20..eocd + 22].copy_from_slice(&u16::try_from(fake.len()).unwrap().to_le_bytes());
        assert_eq!(zip_entry_count(&z), Some(100));
        assert_eq!(read_zip(&z).unwrap_err().kind, Kind::Format);
    }

    #[test]
    fn saturated_count_without_locator_is_capped() {
        let mut z = zip_of(1);
        let eocd = z.windows(4).rposition(|w| w == b"PK\x05\x06").unwrap();
        z[eocd + 10..eocd + 12].copy_from_slice(&0xFFFFu16.to_le_bytes());
        assert_eq!(zip_entry_count(&z), Some(0xFFFF));
        assert_eq!(read_zip(&z).unwrap_err().kind, Kind::Format);
    }

    #[test]
    fn huge_zip64_offset_does_not_panic() {
        let mut z = zip_of(1);
        let eocd = z.windows(4).rposition(|w| w == b"PK\x05\x06").unwrap();
        z[eocd + 10..eocd + 12].copy_from_slice(&0xFFFFu16.to_le_bytes());
        let mut loc = b"PK\x06\x07".to_vec();
        loc.extend_from_slice(&0u32.to_le_bytes());
        loc.extend_from_slice(&u64::MAX.to_le_bytes());
        loc.extend_from_slice(&1u32.to_le_bytes());
        z.splice(eocd..eocd, loc);
        let k = read_zip(&z).unwrap_err().kind;
        assert!(k == Kind::Format || k == Kind::Unstable, "{k:?}");
    }

    #[test]
    fn two_entries_still_load() {
        let mut w = zip::ZipWriter::new(Cursor::new(Vec::new()));
        w.start_file("meta", zip::write::SimpleFileOptions::default())
            .unwrap();
        w.start_file("gamestate", zip::write::SimpleFileOptions::default())
            .unwrap();
        w.write_all(b"a=1").unwrap();
        let z = w.finish().unwrap().into_inner();
        assert_eq!(zip_entry_count(&z), Some(2));
        assert!(read_zip(&z).is_ok());
    }
}
