mod gdb;

use multigalaxy_patch::{
    branch_target, changed_ranges, parse_dol, sha256, DiscIdentity, RevisionCatalog, SectionKind,
};
use std::{env, fs, process};

fn dolphin_config(port: u16, mute: bool) -> String {
    let mut config = format!("[General]\nGDBPort = {port}\n");
    if mute {
        config.push_str("\n[DSP]\nMuted = True\nVolume = 0\n");
    }
    config
}

fn run() -> Result<(), String> {
    let args: Vec<_> = env::args_os().skip(1).collect();
    if (args.len() == 5 || args.len() == 6) && args[0] == "launch" {
        let dolphin = std::path::PathBuf::from(&args[1]);
        let user_dir = std::path::PathBuf::from(&args[2]);
        let game = std::path::PathBuf::from(&args[3]);
        let port = args[4]
            .to_str()
            .ok_or("GDB port must be valid UTF-8")?
            .parse::<u16>()
            .map_err(|_| "GDB port must be between 1 and 65535")?;
        if port == 0 {
            return Err("GDB port 0 is not valid".into());
        }
        let mute = args.len() == 6 && args[5] == "--mute";
        if args.len() == 6 && !mute {
            return Err("the only optional launch flag is --mute (test/debug only)".into());
        }
        if !dolphin.is_file() {
            return Err(format!(
                "Dolphin executable does not exist: {}",
                dolphin.display()
            ));
        }
        if !game.is_file() {
            return Err(format!("game image does not exist: {}", game.display()));
        }
        if user_dir.exists() {
            let mut entries = fs::read_dir(&user_dir)
                .map_err(|e| format!("cannot inspect isolated Dolphin user directory: {e}"))?;
            if entries.next().is_some() {
                return Err("refusing to reuse a non-empty Dolphin user directory; choose a fresh isolated path".into());
            }
        } else {
            fs::create_dir_all(&user_dir)
                .map_err(|e| format!("cannot create isolated Dolphin user directory: {e}"))?;
        }
        let config_dir = user_dir.join("Config");
        fs::create_dir_all(&config_dir)
            .map_err(|e| format!("cannot create isolated Dolphin config directory: {e}"))?;
        let dolphin_ini = config_dir.join("Dolphin.ini");
        fs::write(&dolphin_ini, dolphin_config(port, mute))
            .map_err(|e| format!("cannot write isolated Dolphin configuration: {e}"))?;
        let mut command = process::Command::new(&dolphin);
        command
            .arg("-u")
            .arg(&user_dir)
            .arg("-C")
            .arg(format!("Main.General.GDBPort={port}"));
        if mute {
            command.args(["-C", "Main.DSP.Muted=True", "-C", "Main.DSP.Volume=0"]);
        }
        let child = command
            .arg("-e")
            .arg(&game)
            .spawn()
            .map_err(|e| format!("cannot launch Dolphin: {e}"))?;
        println!(
            "Started Dolphin PID {} with isolated user data at {}",
            child.id(),
            user_dir.display()
        );
        println!("GDB port {port} enabled.");
        if mute {
            println!("Dolphin test/debug audio mute enabled.");
        } else {
            println!("Dolphin audio retains its normal default setting.");
        }
        return Ok(());
    }
    if args.len() == 4 && args[0] == "readmem" {
        let port = args[1]
            .to_str()
            .ok_or("GDB port must be valid UTF-8")?
            .parse::<u16>()
            .map_err(|_| "GDB port must be between 1 and 65535")?;
        let address = u32::from_str_radix(
            args[2]
                .to_str()
                .ok_or("guest address must be UTF-8")?
                .trim_start_matches("0x"),
            16,
        )
        .map_err(|_| "guest address must be a hexadecimal 32-bit address")?;
        let length = args[3]
            .to_str()
            .ok_or("read length must be valid UTF-8")?
            .parse::<usize>()
            .map_err(|_| "read length must be an integer")?;
        let mut client = gdb::DolphinGdb::connect(
            format!("127.0.0.1:{port}").parse().unwrap(),
            std::time::Duration::from_secs(2),
        )?;
        let bytes = client.read_memory(address, length)?;
        println!(
            "{}",
            bytes.iter().map(|b| format!("{b:02x}")).collect::<String>()
        );
        return Ok(());
    }
    if args.len() == 3 && args[0] == "verify-loaded" {
        let port = args[1]
            .to_str()
            .ok_or("GDB port must be valid UTF-8")?
            .parse::<u16>()
            .map_err(|_| "GDB port must be between 1 and 65535")?;
        let dol = fs::read(&args[2]).map_err(|e| format!("cannot read main.dol: {e}"))?;
        let info = parse_dol(&dol).map_err(|e| format!("invalid main.dol: {e}"))?;
        let mut client = gdb::DolphinGdb::connect(
            format!("127.0.0.1:{port}").parse().unwrap(),
            std::time::Duration::from_secs(5),
        )?;
        for section in &info.sections {
            let file_start = section.file_offset as usize;
            let section_len = section.size as usize;
            let mut offset = 0usize;
            while offset < section_len {
                let count = (section_len - offset).min(4096);
                let address = section.address + offset as u32;
                let observed = client.read_memory(address, count)?;
                let expected = &dol[file_start + offset..file_start + offset + count];
                if observed != expected {
                    let mismatch = observed
                        .iter()
                        .zip(expected)
                        .position(|(a, b)| a != b)
                        .unwrap_or(0);
                    return Err(format!(
                        "loaded DOL differs in {:?}[{}] at guest address 0x{:08x}: expected {:02x}, observed {:02x}",
                        section.kind,
                        section.index,
                        address + mismatch as u32,
                        expected[mismatch],
                        observed[mismatch]
                    ));
                }
                offset += count;
            }
            println!(
                "matched {:?}[{}] guest 0x{:08x}..0x{:08x}",
                section.kind,
                section.index,
                section.address,
                section.address + section.size
            );
        }
        println!(
            "Every loaded DOL section matches SHA-256 {}",
            sha256(&dol)
                .iter()
                .map(|b| format!("{b:02x}"))
                .collect::<String>()
        );
        return Ok(());
    }
    if args.len() == 3 && args[0] == "identify" {
        let header = fs::read(&args[1]).map_err(|e| format!("cannot read disc header: {e}"))?;
        let dol = fs::read(&args[2]).map_err(|e| format!("cannot read main.dol: {e}"))?;
        let disc = DiscIdentity::from_header(&header).map_err(|e| e.to_string())?;
        let digest = sha256(&dol)
            .iter()
            .map(|v| format!("{v:02x}"))
            .collect::<String>();
        println!(
            "Disc: {} revision {}; main.dol SHA-256 {digest}",
            disc.game_id, disc.revision
        );
        let catalog = RevisionCatalog::new();
        let map = catalog
            .identify(&disc, &dol)
            .map_err(|e| format!("{e}; no verified PAL build maps are registered yet"))?;
        println!("Verified PAL map: {}", map.provenance);
        return Ok(());
    }
    if args.len() != 2 {
        return Err("usage: multigalaxy <original.dol> <modified.dol> | multigalaxy identify <disc-header.bin> <main.dol> | multigalaxy launch <Dolphin.exe> <fresh-user-dir> <game.rvz> <gdb-port> [--mute] | multigalaxy readmem <port> <guest-hex-address> <length> | multigalaxy verify-loaded <port> <main.dol>".into());
    }
    let a = fs::read(&args[0]).map_err(|e| format!("cannot read original: {e}"))?;
    let b = fs::read(&args[1]).map_err(|e| format!("cannot read modified: {e}"))?;
    let ai = parse_dol(&a).map_err(|e| format!("invalid original DOL: {e}"))?;
    let bi = parse_dol(&b).map_err(|e| format!("invalid modified DOL: {e}"))?;
    let ranges = changed_ranges(&a, &b);
    let hash = |bytes: &[u8]| {
        sha256(bytes)
            .iter()
            .map(|v| format!("{v:02x}"))
            .collect::<String>()
    };
    let common: usize = ranges
        .iter()
        .filter(|r| r.offset < a.len().min(b.len()))
        .map(|r| r.size)
        .sum();
    println!(
        "Original: {} bytes; SHA-256 {}; entry 0x{:08x}",
        a.len(),
        hash(&a),
        ai.entry_point
    );
    println!(
        "Modified: {} bytes; SHA-256 {}; entry 0x{:08x}",
        b.len(),
        hash(&b),
        bi.entry_point
    );
    let shared_runs = ranges
        .iter()
        .filter(|r| r.offset < a.len().min(b.len()))
        .count();
    println!(
        "Changed bytes in shared range: {common}; differing runs: {shared_runs}; size delta: {:+}",
        b.len() as i64 - a.len() as i64
    );
    for s in &bi.sections {
        println!(
            "{:?}[{}]: file 0x{:x}, address 0x{:08x}, size 0x{:x}",
            s.kind, s.index, s.file_offset, s.address, s.size
        );
    }
    for r in ranges {
        println!("change: file 0x{:x}, size 0x{:x}", r.offset, r.size);
    }
    let mut seen = std::collections::BTreeSet::new();
    for r in changed_ranges(&a, &b) {
        if r.offset >= a.len().min(b.len()) {
            continue;
        }
        let off = r.offset & !3;
        if !seen.insert(off) || off + 4 > a.len().min(b.len()) {
            continue;
        }
        if let Some(section) = bi.sections.iter().find(|s| {
            s.kind == SectionKind::Text
                && off >= s.file_offset as usize
                && off + 4 <= (s.file_offset + s.size) as usize
        }) {
            let pc = section.address + (off as u32 - section.file_offset);
            let old = u32::from_be_bytes(a[off..off + 4].try_into().unwrap());
            let new = u32::from_be_bytes(b[off..off + 4].try_into().unwrap());
            if old != new {
                if let Some(target) = branch_target(new, pc) {
                    println!("branch candidate: file 0x{off:x}, PC 0x{pc:08x}, 0x{old:08x} -> 0x{new:08x}, target 0x{target:08x}");
                }
            }
        }
    }
    Ok(())
}
fn main() {
    if let Err(e) = run() {
        eprintln!("error: {e}");
        process::exit(2);
    }
}

#[cfg(test)]
mod tests {
    use super::dolphin_config;

    #[test]
    fn test_mute_is_opt_in_and_does_not_change_normal_audio_defaults() {
        let normal = dolphin_config(55173, false);
        assert!(normal.contains("GDBPort = 55173"));
        assert!(!normal.contains("[DSP]"));
        let muted = dolphin_config(55174, true);
        assert!(muted.contains("Muted = True"));
        assert!(muted.contains("Volume = 0"));
    }
}
