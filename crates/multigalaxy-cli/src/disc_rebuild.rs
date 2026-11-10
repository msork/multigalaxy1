//! Partition-aware ISO extraction and rebuild through user-supplied DolphinTool
//! and Wiimms ISO Tool executables. The original image is opened read-only.

use multigalaxy_patch::{parse_dol, sha256, sha256_reader, vcdiff, PatchWrite, SectionKind};
use std::ffi::OsString;
use std::fs::{self, File};
use std::io::{self, Read};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

const TOOL_TIMEOUT: Duration = Duration::from_secs(300);
const DOL_RELATIVE: &str = "sys/main.dol";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RebuildManifest {
    pub schema_version: u32,
    pub patch_id: String,
    pub game_id: String,
    pub revision: u16,
    pub input_format: String,
    pub source_image_sha256: String,
    pub output_image_sha256: String,
    pub source_dol_sha256: String,
    pub target_dol_sha256: String,
    pub delta_sha256: String,
    pub dolphintool_sha256: String,
    pub wit_sha256: String,
    pub wit_version: String,
}

impl RebuildManifest {
    pub fn to_text(&self) -> String {
        format!(
            "schema_version={}\npatch_id={}\ngame_id={}\nrevision={}\ninput_format={}\nsource_image_sha256={}\noutput_image_sha256={}\nsource_dol_sha256={}\ntarget_dol_sha256={}\ndelta_sha256={}\ndolphintool_sha256={}\nwit_sha256={}\nwit_version={}\n",
            self.schema_version,
            self.patch_id,
            self.game_id,
            self.revision,
            self.input_format,
            self.source_image_sha256,
            self.output_image_sha256,
            self.source_dol_sha256,
            self.target_dol_sha256,
            self.delta_sha256,
            self.dolphintool_sha256,
            self.wit_sha256,
            self.wit_version
        )
    }
}

struct StagingDir(PathBuf);

