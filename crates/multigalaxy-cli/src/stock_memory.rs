//! Read-only Windows views into Dolphin 2609a's named emulated-memory section.
//!
//! Dolphin's stock Windows MemArenaWin names the page-file mapping
//! `dolphin-emu.<pid>`. Memmap.cpp assigns MEM1 the first shared-memory offset.
//! The persistent 64 KiB view remains for the existing mailbox bridge. Explicit
//! inspection requests use temporary, allocation-granularity-aligned read-only
//! views, capped to canonical retail MEM1 and 8 KiB per request.

use crate::gecko_bridge::{Mailbox, HANDLER_BASE, HANDLER_END};
use crate::memory_inspection::{
    read_explicit_range, ReadOnlyMappedMemory, GUEST_MEM1_MIN_SIZE, MAX_READ_BYTES,
    WINDOWS_ALLOCATION_GRANULARITY,
};
use std::ffi::{c_void, OsStr};
use std::os::windows::ffi::OsStrExt;
use std::ptr::{self, NonNull};

const FILE_MAP_WRITE: u32 = 0x0002;
const FILE_MAP_READ: u32 = 0x0004;
const VIEW_BYTES: usize = 0x1_0000;
const GUEST_BASE: u32 = 0x8000_0000;

#[derive(Debug)]
pub enum MappingOpenError {
    NotReady(String),
    Invalid(String),
}

#[link(name = "kernel32")]
extern "system" {
    fn OpenFileMappingW(access: u32, inherit: i32, name: *const u16) -> *mut c_void;
    fn MapViewOfFile(
        mapping: *mut c_void,
        access: u32,
        offset_high: u32,
        offset_low: u32,
        bytes: usize,
    ) -> *mut c_void;
    fn UnmapViewOfFile(address: *const c_void) -> i32;
    fn CloseHandle(handle: isize) -> i32;
}

pub struct StockMem1 {
    handle: NonNull<c_void>,
    view: NonNull<u8>,
    writable: bool,
}

impl StockMem1 {
    pub fn open_dolphin(pid: u32) -> Result<Self, MappingOpenError> {
        Self::open_name_access(&format!("dolphin-emu.{pid}"), true)
    }

    #[cfg(test)]
    fn open_name(name: &str) -> Result<Self, MappingOpenError> {
        Self::open_name_access(name, true)
    }

    pub fn open_dolphin_read_only(pid: u32) -> Result<Self, MappingOpenError> {
        let mapping = Self::open_name_access(&format!("dolphin-emu.{pid}"), false)?;
        mapping
            .validate_minimum_mem1()
            .map_err(MappingOpenError::Invalid)?;
        Ok(mapping)
    }

    fn open_name_access(name: &str, writable: bool) -> Result<Self, MappingOpenError> {
        let wide: Vec<u16> = OsStr::new(name).encode_wide().chain(Some(0)).collect();
        let access = if writable {
            FILE_MAP_READ | FILE_MAP_WRITE
        } else {
            FILE_MAP_READ
        };
        // SAFETY: `wide` is a nul-terminated UTF-16 name and remains alive for
        // the duration of the call. The requested access is limited to the
        // caller's same-session Dolphin page-file mapping.
        let raw_handle = unsafe { OpenFileMappingW(access, 0, wide.as_ptr()) };
        let handle = NonNull::new(raw_handle).ok_or_else(|| {
            let error = std::io::Error::last_os_error();
            let diagnostic = format!("cannot open stock Dolphin shared memory {name:?}: {error}");
            if error.raw_os_error() == Some(2) {
                MappingOpenError::NotReady(diagnostic)
            } else {
                MappingOpenError::Invalid(diagnostic)
            }
        })?;
        // A 64 KiB view covers the ID and Gecko handler region while remaining
        // far smaller than Dolphin's full shared memory mapping.
        // SAFETY: the section is Dolphin's MEM1-first mapping; `VIEW_BYTES` is
        // aligned to the Windows allocation granularity and the Dolphin 2609a
        // Wii section is substantially larger than this bounded view.
        let raw_view = unsafe { MapViewOfFile(handle.as_ptr(), access, 0, 0, VIEW_BYTES) };
        let view = match NonNull::new(raw_view.cast::<u8>()) {
            Some(view) => view,
            None => {
                let error = std::io::Error::last_os_error();
                // SAFETY: `handle` is an owned mapping handle returned above.
                unsafe { CloseHandle(handle.as_ptr() as isize) };
                return Err(MappingOpenError::Invalid(format!(
                    "cannot map Dolphin MEM1 view: {error}"
                )));
            }
        };
        Ok(Self {
            handle,
            view,
            writable,
        })
    }

    pub fn mapped_view_bytes(&self) -> usize {
        VIEW_BYTES
    }

    fn guest_offset(address: u32, length: usize) -> Result<usize, String> {
        if address < GUEST_BASE {
            return Err("stock bridge accepts only PAL MEM1 guest addresses".into());
        }
        let offset = (address - GUEST_BASE) as usize;
        if length == 0
            || offset
                .checked_add(length)
                .map_or(true, |end| end > VIEW_BYTES)
        {
            return Err("guest-memory request is outside the mapped 64 KiB MEM1 window".into());
        }
        Ok(offset)
    }

