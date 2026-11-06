//! Stage 1 trampoline dry-run: host-side, read-only verification of the hook at
//! `0x802B62E4` (docs/references/draw-only-hook-contract.md).
//!
//! The guest-side installer lives in `gecko_bridge` (C0 block, opt-in via
//! `dev-launch --trampoline-dry-run`). This module never writes guest memory: it
//! validates the PAL layout offline and classifies the live hook word.

use crate::{
    gecko_bridge, memory_inspection as mem,
    player_probe::{self, GuestMemory},
};
use multigalaxy_patch::{parse_dol, SectionKind};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum HookState {
    /// Original `bl directDraw` word is in place (hook not installed / rollback done).
    Original,
    /// A plain `b` branch whose target holds the exact 8-word stub template.
    Installed { target: u32 },
    /// Anything else.
    Unexpected { word: u32 },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct HookReport {
    pub hook_word: u32,
    pub state: HookState,
}

/// The exact 8-word stub the installer's hook branch must target.
pub fn stub_template() -> [u32; gecko_bridge::STUB_WORDS_PUBLIC] {
    gecko_bridge::stub_template_public()
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

/// Offline gate: the PAL DOL must contain the exact original `bl` word at the
/// hook address, proving the derivation before any live check.
pub fn verify_hook_layout(dol: &[u8], symbols: &[u8]) -> Result<(), String> {
    player_probe::verify_pal_inputs(dol, symbols)?;
    let info = parse_dol(dol).map_err(|error| format!("invalid PAL DOL: {error}"))?;
    let address = gecko_bridge::HOOK_ADDRESS;
    let section = info
        .sections
        .iter()
        .find(|section| {
            section.kind == SectionKind::Text
                && address >= section.address
                && u64::from(address) + 4 <= u64::from(section.address) + u64::from(section.size)
        })
        .ok_or("hook address is outside a DOL text section")?;
    let offset =
        usize::try_from(u64::from(section.file_offset) + u64::from(address - section.address))
            .map_err(|_| "hook address file offset overflow")?;
    let bytes = dol
        .get(offset..offset + 4)
        .ok_or("PAL DOL is truncated at the hook address")?;
    let word = u32::from_be_bytes(bytes.try_into().expect("four bytes"));
    if word != gecko_bridge::ORIGINAL_HOOK_WORD {
        return Err(format!(
            "PAL DOL hook word at 0x{address:08x} is 0x{word:08x}, expected 0x{:08x}",
            gecko_bridge::ORIGINAL_HOOK_WORD
        ));
    }
    Ok(())
}

/// The mandatory AllStars patch must never write the hook word.
pub fn ensure_hook_disjoint(writes: &[multigalaxy_patch::PatchWrite]) -> Result<(), String> {
    let (start, end) = (
        u64::from(gecko_bridge::HOOK_ADDRESS),
        u64::from(gecko_bridge::HOOK_ADDRESS) + 4,
    );
    if writes.iter().any(|write| {
        let write_start = u64::from(write.address);
        let write_end = write_start + 4;
        write_start < end && start < write_end
    }) {
        return Err(format!(
            "required AllStars patch overlaps the trampoline hook word at 0x{start:08x}"
        ));
    }
    Ok(())
}

/// Launcher-time pre-flight: the hook word must be either the exact original
/// `bl directDraw` word (not yet armed) or a valid installed branch with the
/// stub template at its target. Anything else means the word was corrupted by
/// something else and the hook must not be considered armed.
pub fn preflight_hook(memory: &mut impl GuestMemory) -> Result<(), String> {
    match check_live(memory)?.state {
        HookState::Original | HookState::Installed { .. } => Ok(()),
        HookState::Unexpected { word } => Err(format!(
            "pre-flight refused: hook word at 0x{:08x} is 0x{word:08x}, expected 0x{:08x} or a valid branch",
            gecko_bridge::HOOK_ADDRESS,
            gecko_bridge::ORIGINAL_HOOK_WORD
        )),
    }
}

/// Read-only live classification of the hook word. An installed hook must be a
/// plain (non-linking) `b` whose target holds the exact stub template.
pub fn check_live(memory: &mut impl GuestMemory) -> Result<HookReport, String> {
    let hook_word = read_word(memory, gecko_bridge::HOOK_ADDRESS)?;
    if hook_word == gecko_bridge::ORIGINAL_HOOK_WORD {
        return Ok(HookReport {
            hook_word,
            state: HookState::Original,
        });
    }
    let opcode = hook_word >> 26;
    let links = hook_word & 1 == 1;
    if opcode == 18 && !links {
        let displacement = ((hook_word << 6) as i32) >> 6;
        let target = (gecko_bridge::HOOK_ADDRESS as i64 + 4 + displacement as i64) as u32;
        let mut stub = [0u32; gecko_bridge::STUB_WORDS_PUBLIC];
        for (index, slot) in stub.iter_mut().enumerate() {
            *slot = read_word(
                memory,
                target
                    .checked_add((index * 4) as u32)
                    .ok_or("stub address overflow")?,
            )?;
        }
        if stub == stub_template() {
            return Ok(HookReport {
                hook_word,
                state: HookState::Installed { target },
            });
        }
    }
    Ok(HookReport {
        hook_word,
        state: HookState::Unexpected { word: hook_word },
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::BTreeMap;

    #[test]
    fn stub_template_calls_direct_draw_then_returns_to_the_callsite() {
        let stub = stub_template();
        // lis r11,0x802A; ori r11,r11,0x6B20 -> r11 = directDraw; mtctr; bctrl
        assert_eq!(stub[0], 0x3D60_802A);
        assert_eq!(stub[1], 0x616B_6B20);
        assert_eq!(stub[2], 0x7D60_03A6);
        assert_eq!(stub[3], 0x4E80_0421);
        // lis r11,0x802B; ori r11,r11,0x62E8 -> r11 = hook return; mtctr; bctr
        assert_eq!(stub[4], 0x3D60_802B);
        assert_eq!(stub[5], 0x616B_62E8);
        assert_eq!(stub[6], 0x7D60_03A6);
        assert_eq!(stub[7], 0x4E80_0420);
    }

    #[derive(Default)]
    struct Memory {
        words: BTreeMap<u32, u32>,
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

    fn installed_fixture() -> (Memory, u32) {
        let target = 0x8000_2400;
        let displacement = target as i64 - (gecko_bridge::HOOK_ADDRESS as i64 + 4);
        let hook_word = 0x4800_0000 | ((displacement as u32) & 0x03FF_FFFC);
        let mut memory = Memory::default();
        memory.words.insert(gecko_bridge::HOOK_ADDRESS, hook_word);
        for (index, word) in stub_template().iter().enumerate() {
            memory.words.insert(target + (index * 4) as u32, *word);
        }
        (memory, target)
    }

    #[test]
    fn classifies_original_installed_and_unexpected_hook_words() {
        let mut original = Memory::default();
        original
            .words
            .insert(gecko_bridge::HOOK_ADDRESS, gecko_bridge::ORIGINAL_HOOK_WORD);
        let report = check_live(&mut original).unwrap();
        assert_eq!(report.state, HookState::Original);

        let (mut installed, target) = installed_fixture();
        let report = check_live(&mut installed).unwrap();
        assert_eq!(report.state, HookState::Installed { target });

        let mut unexpected = Memory::default();
        unexpected
            .words
            .insert(gecko_bridge::HOOK_ADDRESS, 0x60000000);
        assert_eq!(
            check_live(&mut unexpected).unwrap().state,
            HookState::Unexpected { word: 0x60000000 }
        );

        let (mut bad_stub, _) = installed_fixture();
        bad_stub.words.insert(0x8000_2400, 0x60000000);
        assert!(matches!(
            check_live(&mut bad_stub).unwrap().state,
            HookState::Unexpected { .. }
        ));
    }

    #[test]
    fn rejects_allstars_writes_overlapping_the_hook_word() {
        assert!(ensure_hook_disjoint(&[multigalaxy_patch::PatchWrite {
            address: gecko_bridge::HOOK_ADDRESS,
            value: 0,
        }])
        .is_err());
        assert!(ensure_hook_disjoint(&[multigalaxy_patch::PatchWrite {
            address: 0x8020_0000,
            value: 0,
        }])
        .is_ok());
    }
}
