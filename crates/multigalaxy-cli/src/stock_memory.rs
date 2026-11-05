//! Narrow Windows view of Dolphin 2609a's named MEM1 backing section.
//!
//! Dolphin's stock Windows MemArenaWin names the page-file mapping
//! `dolphin-emu.<pid>`. Memmap.cpp assigns MEM1 the first shared-memory offset.
//! We open only the first 64 KiB of that view and only permit RMGP01 identity,
//! Gecko-handler reads, and one validated mailbox acknowledgement write.

use crate::gecko_bridge::{Mailbox, HANDLER_BASE, HANDLER_END};
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
}

impl StockMem1 {
    pub fn open_dolphin(pid: u32) -> Result<Self, MappingOpenError> {
        Self::open_name(&format!("dolphin-emu.{pid}"))
    }

    fn open_name(name: &str) -> Result<Self, MappingOpenError> {
        let wide: Vec<u16> = OsStr::new(name).encode_wide().chain(Some(0)).collect();
        // SAFETY: `wide` is a nul-terminated UTF-16 name and remains alive for
        // the duration of the call. The requested access is limited to the
        // caller's same-session Dolphin page-file mapping.
        let raw_handle =
            unsafe { OpenFileMappingW(FILE_MAP_READ | FILE_MAP_WRITE, 0, wide.as_ptr()) };
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
        let raw_view = unsafe {
            MapViewOfFile(
                handle.as_ptr(),
                FILE_MAP_READ | FILE_MAP_WRITE,
                0,
                0,
                VIEW_BYTES,
            )
        };
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
        Ok(Self { handle, view })
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
        let offset = Self::guest_offset(address, length)?;
        let mut bytes = vec![0; length];
        // SAFETY: the requested range was checked against the 64 KiB view;
        // each volatile byte read is independent of Dolphin's concurrent CPU.
        unsafe {
            for (index, byte) in bytes.iter_mut().enumerate() {
                *byte = ptr::read_volatile(self.view.as_ptr().add(offset + index));
            }
        }
        Ok(bytes)
    }

    pub fn read_guest_word(&self, address: u32) -> Result<u32, String> {
        if address & 3 != 0 {
            return Err("guest word reads must be 4-byte aligned".into());
        }
        let offset = Self::guest_offset(address, 4)?;
        // SAFETY: MapViewOfFile is page-aligned and the validated address is
        // aligned; four bytes fit inside the mapped view.
        let raw = unsafe { ptr::read_volatile(self.view.as_ptr().add(offset).cast::<u32>()) };
        Ok(u32::from_be(raw))
    }

    pub fn acknowledge(&self, mailbox: Mailbox, value: u32) -> Result<(), String> {
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
        unsafe {
            ptr::copy_nonoverlapping(b"RMGP01".as_ptr(), initial.as_ptr(), 6);
            let offset = (HANDLER_BASE - GUEST_BASE) as usize + 2880 + 16;
            let body = [
                0x7c08_02a6u32,
                0x4800_0005,
                0x7d88_02a6,
                0x398c_0028,
                0x816c_0000,
                0x396b_0001,
                0x916c_0000,
                0x814c_0004,
                0x914c_0008,
                0x7c08_03a6,
                0x4800_0018,
                0x6000_0000,
                0,
                0,
                0,
                0x4d47_3142,
                0x4e80_0020,
                0x6000_0000,
            ];
            for (index, word) in body.iter().enumerate() {
                ptr::write_volatile(
                    initial.as_ptr().add(offset + index * 4).cast::<u32>(),
                    word.to_be(),
                );
            }
            ptr::write_volatile(
                initial.as_ptr().add(offset + 48).cast::<u32>(),
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
        view.acknowledge(mailbox, 0x89ab_cdef).unwrap();
        assert_eq!(
            view.read_guest_word(mailbox.acknowledgement_address())
                .unwrap(),
            0x89ab_cdef
        );
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
