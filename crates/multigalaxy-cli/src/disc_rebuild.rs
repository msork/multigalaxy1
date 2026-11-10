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
const VALIDATION_CACHE_SCHEMA: u32 = 1;
/// Bump this whenever the security or interpretation of rebuilt ISO evidence changes.
pub const VALIDATION_POLICY_ID: &str = "mg1-wit-rebuilt-iso-validation-v1";

#[derive(Debug, Clone, PartialEq, Eq)]
struct ValidationCacheStamp {
    policy_id: String,
    game_id: String,
    revision: u16,
    source_image_sha256: String,
    output_image_sha256: String,
    source_dol_sha256: String,
    target_dol_sha256: String,
    delta_sha256: String,
    manifest_sha256: String,
    allstars_sha256: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct ValidationCacheRecord {
    stamp: ValidationCacheStamp,
    record_sha256: String,
}

#[derive(Debug, Clone)]
pub struct CachedValidationOutcome {
    pub manifest: RebuildManifest,
    pub cache_hit: bool,
    pub elapsed: Duration,
}

impl ValidationCacheRecord {
    fn new(stamp: ValidationCacheStamp) -> Self {
        let body = stamp.to_text();
        Self {
            stamp,
            record_sha256: hex(&sha256(body.as_bytes())),
        }
    }

    fn to_text(&self) -> String {
        format!(
            "{}record_sha256={}\n",
            self.stamp.to_text(),
            self.record_sha256
        )
    }

    fn parse_text(text: &str) -> Result<Self, String> {
        const FIELDS: [&str; 12] = [
            "cache_schema",
            "policy_id",
            "game_id",
            "revision",
            "source_image_sha256",
            "output_image_sha256",
            "source_dol_sha256",
            "target_dol_sha256",
            "delta_sha256",
            "manifest_sha256",
            "allstars_sha256",
            "record_sha256",
        ];
        if text.len() > 4096 {
            return Err("validation cache exceeds its size bound".into());
        }
        let mut values = std::collections::BTreeMap::new();
        for (line_number, line) in text.lines().enumerate() {
            let (key, value) = line
                .split_once('=')
                .ok_or_else(|| format!("validation cache line {} is malformed", line_number + 1))?;
            if !FIELDS.contains(&key) {
                return Err(format!("validation cache has unknown field {key:?}"));
            }
            if values.insert(key, value).is_some() {
                return Err(format!("validation cache repeats field {key:?}"));
            }
        }
        if values.len() != FIELDS.len() {
            let missing = FIELDS
                .iter()
                .find(|field| !values.contains_key(**field))
                .copied()
                .unwrap_or("unknown");
            return Err(format!("validation cache is missing field {missing:?}"));
        }
        let take = |key: &'static str| values.get(key).copied().unwrap_or_default();
        let schema = take("cache_schema")
            .parse::<u32>()
            .map_err(|_| "validation cache schema is malformed")?;
        if schema != VALIDATION_CACHE_SCHEMA {
            return Err(format!("unsupported validation cache schema {schema}"));
        }
        let revision = take("revision")
            .parse::<u16>()
            .map_err(|_| "validation cache revision is malformed")?;
        let stamp = ValidationCacheStamp {
            policy_id: take("policy_id").to_owned(),
            game_id: take("game_id").to_owned(),
            revision,
            source_image_sha256: take("source_image_sha256").to_owned(),
            output_image_sha256: take("output_image_sha256").to_owned(),
            source_dol_sha256: take("source_dol_sha256").to_owned(),
            target_dol_sha256: take("target_dol_sha256").to_owned(),
            delta_sha256: take("delta_sha256").to_owned(),
            manifest_sha256: take("manifest_sha256").to_owned(),
            allstars_sha256: take("allstars_sha256").to_owned(),
        };
        validate_cache_stamp(&stamp)?;
        let record = Self {
            stamp,
            record_sha256: take("record_sha256").to_owned(),
        };
        parse_sha256(&record.record_sha256)
            .map_err(|_| "validation cache record digest is malformed")?;
        if hex(&sha256(record.stamp.to_text().as_bytes())) != record.record_sha256 {
            return Err("validation cache record digest does not match its contents".into());
        }
        Ok(record)
    }
}

impl ValidationCacheStamp {
    fn to_text(&self) -> String {
        format!(
            "cache_schema={VALIDATION_CACHE_SCHEMA}\npolicy_id={}\ngame_id={}\nrevision={}\nsource_image_sha256={}\noutput_image_sha256={}\nsource_dol_sha256={}\ntarget_dol_sha256={}\ndelta_sha256={}\nmanifest_sha256={}\nallstars_sha256={}\n",
            self.policy_id,
            self.game_id,
            self.revision,
            self.source_image_sha256,
            self.output_image_sha256,
            self.source_dol_sha256,
            self.target_dol_sha256,
            self.delta_sha256,
            self.manifest_sha256,
            self.allstars_sha256,
        )
    }
}

fn validate_cache_stamp(stamp: &ValidationCacheStamp) -> Result<(), String> {
    if stamp.policy_id != VALIDATION_POLICY_ID || stamp.game_id != "RMGP01" || stamp.revision != 0 {
        return Err("validation cache policy or PAL identity is unsupported".into());
    }
    for (name, value) in [
        ("source_image_sha256", &stamp.source_image_sha256),
        ("output_image_sha256", &stamp.output_image_sha256),
        ("source_dol_sha256", &stamp.source_dol_sha256),
        ("target_dol_sha256", &stamp.target_dol_sha256),
        ("delta_sha256", &stamp.delta_sha256),
        ("manifest_sha256", &stamp.manifest_sha256),
        ("allstars_sha256", &stamp.allstars_sha256),
    ] {
        parse_sha256(value).map_err(|_| format!("validation cache {name} is malformed"))?;
    }
    Ok(())
}

fn should_lookup_validation_cache(force_revalidate: bool) -> bool {
    !force_revalidate
}

fn validation_cache_matches(
    record: &ValidationCacheRecord,
    expected: &ValidationCacheStamp,
) -> bool {
    record.stamp == *expected
}

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

