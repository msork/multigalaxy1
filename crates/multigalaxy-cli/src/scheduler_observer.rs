//! Opt-in, read-only PAL NameObjExecuteHolder metadata observer.
//! It never reads execute-info records or callback lists.

use crate::{memory_inspection as mem, penguin_observer, player_probe::GuestMemory};
use multigalaxy_patch::{branch_target, parse_dol, SectionKind};

const SCENE_EXECUTE_SLOT_ID: u32 = 0x58;
const SCENE_SLOT_STRIDE: u32 = 4;
const HOLDER_SLOT_OFFSET: u32 = SCENE_EXECUTE_SLOT_ID * SCENE_SLOT_STRIDE;
const HOLDER_VPTR: u32 = 0x8059_3178;
const HOLDER_SIZE: u32 = 0x20;
const EXEC_ARRAY_PTR: u32 = 0x0C;
const EXEC_ARRAY_CAPACITY: u32 = 0x10;
const EXEC_ARRAY_COUNT: u32 = 0x14;
const EXEC_FLAGS: u32 = 0x18;
const EXPECTED_CAPACITY: u32 = 4096;
const RECORD_SIZE: u32 = 12;
const EXECUTOR_CTOR: u32 = 0x8026_29BC;
const EXECUTOR_REGISTER: u32 = 0x8026_2140;
const EXEC_INFO_SET_CONNECT: u32 = 0x8026_1C50;
const SCENE_OBJ_CREATE: u32 = 0x8034_4A14;
const SCENE_OBJ_GET: u32 = 0x8034_4A70;
const SCENE_OBJ_FACTORY_CASE: u32 = 0x8034_5410;

