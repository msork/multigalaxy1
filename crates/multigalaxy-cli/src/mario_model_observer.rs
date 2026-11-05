//! One-shot, read-only snapshot of Mario's existing PAL model ownership chain.
//!
//! Addresses are guest addresses only. No game pointer is cached between calls,
//! no host pointer is formed from emulated memory, and no memory is written.

use crate::{
    heap_observer::{self, HeapSample},
    memory_inspection as mem,
    player_probe::{self, GuestMemory},
};
use multigalaxy_patch::{branch_target, parse_dol, SectionKind};

const GAME_SYSTEM: u32 = 0x806A_1228;
const GAME_SYSTEM_CONTROLLER: u32 = 0x24;
const ACTIVE_SCENE: u32 = 0xAC;
const SCENE_OBJECT_HOLDER: u32 = 0x10;
const MARIO_HOLDER_SLOT: u32 = 0x50;
const MARIO_HOLDER_ACTOR: u32 = 0x0C;
const LIVE_ACTOR_MODEL_MANAGER: u32 = 0x48;
const MODEL_MANAGER_XANIME_PLAYER: u32 = 0x18;
const MODEL_MANAGER_MODEL: u32 = 0x1C;
const XANIME_PLAYER_MODEL: u32 = 0x00;
const MODEL_DATA_OFFSET: u32 = 0x04;
const MODEL_MTX_BUFFER: u32 = 0x84;

const MARIO_ACTOR_VPTR: u32 = 0x805A_399C;
const J3D_MODEL_X_VPTR: u32 = 0x805A_31B0;
#[cfg(test)]
const J3D_MODEL_VPTR: u32 = 0x805D_4F38;
const J3D_MODEL_DATA_VPTR: u32 = 0x805D_4F18;
const J3D_MTX_BUFFER_VPTR: u32 = 0x805D_4F5C;

const GET_J3D_MODEL: u32 = 0x8016_9744;
const GET_J3D_MODEL_SIG: &[u8] = &[
    0x80, 0x83, 0x00, 0x18, 0x2C, 0x04, 0x00, 0x00, 0x40, 0x82, 0x00, 0x0C, 0x80, 0x63, 0x00, 0x1C,
    0x4E, 0x80, 0x00, 0x20, 0x80, 0x64, 0x00, 0x00, 0x4E, 0x80, 0x00, 0x20,
];
const GET_J3D_MODEL_DATA: u32 = 0x8016_9760;
const GET_J3D_MODEL_DATA_SIG: &[u8] = &[
    0x94, 0x21, 0xFF, 0xF0, 0x7C, 0x08, 0x02, 0xA6, 0x90, 0x01, 0x00, 0x14, 0x4B, 0xFF, 0xFF, 0xD9,
    0x80, 0x01, 0x00, 0x14, 0x80, 0x63, 0x00, 0x04, 0x7C, 0x08, 0x03, 0xA6, 0x38, 0x21, 0x00, 0x10,
    0x4E, 0x80, 0x00, 0x20,
];
const MODEL_X_VPTR_STORE: u32 = 0x802A_708C;
const MODEL_X_VPTR_STORE_SIG: &[u8] = &[
    0x3C, 0xA0, 0x80, 0x5A, 0x38, 0x61, 0x00, 0x48, 0x38, 0xA5, 0x31, 0xB0, 0x38, 0x80, 0x00, 0x00,
    0x90, 0xBC, 0x00, 0x00,
];
const ENTRY_MODEL_DATA_STORE: u32 = 0x8043_1130;
const ENTRY_MODEL_DATA_STORE_SIG: &[u8] = &[0x90, 0x83, 0x00, 0x04];
const ENTRY_MTX_BUFFER_STORE: u32 = 0x8043_1168;
const ENTRY_MTX_BUFFER_STORE_SIG: &[u8] = &[0x93, 0xBE, 0x00, 0x84];
const MARIO_ACTOR_VPTR_STORE: u32 = 0x802A_E888;
const MARIO_ACTOR_VPTR_STORE_SIG: &[u8] = &[
    0x3C, 0x80, 0x80, 0x5A, 0x38, 0x00, 0xFF, 0xFF, 0x38, 0x84, 0x39, 0x9C, 0x90, 0x1F, 0x01, 0xB0,
    0x7F, 0xE3, 0xFB, 0x78, 0x90, 0x9F, 0x00, 0x00,
];
const ACTOR_MANAGER_STORE: u32 = 0x8016_59FC;
const ACTOR_MANAGER_STORE_SIG: &[u8] = &[0x90, 0x7C, 0x00, 0x48];
const ACTOR_MANAGER_LOAD: u32 = 0x8016_5A40;
const ACTOR_MANAGER_LOAD_SIG: &[u8] = &[0x80, 0x7C, 0x00, 0x48];
const XANIME_MODEL_STORE: u32 = 0x8001_AED8;
const XANIME_MODEL_STORE_SIG: &[u8] = &[0x93, 0xBF, 0x00, 0x00];
const NEW_XANIME_PLAYER_CALL_MODEL_FACTORY: u32 = 0x803E_7EFC;
const NEW_XANIME_PLAYER_CALL_MODEL_FACTORY_SIG: &[u8] = &[0x48, 0x00, 0x17, 0x6D];

