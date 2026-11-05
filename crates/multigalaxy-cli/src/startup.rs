//! Read-only Windows window checks used to bound Dolphin startup diagnostics.

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WindowState {
    /// Native top-level window handle. Zero only when this platform cannot
    /// inspect native windows.
    pub hwnd: isize,
    pub thread_id: u32,
    pub title: String,
    pub visible: bool,
    pub hung: bool,
    pub style: usize,
    pub extended_style: usize,
    pub parent: isize,
    pub owner: isize,
    pub desktop: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DesktopContext {
    pub pid: u32,
    pub session_id: u32,
    pub active_console_session_id: u32,
    pub user_name: String,
    pub station: String,
    pub desktop: String,
    pub input_desktop: String,
}

impl DesktopContext {
    pub fn is_interactive_default(&self) -> bool {
        self.session_id == self.active_console_session_id
            && self.station == "WinSta0"
            && self.desktop == "Default"
            && self.input_desktop == "Default"
    }
}

#[cfg(windows)]
mod win {
    use super::{DesktopContext, WindowState};
    use std::ffi::c_void;

    type Hwnd = isize;
    type Bool = i32;
    type Lparam = isize;
    const UOI_NAME: i32 = 2;

    #[link(name = "user32")]
    extern "system" {
        fn EnumWindows(
            callback: Option<unsafe extern "system" fn(Hwnd, Lparam) -> Bool>,
            data: Lparam,
        ) -> Bool;
        fn GetWindowThreadProcessId(hwnd: Hwnd, process_id: *mut u32) -> u32;
        fn IsWindowVisible(hwnd: Hwnd) -> Bool;
        fn IsWindow(hwnd: Hwnd) -> Bool;
        fn IsHungAppWindow(hwnd: Hwnd) -> Bool;
        fn IsIconic(hwnd: Hwnd) -> Bool;
        fn ShowWindow(hwnd: Hwnd, command: i32) -> Bool;
        fn SetForegroundWindow(hwnd: Hwnd) -> Bool;
        fn GetWindowTextW(hwnd: Hwnd, text: *mut u16, max_count: i32) -> i32;
        fn GetParent(hwnd: Hwnd) -> Hwnd;
        fn GetWindow(hwnd: Hwnd, command: u32) -> Hwnd;
        fn GetWindowLongPtrW(hwnd: Hwnd, index: i32) -> isize;
        fn GetThreadDesktop(thread_id: u32) -> Hwnd;
        fn GetCurrentThreadId() -> u32;
        fn GetCurrentProcessId() -> u32;
        fn GetProcessWindowStation() -> Hwnd;
        fn GetUserObjectInformationW(
            handle: Hwnd,
            index: i32,
            info: *mut u16,
            length: u32,
            needed: *mut u32,
        ) -> Bool;
        fn OpenInputDesktop(flags: u32, inherit: Bool, access: u32) -> Hwnd;
        fn CloseDesktop(desktop: Hwnd) -> Bool;
    }

    #[link(name = "kernel32")]
    extern "system" {
        fn ProcessIdToSessionId(process_id: u32, session_id: *mut u32) -> Bool;
        fn OpenProcess(access: u32, inherit: Bool, process_id: u32) -> Hwnd;
        fn QueryFullProcessImageNameW(
            process: Hwnd,
            flags: u32,
            name: *mut u16,
            size: *mut u32,
        ) -> Bool;
        fn CloseHandle(handle: Hwnd) -> Bool;
    }

    #[link(name = "wtsapi32")]
    extern "system" {
        fn WTSGetActiveConsoleSessionId() -> u32;
        fn WTSQuerySessionInformationW(
            server: Hwnd,
            session_id: u32,
            information_class: i32,
            buffer: *mut Hwnd,
            bytes_returned: *mut u32,
        ) -> Bool;
        fn WTSFreeMemory(buffer: Hwnd);
    }

    fn object_name(handle: Hwnd) -> String {
        if handle == 0 {
            return "<unavailable>".into();
        }
        let mut name = [0u16; 512];
        let mut needed = 0;
        // SAFETY: name is writable for its declared byte length; handle was
        // obtained from the process/thread desktop APIs above.
        let ok = unsafe {
            GetUserObjectInformationW(
                handle,
                UOI_NAME,
                name.as_mut_ptr(),
                (name.len() * std::mem::size_of::<u16>()) as u32,
                &mut needed,
            )
        };
        if ok == 0 {
            return format!("<error:{}>", std::io::Error::last_os_error());
        }
        let length = name
            .iter()
            .position(|unit| *unit == 0)
            .unwrap_or(name.len());
        String::from_utf16_lossy(&name[..length])
    }

    fn wts_string(session_id: u32, information_class: i32) -> String {
        let mut buffer = 0;
        let mut bytes = 0;
        // SAFETY: WTS allocates the returned buffer and we release it below.
        let ok = unsafe {
            WTSQuerySessionInformationW(0, session_id, information_class, &mut buffer, &mut bytes)
        };
        if ok == 0 || buffer == 0 {
            return format!("<error:{}>", std::io::Error::last_os_error());
        }
        let units = (bytes as usize) / std::mem::size_of::<u16>();
        // SAFETY: WTS returned a valid UTF-16 buffer of `bytes` bytes.
        let value = unsafe { std::slice::from_raw_parts(buffer as *const u16, units) };
        let length = value.iter().position(|unit| *unit == 0).unwrap_or(units);
        let text = String::from_utf16_lossy(&value[..length]);
        // SAFETY: `buffer` is the allocation returned by WTSQuerySessionInformationW.
        unsafe { WTSFreeMemory(buffer) };
        text
    }

    pub fn desktop_context() -> DesktopContext {
        // SAFETY: these calls query the current process/thread and active WTS
        // console session. OpenInputDesktop is closed immediately after query.
        unsafe {
            let pid = GetCurrentProcessId();
            let mut session_id = u32::MAX;
            let session_ok = ProcessIdToSessionId(pid, &mut session_id) != 0;
            let session_id = if session_ok { session_id } else { u32::MAX };
            let active_console_session_id = WTSGetActiveConsoleSessionId();
            let station = object_name(GetProcessWindowStation());
            let desktop = object_name(GetThreadDesktop(GetCurrentThreadId()));
            let input = OpenInputDesktop(0, 0, 1); // DESKTOP_READOBJECTS
            let input_desktop = object_name(input);
            if input != 0 {
                CloseDesktop(input);
            }
            DesktopContext {
                pid,
                session_id,
                active_console_session_id,
                user_name: wts_string(session_id, 5), // WTSUserName
                station,
                desktop,
                input_desktop,
            }
        }
    }

    pub fn process_session_id(pid: u32) -> Option<u32> {
        let mut session_id = 0;
        // SAFETY: session_id points to writable storage for the API result.
        let ok = unsafe { ProcessIdToSessionId(pid, &mut session_id) };
        (ok != 0).then_some(session_id)
    }

    pub fn process_image_path(pid: u32) -> Result<std::path::PathBuf, String> {
        const PROCESS_QUERY_LIMITED_INFORMATION: u32 = 0x1000;
        // SAFETY: this opens the process with query-only rights and closes the
        // handle after obtaining its full image name.
        let process = unsafe { OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid) };
        if process == 0 {
            return Err(format!(
                "cannot query PID {pid}: {}",
                std::io::Error::last_os_error()
            ));
        }
        let mut buffer = vec![0u16; 32768];
        let mut length = buffer.len() as u32;
        // SAFETY: `buffer` is writable for `length` UTF-16 units.
        let ok =
            unsafe { QueryFullProcessImageNameW(process, 0, buffer.as_mut_ptr(), &mut length) };
        let error = (ok == 0).then(|| std::io::Error::last_os_error().to_string());
        // SAFETY: `process` is the handle returned by OpenProcess above.
        unsafe { CloseHandle(process) };
        if let Some(error) = error {
            return Err(format!("cannot query image path for PID {pid}: {error}"));
        }
        Ok(std::path::PathBuf::from(String::from_utf16_lossy(
            &buffer[..length as usize],
        )))
    }

    struct Query {
        pid: u32,
        windows: Vec<WindowState>,
    }

    // SAFETY: EnumWindows calls this synchronously on the same thread with the
    // live Query pointer passed by `visible_windows`; the pointer is valid for
    // the complete call and the callback does not retain it.
    unsafe extern "system" fn collect(hwnd: Hwnd, data: Lparam) -> Bool {
        let query = &mut *(data as *mut Query);
        let mut owner = 0;
        let thread_id = GetWindowThreadProcessId(hwnd, &mut owner);
        if owner != query.pid {
            return 1;
        }
        let mut title = [0u16; 512];
        let length = GetWindowTextW(hwnd, title.as_mut_ptr(), title.len() as i32);
        let desktop = object_name(GetThreadDesktop(thread_id));
        query.windows.push(WindowState {
            hwnd,
            thread_id,
            title: String::from_utf16_lossy(&title[..length.max(0) as usize]),
            visible: IsWindowVisible(hwnd) != 0,
            hung: IsHungAppWindow(hwnd) != 0,
            style: GetWindowLongPtrW(hwnd, -16) as usize,
            extended_style: GetWindowLongPtrW(hwnd, -20) as usize,
            parent: GetParent(hwnd),
            owner: GetWindow(hwnd, 4), // GW_OWNER
            desktop,
        });
        1
    }

    pub fn process_windows(pid: u32) -> Vec<WindowState> {
        let mut query = Query {
            pid,
            windows: Vec::new(),
        };
        // SAFETY: `collect` only dereferences the valid stack pointer above;
        // EnumWindows is synchronous and does not retain callback data.
        unsafe {
            EnumWindows(
                Some(collect),
                (&mut query as *mut Query) as *mut c_void as Lparam,
            );
        }
        query.windows
    }

    pub fn visible_windows(pid: u32) -> Vec<WindowState> {
        process_windows(pid)
            .into_iter()
            .filter(|window| window.visible)
            .collect()
    }

    pub fn activate(hwnd: isize) -> bool {
        // SAFETY: HWND came from EnumWindows and is revalidated before use.
        unsafe {
            if IsWindow(hwnd) == 0 {
                return false;
            }
            if IsIconic(hwnd) != 0 {
                ShowWindow(hwnd, 9); // SW_RESTORE
            }
            SetForegroundWindow(hwnd) != 0 && IsWindowVisible(hwnd) != 0
        }
    }

    pub fn is_valid_visible_for_pid(hwnd: isize, pid: u32) -> bool {
        // SAFETY: all APIs accept an opaque HWND and only query its state.
        unsafe {
            if hwnd == 0 || IsWindow(hwnd) == 0 || IsWindowVisible(hwnd) == 0 {
                return false;
            }
            let mut owner = 0;
            GetWindowThreadProcessId(hwnd, &mut owner);
            owner == pid
        }
    }
}

