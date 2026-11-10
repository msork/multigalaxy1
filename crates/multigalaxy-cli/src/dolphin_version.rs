//! Read-only validation of the official Dolphin binaries supported by MG1.
//! The embedded application version is checked in both Dolphin.exe and its
//! sibling DolphinTool.exe; no Dolphin process is started for discovery.

use std::path::{Path, PathBuf};

const CODEHANDLER_SHA256: &str = "2b2ff0716aeb9b806cc61181e559041312957f25f04c435bd7da80a78917b33b";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DolphinVersion {
    V2609a,
    V2609b,
}

impl DolphinVersion {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::V2609a => "2609a",
            Self::V2609b => "2609b",
        }
    }
}

/// Resolve a configured install path (executable or directory) to exactly one
/// Dolphin.exe. Recursive discovery is bounded to avoid traversing user data.
pub fn discover_executable(path: &Path) -> Result<PathBuf, String> {
    if path.is_file() {
        return verify_executable_name(path).map(|_| path.to_path_buf());
    }
    if !path.is_dir() {
        return Err(format!(
            "Dolphin installation path is missing or unsupported: {}",
            path.display()
        ));
    }

    let mut pending = vec![(path.to_path_buf(), 0usize)];
    let mut matches = Vec::new();
    while let Some((directory, depth)) = pending.pop() {
        let entries = std::fs::read_dir(&directory).map_err(|error| {
            format!(
                "cannot scan Dolphin directory {}: {error}",
                directory.display()
            )
        })?;
        for entry in entries {
            let entry =
                entry.map_err(|error| format!("cannot read Dolphin directory entry: {error}"))?;
            let kind = entry
                .file_type()
                .map_err(|error| format!("cannot inspect Dolphin directory entry: {error}"))?;
            if kind.is_file()
                && entry
                    .file_name()
                    .to_string_lossy()
                    .eq_ignore_ascii_case("Dolphin.exe")
            {
                matches.push(entry.path());
            } else if kind.is_dir() && depth < 3 {
                pending.push((entry.path(), depth + 1));
            }
            if matches.len() > 1 {
                return Err(format!(
                    "multiple Dolphin.exe files were found below {}; configure the exact executable",
                    path.display()
                ));
            }
        }
    }
    matches
        .pop()
        .ok_or_else(|| format!("no Dolphin.exe found below {}", path.display()))
}

pub fn validate_installation(executable: &Path) -> Result<DolphinVersion, String> {
    verify_executable_name(executable)?;
    let executable = executable
        .canonicalize()
        .map_err(|error| format!("cannot resolve Dolphin.exe: {error}"))?;
    let executable_bytes =
        std::fs::read(&executable).map_err(|error| format!("cannot read Dolphin.exe: {error}"))?;
    let version = detect_version(&executable_bytes)
        .map_err(|error| format!("{}: {error}", executable.display()))?;

    let directory = executable
        .parent()
        .ok_or("Dolphin.exe has no containing directory")?;
    let dolphin_tool = directory.join(if cfg!(windows) {
        "DolphinTool.exe"
    } else {
        "dolphin-tool"
    });
    let tool_bytes = std::fs::read(&dolphin_tool).map_err(|error| {
        format!(
            "matching DolphinTool is missing or unreadable at {}: {error}",
            dolphin_tool.display()
        )
    })?;
    validate_binary_pair(&executable_bytes, &tool_bytes)?;

    let codehandler_path = directory.join("Sys").join("codehandler.bin");
    let codehandler = std::fs::read(&codehandler_path).map_err(|error| {
        format!(
            "Dolphin {} codehandler is missing or unreadable at {}: {error}",
            version.as_str(),
            codehandler_path.display()
        )
    })?;
    validate_codehandler(&codehandler, version)?;
    Ok(version)
}

fn validate_binary_pair(executable: &[u8], tool: &[u8]) -> Result<DolphinVersion, String> {
    let version = detect_version(executable)?;
    let tool_version = detect_version(tool).map_err(|error| format!("DolphinTool: {error}"))?;
    if tool_version != version {
        return Err(format!(
            "Dolphin.exe reports {} but its sibling DolphinTool reports {}; refusing a mixed install",
            version.as_str(),
            tool_version.as_str()
        ));
    }
    Ok(version)
}

fn validate_codehandler(bytes: &[u8], version: DolphinVersion) -> Result<(), String> {
    let codehandler_hash = hex(&multigalaxy_patch::sha256(bytes));
    if bytes.len() != 2880 || codehandler_hash != CODEHANDLER_SHA256 {
        return Err(format!(
            "Dolphin {} codehandler mismatch (size={}, sha256={codehandler_hash})",
            version.as_str(),
            bytes.len()
        ));
    }
    Ok(())
}

