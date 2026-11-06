//! One-shot, read-only snapshot of the shared J3DModelData joint fields that
//! `MR::newJ3DModel` clears (joint `+0x54` matrix-calc procedure and `+0x04`
//! callback), sampled twice within the same scene a few seconds apart so
//! per-frame Xanime writes are not misread as transition effects.
//!
//! All addresses are guest addresses only, pinned to the exact PAL RMGP01
//! Rev 0 DOL by code signatures in `verify_pal_layout`. No game pointer is
//! cached between calls, no memory is written, and the full Mario model chain
//! is re-resolved before and after every sample. Joint-field values are
//! endpoint inferences only: the factory clear itself is not traced.

use crate::{
    mario_model_observer, memory_inspection as mem,
    player_probe::{self, GuestMemory},
};
use multigalaxy_patch::{branch_target, parse_dol, SectionKind};
use std::time::Duration;

// J3DModelData joint table (PAL `invalidateMtxCalc`/`invalidateJointCallback`).
const MODEL_DATA_JOINT_ARRAY: u32 = 0x28;
const MODEL_DATA_JOINT_COUNT: u32 = 0x2C;
const JOINT_CALLBACK: u32 = 0x04;
const JOINT_MTX_CALC: u32 = 0x54;
const JOINT_SPAN: usize = 0x58;
/// Upper bound on joints read per sample; the Mario model has far fewer.
const MAX_JOINTS: u32 = 0x40;
/// Gap between the two within-scene samples of one invocation.
const WITHIN_SCENE_GAP: Duration = Duration::from_secs(3);

const INVALIDATE_MTX_CALC: u32 = 0x803E_7FC4;
const INVALIDATE_MTX_CALC_SIG: &[u8] = &[
    0xA0, 0xC3, 0x00, 0x2C, 0x38, 0xE0, 0x00, 0x00, 0x38, 0xA0, 0x00, 0x00, 0x48, 0x00, 0x00, 0x18,
    0x80, 0x83, 0x00, 0x28, 0x54, 0xE0, 0x13, 0xBA, 0x38, 0xE7, 0x00, 0x01, 0x7C, 0x84, 0x00, 0x2E,
    0x90, 0xA4, 0x00, 0x54, 0x54, 0xE0, 0x04, 0x3E, 0x7C, 0x00, 0x30, 0x40, 0x41, 0x80, 0xFF, 0xE4,
    0x4E, 0x80, 0x00, 0x20,
];
const INVALIDATE_JOINT_CALLBACK: u32 = 0x803E_7FF8;
const INVALIDATE_JOINT_CALLBACK_SIG: &[u8] = &[
    0xA0, 0xC3, 0x00, 0x2C, 0x38, 0xE0, 0x00, 0x00, 0x38, 0xA0, 0x00, 0x00, 0x48, 0x00, 0x00, 0x18,
    0x80, 0x83, 0x00, 0x28, 0x54, 0xE0, 0x13, 0xBA, 0x38, 0xE7, 0x00, 0x01, 0x7C, 0x84, 0x00, 0x2E,
    0x90, 0xA4, 0x00, 0x04, 0x54, 0xE0, 0x04, 0x3E, 0x7C, 0x00, 0x30, 0x40, 0x41, 0x80, 0xFF, 0xE4,
    0x4E, 0x80, 0x00, 0x20,
];
const ENABLE_JOINT_TRANSFORM: u32 = 0x8001_8D98;
const ENABLE_JOINT_TRANSFORM_SIG: &[u8] = &[
    0x94, 0x21, 0xFF, 0xE0, 0x7C, 0x08, 0x02, 0xA6, 0x90, 0x01, 0x00, 0x24, 0x39, 0x61, 0x00, 0x20,
    0x48, 0x4F, 0xE7, 0x89, 0x83, 0x83, 0x00, 0x08, 0x7C, 0x7D, 0x1B, 0x78, 0x7C, 0x9E, 0x23, 0x78,
    0x1C, 0x7C, 0x00, 0x70, 0x38, 0x63, 0x00, 0x10, 0x48, 0x3F, 0x0D, 0x79, 0x3C, 0x80, 0x80, 0x02,
    0x7F, 0x87, 0xE3, 0x78, 0x38, 0x84, 0x8C, 0xFC, 0x38, 0xA0, 0x00, 0x00, 0x38, 0xC0, 0x00, 0x70,
    0x48, 0x4F, 0xE1, 0xED, 0x90, 0x7D, 0x00, 0x14, 0x3B, 0xE0, 0x00, 0x00, 0x3B, 0x80, 0x00, 0x00,
    0x48, 0x00, 0x00, 0xB0, 0x80, 0x9E, 0x00, 0x28, 0x57, 0xE0, 0x13, 0xBA, 0x80, 0x7D, 0x00, 0x14,
];
const INIT_JOINT_TRANSFORM: u32 = 0x8016_9678;
const INIT_JOINT_TRANSFORM_SIG: &[u8] = &[
    0x94, 0x21, 0xFF, 0xF0, 0x7C, 0x08, 0x02, 0xA6, 0x90, 0x01, 0x00, 0x14, 0x93, 0xE1, 0x00, 0x0C,
    0x7C, 0x7F, 0x1B, 0x78, 0x48, 0x00, 0x00, 0xB9, 0x80, 0xBF, 0x00, 0x18, 0x80, 0x83, 0x00, 0x04,
    0x80, 0x65, 0x00, 0x6C, 0x4B, 0xEA, 0xF6, 0xFD, 0x80, 0x01, 0x00, 0x14, 0x83, 0xE1, 0x00, 0x0C,
];

