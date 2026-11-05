//! Read-only resolver for the exact Petari-matched PAL Rev 0 Mario object chain.
use crate::gdb::DolphinGdb;
use multigalaxy_patch::{
    branch_target, parse_dol, sha256, DiscIdentity, RevisionCatalog, SectionKind,
};

pub const GAME_SYSTEM_SINGLETON: u32 = 0x806A_1228;
pub const PAL_RUNTIME_SIGNATURES: &[(u32, &[u8])] = &[
    (
        0x802E_153C,
        &[0x80, 0x63, 0x00, 0x0c, 0x4e, 0x80, 0x00, 0x20],
    ),
    (
        0x8034_4A70,
        &[
            0x54, 0x80, 0x10, 0x3a, 0x7c, 0x63, 0x00, 0x2e, 0x4e, 0x80, 0x00, 0x20,
        ],
    ),
    (
        0x8039_D468,
        &[
            0x80, 0x63, 0x00, 0xac, 0x80, 0x63, 0x00, 0x10, 0x4e, 0x80, 0x00, 0x20,
        ],
    ),
];
const PAL_SYMBOLS_SHA256: [u8; 32] = [
    0x0d, 0x63, 0x23, 0xc1, 0x32, 0x87, 0x71, 0xc3, 0xb9, 0x40, 0x37, 0xb6, 0xf8, 0xba, 0xf1, 0x0f,
    0x64, 0x90, 0x62, 0x29, 0xb5, 0xdf, 0x39, 0x4d, 0x2c, 0x5e, 0x4d, 0x2b, 0xa8, 0x92, 0x66, 0x00,
];