impl Drop for StagingDir {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

/// Extract, identity-patch, recompose, and verify a PAL ISO. Only the requested
/// output and its manifest survive a successful run. Intermediate files are
/// isolated in a new sibling directory and removed on all ordinary exits.
pub fn rebuild_iso(
    dolphintool: &Path,
    wit: &Path,
    source: &Path,
    output: &Path,
    expected_dol: &[u8],
) -> Result<RebuildManifest, String> {
    rebuild_iso_target(
        dolphintool,
        wit,
        source,
        output,
        expected_dol,
        expected_dol,
        &vcdiff::encode(expected_dol, expected_dol),
        "stage1-trampoline-identity-wit-rebuild",
    )
}

/// Rebuild from a caller-supplied, hash-pinned PAL DOL replacement. The
/// replacement may change only bytes that map to existing DOL text sections,
/// and no changed bytes may overlap a write in the mandatory AllStars patch.
/// This validates transport and patch composition, not the semantics of a PPC
/// hook; callers must separately validate any trampoline body and target.
pub fn rebuild_iso_with_dol(
    dolphintool: &Path,
    wit: &Path,
    source: &Path,
    output: &Path,
    expected_dol: &[u8],
    target_dol: &[u8],
    expected_target_sha256: &str,
    allstars_writes: &[PatchWrite],
) -> Result<RebuildManifest, String> {
    validate_dol_replacement(
        expected_dol,
        target_dol,
        expected_target_sha256,
        allstars_writes,
    )?;
    let delta = vcdiff::encode(expected_dol, target_dol);
    rebuild_iso_target(
        dolphintool,
        wit,
        source,
        output,
        expected_dol,
        target_dol,
        &delta,
        "stage1-hash-pinned-dol-replacement",
    )
}

fn rebuild_iso_target(
    dolphintool: &Path,
    wit: &Path,
    source: &Path,
    output: &Path,
    expected_dol: &[u8],
    target_dol: &[u8],
    delta: &[u8],
    patch_id: &str,
) -> Result<RebuildManifest, String> {
    if vcdiff::decode(expected_dol, delta)? != target_dol {
        return Err("encoded VCDIFF does not reconstruct the validated target DOL".into());
    }
    if !dolphintool.is_file() || !wit.is_file() || !source.is_file() {
        return Err("DolphinTool, WIT, and source ISO must be existing regular files".into());
    }
    if source
        .extension()
        .and_then(|e| e.to_str())
        .map(|e| e.to_ascii_lowercase())
        != Some("iso".into())
        || output
            .extension()
            .and_then(|e| e.to_str())
            .map(|e| e.to_ascii_lowercase())
            != Some("iso".into())
    {
        return Err("this pipeline currently accepts ISO input and ISO output only".into());
    }
    if output.exists() {
        return Err("output ISO already exists; refusing to overwrite".into());
    }
    let source_abs = source
        .canonicalize()
        .map_err(|e| format!("cannot resolve source ISO: {e}"))?;
    let output_parent = output
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or(Path::new("."));
    let output_parent = output_parent
        .canonicalize()
        .map_err(|e| format!("cannot resolve output directory: {e}"))?;
    let output_name = output
        .file_name()
        .ok_or("output path must include a filename")?;
    let output_abs = output_parent.join(output_name);
    if source_abs == output_abs {
        return Err("source and output paths must be different".into());
    }
    let manifest_path = manifest_path(&output_abs);
    if manifest_path.exists() {
        return Err("output manifest already exists; refusing to overwrite".into());
    }

    eprintln!("rebuild phase=source-hash-and-pal-header");
    let source_hash_before = hash_file(&source_abs)?;
    verify_dolphintool_header(dolphintool, &source_abs)?;
    eprintln!("rebuild phase=verify-source-partitions");
    run_wit(wit, &["verify".into(), wit_path(&source_abs)?], None)?;
    let wit_version = tool_output(wit, &["version".into()], None)?;
    if !wit_version.contains("Wiimms ISO Tool v") {
        return Err("WIT version output did not identify Wiimms ISO Tool".into());
    }

    let stage = make_staging_dir(&output_parent)?;
    let extracted = stage.0.join("source-fst");
    eprintln!("rebuild phase=extract-data-partition");
    run_wit(
        wit,
        &[
            "extract".into(),
            wit_path(&source_abs)?,
            wit_path(&extracted)?,
            "--psel=data".into(),
            "--pmode=none".into(),
        ],
        None,
    )?;

    let dol_path = extracted.join(DOL_RELATIVE);
    let extracted_dol =
        fs::read(&dol_path).map_err(|e| format!("WIT did not extract {DOL_RELATIVE}: {e}"))?;
    if sha256(&extracted_dol) != sha256(expected_dol) {
        return Err(
            "partition-extracted main.dol does not match the pinned PAL RMGP01 Rev 0 DOL".into(),
        );
    }
    if target_dol.len() != extracted_dol.len() {
        return Err("Stage 1 DOL patch changed file size".into());
    }
    fs::write(&dol_path, target_dol)
        .map_err(|e| format!("cannot write isolated extracted DOL: {e}"))?;

    let staged_iso = stage.0.join("rebuilt.iso");
    eprintln!("rebuild phase=compose-new-iso");
    run_wit(
        wit,
        &[
            "copy".into(),
            wit_path(&extracted)?,
            wit_path(&staged_iso)?,
            "--iso".into(),
        ],
        None,
    )?;
    if !staged_iso.is_file() || fs::metadata(&staged_iso).map(|m| m.len()).unwrap_or(0) == 0 {
        return Err("WIT reported success but produced no non-empty rebuilt ISO".into());
    }
    eprintln!("rebuild phase=verify-output-partitions-and-pal-header");
    run_wit(wit, &["verify".into(), wit_path(&staged_iso)?], None)?;
    verify_dolphintool_header(dolphintool, &staged_iso)?;

    let rebuilt_extract = stage.0.join("rebuilt-fst");
    eprintln!("rebuild phase=reextract-and-compare-dol-and-game-files");
    run_wit(
        wit,
        &[
            "extract".into(),
            wit_path(&staged_iso)?,
            wit_path(&rebuilt_extract)?,
            "--psel=data".into(),
            "--pmode=none".into(),
        ],
        None,
    )?;
    let rebuilt_dol = fs::read(rebuilt_extract.join(DOL_RELATIVE))
        .map_err(|e| format!("rebuilt ISO did not re-extract {DOL_RELATIVE}: {e}"))?;
    verify_rebuilt_dol(&rebuilt_dol, target_dol)?;
    verify_game_files_unchanged(&extracted, &rebuilt_extract)?;

    let source_hash_after = hash_file(&source_abs)?;
    if source_hash_before != source_hash_after {
        return Err("source ISO changed during the rebuild; refusing to publish output".into());
    }
    let output_hash = hash_file(&staged_iso)?;
    let manifest = RebuildManifest {
        schema_version: 1,
        patch_id: patch_id.into(),
        game_id: "RMGP01".into(),
        revision: 0,
        input_format: "ISO".into(),
        source_image_sha256: hex(&source_hash_before),
        output_image_sha256: hex(&output_hash),
        source_dol_sha256: hex(&sha256(expected_dol)),
        target_dol_sha256: hex(&sha256(target_dol)),
        delta_sha256: hex(&sha256(delta)),
        dolphintool_sha256: hex(&hash_file(dolphintool)?),
        wit_sha256: hex(&hash_file(wit)?),
        wit_version: one_line(&wit_version),
    };

    let manifest_staged = stage.0.join("manifest.txt");
    fs::write(&manifest_staged, manifest.to_text())
        .map_err(|e| format!("cannot write staged rebuild manifest: {e}"))?;
    fs::rename(&staged_iso, &output_abs)
        .map_err(|e| format!("cannot publish rebuilt ISO atomically: {e}"))?;
    if let Err(error) = fs::rename(&manifest_staged, &manifest_path) {
        let _ = fs::remove_file(&output_abs);
        return Err(format!(
            "cannot publish rebuild manifest; removed this run's output ISO: {error}"
        ));
    }
    Ok(manifest)
}

fn verify_rebuilt_dol(actual: &[u8], expected: &[u8]) -> Result<(), String> {
    if actual != expected {
        return Err("rebuilt ISO main.dol bytes differ from the validated target DOL".into());
    }
    Ok(())
}

/// Validate a complete target DOL before any image extraction or output work.
/// DOL header/layout must remain identical, all changed bytes must map to
/// existing text, and no changed instruction may overlap a supplied AllStars
/// write. The exact target digest is mandatory.
pub fn validate_dol_replacement(
    source_dol: &[u8],
    target_dol: &[u8],
    expected_target_sha256: &str,
    allstars_writes: &[PatchWrite],
) -> Result<(), String> {
    if target_dol.len() != source_dol.len() {
        return Err("replacement DOL size differs from the pinned source DOL".into());
    }
    let expected_hash = parse_sha256(expected_target_sha256)?;
    if sha256(target_dol) != expected_hash {
        return Err("replacement DOL SHA-256 does not match the declared target hash".into());
    }
    let source = parse_dol(source_dol).map_err(|e| format!("invalid source DOL: {e}"))?;
    let target = parse_dol(target_dol).map_err(|e| format!("invalid replacement DOL: {e}"))?;
    if source.sections != target.sections
        || source.bss_address != target.bss_address
        || source.bss_size != target.bss_size
        || source.entry_point != target.entry_point
        || source_dol.get(..0x100) != target_dol.get(..0x100)
    {
        return Err(
            "replacement DOL changes its header, section layout, BSS, or entry point".into(),
        );
    }
    if source_dol == target_dol {
        return Err("replacement DOL has no changed bytes".into());
    }

    for (offset, (source_byte, target_byte)) in source_dol.iter().zip(target_dol).enumerate() {
        if source_byte == target_byte {
            continue;
        }
        let offset = u64::try_from(offset).map_err(|_| "DOL file offset overflow")?;
        let section = source
            .sections
            .iter()
            .find(|section| {
                section.kind == SectionKind::Text
                    && offset >= u64::from(section.file_offset)
                    && offset < u64::from(section.file_offset) + u64::from(section.size)
            })
            .ok_or("replacement changes a byte outside a loaded DOL text section")?;
        let guest = u64::from(section.address) + (offset - u64::from(section.file_offset));
        let guest_end = guest + 1;
        if allstars_writes.iter().any(|write| {
            let write_start = u64::from(write.address);
            guest < write_start + 4 && write_start < guest_end
        }) {
            return Err(format!(
                "replacement DOL change at guest address 0x{guest:08x} overlaps a mandatory AllStars write"
            ));
        }
    }
    Ok(())
}

fn parse_sha256(input: &str) -> Result<[u8; 32], String> {
    if input.len() != 64 || !input.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        return Err(
            "expected target SHA-256 must contain exactly 64 hexadecimal characters".into(),
        );
    }
    let mut digest = [0u8; 32];
    for (index, byte) in digest.iter_mut().enumerate() {
        *byte = u8::from_str_radix(&input[index * 2..index * 2 + 2], 16)
            .map_err(|_| "invalid target SHA-256")?;
    }
    Ok(digest)
}

