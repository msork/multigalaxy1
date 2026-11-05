//! Opt-in read-only snapshot of PAL's current heap and scene heap handles.
//! Heap pointers are validated as MEM1 addresses but are never dereferenced.

use crate::{memory_inspection as mem, penguin_observer, player_probe::GuestMemory};
use multigalaxy_patch::{parse_dol, SectionKind};

pub const HEAP_WATCHER_SINGLETON: u32 = 0x806A_245C;
pub const CURRENT_HEAP: u32 = 0x806A_26DC;
pub const SCENE_HEAP_NAPA_OFFSET: u32 = 0x14;
pub const SCENE_HEAP_GDDR3_OFFSET: u32 = 0x18;
const WII_MEM2_BASE: u32 = 0x9000_0000;
const WII_MEM2_RETAIL_LIMIT: u32 = 0x9400_0000;

const HEAP_WATCHER_GET_SCENE_NAPA: u32 = 0x803E_7460;
const HEAP_WATCHER_SET_SCENE_HEAP: u32 = 0x8039_EC94;
const HEAP_WATCHER_CREATE_SCENE_HEAP: u32 = 0x8039_EBFC;
const HEAP_WATCHER_DESTROY_SCENE_HEAP: u32 = 0x8039_EC9C;
const JKRHEAP_ALLOC: u32 = 0x8040_9678;

const GET_SCENE_NAPA_SIG: &[u8] = &[
    0x80, 0x6d, 0xd7, 0xbc, 0x80, 0x63, 0x00, 0x14, 0x4e, 0x80, 0x00, 0x20,
];
const SET_SCENE_HEAP_SIG: &[u8] = &[0x80, 0x63, 0x00, 0x14, 0x48, 0x04, 0x87, 0xf8];
const CREATE_SCENE_HEAP_SIG: &[u8] = &[
    0x94, 0x21, 0xff, 0xf0, 0x7c, 0x08, 0x02, 0xa6, 0x90, 0x01, 0x00, 0x14, 0x93, 0xe1, 0x00, 0x0c,
    0x7c, 0x7f, 0x1b, 0x78, 0x38, 0x60, 0xff, 0xff, 0x80, 0x9f, 0x00, 0x08, 0x4b, 0xff, 0xfe, 0x91,
    0x90, 0x7f, 0x00, 0x14, 0x38, 0x60, 0xff, 0xff, 0x80, 0x9f, 0x00, 0x0c, 0x4b, 0xff, 0xfe, 0x81,
    0x90, 0x7f, 0x00, 0x18, 0x83, 0xe1, 0x00, 0x0c, 0x80, 0x01, 0x00, 0x14, 0x7c, 0x08, 0x03, 0xa6,
    0x38, 0x21, 0x00, 0x10, 0x4e, 0x80, 0x00, 0x20,
];
const DESTROY_SCENE_HEAP_SIG: &[u8] = &[
    0x94, 0x21, 0xff, 0xf0, 0x7c, 0x08, 0x02, 0xa6, 0x90, 0x01, 0x00, 0x14, 0x93, 0xe1, 0x00, 0x0c,
    0x7c, 0x7f, 0x1b, 0x78, 0x38, 0x63, 0x00, 0x14, 0x4b, 0xff, 0xfe, 0x3d, 0x38, 0x7f, 0x00, 0x18,
    0x4b, 0xff, 0xfe, 0x35, 0x80, 0x01, 0x00, 0x14, 0x83, 0xe1, 0x00, 0x0c, 0x7c, 0x08, 0x03, 0xa6,
    0x38, 0x21, 0x00, 0x10, 0x4e, 0x80, 0x00, 0x20,
];
const ALLOC_CURRENT_HEAP_SIG: &[u8] = &[
    0x2c, 0x05, 0x00, 0x00, 0x7c, 0x66, 0x1b, 0x78, 0x7c, 0x80, 0x23, 0x78, 0x41, 0x82, 0x00, 0x14,
    0x7c, 0xa3, 0x2b, 0x78, 0x7c, 0xc4, 0x33, 0x78, 0x7c, 0x05, 0x03, 0x78, 0x48, 0x00, 0x00, 0x24,
    0x80, 0x6d, 0xda, 0x3c,
];

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct HeapSample {
    pub scene: u32,
    pub group: u32,
    pub watcher: u32,
    pub sampled_current_heap: Option<u32>,
    pub scene_heap_napa: Option<u32>,
    pub scene_heap_gddr3: Option<u32>,
    /// `Some(false)` is a valid observation: it only says the heap active at
    /// sample time was not the Scene Napa heap. It does not imply a fault.
    pub current_heap_matches_scene_napa: Option<bool>,
}

