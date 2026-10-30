mod controller_patch;
mod gdb;
mod player_probe;

use multigalaxy_patch::{
    branch_target, changed_ranges, parse_dol, sha256, DiscIdentity, RevisionCatalog, SectionKind,
};
use std::io::Write;
use std::{env, fs, process};

fn verification_offsets(section_len: usize, full_scan: bool) -> Vec<usize> {
    if full_scan {
        return (0..section_len).step_by(1024).collect();
    }
    let mut offsets = vec![0, section_len / 2, section_len.saturating_sub(16)];
    offsets.sort_unstable();
    offsets.dedup();
    offsets
}

fn timestamp_unix_ms() -> u128 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
}

fn dolphin_config(port: u16, mute: bool, enable_cheats: bool) -> String {
    let mut config = format!("[General]\nGDBPort = {port}\n");
    if enable_cheats {
        config.push_str("\n[Core]\nEnableCheats = True\n");
    }
    if mute {
        config.push_str("\n[DSP]\nMuted = True\nVolume = 0\n");
    }
    config
}

fn run() -> Result<(), String> {
    let args: Vec<_> = env::args_os().skip(1).collect();
    if args.len() == 3 && args[0] == "validate-mario-map" {
        let dol = fs::read(&args[1]).map_err(|e| format!("cannot read PAL main.dol: {e}"))?;
        let symbols = fs::read(&args[2]).map_err(|e| format!("cannot read Petari symbols: {e}"))?;
        let map = player_probe::verify_pal_inputs(&dol, &symbols)?;
        println!("PAL RMGP01 Rev 0 DOL and Petari symbol file fingerprints verified.");
        println!(
            "DOL BSS: 0x{:08x}..0x{:08x}",
            map.bss_address,
            map.bss_address + map.bss_size
        );
        println!(
            "SingletonHolder<GameSystem>::sInstance: 0x{:08x}",
            player_probe::GAME_SYSTEM_SINGLETON
        );
        println!("MarioActor::mPosition field offset: +0x0c (static Petari layout; live pointer not sampled by this command).");
        println!("PAL MarioAccess/MarioHolder/SceneObjHolder function bytes and branch targets verified.");
        return Ok(());
    }
    if args.len() == 3 && args[0] == "inspect-allstars" {
        let dol = fs::read(&args[2]).map_err(|e| format!("cannot read PAL main.dol: {e}"))?;
        let prepared = controller_patch::prepare_allstars(std::path::Path::new(&args[1]), &dol)?;
        println!("PAL RMGP01 Rev 0 and the pinned AllStars + L/ZL + R/ZR source files passed static validation.");
        println!(
            "Writes: {}; target DOL text writes: {}; changed DOL words: {}.",
            prepared.total_writes, prepared.dol_text_writes, prepared.dol_changed_words
        );
        for site in &prepared.changed_sites {
            println!("{site}");
        }
        println!("No game image or Dolphin configuration was modified.");
        return Ok(());
    }
    if args.len() >= 5 && args[0] == "launch" {
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
        let mut mute = false;
        let mut allstars = None;
        let mut index = 5;
        while index < args.len() {
            match args[index].to_str() {
                Some("--mute") if !mute => {
                    mute = true;
                    index += 1;
                }
                Some("--allstars") if allstars.is_none() && index + 2 < args.len() => {
                    allstars = Some((
                        std::path::PathBuf::from(&args[index + 1]),
                        std::path::PathBuf::from(&args[index + 2]),
                    ));
                    index += 3;
                }
                _ => {
                    return Err(
                        "launch options: [--mute] [--allstars <patch-folder> <PAL-main.dol>]"
                            .into(),
                    )
                }
            }
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
        let prepared_allstars = if let Some((patch_dir, dol_path)) = &allstars {
            let dol = fs::read(dol_path).map_err(|e| format!("cannot read PAL main.dol: {e}"))?;
            Some(controller_patch::prepare_allstars(patch_dir, &dol)?)
        } else {
            None
        };
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
        fs::write(
            &dolphin_ini,
            dolphin_config(port, mute, prepared_allstars.is_some()),
        )
        .map_err(|e| format!("cannot write isolated Dolphin configuration: {e}"))?;
        if let Some(patch) = &prepared_allstars {
            let settings_dir = user_dir.join("GameSettings");
            fs::create_dir_all(&settings_dir)
                .map_err(|e| format!("cannot create isolated GameSettings directory: {e}"))?;
            fs::write(
                settings_dir.join("RMGP01.ini"),
                controller_patch::allstars_game_settings(patch),
            )
            .map_err(|e| format!("cannot write isolated AllStars GameSettings: {e}"))?;
        }
        let mut command = process::Command::new(&dolphin);
        command
            .arg("-u")
            .arg(&user_dir)
            .arg("-C")
            .arg(format!("Main.General.GDBPort={port}"));
        if mute {
            command.args(["-C", "Main.DSP.Muted=True", "-C", "Main.DSP.Volume=0"]);
        }
        let stdout_log = fs::File::create(user_dir.join("dolphin.stdout.log"))
            .map_err(|e| format!("cannot create isolated Dolphin stdout log: {e}"))?;
        let stderr_log = fs::File::create(user_dir.join("dolphin.stderr.log"))
            .map_err(|e| format!("cannot create isolated Dolphin stderr log: {e}"))?;
        let child = command
            .stdin(process::Stdio::null())
            .stdout(process::Stdio::from(stdout_log))
            .stderr(process::Stdio::from(stderr_log))
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
        println!("Dolphin logs are isolated under {}", user_dir.display());
        if mute {
            println!("Dolphin test/debug audio mute enabled.");
        } else {
            println!("Dolphin audio retains its normal default setting.");
        }
        if let Some(patch) = &prepared_allstars {
            println!("Enabled pinned PAL AllStars + L/ZL + R/ZR in this isolated profile only ({} writes; {} match PAL text).", patch.total_writes, patch.dol_text_writes);
        }
        return Ok(());
    }
    if (args.len() == 4 || args.len() == 5) && args[0] == "probe-mario" {
        let port = args[1]
            .to_str()
            .ok_or("GDB port must be valid UTF-8")?
            .parse::<u16>()
            .map_err(|_| "GDB port must be between 1 and 65535")?;
        if port == 0 {
            return Err("GDB port 0 is not valid".into());
        }
        let interval = if args.len() == 5 {
            args[4]
                .to_str()
                .ok_or("sampling interval must be valid UTF-8")?
                .parse::<u64>()
                .map_err(|_| "sampling interval must be milliseconds")?
        } else {
            200
        };
        if !(50..=5000).contains(&interval) {
            return Err("sampling interval must be between 50 and 5000 ms".into());
        }
        let dol = fs::read(&args[2]).map_err(|e| format!("cannot read PAL main.dol: {e}"))?;
        let symbols = fs::read(&args[3]).map_err(|e| format!("cannot read Petari symbols: {e}"))?;
        let map = player_probe::verify_pal_inputs(&dol, &symbols)?;
        let mut client = gdb::DolphinGdb::connect(
            format!("127.0.0.1:{port}").parse().unwrap(),
            std::time::Duration::from_secs(1),
        )?;
        let disc_id = client.read_memory(0x8000_0000, 6)?;
        if disc_id != b"RMGP01" {
            return Err(format!(
                "expected live PAL RMGP01, found {}",
                String::from_utf8_lossy(&disc_id)
            ));
        }
        println!("Verified PAL Rev 0 DOL and Petari map; live Dolphin identity RMGP01.");
        println!("Persistent GDB session open on port {port}; no memory writes are available.");
        println!("Type START then Enter to sample, STOP to pause, Q to quit. Use a controller to move Mario while this terminal has focus.");
        println!("Position field: MarioActor + 0x0c (three big-endian f32 values); sample interval: {interval} ms.");
        let (tx, rx) = std::sync::mpsc::channel::<String>();
        std::thread::spawn(move || {
            let stdin = std::io::stdin();
            loop {
                let mut line = String::new();
                if stdin.read_line(&mut line).is_err() {
                    break;
                }
                if tx.send(line.trim().to_owned()).is_err() {
                    break;
                }
            }
        });
        let mut sampling = false;
        loop {
            match rx.recv_timeout(std::time::Duration::from_millis(interval)) {
                Ok(command) if command.eq_ignore_ascii_case("start") => {
                    sampling = true;
                    println!("sampling=started");
                }
                Ok(command) if command.eq_ignore_ascii_case("stop") => {
                    sampling = false;
                    println!("sampling=stopped");
                }
                Ok(command)
                    if command.eq_ignore_ascii_case("q")
                        || command.eq_ignore_ascii_case("quit") =>
                {
                    break
                }
                Ok(command) if command.is_empty() => {}
                Ok(command) => println!("unknown command {command:?}; use START, STOP, or Q"),
                Err(std::sync::mpsc::RecvTimeoutError::Timeout) => {}
                Err(std::sync::mpsc::RecvTimeoutError::Disconnected) => break,
            }
            if sampling {
                let timestamp = timestamp_unix_ms();
                match player_probe::sample_position(&mut client, &map) {
                    Ok(Ok(p)) => println!(
                        "unix_ms={timestamp} state=ready MarioActor=0x{:08x} x={:.3} y={:.3} z={:.3}",
                        p.mario_actor, p.x, p.y, p.z
                    ),
                    Ok(Err(waiting)) => println!("unix_ms={timestamp} state=waiting {waiting:?}"),
                    Err(error) => eprintln!("unix_ms={timestamp} state=error {error}"),
                }
                let _ = std::io::stdout().flush();
            }
        }
        println!("sampling=exited; persistent GDB connection closed");
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
    if args.len() == 3 && (args[0] == "verify-loaded" || args[0] == "verify-loaded-full") {
        let full_scan = args[0] == "verify-loaded-full";
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
        let identity = client.read_memory(0x8000_0000, 6)?;
        if identity != b"RMGP01" {
            return Err(format!(
                "expected PAL RMGP01 in Dolphin memory, found {}",
                String::from_utf8_lossy(&identity)
            ));
        }
        println!(
            "Dolphin identity RMGP01 verified; mode: {}",
            if full_scan {
                "full section scan"
            } else {
                "sampled section validation"
            }
        );
        let scan_started = std::time::Instant::now();
        let mut total_differences = 0usize;
        let mut total_runs = 0usize;
        let mut shown_differences = 0usize;
        for section in &info.sections {
            let file_start = section.file_offset as usize;
            let section_len = section.size as usize;
            let offsets = verification_offsets(section_len, full_scan);
            for &offset in &offsets {
                let count = if full_scan {
                    (section_len - offset).min(1024)
                } else {
                    (section_len - offset).min(16)
                };
                let address = section.address + offset as u32;
                let observed = client.read_memory(address, count)?;
                let expected = &dol[file_start + offset..file_start + offset + count];
                let runs = gdb::diff_runs(expected, &observed);
                total_runs += runs.len();
                total_differences += runs.iter().map(|(_, size)| size).sum::<usize>();
                for (relative, size) in runs {
                    if shown_differences < 32 {
                        let at = relative.min(count.saturating_sub(1));
                        println!(
                            "difference {:?}[{}] guest 0x{:08x} length 0x{:x}: expected {:02x}, observed {:02x}",
                            section.kind,
                            section.index,
                            address + at as u32,
                            size,
                            expected[at],
                            observed[at]
                        );
                        shown_differences += 1;
                    }
                }
                if full_scan && offset % (64 * 1024) == 0 {
                    println!(
                        "progress {:?}[{}] +0x{:x}/0x{:x}, {:.1}s",
                        section.kind,
                        section.index,
                        offset,
                        section_len,
                        scan_started.elapsed().as_secs_f32()
                    );
                    let _ = std::io::stdout().flush();
                }
            }
            println!(
                "sampled {:?}[{}] guest 0x{:08x}..0x{:08x} ({} reads, {:.2}s)",
                section.kind,
                section.index,
                section.address,
                section.address + section.size,
                offsets.len(),
                scan_started.elapsed().as_secs_f32()
            );
            let _ = std::io::stdout().flush();
        }
        if total_differences > 0 {
            return Err(format!(
                "loaded DOL comparison found {total_differences} differing bytes across {total_runs} runs (showing at most 32); this does not identify whether they are revision differences or runtime modifications"
            ));
        }
        println!(
            "All {} DOL sections matched in {} validation; candidate SHA-256 {} ({:.2}s)",
            info.sections.len(),
            if full_scan { "full" } else { "sampled" },
            sha256(&dol)
                .iter()
                .map(|b| format!("{b:02x}"))
                .collect::<String>(),
            scan_started.elapsed().as_secs_f32()
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
        return Err("usage: multigalaxy <original.dol> <modified.dol> | multigalaxy identify <disc-header.bin> <main.dol> | multigalaxy launch <Dolphin.exe> <fresh-user-dir> <game.rvz> <gdb-port> [--mute] [--allstars <patch-folder> <PAL-main.dol>] | multigalaxy inspect-allstars <patch-folder> <PAL-main.dol> | multigalaxy readmem <port> <guest-hex-address> <length> | multigalaxy validate-mario-map <PAL-main.dol> <Petari-symbols.txt> | multigalaxy probe-mario <port> <PAL-main.dol> <Petari-symbols.txt> [interval-ms] | multigalaxy verify-loaded <port> <main.dol> | multigalaxy verify-loaded-full <port> <main.dol>".into());
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
    use super::{dolphin_config, parse_dol, verification_offsets};

    #[test]
    fn sampled_validation_reads_start_middle_and_end_without_scanning_section() {
        assert_eq!(verification_offsets(0x5000, false), vec![0, 0x2800, 0x4ff0]);
        assert_eq!(verification_offsets(8, false), vec![0, 4]);
        assert_eq!(verification_offsets(2050, true), vec![0, 1024, 2048]);
    }

    #[test]
    fn test_mute_is_opt_in_and_does_not_change_normal_audio_defaults() {
        let normal = dolphin_config(55173, false, false);
        assert!(normal.contains("GDBPort = 55173"));
        assert!(!normal.contains("[DSP]"));
        let muted = dolphin_config(55174, true, false);
        assert!(muted.contains("Muted = True"));
        assert!(muted.contains("Volume = 0"));
        assert!(dolphin_config(55175, false, true).contains("EnableCheats = True"));
    }

    #[test]
    #[ignore = "requires a live Dolphin GDB stub and an already booted PAL SMG1 session"]
    fn live_dolphin_matches_the_pal_dol_read_only() {
        let port = std::env::var("MG1_GDB_PORT")
            .expect("set MG1_GDB_PORT to the live Dolphin GDB port")
            .parse::<u16>()
            .expect("MG1_GDB_PORT must be a valid TCP port");
        let dol_path = std::env::var("MG1_PAL_DOL")
            .expect("set MG1_PAL_DOL to the verified PAL main.dol path");
        let dol = std::fs::read(&dol_path).expect("read the user-supplied PAL main.dol");
        let info = parse_dol(&dol).expect("parse the user-supplied PAL main.dol");
        let mut client = super::gdb::DolphinGdb::connect(
            format!("127.0.0.1:{port}").parse().unwrap(),
            std::time::Duration::from_secs(5),
        )
        .unwrap();
        let disc_id = client.read_memory(0x8000_0000, 6).unwrap();
        assert_eq!(&disc_id, b"RMGP01");
        for section in &info.sections {
            let file_start = section.file_offset as usize;
            let section_len = section.size as usize;
            for offset in verification_offsets(section_len, false) {
                let count = (section_len - offset).min(16);
                let address = section.address + offset as u32;
                let observed = client.read_memory(address, count).unwrap();
                let expected = &dol[file_start + offset..file_start + offset + count];
                let runs = super::gdb::diff_runs(expected, &observed);
                assert!(
                    runs.is_empty(),
                    "PAL {:?}[{}] differs at guest 0x{:08x}: expected {:02x}, observed {:02x}",
                    section.kind,
                    section.index,
                    address + runs.first().map_or(0, |(start, _)| *start as u32),
                    expected[runs.first().map_or(0, |(start, _)| *start)],
                    observed[runs.first().map_or(0, |(start, _)| *start)]
                );
            }
        }
    }
}