    pub fn read_guest(&self, address: u32, length: usize) -> Result<Vec<u8>, String> {
        read_explicit_range(self, address, length)
    }

    pub fn read_guest_word(&self, address: u32) -> Result<u32, String> {
        if address & 3 != 0 {
            return Err("guest word reads must be 4-byte aligned".into());
        }
        let bytes = self.read_guest(address, 4)?;
        Ok(u32::from_be_bytes(bytes.try_into().unwrap()))
    }

    /// Verify the named section reaches the end of the minimum retail MEM1
    /// region. This maps, but does not read, its last 64 KiB block.
    pub fn validate_minimum_mem1(&self) -> Result<(), String> {
        let offset = u64::from(GUEST_MEM1_MIN_SIZE)
            .checked_sub(WINDOWS_ALLOCATION_GRANULARITY)
            .ok_or("minimum MEM1 size is smaller than Windows allocation granularity")?;
        let view = self.map_readonly_window(offset, VIEW_BYTES)?;
        // SAFETY: the view was successfully mapped and is owned by this scope.
        let result = unsafe { UnmapViewOfFile(view.as_ptr().cast()) };
        if result == 0 {
            return Err(format!(
                "could not unmap MEM1 compatibility-check view: {}",
                std::io::Error::last_os_error()
            ));
        }
        Ok(())
    }

    fn map_readonly_window(
        &self,
        mapping_offset: u64,
        mapped_length: usize,
    ) -> Result<NonNull<u8>, String> {
        if mapping_offset % WINDOWS_ALLOCATION_GRANULARITY != 0 {
            return Err("shared-memory view offset is not allocation-granularity aligned".into());
        }
        if mapped_length == 0 || mapped_length > MAX_READ_BYTES + VIEW_BYTES {
            return Err("shared-memory view length exceeds bounded read window".into());
        }
        let raw = unsafe {
            MapViewOfFile(
                self.handle.as_ptr(),
                FILE_MAP_READ,
                (mapping_offset >> 32) as u32,
                mapping_offset as u32,
                mapped_length,
            )
        };
        NonNull::new(raw.cast::<u8>()).ok_or_else(|| {
            format!(
                "could not map read-only Dolphin MEM1 range at shared offset 0x{mapping_offset:x} length 0x{mapped_length:x}: {}",
                std::io::Error::last_os_error()
            )
        })
    }

    pub fn acknowledge(&self, mailbox: Mailbox, value: u32) -> Result<(), String> {
        if !self.writable {
            return Err("refusing ACK write through a read-only Dolphin mapping".into());
        }
        if !mailbox.is_inside_handler()
            || mailbox.acknowledgement_address() < HANDLER_BASE
            || mailbox.acknowledgement_address() + 4 > HANDLER_END
        {
            return Err("refusing shared-memory write outside the validated Gecko mailbox".into());
        }
        let offset = Self::guest_offset(mailbox.acknowledgement_address(), 4)?;
        // SAFETY: mailbox creation is restricted to the checked handler region;
        // its address is aligned and the volatile 32-bit store changes only the
        // reserved acknowledgement word. `to_be` gives PPC the expected bytes.
        unsafe {
            ptr::write_volatile(self.view.as_ptr().add(offset).cast::<u32>(), value.to_be());
        }
        Ok(())
    }
}

impl ReadOnlyMappedMemory for StockMem1 {
    fn read_mapped_window(
        &self,
        mapping_offset: u64,
        mapped_length: usize,
        data_offset: usize,
        length: usize,
    ) -> Result<Vec<u8>, String> {
        let window_end = mapping_offset
            .checked_add(mapped_length as u64)
            .ok_or("mapped view range overflow")?;
        let data_end = (data_offset as u64)
            .checked_add(length as u64)
            .ok_or("mapped data range overflow")?;
        if data_end > mapped_length as u64 || window_end > u64::from(GUEST_MEM1_MIN_SIZE) {
            return Err("mapped view is outside the validated MEM1 range".into());
        }

        if mapping_offset == 0 && window_end <= VIEW_BYTES as u64 {
            let mut bytes = vec![0; length];
            // SAFETY: offsets and lengths were checked against the owned 64 KiB
            // view; volatile reads avoid assuming synchronization with Dolphin.
            unsafe {
                for (index, byte) in bytes.iter_mut().enumerate() {
                    *byte = ptr::read_volatile(self.view.as_ptr().add(data_offset + index));
                }
            }
            return Ok(bytes);
        }

        let view = self.map_readonly_window(mapping_offset, mapped_length)?;
        let mut bytes = vec![0; length];
        // SAFETY: the requested range fits the newly mapped bounded view.
        unsafe {
            for (index, byte) in bytes.iter_mut().enumerate() {
                *byte = ptr::read_volatile(view.as_ptr().add(data_offset + index));
            }
            let unmapped = UnmapViewOfFile(view.as_ptr().cast());
            if unmapped == 0 {
                return Err(format!(
                    "could not unmap read-only Dolphin MEM1 view: {}",
                    std::io::Error::last_os_error()
                ));
            }
        }
        Ok(bytes)
    }
}

