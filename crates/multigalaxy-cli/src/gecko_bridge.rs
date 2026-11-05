//! Reversible Gecko-code mailbox experiment for stock Dolphin.
//!
//! The code handler already reserves 0x80001800..0x80003000. The probe embeds
//! its mailbox in its own C0 execute block, so it does not borrow game globals
//! or write to the DOL/RVZ. The host locates the marker inside that bounded
//! handler region before it is allowed to write the acknowledgement word.

pub const HANDLER_BASE: u32 = 0x8000_1800;
pub const HANDLER_END: u32 = 0x8000_3000;
// The externally supplied PAL AllStars patch places its code cave here. Keep
// the probe's entire Gecko block below this first observed overlapping write.
const ALLSTARS_CAVE_START: u32 = 0x8000_2790;
const MARKER: u32 = 0x4d47_3142; // "MG1B"
const BODY_BYTES: u32 = 72;
const MARKER_OFFSET: usize = 60;

const BODY_WORDS: [u32; 18] = [
    0x7c08_02a6, // mflr r0 (preserve caller link register in volatile r0)
    0x4800_0005, // bl +4 (capture a PC-relative pointer)
    0x7d88_02a6, // mflr r12
    0x398c_0028, // addi r12,r12,40 (mailbox begins at body+48)
    0x816c_0000, // lwz r11,0(r12) -- frame counter
    0x396b_0001, // addi r11,r11,1
    0x916c_0000, // stw r11,0(r12)
    0x814c_0004, // lwz r10,4(r12) -- host acknowledgement
    0x914c_0008, // stw r10,8(r12) -- game-observed echo
    0x7c08_03a6, // mtlr r0 (restore Gecko caller's return address)
    0x4800_0018, // b +24 (skip mutable mailbox data)
    0x6000_0000, // nop
    0,           // mailbox counter
    0,           // mailbox acknowledgement
    0,           // mailbox acknowledgement echo
    MARKER,      // validated marker, never modified
    0x4e80_0020, // blr
    0x6000_0000, // nop
];

pub const CODE_NAME: &str = "MultiGalaxy1 Stock Dolphin Mailbox Probe";

