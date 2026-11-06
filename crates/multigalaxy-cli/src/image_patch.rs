//! Stage 1 xdelta image pipeline: locate the PAL `main.dol` inside an
//! uncompressed Wii image by signature + full SHA-256, apply a size-preserving
//! in-place patch to a user-owned copy via a pure-Rust VCDIFF round-trip, and
//! record a manifest. The original image is never modified.
//!
//! Stage 1 proves the pipeline, not hook semantics: the patched window
//! reproduces the original bytes exactly (identity patch).

use multigalaxy_patch::{parse_dol, sha256, vcdiff, DiscIdentity, SectionKind};
use std::path::Path;

/// Stage 1 hook window: the 5 words at the `drawMarioModel` callsite,
/// `0x802B62D8..0x802B62EC` (`bl 0x802A6560; mr r3,r29; li r4,0;
/// bl 0x802A6B20; li r0,0`). Stage 1 replaces them with identical bytes.
pub const WINDOW_GUEST: u32 = 0x802B_62D8;
pub const WINDOW_WORDS: usize = 5;
pub const WINDOW_LEN: usize = WINDOW_WORDS * 4;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PatchManifest {
    pub schema_version: u32,
    pub patch_id: String,
    pub game_id: String,
    pub revision: u16,
    pub source_image_sha256: String,
    pub output_image_sha256: String,
    pub source_dol_sha256: String,
    pub target_dol_sha256: String,
    pub delta_sha256: String,
    pub window_guest: u32,
    pub window_len: usize,
}