const EXECUTOR_CTOR_SIG: &[u8] = &[
    0x94, 0x21, 0xff, 0xf0, 0x7c, 0x08, 0x02, 0xa6, 0x3c, 0xa0, 0x80, 0x59, 0x90, 0x01, 0x00, 0x14,
    0x93, 0xe1, 0x00, 0x0c, 0x7c, 0x9f, 0x23, 0x78, 0x38, 0x85, 0x31, 0x60, 0x93, 0xc1, 0x00, 0x08,
    0x7c, 0x7e, 0x1b, 0x78, 0x4b, 0xff, 0xe7, 0x99, 0x1c, 0x7f, 0x00, 0x0c, 0x3c, 0x80, 0x80, 0x59,
    0x38, 0x00, 0x00, 0x00, 0x93, 0xfe, 0x00, 0x10, 0x38, 0x84, 0x31, 0x78, 0x90, 0x9e, 0x00, 0x00,
    0x38, 0x63, 0x00, 0x10, 0x90, 0x1e, 0x00, 0x0c, 0x90, 0x1e, 0x00, 0x14, 0x98, 0x1e, 0x00, 0x18,
    0x98, 0x1e, 0x00, 0x19, 0x98, 0x1e, 0x00, 0x1a, 0x98, 0x1e, 0x00, 0x1b, 0x98, 0x1e, 0x00, 0x1c,
    0x48, 0x1a, 0x71, 0x1d, 0x3c, 0x80, 0x80, 0x26, 0x7f, 0xe7, 0xfb, 0x78, 0x38, 0x84, 0x1c, 0x24,
    0x38, 0xa0, 0x00, 0x00, 0x38, 0xc0, 0x00, 0x0c, 0x48, 0x2b, 0x45, 0x91, 0x90, 0x7e, 0x00, 0x0c,
    0x7f, 0xc3, 0xf3, 0x78, 0x83, 0xe1, 0x00, 0x0c, 0x83, 0xc1, 0x00, 0x08, 0x80, 0x01, 0x00, 0x14,
    0x7c, 0x08, 0x03, 0xa6, 0x38, 0x21, 0x00, 0x10, 0x4e, 0x80, 0x00, 0x20,
];
const EXECUTOR_REGISTER_SIG: &[u8] = &[
    0x94, 0x21, 0xff, 0xf0, 0x7c, 0x08, 0x02, 0xa6, 0x90, 0x01, 0x00, 0x14, 0x93, 0xe1, 0x00, 0x0c,
    0x7c, 0x9f, 0x23, 0x78, 0x93, 0xc1, 0x00, 0x08, 0x7c, 0x7e, 0x1b, 0x78, 0x80, 0x03, 0x00, 0x14,
    0x80, 0x63, 0x00, 0x0c, 0x1c, 0x00, 0x00, 0x0c, 0x7c, 0x63, 0x02, 0x14, 0x4b, 0xff, 0xfa, 0xe5,
    0x80, 0x1e, 0x00, 0x14, 0xb0, 0x1f, 0x00, 0x0a, 0x80, 0x7e, 0x00, 0x14, 0x38, 0x03, 0x00, 0x01,
    0x90, 0x1e, 0x00, 0x14, 0x83, 0xe1, 0x00, 0x0c, 0x83, 0xc1, 0x00, 0x08, 0x80, 0x01, 0x00, 0x14,
    0x7c, 0x08, 0x03, 0xa6, 0x38, 0x21, 0x00, 0x10, 0x4e, 0x80, 0x00, 0x20,
];
const EXEC_INFO_SET_CONNECT_SIG: &[u8] = &[
    0x94, 0x21, 0xff, 0xd0, 0x7c, 0x08, 0x02, 0xa6, 0x90, 0x01, 0x00, 0x34, 0x39, 0x61, 0x00, 0x30,
    0x48, 0x2b, 0x58, 0xc9, 0x38, 0x00, 0x00, 0x02, 0x90, 0x83, 0x00, 0x00, 0x7c, 0x79, 0x1b, 0x78,
    0x7c, 0x9a, 0x23, 0x78, 0x98, 0x03, 0x00, 0x04, 0x7c, 0xbb, 0x2b, 0x78, 0x7c, 0xdc, 0x33, 0x78,
    0x7c, 0xfd, 0x3b, 0x78, 0x98, 0x03, 0x00, 0x05, 0x7d, 0x1e, 0x43, 0x78, 0x98, 0xa3, 0x00, 0x06,
    0x98, 0xc3, 0x00, 0x07, 0x98, 0xe3, 0x00, 0x09, 0x99, 0x03, 0x00, 0x08, 0x80, 0x6d, 0xc5, 0x88,
    0x80, 0x63, 0x00, 0x24, 0x48, 0x13, 0xb7, 0xb9, 0x2c, 0x1d, 0xff, 0xff, 0x7c, 0x7f, 0x1b, 0x78,
    0x41, 0x82, 0x00, 0x14, 0x7f, 0x44, 0xd3, 0x78, 0x7f, 0xa5, 0xeb, 0x78, 0x48, 0x00, 0xa3, 0x49,
    0xb0, 0x79, 0x00, 0x0a, 0x2c, 0x1b, 0xff, 0xff, 0x41, 0x82, 0x00, 0x14, 0x7f, 0xe3, 0xfb, 0x78,
    0x7f, 0x44, 0xd3, 0x78, 0x7f, 0x65, 0xdb, 0x78, 0x48, 0x00, 0xa3, 0x8d, 0x2c, 0x1c, 0xff, 0xff,
    0x41, 0x82, 0x00, 0x14, 0x7f, 0xe3, 0xfb, 0x78, 0x7f, 0x44, 0xd3, 0x78, 0x7f, 0x85, 0xe3, 0x78,
    0x48, 0x00, 0xa3, 0x7d, 0x2c, 0x1e, 0xff, 0xff, 0x41, 0x82, 0x00, 0x14, 0x7f, 0xe3, 0xfb, 0x78,
    0x7f, 0x44, 0xd3, 0x78, 0x7f, 0xc5, 0xf3, 0x78, 0x48, 0x00, 0xa3, 0x6d, 0x39, 0x61, 0x00, 0x30,
    0x48, 0x2b, 0x58, 0x65, 0x80, 0x01, 0x00, 0x34, 0x7c, 0x08, 0x03, 0xa6, 0x38, 0x21, 0x00, 0x30,
    0x4e, 0x80, 0x00, 0x20,
];
const SCENE_CREATE_SIG: &[u8] = &[
    0x94, 0x21, 0xff, 0xe0, 0x7c, 0x08, 0x02, 0xa6, 0x90, 0x01, 0x00, 0x24, 0x39, 0x61, 0x00, 0x20,
    0x48, 0x1d, 0x2b, 0x15, 0x54, 0x9f, 0x10, 0x3a, 0x7c, 0x7d, 0x1b, 0x78, 0x7c, 0x03, 0xf8, 0x2e,
    0x2c, 0x00, 0x00, 0x00, 0x41, 0x82, 0x00, 0x0c, 0x7c, 0x03, 0x03, 0x78, 0x48, 0x00, 0x00, 0x18,
    0x48, 0x00, 0x00, 0xed, 0x7c, 0x7e, 0x1b, 0x78, 0x4b, 0xf1, 0xc7, 0xdd, 0x7f, 0xdd, 0xf9, 0x2e,
    0x7f, 0xc3, 0xf3, 0x78, 0x39, 0x61, 0x00, 0x20, 0x48, 0x1d, 0x2b, 0x29, 0x80, 0x01, 0x00, 0x24,
    0x7c, 0x08, 0x03, 0xa6, 0x38, 0x21, 0x00, 0x20, 0x4e, 0x80, 0x00, 0x20,
];
const SCENE_GET_SIG: &[u8] = &[
    0x54, 0x80, 0x10, 0x3a, 0x7c, 0x63, 0x00, 0x2e, 0x4e, 0x80, 0x00, 0x20,
];
const FACTORY_CASE_PREFIX: &[u8] = &[
    0x38, 0x60, 0x00, 0x20, 0x48, 0x0c, 0x47, 0x01, 0x2c, 0x03, 0x00, 0x00, 0x41, 0x82, 0x03, 0x84,
    0x38, 0x80, 0x10, 0x00, 0x4b, 0xf1, 0xd5, 0x99,
];
const PAL_SCHEDULER_SIGNATURES: &[(u32, &[u8])] = &[
    (EXECUTOR_CTOR, EXECUTOR_CTOR_SIG),
    (EXECUTOR_REGISTER, EXECUTOR_REGISTER_SIG),
    (EXEC_INFO_SET_CONNECT, EXEC_INFO_SET_CONNECT_SIG),
    (SCENE_OBJ_CREATE, SCENE_CREATE_SIG),
    (SCENE_OBJ_GET, SCENE_GET_SIG),
    (SCENE_OBJ_FACTORY_CASE, FACTORY_CASE_PREFIX),
];

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct SchedulerSample {
    pub scene: u32,
    pub scene_object_holder: u32,
    pub all_live_actor_group: u32,
    pub execute_holder: u32,
    pub execute_array: u32,
    pub capacity: u32,
    pub count: u32,
    pub remaining: u32,
    /// Deferred scheduler operation flags at holder +0x18..+0x1c.
    pub pending_flags: [u8; 5],
}