const EXPECTED_SYMBOLS: &[(&str, &str)] = &[
    (
        "getJ3DModel__12ModelManagerCFv",
        "getJ3DModel__12ModelManagerCFv = .text:0x80169744; // type:function size:0x1C scope:global align:4",
    ),
    (
        "getJ3DModelData__12ModelManagerCFv",
        "getJ3DModelData__12ModelManagerCFv = .text:0x80169760; // type:function size:0x24 scope:global align:4",
    ),
    (
        "MarioActor constructor",
        "__ct__10MarioActorFPCc = .text:0x802AE86C; // type:function size:0x37C scope:global align:4",
    ),
    (
        "LiveActor model-manager initializer",
        "initModelManagerWithAnm__9LiveActorFPCcPCcb = .text:0x801659C4; // type:function size:0xA0 scope:global align:4",
    ),
    (
        "XanimePlayer constructor",
        "__ct__12XanimePlayerFP8J3DModelP19XanimeResourceTable = .text:0x8001AE90; // type:function size:0x144 scope:global align:4",
    ),
    (
        "newXanimePlayer",
        "newXanimePlayer__2MRFPC14ResourceHolderPCcPC14ResourceHolder10J3DMdlFlagP19XanimeResourceTable = .text:0x803E7EE0; // type:function size:0x58 scope:global align:4",
    ),
    (
        "newJ3DModel",
        "newJ3DModel__2MRFPC14ResourceHolderPCc10J3DMdlFlag = .text:0x803E9668; // type:function size:0x440 scope:global align:4",
    ),
    (
        "MarioActor vtable",
        "__vt__10MarioActor = .data:0x805A399C; // type:object size:0x124 scope:global align:4",
    ),
    (
        "J3DModelX vtable",
        "__vt__9J3DModelX = .data:0x805A31B0; // type:object size:0x28 scope:global align:4",
    ),
    (
        "J3DModel vtable",
        "__vt__8J3DModel = .data:0x805D4F38; // type:object size:0x24 scope:global align:4",
    ),
    (
        "J3DModelData vtable",
        "__vt__12J3DModelData = .data:0x805D4F18; // type:object size:0xC scope:global align:4",
    ),
    (
        "J3DMtxBuffer vtable",
        "__vt__12J3DMtxBuffer = .data:0x805D4F5C; // type:object size:0xC scope:global align:4",
    ),
    (
        "J3DModel::entryModelData",
        "entryModelData__8J3DModelFP12J3DModelDataUlUl = .text:0x8043111C; // type:function size:0x170 scope:global align:4",
    ),
];

