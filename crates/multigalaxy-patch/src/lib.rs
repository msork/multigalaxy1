//! Safe, read-only DOL inspection and comparison primitives.
mod dolphin_patch;
mod revision;
mod shared_render;
pub mod vcdiff;

use std::fmt;

pub use dolphin_patch::{
    combine_allstars_swaps, parse_onframe_patch, validate_pal_patch_writes, DolphinPatchError,
    PatchWrite,
};
pub use revision::{
    BuildFingerprint, DiscIdentity, HookSite, HookSiteStatus, RevisionCatalog, RevisionError,
    VersionMap,
};
pub use shared_render::{
    audit_pal_shared_model_calls, AuditedRenderCall, RenderAuditError, RenderCallSite,
    PAL_SHARED_MODEL_CALLS,
};

pub const DOL_HEADER_SIZE: usize = 0x100;
const TEXT_COUNT: usize = 7;
const DATA_COUNT: usize = 11;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Section {
    pub kind: SectionKind,
    pub index: usize,
    pub file_offset: u32,
    pub address: u32,
    pub size: u32,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SectionKind {
    Text,
    Data,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DolInfo {
    pub entry_point: u32,
    pub bss_address: u32,
    pub bss_size: u32,
    pub sections: Vec<Section>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum DolError {
    TruncatedHeader(usize),
    SectionOutOfBounds {
        kind: SectionKind,
        index: usize,
        offset: u32,
        size: u32,
        file_size: usize,
    },
}

impl fmt::Display for DolError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::TruncatedHeader(n) => write!(f, "DOL header is truncated: need 0x100 bytes, got 0x{n:x}"),
            Self::SectionOutOfBounds { kind, index, offset, size, file_size } => write!(f, "{kind:?}[{index}] file range 0x{offset:x}..0x{:x} exceeds file size 0x{file_size:x}", offset.saturating_add(*size)),
        }
    }
}
impl std::error::Error for DolError {}

fn be32(data: &[u8], at: usize) -> u32 {
    u32::from_be_bytes(data[at..at + 4].try_into().expect("validated DOL header"))
}