fn add(base: u32, offset: u32) -> Result<u32, String> {
    base.checked_add(offset)
        .ok_or_else(|| "scheduler address arithmetic overflow".into())
}

fn read_bytes(memory: &mut impl GuestMemory, address: u32, size: usize) -> Result<Vec<u8>, String> {
    mem::plan_read(address, size)?;
    let bytes = memory.read_guest(address, size)?;
    if bytes.len() != size {
        return Err(format!("short scheduler read at 0x{address:08x}"));
    }
    Ok(bytes)
}

fn read_word(memory: &mut impl GuestMemory, address: u32) -> Result<u32, String> {
    mem::decode_be_u32(address, &read_bytes(memory, address, 4)?)
}

fn read_ptr(memory: &mut impl GuestMemory, address: u32, label: &str) -> Result<u32, String> {
    mem::decode_be_pointer(address, &read_bytes(memory, address, 4)?)?
        .ok_or_else(|| format!("{label} is null (not initialized in this scene)"))
}

fn checked_span(address: u32, bytes: u32, label: &str) -> Result<(), String> {
    if address & 3 != 0 {
        return Err(format!("{label} is not 4-byte aligned"));
    }
    let end = u64::from(address)
        .checked_add(u64::from(bytes))
        .ok_or_else(|| format!("{label} range overflow"))?;
    if address < mem::GUEST_MEM1_BASE || end > u64::from(mem::GUEST_MEM1_LIMIT) {
        return Err(format!("{label} span is outside canonical MEM1"));
    }
    Ok(())
}