#[cfg(windows)]
pub fn visible_windows(pid: u32) -> Vec<WindowState> {
    win::visible_windows(pid)
}

#[cfg(windows)]
pub fn process_windows(pid: u32) -> Vec<WindowState> {
    win::process_windows(pid)
}

#[cfg(windows)]
pub fn desktop_context() -> Option<DesktopContext> {
    Some(win::desktop_context())
}

#[cfg(windows)]
pub fn process_session_id(pid: u32) -> Option<u32> {
    win::process_session_id(pid)
}

#[cfg(windows)]
pub fn process_image_path(pid: u32) -> Result<std::path::PathBuf, String> {
    win::process_image_path(pid)
}

#[cfg(test)]
mod context_tests {
    use super::DesktopContext;

    fn context(desktop: &str, input_desktop: &str, session: u32) -> DesktopContext {
        DesktopContext {
            pid: 10,
            session_id: session,
            active_console_session_id: 1,
            user_name: "test-user".into(),
            station: "WinSta0".into(),
            desktop: desktop.into(),
            input_desktop: input_desktop.into(),
        }
    }

    #[test]
    fn only_default_desktop_in_active_console_session_is_interactive() {
        assert!(context("Default", "Default", 1).is_interactive_default());
        assert!(!context("CodexSandboxDesktop-test", "Default", 1).is_interactive_default());
        assert!(!context("Default", "Winlogon", 1).is_interactive_default());
        assert!(!context("Default", "Default", 0).is_interactive_default());
    }
}