fn field(base: u32, offset: u32) -> Result<u32, String> {
    base.checked_add(offset)
        .ok_or_else(|| "heap pointer field arithmetic overflow".into())
}

fn read_word(memory: &mut impl GuestMemory, address: u32) -> Result<u32, String> {
    mem::plan_read(address, 4)?;
    let bytes = memory.read_guest(address, 4)?;
    mem::decode_be_u32(address, &bytes)
}

fn read_pointer_value(
    memory: &mut impl GuestMemory,
    address: u32,
    nullable: bool,
    label: &str,
) -> Result<Option<u32>, String> {
    let raw = read_word(memory, address)?;
    if raw == 0 {
        return if nullable {
            Ok(None)
        } else {
            Err(format!("{label} is null"))
        };
    }
    if raw & 3 != 0
        || !((mem::GUEST_MEM1_BASE..mem::GUEST_MEM1_LIMIT).contains(&raw)
            || (WII_MEM2_BASE..WII_MEM2_RETAIL_LIMIT).contains(&raw))
    {
        return Err(format!(
            "{label} has invalid Wii MEM1/MEM2 pointer 0x{raw:08x}"
        ));
    }
    Ok(Some(raw))
}

fn read_once(memory: &mut impl GuestMemory) -> Result<HeapSample, String> {
    let (scene, group) = penguin_observer::validated_scene_group(memory)?;
    let watcher = read_pointer_value(
        memory,
        HEAP_WATCHER_SINGLETON,
        false,
        "HeapMemoryWatcher singleton",
    )?
    .ok_or("HeapMemoryWatcher singleton is unavailable")?;
    let current = read_pointer_value(memory, CURRENT_HEAP, true, "JKRHeap::sCurrentHeap")?;
    let napa = read_pointer_value(
        memory,
        field(watcher, SCENE_HEAP_NAPA_OFFSET)?,
        true,
        "Scene heap Napa handle",
    )?;
    let gddr3 = read_pointer_value(
        memory,
        field(watcher, SCENE_HEAP_GDDR3_OFFSET)?,
        true,
        "Scene heap GDDR3 handle",
    )?;
    Ok(HeapSample {
        scene,
        group,
        watcher,
        sampled_current_heap: current,
        scene_heap_napa: napa,
        scene_heap_gddr3: gddr3,
        current_heap_matches_scene_napa: current.zip(napa).map(|(a, b)| a == b),
    })
}

/// Read a small snapshot twice. Any observed scene, group, singleton, or heap
/// pointer change invalidates the sample instead of returning mixed lifetimes.
pub fn observe(memory: &mut impl GuestMemory) -> Result<HeapSample, String> {
    if memory.read_guest(mem::GUEST_MEM1_BASE, 6)? != b"RMGP01" {
        return Err("live game identity is not PAL RMGP01".into());
    }
    let before = read_once(memory)?;
    let after = read_once(memory)?;
    if before != after {
        return Err("scene, group, or heap state changed during sample; discarded".into());
    }
    if memory.read_guest(mem::GUEST_MEM1_BASE, 6)? != b"RMGP01" {
        return Err("live game identity changed during heap sample".into());
    }
    Ok(before)
}

