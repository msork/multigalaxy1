//! One-shot, read-only snapshot of the ResourceHolder/ResTable identities behind
//! Mario's existing PAL model.
//!
//! All addresses are guest addresses only, derived from the exact PAL RMGP01
//! Rev 0 DOL and pinned by code signatures in `verify_pal_layout`. No game
//! pointer is cached between calls, no host pointer is formed from emulated
//! memory, and no memory is written. The full Mario model chain is re-resolved
//! through `mario_model_observer` before and after the holder reads, and any
//! change rejects the whole observation.

use crate::{
    mario_model_observer, memory_inspection as mem,
    player_probe::{self, GuestMemory},
};
use multigalaxy_patch::{branch_target, parse_dol, SectionKind};

// ModelManager fields (PAL `getResourceHolder`/`getModelResourceHolder` and
// `ModelManager::initModelAndAnimation` stores).
const MODEL_MANAGER_ANM_HOLDER: u32 = 0x14;
const MODEL_MANAGER_MODEL_HOLDER: u32 = 0x20;

// ResourceHolder layout. Size 0x50 is the `__nw` argument in
// `ResourceHolderManager::createResourceHolder`; +0x00 is read by
// `MR::newJ3DModel` as the `ResTable*` passed to `ResTable::getRes`; +0x48 is
// the `JKRArchive*` store in the ResourceHolder constructor and the load in
// `initializeArc`. The PAL symbol map has no `__vt__14ResourceHolder`, so the
// holder is not treated as polymorphic and no vptr is checked on it.
const RESOURCE_HOLDER_RES_TABLE: u32 = 0x00;
const RESOURCE_HOLDER_ARCHIVE: u32 = 0x48;
const RESOURCE_HOLDER_SIZE: usize = 0x50;

// ResTable layout: constructor zeroes +0x00 and +0x04; `getRes(u32)` reads the
// entry array from +0x00 with 0x18-byte entries; `getResName(u32)` reads the
// entry name at +0x10; `getResIndex(char const*)` compares the entry hash at
// +0x14. Entry +0x00 is the resource pointer returned by `getRes`.
const RES_TABLE_ENTRIES: u32 = 0x00;
const RES_TABLE_COUNT: u32 = 0x04;
const RES_TABLE_HEADER_SIZE: usize = 0x08;
const RES_TABLE_ENTRY_SIZE: u32 = 0x18;
const ENTRY_RESOURCE: u32 = 0x00;
/// Maximum number of leading ResTable entries probed for the model-data entry.
/// The probe is a bounded, plan-checked read window, not a scan of the heap.
const ENTRY_PROBE_WINDOW: u32 = 16;

