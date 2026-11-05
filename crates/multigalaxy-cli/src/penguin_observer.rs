//! Opt-in, read-only scan of the currently registered PAL Penguin actor class.
//! Only entry pointers and each valid object's first word are read.
use crate::{memory_inspection as mem, player_probe::GuestMemory};
use multigalaxy_patch::{parse_dol, PatchWrite, SectionKind};
use std::time::Instant;

const GAME_SYSTEM: u32 = 0x806A_1228;
const SCENE_CONTROLLER: u32 = 0x24;
const ACTIVE_SCENE: u32 = 0xAC;
const SCENE_HOLDER: u32 = 0x10;
const ALL_LIVE_SLOT: u32 = 0xC0;
const GROUP_VPTR: u32 = 0x8057_327C;
const PENGUIN_VPTR: u32 = 0x8059_C884;
const CAPACITY: u32 = 0xA00;
const GROUP_CAPACITY: u32 = 0x0C;
const GROUP_COUNT: u32 = 0x10;
const GROUP_ARRAY: u32 = 0x14;
const MAX_REPORTED_MATCHES: usize = 16;
const PENGUIN_CTOR: u32 = 0x8027_90E0;
const PENGUIN_CTOR_VPTR_STORE: &[u8] = &[
    0x7C, 0x7F, 0x1B, 0x78, 0x48, 0x02, 0x13, 0xBD, 0x3C, 0x60, 0x80, 0x5A, 0x38, 0x80, 0xFF, 0xFF,
    0x38, 0x63, 0xC8, 0x84, 0x38, 0x00, 0x00, 0x00, 0x90, 0x7F, 0x00, 0x00,
];

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Candidate {
    pub index: u32,
    pub actor: u32,
    pub vptr: u32,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Report {
    pub elapsed_ms: u128,
    pub scene: u32,
    pub group: u32,
    pub capacity: u32,
    pub count: u32,
    pub null_entries: u32,
    pub invalid_entries: u32,
    pub non_penguin: u32,
    pub total_matches: u32,
    pub matches: Vec<Candidate>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct Chain {
    game_system: u32,
    controller: u32,
    scene: u32,
    holder: u32,
    group: u32,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct Metadata {
    chain: Chain,
    capacity: u32,
    count: u32,
    array: u32,
}

pub fn verify_penguin_pal(dol: &[u8], symbols: &[u8]) -> Result<(), String> {
    let symbols =
        std::str::from_utf8(symbols).map_err(|e| format!("symbols file is not UTF-8: {e}"))?;
    if !symbols.lines().any(|l| l.trim() == "__ct__7PenguinFPCc = .text:0x802790E0; // type:function size:0x4C scope:global align:4")
        || !symbols.lines().any(|l| l.trim() == "__vt__7Penguin = .data:0x8059C884; // type:object size:0x78 scope:global align:4") {
        return Err("Penguin constructor/vtable symbols do not match the reviewed PAL map".into());
    }
    let bytes = dol_bytes(dol, PENGUIN_CTOR + 0x10, PENGUIN_CTOR_VPTR_STORE.len())?;
    if bytes != PENGUIN_CTOR_VPTR_STORE {
        return Err("PAL Penguin constructor vptr-store signature mismatch".into());
    }
    Ok(())
}

pub fn ensure_patch_disjoint(writes: &[PatchWrite]) -> Result<(), String> {
    let constructor =
        (PENGUIN_CTOR + 0x10)..(PENGUIN_CTOR + 0x10 + PENGUIN_CTOR_VPTR_STORE.len() as u32);
    let vtable = PENGUIN_VPTR..(PENGUIN_VPTR + 0x78);
    for write in writes {
        let range = write.address
            ..write
                .address
                .checked_add(4)
                .ok_or("AllStars write range overflow")?;
        if overlaps(&range, &constructor) || overlaps(&range, &vtable) {
            return Err(format!("AllStars patch overlaps Penguin identity evidence at 0x{:08x}; refusing observation", write.address));
        }
        for (signature_address, signature) in crate::player_probe::PAL_RUNTIME_SIGNATURES {
            let signature_end = signature_address
                .checked_add(signature.len() as u32)
                .ok_or("PAL runtime signature range overflow")?;
            if overlaps(&range, &(*signature_address..signature_end)) {
                return Err(format!("AllStars patch overlaps PAL runtime identity signature at 0x{signature_address:08x}; refusing observation"));
            }
        }
    }
    Ok(())
}

fn overlaps(a: &std::ops::Range<u32>, b: &std::ops::Range<u32>) -> bool {
    a.start < b.end && b.start < a.end
}

fn dol_bytes(dol: &[u8], address: u32, length: usize) -> Result<&[u8], String> {
    let info = parse_dol(dol).map_err(|e| format!("invalid PAL DOL: {e}"))?;
    let end = u64::from(address)
        .checked_add(length as u64)
        .ok_or("DOL guest range overflow")?;
    let section = info
        .sections
        .iter()
        .find(|s| {
            s.kind == SectionKind::Text
                && address >= s.address
                && end <= u64::from(s.address) + u64::from(s.size)
        })
        .ok_or_else(|| {
            format!("PAL address range 0x{address:08x}+0x{length:x} is not in a text section")
        })?;
    let offset =
        usize::try_from(u64::from(section.file_offset) + u64::from(address - section.address))
            .map_err(|_| "DOL file offset overflow")?;
    let file_end = offset
        .checked_add(length)
        .ok_or("DOL file range overflow")?;
    dol.get(offset..file_end)
        .ok_or_else(|| "PAL DOL section does not contain requested signature".into())
}

fn validate_guest(address: u32, length: usize) -> Result<(), String> {
    mem::plan_read(address, length).map(|_| ())
}

fn read_u32(memory: &mut impl GuestMemory, address: u32) -> Result<u32, String> {
    validate_guest(address, 4)?;
    let bytes = memory.read_guest(address, 4)?;
    mem::decode_be_u32(address, &bytes)
}

fn read_ptr(memory: &mut impl GuestMemory, address: u32, label: &str) -> Result<u32, String> {
    let raw = read_u32(memory, address)?;
    match mem::decode_be_pointer(address, &raw.to_be_bytes())? {
        Some(pointer) => Ok(pointer),
        None => Err(format!("{label} is unavailable (null pointer)")),
    }
}

fn field(base: u32, offset: u32) -> Result<u32, String> {
    base.checked_add(offset)
        .ok_or_else(|| "guest pointer field arithmetic overflow".into())
}

fn metadata(memory: &mut impl GuestMemory) -> Result<Metadata, String> {
    let game_id = memory.read_guest(mem::GUEST_MEM1_BASE, 6)?;
    if game_id != b"RMGP01" {
        return Err(format!(
            "live game identity is not RMGP01: {}",
            String::from_utf8_lossy(&game_id)
        ));
    }
    let game_system = read_ptr(memory, GAME_SYSTEM, "GameSystem singleton")?;
    let controller = read_ptr(
        memory,
        field(game_system, SCENE_CONTROLLER)?,
        "scene controller",
    )?;
    let scene = read_ptr(memory, field(controller, ACTIVE_SCENE)?, "active scene")?;
    let holder = read_ptr(memory, field(scene, SCENE_HOLDER)?, "SceneObjHolder")?;
    let group = read_ptr(memory, field(holder, ALL_LIVE_SLOT)?, "AllLiveActorGroup")?;
    if read_u32(memory, group)? != GROUP_VPTR {
        return Err("AllLiveActorGroup vptr mismatch".into());
    }
    let capacity = read_u32(memory, field(group, GROUP_CAPACITY)?)?;
    if capacity != CAPACITY {
        return Err(format!(
            "AllLiveActorGroup capacity mismatch: 0x{capacity:x}"
        ));
    }
    let count = read_u32(memory, field(group, GROUP_COUNT)?)?;
    if count > capacity {
        return Err(format!(
            "registered actor count {count} exceeds capacity {capacity}"
        ));
    }
    let array = read_ptr(memory, field(group, GROUP_ARRAY)?, "actor pointer array")?;
    let span = u64::from(array)
        .checked_add(
            u64::from(capacity)
                .checked_mul(4)
                .ok_or("actor array span overflow")?,
        )
        .ok_or("actor array end overflow")?;
    if span > u64::from(mem::GUEST_MEM1_LIMIT) {
        return Err("full actor pointer array lies outside canonical MEM1".into());
    }
    Ok(Metadata {
        chain: Chain {
            game_system,
            controller,
            scene,
            holder,
            group,
        },
        capacity,
        count,
        array,
    })
}

#[derive(Default)]
struct Counts {
    null_entries: u32,
    invalid_entries: u32,
    non_penguin: u32,
    total_matches: u32,
    matches: Vec<Candidate>,
    entry_words: Vec<u32>,
}

fn scan_entries(memory: &mut impl GuestMemory, meta: Metadata) -> Result<Counts, String> {
    let mut result = Counts::default();
    let mut index = 0_u32;
    while index < meta.count {
        let chunk_count = ((meta.count - index) as usize).min(mem::MAX_READ_BYTES / 4);
        let address = field(
            meta.array,
            index.checked_mul(4).ok_or("entry offset overflow")?,
        )?;
        let length = chunk_count
            .checked_mul(4)
            .ok_or("entry chunk length overflow")?;
        validate_guest(address, length)?;
        let bytes = memory.read_guest(address, length)?;
        if bytes.len() != length {
            return Err("memory reader returned a short actor-array chunk".into());
        }
        for local in 0..chunk_count {
            let at = local * 4;
            let raw = u32::from_be_bytes(
                bytes[at..at + 4]
                    .try_into()
                    .map_err(|_| "invalid entry word")?,
            );
            result.entry_words.push(raw);
            if raw == 0 {
                result.null_entries += 1;
                continue;
            }
            if raw & 3 != 0
                || raw < mem::GUEST_MEM1_BASE
                || raw >= mem::GUEST_MEM1_LIMIT
                || u64::from(raw) + 4 > u64::from(mem::GUEST_MEM1_LIMIT)
            {
                result.invalid_entries += 1;
                continue;
            }
            let vptr = read_u32(memory, raw)?;
            if vptr == PENGUIN_VPTR {
                result.total_matches += 1;
                if result.matches.len() < MAX_REPORTED_MATCHES {
                    result.matches.push(Candidate {
                        index: index + local as u32,
                        actor: raw,
                        vptr,
                    });
                }
            } else {
                result.non_penguin += 1;
            }
        }
        index += chunk_count as u32;
    }
    Ok(result)
}

pub fn observe(memory: &mut impl GuestMemory) -> Result<Report, String> {
    let started = Instant::now();
    let before = metadata(memory)?;
    let counts = scan_entries(memory, before)?;
    // Recompute every root link and metadata field, then reread the exact registered
    // array range. Any mutation or scene replacement invalidates all candidates.
    let after = metadata(memory)?;
    if before != after {
        return Err("scene/group changed during observation; candidates discarded".into());
    }
    let second = scan_entries(memory, after)?;
    if counts.matches != second.matches
        || counts.entry_words != second.entry_words
        || counts.total_matches != second.total_matches
        || counts.null_entries != second.null_entries
        || counts.invalid_entries != second.invalid_entries
        || counts.non_penguin != second.non_penguin
    {
        return Err(
            "registered actor entries changed during observation; candidates discarded".into(),
        );
    }
    if memory.read_guest(mem::GUEST_MEM1_BASE, 6)? != b"RMGP01" {
        return Err("live game identity changed during observation".into());
    }
    Ok(Report {
        elapsed_ms: started.elapsed().as_millis(),
        scene: before.chain.scene,
        group: before.chain.group,
        capacity: before.capacity,
        count: before.count,
        null_entries: counts.null_entries,
        invalid_entries: counts.invalid_entries,
        non_penguin: counts.non_penguin,
        total_matches: counts.total_matches,
        matches: counts.matches,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::BTreeMap;

    struct Fixture {
        words: BTreeMap<u32, u32>,
        bytes: BTreeMap<u32, Vec<u8>>,
        mutate_after: Option<(usize, u32, u32)>,
        reads: usize,
    }
    impl Fixture {
        fn new(count: u32) -> Self {
            let mut f = Self {
                words: BTreeMap::new(),
                bytes: BTreeMap::new(),
                mutate_after: None,
                reads: 0,
            };
            f.bytes.insert(mem::GUEST_MEM1_BASE, b"RMGP01".to_vec());
            let game = 0x8001_0000;
            let ctrl = 0x8001_0100;
            let scene = 0x8001_0200;
            let holder = 0x8001_0300;
            let group = 0x8001_0400;
            let array = 0x8001_1000;
            for (a, v) in [
                (GAME_SYSTEM, game),
                (game + SCENE_CONTROLLER, ctrl),
                (ctrl + ACTIVE_SCENE, scene),
                (scene + SCENE_HOLDER, holder),
                (holder + ALL_LIVE_SLOT, group),
                (group, GROUP_VPTR),
                (group + GROUP_CAPACITY, CAPACITY),
                (group + GROUP_COUNT, count),
                (group + GROUP_ARRAY, array),
            ] {
                f.words.insert(a, v);
            }
            f
        }
        fn put_entry(&mut self, index: u32, actor: u32, vptr: u32) {
            self.words.insert(0x8001_1000 + index * 4, actor);
            if actor != 0 {
                self.words.insert(actor, vptr);
            }
        }
    }
    impl GuestMemory for Fixture {
        fn read_guest(&mut self, address: u32, length: usize) -> Result<Vec<u8>, String> {
            self.reads += 1;
            if let Some((at, key, value)) = self.mutate_after {
                if self.reads == at {
                    self.words.insert(key, value);
                }
            }
            if let Some(v) = self.bytes.get(&address) {
                return Ok(v[..length.min(v.len())].to_vec());
            }
            let mut out = Vec::new();
            for off in (0..length).step_by(4) {
                let a = address + off as u32;
                let v = *self
                    .words
                    .get(&a)
                    .ok_or_else(|| format!("fixture has no word at {a:08x}"))?;
                out.extend(v.to_be_bytes());
            }
            Ok(out)
        }
    }
    #[test]
    fn observes_exact_vtable_match_and_only_registered_entries() {
        let mut f = Fixture::new(1);
        f.put_entry(0, 0x8001_2000, PENGUIN_VPTR);
        f.put_entry(1, 0x8001_2100, PENGUIN_VPTR);
        let r = observe(&mut f).unwrap();
        assert_eq!(r.total_matches, 1);
        assert_eq!(r.matches[0].index, 0);
    }
    #[test]
    fn invalid_and_null_entries_are_skipped_safely() {
        let mut f = Fixture::new(3);
        f.words.insert(0x8001_1000, 0);
        f.words.insert(0x8001_1004, 0x9000_0000);
        f.put_entry(2, 0x8001_2000, 0x8059_C880);
        let r = observe(&mut f).unwrap();
        assert_eq!(r.null_entries, 1);
        assert_eq!(r.invalid_entries, 1);
        assert_eq!(r.non_penguin, 1);
        assert!(r.matches.is_empty());
    }
    #[test]
    fn rejects_count_and_array_bounds() {
        let mut f = Fixture::new(CAPACITY + 1);
        assert!(observe(&mut f).unwrap_err().contains("exceeds capacity"));
        let mut f = Fixture::new(0);
        f.words.insert(0x8001_0400 + GROUP_ARRAY, 0x817F_FFFC);
        assert!(observe(&mut f)
            .unwrap_err()
            .contains("outside canonical MEM1"));
    }
    #[test]
    fn discards_candidates_when_scene_changes() {
        let mut f = Fixture::new(1);
        f.put_entry(0, 0x8001_2000, PENGUIN_VPTR);
        f.words.insert(0x8001_0500 + SCENE_HOLDER, 0x8001_0600);
        f.words.insert(0x8001_0600 + ALL_LIVE_SLOT, 0x8001_0700);
        for (a, v) in [
            (0x8001_0700, GROUP_VPTR),
            (0x8001_0700 + GROUP_CAPACITY, CAPACITY),
            (0x8001_0700 + GROUP_COUNT, 0),
            (0x8001_0700 + GROUP_ARRAY, 0x8001_3000),
        ] {
            f.words.insert(a, v);
        }
        f.mutate_after = Some((14, 0x8001_0100 + ACTIVE_SCENE, 0x8001_0500));
        assert!(observe(&mut f)
            .unwrap_err()
            .contains("changed during observation"));
    }
    #[test]
    fn rejects_vtable_address_point_variants() {
        let mut f = Fixture::new(1);
        f.put_entry(0, 0x8001_2000, PENGUIN_VPTR + 4);
        let r = observe(&mut f).unwrap();
        assert_eq!(r.total_matches, 0);
    }
    #[test]
    fn arithmetic_and_guest_bounds_are_checked() {
        assert!(field(u32::MAX - 1, 4).is_err());
        assert!(validate_guest(mem::GUEST_MEM1_LIMIT - 2, 4).is_err());
    }
}
