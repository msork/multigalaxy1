//! PAL disc/revision recognition and fail-closed per-build hook maps.
use crate::{parse_dol, sha256, SectionKind};
use std::fmt;

const DISC_HEADER_SIZE: usize = 0x80;
const PAL_REGION_MARKER: u8 = b'P';

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct DiscIdentity {
    pub game_id: String,
    pub revision: u16,
}

impl DiscIdentity {
    /// Read the standard 0x80-byte Wii disc header. This deliberately accepts PAL only.
    pub fn from_header(header: &[u8]) -> Result<Self, RevisionError> {
        if header.len() < DISC_HEADER_SIZE {
            return Err(RevisionError::TruncatedDiscHeader(header.len()));
        }
        let raw_id = &header[..6];
        if !raw_id.iter().all(u8::is_ascii_alphanumeric) {
            return Err(RevisionError::InvalidGameId);
        }
        let game_id =
            String::from_utf8(raw_id.to_vec()).map_err(|_| RevisionError::InvalidGameId)?;
        if raw_id[3] != PAL_REGION_MARKER {
            return Err(RevisionError::UnsupportedRegion(game_id));
        }
        Ok(Self {
            game_id,
            revision: u16::from(header[7]),
        })
    }
}

fn is_pal_game_id(game_id: &str) -> bool {
    let bytes = game_id.as_bytes();
    bytes.len() == 6 && bytes[3] == PAL_REGION_MARKER && bytes.iter().all(u8::is_ascii_alphanumeric)
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BuildFingerprint {
    pub disc: DiscIdentity,
    pub dol_sha256: [u8; 32],
}

impl BuildFingerprint {
    pub fn for_dol(disc: DiscIdentity, dol: &[u8]) -> Self {
        Self {
            disc,
            dol_sha256: sha256(dol),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum HookSiteStatus {
    Unverified,
    StaticVerified,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HookSite {
    pub name: String,
    /// PowerPC guest virtual address, tied to one exact build fingerprint.
    pub address: u32,
    /// Original instruction bytes expected at this address (big-endian).
    pub expected_bytes: Vec<u8>,
    pub status: HookSiteStatus,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct VersionMap {
    pub fingerprint: BuildFingerprint,
    pub provenance: String,
    pub hooks: Vec<HookSite>,
}

#[derive(Debug, Clone, Default)]
pub struct RevisionCatalog {
    maps: Vec<VersionMap>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RevisionError {
    TruncatedDiscHeader(usize),
    InvalidGameId,
    UnsupportedRegion(String),
    UnsupportedRevision { game_id: String, revision: u16 },
    UnknownExecutableHash { game_id: String, revision: u16 },
    DuplicateFingerprint,
    EmptyProvenance,
    WrongBuildFingerprint,
    InvalidHookDefinition(String),
    UnverifiedHook(String),
    HookOutsideText { name: String, address: u32 },
    HookSignatureMismatch { name: String, address: u32 },
    OverlappingHooks(String, String),
    InvalidDol(String),
}

impl fmt::Display for RevisionError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::TruncatedDiscHeader(n) => write!(f, "disc header is truncated: need 0x80 bytes, got 0x{n:x}"),
            Self::InvalidGameId => write!(f, "disc header has an invalid game ID"),
            Self::UnsupportedRegion(id) => write!(f, "{id} is not a supported PAL game ID"),
            Self::UnsupportedRevision { game_id, revision } => write!(f, "no verified map for {game_id} revision {revision}"),
            Self::UnknownExecutableHash { game_id, revision } => write!(f, "{game_id} revision {revision} is known, but this main.dol hash has no verified map"),
            Self::DuplicateFingerprint => write!(f, "duplicate build fingerprint in revision catalog"),
            Self::EmptyProvenance => write!(f, "version map must record its evidence provenance"),
            Self::WrongBuildFingerprint => write!(f, "DOL does not match the exact build fingerprint for this map"),
            Self::InvalidHookDefinition(name) => write!(f, "invalid hook definition: {name}"),
            Self::UnverifiedHook(name) => write!(f, "refusing unverified hook site: {name}"),
            Self::HookOutsideText { name, address } => write!(f, "hook {name} at 0x{address:08x} is outside a DOL text section"),
            Self::HookSignatureMismatch { name, address } => write!(f, "hook {name} original bytes do not match at 0x{address:08x}"),
            Self::OverlappingHooks(a, b) => write!(f, "hook ranges overlap: {a} and {b}"),
            Self::InvalidDol(message) => write!(f, "invalid DOL: {message}"),
        }
    }
}
impl std::error::Error for RevisionError {}

impl RevisionCatalog {
    pub fn new() -> Self {
        Self::default()
    }

    /// Add a reviewed PAL build mapping. Unverified hook sites may be catalogued for research,
    /// but `validate_map` will not accept them for patch use.
    pub fn register(&mut self, map: VersionMap) -> Result<(), RevisionError> {
        if !is_pal_game_id(&map.fingerprint.disc.game_id) {
            return Err(RevisionError::UnsupportedRegion(
                map.fingerprint.disc.game_id,
            ));
        }
        if map.provenance.trim().is_empty() {
            return Err(RevisionError::EmptyProvenance);
        }
        if self
            .maps
            .iter()
            .any(|existing| existing.fingerprint == map.fingerprint)
        {
            return Err(RevisionError::DuplicateFingerprint);
        }
        self.maps.push(map);
        Ok(())
    }

    /// Match all three identity inputs: PAL game ID, disc revision, and exact main.dol SHA-256.
    pub fn identify(&self, disc: &DiscIdentity, dol: &[u8]) -> Result<&VersionMap, RevisionError> {
        if !is_pal_game_id(&disc.game_id) {
            return Err(RevisionError::UnsupportedRegion(disc.game_id.clone()));
        }
        let digest = sha256(dol);
        if let Some(found) = self
            .maps
            .iter()
            .find(|map| map.fingerprint.disc == *disc && map.fingerprint.dol_sha256 == digest)
        {
            return Ok(found);
        }
        if self.maps.iter().any(|map| map.fingerprint.disc == *disc) {
            return Err(RevisionError::UnknownExecutableHash {
                game_id: disc.game_id.clone(),
                revision: disc.revision,
            });
        }
        Err(RevisionError::UnsupportedRevision {
            game_id: disc.game_id.clone(),
            revision: disc.revision,
        })
    }

    /// Verify exact build identity, text-section containment, original bytes, and non-overlap.
    /// This validates hook sites; it does not claim runtime or gameplay compatibility.
    pub fn validate_map(
        &self,
        map: &VersionMap,
        disc: &DiscIdentity,
        dol: &[u8],
    ) -> Result<(), RevisionError> {
        if map.fingerprint.disc != *disc || sha256(dol) != map.fingerprint.dol_sha256 {
            return Err(RevisionError::WrongBuildFingerprint);
        }
        let info = parse_dol(dol).map_err(|e| RevisionError::InvalidDol(e.to_string()))?;
        let mut ranges: Vec<(u64, u64, &str)> = Vec::new();
        for hook in &map.hooks {
            if hook.status != HookSiteStatus::StaticVerified {
                return Err(RevisionError::UnverifiedHook(hook.name.clone()));
            }
            if hook.expected_bytes.is_empty()
                || hook.expected_bytes.len() % 4 != 0
                || hook.address % 4 != 0
            {
                return Err(RevisionError::InvalidHookDefinition(hook.name.clone()));
            }
            let start = u64::from(hook.address);
            let end = start + hook.expected_bytes.len() as u64;
            let section = info
                .sections
                .iter()
                .find(|section| {
                    section.kind == SectionKind::Text
                        && start >= u64::from(section.address)
                        && end <= u64::from(section.address) + u64::from(section.size)
                })
                .ok_or_else(|| RevisionError::HookOutsideText {
                    name: hook.name.clone(),
                    address: hook.address,
                })?;
            let file_offset =
                section.file_offset as usize + (hook.address - section.address) as usize;
            if dol.get(file_offset..file_offset + hook.expected_bytes.len())
                != Some(hook.expected_bytes.as_slice())
            {
                return Err(RevisionError::HookSignatureMismatch {
                    name: hook.name.clone(),
                    address: hook.address,
                });
            }
            ranges.push((start, end, &hook.name));
        }
        ranges.sort_by_key(|range| range.0);
        for pair in ranges.windows(2) {
            if pair[0].1 > pair[1].0 {
                return Err(RevisionError::OverlappingHooks(
                    pair[0].2.into(),
                    pair[1].2.into(),
                ));
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture() -> Vec<u8> {
        let mut dol = vec![0u8; 0x110];
        dol[0..4].copy_from_slice(&0x100u32.to_be_bytes());
        dol[0x48..0x4c].copy_from_slice(&0x80004000u32.to_be_bytes());
        dol[0x90..0x94].copy_from_slice(&0x10u32.to_be_bytes());
        dol[0x100..0x110].copy_from_slice(&[0x60, 0, 0, 0, 0x48, 0, 0, 1, 8, 0, 0, 0, 0, 0, 0, 0]);
        dol
    }
    fn disc_header(id: &[u8; 6], revision: u8) -> Vec<u8> {
        let mut header = vec![0u8; DISC_HEADER_SIZE];
        header[..6].copy_from_slice(id);
        header[7] = revision;
        header
    }
    fn version_map(dol: &[u8], hooks: Vec<HookSite>) -> VersionMap {
        VersionMap {
            fingerprint: BuildFingerprint::for_dol(
                DiscIdentity {
                    game_id: "RMGP01".into(),
                    revision: 0,
                },
                dol,
            ),
            provenance: "synthetic test fixture".into(),
            hooks,
        }
    }
    fn hook(name: &str, address: u32, bytes: &[u8], status: HookSiteStatus) -> HookSite {
        HookSite {
            name: name.into(),
            address,
            expected_bytes: bytes.into(),
            status,
        }
    }

    #[test]
    fn parses_pal_id_and_revision_and_rejects_other_regions() {
        let parsed = DiscIdentity::from_header(&disc_header(b"RMGP01", 2)).unwrap();
        assert_eq!(
            parsed,
            DiscIdentity {
                game_id: "RMGP01".into(),
                revision: 2
            }
        );
        assert!(matches!(
            DiscIdentity::from_header(&disc_header(b"RMGE01", 0)),
            Err(RevisionError::UnsupportedRegion(_))
        ));
        assert!(matches!(
            DiscIdentity::from_header(&[0; 7]),
            Err(RevisionError::TruncatedDiscHeader(_))
        ));
    }

    #[test]
    fn identifies_only_exact_pal_identity_and_dol_hash() {
        let dol = fixture();
        let disc = DiscIdentity {
            game_id: "RMGP01".into(),
            revision: 0,
        };
        let map = version_map(&dol, vec![]);
        let mut catalog = RevisionCatalog::new();
        catalog.register(map).unwrap();
        assert!(catalog.identify(&disc, &dol).is_ok());
        assert!(matches!(
            catalog.identify(&disc, b"different"),
            Err(RevisionError::UnknownExecutableHash { .. })
        ));
        assert!(matches!(
            catalog.identify(
                &DiscIdentity {
                    game_id: "RMGP01".into(),
                    revision: 1
                },
                &dol
            ),
            Err(RevisionError::UnsupportedRevision { .. })
        ));
    }

    #[test]
    fn validates_signature_and_rejects_unverified_or_overlapping_hooks() {
        let dol = fixture();
        let catalog = RevisionCatalog::new();
        let valid = version_map(
            &dol,
            vec![hook(
                "entry",
                0x80004000,
                &[0x60, 0, 0, 0],
                HookSiteStatus::StaticVerified,
            )],
        );
        let disc = DiscIdentity {
            game_id: "RMGP01".into(),
            revision: 0,
        };
        catalog.validate_map(&valid, &disc, &dol).unwrap();

        let unverified = version_map(
            &dol,
            vec![hook(
                "unknown",
                0x80004000,
                &[0x60, 0, 0, 0],
                HookSiteStatus::Unverified,
            )],
        );
        assert!(matches!(
            catalog.validate_map(&unverified, &disc, &dol),
            Err(RevisionError::UnverifiedHook(_))
        ));

        let mismatch = version_map(
            &dol,
            vec![hook(
                "changed",
                0x80004000,
                &[0x48, 0, 0, 1],
                HookSiteStatus::StaticVerified,
            )],
        );
        assert!(matches!(
            catalog.validate_map(&mismatch, &disc, &dol),
            Err(RevisionError::HookSignatureMismatch { .. })
        ));

        let overlap = version_map(
            &dol,
            vec![
                hook(
                    "first",
                    0x80004000,
                    &[0x60, 0, 0, 0, 0x48, 0, 0, 1],
                    HookSiteStatus::StaticVerified,
                ),
                hook(
                    "second",
                    0x80004004,
                    &[0x48, 0, 0, 1],
                    HookSiteStatus::StaticVerified,
                ),
            ],
        );
        assert!(matches!(
            catalog.validate_map(&overlap, &disc, &dol),
            Err(RevisionError::OverlappingHooks(_, _))
        ));
    }
}
