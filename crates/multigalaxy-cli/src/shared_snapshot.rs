//! Versioned, fixed-size atomic snapshot format for a future Dolphin source bridge.
//! This transports bytes only; it does not access emulated memory.

use std::array;
use std::sync::atomic::{AtomicU32, AtomicU64, AtomicU8, Ordering};

pub const MAGIC: u64 = u64::from_le_bytes(*b"MG1BRDGE");
pub const ABI_VERSION: u32 = 1;
pub const MAX_PAYLOAD: usize = 128;
const MAX_READ_RETRIES: usize = 64;

#[repr(C, align(64))]
pub struct SharedSnapshot {
    magic: AtomicU64,
    version: AtomicU32,
    reserved: AtomicU32,
    sequence: AtomicU64,
    payload_len: AtomicU32,
    _padding: AtomicU32,
    payload: [AtomicU8; MAX_PAYLOAD],
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Snapshot {
    pub sequence: u64,
    pub payload: Vec<u8>,
}

impl SharedSnapshot {
    pub fn new() -> Self {
        Self {
            magic: AtomicU64::new(0),
            version: AtomicU32::new(0),
            reserved: AtomicU32::new(0),
            sequence: AtomicU64::new(0),
            payload_len: AtomicU32::new(0),
            _padding: AtomicU32::new(0),
            payload: array::from_fn(|_| AtomicU8::new(0)),
        }
    }

    /// Initialize once, immediately after creating a new shared mapping.
    pub fn initialize(&self) {
        self.version.store(ABI_VERSION, Ordering::SeqCst);
        self.reserved.store(0, Ordering::SeqCst);
        self.sequence.store(0, Ordering::SeqCst);
        self.payload_len.store(0, Ordering::SeqCst);
        self.magic.store(MAGIC, Ordering::SeqCst);
    }

    pub fn validate(&self) -> Result<(), String> {
        let magic = self.magic.load(Ordering::SeqCst);
        if magic != MAGIC {
            return Err(format!("shared snapshot magic mismatch: {magic:#x}"));
        }
        let version = self.version.load(Ordering::SeqCst);
        if version != ABI_VERSION {
            return Err(format!(
                "unsupported shared snapshot ABI {version}; expected {ABI_VERSION}"
            ));
        }
        Ok(())
    }

    /// Publish one bounded payload. Concurrent/multiple publishers are rejected.
    pub fn publish(&self, payload: &[u8]) -> Result<u64, String> {
        self.validate()?;
        if payload.len() > MAX_PAYLOAD {
            return Err(format!(
                "snapshot payload {} exceeds {} byte limit",
                payload.len(),
                MAX_PAYLOAD
            ));
        }
        let even_sequence = self.sequence.load(Ordering::SeqCst);
        if even_sequence & 1 != 0 {
            return Err("another snapshot publisher is active".into());
        }
        self.sequence
            .compare_exchange(
                even_sequence,
                even_sequence.wrapping_add(1),
                Ordering::SeqCst,
                Ordering::Relaxed,
            )
            .map_err(|_| "another snapshot publisher is active".to_owned())?;
        self.payload_len
            .store(payload.len() as u32, Ordering::SeqCst);
        for (target, value) in self.payload.iter().zip(payload.iter().copied()) {
            target.store(value, Ordering::SeqCst);
        }
        let published_sequence = even_sequence.wrapping_add(2);
        self.sequence.store(published_sequence, Ordering::SeqCst);
        Ok(published_sequence)
    }

    /// Read a consistent snapshot without locking or waiting for the writer.
    pub fn read(&self) -> Result<Option<Snapshot>, String> {
        self.validate()?;
        for _ in 0..MAX_READ_RETRIES {
            let before = self.sequence.load(Ordering::SeqCst);
            if before & 1 != 0 {
                std::hint::spin_loop();
                continue;
            }
            let length = self.payload_len.load(Ordering::SeqCst) as usize;
            if length > MAX_PAYLOAD {
                return Err(format!("invalid shared snapshot payload length {length}"));
            }
            let payload = self.payload[..length]
                .iter()
                .map(|byte| byte.load(Ordering::SeqCst))
                .collect();
            let after = self.sequence.load(Ordering::SeqCst);
            if before == after && after & 1 == 0 {
                return Ok(Some(Snapshot {
                    sequence: after,
                    payload,
                }));
            }
        }
        Ok(None)
    }
}

impl Default for SharedSnapshot {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(windows)]
pub mod windows_mapping {
    use super::SharedSnapshot;
    use std::ffi::c_void;
    use std::mem::size_of;
    use std::ptr::NonNull;

    type Handle = isize;