#[cfg(not(windows))]
pub fn visible_windows(_pid: u32) -> Vec<WindowState> {
    Vec::new()
}

#[cfg(not(windows))]
pub fn process_windows(_pid: u32) -> Vec<WindowState> {
    Vec::new()
}

#[cfg(not(windows))]
pub fn desktop_context() -> Option<DesktopContext> {
    None
}

#[cfg(not(windows))]
pub fn process_session_id(_pid: u32) -> Option<u32> {
    None
}

#[cfg(not(windows))]
pub fn process_image_path(_pid: u32) -> Result<std::path::PathBuf, String> {
    Err("process image path validation is currently Windows-only".into())
}

#[cfg(not(windows))]
pub fn window_check_supported() -> bool {
    false
}

#[cfg(windows)]
pub fn window_check_supported() -> bool {
    true
}

#[cfg(windows)]
pub fn activate(hwnd: isize) -> bool {
    win::activate(hwnd)
}

#[cfg(windows)]
pub fn is_valid_visible_for_pid(hwnd: isize, pid: u32) -> bool {
    win::is_valid_visible_for_pid(hwnd, pid)
}

#[cfg(not(windows))]
pub fn activate(_hwnd: isize) -> bool {
    false
}

#[cfg(not(windows))]
pub fn is_valid_visible_for_pid(_hwnd: isize, _pid: u32) -> bool {
    false
}