impl Drop for StockMem1 {
    fn drop(&mut self) {
        // SAFETY: these are the owned view and mapping handle from open_name.
        unsafe {
            UnmapViewOfFile(self.view.as_ptr().cast());
            CloseHandle(self.handle.as_ptr() as isize);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::gecko_bridge::{locate_mailbox, HANDLER_BASE, HANDLER_END};
    use std::sync::atomic::{AtomicU32, Ordering};

    #[link(name = "kernel32")]
    extern "system" {
        fn CreateFileMappingW(
            file: *mut c_void,
            attributes: *mut c_void,
            protection: u32,
            size_high: u32,
            size_low: u32,
            name: *const u16,
        ) -> *mut c_void;
    }

    static NEXT_NAME: AtomicU32 = AtomicU32::new(1);

    #[test]
    fn named_mapping_reads_counter_and_round_trips_ack_echo_synthetically() {
        let name = format!(
            "Local\\mg1-stock-memory-test-{}-{}",
            std::process::id(),
            NEXT_NAME.fetch_add(1, Ordering::Relaxed)
        );
        let wide: Vec<u16> = OsStr::new(&name).encode_wide().chain(Some(0)).collect();
        // SAFETY: INVALID_HANDLE_VALUE requests a page-file mapping; the unique
        // test name and 64 KiB size are valid for this synthetic test only.
        let handle = unsafe {
            CreateFileMappingW(
                -1isize as *mut c_void,
                ptr::null_mut(),
                0x04, // PAGE_READWRITE
                0,
                VIEW_BYTES as u32,
                wide.as_ptr(),
            )
        };
        let handle = NonNull::new(handle).expect("test mapping creation failed");
        // SAFETY: maps the test's 64 KiB page-file section read/write.
        let initial = unsafe {
            MapViewOfFile(
                handle.as_ptr(),
                FILE_MAP_READ | FILE_MAP_WRITE,
                0,
                0,
                VIEW_BYTES,
            )
        };
        let initial = NonNull::new(initial.cast::<u8>()).expect("test view mapping failed");
        let body = crate::gecko_bridge::payload_words();
        let mailbox_offset = crate::gecko_bridge::mailbox_data_word() * 4;
        unsafe {
            ptr::copy_nonoverlapping(b"RMGP01".as_ptr(), initial.as_ptr(), 6);
            let offset = (HANDLER_BASE - GUEST_BASE) as usize + 2880 + 16;
            for (index, word) in body.iter().enumerate() {
                ptr::write_volatile(
                    initial.as_ptr().add(offset + index * 4).cast::<u32>(),
                    word.to_be(),
                );
            }
            ptr::write_volatile(
                initial.as_ptr().add(offset + mailbox_offset).cast::<u32>(),
                7u32.to_be(),
            );
        }

        let view = StockMem1::open_name(&name).unwrap();
        assert_eq!(view.read_guest(GUEST_BASE, 6).unwrap(), b"RMGP01");
        let handler = view
            .read_guest(HANDLER_BASE, (HANDLER_END - HANDLER_BASE) as usize)
            .unwrap();
        let mailbox = locate_mailbox(HANDLER_BASE, 2880, &handler)
            .unwrap()
            .unwrap();
        assert_eq!(view.read_guest_word(mailbox.counter_address()).unwrap(), 7);
        let read_only = StockMem1::open_name_access(&name, false).unwrap();
        assert!(read_only
            .acknowledge(mailbox, 1)
            .unwrap_err()
            .contains("read-only"));
        view.acknowledge(mailbox, 0x89ab_cdef).unwrap();
        assert_eq!(
            view.read_guest_word(mailbox.acknowledgement_address())
                .unwrap(),
            0x89ab_cdef
        );
        assert_eq!(view.read_guest_word(mailbox.counter_address()).unwrap(), 7);
        assert_eq!(view.read_guest_word(mailbox.echo_address()).unwrap(), 0);
        unsafe {
            assert_eq!(
                u32::from_be(ptr::read_volatile(
                    initial
                        .as_ptr()
                        .add(mailbox.acknowledgement_address() as usize - GUEST_BASE as usize)
                        .cast::<u32>()
                )),
                0x89ab_cdef
            );
            UnmapViewOfFile(initial.as_ptr().cast());
            CloseHandle(handle.as_ptr() as isize);
        }
    }

    #[test]
    fn restricts_reads_to_mapped_mem1_window() {
        assert!(StockMem1::guest_offset(0x7fff_ffff, 4).is_err());
        assert!(StockMem1::guest_offset(0x8000_ffff, 2).is_err());
        assert!(StockMem1::guest_offset(0x8000_0000, 0).is_err());
    }
}