    #[link(name = "kernel32")]
    extern "system" {
        fn CreateFileMappingW(
            file: Handle,
            attributes: *mut c_void,
            protection: u32,
            maximum_size_high: u32,
            maximum_size_low: u32,
            name: *const u16,
        ) -> Handle;
        fn CreateMutexW(attributes: *mut c_void, initial_owner: i32, name: *const u16) -> Handle;
        fn WaitForSingleObject(handle: Handle, milliseconds: u32) -> u32;
        fn ReleaseMutex(handle: Handle) -> i32;
        fn OpenFileMappingW(access: u32, inherit: i32, name: *const u16) -> Handle;
        fn MapViewOfFile(
            mapping: Handle,
            access: u32,
            offset_high: u32,
            offset_low: u32,
            bytes: usize,
        ) -> *mut c_void;
        fn UnmapViewOfFile(address: *const c_void) -> i32;
        fn CloseHandle(handle: Handle) -> i32;
        fn GetLastError() -> u32;
    }

    pub struct Mapping {
        handle: Handle,
        view: NonNull<SharedSnapshot>,
    }

    struct InitializationLock(Handle);

    impl InitializationLock {
        fn acquire(name: &str) -> Result<Self, String> {
            let mutex_name: Vec<u16> = format!("Local\\MultiGalaxy1-{name}-init")
                .encode_utf16()
                .chain(std::iter::once(0))
                .collect();
            // SAFETY: mutex_name is a nul-terminated string and no security
            // descriptor is supplied, so Windows uses the current user's ACL.
            let handle = unsafe { CreateMutexW(std::ptr::null_mut(), 0, mutex_name.as_ptr()) };
            if handle == 0 {
                return Err(format!(
                    "CreateMutexW failed with Windows error {}",
                    std::io::Error::last_os_error()
                ));
            }
            // INFINITE wait. WAIT_OBJECT_0 and WAIT_ABANDONED both grant ownership.
            // SAFETY: handle is a valid mutex handle returned above.
            let wait = unsafe { WaitForSingleObject(handle, 0xffff_ffff) };
            if wait != 0 && wait != 0x80 {
                // SAFETY: handle is owned by this initializer.
                unsafe { CloseHandle(handle) };
                return Err(format!(
                    "WaitForSingleObject on mapping initializer failed ({wait:#x})"
                ));
            }
            Ok(Self(handle))
        }
    }

    impl Drop for InitializationLock {
        fn drop(&mut self) {
            // SAFETY: this lock owns the mutex until ReleaseMutex; then it owns
            // and closes the corresponding kernel handle.
            unsafe {
                ReleaseMutex(self.0);
                CloseHandle(self.0);
            }
        }
    }

    impl Mapping {
        pub fn create_or_open(name: &str) -> Result<(Self, bool), String> {
            if name.is_empty() || name.contains('\\') || name.contains('/') {
                return Err("mapping suffix must be a non-empty simple name".into());
            }
            let object_name: Vec<u16> = format!("Local\\MultiGalaxy1-{name}")
                .encode_utf16()
                .chain(std::iter::once(0))
                .collect();
            let _lock = InitializationLock::acquire(name)?;
            let size = size_of::<SharedSnapshot>();
            // INVALID_HANDLE_VALUE + PAGE_READWRITE creates a pagefile-backed local mapping.
            // SAFETY: inputs reference valid memory for the duration of the call.
            let handle = unsafe {
                CreateFileMappingW(
                    -1,
                    std::ptr::null_mut(),
                    0x04,
                    (size as u64 >> 32) as u32,
                    size as u32,
                    object_name.as_ptr(),
                )
            };
            if handle == 0 {
                return Err(format!(
                    "CreateFileMappingW failed with Windows error {}",
                    std::io::Error::last_os_error()
                ));
            }
            // ERROR_ALREADY_EXISTS
            // SAFETY: GetLastError has no arguments and reads the last Win32 status.
            let created = unsafe { GetLastError() } != 183;
            let mapped = Self::map_handle(handle, size, 0x000f001f);
            match mapped {
                Ok(mapping) => {
                    if created {
                        // SAFETY: this mutex serializes initialization with all
                        // other openers. The new pagefile-backed view is writable
                        // and has no live Rust object before this placement write.
                        unsafe { mapping.view.as_ptr().write(SharedSnapshot::new()) };
                        mapping.snapshot().initialize();
                    } else {
                        mapping.snapshot().validate()?;
                    }
                    Ok((mapping, created))
                }
                Err(error) => {
                    // SAFETY: handle is owned by this function on error.
                    unsafe { CloseHandle(handle) };
                    Err(error)
                }
            }
        }

        pub fn open_existing(name: &str) -> Result<Self, String> {
            if name.is_empty() || name.contains('\\') || name.contains('/') {
                return Err("mapping suffix must be a non-empty simple name".into());
            }
            let object_name: Vec<u16> = format!("Local\\MultiGalaxy1-{name}")
                .encode_utf16()
                .chain(std::iter::once(0))
                .collect();
            let _lock = InitializationLock::acquire(name)?;
            // FILE_MAP_ALL_ACCESS
            // SAFETY: object_name is a nul-terminated UTF-16 string.
            // FILE_MAP_READ: readers cannot modify the producer's mapping.
            let handle = unsafe { OpenFileMappingW(0x0004, 0, object_name.as_ptr()) };
            if handle == 0 {
                return Err(format!(
                    "OpenFileMappingW failed with Windows error {}",
                    std::io::Error::last_os_error()
                ));
            }
            match Self::map_handle(handle, size_of::<SharedSnapshot>(), 0x0004) {
                Ok(mapping) => match mapping.snapshot().validate() {
                    Ok(()) => Ok(mapping),
                    Err(error) => {
                        drop(mapping);
                        Err(error)
                    }
                },
                Err(error) => {
                    // SAFETY: handle is owned by this function on error.
                    unsafe { CloseHandle(handle) };
                    Err(error)
                }
            }
        }

