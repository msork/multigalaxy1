//! Explicit, local-only Windows development inputs. This file is git-ignored.
use std::path::PathBuf;

#[derive(Debug, Clone)]
pub struct DevelopmentConfig {
    pub dolphin: PathBuf,
    pub game: PathBuf,
    pub patch_folder: PathBuf,
    pub pal_dol: PathBuf,
    pub pal_symbols: PathBuf,
    pub wii_save: PathBuf,
    pub controller_profile: PathBuf,
    pub fingerprint: String,
}

pub fn parse(text: &str) -> Result<DevelopmentConfig, String> {
    let mut values = std::collections::BTreeMap::new();
    for (line_number, line) in text.lines().enumerate() {
        let line = line.trim().trim_start_matches('\u{feff}');
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let (key, value) = line.split_once('=').ok_or_else(|| {
            format!(
                "development config line {} must be key = 'value'",
                line_number + 1
            )
        })?;
        let key = key.trim();
        let raw = value.trim();
        let unquoted = raw
            .strip_prefix('\'')
            .and_then(|v| v.strip_suffix('\''))
            .or_else(|| raw.strip_prefix('"').and_then(|v| v.strip_suffix('"')))
            .ok_or_else(|| {
                format!(
                    "development config line {} needs a quoted value",
                    line_number + 1
                )
            })?;
        if !matches!(
            key,
            "dolphin"
                | "game"
                | "patch_folder"
                | "pal_dol"
                | "pal_symbols"
                | "wii_save"
                | "controller_profile"
        ) {
            return Err(format!("unknown development config key {key:?}"));
        }
        if values.insert(key, unquoted.to_owned()).is_some() {
            return Err(format!("duplicate development config key {key:?}"));
        }
    }
    let take = |key: &'static str| {
        values
            .get(key)
            .cloned()
            .filter(|v| !v.trim().is_empty())
            .map(PathBuf::from)
            .ok_or_else(|| format!("development config is missing {key:?}"))
    };
    Ok(DevelopmentConfig {
        dolphin: take("dolphin")?,
        game: take("game")?,
        patch_folder: take("patch_folder")?,
        pal_dol: take("pal_dol")?,
        pal_symbols: take("pal_symbols")?,
        wii_save: take("wii_save")?,
        controller_profile: take("controller_profile")?,
        fingerprint: crate::sha256(text.as_bytes())
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    const CONFIG: &str = "dolphin = 'D:\\emu\\Dolphin.exe'\ngame = 'D:\\game.rvz'\npatch_folder = 'D:\\patches'\npal_dol = 'D:\\main.dol'\npal_symbols = 'D:\\symbols.txt'\nwii_save = 'D:\\save.bin'\ncontroller_profile = 'D:\\pad.ini'\n";

    #[test]
    fn parses_quoted_local_paths_without_platform_constants() {
        let config = parse(CONFIG).unwrap();
        assert_eq!(config.dolphin, PathBuf::from("D:\\emu\\Dolphin.exe"));
        assert_eq!(config.wii_save, PathBuf::from("D:\\save.bin"));
        assert_eq!(config.pal_symbols, PathBuf::from("D:\\symbols.txt"));
        assert_eq!(config.controller_profile, PathBuf::from("D:\\pad.ini"));
        assert_eq!(config.fingerprint.len(), 64);
    }

    #[test]
    fn rejects_missing_duplicate_and_unknown_keys() {
        assert!(parse("dolphin = 'x'\n").unwrap_err().contains("missing"));
        assert!(parse(&format!("{CONFIG}game = 'twice'\n"))
            .unwrap_err()
            .contains("duplicate"));
        assert!(parse(&format!("{CONFIG}save = 'x'\n"))
            .unwrap_err()
            .contains("unknown"));
    }
}
