//! Opt-in, read-only PAL movement/calcAnim/draw-buffer metadata observer.
//! No callback or actor-array entries are read.

use crate::{memory_inspection as mem, penguin_observer, player_probe::GuestMemory};
use multigalaxy_patch::{parse_dol, SectionKind};

const SCENE_LIST_EXECUTOR: u32 = 0x08;
const SCENE_EXECUTOR_VPTR: u32 = 0x805b_dcec;
const SCENE_EXECUTOR_SIZE: u32 = 0x14;
const BUFFER_HOLDER: u32 = 0x04;
const MOVEMENT_LIST: u32 = 0x08;
const CALC_ANIM_LIST: u32 = 0x0c;
const DRAW_LIST: u32 = 0x10;
const LIST_ARRAY: u32 = 0x00;
const CATEGORY_STRIDE: u32 = 20;
const CATEGORY_ARRAY: u32 = 0x00;
const CATEGORY_CAPACITY: u32 = 0x04;
const CATEGORY_COUNT: u32 = 0x08;
const CATEGORY_CHECK_COUNT: u32 = 0x10;
const MOVEMENT_CATEGORY_COUNT: u32 = 0x2e;
const CALC_ANIM_CATEGORY_COUNT: u32 = 0x16;
const PENGUIN_MOVEMENT: u32 = 0x28;
const PENGUIN_CALC_ANIM: u32 = 0x06;
const PENGUIN_DRAW_BUFFER: u32 = 0x10;
const PENGUIN_DRAW_CATEGORY: i32 = -1;

const DRAW_BUFFER_GROUPS: u32 = 0x29;
const DRAW_BUFFER_GROUP_STRIDE: u32 = 0x24;
const DRAW_BUFFER_GROUPS_ARRAY: u32 = 0x00;
const DRAW_BUFFER_GROUPS_CAPACITY: u32 = 0x04;
const GROUP_EXECUTORS: u32 = 0x00;
const GROUP_ACTIVE_EXECUTORS: u32 = 0x0c;
const VECTOR_ARRAY: u32 = 0x00;
const VECTOR_CAPACITY: u32 = 0x04;
const VECTOR_COUNT: u32 = 0x08;
const GROUP_DRAW_CAMERA: u32 = 0x18;
const GROUP_LIGHT: u32 = 0x1c;
const DRAW_BUFFER_TYPE_10_CAPACITY: u32 = 80;
const DRAW_BUFFER_TYPE_10_CAMERA: u32 = 0;
const DRAW_BUFFER_TYPE_10_LIGHT: u32 = 1;
const DRAW_BUFFER_TABLE: u32 = 0x8053_8a78;
const DRAW_BUFFER_TABLE_STRIDE: u32 = 16;

const SCENE_INIT_EXECUTOR: u32 = 0x8034_3830;
const LIST_EXECUTOR_CTOR: u32 = 0x8026_be3c;
const LIST_EXECUTOR_INIT: u32 = 0x8026_bf90;
const INIT_MOVEMENT: u32 = 0x8034_5a30;
const INIT_CALC_ANIM: u32 = 0x8034_5aa4;
const INIT_CALC_VIEW_ENTRY: u32 = 0x8034_5b18;
const INIT_DRAW: u32 = 0x8034_5b68;
const CATEGORY_INCREMENT: u32 = 0x8026_1870;
const CATEGORY_ALLOCATE: u32 = 0x8026_188c;
const CATEGORY_ADD: u32 = 0x8026_1914;
const DRAW_GROUP_INIT: u32 = 0x8039_65b8;
const DRAW_GROUP_REGISTER: u32 = 0x8039_6610;
const DRAW_GROUP_ALLOCATE: u32 = 0x8039_66e4;
const DRAW_EXECUTOR_ADD: u32 = 0x8039_63b8;
const DRAW_HOLDER_CTOR: u32 = 0x8039_6d0c;
const DRAW_HOLDER_INIT_TABLE: u32 = 0x8039_6d70;
const PENGUIN_CONNECT: u32 = 0x803e_f020;
const EXECUTE_HOLDER_VPTR: u32 = 0x8059_3178;