/// Add the local-only test Gecko code after the mandatory AllStars OnFrame
/// section. The game image remains untouched; a fresh isolated Dolphin profile
/// owns this temporary GameSettings entry.
pub fn append_code(settings: &mut String) {
    settings.push_str(&format!(
        "\n[Gecko]\n${CODE_NAME} [MultiGalaxy1]\nC0000000 00000009\n"
    ));
    for pair in BODY_WORDS.chunks_exact(2) {
        settings.push_str(&format!("{:08X} {:08X}\n", pair[0], pair[1]));
    }
    settings.push_str(&format!("\n[Gecko_Enabled]\n${CODE_NAME}\n"));
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Mailbox {
    counter: u32,
    acknowledgement: u32,
    echo: u32,
}

impl Mailbox {
    pub fn counter_address(self) -> u32 {
        self.counter
    }

    pub fn acknowledgement_address(self) -> u32 {
        self.acknowledgement
    }

    pub fn echo_address(self) -> u32 {
        self.echo
    }

    pub(crate) fn is_inside_handler(self) -> bool {
        [self.counter, self.acknowledgement, self.echo]
            .into_iter()
            .all(|address| {
                address >= HANDLER_BASE
                    && u64::from(address) + 4 <= u64::from(HANDLER_END)
                    && address < ALLSTARS_CAVE_START
                    && address & 3 == 0
            })
            && self.acknowledgement == self.counter + 4
            && self.echo == self.counter + 8
    }
}

/// Find exactly one active probe body in Dolphin's fixed Gecko handler region.
/// The routine bytes are checked around its marker; mutable mailbox words are
/// intentionally excluded from the comparison.
pub fn locate_mailbox(
    region_base: u32,
    handler_size: usize,
    bytes: &[u8],
) -> Result<Option<Mailbox>, String> {
    if region_base != HANDLER_BASE || bytes.len() != (HANDLER_END - HANDLER_BASE) as usize {
        return Err("memory view does not cover the exact Gecko code-handler region".into());
    }
    if handler_size % 4 != 0 || handler_size > bytes.len() {
        return Err("Dolphin codehandler.bin has an invalid size".into());
    }

    let mut found = None;
    for offset in (handler_size + 8..bytes.len().saturating_sub(BODY_BYTES as usize - 4)).step_by(4)
    {
        let word = u32::from_be_bytes(bytes[offset..offset + 4].try_into().unwrap());
        if word != MARKER {
            continue;
        }
        let Some(body_start) = offset.checked_sub(MARKER_OFFSET) else {
            continue;
        };
        let words: Vec<u32> = bytes[body_start..body_start + BODY_BYTES as usize]
            .chunks_exact(4)
            .map(|chunk| u32::from_be_bytes(chunk.try_into().unwrap()))
            .collect();
        let valid = words.len() == BODY_WORDS.len()
            && BODY_WORDS.iter().enumerate().all(|(index, expected)| {
                matches!(index, 12 | 13 | 14) || words[index] == *expected
            });
        if !valid {
            return Err(
                "found Gecko probe marker, but the PPC routine signature is invalid".into(),
            );
        }
        if found.is_some() {
            return Err("multiple Gecko probe markers found in Dolphin's handler region".into());
        }
        let body_address = region_base + body_start as u32;
        if u64::from(body_address) + u64::from(BODY_BYTES) > u64::from(ALLSTARS_CAVE_START) {
            return Err("Gecko probe body overlaps the PAL AllStars code cave; refusing to use this code-list layout".into());
        }
        let mailbox = Mailbox {
            counter: body_address + 48,
            acknowledgement: body_address + 52,
            echo: body_address + 56,
        };
        if !mailbox.is_inside_handler() {
            return Err("located mailbox is outside Dolphin's reserved Gecko region".into());
        }
        found = Some(mailbox);
    }
    Ok(found)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture() -> Vec<u8> {
        let mut bytes = vec![0; (HANDLER_END - HANDLER_BASE) as usize];
        let offset = 2880 + 16;
        for (index, word) in BODY_WORDS.iter().enumerate() {
            bytes[offset + index * 4..offset + index * 4 + 4].copy_from_slice(&word.to_be_bytes());
        }
        bytes
    }

    #[test]
    fn emits_frame_counter_and_ack_echo_as_gecko_execute_code() {
        let mut settings = "[OnFrame_Enabled]\nAllStars\n".to_owned();
        append_code(&mut settings);
        assert!(settings.contains("C0000000 00000009"));
        assert!(settings.contains("7C0802A6 48000005"));
        assert!(settings.contains("914C0008 7C0803A6"));
        assert!(settings.contains("48000018 60000000"));
        assert!(settings.contains("4E800020 60000000"));
        assert!(settings.ends_with("[Gecko_Enabled]\n$MultiGalaxy1 Stock Dolphin Mailbox Probe\n"));
    }

    #[test]
    fn locates_mailbox_only_from_unique_verified_handler_signature() {
        let mailbox = locate_mailbox(HANDLER_BASE, 2880, &fixture())
            .unwrap()
            .unwrap();
        assert_eq!(mailbox.counter_address(), HANDLER_BASE + 2880 + 16 + 48);
        assert_eq!(
            mailbox.acknowledgement_address(),
            mailbox.counter_address() + 4
        );
        assert_eq!(mailbox.echo_address(), mailbox.counter_address() + 8);
        assert!(mailbox.is_inside_handler());
    }

    #[test]
    fn rejects_wrong_bounds_corruption_and_duplicate_probe() {
        let bytes = fixture();
        assert!(locate_mailbox(HANDLER_BASE + 4, 2880, &bytes).is_err());
        assert!(locate_mailbox(HANDLER_BASE, 2881, &bytes).is_err());
        assert_eq!(
            locate_mailbox(HANDLER_BASE, 2880, &vec![0; bytes.len()]).unwrap(),
            None
        );
        let mut corrupt = bytes.clone();
        corrupt[2880 + 16 + 12] ^= 1;
        assert!(locate_mailbox(HANDLER_BASE, 2880, &corrupt).is_err());
        let mut duplicate = bytes;
        duplicate[3000..3072].copy_from_slice(&fixture()[2880 + 16..2880 + 88]);
        assert!(locate_mailbox(HANDLER_BASE, 2880, &duplicate).is_err());
    }

    #[test]
    fn refuses_a_probe_block_that_reaches_the_allstars_code_cave() {
        let mut bytes = vec![0; (HANDLER_END - HANDLER_BASE) as usize];
        let body_offset = (ALLSTARS_CAVE_START - HANDLER_BASE) as usize - BODY_BYTES as usize + 4;
        for (index, word) in BODY_WORDS.iter().enumerate() {
            bytes[body_offset + index * 4..body_offset + index * 4 + 4]
                .copy_from_slice(&word.to_be_bytes());
        }
        assert!(locate_mailbox(HANDLER_BASE, 2880, &bytes)
            .unwrap_err()
            .contains("overlaps the PAL AllStars code cave"));
    }
}