#[cfg(windows)]
#[cfg(test)]
mod tests {
    use super::*;
    use std::ffi::c_void;

    #[link(name = "user32")]
    extern "system" {
        fn CreateWindowExW(
            ex_style: u32,
            class: *const u16,
            title: *const u16,
            style: u32,
            x: i32,
            y: i32,
            width: i32,
            height: i32,
            parent: isize,
            menu: isize,
            instance: *mut c_void,
            parameter: *mut c_void,
        ) -> isize;
        fn DestroyWindow(hwnd: isize) -> i32;
    }

    #[test]
    fn enumerates_a_real_visible_window_handle_owned_by_the_process() {
        let class: Vec<u16> = "STATIC\0".encode_utf16().collect();
        let title: Vec<u16> = "MultiGalaxy1 window regression fixture\0"
            .encode_utf16()
            .collect();
        // SAFETY: STATIC is a built-in Win32 window class; the returned HWND is
        // destroyed at the end of this test and uses no external pointers.
        let hwnd = unsafe {
            CreateWindowExW(
                0,
                class.as_ptr(),
                title.as_ptr(),
                0x9000_0000, // WS_VISIBLE | WS_POPUP
                20,
                20,
                160,
                80,
                0,
                0,
                std::ptr::null_mut(),
                std::ptr::null_mut(),
            )
        };
        assert_ne!(hwnd, 0, "Windows must create the visible test window");
        let windows = visible_windows(std::process::id());
        assert!(windows.iter().any(|window| {
            window.hwnd == hwnd
                && !window.hung
                && is_valid_visible_for_pid(window.hwnd, std::process::id())
        }));
        // SAFETY: this test owns the temporary test HWND.
        unsafe { DestroyWindow(hwnd) };
    }

    #[test]
    fn reports_current_wts_session_and_desktop_context() {
        let context = desktop_context().expect("Windows desktop APIs should be available");
        assert_eq!(context.pid, std::process::id());
        assert_eq!(context.session_id, process_session_id(context.pid).unwrap());
        assert_ne!(context.active_console_session_id, u32::MAX);
        assert!(!context.user_name.is_empty());
        assert!(!context.station.is_empty());
        assert!(!context.desktop.is_empty());
        assert!(!context.input_desktop.is_empty());
    }
}
