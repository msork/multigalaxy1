mod controller_patch;
mod dev_config;
mod gdb;
mod player_probe;
mod startup;

use multigalaxy_patch::{
    branch_target, changed_ranges, parse_dol, sha256, DiscIdentity, RevisionCatalog, SectionKind,
};
use std::io::Write;
use std::net::TcpListener;
use std::time::{Duration, Instant};
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

fn dolphin_config(port: Option<u16>, mute: bool, enable_cheats: bool, development: bool) -> String {
    let mut config = String::new();
    if let Some(port) = port {
        config.push_str(&format!("[General]\nGDBPort = {port}\n"));
    }
    if enable_cheats {
        config.push_str("\n[Core]\nEnableCheats = True\n");
    }
    if development {
        if !config.contains("[Core]") {
            config.push_str("\n[Core]\n");
        }
        config.push_str("GFXBackend = D3D12\n");
    }
    if mute {
        config.push_str("\n[DSP]\nMuted = True\nVolume = 0\n");
    }
    config
}

fn validate_dev_inputs(config: &dev_config::DevelopmentConfig) -> Result<(), String> {
    for (label, path) in [
        ("Dolphin executable", &config.dolphin),
        ("PAL game image", &config.game),
        ("AllStars patch folder", &config.patch_folder),
        ("PAL main.dol", &config.pal_dol),
        ("Petari symbol map", &config.pal_symbols),
        ("Wii save export", &config.wii_save),
        ("controller profile", &config.controller_profile),
    ] {
        if !path.exists() {
            return Err(format!(
                "required development {label} is missing: {}",
                path.display()
            ));
        }
    }
    if !config.dolphin.is_file()
        || !config.game.is_file()
        || !config.pal_dol.is_file()
        || !config.pal_symbols.is_file()
        || !config.wii_save.is_file()
        || !config.controller_profile.is_file()
    {
        return Err("a required development input is not a regular file".into());
    }
    if !config.patch_folder.is_dir() {
        return Err("development AllStars patch path is not a directory".into());
    }
    verify_pal_game_image(&config.dolphin, &config.game)?;
    Ok(())
}

fn parse_dolphintool_header(output: &str) -> Result<(), String> {
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
        return Err(format!(
            "MultiGalaxy1 supports only PAL RMGP01 revision 0; image reports ID={game_id}, region={region}, revision={revision}"
        ));
    }
    Ok(())
}

fn verify_pal_game_image(dolphin: &std::path::Path, game: &std::path::Path) -> Result<(), String> {
    let dolphin = dolphin
        .canonicalize()
        .map_err(|e| format!("cannot resolve Dolphin executable path: {e}"))?;
    let game = game
        .canonicalize()
        .map_err(|e| format!("cannot resolve game image path: {e}"))?;
    let tool_name = if cfg!(windows) {
        "DolphinTool.exe"
    } else {
        "dolphin-tool"
    };
    let tool = dolphin
        .parent()
        .unwrap_or_else(|| std::path::Path::new("."))
        .join(tool_name);
    if !tool.is_file() {
        return Err(format!(
            "cannot verify PAL game revision: DolphinTool is missing beside Dolphin at {}",
            tool.display()
        ));
    }
    let mut child = process::Command::new(&tool)
        .current_dir(
            dolphin
                .parent()
                .unwrap_or_else(|| std::path::Path::new(".")),
        )
        .arg("header")
        .arg("-i")
        .arg(game)
        .stdout(process::Stdio::piped())
        .stderr(process::Stdio::piped())
        .spawn()
        .map_err(|e| format!("cannot start DolphinTool for PAL image validation: {e}"))?;
    let deadline = Instant::now() + Duration::from_secs(10);
    loop {
        match child.try_wait() {
            Ok(Some(status)) => {
                let output = child
                    .wait_with_output()
                    .map_err(|e| format!("cannot collect DolphinTool header output: {e}"))?;
                if !status.success() {
                    return Err(format!(
                        "DolphinTool could not inspect the game image ({status}): {}",
                        String::from_utf8_lossy(&output.stderr).trim()
                    ));
                }
                return parse_dolphintool_header(&String::from_utf8_lossy(&output.stdout));
            }
            Ok(None) if Instant::now() < deadline => {
                std::thread::sleep(Duration::from_millis(50));
            }
            Ok(None) => {
                let _ = child.kill();
                let _ = child.wait();
                return Err("DolphinTool PAL image validation exceeded 10 seconds; stopped the validation process".into());
            }
            Err(error) => return Err(format!("cannot inspect DolphinTool process: {error}")),
        }
    }
}