        fn map_handle(handle: Handle, size: usize, access: u32) -> Result<Self, String> {
            // SAFETY: handle is an owned mapping handle and size is the exact ABI size.
            let address = unsafe { MapViewOfFile(handle, access, 0, 0, size) };
            let view = NonNull::new(address.cast::<SharedSnapshot>()).ok_or_else(|| {
                format!(
                    "MapViewOfFile failed with Windows error {}",
                    std::io::Error::last_os_error()
                )
            })?;
            Ok(Self { handle, view })
        }

        pub fn snapshot(&self) -> &SharedSnapshot {
            // SAFETY: MapViewOfFile returns a view valid until Drop; SharedSnapshot is
            // initialized before consumers open the named mapping.
            unsafe { self.view.as_ref() }
        }
    }

    impl Drop for Mapping {
        fn drop(&mut self) {
            // SAFETY: view and handle are owned by this Mapping.
            unsafe {
                UnmapViewOfFile(self.view.as_ptr().cast::<c_void>());
                CloseHandle(self.handle);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{SharedSnapshot, MAX_PAYLOAD};
    use std::sync::Arc;

    #[test]
    fn reads_only_initialized_versioned_snapshots_and_bounds_payloads() {
        assert_eq!(std::mem::size_of::<SharedSnapshot>(), 192);
        assert_eq!(std::mem::align_of::<SharedSnapshot>(), 64);
        let uninit = std::mem::MaybeUninit::<SharedSnapshot>::uninit();
        let base = uninit.as_ptr() as usize;
        // SAFETY: addr_of! computes field addresses without dereferencing the
        // uninitialized SharedSnapshot value.
        unsafe {
            assert_eq!(
                std::ptr::addr_of!((*uninit.as_ptr()).magic) as usize - base,
                0
            );
            assert_eq!(
                std::ptr::addr_of!((*uninit.as_ptr()).version) as usize - base,
                8
            );
            assert_eq!(
                std::ptr::addr_of!((*uninit.as_ptr()).reserved) as usize - base,
                12
            );
            assert_eq!(
                std::ptr::addr_of!((*uninit.as_ptr()).sequence) as usize - base,
                16
            );
            assert_eq!(
                std::ptr::addr_of!((*uninit.as_ptr()).payload_len) as usize - base,
                24
            );
            assert_eq!(
                std::ptr::addr_of!((*uninit.as_ptr())._padding) as usize - base,
                28
            );
            assert_eq!(
                std::ptr::addr_of!((*uninit.as_ptr()).payload) as usize - base,
                32
            );
        }
        let snapshot = SharedSnapshot::new();
        assert!(snapshot.validate().is_err());
        snapshot.initialize();
        assert_eq!(snapshot.read().unwrap().unwrap().payload, Vec::<u8>::new());
        let bytes = [0x5au8; MAX_PAYLOAD];
        assert_eq!(snapshot.publish(&bytes).unwrap(), 2);
        assert_eq!(snapshot.read().unwrap().unwrap().payload, bytes);
        assert!(snapshot.publish(&[0; MAX_PAYLOAD + 1]).is_err());
    }

    #[test]
    fn reader_rejects_torn_snapshots_while_writer_publishes() {
        let slot = Arc::new(SharedSnapshot::new());
        slot.initialize();
        let writer = Arc::clone(&slot);
        let thread = std::thread::spawn(move || {
            for marker in 1..10_000u32 {
                let payload = [marker as u8; MAX_PAYLOAD];
                writer.publish(&payload).unwrap();
            }
        });
        while !thread.is_finished() {
            if let Some(snapshot) = slot.read().unwrap() {
                if let Some(first) = snapshot.payload.first() {
                    assert!(snapshot.payload.iter().all(|byte| byte == first));
                }
            }
        }
        thread.join().unwrap();
    }

    #[cfg(windows)]
    #[test]
    fn two_windows_mapping_views_share_snapshot_state() {
        use super::windows_mapping::Mapping;
        let name = format!(
            "test-{}-{}",
            std::process::id(),
            std::thread::current().name().unwrap_or("unnamed")
        );
        let (writer, _created) = Mapping::create_or_open(&name).unwrap();
        let reader = Mapping::open_existing(&name).unwrap();
        let data = b"read-only bridge proto";
        writer.snapshot().publish(data).unwrap();
        assert_eq!(reader.snapshot().read().unwrap().unwrap().payload, data);
    }
}