const GET_RESOURCE_HOLDER: u32 = 0x8016_9720;
const GET_RESOURCE_HOLDER_SIG: &[u8] = &[
    0x80, 0x83, 0x00, 0x14, 0x2C, 0x04, 0x00, 0x00, 0x40, 0x82, 0x00, 0x0C, 0x80, 0x63, 0x00, 0x20,
    0x4E, 0x80, 0x00, 0x20, 0x80, 0x64, 0x00, 0x6C, 0x4E, 0x80, 0x00, 0x20,
];
const GET_MODEL_RESOURCE_HOLDER: u32 = 0x8016_973C;
const GET_MODEL_RESOURCE_HOLDER_SIG: &[u8] = &[0x80, 0x63, 0x00, 0x20, 0x4E, 0x80, 0x00, 0x20];
const INIT_MODEL_ANIM_HOLDER_STORE: u32 = 0x8016_97B0;
const INIT_MODEL_ANIM_HOLDER_STORE_SIG: &[u8] = &[0x90, 0x83, 0x00, 0x20];
const INIT_MODEL_ANIM_NEW_J3DMODEL_CALL: u32 = 0x8016_97E4;
const INIT_MODEL_ANIM_NEW_J3DMODEL_CALL_SIG: &[u8] = &[0x48, 0x27, 0xFE, 0x85];
const NEW_J3D_MODEL_HOLDER_RES_TABLE_LOAD: u32 = 0x803E_9680;
const NEW_J3D_MODEL_HOLDER_RES_TABLE_LOAD_SIG: &[u8] = &[0x80, 0x63, 0x00, 0x00];
const NEW_J3D_MODEL_GETRES_CALL: u32 = 0x803E_9690;
const NEW_J3D_MODEL_GETRES_CALL_SIG: &[u8] = &[0x4B, 0xFB, 0xDD, 0x51];
const GET_RES_NAME_BY_INDEX: u32 = 0x803A_73BC;
const GET_RES_NAME_BY_INDEX_SIG: &[u8] = &[
    0x1C, 0x04, 0x00, 0x18, 0x80, 0x63, 0x00, 0x00, 0x7C, 0x63, 0x02, 0x14, 0x80, 0x63, 0x00, 0x10,
    0x4E, 0x80, 0x00, 0x20,
];
const GET_RES_UL: u32 = 0x803A_73D0;
const GET_RES_UL_SIG: &[u8] = &[
    0x1C, 0x04, 0x00, 0x18, 0x80, 0x63, 0x00, 0x00, 0x7C, 0x63, 0x00, 0x2E, 0x4E, 0x80, 0x00, 0x20,
];
const GET_RES_NAME: u32 = 0x803A_73E0;
const GET_RES_NAME_SIG: &[u8] = &[0x48, 0x00, 0x00, 0x88];
const RES_TABLE_CTOR: u32 = 0x803A_72EC;
const RES_TABLE_CTOR_SIG: &[u8] = &[
    0x38, 0x00, 0x00, 0x00, 0x90, 0x03, 0x00, 0x00, 0x90, 0x03, 0x00, 0x04, 0x4E, 0x80, 0x00, 0x20,
];
const RESOURCE_HOLDER_CTOR_ARCHIVE_STORE: u32 = 0x803A_5EF8;
const RESOURCE_HOLDER_CTOR_ARCHIVE_STORE_SIG: &[u8] = &[0x90, 0x7E, 0x00, 0x48];
const CREATE_RESOURCE_HOLDER_NW: u32 = 0x803A_6F60;
const CREATE_RESOURCE_HOLDER_NW_SIG: &[u8] = &[
    0x38, 0x60, 0x00, 0x50, 0x48, 0x06, 0x2B, 0xB1, 0x2C, 0x03, 0x00, 0x00, 0x41, 0x82, 0x00, 0x0C,
];
const ADD_ENTRY_ASSIGN: u32 = 0x803A_72A8;
const ADD_ENTRY_ASSIGN_SIG: &[u8] = &[
    0x80, 0xE4, 0x00, 0x00, 0x80, 0xC4, 0x00, 0x04, 0x80, 0xA4, 0x00, 0x08, 0x80, 0x04, 0x00, 0x0C,
    0x90, 0xE3, 0x00, 0x00, 0x90, 0xC3, 0x00, 0x04, 0x90, 0xA3, 0x00, 0x08, 0x90, 0x03, 0x00, 0x0C,
];
const REMOVE_IF_EQUAL_HEAP_CMP: u32 = 0x803A_6BFC;
const REMOVE_IF_EQUAL_HEAP_CMP_SIG: &[u8] = &[0x80, 0x7D, 0x00, 0x0C, 0x7C, 0x03, 0xE0, 0x40];
const FIND_HASH_CMP: u32 = 0x803A_7150;
const FIND_HASH_CMP_SIG: &[u8] = &[0x80, 0x1E, 0x00, 0x08, 0x7C, 0x00, 0x18, 0x40];