fn configure_isolated_profile(
    user_dir: &std::path::Path,
    patch: &controller_patch::PreparedPatch,
    controller_profile: Option<&std::path::Path>,
    development: bool,
    gdb_port: Option<u16>,
    mute: bool,
) -> Result<(), String> {
    let config_dir = user_dir.join("Config");
    fs::create_dir_all(&config_dir)
        .map_err(|e| format!("cannot create isolated Dolphin config directory: {e}"))?;
    fs::write(
        config_dir.join("Dolphin.ini"),
        dolphin_config(gdb_port, mute, true, development),
    )
    .map_err(|e| format!("cannot write isolated Dolphin configuration: {e}"))?;
    let settings_dir = user_dir.join("GameSettings");
    fs::create_dir_all(&settings_dir)
        .map_err(|e| format!("cannot create isolated GameSettings directory: {e}"))?;
    let mut settings = controller_patch::allstars_game_settings(patch).to_owned();
    if let Some(profile_path) = controller_profile {
        let profile_name = "Pro Con to Classic Con";
        let profile_dir = config_dir.join("Profiles").join("Wiimote");
        fs::create_dir_all(&profile_dir)
            .map_err(|e| format!("cannot create isolated Wii Remote profile directory: {e}"))?;
        let bytes = fs::read(profile_path)
            .map_err(|e| format!("cannot read requested controller profile: {e}"))?;
        let text = std::str::from_utf8(&bytes)
            .map_err(|e| format!("controller profile is not UTF-8: {e}"))?;
        if !text.contains("[Profile]") || !text.contains("Extension = Classic") {
            return Err("controller profile is not a Dolphin Classic Controller profile".into());
        }
        fs::write(profile_dir.join(format!("{profile_name}.ini")), &bytes).map_err(|e| {
            format!("cannot install controller profile into isolated Dolphin config: {e}")
        })?;
        settings.push_str(&format!(
            "\n[Controls]\nWiimoteSource0 = 1\nWiimoteProfile1 = {profile_name}\n"
        ));
    }
    fs::write(settings_dir.join("RMGP01.ini"), settings)
        .map_err(|e| format!("cannot write isolated PAL game settings: {e}"))?;
    Ok(())
}

fn port_is_available(port: u16) -> bool {
    TcpListener::bind(("127.0.0.1", port)).is_ok()
}

fn log_tail(path: &std::path::Path) -> String {
    fs::read(path)
        .map(|bytes| {
            let start = bytes.len().saturating_sub(4096);
            String::from_utf8_lossy(&bytes[start..]).trim().to_owned()
        })
        .unwrap_or_default()
}

fn absolute_path(path: &std::path::Path) -> Result<std::path::PathBuf, String> {
    if path.is_absolute() {
        Ok(path.to_path_buf())
    } else {
        env::current_dir()
            .map(|cwd| cwd.join(path))
            .map_err(|e| format!("cannot resolve current directory: {e}"))
    }
}

fn powershell_literal(value: &std::path::Path) -> String {
    let value = value.to_string_lossy();
    let value = value.strip_prefix("\\\\?\\").unwrap_or(&value);
    format!("'{}'", value.replace('\'', "''"))
}

fn dev_gui_handoff(
    config_arg: &std::ffi::OsStr,
    profile: &std::path::Path,
    command: &str,
    arguments: &[String],
) -> Result<(), String> {
    let Some(context) = startup::desktop_context() else {
        return Ok(());
    };
    if context.is_interactive_default() {
        return Ok(());
    }
    let config_path = absolute_path(std::path::Path::new(config_arg))?;
    let profile_path = absolute_path(profile)?;
    let executable = env::current_exe()
        .map_err(|e| format!("cannot resolve launcher executable for desktop handoff: {e}"))?;
    let mut command_line = format!(
        "& {} {} {} {}",
        powershell_literal(&executable),
        command,
        powershell_literal(&config_path),
        powershell_literal(&profile_path)
    );
    for argument in arguments {
        command_line.push(' ');
        command_line.push_str(&powershell_literal(std::path::Path::new(argument)));
    }
    Err(format!(
        "GUI launch refused: the launcher is not on the interactive WinSta0\\Default desktop, so a child Dolphin would not appear in your Alt+Tab/Task View. No Dolphin process was started. launcher_pid={} session={} active_console_session={} user={} station={} desktop={} input_desktop={}. Run this exact command from a PowerShell window on the interactive Windows desktop:\n{}",
        context.pid,
        context.session_id,
        context.active_console_session_id,
        context.user_name,
        context.station,
        context.desktop,
        context.input_desktop,
        command_line
    ))
}