fn verify_game_files_unchanged(source: &Path, rebuilt: &Path) -> Result<(), String> {
    let source_files = collect_game_files(&source.join("files"))?;
    let rebuilt_files = collect_game_files(&rebuilt.join("files"))?;
    if source_files != rebuilt_files {
        return Err("game DATA files changed during partition rebuild".into());
    }
    Ok(())
}

fn collect_game_files(root: &Path) -> Result<Vec<(String, u64, String)>, String> {
    fn visit(root: &Path, dir: &Path, out: &mut Vec<(String, u64, String)>) -> Result<(), String> {
        let entries =
            fs::read_dir(dir).map_err(|e| format!("cannot enumerate {}: {e}", dir.display()))?;
        for entry in entries {
            let entry = entry.map_err(|e| format!("cannot read extracted directory entry: {e}"))?;
            let ty = entry
                .file_type()
                .map_err(|e| format!("cannot inspect extracted entry: {e}"))?;
            if ty.is_symlink() {
                return Err("unexpected symlink in extracted DATA files; refusing rebuild".into());
            }
            let path = entry.path();
            if ty.is_dir() {
                visit(root, &path, out)?;
            } else if ty.is_file() {
                let relative = path
                    .strip_prefix(root)
                    .map_err(|_| "extracted file escaped DATA root")?
                    .to_string_lossy()
                    .replace('\\', "/");
                let metadata = entry
                    .metadata()
                    .map_err(|e| format!("cannot stat extracted file: {e}"))?;
                out.push((relative, metadata.len(), hex(&hash_file(&path)?)));
            } else {
                return Err("unexpected special file in extracted DATA files".into());
            }
        }
        Ok(())
    }
    let mut files = Vec::new();
    visit(root, root, &mut files)?;
    files.sort_by(|a, b| a.0.cmp(&b.0));
    Ok(files)
}

