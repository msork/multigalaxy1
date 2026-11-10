//! Partition-aware ISO extraction and rebuild through user-supplied DolphinTool
//! and Wiimms ISO Tool executables. The original image is opened read-only.

use multigalaxy_patch::{sha256, sha256_reader};
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
    let (target_dol, delta) = crate::image_patch::stage1_patch(&extracted_dol)?;
    if target_dol.len() != extracted_dol.len() {
        return Err("Stage 1 DOL patch changed file size".into());
    }
    fs::write(&dol_path, &target_dol)
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
    if sha256(&rebuilt_dol) != sha256(&target_dol) {
        return Err("rebuilt ISO main.dol hash differs from the VCDIFF target".into());
    }
    verify_game_files_unchanged(&extracted, &rebuilt_extract)?;

    let source_hash_after = hash_file(&source_abs)?;
    if source_hash_before != source_hash_after {
        return Err("source ISO changed during the rebuild; refusing to publish output".into());
    }
    let output_hash = hash_file(&staged_iso)?;
    let manifest = RebuildManifest {
        schema_version: 1,
        patch_id: "stage1-trampoline-identity-wit-rebuild".into(),
        game_id: "RMGP01".into(),
        revision: 0,
        input_format: "ISO".into(),
        source_image_sha256: hex(&source_hash_before),
        output_image_sha256: hex(&output_hash),
        source_dol_sha256: hex(&sha256(expected_dol)),
        target_dol_sha256: hex(&sha256(&target_dol)),
        delta_sha256: hex(&sha256(&delta)),
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