pub fn verify_pal_heap_layout(dol: &[u8], symbols: &[u8]) -> Result<(), String> {
    crate::player_probe::verify_pal_inputs(dol, symbols)?;
    let symbols_text = std::str::from_utf8(symbols)
        .map_err(|error| format!("Petari symbols are not UTF-8: {error}"))?;
    for expected in [
        "sInstance__36SingletonHolder<17HeapMemoryWatcher> = .sbss:0x806A245C; // type:object size:0x4 scope:global align:4 data:4byte",
        "sCurrentHeap__7JKRHeap = .sbss:0x806A26DC; // type:object size:0x4 scope:global align:4 data:4byte",
        "createSceneHeapOnGameHeap__17HeapMemoryWatcherFv = .text:0x8039EBFC; // type:function size:0x48 scope:global align:4",
        "setCurrentHeapToSceneHeap__17HeapMemoryWatcherFv = .text:0x8039EC94; // type:function size:0x8 scope:global align:4",
        "destroySceneHeap__17HeapMemoryWatcherFv = .text:0x8039EC9C; // type:function size:0x38 scope:global align:4",
        "getSceneHeapNapa__2MRFv = .text:0x803E7460; // type:function size:0xC scope:global align:4",
        "alloc__7JKRHeapFUliP7JKRHeap = .text:0x80409678; // type:function size:0x40 scope:global align:4",
    ] {
        if !symbols_text.lines().any(|line| line.trim() == expected) {
            return Err(format!("PAL heap symbol mismatch: {expected}"));
        }
    }
    let info = parse_dol(dol).map_err(|error| format!("invalid PAL DOL: {error}"))?;
    for (address, length) in [(HEAP_WATCHER_SINGLETON, 4), (CURRENT_HEAP, 4)] {
        let end = u64::from(address) + length;
        let in_bss = info.bss_size != 0
            && address >= info.bss_address
            && end <= u64::from(info.bss_address) + u64::from(info.bss_size);
        if !in_bss {
            return Err(format!(
                "PAL heap global 0x{address:08x} is outside DOL BSS"
            ));
        }
    }
    for (address, expected) in [
        (HEAP_WATCHER_GET_SCENE_NAPA, GET_SCENE_NAPA_SIG),
        (HEAP_WATCHER_SET_SCENE_HEAP, SET_SCENE_HEAP_SIG),
        (HEAP_WATCHER_CREATE_SCENE_HEAP, CREATE_SCENE_HEAP_SIG),
        (HEAP_WATCHER_DESTROY_SCENE_HEAP, DESTROY_SCENE_HEAP_SIG),
        (JKRHEAP_ALLOC, ALLOC_CURRENT_HEAP_SIG),
    ] {
        if dol_text_bytes(dol, address, expected.len())? != expected {
            return Err(format!(
                "PAL heap instruction signature mismatch at 0x{address:08x}"
            ));
        }
    }
    Ok(())
}