fn verify_dolphintool_header(dolphintool: &Path, image: &Path) -> Result<(), String> {
    let output = tool_output(
        dolphintool,
        &["header".into(), "-i".into(), image.as_os_str().to_owned()],
        None,
    )?;
    let value = |label: &str| {
        output
            .lines()
            .find_map(|line| line.strip_prefix(&format!("{label}: ")))
            .ok_or_else(|| format!("DolphinTool header output is missing {label}"))
    };
    let game_id = value("Game ID")?;
    let region = value("Region")?;
    let revision = value("Revision")?;
    if game_id != "RMGP01" || region != "PAL" || revision != "0" {
        return Err(format!("DolphinTool reports unsupported image ID={game_id}, region={region}, revision={revision}"));
    }
    Ok(())
}

fn run_wit(wit: &Path, args: &[OsString], cwd: Option<&Path>) -> Result<String, String> {
    tool_output(wit, args, cwd)
}

fn tool_output(tool: &Path, args: &[OsString], cwd: Option<&Path>) -> Result<String, String> {
    let mut child = Command::new(tool)
        .args(args)
        .current_dir(cwd.unwrap_or_else(|| Path::new(".")))
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("cannot start {}: {e}", tool.display()))?;
    // Drain both pipes concurrently. Waiting for process exit before reading
    // can deadlock when WIT emits enough progress output to fill a pipe.
    let mut stdout = child
        .stdout
        .take()
        .ok_or_else(|| format!("{} stdout pipe was not created", tool.display()))?;
    let stdout_reader = thread::spawn(move || {
        let mut bytes = Vec::new();
        stdout.read_to_end(&mut bytes).map(|_| bytes)
    });
    let mut stderr = child
        .stderr
        .take()
        .ok_or_else(|| format!("{} stderr pipe was not created", tool.display()))?;
    let stderr_reader = thread::spawn(move || {
        let mut bytes = Vec::new();
        stderr.read_to_end(&mut bytes).map(|_| bytes)
    });
    let deadline = Instant::now() + TOOL_TIMEOUT;
    loop {
        match child.try_wait() {
            Ok(Some(status)) => {
                let stdout = collect_pipe(stdout_reader, tool, "stdout")?;
                let stderr = collect_pipe(stderr_reader, tool, "stderr")?;
                if !status.success() {
                    return Err(format!(
                        "{} failed ({status}): {}{}",
                        tool.display(),
                        String::from_utf8_lossy(&stdout),
                        String::from_utf8_lossy(&stderr)
                    ));
                }
                let mut output = String::from_utf8_lossy(&stdout).into_owned();
                output.push_str(&String::from_utf8_lossy(&stderr));
                return Ok(output);
            }
            Ok(None) if Instant::now() < deadline => thread::sleep(Duration::from_millis(50)),
            Ok(None) => {
                let _ = child.kill();
                let _ = child.wait();
                let _ = stdout_reader.join();
                let _ = stderr_reader.join();
                return Err(format!(
                    "{} exceeded the {}s timeout and was stopped",
                    tool.display(),
                    TOOL_TIMEOUT.as_secs()
                ));
            }
            Err(error) => {
                let _ = child.kill();
                let _ = child.wait();
                let _ = stdout_reader.join();
                let _ = stderr_reader.join();
                return Err(format!(
                    "cannot inspect {} process: {error}",
                    tool.display()
                ));
            }
        }
    }
}

