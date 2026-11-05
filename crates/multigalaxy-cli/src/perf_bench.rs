//! Read-only Windows process CPU sampling for the local Dolphin performance matrix.

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct CpuSample {
    pub elapsed_seconds: f64,
    /// Process CPU as a percentage of total machine capacity, matching the
    /// Task Manager scale where all logical processors together equal 100%.
    pub total_cpu_percent: f64,
}

pub fn valid_condition(value: &str) -> bool {
    matches!(
        value,
        "normal" | "gdb-unattached" | "gdb-idle" | "gdb-sampling"
    )
}

pub fn cpu_percent(cpu_delta_100ns: u64, wall_delta_100ns: u64, processors: u32) -> f64 {
    if wall_delta_100ns == 0 || processors == 0 {
        return 0.0;
    }
    (cpu_delta_100ns as f64 * 100.0) / (wall_delta_100ns as f64 * processors as f64)
}

pub fn is_dolphin_executable(path: &std::path::Path) -> bool {
    path.file_name()
        .map(|name| name.to_string_lossy().eq_ignore_ascii_case("Dolphin.exe"))
        .unwrap_or(false)
}

#[cfg(windows)]
mod windows {
    use super::CpuSample;
    use std::io;
    use std::time::{Duration, Instant};

    type Handle = isize;

    #[repr(C)]
    #[derive(Default)]
    struct FileTime {
        low: u32,
        high: u32,
    }

    impl FileTime {
        fn ticks(&self) -> u64 {
            ((self.high as u64) << 32) | self.low as u64
        }
    }

    #[link(name = "kernel32")]
    extern "system" {
        fn OpenProcess(access: u32, inherit: i32, process_id: u32) -> Handle;
        fn GetProcessTimes(
            process: Handle,
            creation: *mut FileTime,
            exit: *mut FileTime,
            kernel: *mut FileTime,
            user: *mut FileTime,
        ) -> i32;
        fn QueryFullProcessImageNameW(
            process: Handle,
            flags: u32,
            image_name: *mut u16,
            size: *mut u32,
        ) -> i32;
        fn GetActiveProcessorCount(group_number: u16) -> u32;
        fn CloseHandle(handle: Handle) -> i32;
    }

    struct ProcessHandle(Handle);

    impl Drop for ProcessHandle {
        fn drop(&mut self) {
            // SAFETY: this is the owned handle returned by OpenProcess.
            unsafe { CloseHandle(self.0) };
        }
    }

    fn cpu_ticks(process: Handle) -> Result<u64, String> {
        let mut creation = FileTime::default();
        let mut exit = FileTime::default();
        let mut kernel = FileTime::default();
        let mut user = FileTime::default();
        // SAFETY: each pointer refers to a writable FILETIME value.
        let ok =
            unsafe { GetProcessTimes(process, &mut creation, &mut exit, &mut kernel, &mut user) };
        if ok == 0 {
            return Err(format!(
                "GetProcessTimes failed: {}",
                io::Error::last_os_error()
            ));
        }
        Ok(kernel.ticks() + user.ticks())
    }

    fn process_image_path(process: Handle) -> Result<std::path::PathBuf, String> {
        let mut image = vec![0u16; 32768];
        let mut length = image.len() as u32;
        // SAFETY: image points to a writable UTF-16 buffer of `length` units.
        let ok = unsafe { QueryFullProcessImageNameW(process, 0, image.as_mut_ptr(), &mut length) };
        if ok == 0 {
            return Err(format!(
                "QueryFullProcessImageNameW failed: {}",
                io::Error::last_os_error()
            ));
        }
        image.truncate(length as usize);
        Ok(std::path::PathBuf::from(String::from_utf16_lossy(&image)))
    }