fn read_once(memory: &mut impl GuestMemory) -> Result<SchedulerSample, String> {
    let context = penguin_observer::validated_scene_context(memory)?;
    let holder_slot = add(context.scene_object_holder, HOLDER_SLOT_OFFSET)?;
    let execute_holder = read_ptr(memory, holder_slot, "NameObjExecuteHolder scene slot")?;
    checked_span(execute_holder, HOLDER_SIZE, "NameObjExecuteHolder object")?;
    if read_word(memory, execute_holder)? != HOLDER_VPTR {
        return Err("NameObjExecuteHolder vptr mismatch".into());
    }

    let execute_array = read_ptr(
        memory,
        add(execute_holder, EXEC_ARRAY_PTR)?,
        "execute-info array",
    )?;
    let capacity = read_word(memory, add(execute_holder, EXEC_ARRAY_CAPACITY)?)?;
    let count = read_word(memory, add(execute_holder, EXEC_ARRAY_COUNT)?)?;
    if capacity != EXPECTED_CAPACITY {
        return Err(format!(
            "execute-info capacity mismatch: expected {EXPECTED_CAPACITY}, found {capacity}"
        ));
    }
    if count > capacity {
        return Err(format!(
            "execute-info count {count} exceeds capacity {capacity}"
        ));
    }
    let storage_bytes = capacity
        .checked_mul(RECORD_SIZE)
        .ok_or("execute-info storage size overflow")?;
    checked_span(execute_array, storage_bytes, "execute-info array")?;
    let pending = read_bytes(memory, add(execute_holder, EXEC_FLAGS)?, 5)?;
    let pending_flags: [u8; 5] = pending
        .as_slice()
        .try_into()
        .map_err(|_| "invalid scheduler flags length")?;
    if pending_flags.iter().any(|&flag| flag > 1) {
        return Err("scheduler pending flags contain a non-boolean value".into());
    }

    Ok(SchedulerSample {
        scene: context.scene,
        scene_object_holder: context.scene_object_holder,
        all_live_actor_group: context.all_live_actor_group,
        execute_holder,
        execute_array,
        capacity,
        count,
        remaining: capacity - count,
        pending_flags,
    })
}

/// Read holder metadata twice and discard a sample if the scene chain or any
/// holder metadata changed. No execute-info record or callback list is read.
pub fn observe(memory: &mut impl GuestMemory) -> Result<SchedulerSample, String> {
    if memory.read_guest(mem::GUEST_MEM1_BASE, 6)? != b"RMGP01" {
        return Err("live game identity is not PAL RMGP01".into());
    }
    let before = read_once(memory)?;
    let after = read_once(memory)?;
    if before != after {
        return Err("scene or execute-holder metadata changed during sample; discarded".into());
    }
    if memory.read_guest(mem::GUEST_MEM1_BASE, 6)? != b"RMGP01" {
        return Err("live game identity changed during scheduler observation".into());
    }
    Ok(before)
}