fn collect_pipe(
    reader: thread::JoinHandle<io::Result<Vec<u8>>>,
    tool: &Path,
    stream: &str,
) -> Result<Vec<u8>, String> {
    reader
        .join()
        .map_err(|_| format!("{} {stream} reader thread panicked", tool.display()))?
        .map_err(|error| format!("cannot read {} {stream}: {error}", tool.display()))
}

fn make_staging_dir(parent: &Path) -> Result<StagingDir, String> {
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|e| format!("system clock is before Unix epoch: {e}"))?
        .as_nanos();
    for attempt in 0..8u8 {
        let path = parent.join(format!(
            ".mg1-stage-{}-{nonce}-{attempt}",
            std::process::id()
        ));
        match fs::create_dir(&path) {
            Ok(()) => return Ok(StagingDir(path)),
            Err(error) if error.kind() == io::ErrorKind::AlreadyExists => continue,
            Err(error) => return Err(format!("cannot create rebuild staging directory: {error}")),
        }
    }
    Err("could not reserve a unique rebuild staging directory".into())
}

fn hash_file(path: &Path) -> Result<[u8; 32], String> {
    let file =
        File::open(path).map_err(|e| format!("cannot open {} for hashing: {e}", path.display()))?;
    sha256_reader(file).map_err(|e| format!("cannot hash {}: {e}", path.display()))
}