    pub fn sample(pid: u32, seconds: u64) -> Result<Vec<CpuSample>, String> {
        if !(1..=600).contains(&seconds) {
            return Err("benchmark duration must be 1..=600 seconds".into());
        }
        // PROCESS_QUERY_LIMITED_INFORMATION
        // SAFETY: OpenProcess only obtains query access; no process state is modified.
        let handle = unsafe { OpenProcess(0x1000, 0, pid) };
        if handle == 0 {
            return Err(format!(
                "cannot open Dolphin PID {pid} for read-only CPU statistics: {}",
                io::Error::last_os_error()
            ));
        }
        let process = ProcessHandle(handle);
        let image = process_image_path(process.0)?;
        if !super::is_dolphin_executable(&image) {
            return Err(format!(
                "PID {pid} is {}, not Dolphin.exe; refusing to label it as a Dolphin benchmark",
                image.display()
            ));
        }
        println!("benchmark target executable={}", image.display());
        // ALL_PROCESSOR_GROUPS = 0xffff
        // SAFETY: this queries the host processor count.
        let processors = unsafe { GetActiveProcessorCount(0xffff) };
        if processors == 0 {
            return Err(format!(
                "GetActiveProcessorCount failed: {}",
                io::Error::last_os_error()
            ));
        }
        let mut previous_cpu = cpu_ticks(process.0)?;
        let mut previous_wall = Instant::now();
        let start = previous_wall;
        let mut samples = Vec::with_capacity(seconds as usize);
        for _ in 0..seconds {
            let target = previous_wall + Duration::from_secs(1);
            std::thread::sleep(target.saturating_duration_since(Instant::now()));
            let current_cpu = cpu_ticks(process.0)?;
            let current_wall = Instant::now();
            let wall = current_wall.duration_since(previous_wall);
            let wall_100ns = wall.as_nanos() / 100;
            let sample = CpuSample {
                elapsed_seconds: current_wall.duration_since(start).as_secs_f64(),
                total_cpu_percent: super::cpu_percent(
                    current_cpu.saturating_sub(previous_cpu),
                    wall_100ns.min(u64::MAX as u128) as u64,
                    processors,
                ),
            };
            samples.push(sample);
            println!(
                "cpu_sample elapsed_s={:.2} total_cpu_pct={:.2}",
                sample.elapsed_seconds, sample.total_cpu_percent
            );
            previous_cpu = current_cpu;
            previous_wall = current_wall;
        }
        Ok(samples)
    }
}

#[cfg(windows)]
pub fn sample(pid: u32, seconds: u64) -> Result<Vec<CpuSample>, String> {
    windows::sample(pid, seconds)
}

#[cfg(not(windows))]
pub fn sample(_pid: u32, _seconds: u64) -> Result<Vec<CpuSample>, String> {
    Err("bench-dolphin currently requires Windows process statistics".into())
}

#[cfg(test)]
mod tests {
    use super::{cpu_percent, is_dolphin_executable, valid_condition};
    use std::path::Path;

    #[test]
    fn reports_cpu_as_percent_of_total_machine_capacity() {
        assert!((cpu_percent(10_000_000, 10_000_000, 8) - 12.5).abs() < f64::EPSILON);
        assert!((cpu_percent(10_000_000, 10_000_000, 1) - 100.0).abs() < f64::EPSILON);
        assert_eq!(cpu_percent(10, 0, 8), 0.0);
        assert_eq!(cpu_percent(10, 10, 0), 0.0);
    }

    #[test]
    fn accepts_only_dolphin_executable_names() {
        assert!(is_dolphin_executable(Path::new(r"C:\Games\Dolphin.exe")));
        assert!(!is_dolphin_executable(Path::new(
            r"C:\Games\powershell.exe"
        )));
    }

    #[test]
    fn accepts_stock_dolphin_benchmark_conditions_and_rejects_retired_modes() {
        assert!(valid_condition("normal"));
        assert!(valid_condition("gdb-idle"));
        assert!(!valid_condition("bridge-enabled"));
        assert!(!valid_condition("bridge-disabled"));
        assert!(!valid_condition("production"));
    }
}