const MODEL_CODE_SIGNATURES: &[(u32, &[u8])] = &[
    (GET_J3D_MODEL, GET_J3D_MODEL_SIG),
    (GET_J3D_MODEL_DATA, GET_J3D_MODEL_DATA_SIG),
    (MODEL_X_VPTR_STORE, MODEL_X_VPTR_STORE_SIG),
    (ENTRY_MODEL_DATA_STORE, ENTRY_MODEL_DATA_STORE_SIG),
    (ENTRY_MTX_BUFFER_STORE, ENTRY_MTX_BUFFER_STORE_SIG),
    (MARIO_ACTOR_VPTR_STORE, MARIO_ACTOR_VPTR_STORE_SIG),
    (ACTOR_MANAGER_STORE, ACTOR_MANAGER_STORE_SIG),
    (ACTOR_MANAGER_LOAD, ACTOR_MANAGER_LOAD_SIG),
    (XANIME_MODEL_STORE, XANIME_MODEL_STORE_SIG),
    (
        NEW_XANIME_PLAYER_CALL_MODEL_FACTORY,
        NEW_XANIME_PLAYER_CALL_MODEL_FACTORY_SIG,
    ),
];

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct ActorChain {
    game_system: u32,
    scene_controller: u32,
    scene: u32,
    scene_object_holder: u32,
    mario_holder: u32,
    mario_actor: u32,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct ModelPointers {
    mario_actor: u32,
    mario_actor_vptr: u32,
    model_manager: u32,
    xanime_player: Option<u32>,
    model: u32,
    model_vptr: u32,
    model_data: u32,
    model_data_vptr: u32,
    matrix_buffer: u32,
    matrix_buffer_vptr: u32,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ModelSample {
    pub game_system: u32,
    pub scene_controller: u32,
    pub scene: u32,
    pub scene_object_holder: u32,
    pub actor_group: u32,
    pub mario_holder: u32,
    pub mario_actor: u32,
    pub model_manager: u32,
    pub xanime_player: Option<u32>,
    /// Dynamic J3DModelX object; ModelManager's API returns it as J3DModel*.
    pub model: u32,
    pub model_vptr: u32,
    pub model_data: u32,
    pub model_data_vptr: u32,
    pub matrix_buffer: u32,
    pub matrix_buffer_vptr: u32,
    pub scene_heap_napa: Option<u32>,
    pub scene_heap_gddr3: Option<u32>,
    /// This is the heap active at observation time, not the model's allocation owner.
    pub current_heap: Option<u32>,
    pub current_heap_matches_scene_napa: Option<bool>,
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

fn resolve_actor_chain(memory: &mut impl GuestMemory) -> Result<ActorChain, String> {
    if read_guest(memory, mem::GUEST_MEM1_BASE, 6)? != b"RMGP01" {
        return Err("live game identity is not PAL RMGP01".into());
    }
    let game_system = required_pointer(memory, GAME_SYSTEM, "GameSystem singleton")?;
    let scene_controller = required_pointer(
        memory,
        checked_field(
            game_system,
            GAME_SYSTEM_CONTROLLER,
            "GameSystem::mSceneController",
        )?,
        "scene controller",
    )?;
    let scene = required_pointer(
        memory,
        checked_field(scene_controller, ACTIVE_SCENE, "active scene")?,
        "active scene",
    )?;
    let scene_object_holder = required_pointer(
        memory,
        checked_field(scene, SCENE_OBJECT_HOLDER, "Scene::mSceneObjHolder")?,
        "SceneObjHolder",
    )?;
    let mario_holder = required_pointer(
        memory,
        checked_field(scene_object_holder, MARIO_HOLDER_SLOT, "MarioHolder slot")?,
        "MarioHolder",
    )?;
    let mario_actor = required_pointer(
        memory,
        checked_field(mario_holder, MARIO_HOLDER_ACTOR, "MarioHolder::mActor")?,
        "MarioActor",
    )?;
    Ok(ActorChain {
        game_system,
        scene_controller,
        scene,
        scene_object_holder,
        mario_holder,
        mario_actor,
    })
}

fn read_model_pointers(memory: &mut impl GuestMemory, actor: u32) -> Result<ModelPointers, String> {
    let mario_actor_vptr = read_word(memory, actor)?;
    if mario_actor_vptr != MARIO_ACTOR_VPTR {
        return Err(format!(
            "MarioActor vptr mismatch at 0x{actor:08x}: 0x{mario_actor_vptr:08x}"
        ));
    }
    let model_manager = required_pointer(
        memory,
        checked_field(actor, LIVE_ACTOR_MODEL_MANAGER, "LiveActor::mModelManager")?,
        "ModelManager",
    )?;
    let xanime_player = read_pointer(
        memory,
        checked_field(
            model_manager,
            MODEL_MANAGER_XANIME_PLAYER,
            "ModelManager::mXanimePlayer",
        )?,
        "XanimePlayer",
        true,
    )?;
    let model_address = if let Some(player) = xanime_player {
        checked_field(player, XANIME_PLAYER_MODEL, "XanimePlayer::mModel")?
    } else {
        checked_field(model_manager, MODEL_MANAGER_MODEL, "ModelManager::mModel")?
    };
    let model = required_pointer(memory, model_address, "J3DModel")?;
    let model_vptr = read_word(memory, model)?;
    if model_vptr != J3D_MODEL_X_VPTR {
        return Err(format!(
            "Mario model dynamic vptr mismatch at 0x{model:08x}: 0x{model_vptr:08x}"
        ));
    }
    let model_data = required_pointer(
        memory,
        checked_field(model, MODEL_DATA_OFFSET, "J3DModel::mModelData")?,
        "J3DModelData",
    )?;
    let model_data_vptr = read_word(memory, model_data)?;
    if model_data_vptr != J3D_MODEL_DATA_VPTR {
        return Err(format!(
            "J3DModelData vptr mismatch at 0x{model_data:08x}: 0x{model_data_vptr:08x}"
        ));
    }
    let matrix_buffer = required_pointer(
        memory,
        checked_field(model, MODEL_MTX_BUFFER, "J3DModel::mMtxBuffer")?,
        "J3DMtxBuffer",
    )?;
    let matrix_buffer_vptr = read_word(memory, matrix_buffer)?;
    if matrix_buffer_vptr != J3D_MTX_BUFFER_VPTR {
        return Err(format!(
            "J3DMtxBuffer vptr mismatch at 0x{matrix_buffer:08x}: 0x{matrix_buffer_vptr:08x}"
        ));
    }
    Ok(ModelPointers {
        mario_actor: actor,
        mario_actor_vptr,
        model_manager,
        xanime_player,
        model,
        model_vptr,
        model_data,
        model_data_vptr,
        matrix_buffer,
        matrix_buffer_vptr,
    })
}

fn same_scene_heap(a: HeapSample, b: HeapSample) -> bool {
    a.scene == b.scene
        && a.group == b.group
        && a.watcher == b.watcher
        && a.scene_heap_napa == b.scene_heap_napa
        && a.scene_heap_gddr3 == b.scene_heap_gddr3
        && a.sampled_current_heap == b.sampled_current_heap
}

fn sample_once(memory: &mut impl GuestMemory) -> Result<ModelSample, String> {
    let heap_before = heap_observer::observe(memory)?;
    let chain_before = resolve_actor_chain(memory)?;
    if heap_before.scene != chain_before.scene {
        return Err("heap observer and Mario chain disagree on active scene".into());
    }
    let model_before = read_model_pointers(memory, chain_before.mario_actor)?;

    // Re-resolve the complete root chain before touching the model objects again.
    let chain_middle = resolve_actor_chain(memory)?;
    if chain_middle != chain_before {
        return Err("Mario scene/actor chain changed before model revalidation".into());
    }
    let model_after = read_model_pointers(memory, chain_middle.mario_actor)?;
    if model_after != model_before {
        return Err("Mario model pointers or vptrs changed during observation".into());
    }
    let chain_after = resolve_actor_chain(memory)?;
    let heap_after = heap_observer::observe(memory)?;
    if chain_after != chain_before || !same_scene_heap(heap_before, heap_after) {
        return Err("scene generation/heap identity changed during model observation".into());
    }

    Ok(ModelSample {
        game_system: chain_before.game_system,
        scene_controller: chain_before.scene_controller,
        scene: chain_before.scene,
        scene_object_holder: chain_before.scene_object_holder,
        actor_group: heap_before.group,
        mario_holder: chain_before.mario_holder,
        mario_actor: model_before.mario_actor,
        model_manager: model_before.model_manager,
        xanime_player: model_before.xanime_player,
        model: model_before.model,
        model_vptr: model_before.model_vptr,
        model_data: model_before.model_data,
        model_data_vptr: model_before.model_data_vptr,
        matrix_buffer: model_before.matrix_buffer,
        matrix_buffer_vptr: model_before.matrix_buffer_vptr,
        scene_heap_napa: heap_before.scene_heap_napa,
        scene_heap_gddr3: heap_before.scene_heap_gddr3,
        current_heap: heap_before.sampled_current_heap,
        current_heap_matches_scene_napa: heap_before.current_heap_matches_scene_napa,
    })
}

/// Validate local DOL/symbol identity and exact code signatures used to derive
/// the runtime pointer chain. The caller separately validates AllStars overlap.
pub fn verify_pal_layout(dol: &[u8], symbols: &[u8]) -> Result<(), String> {
    player_probe::verify_pal_inputs(dol, symbols)?;
    let text = std::str::from_utf8(symbols)
        .map_err(|error| format!("Petari symbol map is not UTF-8: {error}"))?;
    for (label, expected) in EXPECTED_SYMBOLS {
        if !text.lines().any(|line| line.trim() == *expected) {
            return Err(format!("PAL model-observer symbol mismatch for {label}"));
        }
    }
    for (address, expected) in MODEL_CODE_SIGNATURES {
        if dol_text_bytes(dol, *address, expected.len())? != *expected {
            return Err(format!(
                "PAL model-chain DOL signature mismatch at 0x{address:08x}"
            ));
        }
    }
    let call_word =
        u32::from_be_bytes(NEW_XANIME_PLAYER_CALL_MODEL_FACTORY_SIG.try_into().unwrap());
    if branch_target(call_word, NEW_XANIME_PLAYER_CALL_MODEL_FACTORY) != Some(0x803E_9668) {
        return Err(
            "PAL newXanimePlayer call no longer targets the verified newJ3DModel factory".into(),
        );
    }
    Ok(())
}

pub fn verify_live_signatures(memory: &mut impl GuestMemory, dol: &[u8]) -> Result<(), String> {
    for (address, expected) in MODEL_CODE_SIGNATURES {
        if dol_text_bytes(dol, *address, expected.len())? != *expected {
            return Err(format!(
                "local PAL model signature changed at 0x{address:08x}"
            ));
        }
        if read_guest(memory, *address, expected.len())? != *expected {
            return Err(format!(
                "live PAL model signature mismatch at 0x{address:08x}"
            ));
        }
    }
    Ok(())
}

pub fn ensure_patch_disjoint(writes: &[multigalaxy_patch::PatchWrite]) -> Result<(), String> {
    let mut protected = MODEL_CODE_SIGNATURES
        .iter()
        .map(|(address, signature)| {
            (
                u64::from(*address),
                u64::from(*address) + signature.len() as u64,
            )
        })
        .collect::<Vec<_>>();
    protected.extend(
        [
            MARIO_ACTOR_VPTR,
            J3D_MODEL_X_VPTR,
            J3D_MODEL_DATA_VPTR,
            J3D_MTX_BUFFER_VPTR,
        ]
        .into_iter()
        .map(|address| (u64::from(address), u64::from(address) + 4)),
    );
    for (start, end) in protected {
        if writes.iter().any(|write| {
            let write_start = u64::from(write.address);
            let write_end = write_start + 4;
            write_start < end && start < write_end
        }) {
            return Err(format!(
                "required AllStars patch overlaps Mario model identity evidence at 0x{start:08x}"
            ));
        }
    }
    Ok(())
}

pub fn observe(memory: &mut impl GuestMemory, dol: &[u8]) -> Result<ModelSample, String> {
    verify_live_signatures(memory, dol)?;
    let sample = sample_once(memory)?;
    verify_live_signatures(memory, dol)?;
    Ok(sample)
}

fn dol_text_bytes(dol: &[u8], address: u32, length: usize) -> Result<&[u8], String> {
    let info = parse_dol(dol).map_err(|error| format!("invalid PAL DOL: {error}"))?;
    let end = u64::from(address)
        .checked_add(length as u64)
        .ok_or("PAL model signature range overflow")?;
    let section = info
        .sections
        .iter()
        .find(|section| {
            section.kind == SectionKind::Text
                && address >= section.address
                && end <= u64::from(section.address) + u64::from(section.size)
        })
        .ok_or_else(|| {
            format!("PAL model signature at 0x{address:08x} is outside a DOL text section")
        })?;
    let offset =
        usize::try_from(u64::from(section.file_offset) + u64::from(address - section.address))
            .map_err(|_| "PAL model signature file offset overflow")?;
    let end = offset
        .checked_add(length)
        .ok_or("PAL model signature file span overflow")?;
    dol.get(offset..end)
        .ok_or_else(|| "PAL DOL is truncated at model signature".into())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::heap_observer::{CURRENT_HEAP, HEAP_WATCHER_SINGLETON};
    use std::collections::BTreeMap;

    const GAME_SYSTEM_FIXTURE: u32 = 0x8000_1000;
    const CONTROLLER: u32 = 0x8000_2000;
    const SCENE: u32 = 0x8000_3000;
    const HOLDER: u32 = 0x8000_4000;
    const MARIO_HOLDER: u32 = 0x8000_5000;
    const ACTOR: u32 = 0x8000_6000;
    const MODEL_MANAGER: u32 = 0x8000_7000;
    const XANIME: u32 = 0x8000_8000;
    const MODEL: u32 = 0x8000_9000;
    const MODEL_DATA: u32 = 0x8000_A000;
    const MATRIX_BUFFER: u32 = 0x8000_B000;
    const GROUP: u32 = 0x8000_C000;
    const ALT_GROUP: u32 = 0x8000_F100;
    const WATCHER: u32 = 0x8000_D000;
    const SCENE_HEAP: u32 = 0x8000_E000;

    #[derive(Default)]
    struct Memory {
        words: BTreeMap<u32, u32>,
        reads: usize,
        mutate_at: Option<(usize, u32, u32)>,
    }

    impl Memory {
        fn set(&mut self, address: u32, value: u32) {
            self.words.insert(address, value);
        }

        fn valid() -> Self {
            let mut memory = Self::default();
            for (address, value) in [
                (GAME_SYSTEM, GAME_SYSTEM_FIXTURE),
                (GAME_SYSTEM_FIXTURE + GAME_SYSTEM_CONTROLLER, CONTROLLER),
                (CONTROLLER + ACTIVE_SCENE, SCENE),
                (SCENE + SCENE_OBJECT_HOLDER, HOLDER),
                (HOLDER + MARIO_HOLDER_SLOT, MARIO_HOLDER),
                (MARIO_HOLDER + MARIO_HOLDER_ACTOR, ACTOR),
                (HOLDER + 0xC0, GROUP),
                (ACTOR, MARIO_ACTOR_VPTR),
                (ACTOR + LIVE_ACTOR_MODEL_MANAGER, MODEL_MANAGER),
                (MODEL_MANAGER + MODEL_MANAGER_XANIME_PLAYER, XANIME),
                (XANIME + XANIME_PLAYER_MODEL, MODEL),
                (MODEL, J3D_MODEL_X_VPTR),
                (MODEL + MODEL_DATA_OFFSET, MODEL_DATA),
                (MODEL_DATA, J3D_MODEL_DATA_VPTR),
                (MODEL + MODEL_MTX_BUFFER, MATRIX_BUFFER),
                (MATRIX_BUFFER, J3D_MTX_BUFFER_VPTR),
                (HEAP_WATCHER_SINGLETON, WATCHER),
                (CURRENT_HEAP, SCENE_HEAP),
                (WATCHER + heap_observer::SCENE_HEAP_NAPA_OFFSET, SCENE_HEAP),
                (
                    WATCHER + heap_observer::SCENE_HEAP_GDDR3_OFFSET,
                    0x8000_F000,
                ),
                (GROUP, 0x8057_327C),
                (GROUP + 0x0C, 0xA00),
                (GROUP + 0x10, 10),
                (GROUP + 0x14, 0x8001_0000),
            ] {
                memory.set(address, value);
            }
            memory
        }
    }

    impl GuestMemory for Memory {
        fn read_guest(&mut self, address: u32, length: usize) -> Result<Vec<u8>, String> {
            self.reads += 1;
            if let Some((at, target, value)) = self.mutate_at {
                if self.reads == at {
                    self.set(target, value);
                }
            }
            if address == mem::GUEST_MEM1_BASE && length == 6 {
                return Ok(b"RMGP01".to_vec());
            }
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

    fn observe_fixture(memory: &mut Memory) -> Result<ModelSample, String> {
        // Synthetic observations intentionally omit PAL text verification; the
        // live entry point always verifies DOL bytes before and after sampling.
        sample_once(memory)
    }

    #[test]
    fn resolves_the_xanime_mario_model_and_scene_heap_identities() {
        let mut memory = Memory::valid();
        let sample = observe_fixture(&mut memory).unwrap();
        assert_eq!(sample.scene, SCENE);
        assert_eq!(sample.game_system, GAME_SYSTEM_FIXTURE);
        assert_eq!(sample.scene_controller, CONTROLLER);
        assert_eq!(sample.actor_group, GROUP);
        assert_eq!(sample.mario_actor, ACTOR);
        assert_eq!(sample.model_manager, MODEL_MANAGER);
        assert_eq!(sample.xanime_player, Some(XANIME));
        assert_eq!(sample.model, MODEL);
        assert_eq!(sample.model_data, MODEL_DATA);
        assert_eq!(sample.matrix_buffer, MATRIX_BUFFER);
        assert_eq!(sample.scene_heap_napa, Some(SCENE_HEAP));
        assert_eq!(sample.current_heap, Some(SCENE_HEAP));
    }

    #[test]
    fn supports_verified_non_xanime_model_manager_branch() {
        let mut memory = Memory::valid();
        memory.set(MODEL_MANAGER + MODEL_MANAGER_XANIME_PLAYER, 0);
        memory.set(MODEL_MANAGER + MODEL_MANAGER_MODEL, MODEL);
        let sample = observe_fixture(&mut memory).unwrap();
        assert_eq!(sample.xanime_player, None);
        assert_eq!(sample.model, MODEL);
    }

    #[test]
    fn rejects_wrong_actor_model_data_and_matrix_vptrs() {
        for (address, bad) in [
            (ACTOR, 0x8057_3690),
            (MODEL, J3D_MODEL_VPTR),
            (MODEL_DATA, 0x805D_4F38),
            (MATRIX_BUFFER, 0x805D_4F18),
        ] {
            let mut memory = Memory::valid();
            memory.set(address, bad);
            assert!(observe_fixture(&mut memory).is_err());
        }
    }

    #[test]
    fn rejects_invalid_pointers_and_null_required_model_objects() {
        let mut bad = Memory::valid();
        bad.set(ACTOR + LIVE_ACTOR_MODEL_MANAGER, 0x8000_0002);
        assert!(observe_fixture(&mut bad)
            .unwrap_err()
            .contains("invalid Wii pointer"));
        let mut missing = Memory::valid();
        missing.set(MODEL + MODEL_DATA_OFFSET, 0);
        assert!(observe_fixture(&mut missing)
            .unwrap_err()
            .contains("J3DModelData is null"));

        let mut mem2 = Memory::valid();
        mem2.set(ACTOR + LIVE_ACTOR_MODEL_MANAGER, 0x9000_1000);
        assert!(observe_fixture(&mut mem2)
            .unwrap_err()
            .contains("outside the validated read-only MEM1 window"));
    }

    #[test]
    fn rejects_scene_or_group_changes_during_sample() {
        let mut scene_change = Memory::valid();
        // The read count is deterministic for the synthetic adapter and flips
        // the active-scene edge between the initial and second chain resolve.
        scene_change.mutate_at = Some((44, CONTROLLER + ACTIVE_SCENE, 0x8001_1000));
        scene_change.set(0x8001_1000 + SCENE_OBJECT_HOLDER, HOLDER);
        assert!(observe_fixture(&mut scene_change).is_err());

        let mut group_change = Memory::valid();
        group_change.set(ALT_GROUP, 0x8057_327C);
        group_change.set(ALT_GROUP + 0x0C, 0xA00);
        group_change.set(ALT_GROUP + 0x10, 1);
        group_change.set(ALT_GROUP + 0x14, 0x8001_0000);
        group_change.mutate_at = Some((70, HOLDER + 0xC0, ALT_GROUP));
        assert!(observe_fixture(&mut group_change).is_err());
    }

    #[test]
    fn rejects_model_pointer_reuse_or_change_inside_one_observation() {
        let mut memory = Memory::valid();
        memory.mutate_at = Some((35, XANIME + XANIME_PLAYER_MODEL, 0x8001_3000));
        assert!(observe_fixture(&mut memory).is_err());
    }

    #[test]
    fn reports_only_sampled_heap_identity_not_model_allocation_ownership() {
        let mut memory = Memory::valid();
        memory.set(CURRENT_HEAP, 0x8000_F000);
        let sample = observe_fixture(&mut memory).unwrap();
        assert_eq!(sample.current_heap, Some(0x8000_F000));
        assert_eq!(sample.current_heap_matches_scene_napa, Some(false));
    }

    #[test]
    fn rejects_allstars_overlap_with_code_or_dynamic_vptr_identity() {
        assert!(ensure_patch_disjoint(&[multigalaxy_patch::PatchWrite {
            address: GET_J3D_MODEL + 4,
            value: 0,
        }])
        .is_err());
        assert!(ensure_patch_disjoint(&[multigalaxy_patch::PatchWrite {
            address: J3D_MODEL_X_VPTR,
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
