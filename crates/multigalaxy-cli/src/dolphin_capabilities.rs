//! Capability-oriented inspection of a user's official Dolphin installation.
//!
//! General launch and ISO-validation paths deliberately do not pin Dolphin
//! release strings or executable hashes. Optional memory-mailbox support has
//! a separate, stricter codehandler capability check and runtime validation.

use std::path::{Path, PathBuf};

const MAILBOX_CODEHANDLER_SHA256: &str =
    "2b2ff0716aeb9b806cc61181e559041312957f25f04c435bd7da80a78917b33b";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct InstallCapabilities {
    pub executable: PathBuf,
    pub dolphin_tool: Option<PathBuf>,
    pub gecko_codehandler: Option<PathBuf>,
    pub gecko_codehandler_bytes: Option<u64>,
}

impl InstallCapabilities {
    pub fn has_iso_header_tool(&self) -> bool {
        self.dolphin_tool.is_some()
    }

    pub fn has_gecko_handler_file(&self) -> bool {
        self.gecko_codehandler
            .as_ref()
            .is_some_and(|_| self.gecko_codehandler_bytes.unwrap_or(0) > 0)
    }
}

/// Resolve a configured install path (executable or directory) to exactly one
/// Dolphin executable. Recursive discovery is bounded to avoid traversing user data.
pub fn discover_executable(path: &Path) -> Result<PathBuf, String> {
    if path.is_file() {
        return validate_executable(path).map(|_| path.to_path_buf());
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
            if kind.is_file() && is_dolphin_executable_name(&entry.file_name().to_string_lossy()) {
                matches.push(entry.path());
            } else if kind.is_dir() && depth < 3 {
                pending.push((entry.path(), depth + 1));
            }
            if matches.len() > 1 {
                return Err(format!(
                    "multiple Dolphin executables were found below {}; configure the exact executable",
                    path.display()
                ));
            }
        }
    }
    matches
        .pop()
        .ok_or_else(|| format!("no Dolphin executable found below {}", path.display()))
}

pub fn validate_executable(executable: &Path) -> Result<PathBuf, String> {
    if !executable.is_file() {
        return Err(format!(
            "configured Dolphin executable is missing or not a regular file: {}",
            executable.display()
        ));
    }
    if !executable
        .file_name()
        .is_some_and(|name| is_dolphin_executable_name(&name.to_string_lossy()))
    {
        return Err(format!(
            "configured emulator path does not name the Dolphin executable for this platform: {}",
            executable.display()
        ));
    }
    let resolved = executable
        .canonicalize()
        .map_err(|error| format!("cannot resolve Dolphin executable: {error}"))?;
    let metadata = std::fs::metadata(&resolved)
        .map_err(|error| format!("cannot inspect Dolphin executable: {error}"))?;
    if metadata.len() == 0 {
        return Err("Dolphin executable is empty".into());
    }
    Ok(resolved)
}

/// Report static capabilities that can be checked without starting Dolphin.
/// Presence is only a prerequisite; the launcher still checks PAL identity via
/// DolphinTool and validates requested runtime capabilities when they are used.
pub fn inspect_installation(executable: &Path) -> Result<InstallCapabilities, String> {
    let executable = validate_executable(executable)?;
    let directory = executable
        .parent()
        .ok_or("Dolphin executable has no containing directory")?;
    let tool = directory.join(if cfg!(windows) {
        "DolphinTool.exe"
    } else {
        "dolphin-tool"
    });
    let dolphin_tool = nonempty_file(&tool).then_some(tool);

    let handler = directory.join("Sys").join("codehandler.bin");
    let handler_bytes = std::fs::metadata(&handler)
        .ok()
        .filter(|metadata| metadata.is_file() && metadata.len() > 0)
        .map(|metadata| metadata.len());
    let gecko_codehandler = handler_bytes.map(|_| handler);

    Ok(InstallCapabilities {
        executable,
        dolphin_tool,
        gecko_codehandler,
        gecko_codehandler_bytes: handler_bytes,
    })
}