fn manifest_path(output: &Path) -> PathBuf {
    let name = output.file_name().unwrap_or_default().to_string_lossy();
    output.with_file_name(format!("{name}.mg1manifest.txt"))
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

fn one_line(input: &str) -> String {
    input.lines().next().unwrap_or_default().trim().to_owned()
}

fn wit_path(path: &Path) -> Result<OsString, String> {
    #[cfg(windows)]
    {
        let native = path
            .to_str()
            .ok_or("WIT path is not valid UTF-8 on this Windows build")?;
        let native = native.strip_prefix(r"\\?\").unwrap_or(native);
        let bytes = native.as_bytes();
        if bytes.len() < 3 || bytes[1] != b':' || !matches!(bytes[2], b'\\' | b'/') {
            return Err("WIT on Windows requires local drive-letter paths; UNC and relative paths are unsupported".into());
        }
        let drive = (bytes[0] as char).to_ascii_lowercase();
        let tail = native[3..].replace('\\', "/");
        return Ok(format!("/cygdrive/{drive}/{tail}").into());
    }
    #[cfg(not(windows))]
    {
        Ok(path.as_os_str().to_owned())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn put_be(bytes: &mut [u8], offset: usize, value: u32) {
        bytes[offset..offset + 4].copy_from_slice(&value.to_be_bytes());
    }

    fn synthetic_dol() -> Vec<u8> {
        let mut dol = vec![0; 0x124];
        put_be(&mut dol, 0x00, 0x100); // text section 0 file offset
        put_be(&mut dol, 0x1c, 0x120); // data section 0 file offset
        put_be(&mut dol, 0x48, 0x8000_4000); // text section 0 guest address
        put_be(&mut dol, 0x64, 0x8000_4020); // data section 0 guest address
        put_be(&mut dol, 0x90, 0x20); // text section 0 size
        put_be(&mut dol, 0xac, 4); // data section 0 size
        for word in dol[0x100..0x120].chunks_exact_mut(4) {
            word.copy_from_slice(&0x6000_0000u32.to_be_bytes()); // PPC nop
        }
        dol
    }

    fn sha256_hex(bytes: &[u8]) -> String {
        hex(&sha256(bytes))
    }

    #[test]
    fn accepts_hash_pinned_changed_text_dol_and_checks_expected_bytes() {
        let source = synthetic_dol();
        let mut target = source.clone();
        let expected = 0x6000_0000u32;
        let replacement = 0x7c84_2278u32; // synthetic changed instruction
        target[0x110..0x114].copy_from_slice(&replacement.to_be_bytes());
        let digest = sha256_hex(&target);

        validate_dol_replacement(&source, &target, &digest, &[]).unwrap();
        assert_eq!(
            u32::from_be_bytes(target[0x110..0x114].try_into().unwrap()),
            replacement
        );
        verify_rebuilt_dol(&target, &target).unwrap();
        let mut wrong_output = target.clone();
        wrong_output[0x110] ^= 1;
        assert!(verify_rebuilt_dol(&wrong_output, &target).is_err());
        assert_eq!(
            u32::from_be_bytes(source[0x110..0x114].try_into().unwrap()),
            expected
        );
    }

    #[test]
    fn replacement_rejects_wrong_hash_header_changes_and_data_changes() {
        let source = synthetic_dol();
        let mut target = source.clone();
        target[0x110] ^= 1;
        assert!(
            validate_dol_replacement(&source, &target, &"0".repeat(63), &[])
                .unwrap_err()
                .contains("64 hexadecimal")
        );
        assert!(
            validate_dol_replacement(&source, &target, &"0".repeat(64), &[])
                .unwrap_err()
                .contains("SHA-256")
        );

        let mut header_changed = target.clone();
        header_changed[0xe0] ^= 1;
        assert!(validate_dol_replacement(
            &source,
            &header_changed,
            &sha256_hex(&header_changed),
            &[]
        )
        .unwrap_err()
        .contains("header"));

        let mut data_changed = source.clone();
        data_changed[0x120] ^= 1;
        assert!(
            validate_dol_replacement(&source, &data_changed, &sha256_hex(&data_changed), &[])
                .unwrap_err()
                .contains("outside a loaded DOL text section")
        );
    }

    #[test]
    fn replacement_rejects_allstars_overlap_and_layout_changes() {
        let source = synthetic_dol();
        let mut target = source.clone();
        target[0x110] ^= 1;
        let digest = sha256_hex(&target);
        let writes = [PatchWrite {
            address: 0x8000_4010,
            value: 0x6000_0000,
        }];
        assert!(validate_dol_replacement(&source, &target, &digest, &writes)
            .unwrap_err()
            .contains("overlaps a mandatory AllStars write"));

        let mut layout_changed = target.clone();
        put_be(&mut layout_changed, 0x90, 0x1c);
        assert!(validate_dol_replacement(
            &source,
            &layout_changed,
            &sha256_hex(&layout_changed),
            &[]
        )
        .unwrap_err()
        .contains("section layout"));
    }

    #[test]
    fn manifest_serialization_is_stable_and_has_no_machine_paths_or_timestamps() {
        let manifest = RebuildManifest {
            schema_version: 1,
            patch_id: "stage1-trampoline-identity-wit-rebuild".into(),
            game_id: "RMGP01".into(),
            revision: 0,
            input_format: "ISO".into(),
            source_image_sha256: "a".repeat(64),
            output_image_sha256: "b".repeat(64),
            source_dol_sha256: "c".repeat(64),
            target_dol_sha256: "c".repeat(64),
            delta_sha256: "d".repeat(64),
            dolphintool_sha256: "e".repeat(64),
            wit_sha256: "f".repeat(64),
            wit_version: "wit: Wiimms ISO Tool v3.02a r7679".into(),
        };
        let first = manifest.to_text();
        assert_eq!(first, manifest.to_text());
        assert!(first
            .starts_with("schema_version=1\npatch_id=stage1-trampoline-identity-wit-rebuild\n"));
        assert!(!first.contains("C:\\"));
        assert!(!first.contains("timestamp"));
    }

    #[test]
    fn output_manifest_name_is_adjacent_and_deterministic() {
        assert_eq!(
            manifest_path(Path::new("build/out.iso")),
            Path::new("build/out.iso.mg1manifest.txt")
        );
    }

    #[cfg(windows)]
    #[test]
    fn cygwin_wit_paths_are_converted_and_unc_is_rejected() {
        assert_eq!(
            wit_path(Path::new(r"C:\Users\player\game.iso")).unwrap(),
            OsString::from("/cygdrive/c/Users/player/game.iso")
        );
        assert_eq!(
            wit_path(Path::new(r"\\?\C:\Users\player\game.iso")).unwrap(),
            OsString::from("/cygdrive/c/Users/player/game.iso")
        );
        assert!(wit_path(Path::new(r"\\server\share\game.iso")).is_err());
    }
}