const GAME_SYSTEM_SCENE_CONTROLLER: u32 = 0x24;
const SCENE_CONTROLLER_SCENE: u32 = 0xac;
const SCENE_SCENE_OBJECT_HOLDER: u32 = 0x10;
const SCENE_OBJECT_MARIO_HOLDER_SLOT: u32 = 0x50;
const MARIO_HOLDER_ACTOR: u32 = 0x0c;
const LIVE_ACTOR_POSITION: u32 = 0x0c;

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct PositionSample {
    pub game_system: u32,
    pub scene_controller: u32,
    pub scene: u32,
    pub scene_object_holder: u32,
    pub mario_holder: u32,
    pub mario_actor: u32,
    pub x: f32,
    pub y: f32,
    pub z: f32,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ProbeState {
    Waiting(&'static str),
}

pub trait GuestMemory {
    fn read_guest(&mut self, address: u32, length: usize) -> Result<Vec<u8>, String>;
}

impl GuestMemory for DolphinGdb {
    fn read_guest(&mut self, address: u32, length: usize) -> Result<Vec<u8>, String> {
        self.read_memory(address, length)
    }
}

#[cfg(windows)]
impl GuestMemory for crate::stock_memory::StockMem1 {
    fn read_guest(&mut self, address: u32, length: usize) -> Result<Vec<u8>, String> {
        crate::stock_memory::StockMem1::read_guest(self, address, length)
    }
}

#[derive(Debug, Clone)]
pub struct VerifiedPalMap {
    pub bss_address: u32,
    pub bss_size: u32,
}

/// Accept only the exact PAL DOL and exact Petari symbols file that were checked in this repo.
pub fn verify_pal_inputs(dol: &[u8], symbols: &[u8]) -> Result<VerifiedPalMap, String> {
    if sha256(symbols) != PAL_SYMBOLS_SHA256 {
        return Err("Petari symbols file hash is not the reviewed RMGP01 map".into());
    }
    let info = parse_dol(dol).map_err(|e| format!("invalid PAL DOL: {e}"))?;
    let disc = DiscIdentity {
        game_id: "RMGP01".into(),
        revision: 0,
    };
    RevisionCatalog::new()
        .identify(&disc, dol)
        .map_err(|e| format!("PAL DOL identity is not verified: {e}"))?;
    if !contains_guest_range(info.bss_address, info.bss_size, GAME_SYSTEM_SINGLETON, 4) {
        return Err(format!(
            "GameSystem singleton 0x{GAME_SYSTEM_SINGLETON:08x} is outside DOL BSS 0x{:08x}+0x{:x}",
            info.bss_address, info.bss_size
        ));
    }

    let text =
        std::str::from_utf8(symbols).map_err(|e| format!("symbols file is not UTF-8: {e}"))?;
    for (name, section, address, size) in [
        (
            "sInstance__29SingletonHolder<10GameSystem>",
            "sbss",
            0x806A_1228,
            8,
        ),
        ("getMarioActor__11MarioHolderCFv", "text", 0x802E_153C, 8),
        ("getMarioHolder__2MRFv", "text", 0x802E_1544, 0x28),
        ("getPlayerActor__11MarioAccessFv", "text", 0x8030_4220, 0x24),
        ("getObj__14SceneObjHolderCFi", "text", 0x8034_4A70, 0x0c),
        ("getSceneObjHolder__2MRFv", "text", 0x8034_4AC4, 0x0c),
        (
            "getSceneObjHolder__25GameSystemSceneControllerCFv",
            "text",
            0x8039_D468,
            0x0c,
        ),
    ] {
        require_symbol(text, name, section, address, size)?;
    }

    // These exact function bytes belong to the identified PAL DOL and establish the
    // pointer-field loads and call targets used by the runtime chain.
    require_dol_bytes(
        dol,
        0x802E_153C,
        &[0x80, 0x63, 0x00, 0x0c, 0x4e, 0x80, 0x00, 0x20],
    )?;
    require_dol_bytes(
        dol,
        0x8034_4A70,
        &[
            0x54, 0x80, 0x10, 0x3a, 0x7c, 0x63, 0x00, 0x2e, 0x4e, 0x80, 0x00, 0x20,
        ],
    )?;
    require_dol_bytes(
        dol,
        0x8039_D468,
        &[
            0x80, 0x63, 0x00, 0xac, 0x80, 0x63, 0x00, 0x10, 0x4e, 0x80, 0x00, 0x20,
        ],
    )?;
    let actor_access = dol_bytes(dol, 0x8030_4220, 0x24)?;
    let first_call = u32::from_be_bytes(actor_access[0x0c..0x10].try_into().unwrap());
    let second_call = u32::from_be_bytes(actor_access[0x10..0x14].try_into().unwrap());
    if branch_target(first_call, 0x8030_422c) != Some(0x802e_1544)
        || branch_target(second_call, 0x8030_4230) != Some(0x802e_153c)
    {
        return Err(
            "PAL MarioAccess call targets do not match the reviewed MarioHolder path".into(),
        );
    }
    let holder = dol_bytes(dol, 0x802e_1544, 0x28)?;
    let holder_calls = [
        u32::from_be_bytes(holder[0x0c..0x10].try_into().unwrap()),
        u32::from_be_bytes(holder[0x14..0x18].try_into().unwrap()),
    ];
    if branch_target(holder_calls[0], 0x802e_1550) != Some(0x8034_4ac4)
        || branch_target(holder_calls[1], 0x802e_1558) != Some(0x8034_4a70)
        || holder[0x10..0x14] != [0x38, 0x80, 0x00, 0x14]
    {
        return Err("PAL MarioHolder call path or SceneObj_MarioHolder ID does not match".into());
    }
    require_dol_bytes(
        dol,
        0x8034_4ac4,
        &[
            0x80, 0x6d, 0xc5, 0x88, 0x80, 0x63, 0x00, 0x24, 0x48, 0x05, 0x89, 0x9c,
        ],
    )?;
    let scene_holder_call = u32::from_be_bytes(dol_bytes(dol, 0x8034_4acc, 4)?.try_into().unwrap());
    if branch_target(scene_holder_call, 0x8034_4acc) != Some(0x8039_d468) {
        return Err(
            "PAL scene-holder call does not target the verified controller accessor".into(),
        );
    }

    Ok(VerifiedPalMap {
        bss_address: info.bss_address,
        bss_size: info.bss_size,
    })
}

/// Confirm the live DOL still contains exact PAL Rev 0 function signatures at
/// selected sites not expected to be touched by the required AllStars patch.
/// This is an identity check, not a general-purpose code scan.
pub fn verify_live_pal_signatures(memory: &mut impl GuestMemory, dol: &[u8]) -> Result<(), String> {
    for &(address, expected) in PAL_RUNTIME_SIGNATURES {
        if dol_bytes(dol, address, expected.len())? != expected {
            return Err(format!(
                "local PAL identity signature differs at 0x{address:08x}"
            ));
        }
        let observed = read_checked(memory, address, expected.len(), "PAL runtime signature")?;
        if observed != expected {
            return Err(format!(
                "live memory does not match PAL RMGP01 Rev 0 at 0x{address:08x}"
            ));
        }
    }
    Ok(())
}

fn require_symbol(
    text: &str,
    name: &str,
    section: &str,
    expected_address: u32,
    expected_size: u32,
) -> Result<(), String> {
    let prefix = format!("{name} = .{section}:0x");
    let line = text
        .lines()
        .find(|line| line.starts_with(&prefix))
        .ok_or_else(|| format!("Petari PAL symbols are missing {name}"))?;
    let address_text = line[prefix.len()..]
        .split(|c: char| !c.is_ascii_hexdigit())
        .next()
        .unwrap_or_default();
    let address = u32::from_str_radix(address_text, 16)
        .map_err(|_| format!("Petari symbol {name} has an invalid address"))?;
    let size_marker = "size:0x";
    let size_text = line
        .split_once(size_marker)
        .map(|(_, tail)| {
            tail.split(|c: char| !c.is_ascii_hexdigit())
                .next()
                .unwrap_or_default()
        })
        .ok_or_else(|| format!("Petari symbol {name} has no size"))?;
    let size = u32::from_str_radix(size_text, 16)
        .map_err(|_| format!("Petari symbol {name} has an invalid size"))?;
    if address != expected_address || size != expected_size {
        return Err(format!(
            "Petari symbol {name} mismatch: expected 0x{expected_address:08x}/0x{expected_size:x}, found 0x{address:08x}/0x{size:x}"
        ));
    }
    Ok(())
}

fn dol_bytes(dol: &[u8], address: u32, length: usize) -> Result<&[u8], String> {
    let info = parse_dol(dol).map_err(|e| format!("invalid DOL: {e}"))?;
    let end = u64::from(address) + length as u64;
    let section = info
        .sections
        .iter()
        .find(|s| {
            s.kind == SectionKind::Text
                && address >= s.address
                && end <= u64::from(s.address) + u64::from(s.size)
        })
        .ok_or_else(|| {
            format!("verified function 0x{address:08x}+0x{length:x} is outside PAL text")
        })?;
    let offset = section.file_offset as usize + (address - section.address) as usize;
    Ok(&dol[offset..offset + length])
}

fn require_dol_bytes(dol: &[u8], address: u32, expected: &[u8]) -> Result<(), String> {
    if dol_bytes(dol, address, expected.len())? != expected {
        return Err(format!("PAL code signature differs at 0x{address:08x}"));
    }
    Ok(())
}

fn contains_guest_range(base: u32, size: u32, address: u32, length: usize) -> bool {
    let start = u64::from(base);
    let end = start + u64::from(size);
    u64::from(address) >= start && u64::from(address) + length as u64 <= end
}

pub fn sample_position(
    memory: &mut impl GuestMemory,
    map: &VerifiedPalMap,
) -> Result<Result<PositionSample, ProbeState>, String> {
    let system = read_pointer(memory, GAME_SYSTEM_SINGLETON, "GameSystem singleton")?;
    let Some(game_system) = system else {
        return Ok(Err(ProbeState::Waiting(
            "GameSystem singleton is not initialized",
        )));
    };
    let Some(scene_controller) = read_pointer(
        memory,
        checked_field(game_system, GAME_SYSTEM_SCENE_CONTROLLER)?,
        "GameSystem scene controller",
    )?
    else {
        return Ok(Err(ProbeState::Waiting(
            "scene controller is not initialized",
        )));
    };
    let Some(scene) = read_pointer(
        memory,
        checked_field(scene_controller, SCENE_CONTROLLER_SCENE)?,
        "active scene",
    )?
    else {
        return Ok(Err(ProbeState::Waiting("no active scene")));
    };
    let Some(scene_holder) = read_pointer(
        memory,
        checked_field(scene, SCENE_SCENE_OBJECT_HOLDER)?,
        "scene object holder",
    )?
    else {
        return Ok(Err(ProbeState::Waiting(
            "scene object holder is not initialized",
        )));
    };
    let Some(mario_holder) = read_pointer(
        memory,
        checked_field(scene_holder, SCENE_OBJECT_MARIO_HOLDER_SLOT)?,
        "MarioHolder slot",
    )?
    else {
        return Ok(Err(ProbeState::Waiting(
            "MarioHolder has not been created in this scene",
        )));
    };
    let Some(mario_actor) = read_pointer(
        memory,
        checked_field(mario_holder, MARIO_HOLDER_ACTOR)?,
        "MarioHolder::mActor",
    )?
    else {
        return Ok(Err(ProbeState::Waiting(
            "MarioActor is not attached to MarioHolder",
        )));
    };
    let position_address = checked_field(mario_actor, LIVE_ACTOR_POSITION)?;
    let bytes = read_checked(memory, position_address, 12, "MarioActor::mPosition")?;
    let x = f32::from_bits(u32::from_be_bytes(bytes[0..4].try_into().unwrap()));
    let y = f32::from_bits(u32::from_be_bytes(bytes[4..8].try_into().unwrap()));
    let z = f32::from_bits(u32::from_be_bytes(bytes[8..12].try_into().unwrap()));
    if ![x, y, z]
        .into_iter()
        .all(|v| v.is_finite() && v.abs() < 10_000_000.0)
    {
        return Err(format!("MarioActor::mPosition at 0x{position_address:08x} contains invalid floats: {x}, {y}, {z}"));
    }
    if !contains_guest_range(map.bss_address, map.bss_size, GAME_SYSTEM_SINGLETON, 4) {
        return Err("verified BSS range changed after map validation".into());
    }
    Ok(Ok(PositionSample {
        game_system,
        scene_controller,
        scene,
        scene_object_holder: scene_holder,
        mario_holder,
        mario_actor,
        x,
        y,
        z,
    }))
}

fn checked_field(base: u32, offset: u32) -> Result<u32, String> {
    let address = base
        .checked_add(offset)
        .ok_or("pointer field address overflow")?;
    if !is_guest_ram_range(address, 4) {
        return Err(format!("pointer field 0x{address:08x} is outside Wii RAM"));
    }
    Ok(address)
}

fn read_pointer(
    memory: &mut impl GuestMemory,
    address: u32,
    label: &str,
) -> Result<Option<u32>, String> {
    let bytes = read_checked(memory, address, 4, label)?;
    let pointer = u32::from_be_bytes(bytes.try_into().unwrap());
    if pointer == 0 {
        return Ok(None);
    }
    if pointer % 4 != 0 || !is_guest_ram_range(pointer, 4) {
        return Err(format!(
            "{label} contains invalid guest pointer 0x{pointer:08x}"
        ));
    }
    Ok(Some(pointer))
}

fn read_checked(
    memory: &mut impl GuestMemory,
    address: u32,
    length: usize,
    label: &str,
) -> Result<Vec<u8>, String> {
    if !is_guest_ram_range(address, length) {
        return Err(format!(
            "{label} read 0x{address:08x}+0x{length:x} is outside Wii RAM"
        ));
    }
    memory
        .read_guest(address, length)
        .map_err(|e| format!("{label} read failed: {e}"))
}

pub fn is_guest_ram_range(address: u32, length: usize) -> bool {
    let start = u64::from(address);
    let end = start.saturating_add(length as u64);
    (start >= 0x8000_0000 && end <= 0x8180_0000) || (start >= 0x9000_0000 && end <= 0x9400_0000)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    #[derive(Default)]
    struct Memory(HashMap<u32, u8>);

    impl Memory {
        fn put(&mut self, address: u32, bytes: &[u8]) {
            for (i, byte) in bytes.iter().enumerate() {
                self.0.insert(address + i as u32, *byte);
            }
        }
        fn put_ptr(&mut self, address: u32, value: u32) {
            self.put(address, &value.to_be_bytes());
        }
        fn put_f32(&mut self, address: u32, value: f32) {
            self.put(address, &value.to_bits().to_be_bytes());
        }
    }

    impl GuestMemory for Memory {
        fn read_guest(&mut self, address: u32, length: usize) -> Result<Vec<u8>, String> {
            (0..length)
                .map(|i| {
                    self.0.get(&(address + i as u32)).copied().ok_or_else(|| {
                        format!("missing fixture byte at 0x{:08x}", address + i as u32)
                    })
                })
                .collect()
        }
    }

    fn full_chain() -> (Memory, VerifiedPalMap) {
        let mut m = Memory::default();
        m.put_ptr(GAME_SYSTEM_SINGLETON, 0x8000_1000);
        m.put_ptr(0x8000_1000 + GAME_SYSTEM_SCENE_CONTROLLER, 0x8000_2000);
        m.put_ptr(0x8000_2000 + SCENE_CONTROLLER_SCENE, 0x8000_3000);
        m.put_ptr(0x8000_3000 + SCENE_SCENE_OBJECT_HOLDER, 0x8000_4000);
        m.put_ptr(0x8000_4000 + SCENE_OBJECT_MARIO_HOLDER_SLOT, 0x8000_5000);
        m.put_ptr(0x8000_5000 + MARIO_HOLDER_ACTOR, 0x8000_6000);
        m.put_f32(0x8000_6000 + LIVE_ACTOR_POSITION, 12.5);
        m.put_f32(0x8000_6000 + LIVE_ACTOR_POSITION + 4, -3.25);
        m.put_f32(0x8000_6000 + LIVE_ACTOR_POSITION + 8, 99.0);
        (
            m,
            VerifiedPalMap {
                bss_address: 0x806A_0000,
                bss_size: 0x10000,
            },
        )
    }

    #[test]
    fn resolves_exact_chain_and_decodes_big_endian_position() {
        let (mut memory, map) = full_chain();
        let sample = sample_position(&mut memory, &map).unwrap().unwrap();
        assert_eq!((sample.x, sample.y, sample.z), (12.5, -3.25, 99.0));
        assert_eq!(sample.mario_actor, 0x8000_6000);
    }

    #[test]
    fn null_player_chain_waits_without_scanning_or_guessing() {
        let mut memory = Memory::default();
        memory.put_ptr(GAME_SYSTEM_SINGLETON, 0);
        let map = VerifiedPalMap {
            bss_address: 0x806A_0000,
            bss_size: 0x10000,
        };
        assert_eq!(
            sample_position(&mut memory, &map).unwrap(),
            Err(ProbeState::Waiting(
                "GameSystem singleton is not initialized"
            ))
        );
    }

    #[test]
    fn rejects_bad_pointer_and_nonfinite_position() {
        let (mut memory, map) = full_chain();
        memory.put_ptr(0x8000_1000 + GAME_SYSTEM_SCENE_CONTROLLER, 0x1234);
        assert!(sample_position(&mut memory, &map)
            .unwrap_err()
            .contains("invalid guest pointer"));
        let (mut memory, map) = full_chain();
        memory.put_f32(0x8000_6000 + LIVE_ACTOR_POSITION, f32::NAN);
        assert!(sample_position(&mut memory, &map)
            .unwrap_err()
            .contains("invalid floats"));
    }
}