const SYMBOLS: &[&str] = &[
    "initNameObjListExecutor__5SceneFv = .text:0x80343830; // type:function size:0x60 scope:global align:4",
    "__ct__19NameObjListExecutorFv = .text:0x8026BE3C; // type:function size:0x24 scope:global align:4",
    "init__19NameObjListExecutorFv = .text:0x8026BF90; // type:function size:0x74 scope:global align:4",
    "initMovementList__24SceneNameObjListExecutorFv = .text:0x80345A30; // type:function size:0x74 scope:global align:4",
    "initCalcAnimList__24SceneNameObjListExecutorFv = .text:0x80345AA4; // type:function size:0x74 scope:global align:4",
    "initCalcViewAndEntryList__24SceneNameObjListExecutorFv = .text:0x80345B18; // type:function size:0x50 scope:global align:4",
    "initDrawList__24SceneNameObjListExecutorFv = .text:0x80345B68; // type:function size:0x74 scope:global align:4",
    "connectToSceneNpc__2MRFP9LiveActor = .text:0x803EF020; // type:function size:0x14 scope:global align:4",
    "incrementCheck__19NameObjCategoryListFP7NameObji = .text:0x80261870; // type:function size:0x1C scope:global align:4",
    "allocateBuffer__19NameObjCategoryListFv = .text:0x8026188C; // type:function size:0x88 scope:global align:4",
    "add__19NameObjCategoryListFP7NameObji = .text:0x80261914; // type:function size:0x28 scope:global align:4",
    "init__15DrawBufferGroupFl = .text:0x803965B8; // type:function size:0x58 scope:global align:4",
    "registerDrawBuffer__15DrawBufferGroupFP9LiveActor = .text:0x80396610; // type:function size:0xD4 scope:global align:4",
    "allocateActorListBuffer__15DrawBufferGroupFv = .text:0x803966E4; // type:function size:0x64 scope:global align:4",
    "add__18DrawBufferExecuterFP9LiveActor = .text:0x803963B8; // type:function size:0x20 scope:global align:4",
    "__ct__16DrawBufferHolderFv = .text:0x80396D0C; // type:function size:0x64 scope:global align:4",
    "initTable__16DrawBufferHolderFPC22DrawBufferInitialTablel = .text:0x80396D70; // type:function size:0x12C scope:global align:4",
    "__vt__24SceneNameObjListExecutor = .data:0x805BDCEC; // type:object size:0x1C scope:global align:4",
    "cMovementListInitTable__38@unnamed@SceneNameObjListExecutor_cpp@ = .rodata:0x80538848; // type:object size:0x178 scope:global align:4",
    "cCalcAnimListInitTable__38@unnamed@SceneNameObjListExecutor_cpp@ = .rodata:0x805389C0; // type:object size:0xB8 scope:global align:4",
    "cDrawBufferListInitTable__38@unnamed@SceneNameObjListExecutor_cpp@ = .rodata:0x80538A78; // type:object size:0x2A0 scope:global align:4",
];

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ListCount {
    pub identity: u32,
    pub list: u32,
    pub address: u32,
    pub array: u32,
    pub count: u32,
    pub capacity: u32,
    pub remaining: u32,
    pub check_count: u32,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct DrawBufferCounts {
    pub address: u32,
    pub executors_array: u32,
    pub executors: u32,
    pub executor_capacity: u32,
    pub executor_remaining: u32,
    pub active_array: u32,
    pub active: u32,
    pub active_capacity: u32,
    pub active_remaining: u32,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct CallbackSample {
    pub scene: u32,
    pub scene_object_holder: u32,
    pub all_live_actor_group: u32,
    pub scene_list_executor: u32,
    pub draw_buffer_holder: u32,
    pub draw_group_table: u32,
    pub draw_group_table_capacity: u32,
    pub movement: ListCount,
    pub calc_anim: ListCount,
    pub draw_list: u32,
    pub draw_categories: u32,
    pub draw_category_count: u32,
    pub draw_category: i32,
    pub draw_buffer: DrawBufferCounts,
    pub execute_holder: u32,
    pub execute_array: u32,
    pub execute_capacity: u32,
    pub execute_count: u32,
    pub pending_flags: [u8; 5],
}

fn add(base: u32, offset: u32) -> Result<u32, String> {
    base.checked_add(offset)
        .ok_or_else(|| "callback observer address arithmetic overflow".into())
}

fn span(address: u32, bytes: u32, label: &str) -> Result<(), String> {
    if address & 3 != 0 {
        return Err(format!("{label} is not 4-byte aligned"));
    }
    let end = u64::from(address)
        .checked_add(u64::from(bytes))
        .ok_or_else(|| format!("{label} span overflow"))?;
    if address < mem::GUEST_MEM1_BASE || end > u64::from(mem::GUEST_MEM1_LIMIT) {
        return Err(format!("{label} span is outside canonical MEM1"));
    }
    Ok(())
}

fn bytes(memory: &mut impl GuestMemory, address: u32, size: usize) -> Result<Vec<u8>, String> {
    mem::plan_read(address, size)?;
    let value = memory.read_guest(address, size)?;
    if value.len() != size {
        return Err(format!("short callback metadata read at 0x{address:08x}"));
    }
    Ok(value)
}

fn word(memory: &mut impl GuestMemory, address: u32) -> Result<u32, String> {
    mem::decode_be_u32(address, &bytes(memory, address, 4)?)
}

fn pointer(memory: &mut impl GuestMemory, address: u32, what: &str) -> Result<u32, String> {
    mem::decode_be_pointer(address, &bytes(memory, address, 4)?)?
        .ok_or_else(|| format!("{what} is null or not initialized"))
}

fn check_array(array: u32, capacity: u32, what: &str) -> Result<(), String> {
    let size = capacity
        .checked_mul(4)
        .ok_or_else(|| format!("{what} byte size overflow"))?;
    span(array, size.max(4), what)
}

fn category(
    memory: &mut impl GuestMemory,
    list: u32,
    list_count: u32,
    index: u32,
) -> Result<ListCount, String> {
    if index >= list_count {
        return Err("Penguin category index exceeds verified list length".into());
    }
    let categories = pointer(memory, add(list, LIST_ARRAY)?, "category-info array")?;
    let allocated_categories = word(memory, add(list, 4)?)?;
    if allocated_categories != list_count {
        return Err(format!(
            "category-list size mismatch: expected {list_count}, found {allocated_categories}"
        ));
    }
    let category_bytes = list_count
        .checked_mul(CATEGORY_STRIDE)
        .ok_or("category-info span overflow")?;
    span(categories, category_bytes, "category-info array")?;
    let info = add(
        categories,
        index
            .checked_mul(CATEGORY_STRIDE)
            .ok_or("category index multiplication overflow")?,
    )?;
    let array_value = word(memory, add(info, CATEGORY_ARRAY)?)?;
    let capacity = word(memory, add(info, CATEGORY_CAPACITY)?)?;
    let count = word(memory, add(info, CATEGORY_COUNT)?)?;
    let check_count = word(memory, add(info, CATEGORY_CHECK_COUNT)?)?;
    if array_value == 0 {
        return Err("callback category buffer is not allocated yet".into());
    }
    if count > capacity {
        return Err(format!("callback category {index} count exceeds capacity"));
    }
    if capacity != check_count {
        return Err(format!(
            "callback category {index} prepared and allocated capacities differ"
        ));
    }
    check_array(array_value, capacity, "callback pointer array")?;
    Ok(ListCount {
        identity: index,
        list,
        address: info,
        array: array_value,
        count,
        capacity,
        remaining: capacity - count,
        check_count,
    })
}

fn vector(
    memory: &mut impl GuestMemory,
    base: u32,
    label: &str,
    expected_capacity: Option<u32>,
) -> Result<(u32, u32, u32), String> {
    let array = pointer(memory, add(base, VECTOR_ARRAY)?, &format!("{label} array"))?;
    let capacity = word(memory, add(base, VECTOR_CAPACITY)?)?;
    let count = word(memory, add(base, VECTOR_COUNT)?)?;
    if count > capacity {
        return Err(format!("{label} count exceeds capacity"));
    }
    if let Some(expected) = expected_capacity {
        if capacity != expected {
            return Err(format!(
                "{label} capacity mismatch: expected {expected}, found {capacity}"
            ));
        }
    }
    check_array(array, capacity, label)?;
    Ok((array, count, capacity))
}

fn read_once(memory: &mut impl GuestMemory) -> Result<CallbackSample, String> {
    let context = penguin_observer::validated_scene_context(memory)?;
    let executor = pointer(
        memory,
        add(context.scene, SCENE_LIST_EXECUTOR)?,
        "scene list executor",
    )?;
    span(executor, SCENE_EXECUTOR_SIZE, "scene list executor object")?;
    if word(memory, executor)? != SCENE_EXECUTOR_VPTR {
        return Err("SceneNameObjListExecutor vptr mismatch".into());
    }
    let movement_list = pointer(
        memory,
        add(executor, MOVEMENT_LIST)?,
        "movement category list",
    )?;
    let calc_list = pointer(
        memory,
        add(executor, CALC_ANIM_LIST)?,
        "calcAnim category list",
    )?;
    let draw_list = pointer(memory, add(executor, DRAW_LIST)?, "draw category list")?;
    let movement = category(
        memory,
        movement_list,
        MOVEMENT_CATEGORY_COUNT,
        PENGUIN_MOVEMENT,
    )?;
    let calc_anim = category(
        memory,
        calc_list,
        CALC_ANIM_CATEGORY_COUNT,
        PENGUIN_CALC_ANIM,
    )?;
    // Penguin's verified NPC connect helper uses draw category -1. Validate the
    // list object is present, but do not treat any draw-category record as used.
    let draw_categories = pointer(memory, draw_list, "draw category-info array")?;
    let draw_category_count = word(memory, add(draw_list, 4)?)?;
    if draw_category_count != 0x53 {
        return Err(format!(
            "draw category-list size mismatch: expected 83, found {draw_category_count}"
        ));
    }
    span(
        draw_categories,
        draw_category_count
            .checked_mul(CATEGORY_STRIDE)
            .ok_or("draw category-info span overflow")?,
        "draw category-info array",
    )?;

    let buffer_holder = pointer(memory, add(executor, BUFFER_HOLDER)?, "draw buffer holder")?;
    span(buffer_holder, 0x30, "draw buffer holder object")?;
    let groups_array = pointer(
        memory,
        add(buffer_holder, DRAW_BUFFER_GROUPS_ARRAY)?,
        "draw buffer groups",
    )?;
    let groups_capacity = word(memory, add(buffer_holder, DRAW_BUFFER_GROUPS_CAPACITY)?)?;
    if groups_capacity != DRAW_BUFFER_GROUPS {
        return Err(format!(
            "draw buffer group table capacity mismatch: expected {DRAW_BUFFER_GROUPS}, found {groups_capacity}"
        ));
    }
    span(
        groups_array,
        groups_capacity
            .checked_mul(DRAW_BUFFER_GROUP_STRIDE)
            .ok_or("draw buffer group array span overflow")?,
        "draw buffer group array",
    )?;
    let group = add(
        groups_array,
        PENGUIN_DRAW_BUFFER
            .checked_mul(DRAW_BUFFER_GROUP_STRIDE)
            .ok_or("draw buffer group index overflow")?,
    )?;
    let camera = word(memory, add(group, GROUP_DRAW_CAMERA)?)?;
    let light = word(memory, add(group, GROUP_LIGHT)?)?;
    if camera != DRAW_BUFFER_TYPE_10_CAMERA || light != DRAW_BUFFER_TYPE_10_LIGHT {
        return Err("draw buffer type 0x10 group metadata mismatch".into());
    }
    let (executors_array, executors, executor_capacity) = vector(
        memory,
        add(group, GROUP_EXECUTORS)?,
        "draw-buffer executor list",
        Some(DRAW_BUFFER_TYPE_10_CAPACITY),
    )?;
    let (active_array, active, active_capacity) = vector(
        memory,
        add(group, GROUP_ACTIVE_EXECUTORS)?,
        "active draw-buffer executor list",
        Some(DRAW_BUFFER_TYPE_10_CAPACITY),
    )?;

    let holder_slot = add(context.scene_object_holder, 0x160)?;
    let execute_holder = pointer(memory, holder_slot, "execute holder")?;
    span(execute_holder, 0x20, "execute holder object")?;
    if word(memory, execute_holder)? != EXECUTE_HOLDER_VPTR {
        return Err("NameObjExecuteHolder vptr mismatch".into());
    }
    let execute_array = pointer(memory, add(execute_holder, 0x0c)?, "execute-info array")?;
    let execute_capacity = word(memory, add(execute_holder, 0x10)?)?;
    let execute_count = word(memory, add(execute_holder, 0x14)?)?;
    if execute_capacity != 4096 || execute_count > execute_capacity {
        return Err("execute-holder metadata is invalid".into());
    }
    let execute_bytes = execute_capacity
        .checked_mul(12)
        .ok_or("execute-info array span overflow")?;
    span(execute_array, execute_bytes, "execute-info array")?;
    let raw_flags = bytes(memory, add(execute_holder, 0x18)?, 5)?;
    let pending_flags: [u8; 5] = raw_flags
        .try_into()
        .map_err(|_| "invalid pending-flag length")?;
    if pending_flags.iter().any(|value| *value > 1) {
        return Err("pending operation flags are not boolean".into());
    }
    if pending_flags != [0; 5] {
        return Err("scheduler has pending connect/disconnect operations".into());
    }

    Ok(CallbackSample {
        scene: context.scene,
        scene_object_holder: context.scene_object_holder,
        all_live_actor_group: context.all_live_actor_group,
        scene_list_executor: executor,
        draw_buffer_holder: buffer_holder,
        draw_group_table: groups_array,
        draw_group_table_capacity: groups_capacity,
        movement,
        calc_anim,
        draw_list,
        draw_categories,
        draw_category_count,
        draw_category: PENGUIN_DRAW_CATEGORY,
        draw_buffer: DrawBufferCounts {
            address: group,
            executors_array,
            executors,
            executor_capacity,
            executor_remaining: executor_capacity - executors,
            active_array,
            active,
            active_capacity,
            active_remaining: active_capacity - active,
        },
        execute_holder,
        execute_array,
        execute_capacity,
        execute_count,
        pending_flags,
    })
}

pub fn observe(memory: &mut impl GuestMemory) -> Result<CallbackSample, String> {
    if bytes(memory, mem::GUEST_MEM1_BASE, 6)? != b"RMGP01" {
        return Err("live game identity is not PAL RMGP01".into());
    }
    let before = read_once(memory)?;
    let after = read_once(memory)?;
    if before != after {
        return Err("scene or callback metadata changed during observation; discarded".into());
    }
    if bytes(memory, mem::GUEST_MEM1_BASE, 6)? != b"RMGP01" {
        return Err("live game identity changed during callback observation".into());
    }
    Ok(before)
}

pub fn verify_pal_layout(dol: &[u8], symbols: &[u8]) -> Result<(), String> {
    crate::player_probe::verify_pal_inputs(dol, symbols)?;
    let symbols =
        std::str::from_utf8(symbols).map_err(|e| format!("symbol map is not UTF-8: {e}"))?;
    for expected in SYMBOLS {
        if !symbols.lines().any(|line| line.trim() == *expected) {
            return Err(format!("callback-observer PAL symbol mismatch: {expected}"));
        }
    }
    let info = parse_dol(dol).map_err(|e| format!("invalid PAL DOL: {e}"))?;
    for (address, length) in [
        (SCENE_INIT_EXECUTOR, 0x60),
        (LIST_EXECUTOR_CTOR, 0x24),
        (LIST_EXECUTOR_INIT, 0x74),
        (INIT_MOVEMENT, 0x74),
        (INIT_CALC_ANIM, 0x74),
        (INIT_CALC_VIEW_ENTRY, 0x50),
        (INIT_DRAW, 0x74),
        (CATEGORY_INCREMENT, 0x1c),
        (CATEGORY_ALLOCATE, 0x88),
        (CATEGORY_ADD, 0x28),
        (DRAW_GROUP_INIT, 0x58),
        (DRAW_GROUP_REGISTER, 0xd4),
        (DRAW_GROUP_ALLOCATE, 0x64),
        (DRAW_EXECUTOR_ADD, 0x20),
        (DRAW_HOLDER_CTOR, 0x64),
        (DRAW_HOLDER_INIT_TABLE, 0x12c),
        (PENGUIN_CONNECT, 0x14),
    ] {
        dol_bytes(dol, &info, address, length)?;
    }
    let row_address = add(DRAW_BUFFER_TABLE, 7 * DRAW_BUFFER_TABLE_STRIDE)?;
    let row = dol_bytes(dol, &info, row_address, DRAW_BUFFER_TABLE_STRIDE as usize)?;
    let values: [u32; 4] = std::array::from_fn(|i| {
        u32::from_be_bytes(
            row[i * 4..i * 4 + 4]
                .try_into()
                .expect("fixed DOL table row"),
        )
    });
    if values
        != [
            PENGUIN_DRAW_BUFFER,
            DRAW_BUFFER_TYPE_10_CAPACITY,
            DRAW_BUFFER_TYPE_10_LIGHT,
            DRAW_BUFFER_TYPE_10_CAMERA,
        ]
    {
        return Err(format!(
            "PAL draw-buffer type 0x10 table row mismatch: {values:x?}"
        ));
    }
    for (address, expected) in [
        (0x8034_5a78, [0x38, 0x80, 0x00, 0x2e]),
        (0x8034_5aec, [0x38, 0x80, 0x00, 0x16]),
        (0x8034_5b48, [0x38, 0xa0, 0x00, 0x29]),
        (0x8034_5bb0, [0x38, 0x80, 0x00, 0x53]),
    ] {
        if dol_bytes(dol, &info, address, expected.len())? != expected {
            return Err(format!(
                "PAL scheduler table-count instruction mismatch at 0x{address:08x}"
            ));
        }
    }
    Ok(())
}

pub fn verify_live_signatures(memory: &mut impl GuestMemory, dol: &[u8]) -> Result<(), String> {
    let info = parse_dol(dol).map_err(|e| format!("invalid PAL DOL: {e}"))?;
    for (address, length) in [
        (SCENE_INIT_EXECUTOR, 0x60),
        (LIST_EXECUTOR_CTOR, 0x24),
        (LIST_EXECUTOR_INIT, 0x74),
        (INIT_MOVEMENT, 0x74),
        (INIT_CALC_ANIM, 0x74),
        (INIT_CALC_VIEW_ENTRY, 0x50),
        (INIT_DRAW, 0x74),
        (CATEGORY_INCREMENT, 0x1c),
        (CATEGORY_ALLOCATE, 0x88),
        (CATEGORY_ADD, 0x28),
        (DRAW_GROUP_INIT, 0x58),
        (DRAW_GROUP_REGISTER, 0xd4),
        (DRAW_GROUP_ALLOCATE, 0x64),
        (DRAW_EXECUTOR_ADD, 0x20),
        (DRAW_HOLDER_CTOR, 0x64),
        (DRAW_HOLDER_INIT_TABLE, 0x12c),
        (PENGUIN_CONNECT, 0x14),
    ] {
        let expected = dol_bytes(dol, &info, address, length)?;
        if bytes(memory, address, length)? != expected {
            return Err(format!(
                "live PAL callback signature mismatch at 0x{address:08x}"
            ));
        }
    }
    let row_address = add(DRAW_BUFFER_TABLE, 7 * DRAW_BUFFER_TABLE_STRIDE)?;
    let expected_row = dol_bytes(dol, &info, row_address, DRAW_BUFFER_TABLE_STRIDE as usize)?;
    if bytes(memory, row_address, DRAW_BUFFER_TABLE_STRIDE as usize)? != expected_row {
        return Err("live PAL draw-buffer table row 0x10 mismatch".into());
    }
    Ok(())
}

fn dol_bytes<'a>(
    dol: &'a [u8],
    info: &multigalaxy_patch::DolInfo,
    address: u32,
    length: usize,
) -> Result<&'a [u8], String> {
    let end = u64::from(address)
        .checked_add(length as u64)
        .ok_or("callback signature range overflow")?;
    let section = info
        .sections
        .iter()
        .find(|section| {
            section.kind != SectionKind::Text
                && address >= section.address
                && end <= u64::from(section.address) + u64::from(section.size)
        })
        .or_else(|| {
            info.sections.iter().find(|section| {
                section.kind == SectionKind::Text
                    && address >= section.address
                    && end <= u64::from(section.address) + u64::from(section.size)
            })
        })
        .ok_or_else(|| format!("PAL callback address 0x{address:08x} is not in a DOL section"))?;
    let file =
        usize::try_from(u64::from(section.file_offset) + u64::from(address - section.address))
            .map_err(|_| "PAL callback DOL file offset overflow")?;
    dol.get(
        file..file
            .checked_add(length)
            .ok_or("PAL callback file range overflow")?,
    )
    .ok_or_else(|| "PAL DOL callback bytes are truncated".into())
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
    const LIST_EXECUTOR: u32 = 0x8001_0600;
    const BUFFER_HOLDER_ADDR: u32 = 0x8001_0700;
    const MOVEMENT: u32 = 0x8001_0800;
    const CALC: u32 = 0x8001_0900;
    const DRAW: u32 = 0x8001_0a00;
    const MOVEMENT_INFO: u32 = 0x8001_1000;
    const CALC_INFO: u32 = 0x8001_1800;
    const DRAW_INFO: u32 = 0x8001_1a00;
    const DRAW_GROUPS: u32 = 0x8001_2000;
    const EXECUTORS: u32 = 0x8001_3000;
    const ACTIVE: u32 = 0x8001_3200;
    const EXEC_ARRAY: u32 = 0x8001_4000;

    struct Fixture {
        words: BTreeMap<u32, u32>,
        reads: Vec<(u32, usize)>,
        scene_reads: usize,
        change_scene: bool,
    }
    impl Fixture {
        fn set(&mut self, at: u32, value: u32) {
            self.words.insert(at, value);
        }
        fn valid() -> Self {
            let mut f = Self {
                words: BTreeMap::new(),
                reads: Vec::new(),
                scene_reads: 0,
                change_scene: false,
            };
            let mut w: Vec<(u32, u32)> = vec![
                (0x806a1228, GAME),
                (GAME + 0x24, CONTROLLER),
                (CONTROLLER + 0xac, SCENE),
                (SCENE + 0x10, SCENE_HOLDER),
                (SCENE_HOLDER + 0xc0, GROUP),
                (GROUP, 0x8057327c),
                (GROUP + 0xc, 0xa00),
                (GROUP + 0x10, 0x48c),
                (GROUP + 0x14, 0x80015000),
                (SCENE + SCENE_LIST_EXECUTOR, LIST_EXECUTOR),
                (LIST_EXECUTOR, SCENE_EXECUTOR_VPTR),
                (LIST_EXECUTOR + BUFFER_HOLDER, BUFFER_HOLDER_ADDR),
                (LIST_EXECUTOR + MOVEMENT_LIST, MOVEMENT),
                (LIST_EXECUTOR + CALC_ANIM_LIST, CALC),
                (LIST_EXECUTOR + DRAW_LIST, DRAW),
                (MOVEMENT + 0, MOVEMENT_INFO),
                (MOVEMENT + 4, MOVEMENT_CATEGORY_COUNT),
                (CALC + 0, CALC_INFO),
                (CALC + 4, CALC_ANIM_CATEGORY_COUNT),
                (DRAW + 0, DRAW_INFO),
                (DRAW + 4, 0x53),
                (BUFFER_HOLDER_ADDR, DRAW_GROUPS),
                (BUFFER_HOLDER_ADDR + 4, DRAW_BUFFER_GROUPS),
                (
                    DRAW_GROUPS + PENGUIN_DRAW_BUFFER * DRAW_BUFFER_GROUP_STRIDE + GROUP_EXECUTORS,
                    EXECUTORS,
                ),
                (
                    DRAW_GROUPS
                        + PENGUIN_DRAW_BUFFER * DRAW_BUFFER_GROUP_STRIDE
                        + GROUP_EXECUTORS
                        + 4,
                    DRAW_BUFFER_TYPE_10_CAPACITY,
                ),
                (
                    DRAW_GROUPS
                        + PENGUIN_DRAW_BUFFER * DRAW_BUFFER_GROUP_STRIDE
                        + GROUP_EXECUTORS
                        + 8,
                    30,
                ),
                (
                    DRAW_GROUPS
                        + PENGUIN_DRAW_BUFFER * DRAW_BUFFER_GROUP_STRIDE
                        + GROUP_ACTIVE_EXECUTORS,
                    ACTIVE,
                ),
                (
                    DRAW_GROUPS
                        + PENGUIN_DRAW_BUFFER * DRAW_BUFFER_GROUP_STRIDE
                        + GROUP_ACTIVE_EXECUTORS
                        + 4,
                    DRAW_BUFFER_TYPE_10_CAPACITY,
                ),
                (
                    DRAW_GROUPS
                        + PENGUIN_DRAW_BUFFER * DRAW_BUFFER_GROUP_STRIDE
                        + GROUP_ACTIVE_EXECUTORS
                        + 8,
                    12,
                ),
                (
                    DRAW_GROUPS
                        + PENGUIN_DRAW_BUFFER * DRAW_BUFFER_GROUP_STRIDE
                        + GROUP_DRAW_CAMERA,
                    0,
                ),
                (
                    DRAW_GROUPS + PENGUIN_DRAW_BUFFER * DRAW_BUFFER_GROUP_STRIDE + GROUP_LIGHT,
                    1,
                ),
                (SCENE_HOLDER + 0x160, EXEC_HOLDER),
                (EXEC_HOLDER, 0x80593178),
                (EXEC_HOLDER + 0x0c, EXEC_ARRAY),
                (EXEC_HOLDER + 0x10, 4096),
                (EXEC_HOLDER + 0x14, 245),
                (EXEC_ARRAY, 0),
            ];
            for (base, id, used) in [
                (MOVEMENT_INFO, PENGUIN_MOVEMENT, 7),
                (CALC_INFO, PENGUIN_CALC_ANIM, 5),
            ] {
                w.push((base + id * CATEGORY_STRIDE, 0x80016000));
                w.push((base + id * CATEGORY_STRIDE + 4, 20));
                w.push((base + id * CATEGORY_STRIDE + 8, used));
                w.push((base + id * CATEGORY_STRIDE + 16, 20));
            }
            f.words.extend(w);
            f.words.insert(0x80016000, 0);
            f.words.insert(EXECUTORS, 0);
            f.words.insert(EXECUTORS + 4, 80);
            f.words.insert(EXECUTORS + 8, 30);
            f.words.insert(ACTIVE, 0);
            f.words.insert(ACTIVE + 4, 80);
            f.words.insert(ACTIVE + 8, 12);
            f
        }
    }
    impl GuestMemory for Fixture {
        fn read_guest(&mut self, address: u32, size: usize) -> Result<Vec<u8>, String> {
            self.reads.push((address, size));
            if address == mem::GUEST_MEM1_BASE {
                return Ok(b"RMGP01"[..size].to_vec());
            }
            if address == CONTROLLER + 0xac {
                self.scene_reads += 1;
                if self.change_scene && self.scene_reads > 2 {
                    return Ok(0x8001_0600u32.to_be_bytes()[..size].to_vec());
                }
            }
            let mut out = Vec::new();
            for off in (0..size).step_by(4) {
                let at = address + off as u32;
                let v = *self.words.get(&at).unwrap_or(&0);
                out.extend_from_slice(&v.to_be_bytes());
            }
            out.truncate(size);
            Ok(out)
        }
    }

    #[test]
    fn reports_only_bounded_category_and_draw_group_metadata() {
        let mut f = Fixture::valid();
        let sample = observe(&mut f).unwrap();
        assert_eq!(sample.movement.count, 7);
        assert_eq!(sample.movement.capacity, 20);
        assert_eq!(sample.movement.remaining, 13);
        assert_eq!(sample.calc_anim.count, 5);
        assert_eq!(sample.draw_category, -1);
        assert_eq!(sample.draw_category_count, 83);
        assert_eq!(sample.draw_buffer.executors, 30);
        assert_eq!(sample.draw_buffer.executor_remaining, 50);
        assert_eq!(sample.draw_buffer.active, 12);
        assert_eq!(sample.draw_buffer.active_remaining, 68);
        assert_eq!(sample.pending_flags, [0; 5]);
        assert!(!f
            .reads
            .iter()
            .any(|(a, _)| *a >= 0x8001_6000 && *a < 0x8001_7000));
        assert!(!f
            .reads
            .iter()
            .any(|(a, _)| *a >= EXECUTORS && *a < EXECUTORS + 80 * 4));
        assert!(!f
            .reads
            .iter()
            .any(|(a, _)| *a >= ACTIVE && *a < ACTIVE + 80 * 4));
    }

    #[test]
    fn rejects_invalid_category_count_capacity_and_pending_state() {
        let mut f = Fixture::valid();
        f.set(MOVEMENT_INFO + PENGUIN_MOVEMENT * CATEGORY_STRIDE + 8, 21);
        assert!(observe(&mut f).unwrap_err().contains("count exceeds"));
        let mut f = Fixture::valid();
        f.set(CALC_INFO + PENGUIN_CALC_ANIM * CATEGORY_STRIDE + 16, 19);
        assert!(observe(&mut f).unwrap_err().contains("capacities differ"));
        let mut f = Fixture::valid();
        f.set(MOVEMENT + 4, MOVEMENT_CATEGORY_COUNT - 1);
        assert!(observe(&mut f).unwrap_err().contains("list size mismatch"));
        let mut f = Fixture::valid();
        f.set(DRAW + 4, 82);
        assert!(observe(&mut f)
            .unwrap_err()
            .contains("draw category-list size mismatch"));
        let mut f = Fixture::valid();
        f.set(
            DRAW_GROUPS
                + PENGUIN_DRAW_BUFFER * DRAW_BUFFER_GROUP_STRIDE
                + GROUP_EXECUTORS
                + VECTOR_COUNT,
            DRAW_BUFFER_TYPE_10_CAPACITY + 1,
        );
        assert!(observe(&mut f)
            .unwrap_err()
            .contains("count exceeds capacity"));
        let mut f = Fixture::valid();
        f.set(EXEC_HOLDER + 0x18, 1);
        assert!(observe(&mut f).unwrap_err().contains("pending"));
    }

    #[test]
    fn rejects_bad_group_pointer_or_scene_transition() {
        let mut f = Fixture::valid();
        f.set(BUFFER_HOLDER_ADDR, 0x817f_fffc);
        assert!(observe(&mut f)
            .unwrap_err()
            .contains("outside canonical MEM1"));
        let mut f = Fixture::valid();
        f.change_scene = true;
        assert!(observe(&mut f).is_err());
    }
}