    pub fn parse_text(text: &str) -> Result<Self, String> {
        const REQUIRED_FIELDS: [&str; 10] = [
            "schema_version",
            "patch_id",
            "game_id",
            "revision",
            "input_format",
            "source_image_sha256",
            "output_image_sha256",
            "source_dol_sha256",
            "target_dol_sha256",
            "delta_sha256",
        ];
        const ALL_FIELDS: [&str; 13] = [
            "schema_version",
            "patch_id",
            "game_id",
            "revision",
            "input_format",
            "source_image_sha256",
            "output_image_sha256",
            "source_dol_sha256",
            "target_dol_sha256",
            "delta_sha256",
            "dolphintool_sha256",
            "wit_sha256",
            "wit_version",
        ];
        let mut values = std::collections::BTreeMap::new();
        for (line_index, line) in text.lines().enumerate() {
            let (key, value) = line.split_once('=').ok_or_else(|| {
                format!("WIT rebuild manifest line {} is malformed", line_index + 1)
            })?;
            if !ALL_FIELDS.contains(&key) {
                return Err(format!("WIT rebuild manifest has unknown field {key:?}"));
            }
            if values.insert(key, value).is_some() {
                return Err(format!("WIT rebuild manifest repeats field {key:?}"));
            }
        }
        if let Some(missing) = REQUIRED_FIELDS
            .iter()
            .find(|field| !values.contains_key(**field))
            .copied()
        {
            return Err(format!("WIT rebuild manifest is missing field {missing:?}"));
        }
        let take = |key: &'static str| values.get(key).copied().unwrap_or_default();
        let parse_u32 = |key: &'static str| {
            take(key)
                .parse::<u32>()
                .map_err(|_| format!("WIT rebuild manifest field {key} is not an integer"))
        };
        Ok(Self {
            schema_version: parse_u32("schema_version")?,
            patch_id: take("patch_id").to_owned(),
            game_id: take("game_id").to_owned(),
            revision: u16::try_from(parse_u32("revision")?)
                .map_err(|_| "WIT rebuild manifest revision is out of range")?,
            input_format: take("input_format").to_owned(),
            source_image_sha256: take("source_image_sha256").to_owned(),
            output_image_sha256: take("output_image_sha256").to_owned(),
            source_dol_sha256: take("source_dol_sha256").to_owned(),
            target_dol_sha256: take("target_dol_sha256").to_owned(),
            delta_sha256: take("delta_sha256").to_owned(),
            dolphintool_sha256: take("dolphintool_sha256").to_owned(),
            wit_sha256: take("wit_sha256").to_owned(),
            wit_version: take("wit_version").to_owned(),
        })
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

/// Revalidate a WIT-built ISO before dev-launch. This checks both image
/// identities, the pinned source and extracted target DOLs, the exact tools
/// recorded in the manifest, partition integrity, and unchanged game files.
pub fn validate_rebuilt_iso_for_launch(
    dolphintool: &Path,
    wit: &Path,
    source: &Path,
    output: &Path,
    expected_dol: &[u8],
    allstars_writes: &[PatchWrite],
) -> Result<RebuildManifest, String> {
    require_iso_extension("source", source)?;
    require_iso_extension("rebuilt output", output)?;
    for (label, path) in [
        ("DolphinTool", dolphintool),
        ("WIT", wit),
        ("source ISO", source),
        ("rebuilt ISO", output),
    ] {
        if !path.is_file() {
            return Err(format!("{label} is missing or not a regular file"));
        }
    }
    let source_abs = source
        .canonicalize()
        .map_err(|e| format!("cannot resolve source ISO: {e}"))?;
    let output_abs = output
        .canonicalize()
        .map_err(|e| format!("cannot resolve rebuilt ISO: {e}"))?;
    if source_abs == output_abs {
        return Err("source and rebuilt ISO paths must be different".into());
    }
    let manifest_text = fs::read_to_string(manifest_path(&output_abs))
        .map_err(|e| format!("rebuilt ISO has no readable adjacent WIT manifest: {e}"))?;
    let manifest_sha256 = hex(&sha256(manifest_text.as_bytes()));
    let manifest = RebuildManifest::parse_text(&manifest_text)?;
    eprintln!("rebuilt ISO validation phase=hash-images-and-provenance");
    let source_hash = hex(&hash_file(&source_abs)?);
    let output_hash = hex(&hash_file(&output_abs)?);
    // Historical manifest tool hashes are provenance, not validation locks.
    // Keep current identities only to detect a tool being swapped mid-run.
    let dolphin_tool_hash_before = hex(&hash_file(dolphintool)?);
    let wit_hash_before = hex(&hash_file(wit)?);
    let wit_version = one_line(&tool_output(wit, &["version".into()], None)?);
    validate_rebuild_manifest_evidence(
        &manifest,
        &source_hash,
        &output_hash,
        &hex(&sha256(expected_dol)),
        &dolphin_tool_hash_before,
        &wit_hash_before,
        &wit_version,
        expected_dol,
    )?;

    eprintln!("rebuilt ISO validation phase=verify-disc-identities-and-partitions");
    verify_dolphintool_header(dolphintool, &source_abs)?;
    verify_dolphintool_header(dolphintool, &output_abs)?;
    run_wit(wit, &["verify".into(), wit_path(&source_abs)?], None)?;
    run_wit(wit, &["verify".into(), wit_path(&output_abs)?], None)?;

    let output_parent = output_abs.parent().unwrap_or_else(|| Path::new("."));
    let stage = make_staging_dir(output_parent)?;
    let source_extract = stage.0.join("source-fst");
    let output_extract = stage.0.join("rebuilt-fst");
    eprintln!("rebuilt ISO validation phase=extract-and-compare-files-and-dols");
    for (image, destination) in [
        (&source_abs, &source_extract),
        (&output_abs, &output_extract),
    ] {
        run_wit(
            wit,
            &[
                "extract".into(),
                wit_path(image)?,
                wit_path(destination)?,
                "--psel=data".into(),
                "--pmode=none".into(),
            ],
            None,
        )?;
    }
    let source_extracted_dol = fs::read(source_extract.join(DOL_RELATIVE))
        .map_err(|e| format!("WIT could not extract source {DOL_RELATIVE}: {e}"))?;
    if source_extracted_dol != expected_dol {
        return Err("source ISO extracted DOL does not match the pinned PAL DOL".into());
    }
    let target_dol = fs::read(output_extract.join(DOL_RELATIVE))
        .map_err(|e| format!("WIT could not extract rebuilt {DOL_RELATIVE}: {e}"))?;
    validate_rebuild_target_dol(&manifest, expected_dol, &target_dol, allstars_writes)?;
    verify_game_files_unchanged(&source_extract, &output_extract)?;
    eprintln!("rebuilt ISO validation phase=recheck-images-and-current-tool-identities");
    if hex(&hash_file(&source_abs)?) != manifest.source_image_sha256
        || hex(&hash_file(&output_abs)?) != manifest.output_image_sha256
        || hex(&hash_file(dolphintool)?) != dolphin_tool_hash_before
        || hex(&hash_file(wit)?) != wit_hash_before
        || hex(&hash_file(&manifest_path(&output_abs))?) != manifest_sha256
    {
        return Err(
            "an image/manifest changed or a validation tool was replaced during validation".into(),
        );
    }
    eprintln!("validation tools: DolphinTool sha256={dolphin_tool_hash_before}; WIT sha256={wit_hash_before}; WIT reported={wit_version}");
    Ok(manifest)
}

/// Validate a rebuilt ISO using a persistent content-addressed local cache.
/// Cache hits still hash current image/manifest/DOL/AllStars contents and ask
/// the current DolphinTool to identify both images; only the expensive WIT
/// verification/extraction comparison is reused from the previous full run.
pub fn validate_rebuilt_iso_for_launch_cached(
    dolphintool: &Path,
    wit: &Path,
    source: &Path,
    output: &Path,
    expected_dol: &[u8],
    allstars_writes: &[PatchWrite],
    allstars_settings: &[u8],
    force_revalidate: bool,
) -> Result<CachedValidationOutcome, String> {
    let started = Instant::now();
    if !should_lookup_validation_cache(force_revalidate) {
        eprintln!("rebuilt ISO validation cache=bypassed reason=force_revalidate");
    } else {
        match try_cached_validation(
            dolphintool,
            wit,
            source,
            output,
            expected_dol,
            allstars_settings,
        ) {
            Ok(Some(manifest)) => {
                let elapsed = started.elapsed();
                eprintln!(
                    "rebuilt ISO validation cache=hit elapsed_ms={}",
                    elapsed.as_millis()
                );
                return Ok(CachedValidationOutcome {
                    manifest,
                    cache_hit: true,
                    elapsed,
                });
            }
            Ok(None) => {
                eprintln!("rebuilt ISO validation cache=miss reason=not_found_or_identity_changed")
            }
            Err(reason) => eprintln!("rebuilt ISO validation cache=miss reason={reason}"),
        }
    }

    let manifest = validate_rebuilt_iso_for_launch(
        dolphintool,
        wit,
        source,
        output,
        expected_dol,
        allstars_writes,
    )?;
    let output_abs = output
        .canonicalize()
        .map_err(|error| format!("cannot resolve rebuilt ISO for cache write: {error}"))?;
    let manifest_file = manifest_path(&output_abs);
    let stamp = stamp_from_manifest(
        &manifest,
        hex(&hash_file(&manifest_file)?),
        hex(&sha256(allstars_settings)),
    );
    validate_cache_stamp(&stamp)?;
    match write_validation_cache(&output_abs, &ValidationCacheRecord::new(stamp)) {
        Ok(()) => eprintln!("rebuilt ISO validation cache=written"),
        Err(error) => eprintln!(
            "rebuilt ISO validation cache=write_failed full_validation_passed reason={error}"
        ),
    }
    let elapsed = started.elapsed();
    eprintln!(
        "rebuilt ISO validation mode=full elapsed_ms={}",
        elapsed.as_millis()
    );
    Ok(CachedValidationOutcome {
        manifest,
        cache_hit: false,
        elapsed,
    })
}

fn try_cached_validation(
    dolphintool: &Path,
    wit: &Path,
    source: &Path,
    output: &Path,
    expected_dol: &[u8],
    allstars_settings: &[u8],
) -> Result<Option<RebuildManifest>, String> {
    require_iso_extension("source", source)?;
    require_iso_extension("rebuilt output", output)?;
    for (label, path) in [
        ("DolphinTool", dolphintool),
        ("WIT", wit),
        ("source ISO", source),
        ("rebuilt ISO", output),
    ] {
        if !path.is_file() {
            return Err(format!("{label} is missing or not a regular file"));
        }
    }
    let source_abs = source
        .canonicalize()
        .map_err(|error| format!("cannot resolve source ISO: {error}"))?;
    let output_abs = output
        .canonicalize()
        .map_err(|error| format!("cannot resolve rebuilt ISO: {error}"))?;
    if source_abs == output_abs {
        return Err("source and rebuilt ISO paths must be different".into());
    }
    let manifest_file = manifest_path(&output_abs);
    let manifest_text = fs::read_to_string(&manifest_file)
        .map_err(|error| format!("cannot read rebuild manifest: {error}"))?;
    let manifest_sha256 = hex(&sha256(manifest_text.as_bytes()));
    let manifest = RebuildManifest::parse_text(&manifest_text)?;
    let source_hash = hex(&hash_file(&source_abs)?);
    let output_hash = hex(&hash_file(&output_abs)?);
    validate_rebuild_manifest_core(
        &manifest,
        &source_hash,
        &output_hash,
        &hex(&sha256(expected_dol)),
        expected_dol,
    )?;
    let expected_stamp =
        stamp_from_manifest(&manifest, manifest_sha256, hex(&sha256(allstars_settings)));
    validate_cache_stamp(&expected_stamp)?;
    let cache_file = validation_cache_path(&output_abs);
    let cache_text = match fs::read_to_string(&cache_file) {
        Ok(text) => text,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(format!("cannot read validation cache: {error}")),
    };
    let cache = ValidationCacheRecord::parse_text(&cache_text)?;
    if !validation_cache_matches(&cache, &expected_stamp) {
        return Ok(None);
    }

    // Current DolphinTool versions are not cache identities, but the current
    // tool still must perform its real PAL-header check on both cached images.
    let tool_hash_before = hex(&hash_file(dolphintool)?);
    verify_dolphintool_header(dolphintool, &source_abs)?;
    verify_dolphintool_header(dolphintool, &output_abs)?;
    if hex(&hash_file(&source_abs)?) != expected_stamp.source_image_sha256
        || hex(&hash_file(&output_abs)?) != expected_stamp.output_image_sha256
        || hex(&hash_file(&manifest_file)?) != expected_stamp.manifest_sha256
        || hex(&hash_file(dolphintool)?) != tool_hash_before
    {
        return Err(
            "image/manifest changed or DolphinTool was replaced during cached validation".into(),
        );
    }
    Ok(Some(manifest))
}

fn stamp_from_manifest(
    manifest: &RebuildManifest,
    manifest_sha256: String,
    allstars_sha256: String,
) -> ValidationCacheStamp {
    ValidationCacheStamp {
        policy_id: VALIDATION_POLICY_ID.to_owned(),
        game_id: manifest.game_id.clone(),
        revision: manifest.revision,
        source_image_sha256: manifest.source_image_sha256.clone(),
        output_image_sha256: manifest.output_image_sha256.clone(),
        source_dol_sha256: manifest.source_dol_sha256.clone(),
        target_dol_sha256: manifest.target_dol_sha256.clone(),
        delta_sha256: manifest.delta_sha256.clone(),
        manifest_sha256,
        allstars_sha256,
    }
}

fn validation_cache_path(output: &Path) -> PathBuf {
    let name = output.file_name().unwrap_or_default().to_string_lossy();
    output.with_file_name(format!("{name}.mg1validation-cache"))
}

fn write_validation_cache(output: &Path, record: &ValidationCacheRecord) -> Result<(), String> {
    use std::io::Write as _;
    let destination = validation_cache_path(output);
    let parent = destination.parent().unwrap_or_else(|| Path::new("."));
    let mut temporary = None;
    for attempt in 0..8u32 {
        let candidate = parent.join(format!(
            ".mg1validation-{}-{}-{attempt}.tmp",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap_or_default()
                .as_nanos()
        ));
        match fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&candidate)
        {
            Ok(mut file) => {
                if let Err(error) = file
                    .write_all(record.to_text().as_bytes())
                    .and_then(|()| file.sync_all())
                {
                    drop(file);
                    let _ = fs::remove_file(&candidate);
                    return Err(format!("cannot persist cache temporary file: {error}"));
                }
                temporary = Some(candidate);
                break;
            }
            Err(error) if error.kind() == io::ErrorKind::AlreadyExists => continue,
            Err(error) => return Err(format!("cannot create cache temporary file: {error}")),
        }
    }
    let temporary = temporary.ok_or("could not reserve a unique cache temporary file")?;
    if destination.exists() {
        fs::remove_file(&destination)
            .map_err(|error| format!("cannot replace old validation cache: {error}"))?;
    }
    fs::rename(&temporary, &destination).map_err(|error| {
        let _ = fs::remove_file(&temporary);
        format!("cannot install validation cache: {error}")
    })
}