/// The current mailbox payload depends on a verified Gecko codehandler cave.
/// This resource fingerprint is a feature-specific capability, not an emulator
/// release or Dolphin.exe identity requirement. Runtime map/game/mailbox checks
/// are still mandatory before the bridge reads or writes emulated memory.
pub fn validate_mailbox_codehandler(executable: &Path) -> Result<(), String> {
    let executable = validate_executable(executable)?;
    let handler = executable
        .parent()
        .ok_or("Dolphin executable has no containing directory")?
        .join("Sys")
        .join("codehandler.bin");
    let bytes = std::fs::read(&handler).map_err(|error| {
        format!(
            "cannot read Gecko codehandler needed by the mailbox capability at {}: {error}",
            handler.display()
        )
    })?;
    let digest = hex(&multigalaxy_patch::sha256(&bytes));
    if bytes.len() != crate::gecko_bridge::CODEHANDLER_BYTES || digest != MAILBOX_CODEHANDLER_SHA256
    {
        return Err(format!(
            "mailbox bridge is unavailable for this Gecko codehandler (size={}, sha256={digest}); basic Dolphin launch remains available",
            bytes.len()
        ));
    }
    Ok(())
}

fn nonempty_file(path: &Path) -> bool {
    std::fs::metadata(path)
        .map(|metadata| metadata.is_file() && metadata.len() > 0)
        .unwrap_or(false)
}

fn is_dolphin_executable_name(name: &str) -> bool {
    if cfg!(windows) {
        name.eq_ignore_ascii_case("Dolphin.exe")
    } else if cfg!(target_os = "macos") {
        name == "Dolphin"
    } else {
        name == "dolphin-emu"
    }
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp_dir() -> PathBuf {
        std::env::temp_dir().join(format!(
            "mg1-dolphin-capabilities-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ))
    }

    #[test]
    fn discovery_does_not_require_release_markers_or_executable_hashes() {
        let root = temp_dir();
        std::fs::create_dir_all(root.join("portable")).unwrap();
        let exe = root.join("portable").join(if cfg!(windows) {
            "Dolphin.exe"
        } else if cfg!(target_os = "macos") {
            "Dolphin"
        } else {
            "dolphin-emu"
        });
        std::fs::write(&exe, b"arbitrary nonempty executable fixture").unwrap();
        let discovered = discover_executable(&root).unwrap();
        assert_eq!(discovered, exe);
        assert!(inspect_installation(&discovered).is_ok());
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn reports_optional_tools_without_claiming_runtime_capability() {
        let root = temp_dir();
        std::fs::create_dir_all(root.join("Sys")).unwrap();
        let exe = root.join(if cfg!(windows) {
            "Dolphin.exe"
        } else if cfg!(target_os = "macos") {
            "Dolphin"
        } else {
            "dolphin-emu"
        });
        std::fs::write(&exe, b"exe fixture").unwrap();
        let baseline = inspect_installation(&exe).unwrap();
        assert!(!baseline.has_iso_header_tool());
        assert!(!baseline.has_gecko_handler_file());

        let tool = root.join(if cfg!(windows) {
            "DolphinTool.exe"
        } else {
            "dolphin-tool"
        });
        std::fs::write(&tool, b"tool fixture").unwrap();
        std::fs::write(root.join("Sys").join("codehandler.bin"), b"handler").unwrap();
        let with_optional_files = inspect_installation(&exe).unwrap();
        assert!(with_optional_files.has_iso_header_tool());
        assert!(with_optional_files.has_gecko_handler_file());
        assert!(validate_mailbox_codehandler(&exe).is_err());
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn discovery_rejects_ambiguous_and_empty_executables() {
        let root = temp_dir();
        std::fs::create_dir_all(root.join("nested")).unwrap();
        let name = if cfg!(windows) {
            "Dolphin.exe"
        } else if cfg!(target_os = "macos") {
            "Dolphin"
        } else {
            "dolphin-emu"
        };
        std::fs::write(root.join(name), b"one").unwrap();
        std::fs::write(root.join("nested").join(name), b"two").unwrap();
        assert!(discover_executable(&root)
            .unwrap_err()
            .contains("multiple Dolphin executables"));
        assert!(validate_executable(&root.join("missing")).is_err());
        std::fs::remove_dir_all(root).unwrap();
    }
}