fn log_process_windows(pid: u32) {
    let session_id = startup::process_session_id(pid)
        .map(|id| id.to_string())
        .unwrap_or_else(|| "unknown".into());
    let station = startup::desktop_context()
        .map(|context| context.station)
        .unwrap_or_else(|| "unsupported".into());
    println!("window diagnostics for pid={pid} session={session_id} station={station:?}:");
    for window in startup::process_windows(pid) {
        println!(
            "window hwnd=0x{:x} thread={} desktop={:?} visible={} hung={} style=0x{:x} exstyle=0x{:x} parent=0x{:x} owner=0x{:x} title={:?}",
            window.hwnd,
            window.thread_id,
            window.desktop,
            window.visible,
            window.hung,
            window.style,
            window.extended_style,
            window.parent,
            window.owner,
            window.title
        );
    }
}

fn wait_for_dolphin_window(
    child: &mut process::Child,
    stdout_path: &std::path::Path,
    stderr_path: &std::path::Path,
) -> Result<startup::WindowState, String> {
    const STARTUP_TIMEOUT: Duration = Duration::from_secs(15);
    let deadline = Instant::now() + STARTUP_TIMEOUT;
    if !startup::window_check_supported() {
        std::thread::sleep(Duration::from_secs(2));
        if child
            .try_wait()
            .map_err(|e| format!("cannot inspect Dolphin process state: {e}"))?
            .is_none()
        {
            return Ok(startup::WindowState {
                hwnd: 0,
                thread_id: 0,
                title: "process alive (window check unsupported on this platform)".into(),
                visible: false,
                hung: false,
                style: 0,
                extended_style: 0,
                parent: 0,
                owner: 0,
                desktop: "unsupported".into(),
            });
        }
    }
    loop {
        if let Some(status) = child
            .try_wait()
            .map_err(|e| format!("cannot inspect Dolphin process state: {e}"))?
        {
            return Err(format!(
                "Dolphin exited during startup ({status}). stdout: {}; stderr: {}",
                log_tail(stdout_path),
                log_tail(stderr_path)
            ));
        }
        let last_windows = startup::visible_windows(child.id());
        if let Some(window) = last_windows.iter().find(|window| {
            !window.hung && startup::is_valid_visible_for_pid(window.hwnd, child.id())
        }) {
            if !startup::activate(window.hwnd) {
                eprintln!(
                    "Dolphin created visible window HWND=0x{:x}, but Windows did not allow foreground activation; title={:?}",
                    window.hwnd, window.title
                );
            }
            println!(
                "Dolphin visible window verified: HWND=0x{:x}, title={:?}",
                window.hwnd, window.title
            );
            return Ok(window.clone());
        }
        if Instant::now() >= deadline {
            let window_state = if last_windows.is_empty() {
                "no visible Dolphin window was created".to_owned()
            } else {
                format!("Dolphin windows are not responding: {last_windows:?}")
            };
            return Err(format!(
                "Dolphin stayed alive but no valid visible window handle was available within 15 seconds ({window_state}). The test process was left open as PID {}. stdout: {}; stderr: {}",
                child.id(), log_tail(stdout_path), log_tail(stderr_path)
            ));
        }
        std::thread::sleep(Duration::from_millis(250));
    }
}

