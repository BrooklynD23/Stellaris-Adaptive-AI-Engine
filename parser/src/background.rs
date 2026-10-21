//! `--background`: Windows background priority class plus EcoQoS. No-op elsewhere.

#[cfg(windows)]
use crate::log::log;

#[cfg(windows)]
fn warn(msg: &str) {
    log("warn", msg);
}

// Windows path: not executed in CI on this machine (cargo check on windows-gnu only).
#[cfg(windows)]
pub fn enter() {
    use windows_sys::Win32::System::Threading::{
        GetCurrentProcess, ProcessPowerThrottling, SetPriorityClass, SetProcessInformation,
        PROCESS_MODE_BACKGROUND_BEGIN, PROCESS_POWER_THROTTLING_CURRENT_VERSION,
        PROCESS_POWER_THROTTLING_EXECUTION_SPEED, PROCESS_POWER_THROTTLING_STATE,
    };
    // SAFETY: FFI on the current-process pseudo handle with a correctly sized struct.
    unsafe {
        let h = GetCurrentProcess();
        if SetPriorityClass(h, PROCESS_MODE_BACKGROUND_BEGIN) == 0 {
            warn(&format!(
                "SetPriorityClass failed: {}",
                std::io::Error::last_os_error()
            ));
        }
        let state = PROCESS_POWER_THROTTLING_STATE {
            Version: PROCESS_POWER_THROTTLING_CURRENT_VERSION,
            ControlMask: PROCESS_POWER_THROTTLING_EXECUTION_SPEED,
            StateMask: PROCESS_POWER_THROTTLING_EXECUTION_SPEED,
        };
        let ok = SetProcessInformation(
            h,
            ProcessPowerThrottling,
            std::ptr::addr_of!(state).cast(),
            std::mem::size_of::<PROCESS_POWER_THROTTLING_STATE>() as u32,
        );
        if ok == 0 {
            warn(&format!(
                "EcoQoS failed: {}",
                std::io::Error::last_os_error()
            ));
        }
    }
}

/// Non-Windows: the spawner already applied SCHED_IDLE/ionice (C7 section 8).
#[cfg(not(windows))]
pub fn enter() {}