const JOINT_CODE_SIGNATURES: &[(u32, &[u8])] = &[
    (INVALIDATE_MTX_CALC, INVALIDATE_MTX_CALC_SIG),
    (INVALIDATE_JOINT_CALLBACK, INVALIDATE_JOINT_CALLBACK_SIG),
    (ENABLE_JOINT_TRANSFORM, ENABLE_JOINT_TRANSFORM_SIG),
    (INIT_JOINT_TRANSFORM, INIT_JOINT_TRANSFORM_SIG),
];

const EXPECTED_SYMBOLS: &[(&str, &str)] = &[
    (
        "ModelManager::initJointTransform",
        "initJointTransform__12ModelManagerFv = .text:0x80169678; // type:function size:0x3C scope:global align:4",
    ),
    (
        "XanimeCore::enableJointTransform",
        "enableJointTransform__10XanimeCoreFP12J3DModelData = .text:0x80018D98; // type:function size:0x124 scope:global align:4",
    ),
    (
        "newJ3DModel",
        "newJ3DModel__2MRFPC14ResourceHolderPCc10J3DMdlFlag = .text:0x803E9668; // type:function size:0x440 scope:global align:4",
    ),
];

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct JointFields {
    pub joint: u32,
    pub callback: u32,
    pub mtx_calc: u32,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct JointSample {
    pub joint_array: u32,
    pub joint_count: u32,
    pub joints: Vec<JointFields>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct JointObservation {
    pub base: mario_model_observer::ModelSample,
    pub first: JointSample,
    pub second: JointSample,
    pub within_scene_stable: bool,
}

fn read_guest(
    memory: &mut impl GuestMemory,
    address: u32,
    length: usize,
) -> Result<Vec<u8>, String> {
    mem::plan_read(address, length)?;
    memory
        .read_guest(address, length)
        .map_err(|error| format!("read at 0x{address:08x}+0x{length:x} failed: {error}"))
}

fn read_word(memory: &mut impl GuestMemory, address: u32) -> Result<u32, String> {
    let bytes = read_guest(memory, address, 4)?;
    mem::decode_be_u32(address, &bytes)
}

fn checked_field(base: u32, offset: u32, label: &str) -> Result<u32, String> {
    let address = base
        .checked_add(offset)
        .ok_or_else(|| format!("{label} field address overflow"))?;
    mem::plan_read(address, 4).map_err(|error| format!("{label}: {error}"))?;
    Ok(address)
}

fn required_pointer(
    memory: &mut impl GuestMemory,
    address: u32,
    label: &str,
) -> Result<u32, String> {
    let raw = read_word(memory, address)?;
    if raw == 0 {
        return Err(format!("{label} is null"));
    }
    if raw & 3 != 0 || !player_probe::is_guest_ram_range(raw, 4) {
        return Err(format!("{label} has invalid Wii pointer 0x{raw:08x}"));
    }
    if mem::plan_read(raw, 4).is_err() {
        return Err(format!(
            "{label} points outside the validated read-only MEM1 window: 0x{raw:08x}"
        ));
    }
    Ok(raw)
}

/// Read the shared ModelData joint table and each joint's `+0x04`/`+0x54`
/// words. Fixed, plan-checked reads only; the joint count is capped.
pub fn read_joint_fields(
    memory: &mut impl GuestMemory,
    model_data: u32,
) -> Result<JointSample, String> {
    let count = read_word(
        memory,
        checked_field(
            model_data,
            MODEL_DATA_JOINT_COUNT,
            "J3DModelData joint count",
        )?,
    )?;
    if count > MAX_JOINTS {
        return Err(format!(
            "J3DModelData joint count {count} exceeds the bounded observer cap {MAX_JOINTS}"
        ));
    }
    let array = required_pointer(
        memory,
        checked_field(
            model_data,
            MODEL_DATA_JOINT_ARRAY,
            "J3DModelData joint array",
        )?,
        "J3DModelData joint array",
    )?;
    let mut joints = Vec::with_capacity(count as usize);
    for index in 0..count {
        let slot = array
            .checked_add(index.checked_mul(4).ok_or("joint slot overflow")?)
            .ok_or("joint slot address overflow")?;
        let joint = required_pointer(memory, slot, &format!("joint array slot {index}"))?;
        mem::plan_read(joint, JOINT_SPAN)
            .map_err(|error| format!("joint {index} at 0x{joint:08x} is out of bounds: {error}"))?;
        let callback = read_word(
            memory,
            checked_field(joint, JOINT_CALLBACK, "joint callback")?,
        )
        .map_err(|error| format!("joint {index} callback: {error}"))?;
        let mtx_calc = read_word(
            memory,
            checked_field(joint, JOINT_MTX_CALC, "joint mtx calc")?,
        )
        .map_err(|error| format!("joint {index} mtx calc: {error}"))?;
        joints.push(JointFields {
            joint,
            callback,
            mtx_calc,
        });
    }
    Ok(JointSample {
        joint_array: array,
        joint_count: count,
        joints,
    })
}

fn same_model_tuple(
    a: &mario_model_observer::ModelSample,
    b: &mario_model_observer::ModelSample,
) -> bool {
    a.scene == b.scene
        && a.actor_group == b.actor_group
        && a.mario_actor == b.mario_actor
        && a.model_manager == b.model_manager
        && a.model == b.model
        && a.model_data == b.model_data
        && a.scene_heap_napa == b.scene_heap_napa
        && a.current_heap == b.current_heap
}

/// Validate local DOL/symbol identity and exact code signatures for the joint
/// invalidation/re-arm paths. The caller separately validates AllStars overlap.
pub fn verify_pal_layout(dol: &[u8], symbols: &[u8]) -> Result<(), String> {
    player_probe::verify_pal_inputs(dol, symbols)?;
    let text = std::str::from_utf8(symbols)
        .map_err(|error| format!("Petari symbol map is not UTF-8: {error}"))?;
    for (label, expected) in EXPECTED_SYMBOLS {
        if !text.lines().any(|line| line.trim() == *expected) {
            return Err(format!("PAL joint-observer symbol mismatch for {label}"));
        }
    }
    for (address, expected) in JOINT_CODE_SIGNATURES {
        if dol_text_bytes(dol, *address, expected.len())? != *expected {
            return Err(format!(
                "PAL joint-observer DOL signature mismatch at 0x{address:08x}"
            ));
        }
    }
    let rearm_call = u32::from_be_bytes(
        INIT_JOINT_TRANSFORM_SIG[36..40]
            .try_into()
            .expect("four-byte branch signature"),
    );
    if branch_target(rearm_call, INIT_JOINT_TRANSFORM + 36) != Some(ENABLE_JOINT_TRANSFORM) {
        return Err(
            "PAL initJointTransform no longer calls the verified enableJointTransform".into(),
        );
    }
    Ok(())
}

pub fn verify_live_signatures(memory: &mut impl GuestMemory, dol: &[u8]) -> Result<(), String> {
    for (address, expected) in JOINT_CODE_SIGNATURES {
        if dol_text_bytes(dol, *address, expected.len())? != *expected {
            return Err(format!(
                "local PAL joint-observer signature changed at 0x{address:08x}"
            ));
        }
        if read_guest(memory, *address, expected.len())? != *expected {
            return Err(format!(
                "live PAL joint-observer signature mismatch at 0x{address:08x}"
            ));
        }
    }
    Ok(())
}

pub fn ensure_patch_disjoint(writes: &[multigalaxy_patch::PatchWrite]) -> Result<(), String> {
    for (address, signature) in JOINT_CODE_SIGNATURES {
        let (start, end) = (
            u64::from(*address),
            u64::from(*address) + signature.len() as u64,
        );
        if writes.iter().any(|write| {
            let write_start = u64::from(write.address);
            let write_end = write_start + 4;
            write_start < end && start < write_end
        }) {
            return Err(format!(
                "required AllStars patch overlaps joint-observer evidence at 0x{start:08x}"
            ));
        }
    }
    Ok(())
}

pub fn observe(memory: &mut impl GuestMemory, dol: &[u8]) -> Result<JointObservation, String> {
    verify_live_signatures(memory, dol)?;
    let base_first = mario_model_observer::observe(memory, dol)?;
    let first = read_joint_fields(memory, base_first.model_data)?;
    std::thread::sleep(WITHIN_SCENE_GAP);
    let base_second = mario_model_observer::observe(memory, dol)?;
    let second = read_joint_fields(memory, base_second.model_data)?;
    if !same_model_tuple(&base_first, &base_second) {
        return Err(
            "Mario model chain or scene identity changed between the two within-scene samples"
                .into(),
        );
    }
    verify_live_signatures(memory, dol)?;
    let within_scene_stable = first == second;
    Ok(JointObservation {
        base: base_first,
        first,
        second,
        within_scene_stable,
    })
}

fn dol_text_bytes(dol: &[u8], address: u32, length: usize) -> Result<&[u8], String> {
    let info = parse_dol(dol).map_err(|error| format!("invalid PAL DOL: {error}"))?;
    let end = u64::from(address)
        .checked_add(length as u64)
        .ok_or("PAL joint-observer signature range overflow")?;
    let section = info
        .sections
        .iter()
        .find(|section| {
            section.kind == SectionKind::Text
                && address >= section.address
                && end <= u64::from(section.address) + u64::from(section.size)
        })
        .ok_or_else(|| {
            format!("PAL joint-observer signature at 0x{address:08x} is outside a DOL text section")
        })?;
    let offset =
        usize::try_from(u64::from(section.file_offset) + u64::from(address - section.address))
            .map_err(|_| "PAL joint-observer signature file offset overflow")?;
    let end = offset
        .checked_add(length)
        .ok_or("PAL joint-observer signature file span overflow")?;
    dol.get(offset..end)
        .ok_or_else(|| "PAL DOL is truncated at joint-observer signature".into())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::BTreeMap;

    const MODEL_DATA: u32 = 0x8000_A000;
    const JOINT_ARRAY: u32 = 0x8000_E000;
    const JOINT0: u32 = 0x8000_F000;
    const JOINT1: u32 = 0x8001_0000;

    #[derive(Default)]
    struct Memory {
        words: BTreeMap<u32, u32>,
    }

    impl Memory {
        fn set(&mut self, address: u32, value: u32) {
            self.words.insert(address, value);
        }

        fn valid() -> Self {
            let mut memory = Self::default();
            memory.set(MODEL_DATA + MODEL_DATA_JOINT_ARRAY, JOINT_ARRAY);
            memory.set(MODEL_DATA + MODEL_DATA_JOINT_COUNT, 2);
            memory.set(JOINT_ARRAY, JOINT0);
            memory.set(JOINT_ARRAY + 4, JOINT1);
            memory.set(JOINT0 + JOINT_CALLBACK, 0x8002_0000);
            memory.set(JOINT0 + JOINT_MTX_CALC, 0x8002_1000);
            memory.set(JOINT1 + JOINT_CALLBACK, 0);
            memory.set(JOINT1 + JOINT_MTX_CALC, 0);
            memory
        }
    }

    impl GuestMemory for Memory {
        fn read_guest(&mut self, address: u32, length: usize) -> Result<Vec<u8>, String> {
            if length != 4 {
                return Err(format!("unexpected read 0x{address:08x}+0x{length:x}"));
            }
            self.words
                .get(&address)
                .copied()
                .map(|value| value.to_be_bytes().to_vec())
                .ok_or_else(|| format!("fixture has no word at 0x{address:08x}"))
        }
    }

    #[test]
    fn reads_joint_array_count_and_both_field_words() {
        let mut memory = Memory::valid();
        let sample = read_joint_fields(&mut memory, MODEL_DATA).unwrap();
        assert_eq!(sample.joint_array, JOINT_ARRAY);
        assert_eq!(sample.joint_count, 2);
        assert_eq!(
            sample.joints,
            vec![
                JointFields {
                    joint: JOINT0,
                    callback: 0x8002_0000,
                    mtx_calc: 0x8002_1000,
                },
                JointFields {
                    joint: JOINT1,
                    callback: 0,
                    mtx_calc: 0,
                },
            ]
        );
    }

    #[test]
    fn rejects_count_above_the_bounded_cap() {
        let mut memory = Memory::valid();
        memory.set(MODEL_DATA + MODEL_DATA_JOINT_COUNT, MAX_JOINTS + 1);
        assert!(read_joint_fields(&mut memory, MODEL_DATA)
            .unwrap_err()
            .contains("exceeds the bounded observer cap"));
    }

    #[test]
    fn rejects_null_array_and_invalid_joint_pointers() {
        let mut null_array = Memory::valid();
        null_array.set(MODEL_DATA + MODEL_DATA_JOINT_ARRAY, 0);
        assert!(read_joint_fields(&mut null_array, MODEL_DATA)
            .unwrap_err()
            .contains("joint array is null"));

        let mut bad = Memory::valid();
        bad.set(JOINT_ARRAY, 0x8000_0002);
        assert!(read_joint_fields(&mut bad, MODEL_DATA)
            .unwrap_err()
            .contains("invalid Wii pointer"));
    }

    #[test]
    fn rejects_joint_span_crossing_mem1_end() {
        let mut memory = Memory::valid();
        memory.set(JOINT_ARRAY, 0x817F_FFD0);
        assert!(read_joint_fields(&mut memory, MODEL_DATA)
            .unwrap_err()
            .contains("out of bounds"));
    }

    #[test]
    fn rejects_allstars_overlap_with_joint_evidence() {
        assert!(ensure_patch_disjoint(&[multigalaxy_patch::PatchWrite {
            address: INVALIDATE_MTX_CALC + 4,
            value: 0,
        }])
        .is_err());
        assert!(ensure_patch_disjoint(&[multigalaxy_patch::PatchWrite {
            address: ENABLE_JOINT_TRANSFORM,
            value: 0,
        }])
        .is_err());
        assert!(ensure_patch_disjoint(&[multigalaxy_patch::PatchWrite {
            address: 0x8020_0000,
            value: 0,
        }])
        .is_ok());
    }
}
