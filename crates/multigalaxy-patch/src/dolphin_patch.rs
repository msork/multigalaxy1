use crate::{parse_dol, SectionKind};
use std::collections::BTreeMap;
use std::fmt;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct PatchWrite {
    pub address: u32,
    pub value: u32,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum DolphinPatchError {
    InvalidLine(usize),
    DuplicateAddress(u32),
    InvalidAllStarsBase,
    InvalidSwapPatch(&'static str),
    InvalidDol(String),
    UnsupportedWriteAddress(u32),
}

impl fmt::Display for DolphinPatchError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidLine(line) => write!(f, "invalid Dolphin OnFrame patch line {line}"),
            Self::DuplicateAddress(address) => write!(f, "duplicate patch write at 0x{address:08x}"),
            Self::InvalidAllStarsBase => write!(f, "input is not the expected PAL AllStars base patch"),
            Self::InvalidSwapPatch(name) => write!(f, "invalid PAL {name} swap patch"),
            Self::InvalidDol(message) => write!(f, "invalid DOL: {message}"),
            Self::UnsupportedWriteAddress(address) => write!(f, "patch address 0x{address:08x} is outside PAL text and the reserved low-memory hook area"),
        }
    }
}
impl std::error::Error for DolphinPatchError {}

pub fn parse_onframe_patch(text: &str) -> Result<Vec<PatchWrite>, DolphinPatchError> {
    let mut writes = BTreeMap::new();
    for (index, raw) in text.lines().enumerate() {
        let line = raw.trim();
        if line.is_empty()
            || line.starts_with('#')
            || line.starts_with('$')
            || line.starts_with('[')
        {
            continue;
        }
        let fields: Vec<_> = line.split(':').collect();
        if fields.len() != 3 || !fields[1].eq_ignore_ascii_case("dword") {
            return Err(DolphinPatchError::InvalidLine(index + 1));
        }
        let address = parse_hex(fields[0]).ok_or(DolphinPatchError::InvalidLine(index + 1))?;
        let value = parse_hex(fields[2]).ok_or(DolphinPatchError::InvalidLine(index + 1))?;
        if address & 3 != 0 {
            return Err(DolphinPatchError::InvalidLine(index + 1));
        }
        if writes.insert(address, value).is_some() {
            return Err(DolphinPatchError::DuplicateAddress(address));
        }
    }
    if writes.is_empty() {
        return Err(DolphinPatchError::InvalidLine(1));
    }
    Ok(writes
        .into_iter()
        .map(|(address, value)| PatchWrite { address, value })
        .collect())
}

fn parse_hex(text: &str) -> Option<u32> {
    u32::from_str_radix(
        text.trim()
            .strip_prefix("0x")
            .or_else(|| text.trim().strip_prefix("0X"))?,
        16,
    )
    .ok()
}

/// Merge the two upstream PAL shoulder swap fragments into AllStars writes,
/// replacing only the four values their fragments intentionally override.
pub fn combine_allstars_swaps(
    allstars: &str,
    swap_l_zl: &str,
    swap_r_zr: &str,
) -> Result<Vec<PatchWrite>, DolphinPatchError> {
    let mut base: BTreeMap<_, _> = parse_onframe_patch(allstars)?
        .into_iter()
        .map(|w| (w.address, w.value))
        .collect();
    let expected_base = [
        (0x8000_0b1c, 0x700c_0080),
        (0x8000_0b2c, 0x700c_2000),
        (0x8000_0aec, 0x700c_0004),
        (0x8000_0b0c, 0x700c_0200),
    ];
    if base.len() < 300 || expected_base.iter().any(|(a, v)| base.get(a) != Some(v)) {
        return Err(DolphinPatchError::InvalidAllStarsBase);
    }
    let swaps = [
        (
            "L/ZL",
            swap_l_zl,
            [(0x8000_0b2c, 0x700c_0080), (0x8000_0b1c, 0x700c_2000)],
        ),
        (
            "R/ZR",
            swap_r_zr,
            [(0x8000_0b0c, 0x700c_0004), (0x8000_0aec, 0x700c_0200)],
        ),
    ];
    for (name, text, expected) in swaps {
        let writes =
            parse_onframe_patch(text).map_err(|_| DolphinPatchError::InvalidSwapPatch(name))?;
        if writes.len() != 2
            || writes
                .iter()
                .any(|w| !expected.contains(&(w.address, w.value)))
            || expected
                .iter()
                .any(|e| !writes.iter().any(|w| (w.address, w.value) == *e))
        {
            return Err(DolphinPatchError::InvalidSwapPatch(name));
        }
        for write in writes {
            base.insert(write.address, write.value);
        }
    }
    Ok(base
        .into_iter()
        .map(|(address, value)| PatchWrite { address, value })
        .collect())
}

