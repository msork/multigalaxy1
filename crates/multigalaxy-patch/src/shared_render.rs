//! Read-only audit of PAL Mario shared-model hook candidates.
//!
//! This does not generate a patch. Matrix-buffer ownership and hook ABI work
//! remain gates before any executable hook can be emitted.
use crate::{branch_target, parse_dol, SectionKind};
use std::{error::Error, fmt};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct RenderCallSite {
    pub name: &'static str,
    pub address: u32,
    pub expected_instruction: u32,
    pub expected_target: u32,
    /// Optional preceding words that establish the call arguments.
    pub context_words_before: &'static [u32],
    /// Following words that demonstrate how the caller consumes the result.
    pub context_words_after: &'static [u32],
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct AuditedRenderCall {
    pub name: &'static str,
    pub address: u32,
    pub instruction: u32,
    pub target: u32,
}

const DRAW_CONTEXT: &[u32] = &[0x7fa3_eb78, 0x3880_0000]; // mr r3,r29; li r4,0
const MODEL_CONTEXT_BEFORE: &[u32] = &[0x7fc3_f378]; // mr r3,r30
const MODEL_CONTEXT_AFTER: &[u32] = &[0x3800_0008, 0x7c7b_1b78, 0x9803_00dd]; // li r0,8; mr r27,r3; stb r0,0xdd(r3)
const ANIM_INIT_CONTEXT_BEFORE: &[u32] = &[0x7c7f_1b78, 0x9083_0004, 0x90a3_0000]; // save this; initialize constructor fields
const ANIM_INIT_CONTEXT_AFTER: &[u32] = &[0x7fe3_fb78]; // mr r3,r31 restores this
const CALC_CONTEXT_BEFORE: &[u32] = &[0x807e_0234]; // lwz r3,0x234(r30)
const DRAW_CONTEXT_AFTER: &[u32] = &[0x3800_0000, 0x901d_01b0]; // li r0,0; stw r0,0x1b0(r29)

/// Candidate call boundaries derived from the reviewed upstream architecture,
/// independently checked against PAL RMGP01 Rev 0 DOL instructions.
/// Candidates are not authorization to patch these locations.
pub const PAL_SHARED_MODEL_CALLS: &[RenderCallSite] = &[
    RenderCallSite {
        name: "Mario model acquisition after initDrawAndModel",
        address: 0x802b_543c,
        expected_instruction: 0x4813_2bf1,
        expected_target: 0x803e_802c, // MR::getJ3DModel(LiveActor const*)
        context_words_before: MODEL_CONTEXT_BEFORE,
        context_words_after: MODEL_CONTEXT_AFTER,
    },
    RenderCallSite {
        name: "MarioAnimator initialization",
        address: 0x802c_a78c,
        expected_instruction: 0x4800_001d,
        expected_target: 0x802c_a7a8, // MarioAnimator::init()
        context_words_before: ANIM_INIT_CONTEXT_BEFORE,
        context_words_after: ANIM_INIT_CONTEXT_AFTER,
    },
    RenderCallSite {
        name: "Mario animation calculation",
        address: 0x802b_2ce8,
        expected_instruction: 0x4801_9649,
        expected_target: 0x802c_c330, // MarioAnimator::calc()
        context_words_before: CALC_CONTEXT_BEFORE,
        context_words_after: &[],
    },
    RenderCallSite {
        name: "Mario model draw",
        address: 0x802b_62e4,
        expected_instruction: 0x4bff_083d,
        expected_target: 0x802a_6b20, // J3DModelX::directDraw(J3DModel*)
        context_words_before: DRAW_CONTEXT,
        context_words_after: DRAW_CONTEXT_AFTER,
    },
];

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RenderAuditError {
    InvalidDol(String),
    SiteOutsideText {
        name: &'static str,
        address: u32,
    },
    ContextOutsideText {
        name: &'static str,
        address: u32,
    },
    ContextMismatch {
        name: &'static str,
        address: u32,
        expected: u32,
        found: u32,
    },
    AfterContextOutsideText {
        name: &'static str,
        address: u32,
    },
    AfterContextMismatch {
        name: &'static str,
        address: u32,
        expected: u32,
        found: u32,
    },
    InstructionMismatch {
        name: &'static str,
        address: u32,
        expected: u32,
        found: u32,
    },
    NotRelativeLinkedBranch {
        name: &'static str,
        address: u32,
    },
    BranchTargetMismatch {
        name: &'static str,
        address: u32,
        expected: u32,
        found: u32,
    },
}

impl fmt::Display for RenderAuditError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidDol(message) => write!(f, "invalid DOL: {message}"),
            Self::SiteOutsideText { name, address } => {
                write!(f, "render call {name} at 0x{address:08x} is outside DOL text")
            }
            Self::ContextOutsideText { name, address } => {
                write!(f, "render call context for {name} at 0x{address:08x} is outside DOL text")
            }
            Self::ContextMismatch { name, address, expected, found } => write!(
                f,
                "render call context mismatch for {name} at 0x{address:08x}: expected 0x{expected:08x}, found 0x{found:08x}"
            ),
            Self::AfterContextOutsideText { name, address } => write!(f, "render call return context for {name} at 0x{address:08x} is outside DOL text"),
            Self::AfterContextMismatch { name, address, expected, found } => write!(f, "render call return context mismatch for {name} at 0x{address:08x}: expected 0x{expected:08x}, found 0x{found:08x}"),
            Self::InstructionMismatch { name, address, expected, found } => write!(
                f,
                "render call instruction mismatch for {name} at 0x{address:08x}: expected 0x{expected:08x}, found 0x{found:08x}"
            ),
            Self::NotRelativeLinkedBranch { name, address } => write!(
                f,
                "render call {name} at 0x{address:08x} is not a relative PowerPC branch-with-link"
            ),
            Self::BranchTargetMismatch { name, address, expected, found } => write!(
                f,
                "render call target mismatch for {name} at 0x{address:08x}: expected 0x{expected:08x}, found 0x{found:08x}"
            ),
        }
    }
}
impl Error for RenderAuditError {}