fn validate_rebuild_manifest_evidence(
    manifest: &RebuildManifest,
    source_image_sha256: &str,
    output_image_sha256: &str,
    source_dol_sha256: &str,
    dolphintool_sha256: &str,
    wit_sha256: &str,
    wit_version: &str,
    source_dol: &[u8],
) -> Result<(), String> {
    validate_rebuild_manifest_core(
        manifest,
        source_image_sha256,
        output_image_sha256,
        source_dol_sha256,
        source_dol,
    )?;
    // Current tool hashes prove the inputs are hashable and are checked again
    // after validation to prevent replacement during the run. They are not
    // compared with historical provenance hashes stored in the manifest.
    parse_sha256(dolphintool_sha256).map_err(|_| "current DolphinTool SHA-256 is invalid")?;
    parse_sha256(wit_sha256).map_err(|_| "current WIT SHA-256 is invalid")?;
    if wit_version.trim().is_empty() {
        return Err("current WIT did not report its version".into());
    }
    Ok(())
}

fn validate_rebuild_manifest_core(
    manifest: &RebuildManifest,
    source_image_sha256: &str,
    output_image_sha256: &str,
    source_dol_sha256: &str,
    source_dol: &[u8],
) -> Result<(), String> {
    if manifest.schema_version != 1
        || manifest.patch_id != "stage1-hash-pinned-dol-replacement"
        || manifest.game_id != "RMGP01"
        || manifest.revision != 0
        || manifest.input_format != "ISO"
    {
        return Err("manifest is not a supported PAL RMGP01 Rev 0 WIT replacement build".into());
    }
    for (name, value) in [
        ("source_image_sha256", &manifest.source_image_sha256),
        ("output_image_sha256", &manifest.output_image_sha256),
        ("source_dol_sha256", &manifest.source_dol_sha256),
        ("target_dol_sha256", &manifest.target_dol_sha256),
        ("delta_sha256", &manifest.delta_sha256),
    ] {
        parse_sha256(value).map_err(|_| format!("manifest {name} is not a valid SHA-256"))?;
    }
    for (name, value) in [
        ("dolphintool_sha256", &manifest.dolphintool_sha256),
        ("wit_sha256", &manifest.wit_sha256),
    ] {
        if !value.is_empty() {
            parse_sha256(value).map_err(|_| format!("manifest {name} is not a valid SHA-256"))?;
        }
    }
    if manifest.source_image_sha256 != source_image_sha256
        || manifest.output_image_sha256 != output_image_sha256
        || manifest.source_dol_sha256 != source_dol_sha256
    {
        return Err(
            "manifest source/output image or source DOL hashes do not match validation inputs"
                .into(),
        );
    }
    if manifest.source_image_sha256 == manifest.output_image_sha256 {
        return Err("replacement manifest unexpectedly describes an unchanged ISO".into());
    }
    if manifest.source_dol_sha256 == manifest.target_dol_sha256 {
        return Err("replacement manifest unexpectedly describes an unchanged DOL".into());
    }
    // Validate the explicit target identity using synthetic bytes here; the
    // full target is checked after WIT extracts the output ISO.
    if hex(&sha256(source_dol)) != manifest.source_dol_sha256 {
        return Err("manifest source DOL hash does not match the pinned PAL DOL".into());
    }
    Ok(())
}

