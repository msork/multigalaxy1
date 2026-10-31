//! Read-only Windows window checks used to bound Dolphin startup diagnostics.

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WindowState {
    /// Native top-level window handle. Zero only when this platform cannot
    /// inspect native windows.
    pub hwnd: isize,
    pub title: String,
    pub hung: bool,
}

#[cfg(windows)]
mod win {
    use super::WindowState;
    use std::ffi::c_void;

    type Hwnd = isize;
    type Bool = i32;
    type Lparam = isize;

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
        if IsWindowVisible(hwnd) == 0 {
            return 1;
        }
        let mut owner = 0;
        GetWindowThreadProcessId(hwnd, &mut owner);
        if owner != query.pid {
            return 1;
        }
        let mut title = [0u16; 512];
        let length = GetWindowTextW(hwnd, title.as_mut_ptr(), title.len() as i32);
        query.windows.push(WindowState {
            hwnd,
            title: String::from_utf16_lossy(&title[..length.max(0) as usize]),
            hung: IsHungAppWindow(hwnd) != 0,
        });
        1
    }

    pub fn visible_windows(pid: u32) -> Vec<WindowState> {
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

#[cfg(not(windows))]
pub fn visible_windows(_pid: u32) -> Vec<WindowState> {
    Vec::new()
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
}