const RESOURCE_CODE_SIGNATURES: &[(u32, &[u8])] = &[
    (GET_RESOURCE_HOLDER, GET_RESOURCE_HOLDER_SIG),
    (GET_MODEL_RESOURCE_HOLDER, GET_MODEL_RESOURCE_HOLDER_SIG),
    (
        INIT_MODEL_ANIM_HOLDER_STORE,
        INIT_MODEL_ANIM_HOLDER_STORE_SIG,
    ),
    (
        INIT_MODEL_ANIM_NEW_J3DMODEL_CALL,
        INIT_MODEL_ANIM_NEW_J3DMODEL_CALL_SIG,
    ),
    (
        NEW_J3D_MODEL_HOLDER_RES_TABLE_LOAD,
        NEW_J3D_MODEL_HOLDER_RES_TABLE_LOAD_SIG,
    ),
    (NEW_J3D_MODEL_GETRES_CALL, NEW_J3D_MODEL_GETRES_CALL_SIG),
    (GET_RES_NAME_BY_INDEX, GET_RES_NAME_BY_INDEX_SIG),
    (GET_RES_UL, GET_RES_UL_SIG),
    (GET_RES_NAME, GET_RES_NAME_SIG),
    (RES_TABLE_CTOR, RES_TABLE_CTOR_SIG),
    (
        RESOURCE_HOLDER_CTOR_ARCHIVE_STORE,
        RESOURCE_HOLDER_CTOR_ARCHIVE_STORE_SIG,
    ),
    (CREATE_RESOURCE_HOLDER_NW, CREATE_RESOURCE_HOLDER_NW_SIG),
    (ADD_ENTRY_ASSIGN, ADD_ENTRY_ASSIGN_SIG),
    (REMOVE_IF_EQUAL_HEAP_CMP, REMOVE_IF_EQUAL_HEAP_CMP_SIG),
    (FIND_HASH_CMP, FIND_HASH_CMP_SIG),
];