pub fn require_2609a(version: DolphinVersion, feature: &str) -> Result<(), String> {
    if version != DolphinVersion::V2609a {
        return Err(format!(
            "{feature} is validated only against Dolphin 2609a; configured Dolphin is {}. Use the preserved 2609a development config for this feature.",
            version.as_str()
        ));
    }
    Ok(())
}

fn verify_executable_name(path: &Path) -> Result<(), String> {
    if !path.is_file()
        || !path
            .file_name()
            .is_some_and(|name| name.to_string_lossy().eq_ignore_ascii_case("Dolphin.exe"))
    {
        return Err(format!(
            "configured Dolphin executable must be an existing Dolphin.exe: {}",
            path.display()
        ));
    }
    Ok(())
}

fn detect_version(binary: &[u8]) -> Result<DolphinVersion, String> {
    let has_2609a = binary
        .windows(b"Dolphin/2609a".len())
        .any(|window| window == b"Dolphin/2609a")
        && binary
            .windows(b"Dolphin 2609a".len())
            .any(|window| window == b"Dolphin 2609a");
    let has_2609b = binary
        .windows(b"Dolphin/2609b".len())
        .any(|window| window == b"Dolphin/2609b")
        && binary
            .windows(b"Dolphin 2609b".len())
            .any(|window| window == b"Dolphin 2609b");
    match (has_2609a, has_2609b) {
        (true, false) => Ok(DolphinVersion::V2609a),
        (false, true) => Ok(DolphinVersion::V2609b),
        (true, true) => {
            Err("binary contains conflicting Dolphin 2609a and 2609b build markers".into())
        }
        (false, false) => {
            Err("binary does not contain a recognized Dolphin 2609a/2609b build identity".into())
        }
    }
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn marker(version: &str) -> Vec<u8> {
        format!("prefix Dolphin/{version} middle Dolphin {version} suffix").into_bytes()
    }

    #[test]
    fn detects_only_explicit_2609a_and_2609b_binary_markers() {
        assert_eq!(
            detect_version(&marker("2609a")).unwrap(),
            DolphinVersion::V2609a
        );
        assert_eq!(
            detect_version(&marker("2609b")).unwrap(),
            DolphinVersion::V2609b
        );
        assert!(detect_version(b"Dolphin 2609").is_err());
        assert!(detect_version(b"Dolphin/2609b").is_err());
        assert!(detect_version(&[marker("2609a"), marker("2609b")].concat()).is_err());
    }

    #[test]
    fn requires_dolphin_and_dolphintool_to_report_the_same_supported_version() {
        assert_eq!(
            validate_binary_pair(&marker("2609a"), &marker("2609a")).unwrap(),
            DolphinVersion::V2609a
        );
        assert_eq!(
            validate_binary_pair(&marker("2609b"), &marker("2609b")).unwrap(),
            DolphinVersion::V2609b
        );
        assert!(validate_binary_pair(&marker("2609b"), &marker("2609a"))
            .unwrap_err()
            .contains("refusing a mixed install"));
        assert!(
            validate_binary_pair(&marker("2609b"), b"unrecognized DolphinTool")
                .unwrap_err()
                .contains("DolphinTool")
        );
    }

    #[test]
    fn rejects_unverified_codehandler_bytes() {
        assert!(validate_codehandler(&vec![0; 2880], DolphinVersion::V2609b)
            .unwrap_err()
            .contains("codehandler mismatch"));
        assert!(validate_codehandler(&[0; 8], DolphinVersion::V2609a).is_err());
    }

    #[test]
    fn gates_the_unverified_stock_memory_bridge_to_2609a() {
        assert!(require_2609a(DolphinVersion::V2609a, "MEM1 bridge").is_ok());
        assert!(require_2609a(DolphinVersion::V2609b, "MEM1 bridge")
            .unwrap_err()
            .contains("validated only against Dolphin 2609a"));
    }

    #[test]
    fn discovers_one_nested_executable_and_rejects_ambiguous_installs() {
        let root = std::env::temp_dir().join(format!(
            "mg1-dolphin-discovery-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        let nested = root.join("portable").join("x64");
        std::fs::create_dir_all(&nested).unwrap();
        std::fs::write(nested.join("Dolphin.exe"), b"synthetic").unwrap();
        assert_eq!(
            discover_executable(&root).unwrap(),
            nested.join("Dolphin.exe")
        );
        std::fs::write(root.join("Dolphin.exe"), b"synthetic").unwrap();
        assert!(discover_executable(&root)
            .unwrap_err()
            .contains("multiple Dolphin.exe"));
        assert!(discover_executable(&root.join("missing")).is_err());
        std::fs::remove_dir_all(root).unwrap();
    }
}
