//! Read-only Windows window checks used to bound Dolphin startup diagnostics.

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WindowState {
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
        fn IsHungAppWindow(hwnd: Hwnd) -> Bool;
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