pub fn parse_dol(data: &[u8]) -> Result<DolInfo, DolError> {
    if data.len() < DOL_HEADER_SIZE {
        return Err(DolError::TruncatedHeader(data.len()));
    }
    let mut sections = Vec::new();
    for (kind, count, offset_base, address_base, size_base) in [
        (SectionKind::Text, TEXT_COUNT, 0x00, 0x48, 0x90),
        (SectionKind::Data, DATA_COUNT, 0x1c, 0x64, 0xac),
    ] {
        for index in 0..count {
            let file_offset = be32(data, offset_base + index * 4);
            let address = be32(data, address_base + index * 4);
            let size = be32(data, size_base + index * 4);
            if size == 0 {
                continue;
            }
            let end = u64::from(file_offset) + u64::from(size);
            if file_offset < DOL_HEADER_SIZE as u32 || end > data.len() as u64 {
                return Err(DolError::SectionOutOfBounds {
                    kind,
                    index,
                    offset: file_offset,
                    size,
                    file_size: data.len(),
                });
            }
            sections.push(Section {
                kind,
                index,
                file_offset,
                address,
                size,
            });
        }
    }
    Ok(DolInfo {
        entry_point: be32(data, 0xe0),
        bss_address: be32(data, 0xd8),
        bss_size: be32(data, 0xdc),
        sections,
    })
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ChangedRange {
    pub offset: usize,
    pub size: usize,
}

/// Decodes a PowerPC branch destination for primary opcodes 16 and 18.
pub fn branch_target(instruction: u32, pc: u32) -> Option<u32> {
    let opcode = instruction >> 26;
    let (disp, mask, sign) = match opcode {
        16 => (
            (instruction & 0x0000_fffc) as i32,
            0x0000_8000u32,
            0x0001_0000i32,
        ),
        18 => (
            (instruction & 0x03ff_fffc) as i32,
            0x0200_0000u32,
            0x0400_0000i32,
        ),
        _ => return None,
    };
    let signed_disp = if (disp as u32) & mask != 0 {
        disp - sign
    } else {
        disp
    };
    let base = if instruction & 2 != 0 { 0 } else { pc as i64 };
    Some(base.wrapping_add(i64::from(signed_disp)) as u32)
}

/// Returns changed runs in the shared file region, followed by an appended/truncated tail.
pub fn changed_ranges(left: &[u8], right: &[u8]) -> Vec<ChangedRange> {
    let limit = left.len().min(right.len());
    let mut ranges = Vec::new();
    let mut pos = 0;
    while pos < limit {
        if left[pos] == right[pos] {
            pos += 1;
            continue;
        }
        let start = pos;
        while pos < limit && left[pos] != right[pos] {
            pos += 1;
        }
        ranges.push(ChangedRange {
            offset: start,
            size: pos - start,
        });
    }
    if left.len() != right.len() {
        ranges.push(ChangedRange {
            offset: limit,
            size: left.len().abs_diff(right.len()),
        });
    }
    ranges
}

/// SHA-256 digest, implemented here to keep the patch-inspection CLI dependency-free.
pub fn sha256(input: &[u8]) -> [u8; 32] {
    const K: [u32; 64] = [
        0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4,
        0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe,
        0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f,
        0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7,
        0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc,
        0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
        0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070, 0x19a4c116,
        0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
        0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7,
        0xc67178f2,
    ];
    let mut h = [
        0x6a09e667u32,
        0xbb67ae85,
        0x3c6ef372,
        0xa54ff53a,
        0x510e527f,
        0x9b05688c,
        0x1f83d9ab,
        0x5be0cd19,
    ];
    let bit_len = (input.len() as u64).wrapping_mul(8);
    let mut padded = input.to_vec();
    padded.push(0x80);
    while padded.len() % 64 != 56 {
        padded.push(0);
    }
    padded.extend_from_slice(&bit_len.to_be_bytes());
    for chunk in padded.chunks_exact(64) {
        let mut w = [0u32; 64];
        for (i, word) in w[..16].iter_mut().enumerate() {
            *word = u32::from_be_bytes(chunk[i * 4..i * 4 + 4].try_into().unwrap());
        }
        for i in 16..64 {
            let s0 = w[i - 15].rotate_right(7) ^ w[i - 15].rotate_right(18) ^ (w[i - 15] >> 3);
            let s1 = w[i - 2].rotate_right(17) ^ w[i - 2].rotate_right(19) ^ (w[i - 2] >> 10);
            w[i] = w[i - 16]
                .wrapping_add(s0)
                .wrapping_add(w[i - 7])
                .wrapping_add(s1);
        }
        let [mut a, mut b, mut c, mut d, mut e, mut f, mut g, mut hh] = h;
        for i in 0..64 {
            let s1 = e.rotate_right(6) ^ e.rotate_right(11) ^ e.rotate_right(25);
            let ch = (e & f) ^ (!e & g);
            let t1 = hh
                .wrapping_add(s1)
                .wrapping_add(ch)
                .wrapping_add(K[i])
                .wrapping_add(w[i]);
            let s0 = a.rotate_right(2) ^ a.rotate_right(13) ^ a.rotate_right(22);
            let maj = (a & b) ^ (a & c) ^ (b & c);
            let t2 = s0.wrapping_add(maj);
            hh = g;
            g = f;
            f = e;
            e = d.wrapping_add(t1);
            d = c;
            c = b;
            b = a;
            a = t1.wrapping_add(t2);
        }
        for (v, add) in h.iter_mut().zip([a, b, c, d, e, f, g, hh]) {
            *v = v.wrapping_add(add);
        }
    }
    let mut digest = [0u8; 32];
    for (i, word) in h.iter().enumerate() {
        digest[i * 4..i * 4 + 4].copy_from_slice(&word.to_be_bytes());
    }
    digest
}

#[cfg(test)]
mod tests {
    use super::*;
    fn fixture() -> Vec<u8> {
        let mut d = vec![0; 0x110];
        d[0..4].copy_from_slice(&0x100u32.to_be_bytes());
        d[0x48..0x4c].copy_from_slice(&0x80004000u32.to_be_bytes());
        d[0x90..0x94].copy_from_slice(&0x10u32.to_be_bytes());
        d[0xd8..0xdc].copy_from_slice(&0x80500000u32.to_be_bytes());
        d[0xdc..0xe0].copy_from_slice(&0x2000u32.to_be_bytes());
        d[0xe0..0xe4].copy_from_slice(&0x80004000u32.to_be_bytes());
        for (i, b) in d[0x100..0x110].iter_mut().enumerate() {
            *b = i as u8;
        }
        d
    }
    #[test]
    fn parses_header_sections_and_entry() {
        let info = parse_dol(&fixture()).unwrap();
        assert_eq!(info.entry_point, 0x80004000);
        assert_eq!(info.bss_address, 0x80500000);
        assert_eq!(info.bss_size, 0x2000);
        assert_eq!(
            info.sections,
            vec![Section {
                kind: SectionKind::Text,
                index: 0,
                file_offset: 0x100,
                address: 0x80004000,
                size: 0x10
            }]
        );
    }
    #[test]
    fn rejects_short_header_and_bad_section_range() {
        assert_eq!(parse_dol(b"short"), Err(DolError::TruncatedHeader(5)));
        let mut d = fixture();
        d[0x90..0x94].copy_from_slice(&0x1000u32.to_be_bytes());
        assert!(matches!(
            parse_dol(&d),
            Err(DolError::SectionOutOfBounds { .. })
        ));
    }
    #[test]
    fn detects_changed_runs_and_file_tail() {
        let a = fixture();
        let mut b = a.clone();
        b[0x104] ^= 0xff;
        b.extend_from_slice(b"extra");
        assert_eq!(
            changed_ranges(&a, &b),
            vec![
                ChangedRange {
                    offset: 0x104,
                    size: 1
                },
                ChangedRange {
                    offset: a.len(),
                    size: 5
                }
            ]
        );
    }
    #[test]
    fn sha256_matches_standard_vectors() {
        let hex = |bytes: &[u8]| bytes.iter().map(|b| format!("{b:02x}")).collect::<String>();
        assert_eq!(
            hex(&sha256(b"")),
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        );
        assert_eq!(
            hex(&sha256(b"abc")),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
    }
    #[test]
    fn decodes_relative_and_absolute_branches() {
        assert_eq!(branch_target(0x4800_0011, 0x8000_1000), Some(0x8000_1010));
        assert_eq!(branch_target(0x4bff_fff1, 0x8000_1000), Some(0x8000_0ff0));
        assert_eq!(branch_target(0x4800_0013, 0x8000_1000), Some(0x10));
        assert_eq!(branch_target(0x6000_0000, 0x8000_1000), None);
    }
}