impl PatchManifest {
    pub fn to_text(&self) -> String {
        format!(
            "schema_version={}\npatch_id={}\ngame_id={}\nrevision={}\nsource_image_sha256={}\noutput_image_sha256={}\nsource_dol_sha256={}\ntarget_dol_sha256={}\ndelta_sha256={}\nwindow_guest=0x{:08x}\nwindow_len={}\n",
            self.schema_version,
            self.patch_id,
            self.game_id,
            self.revision,
            self.source_image_sha256,
            self.output_image_sha256,
            self.source_dol_sha256,
            self.target_dol_sha256,
            self.delta_sha256,
            self.window_guest,
            self.window_len
        )
    }
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

/// Translate a guest address to a DOL file offset and read `WINDOW_WORDS` words.
pub fn window_words(dol: &[u8], guest: u32) -> Result<[u32; WINDOW_WORDS], String> {
    let info = parse_dol(dol).map_err(|e| format!("invalid PAL DOL: {e}"))?;
    let end = u64::from(guest) + WINDOW_LEN as u64;
    let section = info
        .sections
        .iter()
        .find(|s| {
            s.kind == SectionKind::Text
                && guest >= s.address
                && end <= u64::from(s.address) + u64::from(s.size)
        })
        .ok_or("hook window is outside a DOL text section")?;
    let offset =
        usize::try_from(u64::from(section.file_offset) + u64::from(guest - section.address))
            .map_err(|_| "window file offset overflow")?;
    let mut words = [0u32; WINDOW_WORDS];
    for (i, w) in words.iter_mut().enumerate() {
        let at = offset + i * 4;
        let bytes = dol
            .get(at..at + 4)
            .ok_or("PAL DOL truncated at hook window")?;
        *w = u32::from_be_bytes(bytes.try_into().expect("four bytes"));
    }
    Ok(words)
}

/// Locate exactly one copy of `dol` in an uncompressed Wii image by scanning
/// for the DOL header prefix and verifying the full SHA-256 of the candidate
/// region. Fail closed on zero or multiple matches.
pub fn locate_dol_in_image(image: &[u8], dol: &[u8]) -> Result<usize, String> {
    if image.len() < dol.len() + 0x80 {
        return Err("image too small to contain the disc header and DOL".into());
    }
    let prefix = &dol[..0x40];
    let want = sha256(dol);
    let mut found = None;
    let mut off = 0x400usize; // skip the disc header itself
    while off + dol.len() <= image.len() {
        if &image[off..off + 0x40] == prefix {
            if sha256(&image[off..off + dol.len()]) == want {
                if found.is_some() {
                    return Err("multiple DOL matches in image; refusing to patch".into());
                }
                found = Some(off);
            }
        }
        off += 4;
    }
    found.ok_or_else(|| "pinned PAL DOL not found in image (is it an uncompressed ISO?)".into())
}

/// Build the Stage 1 patched DOL (identity window) and its VCDIFF delta,
/// verifying the codec round-trip before anything is written.
pub fn stage1_patch(dol: &[u8]) -> Result<(Vec<u8>, Vec<u8>), String> {
    let words = window_words(dol, WINDOW_GUEST)?;
    // Stage 1 acceptance: the replacement reproduces the original window
    // exactly; the word list is checked so a future Stage 2 change must be
    // hand-verified against these.
    let expected = [
        0x4BFF0289u32,
        0x7FA3EB78,
        0x38800000,
        0x4BFF083D,
        0x38000000,
    ];
    if words != expected {
        return Err(format!(
            "hook window words changed in the PAL DOL: got {:08x?}, expected {:08x?}",
            words, expected
        ));
    }
    let patched = dol.to_vec(); // identity patch: pipeline proof only
    let delta = vcdiff::encode(dol, &patched);
    let decoded = vcdiff::decode(dol, &delta)?;
    if decoded != patched {
        return Err("VCDIFF round-trip mismatch".into());
    }
    Ok((patched, delta))
}

/// Apply the Stage 1 patch to a copy of `source` at `output`. Source is opened
/// read-only and never modified. Returns the manifest.
pub fn patch_image(source: &Path, output: &Path, dol: &[u8]) -> Result<PatchManifest, String> {
    let image = std::fs::read(source).map_err(|e| format!("cannot read source image: {e}"))?;
    let hdr_len = image.len().min(0x80);
    let disc =
        DiscIdentity::from_header(&image[..hdr_len]).map_err(|e| format!("disc header: {e}"))?;
    if disc.game_id != "RMGP01" || disc.revision != 0 {
        return Err(format!(
            "unsupported disc identity {}:{} (PAL RMGP01 Rev 0 required)",
            disc.game_id, disc.revision
        ));
    }
    let dol_off = locate_dol_in_image(&image, dol)?;
    let (patched_dol, delta) = stage1_patch(dol)?;
    if patched_dol.len() != dol.len() {
        return Err("Stage 1 patch must be size-preserving".into());
    }

    let mut out_image = image.clone();
    out_image[dol_off..dol_off + dol.len()].copy_from_slice(&patched_dol);
    if sha256(&out_image[dol_off..dol_off + dol.len()]) != sha256(&patched_dol) {
        return Err("output DOL region hash mismatch after splice".into());
    }

    if output.exists() {
        return Err("output image already exists; refusing to overwrite".into());
    }
    std::fs::write(output, &out_image).map_err(|e| format!("cannot write output image: {e}"))?;
    let manifest = PatchManifest {
        schema_version: 1,
        patch_id: "stage1-trampoline-identity".into(),
        game_id: disc.game_id,
        revision: disc.revision,
        source_image_sha256: hex(&sha256(&image)),
        output_image_sha256: hex(&sha256(&out_image)),
        source_dol_sha256: hex(&sha256(dol)),
        target_dol_sha256: hex(&sha256(&patched_dol)),
        delta_sha256: hex(&sha256(&delta)),
        window_guest: WINDOW_GUEST,
        window_len: WINDOW_LEN,
    };
    let manifest_path = output.with_extension("mg1patch.txt");
    std::fs::write(&manifest_path, manifest.to_text())
        .map_err(|e| format!("cannot write manifest: {e}"))?;
    Ok(manifest)
}

/// Validate that `image` is a Stage 1 patched copy with a matching manifest.
pub fn validate_patched_image(image_path: &Path, dol: &[u8]) -> Result<PatchManifest, String> {
    let image = std::fs::read(image_path).map_err(|e| format!("cannot read image: {e}"))?;
    let hdr_len = image.len().min(0x80);
    let disc =
        DiscIdentity::from_header(&image[..hdr_len]).map_err(|e| format!("disc header: {e}"))?;
    if disc.game_id != "RMGP01" || disc.revision != 0 {
        return Err("not a PAL RMGP01 Rev 0 image".into());
    }
    let dol_off = locate_dol_in_image(&image, dol)?;
    if sha256(&image[dol_off..dol_off + dol.len()]) != sha256(dol) {
        return Err("image DOL region does not match the pinned PAL DOL".into());
    }
    let manifest_path = image_path.with_extension("mg1patch.txt");
    let text = std::fs::read_to_string(&manifest_path).map_err(|e| {
        format!(
            "patched image is missing its manifest {}: {e}",
            manifest_path.display()
        )
    })?;
    let get = |key: &str| -> Result<String, String> {
        text.lines()
            .find_map(|l| l.strip_prefix(&format!("{key}=")))
            .map(|s| s.to_string())
            .ok_or_else(|| format!("manifest missing {key}"))
    };
    let manifest = PatchManifest {
        schema_version: get("schema_version")?
            .parse()
            .map_err(|_| "bad schema_version")?,
        patch_id: get("patch_id")?,
        game_id: get("game_id")?,
        revision: get("revision")?.parse().map_err(|_| "bad revision")?,
        source_image_sha256: get("source_image_sha256")?,
        output_image_sha256: get("output_image_sha256")?,
        source_dol_sha256: get("source_dol_sha256")?,
        target_dol_sha256: get("target_dol_sha256")?,
        delta_sha256: get("delta_sha256")?,
        window_guest: u32::from_str_radix(get("window_guest")?.trim_start_matches("0x"), 16)
            .map_err(|_| "bad window_guest")?,
        window_len: get("window_len")?.parse().map_err(|_| "bad window_len")?,
    };
    if manifest.output_image_sha256 != hex(&sha256(&image)) {
        return Err("manifest output hash does not match the image".into());
    }
    if manifest.source_dol_sha256 != hex(&sha256(dol)) {
        return Err("manifest source DOL hash does not match the pinned PAL DOL".into());
    }
    Ok(manifest)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn synthetic_image(dol: &[u8]) -> Vec<u8> {
        let mut image = vec![0u8; 0x400];
        image[..6].copy_from_slice(b"RMGP01");
        image[7] = 0; // revision 0
        image.extend_from_slice(&vec![0u8; 0x1000]);
        let off = image.len();
        image.extend_from_slice(dol);
        image.extend_from_slice(&vec![0u8; 0x100]);
        let _ = off;
        image
    }

    #[test]
    fn locates_exactly_one_dol_and_rejects_ambiguity() {
        let dol = vec![0xABu8; 0x200];
        let image = synthetic_image(&dol);
        assert_eq!(locate_dol_in_image(&image, &dol).unwrap(), 0x1400);
        let mut dup = image.clone();
        dup.extend_from_slice(&dol);
        assert!(locate_dol_in_image(&dup, &dol).is_err());
        let mut absent = image.clone();
        absent.truncate(0x1400 + 0x100);
        assert!(locate_dol_in_image(&absent, &dol).is_err());
    }

    /// Synthetic DOL with text[0] at guest 0x80004000, file offset 0x100,
    /// large enough to contain the pinned hook window at 0x802B62D8.
    fn synthetic_dol() -> Vec<u8> {
        let mut dol = vec![0u8; 0x100 + 0x2B3000];
        let base = 0x802B62D8u32 - 0x80004000;
        let words = [
            0x4BFF0289u32,
            0x7FA3EB78,
            0x38800000,
            0x4BFF083D,
            0x38000000,
        ];
        for (i, w) in words.iter().enumerate() {
            dol[0x100 + base as usize + i * 4..0x100 + base as usize + i * 4 + 4]
                .copy_from_slice(&w.to_be_bytes());
        }
        let text_size = (dol.len() - 0x100) as u32;
        let mut hdr = |at: usize, v: u32| dol[at..at + 4].copy_from_slice(&v.to_be_bytes());
        hdr(0x48, 0x80004000); // text addr[0]
        hdr(0x00, 0x100); // text offset[0]
        hdr(0x90, text_size); // text size[0]
        dol
    }

    #[test]
    fn stage1_patch_is_identity_and_round_trips() {
        let dol = synthetic_dol();
        let words = window_words(&dol, WINDOW_GUEST).unwrap();
        let (patched, delta) = stage1_patch(&dol).unwrap();
        assert_eq!(patched, dol);
        assert_eq!(vcdiff::decode(&dol, &delta).unwrap(), dol);
        let words_out = window_words(&patched, WINDOW_GUEST).unwrap();
        assert_eq!(words_out, words);
    }

    #[test]
    fn patch_image_writes_only_the_output_copy_and_records_hashes() {
        let dir = std::env::temp_dir().join(format!("mg1-imgpatch-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let dol = synthetic_dol();
        let image = synthetic_image(&dol);
        let src = dir.join("src.iso");
        let out = dir.join("out.iso");
        std::fs::write(&src, &image).unwrap();
        let before = std::fs::read(&src).unwrap();
        let manifest = patch_image(&src, &out, &dol).unwrap();
        assert_eq!(std::fs::read(&src).unwrap(), before, "source untouched");
        assert_eq!(manifest.source_image_sha256, hex(&sha256(&image)));
        let out_image = std::fs::read(&out).unwrap();
        assert_eq!(manifest.output_image_sha256, hex(&sha256(&out_image)));
        assert!(out.with_extension("mg1patch.txt").exists());
        assert!(validate_patched_image(&out, &dol).is_ok());
        // refusing to overwrite
        assert!(patch_image(&src, &out, &dol).is_err());
        std::fs::remove_dir_all(&dir).ok();
    }
}
