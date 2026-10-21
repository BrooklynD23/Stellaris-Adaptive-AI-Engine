//! Self-measured cost: peak RSS, CPU seconds, wall seconds.

use std::time::Instant;

use crate::output::Cost;

/// Measure cost now; `start` is captured first thing in `main`.
pub fn measure(start: Instant) -> Cost {
    let (rss_peak_bytes, cpu_s) = process_usage();
    Cost {
        rss_peak_bytes,
        cpu_s,
        wall_s: start.elapsed().as_secs_f64(),
    }
}

#[cfg(unix)]
fn process_usage() -> (u64, f64) {
    // SAFETY: getrusage writes into a zeroed, correctly typed struct we own.
    let ru = unsafe {
        let mut ru: libc::rusage = std::mem::zeroed();
        if libc::getrusage(libc::RUSAGE_SELF, &mut ru) != 0 {
            return (0, 0.0);
        }
        ru
    };
    let maxrss = u64::try_from(ru.ru_maxrss).unwrap_or(0);
    // Linux reports KiB; macOS reports bytes.
    let rss = if cfg!(target_os = "macos") {
        maxrss
    } else {
        maxrss.saturating_mul(1024)
    };
    let tv = |t: libc::timeval| t.tv_sec as f64 + t.tv_usec as f64 / 1e6;
    (rss, (tv(ru.ru_utime) + tv(ru.ru_stime)).max(0.0))
}

// Windows path: not executed in CI on this machine (cargo check on windows-gnu only).
#[cfg(windows)]
fn process_usage() -> (u64, f64) {
    use windows_sys::Win32::Foundation::FILETIME;
    use windows_sys::Win32::System::ProcessStatus::{
        GetProcessMemoryInfo, PROCESS_MEMORY_COUNTERS,
    };
    use windows_sys::Win32::System::Threading::{GetCurrentProcess, GetProcessTimes};

    let ft = |f: FILETIME| {
        ((u64::from(f.dwHighDateTime) << 32) | u64::from(f.dwLowDateTime)) as f64 * 1e-7
    };
    // SAFETY: plain FFI calls with correctly sized out-structs owned here.
    unsafe {
        let h = GetCurrentProcess();
        let mut pmc: PROCESS_MEMORY_COUNTERS = std::mem::zeroed();
        pmc.cb = std::mem::size_of::<PROCESS_MEMORY_COUNTERS>() as u32;
        let rss = if GetProcessMemoryInfo(h, &mut pmc, pmc.cb) != 0 {
            pmc.PeakWorkingSetSize as u64
        } else {
            0
        };
        let mut c: FILETIME = std::mem::zeroed();
        let mut e: FILETIME = std::mem::zeroed();
        let mut k: FILETIME = std::mem::zeroed();
        let mut u: FILETIME = std::mem::zeroed();
        let cpu = if GetProcessTimes(h, &mut c, &mut e, &mut k, &mut u) != 0 {
            ft(k) + ft(u)
        } else {
            0.0
        };
        (rss, cpu)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn non_negative() {
        let c = measure(Instant::now());
        assert!(c.cpu_s >= 0.0 && c.wall_s >= 0.0);
    }
}