fn require_iso_extension(label: &str, path: &Path) -> Result<(), String> {
    if path
        .extension()
        .and_then(|extension| extension.to_str())
        .map(|extension| extension.eq_ignore_ascii_case("iso"))
        != Some(true)
    {
        return Err(format!(
            "{label} must be an ISO; RVZ and other formats are unsupported"
        ));
    }
    Ok(())
}

fn validate_rebuild_target_dol(
    manifest: &RebuildManifest,
    source_dol: &[u8],
    target_dol: &[u8],
    allstars_writes: &[PatchWrite],
) -> Result<(), String> {
    if hex(&sha256(target_dol)) != manifest.target_dol_sha256 {
        return Err("rebuilt ISO extracted DOL hash does not match the manifest target".into());
    }
    validate_dol_replacement(
        source_dol,
        target_dol,
        &manifest.target_dol_sha256,
        allstars_writes,
    )?;
    let delta = vcdiff::encode(source_dol, target_dol);
    if hex(&sha256(&delta)) != manifest.delta_sha256 {
        return Err("recomputed source-to-target VCDIFF hash does not match the manifest".into());
    }
    Ok(())
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
                if std::env::var_os("MG1_REBUILD_VERBOSE").is_some() {
                    let command = args
                        .iter()
                        .map(|arg| arg.to_string_lossy())
                        .collect::<Vec<_>>()
                        .join(" ");
                    eprintln!(
                        "external_tool={} status=success args={} output_begin\n{}\nexternal_tool_output_end",
                        tool.display(),
                        command,
                        output
                    );
                }
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
    fn ori_register_with_zero_immediate_is_architecturally_neutral() {
        // PAL test candidate at 0x80078A5C: `ori r7,r7,0` replaces the
        // canonical `ori r0,r0,0` NOP. PPC ORI has no record-form variant.
        for word in [0x6000_0000u32, 0x60E7_0000u32] {
            assert_eq!(word >> 26, 24, "expected primary opcode ORI");
            let source = (word >> 21) & 0x1f;
            let destination = (word >> 16) & 0x1f;
            let immediate = word & 0xffff;
            assert_eq!(source, destination);
            assert_eq!(immediate, 0);
        }
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

    fn launch_manifest(source: &[u8], target: &[u8]) -> RebuildManifest {
        RebuildManifest {
            schema_version: 1,
            patch_id: "stage1-hash-pinned-dol-replacement".into(),
            game_id: "RMGP01".into(),
            revision: 0,
            input_format: "ISO".into(),
            source_image_sha256: "1".repeat(64),
            output_image_sha256: "2".repeat(64),
            source_dol_sha256: hex(&sha256(source)),
            target_dol_sha256: hex(&sha256(target)),
            delta_sha256: hex(&sha256(&vcdiff::encode(source, target))),
            dolphintool_sha256: "4".repeat(64),
            wit_sha256: "5".repeat(64),
            wit_version: "wit: Wiimms ISO Tool v3.02a r7679".into(),
        }
    }

    fn validation_cache_fixture() -> (RebuildManifest, ValidationCacheRecord) {
        let source = synthetic_dol();
        let mut target = source.clone();
        target[0x110..0x114].copy_from_slice(&0x60E7_0000u32.to_be_bytes());
        let manifest = launch_manifest(&source, &target);
        let stamp = stamp_from_manifest(&manifest, "6".repeat(64), "7".repeat(64));
        (manifest, ValidationCacheRecord::new(stamp))
    }

    #[test]
    fn validation_cache_is_strict_and_detects_corruption() {
        let (_, record) = validation_cache_fixture();
        let encoded = record.to_text();
        assert_eq!(ValidationCacheRecord::parse_text(&encoded).unwrap(), record);
        assert!(!encoded.contains("dolphintool_sha256"));
        assert!(!encoded.contains("wit_sha256"));
        assert!(!encoded.contains("wit_version"));

        let corrupted = encoded.replacen("output_image_sha256=2", "output_image_sha256=3", 1);
        assert!(ValidationCacheRecord::parse_text(&corrupted)
            .unwrap_err()
            .contains("digest"));
        assert!(
            ValidationCacheRecord::parse_text(&encoded.replace("game_id=RMGP01\n", ""))
                .unwrap_err()
                .contains("missing field")
        );
        assert!(
            ValidationCacheRecord::parse_text(&format!("{encoded}unexpected=x\n"))
                .unwrap_err()
                .contains("unknown field")
        );
        assert!(ValidationCacheRecord::parse_text(&"x".repeat(4097))
            .unwrap_err()
            .contains("size bound"));
    }

    #[test]
    fn validation_cache_invalidates_for_content_manifest_patch_and_policy_changes() {
        let (manifest, record) = validation_cache_fixture();
        let baseline = record.stamp.clone();
        assert!(validation_cache_matches(&record, &baseline));

        for changed in [
            ValidationCacheStamp {
                source_image_sha256: "8".repeat(64),
                ..baseline.clone()
            },
            ValidationCacheStamp {
                output_image_sha256: "8".repeat(64),
                ..baseline.clone()
            },
            ValidationCacheStamp {
                source_dol_sha256: "8".repeat(64),
                ..baseline.clone()
            },
            ValidationCacheStamp {
                target_dol_sha256: "8".repeat(64),
                ..baseline.clone()
            },
            ValidationCacheStamp {
                delta_sha256: "8".repeat(64),
                ..baseline.clone()
            },
            ValidationCacheStamp {
                manifest_sha256: "8".repeat(64),
                ..baseline.clone()
            },
            ValidationCacheStamp {
                allstars_sha256: "8".repeat(64),
                ..baseline.clone()
            },
            ValidationCacheStamp {
                policy_id: "mg1-future-policy".into(),
                ..baseline.clone()
            },
        ] {
            assert!(!validation_cache_matches(&record, &changed));
        }
        let mut bad_policy = baseline.clone();
        bad_policy.policy_id = "mg1-future-policy".into();
        assert!(validate_cache_stamp(&bad_policy).is_err());

        // Dolphin, DolphinTool, and WIT versions are deliberately not cache
        // inputs. The historical tool fields stay in the manifest but do not
        // appear in the stamp; current DolphinTool header behavior is checked.
        assert!(manifest.to_text().contains("dolphintool_sha256="));
        assert!(baseline.to_text().contains("policy_id="));
        assert!(!baseline.to_text().contains("dolphintool"));
        assert!(!baseline.to_text().contains("wit_"));
    }

    #[test]
    fn force_revalidate_bypasses_cache_lookup() {
        assert!(should_lookup_validation_cache(false));
        assert!(!should_lookup_validation_cache(true));
        let (_, record) = validation_cache_fixture();
        assert!(validation_cache_matches(&record, &record.stamp));
        // The force flag is handled before lookup, even when the record is valid.
        assert!(!should_lookup_validation_cache(true));
    }

    #[test]
    fn strict_wit_manifest_parser_round_trips_and_rejects_ambiguous_fields() {
        let source = synthetic_dol();
        let mut target = source.clone();
        target[0x110..0x114].copy_from_slice(&0x60E7_0000u32.to_be_bytes());
        let manifest = launch_manifest(&source, &target);
        assert_eq!(
            RebuildManifest::parse_text(&manifest.to_text()).unwrap(),
            manifest
        );

        let duplicate = format!(
            "{}source_dol_sha256={}\n",
            manifest.to_text(),
            "f".repeat(64)
        );
        assert!(RebuildManifest::parse_text(&duplicate)
            .unwrap_err()
            .contains("repeats"));
        let unknown = format!("{}unexpected=yes\n", manifest.to_text());
        assert!(RebuildManifest::parse_text(&unknown)
            .unwrap_err()
            .contains("unknown field"));
        let missing = manifest.to_text().replace("revision=0\n", "");
        assert!(RebuildManifest::parse_text(&missing)
            .unwrap_err()
            .contains("missing field"));

        let provenance_omitted = manifest
            .to_text()
            .lines()
            .filter(|line| {
                !line.starts_with("dolphintool_sha256=")
                    && !line.starts_with("wit_sha256=")
                    && !line.starts_with("wit_version=")
            })
            .collect::<Vec<_>>()
            .join("\n");
        let parsed_without_provenance = RebuildManifest::parse_text(&provenance_omitted).unwrap();
        assert!(parsed_without_provenance.dolphintool_sha256.is_empty());
        assert!(parsed_without_provenance.wit_sha256.is_empty());
        assert!(parsed_without_provenance.wit_version.is_empty());
    }

    #[test]
    fn launch_manifest_authorizes_exact_changed_target_without_requiring_source_hash_equality() {
        let source = synthetic_dol();
        let mut target = source.clone();
        target[0x110..0x114].copy_from_slice(&0x60E7_0000u32.to_be_bytes());
        let manifest = launch_manifest(&source, &target);
        validate_rebuild_manifest_evidence(
            &manifest,
            &manifest.source_image_sha256,
            &manifest.output_image_sha256,
            &manifest.source_dol_sha256,
            &manifest.dolphintool_sha256,
            &manifest.wit_sha256,
            &manifest.wit_version,
            &source,
        )
        .unwrap();
        assert_ne!(manifest.source_dol_sha256, manifest.target_dol_sha256);
        assert_ne!(manifest.source_image_sha256, manifest.output_image_sha256);
        validate_rebuild_target_dol(&manifest, &source, &target, &[]).unwrap();

        let mut unexpected_target = target.clone();
        unexpected_target[0x114] ^= 1;
        assert!(validate_rebuild_target_dol(&manifest, &source, &unexpected_target, &[]).is_err());
        let mut allstars_target = target.clone();
        allstars_target[0x110..0x114].copy_from_slice(&0x60E7_0000u32.to_be_bytes());
        let writes = [PatchWrite {
            address: 0x8000_4010,
            value: 0x6000_0000,
        }];
        // This synthetic write maps to file offset 0x110 (guest 0x80004010).
        assert!(
            validate_rebuild_target_dol(&manifest, &source, &allstars_target, &writes)
                .unwrap_err()
                .contains("overlaps a mandatory AllStars write")
        );

        let mut wrong_output = manifest.clone();
        wrong_output.output_image_sha256 = "6".repeat(64);
        assert!(validate_rebuild_manifest_evidence(
            &wrong_output,
            &manifest.source_image_sha256,
            &manifest.output_image_sha256,
            &manifest.source_dol_sha256,
            &manifest.dolphintool_sha256,
            &manifest.wit_sha256,
            &manifest.wit_version,
            &source,
        )
        .is_err());
        let mut wrong_source_dol = manifest.clone();
        wrong_source_dol.source_dol_sha256 = "7".repeat(64);
        assert!(validate_rebuild_manifest_evidence(
            &wrong_source_dol,
            &manifest.source_image_sha256,
            &manifest.output_image_sha256,
            &manifest.source_dol_sha256,
            &manifest.dolphintool_sha256,
            &manifest.wit_sha256,
            &manifest.wit_version,
            &source,
        )
        .is_err());
        let mut wrong_tools = manifest.clone();
        wrong_tools.wit_sha256 = "invalid-provenance-hash".into();
        assert!(validate_rebuild_manifest_evidence(
            &wrong_tools,
            &manifest.source_image_sha256,
            &manifest.output_image_sha256,
            &manifest.source_dol_sha256,
            &manifest.dolphintool_sha256,
            &manifest.wit_sha256,
            &manifest.wit_version,
            &source,
        )
        .unwrap_err()
        .contains("wit_sha256 is not a valid SHA-256"));
        let mut changed_historical_version = manifest.clone();
        changed_historical_version.wit_version = "another historical WIT build".into();
        assert!(validate_rebuild_manifest_evidence(
            &changed_historical_version,
            &manifest.source_image_sha256,
            &manifest.output_image_sha256,
            &manifest.source_dol_sha256,
            &manifest.dolphintool_sha256,
            &manifest.wit_sha256,
            &manifest.wit_version,
            &source,
        )
        .is_ok());
        let mut wrong_revision = manifest.clone();
        wrong_revision.revision = 1;
        assert!(validate_rebuild_manifest_evidence(
            &wrong_revision,
            &manifest.source_image_sha256,
            &manifest.output_image_sha256,
            &manifest.source_dol_sha256,
            &manifest.dolphintool_sha256,
            &manifest.wit_sha256,
            &manifest.wit_version,
            &source,
        )
        .unwrap_err()
        .contains("PAL RMGP01 Rev 0"));
        let mut wrong_patch = manifest.clone();
        wrong_patch.patch_id = "unknown-patch".into();
        assert!(validate_rebuild_manifest_evidence(
            &wrong_patch,
            &manifest.source_image_sha256,
            &manifest.output_image_sha256,
            &manifest.source_dol_sha256,
            &manifest.dolphintool_sha256,
            &manifest.wit_sha256,
            &manifest.wit_version,
            &source,
        )
        .is_err());
    }

    #[test]
    fn accepts_updated_validation_tools_while_preserving_historical_manifest_provenance() {
        let source = synthetic_dol();
        let mut target = source.clone();
        target[0x110..0x114].copy_from_slice(&0x60E7_0000u32.to_be_bytes());
        let manifest = launch_manifest(&source, &target);
        let original_dolphintool_provenance = manifest.dolphintool_sha256.clone();
        let original_wit_provenance = manifest.wit_sha256.clone();

        validate_rebuild_manifest_evidence(
            &manifest,
            &manifest.source_image_sha256,
            &manifest.output_image_sha256,
            &manifest.source_dol_sha256,
            &"6".repeat(64),
            &"7".repeat(64),
            "Wiimms ISO Tool newer compatible build",
            &source,
        )
        .unwrap();

        assert_eq!(manifest.dolphintool_sha256, original_dolphintool_provenance);
        assert_eq!(manifest.wit_sha256, original_wit_provenance);

        let mut provenance_only_edit = manifest.clone();
        provenance_only_edit.dolphintool_sha256 = "8".repeat(64);
        provenance_only_edit.wit_sha256 = "9".repeat(64);
        provenance_only_edit.wit_version = "different historical WIT metadata".into();
        validate_rebuild_manifest_evidence(
            &provenance_only_edit,
            &manifest.source_image_sha256,
            &manifest.output_image_sha256,
            &manifest.source_dol_sha256,
            &"6".repeat(64),
            &"7".repeat(64),
            "Wiimms ISO Tool newer compatible build",
            &source,
        )
        .unwrap();

        let mut no_tool_provenance = manifest.clone();
        no_tool_provenance.dolphintool_sha256.clear();
        no_tool_provenance.wit_sha256.clear();
        no_tool_provenance.wit_version.clear();
        validate_rebuild_manifest_evidence(
            &no_tool_provenance,
            &manifest.source_image_sha256,
            &manifest.output_image_sha256,
            &manifest.source_dol_sha256,
            &"6".repeat(64),
            &"7".repeat(64),
            "Wiimms ISO Tool newer compatible build",
            &source,
        )
        .unwrap();
    }

    #[test]
    fn changed_iso_bytes_and_tampered_dol_or_delta_hashes_are_rejected() {
        let source = synthetic_dol();
        let mut target = source.clone();
        target[0x110..0x114].copy_from_slice(&0x60E7_0000u32.to_be_bytes());
        let mut manifest = launch_manifest(&source, &target);
        let source_iso = b"synthetic source ISO bytes".to_vec();
        let output_iso = b"synthetic rebuilt ISO bytes".to_vec();
        manifest.source_image_sha256 = hex(&sha256(&source_iso));
        manifest.output_image_sha256 = hex(&sha256(&output_iso));
        let mut tampered_output_iso = output_iso.clone();
        tampered_output_iso[8] ^= 0x40;
        assert!(validate_rebuild_manifest_evidence(
            &manifest,
            &manifest.source_image_sha256,
            &hex(&sha256(&tampered_output_iso)),
            &manifest.source_dol_sha256,
            &manifest.dolphintool_sha256,
            &manifest.wit_sha256,
            &manifest.wit_version,
            &source,
        )
        .is_err());

        let mut tampered_source_iso = source_iso;
        tampered_source_iso[0] ^= 1;
        assert!(validate_rebuild_manifest_evidence(
            &manifest,
            &hex(&sha256(&tampered_source_iso)),
            &manifest.output_image_sha256,
            &manifest.source_dol_sha256,
            &manifest.dolphintool_sha256,
            &manifest.wit_sha256,
            &manifest.wit_version,
            &source,
        )
        .is_err());

        let mut tampered_source = manifest.clone();
        tampered_source.source_dol_sha256 = "8".repeat(64);
        assert!(validate_rebuild_manifest_evidence(
            &tampered_source,
            &manifest.source_image_sha256,
            &manifest.output_image_sha256,
            &manifest.source_dol_sha256,
            &"9".repeat(64),
            &"a".repeat(64),
            "compatible WIT",
            &source,
        )
        .is_err());

        let mut tampered_delta = manifest.clone();
        tampered_delta.delta_sha256 = "b".repeat(64);
        assert!(validate_rebuild_target_dol(&tampered_delta, &source, &target, &[]).is_err());
    }

    #[test]
    fn rebuilt_iso_validation_rejects_unsupported_image_extensions() {
        assert!(require_iso_extension("source", Path::new("game.rvz"))
            .unwrap_err()
            .contains("RVZ and other formats are unsupported"));
        assert!(require_iso_extension("output", Path::new("game.iso")).is_ok());
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