fn dol_text_bytes(dol: &[u8], address: u32, length: usize) -> Result<&[u8], String> {
    let info = parse_dol(dol).map_err(|error| format!("invalid PAL DOL: {error}"))?;
    let end = u64::from(address)
        .checked_add(length as u64)
        .ok_or("PAL signature range overflow")?;
    let section = info
        .sections
        .iter()
        .find(|section| {
            section.kind == SectionKind::Text
                && address >= section.address
                && end <= u64::from(section.address) + u64::from(section.size)
        })
        .ok_or_else(|| format!("PAL heap code range 0x{address:08x} is not in DOL text"))?;
    let file_offset =
        usize::try_from(u64::from(section.file_offset) + u64::from(address - section.address))
            .map_err(|_| "PAL DOL file offset overflow")?;
    dol.get(
        file_offset
            ..file_offset
                .checked_add(length)
                .ok_or("PAL file range overflow")?,
    )
    .ok_or_else(|| "PAL DOL does not contain heap instruction signature".into())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::BTreeMap;

    const GAME_SYSTEM: u32 = 0x806A_1228;
    const CONTROLLER_OFFSET: u32 = 0x24;
    const ACTIVE_SCENE_OFFSET: u32 = 0xAC;
    const SCENE_HOLDER_OFFSET: u32 = 0x10;
    const ALL_LIVE_SLOT: u32 = 0xC0;
    const GROUP_VPTR: u32 = 0x8057_327C;
    const GROUP_CAPACITY: u32 = 0x0C;
    const GROUP_COUNT: u32 = 0x10;
    const GROUP_ARRAY: u32 = 0x14;

    struct Fixture {
        words: BTreeMap<u32, u32>,
        reads: usize,
        mutate_at: Option<(usize, u32, u32)>,
    }

    impl Fixture {
        fn valid() -> Self {
            let mut result = Self {
                words: BTreeMap::new(),
                reads: 0,
                mutate_at: None,
            };
            result.words.insert(GAME_SYSTEM, 0x8001_0000);
            result
                .words
                .insert(0x8001_0000 + CONTROLLER_OFFSET, 0x8001_0100);
            result
                .words
                .insert(0x8001_0100 + ACTIVE_SCENE_OFFSET, 0x8001_0200);
            result
                .words
                .insert(0x8001_0200 + SCENE_HOLDER_OFFSET, 0x8001_0300);
            result
                .words
                .insert(0x8001_0300 + ALL_LIVE_SLOT, 0x8001_0400);
            result.words.insert(0x8001_0400, GROUP_VPTR);
            result.words.insert(0x8001_0400 + GROUP_CAPACITY, 0xA00);
            result.words.insert(0x8001_0400 + GROUP_COUNT, 0x48C);
            result.words.insert(0x8001_0400 + GROUP_ARRAY, 0x8001_1000);
            result.words.insert(HEAP_WATCHER_SINGLETON, 0x8001_2000);
            result.words.insert(CURRENT_HEAP, 0x8001_3000);
            result
                .words
                .insert(0x8001_2000 + SCENE_HEAP_NAPA_OFFSET, 0x8001_3000);
            result
                .words
                .insert(0x8001_2000 + SCENE_HEAP_GDDR3_OFFSET, 0x8001_4000);
            result
        }

        fn set(&mut self, address: u32, value: u32) {
            self.words.insert(address, value);
        }
    }

    impl GuestMemory for Fixture {
        fn read_guest(&mut self, address: u32, length: usize) -> Result<Vec<u8>, String> {
            self.reads += 1;
            if let Some((at, address, value)) = self.mutate_at {
                if self.reads == at {
                    self.words.insert(address, value);
                }
            }
            if address == mem::GUEST_MEM1_BASE && length == 6 {
                return Ok(b"RMGP01".to_vec());
            }
            if length != 4 {
                return Err(format!(
                    "unexpected synthetic read {address:08x}+{length:x}"
                ));
            }
            self.words
                .get(&address)
                .copied()
                .map(|word| word.to_be_bytes().to_vec())
                .ok_or_else(|| format!("no synthetic word at 0x{address:08x}"))
        }
    }

    #[test]
    fn reports_stable_scene_and_heap_values_without_dereferencing_heap_handles() {
        let mut fixture = Fixture::valid();
        let sample = observe(&mut fixture).unwrap();
        assert_eq!(sample.scene, 0x8001_0200);
        assert_eq!(sample.group, 0x8001_0400);
        assert_eq!(sample.watcher, 0x8001_2000);
        assert_eq!(sample.sampled_current_heap, Some(0x8001_3000));
        assert_eq!(sample.scene_heap_napa, Some(0x8001_3000));
        assert_eq!(sample.scene_heap_gddr3, Some(0x8001_4000));
        assert_eq!(sample.current_heap_matches_scene_napa, Some(true));
    }

    #[test]
    fn accepts_and_reports_heap_mismatch_without_calling_it_constructor_evidence() {
        let mut fixture = Fixture::valid();
        fixture.set(CURRENT_HEAP, 0x8001_5000);
        let sample = observe(&mut fixture).unwrap();
        assert_eq!(sample.sampled_current_heap, Some(0x8001_5000));
        assert_eq!(sample.scene_heap_napa, Some(0x8001_3000));
        assert_eq!(sample.current_heap_matches_scene_napa, Some(false));
    }

    #[test]
    fn rejects_invalid_heap_singleton_and_heap_values() {
        let mut bad_singleton = Fixture::valid();
        bad_singleton.set(HEAP_WATCHER_SINGLETON, 0x9400_0000);
        assert!(observe(&mut bad_singleton)
            .unwrap_err()
            .contains("invalid Wii MEM1/MEM2 pointer"));

        let mut bad_current = Fixture::valid();
        bad_current.set(CURRENT_HEAP, 0x8000_0002);
        assert!(observe(&mut bad_current)
            .unwrap_err()
            .contains("invalid Wii MEM1/MEM2 pointer"));

        let mut mem2_heap = Fixture::valid();
        mem2_heap.set(
            0x8001_2000 + SCENE_HEAP_GDDR3_OFFSET,
            WII_MEM2_BASE + 0x1000,
        );
        assert_eq!(
            observe(&mut mem2_heap).unwrap().scene_heap_gddr3,
            Some(WII_MEM2_BASE + 0x1000)
        );

        let mut bad_mem2 = Fixture::valid();
        bad_mem2.set(CURRENT_HEAP, WII_MEM2_RETAIL_LIMIT);
        assert!(observe(&mut bad_mem2)
            .unwrap_err()
            .contains("invalid Wii MEM1/MEM2 pointer"));

        let mut bad_field = Fixture::valid();
        bad_field.set(HEAP_WATCHER_SINGLETON, 0xFFFF_FFFC);
        assert!(observe(&mut bad_field).is_err());
    }

    #[test]
    fn rejects_scene_transition_between_consistency_passes() {
        let mut fixture = Fixture::valid();
        fixture.mutate_at = Some((26, 0x8001_0100 + ACTIVE_SCENE_OFFSET, 0x8001_0500));
        fixture.set(0x8001_0500 + SCENE_HOLDER_OFFSET, 0x8001_0600);
        fixture.set(0x8001_0600 + ALL_LIVE_SLOT, 0x8001_0700);
        fixture.set(0x8001_0700, GROUP_VPTR);
        fixture.set(0x8001_0700 + GROUP_CAPACITY, 0xA00);
        fixture.set(0x8001_0700 + GROUP_COUNT, 0x48C);
        fixture.set(0x8001_0700 + GROUP_ARRAY, 0x8001_1100);
        assert!(observe(&mut fixture)
            .unwrap_err()
            .contains("changed during sample"));
    }
}
