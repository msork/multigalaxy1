use multigalaxy_patch::{
    branch_target, combine_allstars_swaps, parse_dol, parse_onframe_patch, sha256,
    validate_pal_patch_writes, DiscIdentity, RevisionCatalog, SectionKind,
};
use std::{fs, path::Path};

const ALLSTARS_SHA256: &str = "c3b00b9e7471f980b887390d3b8bff11fd102be76260d60a42f218103617d5a9";
const SWAP_LZL_SHA256: &str = "5003d243aebbac6308702586d333ada04038d9dbe0aaf5be55c6ad907a71427b";
const SWAP_RZR_SHA256: &str = "f1444b6e45cd8349333155c750c160ad364888cdc204e0acd4b83cf0ecc362d5";
const ALLSTARS_NAME: &str = "$ SMG1 Classic Controller (AllStars + L/ZL + R/ZR)";

#[derive(Debug)]
pub struct PreparedPatch {
    pub settings: String,
    pub total_writes: usize,
    pub dol_text_writes: usize,
    pub dol_changed_words: usize,
    pub changed_sites: Vec<String>,
}

fn hash_hex(bytes: &[u8]) -> String {
    sha256(bytes).iter().map(|b| format!("{b:02x}")).collect()
}

fn read_exact_source(
    directory: &Path,
    filename: &str,
    expected_hash: &str,
) -> Result<String, String> {
    let path = directory.join(filename);
    let bytes = fs::read(&path).map_err(|e| format!("cannot read {filename}: {e}"))?;
    let actual = hash_hex(&bytes);
    if actual != expected_hash {
        return Err(format!("{filename} fingerprint mismatch (expected {expected_hash}, found {actual}); refusing to enable an unreviewed patch"));
    }
    String::from_utf8(bytes).map_err(|e| format!("{filename} is not UTF-8: {e}"))
}

pub fn prepare_allstars(directory: &Path, dol: &[u8]) -> Result<PreparedPatch, String> {
    let identity = DiscIdentity {
        game_id: "RMGP01".into(),
        revision: 0,
    };
    let catalog = RevisionCatalog::new();
    let map = catalog
        .identify(&identity, dol)
        .map_err(|e| format!("AllStars patch requires the exact verified PAL Rev 0 DOL: {e}"))?;
    let base = read_exact_source(directory, "SMG-AllStars-Pal.txt", ALLSTARS_SHA256)?;
    let swap_l = read_exact_source(directory, "SMG-SwapLZL-Pal.txt", SWAP_LZL_SHA256)?;
    let swap_r = read_exact_source(directory, "SMG-SwapRZR-Pal.txt", SWAP_RZR_SHA256)?;
    let writes = combine_allstars_swaps(&base, &swap_l, &swap_r).map_err(|e| e.to_string())?;
    let dol_text_writes = validate_pal_patch_writes(dol, &writes).map_err(|e| e.to_string())?;
    for write in &writes {
        let write_range = u64::from(write.address)..u64::from(write.address) + 4;
        if map.hooks.iter().any(|hook| {
            let hook_range =
                u64::from(hook.address)..u64::from(hook.address) + hook.expected_bytes.len() as u64;
            write_range.start < hook_range.end && hook_range.start < write_range.end
        }) {
            return Err(format!(
                "AllStars patch overlaps registered MultiGalaxy1 hook at 0x{:08x}",
                write.address
            ));
        }
    }

    let info = parse_dol(dol).map_err(|e| format!("invalid DOL: {e}"))?;
    let writes = parse_onframe_patch(
        &writes
            .iter()
            .map(|w| format!("0x{:08x}:dword:0x{:08x}", w.address, w.value))
            .collect::<Vec<_>>()
            .join("\n"),
    )
    .map_err(|e| e.to_string())?;
    let mut dol_changed_words = 0;
    let mut changed_sites = Vec::new();
    let mut output = format!("[OnFrame]\n{ALLSTARS_NAME}\n");
    for write in &writes {
        if let Some(section) = info.sections.iter().find(|s| {
            s.kind == SectionKind::Text
                && write.address >= s.address
                && u64::from(write.address) + 4 <= u64::from(s.address) + u64::from(s.size)
        }) {
            let offset = section.file_offset as usize + (write.address - section.address) as usize;
            let old_word = u32::from_be_bytes(dol[offset..offset + 4].try_into().unwrap());
            if old_word != write.value {
                dol_changed_words += 1;
                let branch = branch_target(write.value, write.address)
                    .map(|target| format!(" branch_target=0x{target:08x}"))
                    .unwrap_or_default();
                changed_sites.push(format!(
                    "0x{:08x}: original=0x{old_word:08x} patch=0x{:08x}{branch}",
                    write.address, write.value
                ));
            }
        }
        output.push_str(&format!(
            "0x{:08x}:dword:0x{:08x}\n",
            write.address, write.value
        ));
    }
    output.push_str(&format!("\n[OnFrame_Enabled]\n{ALLSTARS_NAME}\n"));
    Ok(PreparedPatch {
        settings: output,
        total_writes: writes.len(),
        dol_text_writes,
        dol_changed_words,
        changed_sites,
    })
}

pub fn allstars_game_settings(patch: &PreparedPatch) -> &str {
    &patch.settings
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn patch_hashes_have_expected_width() {
        assert_eq!(ALLSTARS_SHA256.len(), 64);
        assert_eq!(SWAP_LZL_SHA256.len(), 64);
        assert_eq!(SWAP_RZR_SHA256.len(), 64);
        assert_eq!(
            hash_hex(b"abc"),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
    }
}