fn spawn_game(
    dolphin: &std::path::Path,
    user_dir: &std::path::Path,
    game: &std::path::Path,
    gdb_enabled: bool,
    port: u16,
    mute: bool,
    patch: &controller_patch::PreparedPatch,
) -> Result<process::Child, String> {
    if let Some(context) = startup::desktop_context() {
        println!(
            "launcher desktop: pid={} session={} active_console_session={} user={} station={:?} desktop={:?} input_desktop={:?}",
            context.pid,
            context.session_id,
            context.active_console_session_id,
            context.user_name,
            context.station,
            context.desktop,
            context.input_desktop
        );
        if !context.is_interactive_default() {
            return Err(format!(
                "refusing to start GUI Dolphin from non-interactive desktop: session={} active_console_session={} station={:?} desktop={:?} input_desktop={:?}",
                context.session_id,
                context.active_console_session_id,
                context.station,
                context.desktop,
                context.input_desktop
            ));
        }
    }
    let dolphin = dolphin
        .canonicalize()
        .map_err(|e| format!("cannot resolve Dolphin executable path: {e}"))?;
    let user_dir = user_dir
        .canonicalize()
        .map_err(|e| format!("cannot resolve isolated user directory: {e}"))?;
    let game = game
        .canonicalize()
        .map_err(|e| format!("cannot resolve game image path: {e}"))?;
    let stdout_path = user_dir.join("dolphin.stdout.log");
    let stderr_path = user_dir.join("dolphin.stderr.log");
    let mut child = process::Command::new(&dolphin)
        .current_dir(
            dolphin
                .parent()
                .unwrap_or_else(|| std::path::Path::new(".")),
        )
        .arg("-u")
        .arg(&user_dir)
        .stdin(process::Stdio::null())
        .stdout(process::Stdio::from(
            fs::File::create(&stdout_path)
                .map_err(|e| format!("cannot create isolated Dolphin stdout log: {e}"))?,
        ))
        .stderr(process::Stdio::from(
            fs::File::create(&stderr_path)
                .map_err(|e| format!("cannot create isolated Dolphin stderr log: {e}"))?,
        ))
        .arg("-e")
        .arg(game)
        .spawn()
        .map_err(|e| format!("cannot launch Dolphin: {e}"))?;
    let window = wait_for_dolphin_window(&mut child, &stdout_path, &stderr_path)?;
    println!(
        "Started Dolphin window HWND=0x{:x} {:?}, PID {} session {}, with isolated user data at {}",
        window.hwnd,
        window.title,
        child.id(),
        startup::process_session_id(child.id())
            .map(|id| id.to_string())
            .unwrap_or_else(|| "unknown".into()),
        user_dir.display()
    );
    log_process_windows(child.id());
    println!("Mandatory pinned PAL AllStars + both shoulder swaps are enabled in this isolated profile ({} writes).", patch.total_writes);
    if gdb_enabled {
        println!("debug stage=window-ready; GDB enabled on port {port}; emulation is expected to be paused until the launcher attaches.");
    } else {
        println!("GDB is disabled; emulation starts normally.");
    }
    if mute {
        println!("Dolphin test/debug audio mute enabled.");
    }
    println!("Dolphin logs: {}", user_dir.display());
    Ok(child)
}

fn connect_gdb_until_ready(
    child: &mut process::Child,
    port: u16,
    timeout: Duration,
) -> Result<gdb::DolphinGdb, String> {
    let deadline = Instant::now() + timeout;
    let address = format!("127.0.0.1:{port}").parse().unwrap();
    let mut last_error;
    loop {
        if let Some(status) = child
            .try_wait()
            .map_err(|e| format!("cannot inspect Dolphin while waiting for GDB: {e}"))?
        {
            return Err(format!(
                "Dolphin exited before its GDB stub was ready ({status})"
            ));
        }
        match gdb::DolphinGdb::connect(address, Duration::from_millis(500)) {
            Ok(client) => return Ok(client),
            Err(error) => last_error = error,
        }
        if Instant::now() >= deadline {
            return Err(format!(
                "Dolphin GDB stub did not accept the automatic debug connection within {} seconds: {last_error}",
                timeout.as_secs()
            ));
        }
        std::thread::sleep(Duration::from_millis(100));
    }
}