const EXPECTED_SYMBOLS: &[(&str, &str)] = &[
    (
        "ModelManager::getResourceHolder",
        "getResourceHolder__12ModelManagerCFv = .text:0x80169720; // type:function size:0x1C scope:global align:4",
    ),
    (
        "ModelManager::getModelResourceHolder",
        "getModelResourceHolder__12ModelManagerCFv = .text:0x8016973C; // type:function size:0x8 scope:global align:4",
    ),
    (
        "ModelManager::initModelAndAnimation",
        "initModelAndAnimation__12ModelManagerFP14ResourceHolderPCcP14ResourceHolder10J3DMdlFlag = .text:0x8016979C; // type:function size:0x94 scope:global align:4",
    ),
    (
        "newJ3DModel",
        "newJ3DModel__2MRFPC14ResourceHolderPCc10J3DMdlFlag = .text:0x803E9668; // type:function size:0x440 scope:global align:4",
    ),
    (
        "ResourceHolder constructor",
        "__ct__14ResourceHolderFR10JKRArchive = .text:0x803A5E84; // type:function size:0x12C scope:global align:4",
    ),
    (
        "ResourceHolderManager::createAndAdd",
        "createAndAdd__21ResourceHolderManagerFPCcP7JKRHeap = .text:0x803A6A58; // type:function size:0x34 scope:global align:4",
    ),
    (
        "ResourceHolderManager::createAndAddStationed",
        "createAndAddStationed__21ResourceHolderManagerFPCc = .text:0x803A6A8C; // type:function size:0x44 scope:global align:4",
    ),
    (
        "ResourceHolderManager::removeIfIsEqualHeap",
        "removeIfIsEqualHeap__21ResourceHolderManagerFP7JKRHeap = .text:0x803A6BCC; // type:function size:0x148 scope:global align:4",
    ),
    (
        "ResourceHolderManager::createAndAddInner",
        "createAndAddInner__21ResourceHolderManagerFPCcPFPcUlPCc_bPFPCcP24CreateResourceHolderArgs_v = .text:0x803A6DE4; // type:function size:0xB8 scope:global align:4",
    ),
    (
        "ResourceHolderManager::createResourceHolder",
        "createResourceHolder__21ResourceHolderManagerFPCcP24CreateResourceHolderArgs = .text:0x803A6F28; // type:function size:0x80 scope:global align:4",
    ),
    (
        "ResourceHolderManager::add",
        "add__21ResourceHolderManagerFPCcRC24CreateResourceHolderArgs = .text:0x803A7070; // type:function size:0x94 scope:global align:4",
    ),
    (
        "ResourceHolderManager::find",
        "find__21ResourceHolderManagerFPCc = .text:0x803A7104; // type:function size:0x84 scope:global align:4",
    ),
    (
        "ResTable constructor",
        "__ct__8ResTableFv = .text:0x803A72EC; // type:function size:0x10 scope:global align:4",
    ),
    (
        "ResTable::getResName",
        "getResName__8ResTableCFUl = .text:0x803A73BC; // type:function size:0x14 scope:global align:4",
    ),
    (
        "ResTable::getRes(u32)",
        "getRes__8ResTableCFUl = .text:0x803A73D0; // type:function size:0x10 scope:global align:4",
    ),
    (
        "ResTable::getRes(char const*)",
        "getRes__8ResTableCFPCc = .text:0x803A73E0; // type:function size:0x4 scope:global align:4",
    ),
];

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct HolderIdentities {
    pub model_resource_holder: u32,
    pub anm_resource_holder: Option<u32>,
    pub holder_res_table: u32,
    pub res_table_entries: u32,
    pub res_table_count: u32,
    pub holder_archive: Option<u32>,
    /// Index of the ResTable entry whose resource pointer equals the sampled
    /// `J3DModelData`, if it lies within the bounded probe window.
    pub model_data_entry_index: Option<u32>,
    pub entry_probe_window: u32,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ResourceHolderSample {
    pub base: mario_model_observer::ModelSample,
    pub holder: HolderIdentities,
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

fn read_pointer(
    memory: &mut impl GuestMemory,
    address: u32,
    label: &str,
    nullable: bool,
) -> Result<Option<u32>, String> {
    let raw = read_word(memory, address)?;
    if raw == 0 {
        return if nullable {
            Ok(None)
        } else {
            Err(format!("{label} is null"))
        };
    }
    if raw & 3 != 0 || !player_probe::is_guest_ram_range(raw, 4) {
        return Err(format!("{label} has invalid Wii pointer 0x{raw:08x}"));
    }
    if mem::plan_read(raw, 4).is_err() {
        return Err(format!(
            "{label} points outside the validated read-only MEM1 window: 0x{raw:08x}"
        ));
    }
    Ok(Some(raw))
}

fn required_pointer(
    memory: &mut impl GuestMemory,
    address: u32,
    label: &str,
) -> Result<u32, String> {
    read_pointer(memory, address, label, false)?.ok_or_else(|| format!("{label} is null"))
}

/// Read the archive word without dereferencing it. A live holder may hold the
/// pre-mount sentinel 0xFFFFFFFF (the creation path constructs with -1), so
/// only range-check a real pointer.
fn read_archive_word(memory: &mut impl GuestMemory, address: u32) -> Result<Option<u32>, String> {
    let raw = read_word(memory, address)?;
    if raw == 0 || raw == 0xFFFF_FFFF {
        return Ok(None);
    }
    if raw & 3 != 0 || !player_probe::is_guest_ram_range(raw, 4) {
        return Err(format!(
            "ResourceHolder archive field has invalid Wii pointer 0x{raw:08x}"
        ));
    }
    Ok(Some(raw))
}

/// Read the holder/ResTable identities reachable from a validated ModelManager
/// and model-data pointer. Pure bounded reads; the caller re-resolves the
/// model chain afterwards and rejects any change.
pub fn read_holder_identities(
    memory: &mut impl GuestMemory,
    model_manager: u32,
    model_data: u32,
) -> Result<HolderIdentities, String> {
    let model_holder = required_pointer(
        memory,
        checked_field(
            model_manager,
            MODEL_MANAGER_MODEL_HOLDER,
            "ModelManager::mModelResourceHolder",
        )?,
        "model ResourceHolder",
    )?;
    let anm_holder = read_pointer(
        memory,
        checked_field(
            model_manager,
            MODEL_MANAGER_ANM_HOLDER,
            "ModelManager::mAnmResourceHolder",
        )?,
        "animation ResourceHolder",
        true,
    )?;
    mem::plan_read(model_holder, RESOURCE_HOLDER_SIZE).map_err(|error| {
        format!("ResourceHolder object at 0x{model_holder:08x} is out of bounds: {error}")
    })?;
    let res_table = required_pointer(
        memory,
        checked_field(
            model_holder,
            RESOURCE_HOLDER_RES_TABLE,
            "ResourceHolder::mResTable",
        )?,
        "ResourceHolder ResTable",
    )?;
    let archive = read_archive_word(
        memory,
        checked_field(
            model_holder,
            RESOURCE_HOLDER_ARCHIVE,
            "ResourceHolder::mArchive",
        )?,
    )?;
    mem::plan_read(res_table, RES_TABLE_HEADER_SIZE).map_err(|error| {
        format!("ResTable object at 0x{res_table:08x} is out of bounds: {error}")
    })?;
    let entries = required_pointer(
        memory,
        checked_field(res_table, RES_TABLE_ENTRIES, "ResTable::mEntries")?,
        "ResTable entry array",
    )?;
    let count = read_word(
        memory,
        checked_field(res_table, RES_TABLE_COUNT, "ResTable::mCount")?,
    )?;
    let window = count.min(ENTRY_PROBE_WINDOW);
    let mut model_data_entry_index = None;
    for index in 0..window {
        let entry = entries
            .checked_add(
                index
                    .checked_mul(RES_TABLE_ENTRY_SIZE)
                    .ok_or("entry index overflow")?,
            )
            .ok_or("ResTable entry address overflow")?;
        mem::plan_read(entry, RES_TABLE_ENTRY_SIZE as usize)
            .map_err(|error| format!("ResTable entry {index} is out of bounds: {error}"))?;
        let resource = read_word(
            memory,
            checked_field(entry, ENTRY_RESOURCE, "ResTable entry resource")?,
        )
        .map_err(|error| format!("ResTable entry {index}: {error}"))?;
        if resource == model_data {
            model_data_entry_index = Some(index);
            break;
        }
    }
    Ok(HolderIdentities {
        model_resource_holder: model_holder,
        anm_resource_holder: anm_holder,
        holder_res_table: res_table,
        res_table_entries: entries,
        res_table_count: count,
        holder_archive: archive,
        model_data_entry_index,
        entry_probe_window: window,
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
        && a.matrix_buffer == b.matrix_buffer
        && a.scene_heap_napa == b.scene_heap_napa
        && a.scene_heap_gddr3 == b.scene_heap_gddr3
        && a.current_heap == b.current_heap
}

/// Validate local DOL/symbol identity and exact code signatures used to derive
/// the holder/ResTable chain. The caller separately validates AllStars overlap.
pub fn verify_pal_layout(dol: &[u8], symbols: &[u8]) -> Result<(), String> {
    player_probe::verify_pal_inputs(dol, symbols)?;
    let text = std::str::from_utf8(symbols)
        .map_err(|error| format!("Petari symbol map is not UTF-8: {error}"))?;
    for (label, expected) in EXPECTED_SYMBOLS {
        if !text.lines().any(|line| line.trim() == *expected) {
            return Err(format!("PAL resource-holder symbol mismatch for {label}"));
        }
    }
    for (address, expected) in RESOURCE_CODE_SIGNATURES {
        if dol_text_bytes(dol, *address, expected.len())? != *expected {
            return Err(format!(
                "PAL resource-holder DOL signature mismatch at 0x{address:08x}"
            ));
        }
    }
    let new_j3d_model_call = u32::from_be_bytes(
        INIT_MODEL_ANIM_NEW_J3DMODEL_CALL_SIG
            .try_into()
            .expect("four-byte branch signature"),
    );
    if branch_target(new_j3d_model_call, INIT_MODEL_ANIM_NEW_J3DMODEL_CALL) != Some(0x803E_9668) {
        return Err(
            "PAL initModelAndAnimation no longer calls the verified newJ3DModel factory".into(),
        );
    }
    let get_res_call = u32::from_be_bytes(
        NEW_J3D_MODEL_GETRES_CALL_SIG
            .try_into()
            .expect("four-byte branch signature"),
    );
    if branch_target(get_res_call, NEW_J3D_MODEL_GETRES_CALL) != Some(0x803A_73E0) {
        return Err("PAL newJ3DModel no longer calls the verified ResTable::getRes(name)".into());
    }
    let get_res_name = u32::from_be_bytes(
        GET_RES_NAME_SIG
            .try_into()
            .expect("four-byte branch signature"),
    );
    if branch_target(get_res_name, GET_RES_NAME) != Some(0x803A_7468) {
        return Err(
            "PAL ResTable::getRes(name) no longer tail-calls the verified index lookup".into(),
        );
    }
    Ok(())
}

pub fn verify_live_signatures(memory: &mut impl GuestMemory, dol: &[u8]) -> Result<(), String> {
    for (address, expected) in RESOURCE_CODE_SIGNATURES {
        if dol_text_bytes(dol, *address, expected.len())? != *expected {
            return Err(format!(
                "local PAL resource-holder signature changed at 0x{address:08x}"
            ));
        }
        if read_guest(memory, *address, expected.len())? != *expected {
            return Err(format!(
                "live PAL resource-holder signature mismatch at 0x{address:08x}"
            ));
        }
    }
    Ok(())
}

pub fn ensure_patch_disjoint(writes: &[multigalaxy_patch::PatchWrite]) -> Result<(), String> {
    for (address, signature) in RESOURCE_CODE_SIGNATURES {
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
                "required AllStars patch overlaps resource-holder evidence at 0x{start:08x}"
            ));
        }
    }
    Ok(())
}

pub fn observe(memory: &mut impl GuestMemory, dol: &[u8]) -> Result<ResourceHolderSample, String> {
    verify_live_signatures(memory, dol)?;
    let before = mario_model_observer::observe(memory, dol)?;
    let holder = read_holder_identities(memory, before.model_manager, before.model_data)?;
    let after = mario_model_observer::observe(memory, dol)?;
    if !same_model_tuple(&before, &after) {
        return Err(
            "Mario model chain or scene/heap identity changed during resource-holder observation"
                .into(),
        );
    }
    verify_live_signatures(memory, dol)?;
    Ok(ResourceHolderSample {
        base: before,
        holder,
    })
}

fn dol_text_bytes(dol: &[u8], address: u32, length: usize) -> Result<&[u8], String> {
    let info = parse_dol(dol).map_err(|error| format!("invalid PAL DOL: {error}"))?;
    let end = u64::from(address)
        .checked_add(length as u64)
        .ok_or("PAL resource-holder signature range overflow")?;
    let section = info
        .sections
        .iter()
        .find(|section| {
            section.kind == SectionKind::Text
                && address >= section.address
                && end <= u64::from(section.address) + u64::from(section.size)
        })
        .ok_or_else(|| {
            format!(
                "PAL resource-holder signature at 0x{address:08x} is outside a DOL text section"
            )
        })?;
    let offset =
        usize::try_from(u64::from(section.file_offset) + u64::from(address - section.address))
            .map_err(|_| "PAL resource-holder signature file offset overflow")?;
    let end = offset
        .checked_add(length)
        .ok_or("PAL resource-holder signature file span overflow")?;
    dol.get(offset..end)
        .ok_or_else(|| "PAL DOL is truncated at resource-holder signature".into())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::BTreeMap;

    const MODEL_MANAGER: u32 = 0x8000_7000;
    const MODEL_DATA: u32 = 0x8000_A000;
    const HOLDER: u32 = 0x8000_C000;
    const ANM_HOLDER: u32 = 0x8000_D000;
    const RES_TABLE: u32 = 0x8000_E000;
    const ENTRIES: u32 = 0x8000_F000;
    const ARCHIVE: u32 = 0x8001_1000;

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
            memory.set(MODEL_MANAGER + MODEL_MANAGER_MODEL_HOLDER, HOLDER);
            memory.set(MODEL_MANAGER + MODEL_MANAGER_ANM_HOLDER, ANM_HOLDER);
            memory.set(HOLDER + RESOURCE_HOLDER_RES_TABLE, RES_TABLE);
            memory.set(HOLDER + RESOURCE_HOLDER_ARCHIVE, ARCHIVE);
            memory.set(RES_TABLE + RES_TABLE_ENTRIES, ENTRIES);
            memory.set(RES_TABLE + RES_TABLE_COUNT, 3);
            // entry 0: unrelated resource; entry 1: the sampled model data
            memory.set(ENTRIES + ENTRY_RESOURCE, 0x8001_2000);
            memory.set(ENTRIES + RES_TABLE_ENTRY_SIZE + ENTRY_RESOURCE, MODEL_DATA);
            memory.set(
                ENTRIES + 2 * RES_TABLE_ENTRY_SIZE + ENTRY_RESOURCE,
                0x8001_3000,
            );
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
    fn resolves_holder_restable_and_model_data_entry() {
        let mut memory = Memory::valid();
        let holder = read_holder_identities(&mut memory, MODEL_MANAGER, MODEL_DATA).unwrap();
        assert_eq!(holder.model_resource_holder, HOLDER);
        assert_eq!(holder.anm_resource_holder, Some(ANM_HOLDER));
        assert_eq!(holder.holder_res_table, RES_TABLE);
        assert_eq!(holder.res_table_entries, ENTRIES);
        assert_eq!(holder.res_table_count, 3);
        assert_eq!(holder.holder_archive, Some(ARCHIVE));
        assert_eq!(holder.model_data_entry_index, Some(1));
        assert_eq!(holder.entry_probe_window, 3);
    }

    #[test]
    fn supports_null_anm_holder_and_pre_mount_archive_sentinel() {
        let mut memory = Memory::valid();
        memory.set(MODEL_MANAGER + MODEL_MANAGER_ANM_HOLDER, 0);
        memory.set(HOLDER + RESOURCE_HOLDER_ARCHIVE, 0xFFFF_FFFF);
        let holder = read_holder_identities(&mut memory, MODEL_MANAGER, MODEL_DATA).unwrap();
        assert_eq!(holder.anm_resource_holder, None);
        assert_eq!(holder.holder_archive, None);
    }

    #[test]
    fn reports_no_entry_when_model_data_is_outside_probe_window() {
        let mut memory = Memory::valid();
        memory.set(RES_TABLE + RES_TABLE_COUNT, 32);
        for index in 3..ENTRY_PROBE_WINDOW {
            memory.set(
                ENTRIES + index * RES_TABLE_ENTRY_SIZE + ENTRY_RESOURCE,
                0x8001_2000 + index,
            );
        }
        let holder = read_holder_identities(&mut memory, MODEL_MANAGER, 0x8001_9000).unwrap();
        assert_eq!(holder.model_data_entry_index, None);
        assert_eq!(holder.entry_probe_window, ENTRY_PROBE_WINDOW);
    }

    #[test]
    fn rejects_null_or_invalid_holder_and_restable_pointers() {
        let mut null_holder = Memory::valid();
        null_holder.set(MODEL_MANAGER + MODEL_MANAGER_MODEL_HOLDER, 0);
        assert!(
            read_holder_identities(&mut null_holder, MODEL_MANAGER, MODEL_DATA)
                .unwrap_err()
                .contains("model ResourceHolder is null")
        );

        let mut bad = Memory::valid();
        bad.set(MODEL_MANAGER + MODEL_MANAGER_MODEL_HOLDER, 0x8000_0002);
        assert!(read_holder_identities(&mut bad, MODEL_MANAGER, MODEL_DATA)
            .unwrap_err()
            .contains("invalid Wii pointer"));

        let mut null_table = Memory::valid();
        null_table.set(HOLDER + RESOURCE_HOLDER_RES_TABLE, 0);
        assert!(
            read_holder_identities(&mut null_table, MODEL_MANAGER, MODEL_DATA)
                .unwrap_err()
                .contains("ResTable is null")
        );

        let mut bad_archive = Memory::valid();
        bad_archive.set(HOLDER + RESOURCE_HOLDER_ARCHIVE, 0x8000_0006);
        assert!(
            read_holder_identities(&mut bad_archive, MODEL_MANAGER, MODEL_DATA)
                .unwrap_err()
                .contains("archive field")
        );
    }

    #[test]
    fn rejects_holder_span_crossing_mem1_end() {
        let mut memory = Memory::valid();
        memory.set(MODEL_MANAGER + MODEL_MANAGER_MODEL_HOLDER, 0x817F_FFE0);
        assert!(
            read_holder_identities(&mut memory, MODEL_MANAGER, MODEL_DATA)
                .unwrap_err()
                .contains("ResourceHolder object")
        );
    }

    #[test]
    fn rejects_allstars_overlap_with_resource_holder_evidence() {
        assert!(ensure_patch_disjoint(&[multigalaxy_patch::PatchWrite {
            address: GET_RES_UL + 4,
            value: 0,
        }])
        .is_err());
        assert!(ensure_patch_disjoint(&[multigalaxy_patch::PatchWrite {
            address: GET_MODEL_RESOURCE_HOLDER,
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