pub fn verify_pal_layout(dol: &[u8], symbols: &[u8]) -> Result<(), String> {
    crate::player_probe::verify_pal_inputs(dol, symbols)?;
    let text = std::str::from_utf8(symbols)
        .map_err(|error| format!("Petari symbols are not UTF-8: {error}"))?;
    for expected in [
        "create__14SceneObjHolderFi = .text:0x80344A14; // type:function size:0x5C scope:global align:4",
        "getObj__14SceneObjHolderCFi = .text:0x80344A70; // type:function size:0xC scope:global align:4",
        "newEachObj__14SceneObjHolderFi = .text:0x80344B30; // type:function size:0xC84 scope:global align:4",
        "registerActor__20NameObjExecuteHolderFP7NameObjiiii = .text:0x80262140; // type:function size:0x5C scope:global align:4",
        "setConnectInfo__18NameObjExecuteInfoFP7NameObjiiii = .text:0x80261C50; // type:function size:0xD4 scope:global align:4",
        "__ct__20NameObjExecuteHolderFi = .text:0x802629BC; // type:function size:0x9C scope:global align:4",
        "__vt__20NameObjExecuteHolder = .data:0x80593178; // type:object size:0x28 scope:global align:4",
    ] {
        if !text.lines().any(|line| line.trim() == expected) {
            return Err(format!("PAL scheduler symbol mismatch: {expected}"));
        }
    }
    let info = parse_dol(dol).map_err(|error| format!("invalid PAL DOL: {error}"))?;
    for (address, signature) in [
        (EXECUTOR_CTOR, EXECUTOR_CTOR_SIG),
        (EXECUTOR_REGISTER, EXECUTOR_REGISTER_SIG),
        (SCENE_OBJ_CREATE, SCENE_CREATE_SIG),
        (SCENE_OBJ_GET, SCENE_GET_SIG),
        (SCENE_OBJ_FACTORY_CASE, FACTORY_CASE_PREFIX),
    ] {
        let bytes = dol_text_bytes(dol, &info, address, signature.len())?;
        if bytes != signature {
            return Err(format!(
                "PAL scheduler instruction signature mismatch at 0x{address:08x}"
            ));
        }
    }
    let factory_branch = u32::from_be_bytes(FACTORY_CASE_PREFIX[20..24].try_into().unwrap());
    if branch_target(factory_branch, SCENE_OBJ_FACTORY_CASE + 20) != Some(EXECUTOR_CTOR) {
        return Err(
            "PAL SceneObj factory case does not call the verified execute-holder constructor"
                .into(),
        );
    }
    Ok(())
}

pub fn verify_live_signatures(memory: &mut impl GuestMemory, dol: &[u8]) -> Result<(), String> {
    let info = parse_dol(dol).map_err(|error| format!("invalid PAL DOL: {error}"))?;
    for &(address, signature) in PAL_SCHEDULER_SIGNATURES {
        let expected = dol_text_bytes(dol, &info, address, signature.len())?;
        let actual = read_bytes(memory, address, signature.len())?;
        if actual != expected {
            return Err(format!(
                "live PAL scheduler signature mismatch at 0x{address:08x}"
            ));
        }
    }
    Ok(())
}