fn read_text_word(dol: &[u8], address: u32) -> Option<u32> {
    let info = parse_dol(dol).ok()?;
    let section = info.sections.iter().find(|section| {
        section.kind == SectionKind::Text
            && address >= section.address
            && u64::from(address) + 4 <= u64::from(section.address) + u64::from(section.size)
    })?;
    let offset = section.file_offset as usize + (address - section.address) as usize;
    Some(u32::from_be_bytes(
        dol.get(offset..offset + 4)?.try_into().ok()?,
    ))
}

/// Validate PAL candidate call words and their branch targets without writing
/// or emitting a Gecko code.
pub fn audit_pal_shared_model_calls(
    dol: &[u8],
) -> Result<Vec<AuditedRenderCall>, RenderAuditError> {
    parse_dol(dol).map_err(|error| RenderAuditError::InvalidDol(error.to_string()))?;
    let mut audited = Vec::with_capacity(PAL_SHARED_MODEL_CALLS.len());
    for site in PAL_SHARED_MODEL_CALLS {
        for (index, expected) in site.context_words_before.iter().enumerate() {
            let address =
                site.address - 4 * (site.context_words_before.len() as u32 - index as u32);
            let found =
                read_text_word(dol, address).ok_or(RenderAuditError::ContextOutsideText {
                    name: site.name,
                    address,
                })?;
            if found != *expected {
                return Err(RenderAuditError::ContextMismatch {
                    name: site.name,
                    address,
                    expected: *expected,
                    found,
                });
            }
        }

        let instruction =
            read_text_word(dol, site.address).ok_or(RenderAuditError::SiteOutsideText {
                name: site.name,
                address: site.address,
            })?;
        if instruction != site.expected_instruction {
            return Err(RenderAuditError::InstructionMismatch {
                name: site.name,
                address: site.address,
                expected: site.expected_instruction,
                found: instruction,
            });
        }
        if instruction >> 26 != 18 || instruction & 3 != 1 {
            return Err(RenderAuditError::NotRelativeLinkedBranch {
                name: site.name,
                address: site.address,
            });
        }
        let target = branch_target(instruction, site.address).ok_or(
            RenderAuditError::NotRelativeLinkedBranch {
                name: site.name,
                address: site.address,
            },
        )?;
        if target != site.expected_target {
            return Err(RenderAuditError::BranchTargetMismatch {
                name: site.name,
                address: site.address,
                expected: site.expected_target,
                found: target,
            });
        }
        for (index, expected) in site.context_words_after.iter().enumerate() {
            let address = site.address + 4 * (index as u32 + 1);
            let found =
                read_text_word(dol, address).ok_or(RenderAuditError::AfterContextOutsideText {
                    name: site.name,
                    address,
                })?;
            if found != *expected {
                return Err(RenderAuditError::AfterContextMismatch {
                    name: site.name,
                    address,
                    expected: *expected,
                    found,
                });
            }
        }
        audited.push(AuditedRenderCall {
            name: site.name,
            address: site.address,
            instruction,
            target,
        });
    }
    Ok(audited)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn put_be(bytes: &mut [u8], offset: usize, value: u32) {
        bytes[offset..offset + 4].copy_from_slice(&value.to_be_bytes());
    }

    fn fixture() -> Vec<u8> {
        let mut dol = vec![0; 0x180];
        let sites = PAL_SHARED_MODEL_CALLS;
        let ranges = [
            (
                sites[0].address - 4,
                (sites[0].context_words_before.len() + 1 + sites[0].context_words_after.len()) * 4,
            ),
            (
                sites[1].address - sites[1].context_words_before.len() as u32 * 4,
                (sites[1].context_words_before.len() + 1 + sites[1].context_words_after.len()) * 4,
            ),
            (sites[2].address - 4, 8usize),
            (sites[3].address - 8, 20usize),
        ];
        for (index, (address, size)) in ranges.into_iter().enumerate() {
            let file_offset = 0x100 + index as u32 * 0x20;
            put_be(&mut dol, index * 4, file_offset);
            put_be(&mut dol, 0x48 + index * 4, address);
            put_be(&mut dol, 0x90 + index * 4, size as u32);
        }
        for (index, site) in sites.iter().enumerate() {
            let (section_address, section_size) = ranges[index];
            let file_offset = 0x100 + index * 0x20;
            for (ctx_index, word) in site.context_words_before.iter().enumerate() {
                let address =
                    site.address - 4 * (site.context_words_before.len() as u32 - ctx_index as u32);
                let offset = file_offset + (address - section_address) as usize;
                put_be(&mut dol, offset, *word);
            }
            let call_offset = file_offset + (site.address - section_address) as usize;
            put_be(&mut dol, call_offset, site.expected_instruction);
            for (ctx_index, word) in site.context_words_after.iter().enumerate() {
                let offset = call_offset + 4 * (ctx_index + 1);
                put_be(&mut dol, offset, *word);
            }
            debug_assert!(call_offset + 4 <= file_offset + section_size);
        }
        dol
    }

    #[test]
    fn accepts_only_all_four_expected_pal_call_boundaries_in_synthetic_dol() {
        let audited = audit_pal_shared_model_calls(&fixture()).unwrap();
        assert_eq!(audited.len(), 4);
        assert_eq!(audited[3].target, 0x802a_6b20);
    }

    #[test]
    fn rejects_changed_instruction_and_argument_context() {
        let mut dol = fixture();
        // Draw call is in text section four at file offset 0x168.
        put_be(&mut dol, 0x168, 0x4bff_0839);
        assert!(matches!(
            audit_pal_shared_model_calls(&dol),
            Err(RenderAuditError::InstructionMismatch { .. })
        ));

        let mut dol = fixture();
        put_be(&mut dol, 0x160, 0x7fa3_eb79);
        assert!(matches!(
            audit_pal_shared_model_calls(&dol),
            Err(RenderAuditError::ContextMismatch { .. })
        ));

        let mut dol = fixture();
        // The model-acquisition caller consumes r3 as a returned model pointer.
        put_be(&mut dol, 0x10c, 0x7c7b_1b79);
        assert!(matches!(
            audit_pal_shared_model_calls(&dol),
            Err(RenderAuditError::AfterContextMismatch { .. })
        ));

        // Every call boundary must retain its caller-established inputs.
        for (site_index, site) in PAL_SHARED_MODEL_CALLS.iter().enumerate() {
            if site.context_words_before.is_empty() {
                continue;
            }
            let mut dol = fixture();
            let first_context_file = 0x100 + site_index * 0x20;
            put_be(
                &mut dol,
                first_context_file,
                site.context_words_before[0] ^ 1,
            );
            assert!(matches!(
                audit_pal_shared_model_calls(&dol),
                Err(RenderAuditError::ContextMismatch { .. })
            ));
        }

        // Draw's caller observes model side effects immediately after return.
        let mut dol = fixture();
        put_be(&mut dol, 0x16c, 0x3800_0004);
        assert!(matches!(
            audit_pal_shared_model_calls(&dol),
            Err(RenderAuditError::AfterContextMismatch { .. })
        ));
    }

    #[test]
    fn rejects_non_link_branch_and_truncated_or_unmapped_sites() {
        let mut dol = fixture();
        put_be(&mut dol, 0x168, 0x4bff_083c);
        assert!(matches!(
            audit_pal_shared_model_calls(&dol),
            Err(RenderAuditError::InstructionMismatch { .. })
        ));

        assert!(matches!(
            audit_pal_shared_model_calls(&[0; 16]),
            Err(RenderAuditError::InvalidDol(_))
        ));

        let mut dol = fixture();
        put_be(&mut dol, 0x90 + 3 * 4, 4);
        assert!(matches!(
            audit_pal_shared_model_calls(&dol),
            Err(RenderAuditError::ContextOutsideText { .. })
                | Err(RenderAuditError::SiteOutsideText { .. })
        ));
    }
}