pub fn validate_pal_patch_writes(
    dol: &[u8],
    writes: &[PatchWrite],
) -> Result<usize, DolphinPatchError> {
    let info = parse_dol(dol).map_err(|e| DolphinPatchError::InvalidDol(e.to_string()))?;
    let mut text_writes = 0;
    for write in writes {
        if (0x8000_0000..0x8000_4000).contains(&write.address) {
            continue; // upstream's small trampoline code cave, below DOL text
        }
        let found = info.sections.iter().any(|section| {
            section.kind == SectionKind::Text
                && write.address >= section.address
                && u64::from(write.address) + 4
                    <= u64::from(section.address) + u64::from(section.size)
        });
        if !found {
            return Err(DolphinPatchError::UnsupportedWriteAddress(write.address));
        }
        text_writes += 1;
    }
    Ok(text_writes)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn put_be(bytes: &mut [u8], offset: usize, value: u32) {
        bytes[offset..offset + 4].copy_from_slice(&value.to_be_bytes());
    }

    fn fixture_dol() -> Vec<u8> {
        let mut dol = vec![0; 0x108];
        put_be(&mut dol, 0, 0x100);
        put_be(&mut dol, 0x48, 0x8000_4000);
        put_be(&mut dol, 0x90, 8);
        put_be(&mut dol, 0x100, 0x6000_0000);
        put_be(&mut dol, 0x104, 0x4e80_0020);
        dol
    }

    #[test]
    fn parses_writes_and_rejects_duplicate_or_unaligned_addresses() {
        assert_eq!(
            parse_onframe_patch("0x80004000:dword:0x60000000\n").unwrap(),
            vec![PatchWrite {
                address: 0x8000_4000,
                value: 0x6000_0000
            }]
        );
        assert_eq!(
            parse_onframe_patch("0x80004000:dword:0x0\n0x80004000:dword:0x0").unwrap_err(),
            DolphinPatchError::DuplicateAddress(0x8000_4000)
        );
        assert!(parse_onframe_patch("0x80004001:dword:0x0").is_err());
    }

    #[test]
    fn combines_only_expected_allstars_swap_fragments() {
        let mut base = "0x80000B1C:dword:0x700C0080\n0x80000B2C:dword:0x700C2000\n0x80000AEC:dword:0x700C0004\n0x80000B0C:dword:0x700C0200\n".to_owned();
        for index in 0..296u32 {
            base.push_str(&format!(
                "0x{:08X}:dword:0x60000000\n",
                0x8000_4000 + index * 4
            ));
        }
        let lz = "0x80000B2C:dword:0x700C0080\n0x80000B1C:dword:0x700C2000\n";
        let rz = "0x80000B0C:dword:0x700C0004\n0x80000AEC:dword:0x700C0200\n";
        let merged = combine_allstars_swaps(&base, lz, rz).unwrap();
        let values: BTreeMap<_, _> = merged.into_iter().map(|w| (w.address, w.value)).collect();
        assert_eq!(values[&0x8000_0b2c], 0x700c_0080);
        assert_eq!(values[&0x8000_0b1c], 0x700c_2000);
        assert_eq!(values[&0x8000_0b0c], 0x700c_0004);
        assert_eq!(values[&0x8000_0aec], 0x700c_0200);
        assert!(combine_allstars_swaps(&base, "0x80000B2C:dword:0", rz).is_err());
    }

    #[test]
    fn validates_only_text_or_reserved_low_code_cave_addresses() {
        let dol = fixture_dol();
        let writes = [
            PatchWrite {
                address: 0x8000_4000,
                value: 0x6000_0000,
            },
            PatchWrite {
                address: 0x8000_09b0,
                value: 0x9421_fff0,
            },
        ];
        assert_eq!(validate_pal_patch_writes(&dol, &writes), Ok(1));
        assert_eq!(
            validate_pal_patch_writes(
                &dol,
                &[PatchWrite {
                    address: 0x8000_5000,
                    value: 0
                }]
            ),
            Err(DolphinPatchError::UnsupportedWriteAddress(0x8000_5000))
        );
    }
}