fn run_interactive_sampler(
    mut client: gdb::DolphinGdb,
    map: &player_probe::VerifiedPalMap,
    port: u16,
    interval: u64,
    start_sampling: bool,
) -> Result<(), String> {
    println!("Persistent GDB session open on port {port}; emulation continues while this read-only sampler polls memory.");
    println!(
        "Sampling is {}. Type START, STOP, or Q. Use your controller to move Mario.",
        if start_sampling {
            "already active"
        } else {
            "stopped"
        }
    );
    println!(
        "Position field: MarioActor + 0x0c (three big-endian f32 values); interval: {interval} ms."
    );
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
    let mut sampling = start_sampling;
    loop {
        match rx.recv_timeout(Duration::from_millis(interval)) {
            Ok(command) if command.eq_ignore_ascii_case("start") => {
                sampling = true;
                println!("sampling=started");
            }
            Ok(command) if command.eq_ignore_ascii_case("stop") => {
                sampling = false;
                println!("sampling=stopped");
            }
            Ok(command)
                if command.eq_ignore_ascii_case("q") || command.eq_ignore_ascii_case("quit") =>
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
            match player_probe::sample_position(&mut client, map) {
                Ok(Ok(p)) => println!(
                    "unix_ms={timestamp} state=ready MarioActor=0x{:08x} x={:.3} y={:.3} z={:.3}",
                    p.mario_actor, p.x, p.y, p.z
                ),
                Ok(Err(waiting)) => println!("unix_ms={timestamp} state=waiting {waiting:?}"),
                Err(error) => {
                    eprintln!("unix_ms={timestamp} state=error {error}");
                    eprintln!("GDB session is no longer usable. Dolphin accepts one GDB connection per process; close this sampler and restart Dolphin before reconnecting.");
                    break;
                }
            }
            let _ = std::io::stdout().flush();
        }
    }
    println!("sampling=exited; persistent GDB connection closed");
    Ok(())
}