fn dol_text_bytes<'a>(
    dol: &'a [u8],
    info: &multigalaxy_patch::DolInfo,
    address: u32,
    length: usize,
) -> Result<&'a [u8], String> {
    let end = u64::from(address)
        .checked_add(length as u64)
        .ok_or("PAL scheduler signature range overflow")?;
    let section = info
        .sections
        .iter()
        .find(|section| {
            section.kind == SectionKind::Text
                && address >= section.address
                && end <= u64::from(section.address) + u64::from(section.size)
        })
        .ok_or_else(|| format!("PAL scheduler code range 0x{address:08x} is not in DOL text"))?;
    let offset =
        usize::try_from(u64::from(section.file_offset) + u64::from(address - section.address))
            .map_err(|_| "PAL scheduler DOL offset overflow")?;
    let end = offset
        .checked_add(length)
        .ok_or("PAL scheduler file range overflow")?;
    dol.get(offset..end)
        .ok_or_else(|| "PAL DOL does not contain scheduler signature".into())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::BTreeMap;

    const GAME: u32 = 0x8001_0000;
    const CONTROLLER: u32 = 0x8001_0100;
    const SCENE: u32 = 0x8001_0200;
    const SCENE_HOLDER: u32 = 0x8001_0300;
    const GROUP: u32 = 0x8001_0400;
    const EXEC_HOLDER: u32 = 0x8001_0500;
    const EXEC_ARRAY: u32 = 0x8001_1000;

    struct Fixture {
        words: BTreeMap<u32, u32>,
        byte_reads: BTreeMap<u32, Vec<u8>>,
        reads: Vec<(u32, usize)>,
        mutate_scene_on_scene_read: Option<usize>,
        scene_reads: usize,
    }

    impl Fixture {
        fn valid() -> Self {
            let mut result = Self {
                words: BTreeMap::new(),
                byte_reads: BTreeMap::new(),
                reads: Vec::new(),
                mutate_scene_on_scene_read: None,
                scene_reads: 0,
            };
            for (address, value) in [
                (0x806A_1228, GAME),
                (GAME + 0x24, CONTROLLER),
                (CONTROLLER + 0xAC, SCENE),
                (SCENE + 0x10, SCENE_HOLDER),
                (SCENE_HOLDER + 0xC0, GROUP),
                (GROUP, 0x8057_327C),
                (GROUP + 0x0C, 0xA00),
                (GROUP + 0x10, 0x48C),
                (GROUP + 0x14, 0x8001_2000),
                (SCENE_HOLDER + HOLDER_SLOT_OFFSET, EXEC_HOLDER),
                (EXEC_HOLDER, HOLDER_VPTR),
                (EXEC_HOLDER + EXEC_ARRAY_PTR, EXEC_ARRAY),
                (EXEC_HOLDER + EXEC_ARRAY_CAPACITY, EXPECTED_CAPACITY),
                (EXEC_HOLDER + EXEC_ARRAY_COUNT, 2500),
            ] {
                result.words.insert(address, value);
            }
            result
                .byte_reads
                .insert(mem::GUEST_MEM1_BASE, b"RMGP01".to_vec());
            result
                .byte_reads
                .insert(EXEC_HOLDER + EXEC_FLAGS, vec![0, 0, 0, 0, 0]);
            result
        }

        fn set(&mut self, address: u32, value: u32) {
            self.words.insert(address, value);
        }
    }

    impl GuestMemory for Fixture {
        fn read_guest(&mut self, address: u32, length: usize) -> Result<Vec<u8>, String> {
            self.reads.push((address, length));
            if address == CONTROLLER + 0xAC {
                self.scene_reads += 1;
                if self.mutate_scene_on_scene_read == Some(self.scene_reads) {
                    self.words.insert(CONTROLLER + 0xAC, 0x8001_0600);
                }
            }
            if let Some(bytes) = self.byte_reads.get(&address) {
                return Ok(bytes[..length.min(bytes.len())].to_vec());
            }
            let mut out = Vec::new();
            for offset in (0..length).step_by(4) {
                let at = address + offset as u32;
                let value = self
                    .words
                    .get(&at)
                    .ok_or_else(|| format!("fixture missing word 0x{at:08x}"))?;
                out.extend_from_slice(&value.to_be_bytes());
            }
            Ok(out)
        }
    }

    #[test]
    fn observes_holder_capacity_count_and_remaining_without_reading_records() {
        let mut fixture = Fixture::valid();
        let sample = observe(&mut fixture).unwrap();
        assert_eq!(sample.scene, SCENE);
        assert_eq!(sample.scene_object_holder, SCENE_HOLDER);
        assert_eq!(sample.all_live_actor_group, GROUP);
        assert_eq!(sample.execute_holder, EXEC_HOLDER);
        assert_eq!(sample.execute_array, EXEC_ARRAY);
        assert_eq!(sample.capacity, 4096);
        assert_eq!(sample.count, 2500);
        assert_eq!(sample.remaining, 1596);
        assert_eq!(sample.pending_flags, [0; 5]);
        assert!(!fixture.reads.iter().any(|(address, _)| {
            *address >= EXEC_ARRAY && *address < EXEC_ARRAY + 4096 * RECORD_SIZE
        }));
    }

    #[test]
    fn rejects_null_holder_wrong_vptr_and_invalid_array_pointer() {
        let mut null = Fixture::valid();
        null.set(SCENE_HOLDER + HOLDER_SLOT_OFFSET, 0);
        assert!(observe(&mut null).unwrap_err().contains("not initialized"));

        let mut bad_vptr = Fixture::valid();
        bad_vptr.set(EXEC_HOLDER, HOLDER_VPTR + 4);
        assert!(observe(&mut bad_vptr)
            .unwrap_err()
            .contains("vptr mismatch"));

        let mut bad_array = Fixture::valid();
        bad_array.set(EXEC_HOLDER + EXEC_ARRAY_PTR, 0x817F_FFFC);
        assert!(observe(&mut bad_array)
            .unwrap_err()
            .contains("outside canonical MEM1"));
    }

    #[test]
    fn rejects_non_pal_game_identity() {
        let mut fixture = Fixture::valid();
        fixture
            .byte_reads
            .insert(mem::GUEST_MEM1_BASE, b"RMGP02".to_vec());
        assert!(observe(&mut fixture)
            .unwrap_err()
            .contains("not PAL RMGP01"));
    }

    #[test]
    fn rejects_capacity_count_flags_and_storage_overflow() {
        let mut bad_capacity = Fixture::valid();
        bad_capacity.set(EXEC_HOLDER + EXEC_ARRAY_CAPACITY, 4095);
        assert!(observe(&mut bad_capacity)
            .unwrap_err()
            .contains("capacity mismatch"));

        let mut bad_count = Fixture::valid();
        bad_count.set(EXEC_HOLDER + EXEC_ARRAY_COUNT, 4097);
        assert!(observe(&mut bad_count)
            .unwrap_err()
            .contains("exceeds capacity"));

        let mut bad_bounds = Fixture::valid();
        bad_bounds.set(EXEC_HOLDER + EXEC_ARRAY_PTR, 0x817F_5000);
        assert!(observe(&mut bad_bounds)
            .unwrap_err()
            .contains("outside canonical MEM1"));

        let mut bad_flags = Fixture::valid();
        bad_flags
            .byte_reads
            .insert(EXEC_HOLDER + EXEC_FLAGS, vec![0, 0, 2, 0, 0]);
        assert!(observe(&mut bad_flags).unwrap_err().contains("non-boolean"));
    }

    #[test]
    fn rejects_inconsistent_scene_chain_between_samples() {
        let mut fixture = Fixture::valid();
        fixture.mutate_scene_on_scene_read = Some(2);
        fixture.words.insert(0x8001_0600 + 0x10, 0x8001_0700);
        fixture.words.insert(0x8001_0700 + 0xC0, 0x8001_0800);
        fixture.words.insert(0x8001_0800, 0x8057_327C);
        fixture.words.insert(0x8001_0800 + 0x0C, 0xA00);
        fixture.words.insert(0x8001_0800 + 0x10, 0x48C);
        fixture.words.insert(0x8001_0800 + 0x14, 0x8001_3000);
        let error = observe(&mut fixture).unwrap_err();
        assert!(
            error.contains("changed during identity check")
                || error.contains("changed during sample")
        );
    }
}