fn run() -> Result<(), String> {
    let args: Vec<_> = env::args_os().skip(1).collect();
    if args.len() == 3 && args[0] == "dev-setup" {
        dev_gui_handoff(&args[1], std::path::Path::new(&args[2]), "dev-setup", &[])?;
        let config_text = fs::read_to_string(&args[1])
            .map_err(|e| format!("cannot read local development config: {e}"))?;
        let config = dev_config::parse(&config_text)?;
        validate_dev_inputs(&config)?;
        let dol =
            fs::read(&config.pal_dol).map_err(|e| format!("cannot read PAL main.dol: {e}"))?;
        let patch = controller_patch::prepare_allstars(&config.patch_folder, &dol)?;
        let user_dir = std::path::PathBuf::from(&args[2]);
        if user_dir.exists() {
            let has_entries = fs::read_dir(&user_dir)
                .map_err(|e| format!("cannot inspect isolated profile: {e}"))?
                .next()
                .is_some();
            if has_entries {
                let marker = fs::read_to_string(user_dir.join(".mg1-development-profile"))
                    .map_err(|_| {
                        "dev-setup refuses a non-empty directory not prepared by MultiGalaxy1"
                            .to_owned()
                    })?;
                if marker.trim() != config.fingerprint {
                    return Err("development profile was prepared from a different local config; choose a fresh isolated directory".into());
                }
            }
        }
        fs::create_dir_all(&user_dir)
            .map_err(|e| format!("cannot create isolated development profile: {e}"))?;
        let user_dir = user_dir
            .canonicalize()
            .map_err(|e| format!("cannot resolve isolated development profile: {e}"))?;
        let dolphin = config
            .dolphin
            .canonicalize()
            .map_err(|e| format!("cannot resolve Dolphin executable path: {e}"))?;
        configure_isolated_profile(
            &user_dir,
            &patch,
            Some(&config.controller_profile),
            true,
            None,
            false,
        )?;
        fs::write(
            user_dir.join(".mg1-development-profile"),
            &config.fingerprint,
        )
        .map_err(|e| format!("cannot write development profile marker: {e}"))?;
        let stdout_path = user_dir.join("dolphin.stdout.log");
        let stderr_path = user_dir.join("dolphin.stderr.log");
        let mut child = process::Command::new(&dolphin)
            .current_dir(
                dolphin
                    .parent()
                    .unwrap_or_else(|| std::path::Path::new(".")),
            )
            .arg("-u")
            .arg(&user_dir)
            .stdin(process::Stdio::null())
            .stdout(process::Stdio::from(
                fs::File::create(&stdout_path)
                    .map_err(|e| format!("cannot create isolated Dolphin stdout log: {e}"))?,
            ))
            .stderr(process::Stdio::from(
                fs::File::create(&stderr_path)
                    .map_err(|e| format!("cannot create isolated Dolphin stderr log: {e}"))?,
            ))
            .spawn()
            .map_err(|e| format!("cannot launch Dolphin setup window: {e}"))?;
        let window = wait_for_dolphin_window(&mut child, &stdout_path, &stderr_path)?;
        log_process_windows(child.id());
        println!("Started Dolphin setup window {:?}, PID {}. Import the Wii save using Tools > Import Wii Save, select {}, then close Dolphin before running dev-launch.", window.title, child.id(), config.wii_save.display());
        println!("This uses Dolphin's own importer; the source save and regular Dolphin profile are untouched. D3D12, the copied controller profile, and AllStars are isolated under {}.", user_dir.display());
        return Ok(());
    }
    if args.len() >= 4 && args[0] == "dev-launch" {
        let config_text = fs::read_to_string(&args[1])
            .map_err(|e| format!("cannot read local development config: {e}"))?;
        let config = dev_config::parse(&config_text)?;
        validate_dev_inputs(&config)?;
        let user_dir = std::path::PathBuf::from(&args[2])
            .canonicalize()
            .map_err(|_| "isolated profile path does not exist; run dev-setup first".to_owned())?;
        let marker = fs::read_to_string(user_dir.join(".mg1-development-profile"))
            .map_err(|_| "isolated profile is not prepared; run dev-setup first".to_owned())?;
        if marker.trim() != config.fingerprint {
            return Err("development config differs from the config used by dev-setup; prepare a fresh profile".into());
        }
        let port = args[3]
            .to_str()
            .ok_or("GDB port must be valid UTF-8")?
            .parse::<u16>()
            .map_err(|_| "GDB port must be between 1 and 65535")?;
        let mut debug = false;
        let mut mute = true;
        for option in &args[4..] {
            match option.to_str() {
                Some("--debug") if !debug => debug = true,
                Some("--unmute") if mute => mute = false,
                _ => return Err("dev-launch options: [--debug] [--unmute]".into()),
            }
        }
        if debug && (port == 0 || !port_is_available(port)) {
            return Err(format!(
                "debug GDB port {port} is invalid or already in use"
            ));
        }
        let mut handoff_arguments = vec![port.to_string()];
        handoff_arguments.extend(
            args[4..]
                .iter()
                .map(|argument| argument.to_string_lossy().into_owned()),
        );
        dev_gui_handoff(&args[1], &user_dir, "dev-launch", &handoff_arguments)?;
        let dol =
            fs::read(&config.pal_dol).map_err(|e| format!("cannot read PAL main.dol: {e}"))?;
        let patch = controller_patch::prepare_allstars(&config.patch_folder, &dol)?;
        let probe_map = if debug {
            let symbols = fs::read(&config.pal_symbols)
                .map_err(|e| format!("cannot read Petari symbols: {e}"))?;
            Some(player_probe::verify_pal_inputs(&dol, &symbols)?)
        } else {
            None
        };
        configure_isolated_profile(
            &user_dir,
            &patch,
            Some(&config.controller_profile),
            true,
            if debug { Some(port) } else { None },
            mute,
        )?;
        let mut child = spawn_game(
            &config.dolphin,
            &user_dir,
            &config.game,
            debug,
            port,
            mute,
            &patch,
        )?;
        if debug {
            println!("debug stage=gdb-attach; opening the persistent Rust GDB session");
            let mut client =
                match connect_gdb_until_ready(&mut child, port, Duration::from_secs(15)) {
                    Ok(client) => client,
                    Err(error) => {
                        let _ = child.kill();
                        let _ = child.wait();
                        return Err(format!(
                        "debug launch failed before resume; stopped its Dolphin process: {error}"
                    ));
                    }
                };
            let disc_id = match client.read_memory(0x8000_0000, 6) {
                Ok(bytes) => bytes,
                Err(error) => {
                    let _ = child.kill();
                    let _ = child.wait();
                    return Err(format!("debug launch could not verify live disc ID; stopped its Dolphin process: {error}"));
                }
            };
            if disc_id != b"RMGP01" {
                let found = String::from_utf8_lossy(&disc_id);
                let _ = child.kill();
                let _ = child.wait();
                return Err(format!("debug launch expected live PAL RMGP01, found {found}; stopped its Dolphin process"));
            }
            println!("debug stage=verified; live game ID RMGP01");
            if let Err(error) = client.continue_execution() {
                let _ = child.kill();
                let _ = child.wait();
                return Err(format!("debug launch could not resume Dolphin through GDB; stopped its process: {error}"));
            }
            println!("debug stage=continued; rendering/gameplay starts without an external probe attachment");
            log_process_windows(child.id());
            run_interactive_sampler(
                client,
                probe_map
                    .as_ref()
                    .expect("debug mode prevalidates the PAL map"),
                port,
                200,
                true,
            )?;
        } else {
            drop(child);
        }
        return Ok(());
    }
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
    if args.len() == 3 && args[0] == "validate-pal-game" {
        let dolphin = std::path::PathBuf::from(&args[1]);
        let game = std::path::PathBuf::from(&args[2]);
        verify_pal_game_image(&dolphin, &game)?;
        println!("DolphinTool verified the game image as PAL RMGP01 revision 0; source image was read only.");
        return Ok(());
    }
    if args.len() >= 7 && args[0] == "launch" {
        let dolphin = std::path::PathBuf::from(&args[1]);
        let user_dir = std::path::PathBuf::from(&args[2]);
        let game = std::path::PathBuf::from(&args[3]);
        let port = args[4]
            .to_str()
            .ok_or("GDB port must be valid UTF-8")?
            .parse::<u16>()
            .map_err(|_| "GDB port must be between 1 and 65535")?;
        let mut mute = false;
        let mut gdb_enabled = false;
        let mut gdb_mode_selected = false;
        let patch_dir = std::path::PathBuf::from(&args[5]);
        let dol_path = std::path::PathBuf::from(&args[6]);
        let mut index = 7;
        while index < args.len() {
            match args[index].to_str() {
                Some("--mute") if !mute => {
                    mute = true;
                    index += 1;
                }
                Some("--gdb") if !gdb_mode_selected => {
                    gdb_enabled = true;
                    gdb_mode_selected = true;
                    index += 1;
                }
                Some("--no-gdb") if !gdb_mode_selected => {
                    gdb_enabled = false;
                    gdb_mode_selected = true;
                    index += 1;
                }
                _ => {
                    return Err(
                        "launch requires <AllStars-patch-folder> <PAL-main.dol>; options: [--mute] [--gdb|--no-gdb]"
                            .into(),
                    )
                }
            }
        }
        if gdb_enabled && port == 0 {
            return Err("GDB port 0 is invalid when --gdb is enabled".into());
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
        verify_pal_game_image(&dolphin, &game)?;
        if gdb_enabled && !port_is_available(port) {
            return Err(format!(
                "GDB port {port} is already in use; refusing to launch against another process"
            ));
        }
        let dol = fs::read(&dol_path).map_err(|e| format!("cannot read PAL main.dol: {e}"))?;
        let prepared_allstars = controller_patch::prepare_allstars(&patch_dir, &dol)?;
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
        let user_dir = user_dir
            .canonicalize()
            .map_err(|e| format!("cannot resolve isolated Dolphin user directory: {e}"))?;
        configure_isolated_profile(
            &user_dir,
            &prepared_allstars,
            None,
            false,
            if gdb_enabled { Some(port) } else { None },
            mute,
        )?;
        spawn_game(
            &dolphin,
            &user_dir,
            &game,
            gdb_enabled,
            port,
            mute,
            &prepared_allstars,
        )?;
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
        )
        .map_err(|e| format!("{e}; start Dolphin with dev-launch --debug on this port and wait for its GDB listener"))?;
        let disc_id = client.read_memory(0x8000_0000, 6)?;
        if disc_id != b"RMGP01" {
            return Err(format!(
                "expected live PAL RMGP01, found {}",
                String::from_utf8_lossy(&disc_id)
            ));
        }
        println!("Verified PAL Rev 0 DOL and Petari map; live Dolphin identity RMGP01.");
        client
            .continue_execution()
            .map_err(|e| format!("could not resume Dolphin through GDB: {e}"))?;
        return run_interactive_sampler(client, &map, port, interval, false);
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
        return Err("usage: multigalaxy dev-setup <development.local.toml> <fresh-user-dir> | multigalaxy dev-launch <development.local.toml> <prepared-user-dir> <gdb-port> [--debug] [--unmute] | multigalaxy launch <Dolphin.exe> <fresh-user-dir> <game.rvz> <gdb-port-or-0> <AllStars-patch-folder> <PAL-main.dol> [--mute] [--gdb|--no-gdb] | multigalaxy validate-pal-game <Dolphin.exe> <game.rvz> | multigalaxy inspect-allstars <patch-folder> <PAL-main.dol> | multigalaxy readmem <port> <guest-hex-address> <length> | multigalaxy validate-mario-map <PAL-main.dol> <Petari-symbols.txt> | multigalaxy probe-mario <port> <PAL-main.dol> <Petari-symbols.txt> [interval-ms] | multigalaxy verify-loaded <port> <main.dol> | multigalaxy verify-loaded-full <port> <main.dol>".into());
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
    use super::*;

    #[test]
    fn powershell_handoff_quotes_paths_and_removes_extended_prefix() {
        let path = std::path::Path::new("\\\\?\\C:\\Games\\Max's SMG");
        assert_eq!(powershell_literal(path), "'C:\\Games\\Max''s SMG'");
    }

    #[test]
    fn sampled_validation_reads_start_middle_and_end_without_scanning_section() {
        assert_eq!(verification_offsets(0x5000, false), vec![0, 0x2800, 0x4ff0]);
        assert_eq!(verification_offsets(8, false), vec![0, 4]);
        assert_eq!(verification_offsets(2050, true), vec![0, 1024, 2048]);
    }

    #[test]
    fn test_mute_is_opt_in_and_does_not_change_normal_audio_defaults() {
        let normal = dolphin_config(Some(55173), false, false, false);
        assert!(normal.contains("GDBPort = 55173"));
        assert!(!normal.contains("[DSP]"));
        let no_gdb = dolphin_config(None, false, false, false);
        assert!(no_gdb.is_empty());
        assert!(!no_gdb.contains("GDBPort"));
        let muted = dolphin_config(None, true, false, false);
        assert!(muted.contains("Muted = True"));
        assert!(muted.contains("Volume = 0"));
        assert!(dolphin_config(None, false, true, false).contains("EnableCheats = True"));
        let development = dolphin_config(Some(55176), false, true, true);
        assert!(development.contains("GFXBackend = D3D12"));
        assert!(development.contains("GDBPort = 55176"));
        assert!(!no_gdb.contains("GFXBackend"));
    }

    #[test]
    fn isolated_dev_profile_activates_mandatory_patch_and_profile_without_touching_sources() {
        let root = std::env::temp_dir().join(format!(
            "mg1-profile-test-{}-{}",
            std::process::id(),
            super::timestamp_unix_ms()
        ));
        fs::create_dir_all(&root).unwrap();
        let source_profile = root.join("source-controller.ini");
        let profile_bytes = b"[Profile]\nExtension = Classic\nDevice = SDL/0/Test Pad\n";
        fs::write(&source_profile, profile_bytes).unwrap();
        let patch = controller_patch::PreparedPatch {
            settings: "[OnFrame]\n$ AllStars\n0x80004000:dword:0x60000000\n\n[OnFrame_Enabled]\n$ AllStars\n".into(),
            total_writes: 1,
            dol_text_writes: 1,
            dol_changed_words: 1,
            changed_sites: Vec::new(),
        };
        let user = root.join("isolated");
        configure_isolated_profile(
            &user,
            &patch,
            Some(&source_profile),
            true,
            Some(55221),
            true,
        )
        .unwrap();
        let dolphin_ini = fs::read_to_string(user.join("Config/Dolphin.ini")).unwrap();
        let game_ini = fs::read_to_string(user.join("GameSettings/RMGP01.ini")).unwrap();
        let copied_profile =
            fs::read(user.join("Config/Profiles/Wiimote/Pro Con to Classic Con.ini")).unwrap();
        assert!(dolphin_ini.contains("GFXBackend = D3D12"));
        assert!(dolphin_ini.contains("GDBPort = 55221"));
        assert!(game_ini.contains("[OnFrame_Enabled]"));
        assert!(game_ini.contains("WiimoteSource0 = 1"));
        assert!(game_ini.contains("WiimoteProfile1 = Pro Con to Classic Con"));
        assert_eq!(copied_profile, profile_bytes);
        assert_eq!(fs::read(source_profile).unwrap(), profile_bytes);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn accepts_only_pal_rmgp01_revision_zero_game_images() {
        assert!(parse_dolphintool_header("Game ID: RMGP01\nRevision: 0\nRegion: PAL\n").is_ok());
        assert!(
            parse_dolphintool_header("Game ID: RMGP01\nRevision: 1\nRegion: PAL\n")
                .unwrap_err()
                .contains("revision 0")
        );
        assert!(
            parse_dolphintool_header("Game ID: RMGE01\nRevision: 0\nRegion: USA\n")
                .unwrap_err()
                .contains("only PAL RMGP01")
        );
    }

    #[test]
    fn gdb_port_probe_never_connects_or_consumes_the_stub_session() {
        let listener = TcpListener::bind(("127.0.0.1", 0)).unwrap();
        let port = listener.local_addr().unwrap().port();
        assert!(!port_is_available(port));
        drop(listener);
        assert!(port_is_available(port));
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
