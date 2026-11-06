mod bridge_identity;
mod callback_observer;
mod controller_patch;
mod dev_config;
mod gdb;
mod gecko_bridge;
mod heap_observer;
mod hook_trampoline;
mod image_patch;
mod latency_stats;
mod mario_model_observer;
mod memory_inspection;
mod model_joint_observer;
mod penguin_observer;
mod perf_bench;
mod player_probe;
mod render_ownership;
mod resource_holder_observer;
mod scheduler_observer;
mod startup;
#[cfg(windows)]
mod stock_memory;
mod sync_net;

use multigalaxy_patch::{
    branch_target, changed_ranges, parse_dol, parse_onframe_patch, sha256, DiscIdentity,
    RevisionCatalog, SectionKind,
};
use std::collections::HashMap;
use std::io::Write;
use std::net::TcpListener;
use std::thread;
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

#[cfg(windows)]
fn run_inspect_memory(
    config_path: &std::path::Path,
    pid: u32,
    guest_address: u32,
    length: usize,
    format: memory_inspection::ValueFormat,
) -> Result<(), String> {
    const EXPECTED_HANDLER_SHA256: &str =
        "2b2ff0716aeb9b806cc61181e559041312957f25f04c435bd7da80a78917b33b";

    memory_inspection::validate_typed_request(guest_address, length, format)?;
    let config_text = fs::read_to_string(config_path)
        .map_err(|e| format!("cannot read development config: {e}"))?;
    let config = dev_config::parse(&config_text)?;
    validate_dev_inputs(&config)?;
    let dol = fs::read(&config.pal_dol).map_err(|e| format!("cannot read PAL main.dol: {e}"))?;
    let symbols =
        fs::read(&config.pal_symbols).map_err(|e| format!("cannot read Petari symbol map: {e}"))?;
    player_probe::verify_pal_inputs(&dol, &symbols)?;

    // Keep the required AllStars patch inputs validated and ensure none of its
    // current writes overlap the small PAL Rev 0 signatures used at runtime.
    let patch = controller_patch::prepare_allstars(&config.patch_folder, &dol)?;
    let patch_writes = parse_onframe_patch(&patch.settings)
        .map_err(|e| format!("invalid prepared AllStars patch: {e}"))?;
    for &(signature_address, signature) in player_probe::PAL_RUNTIME_SIGNATURES {
        let signature_range =
            u64::from(signature_address)..u64::from(signature_address) + signature.len() as u64;
        if patch_writes.iter().any(|write| {
            let write_range = u64::from(write.address)..u64::from(write.address) + 4;
            write_range.start < signature_range.end && signature_range.start < write_range.end
        }) {
            return Err(format!(
                "required AllStars patch overlaps PAL identity signature at 0x{signature_address:08x}; refusing runtime inspection"
            ));
        }
    }

    let dolphin = config
        .dolphin
        .canonicalize()
        .map_err(|e| format!("cannot resolve configured Dolphin executable: {e}"))?;
    if !dolphin
        .file_name()
        .is_some_and(|name| name.to_string_lossy().eq_ignore_ascii_case("Dolphin.exe"))
    {
        return Err("configured executable is not named Dolphin.exe".into());
    }
    let process_image = startup::process_image_path(pid)?
        .canonicalize()
        .map_err(|e| format!("cannot resolve PID {pid} executable: {e}"))?;
    if !process_image
        .to_string_lossy()
        .eq_ignore_ascii_case(&dolphin.to_string_lossy())
    {
        return Err(format!(
            "PID {pid} executable does not match configured Dolphin: {}",
            process_image.display()
        ));
    }
    let current_session = startup::desktop_context()
        .ok_or("cannot determine current Windows session")?
        .session_id;
    let process_session = startup::process_session_id(pid)
        .ok_or_else(|| format!("cannot determine Windows session for Dolphin PID {pid}"))?;
    if process_session != current_session {
        return Err(format!(
            "Dolphin PID {pid} is in session {process_session}, current session is {current_session}"
        ));
    }

    let handler_path = dolphin
        .parent()
        .ok_or("configured Dolphin executable has no parent directory")?
        .join("Sys")
        .join("codehandler.bin");
    let handler = fs::read(&handler_path)
        .map_err(|e| format!("cannot read Dolphin's 2609a codehandler: {e}"))?;
    let handler_hash = sha256(&handler)
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect::<String>();
    if handler.len() != 2880 || handler_hash != EXPECTED_HANDLER_SHA256 {
        return Err(format!(
            "Dolphin 2609a codehandler validation failed (size={}, sha256={handler_hash})",
            handler.len()
        ));
    }

    let mut memory = stock_memory::StockMem1::open_dolphin_read_only(pid)
        .map_err(|e| format!("cannot open compatible Dolphin MEM1 mapping read-only: {e:?}"))?;
    let game_id = memory.read_guest(memory_inspection::GUEST_MEM1_BASE, 6)?;
    if game_id != b"RMGP01" {
        return Err(format!(
            "live mapping is not PAL RMGP01: {}",
            String::from_utf8_lossy(&game_id)
        ));
    }
    player_probe::verify_live_pal_signatures(&mut memory, &dol)?;

    let bytes = memory.read_guest(guest_address, length)?;
    // Recheck identity after the requested sample so process/scene-independent
    // guest mapping changes fail closed instead of returning untrusted bytes.
    if memory.read_guest(memory_inspection::GUEST_MEM1_BASE, 6)? != b"RMGP01" {
        return Err("live game identity changed during requested read".into());
    }
    player_probe::verify_live_pal_signatures(&mut memory, &dol)?;

    let hex = bytes
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect::<String>();
    let decoded = match format {
        memory_inspection::ValueFormat::Bytes => format!("bytes={hex}"),
        memory_inspection::ValueFormat::U32 => format!(
            "u32=0x{:08x}",
            memory_inspection::decode_be_u32(guest_address, &bytes)?
        ),
        memory_inspection::ValueFormat::Pointer => {
            match memory_inspection::decode_be_pointer(guest_address, &bytes)? {
                Some(pointer) => format!("pointer=0x{pointer:08x}"),
                None => "pointer=null".into(),
            }
        }
        memory_inspection::ValueFormat::F32 => format!(
            "f32={}",
            memory_inspection::decode_be_f32(guest_address, &bytes)?
        ),
    };
    println!(
        "memory_read=validated pid={pid} game=RMGP01 revision=0 backend=stock-read-only-mem1 address=0x{guest_address:08x} length={length} {decoded}"
    );
    Ok(())
}

#[cfg(windows)]
fn run_observe_penguins(config_path: &std::path::Path, pid: u32) -> Result<(), String> {
    const EXPECTED_HANDLER_SHA256: &str =
        "2b2ff0716aeb9b806cc61181e559041312957f25f04c435bd7da80a78917b33b";
    let config_text = fs::read_to_string(config_path)
        .map_err(|e| format!("cannot read development config: {e}"))?;
    let config = dev_config::parse(&config_text)?;
    validate_dev_inputs(&config)?;
    let dol = fs::read(&config.pal_dol).map_err(|e| format!("cannot read PAL main.dol: {e}"))?;
    let symbols =
        fs::read(&config.pal_symbols).map_err(|e| format!("cannot read Petari symbol map: {e}"))?;
    player_probe::verify_pal_inputs(&dol, &symbols)?;
    penguin_observer::verify_penguin_pal(&dol, &symbols)?;
    let patch = controller_patch::prepare_allstars(&config.patch_folder, &dol)?;
    let writes = parse_onframe_patch(&patch.settings)
        .map_err(|e| format!("invalid prepared AllStars patch: {e}"))?;
    penguin_observer::ensure_patch_disjoint(&writes)?;

    let dolphin = config
        .dolphin
        .canonicalize()
        .map_err(|e| format!("cannot resolve configured Dolphin executable: {e}"))?;
    if !dolphin
        .file_name()
        .is_some_and(|name| name.to_string_lossy().eq_ignore_ascii_case("Dolphin.exe"))
    {
        return Err("configured executable is not named Dolphin.exe".into());
    }
    let image = startup::process_image_path(pid)?
        .canonicalize()
        .map_err(|e| format!("cannot resolve PID {pid} executable: {e}"))?;
    if !image
        .to_string_lossy()
        .eq_ignore_ascii_case(&dolphin.to_string_lossy())
    {
        return Err(format!(
            "PID {pid} executable does not match configured Dolphin: {}",
            image.display()
        ));
    }
    let current_session = startup::desktop_context()
        .ok_or("cannot determine current Windows session")?
        .session_id;
    let process_session = startup::process_session_id(pid)
        .ok_or_else(|| format!("cannot determine Windows session for Dolphin PID {pid}"))?;
    if current_session != process_session {
        return Err(format!("Dolphin PID {pid} is in session {process_session}, current session is {current_session}"));
    }
    let handler_path = dolphin
        .parent()
        .ok_or("configured Dolphin executable has no parent directory")?
        .join("Sys")
        .join("codehandler.bin");
    let handler = fs::read(&handler_path)
        .map_err(|e| format!("cannot read Dolphin's 2609a codehandler: {e}"))?;
    let handler_hash = sha256(&handler)
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect::<String>();
    if handler.len() != 2880 || handler_hash != EXPECTED_HANDLER_SHA256 {
        return Err(format!(
            "Dolphin 2609a codehandler validation failed (size={}, sha256={handler_hash})",
            handler.len()
        ));
    }
    let mut memory = stock_memory::StockMem1::open_dolphin_read_only(pid)
        .map_err(|e| format!("cannot open compatible Dolphin MEM1 mapping read-only: {e:?}"))?;
    if memory.read_guest(memory_inspection::GUEST_MEM1_BASE, 6)? != b"RMGP01" {
        return Err("live mapping is not PAL RMGP01".into());
    }
    player_probe::verify_live_pal_signatures(&mut memory, &dol)?;
    let report = penguin_observer::observe(&mut memory)?;
    player_probe::verify_live_pal_signatures(&mut memory, &dol)?;
    let matches = report
        .matches
        .iter()
        .map(|m| {
            format!(
                " index={} actor=0x{:08x} vptr=0x{:08x} x={:.3} y={:.3} z={:.3}",
                m.index, m.actor, m.vptr, m.x, m.y, m.z
            )
        })
        .collect::<String>();
    println!("penguin_observer=validated pid={pid} game=RMGP01 revision=0 elapsed_ms={} scene=0x{:08x} group=0x{:08x} capacity={} count={} invalid_entries={} null_entries={} non_penguin={} matches={}{}", report.elapsed_ms, report.scene, report.group, report.capacity, report.count, report.invalid_entries, report.null_entries, report.non_penguin, report.total_matches, matches);
    Ok(())
}

#[cfg(windows)]
fn run_observe_mario_model(config_path: &std::path::Path, pid: u32) -> Result<(), String> {
    const EXPECTED_HANDLER_SHA256: &str =
        "2b2ff0716aeb9b806cc61181e559041312957f25f04c435bd7da80a78917b33b";
    let config_text = fs::read_to_string(config_path)
        .map_err(|error| format!("cannot read development config: {error}"))?;
    let config = dev_config::parse(&config_text)?;
    validate_dev_inputs(&config)?;
    let dol =
        fs::read(&config.pal_dol).map_err(|error| format!("cannot read PAL main.dol: {error}"))?;
    let symbols = fs::read(&config.pal_symbols)
        .map_err(|error| format!("cannot read Petari symbol map: {error}"))?;
    mario_model_observer::verify_pal_layout(&dol, &symbols)?;
    let patch = controller_patch::prepare_allstars(&config.patch_folder, &dol)?;
    let writes = parse_onframe_patch(&patch.settings)
        .map_err(|error| format!("invalid prepared AllStars patch: {error}"))?;
    mario_model_observer::ensure_patch_disjoint(&writes)?;

    let dolphin = config
        .dolphin
        .canonicalize()
        .map_err(|error| format!("cannot resolve configured Dolphin executable: {error}"))?;
    if !dolphin
        .file_name()
        .is_some_and(|name| name.to_string_lossy().eq_ignore_ascii_case("Dolphin.exe"))
    {
        return Err("configured executable is not named Dolphin.exe".into());
    }
    let image = startup::process_image_path(pid)?
        .canonicalize()
        .map_err(|error| format!("cannot resolve PID {pid} executable: {error}"))?;
    if !image
        .to_string_lossy()
        .eq_ignore_ascii_case(&dolphin.to_string_lossy())
    {
        return Err(format!(
            "PID {pid} executable does not match configured Dolphin: {}",
            image.display()
        ));
    }
    let current_session = startup::desktop_context()
        .ok_or("cannot determine current Windows session")?
        .session_id;
    let process_session = startup::process_session_id(pid)
        .ok_or_else(|| format!("cannot determine Windows session for Dolphin PID {pid}"))?;
    if process_session != current_session {
        return Err(format!(
            "Dolphin PID {pid} is in session {process_session}, current session is {current_session}"
        ));
    }
    let handler_path = dolphin
        .parent()
        .ok_or("configured Dolphin executable has no parent directory")?
        .join("Sys")
        .join("codehandler.bin");
    let handler = fs::read(&handler_path)
        .map_err(|error| format!("cannot read Dolphin's 2609a codehandler: {error}"))?;
    let handler_hash = sha256(&handler)
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect::<String>();
    if handler.len() != 2880 || handler_hash != EXPECTED_HANDLER_SHA256 {
        return Err(format!(
            "Dolphin 2609a codehandler validation failed (size={}, sha256={handler_hash})",
            handler.len()
        ));
    }

    let mut memory = stock_memory::StockMem1::open_dolphin_read_only(pid).map_err(|error| {
        format!("cannot open compatible Dolphin MEM1 mapping read-only: {error:?}")
    })?;
    player_probe::verify_live_pal_signatures(&mut memory, &dol)?;
    let sample = mario_model_observer::observe(&mut memory, &dol)?;
    player_probe::verify_live_pal_signatures(&mut memory, &dol)?;
    let ptr = |value: Option<u32>| {
        value.map_or_else(
            || "unavailable".to_string(),
            |value| format!("0x{value:08x}"),
        )
    };
    println!(
        "mario_model_observer=validated_read_only unix_ms={} pid={pid} game=RMGP01 revision=0 GameSystem=0x{:08x} SceneController=0x{:08x} scene=0x{:08x} scene_object_holder=0x{:08x} actor_group=0x{:08x} MarioHolder=0x{:08x} MarioActor=0x{:08x} ModelManager=0x{:08x} XanimePlayer={} J3DModel=0x{:08x} dynamic_vptr=0x{:08x} J3DModelData=0x{:08x} data_vptr=0x{:08x} J3DJointTree=0x{:08x} joint_tree_vptr=0x{:08x} J3DMtxBuffer=0x{:08x} matrix_buffer_joint_tree=0x{:08x} matrix_vptr_at_plus_38=0x{:08x} SceneHeapNapa={} SceneHeapGDDR3={} sampled_current_heap={} current_matches_scene_napa={:?} model_allocation_heap=unverified scene_identity_tuple=stable_for_sample scene_serial_generation=unavailable",
        timestamp_unix_ms(),
        sample.game_system,
        sample.scene_controller,
        sample.scene,
        sample.scene_object_holder,
        sample.actor_group,
        sample.mario_holder,
        sample.mario_actor,
        sample.model_manager,
        ptr(sample.xanime_player),
        sample.model,
        sample.model_vptr,
        sample.model_data,
        sample.model_data_vptr,
        sample.joint_tree,
        sample.joint_tree_vptr,
        sample.matrix_buffer,
        sample.matrix_buffer_joint_tree,
        sample.matrix_buffer_vptr,
        ptr(sample.scene_heap_napa),
        ptr(sample.scene_heap_gddr3),
        ptr(sample.current_heap),
        sample.current_heap_matches_scene_napa,
    );
    Ok(())
}

#[cfg(not(windows))]
fn run_observe_mario_model(_config_path: &std::path::Path, _pid: u32) -> Result<(), String> {
    Err("observe-mario-model requires official stock Dolphin 2609a on Windows".into())
}

#[cfg(windows)]
fn run_observe_resource_holder(config_path: &std::path::Path, pid: u32) -> Result<(), String> {
    const EXPECTED_HANDLER_SHA256: &str =
        "2b2ff0716aeb9b806cc61181e559041312957f25f04c435bd7da80a78917b33b";
    let config_text = fs::read_to_string(config_path)
        .map_err(|error| format!("cannot read development config: {error}"))?;
    let config = dev_config::parse(&config_text)?;
    validate_dev_inputs(&config)?;
    let dol =
        fs::read(&config.pal_dol).map_err(|error| format!("cannot read PAL main.dol: {error}"))?;
    let symbols = fs::read(&config.pal_symbols)
        .map_err(|error| format!("cannot read Petari symbol map: {error}"))?;
    mario_model_observer::verify_pal_layout(&dol, &symbols)?;
    resource_holder_observer::verify_pal_layout(&dol, &symbols)?;
    let patch = controller_patch::prepare_allstars(&config.patch_folder, &dol)?;
    let writes = parse_onframe_patch(&patch.settings)
        .map_err(|error| format!("invalid prepared AllStars patch: {error}"))?;
    mario_model_observer::ensure_patch_disjoint(&writes)?;
    resource_holder_observer::ensure_patch_disjoint(&writes)?;

    let dolphin = config
        .dolphin
        .canonicalize()
        .map_err(|error| format!("cannot resolve configured Dolphin executable: {error}"))?;
    if !dolphin
        .file_name()
        .is_some_and(|name| name.to_string_lossy().eq_ignore_ascii_case("Dolphin.exe"))
    {
        return Err("configured executable is not named Dolphin.exe".into());
    }
    let image = startup::process_image_path(pid)?
        .canonicalize()
        .map_err(|error| format!("cannot resolve PID {pid} executable: {error}"))?;
    if !image
        .to_string_lossy()
        .eq_ignore_ascii_case(&dolphin.to_string_lossy())
    {
        return Err(format!(
            "PID {pid} executable does not match configured Dolphin: {}",
            image.display()
        ));
    }
    let current_session = startup::desktop_context()
        .ok_or("cannot determine current Windows session")?
        .session_id;
    let process_session = startup::process_session_id(pid)
        .ok_or_else(|| format!("cannot determine Windows session for Dolphin PID {pid}"))?;
    if process_session != current_session {
        return Err(format!(
            "Dolphin PID {pid} is in session {process_session}, current session is {current_session}"
        ));
    }
    let handler_path = dolphin
        .parent()
        .ok_or("configured Dolphin executable has no parent directory")?
        .join("Sys")
        .join("codehandler.bin");
    let handler = fs::read(&handler_path)
        .map_err(|error| format!("cannot read Dolphin's 2609a codehandler: {error}"))?;
    let handler_hash = sha256(&handler)
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect::<String>();
    if handler.len() != 2880 || handler_hash != EXPECTED_HANDLER_SHA256 {
        return Err(format!(
            "Dolphin 2609a codehandler validation failed (size={}, sha256={handler_hash})",
            handler.len()
        ));
    }

    let mut memory = stock_memory::StockMem1::open_dolphin_read_only(pid).map_err(|error| {
        format!("cannot open compatible Dolphin MEM1 mapping read-only: {error:?}")
    })?;
    player_probe::verify_live_pal_signatures(&mut memory, &dol)?;
    let sample = resource_holder_observer::observe(&mut memory, &dol)?;
    player_probe::verify_live_pal_signatures(&mut memory, &dol)?;
    let ptr = |value: Option<u32>| {
        value.map_or_else(
            || "unavailable".to_string(),
            |value| format!("0x{value:08x}"),
        )
    };
    let index = |value: Option<u32>| {
        value.map_or_else(|| "not_in_window".to_string(), |value| value.to_string())
    };
    let base = sample.base;
    let holder = sample.holder;
    println!(
        "resource_holder_observer=validated_read_only unix_ms={} pid={pid} game=RMGP01 revision=0 scene=0x{:08x} actor_group=0x{:08x} MarioActor=0x{:08x} ModelManager=0x{:08x} J3DModelData=0x{:08x} model_resource_holder=0x{:08x} anm_resource_holder={} holder_res_table=0x{:08x} res_table_entries=0x{:08x} res_table_count={} model_data_entry_index={} entry_probe_window={} holder_archive={} SceneHeapNapa={} SceneHeapGDDR3={} sampled_current_heap={} holder_vptr=none_polymorphic heap_free_list=unavailable scene_serial_generation=unavailable pointer_equality=not_lifetime_proof",
        timestamp_unix_ms(),
        base.scene,
        base.actor_group,
        base.mario_actor,
        base.model_manager,
        base.model_data,
        holder.model_resource_holder,
        ptr(holder.anm_resource_holder),
        holder.holder_res_table,
        holder.res_table_entries,
        holder.res_table_count,
        index(holder.model_data_entry_index),
        holder.entry_probe_window,
        ptr(holder.holder_archive),
        ptr(base.scene_heap_napa),
        ptr(base.scene_heap_gddr3),
        ptr(base.current_heap),
    );
    Ok(())
}

#[cfg(not(windows))]
fn run_observe_resource_holder(_config_path: &std::path::Path, _pid: u32) -> Result<(), String> {
    Err("observe-resource-holder requires official stock Dolphin 2609a on Windows".into())
}

#[cfg(windows)]
fn run_observe_model_joints(config_path: &std::path::Path, pid: u32) -> Result<(), String> {
    const EXPECTED_HANDLER_SHA256: &str =
        "2b2ff0716aeb9b806cc61181e559041312957f25f04c435bd7da80a78917b33b";
    let config_text = fs::read_to_string(config_path)
        .map_err(|error| format!("cannot read development config: {error}"))?;
    let config = dev_config::parse(&config_text)?;
    validate_dev_inputs(&config)?;
    let dol =
        fs::read(&config.pal_dol).map_err(|error| format!("cannot read PAL main.dol: {error}"))?;
    let symbols = fs::read(&config.pal_symbols)
        .map_err(|error| format!("cannot read Petari symbol map: {error}"))?;
    mario_model_observer::verify_pal_layout(&dol, &symbols)?;
    model_joint_observer::verify_pal_layout(&dol, &symbols)?;
    let patch = controller_patch::prepare_allstars(&config.patch_folder, &dol)?;
    let writes = parse_onframe_patch(&patch.settings)
        .map_err(|error| format!("invalid prepared AllStars patch: {error}"))?;
    mario_model_observer::ensure_patch_disjoint(&writes)?;
    model_joint_observer::ensure_patch_disjoint(&writes)?;

    let dolphin = config
        .dolphin
        .canonicalize()
        .map_err(|error| format!("cannot resolve configured Dolphin executable: {error}"))?;
    if !dolphin
        .file_name()
        .is_some_and(|name| name.to_string_lossy().eq_ignore_ascii_case("Dolphin.exe"))
    {
        return Err("configured executable is not named Dolphin.exe".into());
    }
    let image = startup::process_image_path(pid)?
        .canonicalize()
        .map_err(|error| format!("cannot resolve PID {pid} executable: {error}"))?;
    if !image
        .to_string_lossy()
        .eq_ignore_ascii_case(&dolphin.to_string_lossy())
    {
        return Err(format!(
            "PID {pid} executable does not match configured Dolphin: {}",
            image.display()
        ));
    }
    let current_session = startup::desktop_context()
        .ok_or("cannot determine current Windows session")?
        .session_id;
    let process_session = startup::process_session_id(pid)
        .ok_or_else(|| format!("cannot determine Windows session for Dolphin PID {pid}"))?;
    if process_session != current_session {
        return Err(format!(
            "Dolphin PID {pid} is in session {process_session}, current session is {current_session}"
        ));
    }
    let handler_path = dolphin
        .parent()
        .ok_or("configured Dolphin executable has no parent directory")?
        .join("Sys")
        .join("codehandler.bin");
    let handler = fs::read(&handler_path)
        .map_err(|error| format!("cannot read Dolphin's 2609a codehandler: {error}"))?;
    let handler_hash = sha256(&handler)
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect::<String>();
    if handler.len() != 2880 || handler_hash != EXPECTED_HANDLER_SHA256 {
        return Err(format!(
            "Dolphin 2609a codehandler validation failed (size={}, sha256={handler_hash})",
            handler.len()
        ));
    }

    let mut memory = stock_memory::StockMem1::open_dolphin_read_only(pid).map_err(|error| {
        format!("cannot open compatible Dolphin MEM1 mapping read-only: {error:?}")
    })?;
    player_probe::verify_live_pal_signatures(&mut memory, &dol)?;
    let observation = model_joint_observer::observe(&mut memory, &dol)?;
    player_probe::verify_live_pal_signatures(&mut memory, &dol)?;
    let fmt_sample = |sample: &model_joint_observer::JointSample| {
        let joints = sample
            .joints
            .iter()
            .map(|j| {
                format!(
                    "[joint=0x{:08x} callback=0x{:08x} mtx_calc=0x{:08x}]",
                    j.joint, j.callback, j.mtx_calc
                )
            })
            .collect::<Vec<_>>()
            .join(" ");
        format!(
            "joint_array=0x{:08x} joint_count={} joints={}",
            sample.joint_array, sample.joint_count, joints
        )
    };
    let base = observation.base;
    println!(
        "model_joint_observer=validated_read_only unix_ms={} pid={pid} game=RMGP01 revision=0 scene=0x{:08x} actor_group=0x{:08x} MarioActor=0x{:08x} ModelManager=0x{:08x} J3DModelData=0x{:08x} within_scene_gap_secs=3 within_scene_stable={} first{{{}}} second{{{}}} inference=endpoint_only factory_clear_not_traced pointer_equality=not_lifetime_proof",
        timestamp_unix_ms(),
        base.scene,
        base.actor_group,
        base.mario_actor,
        base.model_manager,
        base.model_data,
        observation.within_scene_stable,
        fmt_sample(&observation.first),
        fmt_sample(&observation.second),
    );
    Ok(())
}

#[cfg(not(windows))]
fn run_observe_model_joints(_config_path: &std::path::Path, _pid: u32) -> Result<(), String> {
    Err("observe-model-joints requires official stock Dolphin 2609a on Windows".into())
}

#[cfg(windows)]
fn run_verify_hook(config_path: &std::path::Path, pid: u32) -> Result<(), String> {
    const EXPECTED_HANDLER_SHA256: &str =
        "2b2ff0716aeb9b806cc61181e559041312957f25f04c435bd7da80a78917b33b";
    let config_text = fs::read_to_string(config_path)
        .map_err(|error| format!("cannot read development config: {error}"))?;
    let config = dev_config::parse(&config_text)?;
    validate_dev_inputs(&config)?;
    let dol =
        fs::read(&config.pal_dol).map_err(|error| format!("cannot read PAL main.dol: {error}"))?;
    let symbols = fs::read(&config.pal_symbols)
        .map_err(|error| format!("cannot read Petari symbol map: {error}"))?;
    hook_trampoline::verify_hook_layout(&dol, &symbols)?;
    let dolphin = config
        .dolphin
        .canonicalize()
        .map_err(|error| format!("cannot resolve configured Dolphin executable: {error}"))?;
    if !dolphin
        .file_name()
        .is_some_and(|name| name.to_string_lossy().eq_ignore_ascii_case("Dolphin.exe"))
    {
        return Err("configured executable is not named Dolphin.exe".into());
    }
    let image = startup::process_image_path(pid)?
        .canonicalize()
        .map_err(|error| format!("cannot resolve PID {pid} executable: {error}"))?;
    if !image
        .to_string_lossy()
        .eq_ignore_ascii_case(&dolphin.to_string_lossy())
    {
        return Err(format!(
            "PID {pid} executable does not match configured Dolphin: {}",
            image.display()
        ));
    }
    let current_session = startup::desktop_context()
        .ok_or("cannot determine current Windows session")?
        .session_id;
    let process_session = startup::process_session_id(pid)
        .ok_or_else(|| format!("cannot determine Windows session for Dolphin PID {pid}"))?;
    if process_session != current_session {
        return Err(format!(
            "Dolphin PID {pid} is in session {process_session}, current session is {current_session}"
        ));
    }
    let handler_path = dolphin
        .parent()
        .ok_or("configured Dolphin executable has no parent directory")?
        .join("Sys")
        .join("codehandler.bin");
    let handler = fs::read(&handler_path)
        .map_err(|error| format!("cannot read Dolphin's 2609a codehandler: {error}"))?;
    let handler_hash = sha256(&handler)
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect::<String>();
    if handler.len() != 2880 || handler_hash != EXPECTED_HANDLER_SHA256 {
        return Err(format!(
            "Dolphin 2609a codehandler validation failed (size={}, sha256={handler_hash})",
            handler.len()
        ));
    }
    let mut memory = stock_memory::StockMem1::open_dolphin_read_only(pid).map_err(|error| {
        format!("cannot open compatible Dolphin MEM1 mapping read-only: {error:?}")
    })?;
    player_probe::verify_live_pal_signatures(&mut memory, &dol)?;
    let report = hook_trampoline::check_live(&mut memory)?;
    let expected_window = image_patch::window_words(&dol, image_patch::WINDOW_GUEST)?;
    let mut live_window = [0u32; image_patch::WINDOW_WORDS];
    for (i, w) in live_window.iter_mut().enumerate() {
        let at = image_patch::WINDOW_GUEST + (i * 4) as u32;
        let bytes = memory
            .read_guest(at, 4)
            .map_err(|e| format!("window read at 0x{at:08x} failed: {e}"))?;
        *w = u32::from_be_bytes(bytes.try_into().expect("four bytes"));
    }
    let window_state = if live_window == expected_window {
        "original"
    } else {
        "MISMATCH"
    };
    player_probe::verify_live_pal_signatures(&mut memory, &dol)?;
    let state = match report.state {
        hook_trampoline::HookState::Original => {
            "original bl_directDraw=0x4BFF083D (hook not installed or rollback restored)"
                .to_string()
        }
        hook_trampoline::HookState::Installed { target } => {
            format!(
                "installed b_stub=0x{:08x} stub=0x{target:08x} stub_template=verified",
                report.hook_word
            )
        }
        hook_trampoline::HookState::Unexpected { word } => {
            format!("UNEXPECTED word=0x{word:08x}")
        }
    };
    println!(
        "verify_hook=validated_read_only unix_ms={} pid={pid} game=RMGP01 revision=0 hook_address=0x{:08x} hook_word=0x{:08x} state={} window_guest=0x{:08x} window={} window_words={:08x?}",
        timestamp_unix_ms(),
        gecko_bridge::HOOK_ADDRESS,
        report.hook_word,
        state,
        image_patch::WINDOW_GUEST,
        window_state,
        live_window,
    );
    Ok(())
}

#[cfg(not(windows))]
fn run_verify_hook(_config_path: &std::path::Path, _pid: u32) -> Result<(), String> {
    Err("verify-hook requires official stock Dolphin 2609a on Windows".into())
}

fn run_patch_image(
    config_path: &std::path::Path,
    source: &std::path::Path,
    output: &std::path::Path,
) -> Result<(), String> {
    let config_text = fs::read_to_string(config_path)
        .map_err(|error| format!("cannot read development config: {error}"))?;
    let config = dev_config::parse(&config_text)?;
    validate_dev_inputs(&config)?;
    let dol =
        fs::read(&config.pal_dol).map_err(|error| format!("cannot read PAL main.dol: {error}"))?;
    let symbols = fs::read(&config.pal_symbols)
        .map_err(|error| format!("cannot read Petari symbol map: {error}"))?;
    player_probe::verify_pal_inputs(&dol, &symbols)?;
    hook_trampoline::verify_hook_layout(&dol, &symbols)?;
    let manifest = image_patch::patch_image(source, output, &dol)?;
    println!(
        "patch_image=ok patch_id={} source_image_sha256={} output_image_sha256={} delta_sha256={} window_guest=0x{:08x} window_len={}",
        manifest.patch_id,
        manifest.source_image_sha256,
        manifest.output_image_sha256,
        manifest.delta_sha256,
        manifest.window_guest,
        manifest.window_len
    );
    println!("manifest written next to the output image; original image untouched");
    Ok(())
}

#[cfg(windows)]
fn run_observe_heaps(config_path: &std::path::Path, pid: u32) -> Result<(), String> {
    const EXPECTED_HANDLER_SHA256: &str =
        "2b2ff0716aeb9b806cc61181e559041312957f25f04c435bd7da80a78917b33b";
    let config_text = fs::read_to_string(config_path)
        .map_err(|error| format!("cannot read development config: {error}"))?;
    let config = dev_config::parse(&config_text)?;
    validate_dev_inputs(&config)?;
    let dol =
        fs::read(&config.pal_dol).map_err(|error| format!("cannot read PAL main.dol: {error}"))?;
    let symbols = fs::read(&config.pal_symbols)
        .map_err(|error| format!("cannot read Petari symbol map: {error}"))?;
    heap_observer::verify_pal_heap_layout(&dol, &symbols)?;
    let patch = controller_patch::prepare_allstars(&config.patch_folder, &dol)?;
    let writes = parse_onframe_patch(&patch.settings)
        .map_err(|error| format!("invalid prepared AllStars patch: {error}"))?;
    penguin_observer::ensure_patch_disjoint(&writes)?;

    let dolphin = config
        .dolphin
        .canonicalize()
        .map_err(|error| format!("cannot resolve configured Dolphin executable: {error}"))?;
    if !dolphin
        .file_name()
        .is_some_and(|name| name.to_string_lossy().eq_ignore_ascii_case("Dolphin.exe"))
    {
        return Err("configured executable is not named Dolphin.exe".into());
    }
    let image = startup::process_image_path(pid)?
        .canonicalize()
        .map_err(|error| format!("cannot resolve PID {pid} executable: {error}"))?;
    if !image
        .to_string_lossy()
        .eq_ignore_ascii_case(&dolphin.to_string_lossy())
    {
        return Err(format!(
            "PID {pid} executable does not match configured Dolphin: {}",
            image.display()
        ));
    }
    let current_session = startup::desktop_context()
        .ok_or("cannot determine current Windows session")?
        .session_id;
    let process_session = startup::process_session_id(pid)
        .ok_or_else(|| format!("cannot determine Windows session for Dolphin PID {pid}"))?;
    if current_session != process_session {
        return Err(format!("Dolphin PID {pid} is in session {process_session}, current session is {current_session}"));
    }
    let handler_path = dolphin
        .parent()
        .ok_or("configured Dolphin executable has no parent directory")?
        .join("Sys")
        .join("codehandler.bin");
    let handler = fs::read(&handler_path)
        .map_err(|error| format!("cannot read Dolphin's 2609a codehandler: {error}"))?;
    let handler_hash = sha256(&handler)
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect::<String>();
    if handler.len() != 2880 || handler_hash != EXPECTED_HANDLER_SHA256 {
        return Err(format!(
            "Dolphin 2609a codehandler validation failed (size={}, sha256={handler_hash})",
            handler.len()
        ));
    }
    let mut memory = stock_memory::StockMem1::open_dolphin_read_only(pid).map_err(|error| {
        format!("cannot open compatible Dolphin MEM1 mapping read-only: {error:?}")
    })?;
    player_probe::verify_live_pal_signatures(&mut memory, &dol)?;
    let sample = heap_observer::observe(&mut memory)?;
    player_probe::verify_live_pal_signatures(&mut memory, &dol)?;
    let format_pointer = |pointer: Option<u32>| {
        pointer.map_or_else(|| "null".to_string(), |value| format!("0x{value:08x}"))
    };
    println!(
        "heap_observer=validated_read_only pid={pid} game=RMGP01 revision=0 scene=0x{:08x} group=0x{:08x} watcher=0x{:08x} sampled_gameplay_current_heap={} scene_heap_napa={} scene_heap_gddr3={} current_matches_scene_napa={:?} constructor_time_heap=not_observed",
        sample.scene,
        sample.group,
        sample.watcher,
        format_pointer(sample.sampled_current_heap),
        format_pointer(sample.scene_heap_napa),
        format_pointer(sample.scene_heap_gddr3),
        sample.current_heap_matches_scene_napa,
    );
    Ok(())
}

#[cfg(windows)]
fn run_observe_scheduler(config_path: &std::path::Path, pid: u32) -> Result<(), String> {
    const EXPECTED_HANDLER_SHA256: &str =
        "2b2ff0716aeb9b806cc61181e559041312957f25f04c435bd7da80a78917b33b";
    let config_text = fs::read_to_string(config_path)
        .map_err(|error| format!("cannot read development config: {error}"))?;
    let config = dev_config::parse(&config_text)?;
    validate_dev_inputs(&config)?;
    let dol =
        fs::read(&config.pal_dol).map_err(|error| format!("cannot read PAL main.dol: {error}"))?;
    let symbols = fs::read(&config.pal_symbols)
        .map_err(|error| format!("cannot read Petari symbol map: {error}"))?;
    scheduler_observer::verify_pal_layout(&dol, &symbols)?;
    let patch = controller_patch::prepare_allstars(&config.patch_folder, &dol)?;
    let writes = parse_onframe_patch(&patch.settings)
        .map_err(|error| format!("invalid prepared AllStars patch: {error}"))?;
    penguin_observer::ensure_patch_disjoint(&writes)?;

    let dolphin = config
        .dolphin
        .canonicalize()
        .map_err(|error| format!("cannot resolve configured Dolphin executable: {error}"))?;
    if !dolphin
        .file_name()
        .is_some_and(|name| name.to_string_lossy().eq_ignore_ascii_case("Dolphin.exe"))
    {
        return Err("configured executable is not named Dolphin.exe".into());
    }
    let image = startup::process_image_path(pid)?
        .canonicalize()
        .map_err(|error| format!("cannot resolve PID {pid} executable: {error}"))?;
    if !image
        .to_string_lossy()
        .eq_ignore_ascii_case(&dolphin.to_string_lossy())
    {
        return Err(format!(
            "PID {pid} executable does not match configured Dolphin: {}",
            image.display()
        ));
    }
    let current_session = startup::desktop_context()
        .ok_or("cannot determine current Windows session")?
        .session_id;
    let process_session = startup::process_session_id(pid)
        .ok_or_else(|| format!("cannot determine Windows session for Dolphin PID {pid}"))?;
    if current_session != process_session {
        return Err(format!("Dolphin PID {pid} is in session {process_session}, current session is {current_session}"));
    }
    let handler_path = dolphin
        .parent()
        .ok_or("configured Dolphin executable has no parent directory")?
        .join("Sys")
        .join("codehandler.bin");
    let handler = fs::read(&handler_path)
        .map_err(|error| format!("cannot read Dolphin's 2609a codehandler: {error}"))?;
    let handler_hash = sha256(&handler)
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect::<String>();
    if handler.len() != 2880 || handler_hash != EXPECTED_HANDLER_SHA256 {
        return Err(format!(
            "Dolphin 2609a codehandler validation failed (size={}, sha256={handler_hash})",
            handler.len()
        ));
    }
    let mut memory = stock_memory::StockMem1::open_dolphin_read_only(pid).map_err(|error| {
        format!("cannot open compatible Dolphin MEM1 mapping read-only: {error:?}")
    })?;
    player_probe::verify_live_pal_signatures(&mut memory, &dol)?;
    scheduler_observer::verify_live_signatures(&mut memory, &dol)?;
    let sample = scheduler_observer::observe(&mut memory)?;
    player_probe::verify_live_pal_signatures(&mut memory, &dol)?;
    scheduler_observer::verify_live_signatures(&mut memory, &dol)?;
    println!(
        "scheduler_observer=validated_read_only pid={pid} game=RMGP01 revision=0 scene=0x{:08x} scene_object_holder=0x{:08x} all_live_actor_group=0x{:08x} execute_holder=0x{:08x} execute_array=0x{:08x} capacity={} count={} remaining={} pending_movement_connect={} pending_movement_disconnect={} pending_draw_connect={} pending_draw_disconnect={} pending_delayed_draw_disconnect={} callback_lists=not_read",
        sample.scene,
        sample.scene_object_holder,
        sample.all_live_actor_group,
        sample.execute_holder,
        sample.execute_array,
        sample.capacity,
        sample.count,
        sample.remaining,
        sample.pending_flags[0],
        sample.pending_flags[1],
        sample.pending_flags[2],
        sample.pending_flags[3],
        sample.pending_flags[4],
    );
    Ok(())
}

#[cfg(windows)]
fn run_observe_callback_lists(config_path: &std::path::Path, pid: u32) -> Result<(), String> {
    const EXPECTED_HANDLER_SHA256: &str =
        "2b2ff0716aeb9b806cc61181e559041312957f25f04c435bd7da80a78917b33b";
    let config_text = fs::read_to_string(config_path)
        .map_err(|error| format!("cannot read development config: {error}"))?;
    let config = dev_config::parse(&config_text)?;
    validate_dev_inputs(&config)?;
    let dol =
        fs::read(&config.pal_dol).map_err(|error| format!("cannot read PAL main.dol: {error}"))?;
    let symbols = fs::read(&config.pal_symbols)
        .map_err(|error| format!("cannot read Petari symbol map: {error}"))?;
    callback_observer::verify_pal_layout(&dol, &symbols)?;
    let patch = controller_patch::prepare_allstars(&config.patch_folder, &dol)?;
    let writes = parse_onframe_patch(&patch.settings)
        .map_err(|error| format!("invalid prepared AllStars patch: {error}"))?;
    penguin_observer::ensure_patch_disjoint(&writes)?;

    let dolphin = config
        .dolphin
        .canonicalize()
        .map_err(|error| format!("cannot resolve configured Dolphin executable: {error}"))?;
    if !dolphin
        .file_name()
        .is_some_and(|name| name.to_string_lossy().eq_ignore_ascii_case("Dolphin.exe"))
    {
        return Err("configured executable is not named Dolphin.exe".into());
    }
    let image = startup::process_image_path(pid)?
        .canonicalize()
        .map_err(|error| format!("cannot resolve PID {pid} executable: {error}"))?;
    if !image
        .to_string_lossy()
        .eq_ignore_ascii_case(&dolphin.to_string_lossy())
    {
        return Err(format!(
            "PID {pid} executable does not match configured Dolphin: {}",
            image.display()
        ));
    }
    let current_session = startup::desktop_context()
        .ok_or("cannot determine current Windows session")?
        .session_id;
    let process_session = startup::process_session_id(pid)
        .ok_or_else(|| format!("cannot determine Windows session for Dolphin PID {pid}"))?;
    if current_session != process_session {
        return Err(format!("Dolphin PID {pid} is in session {process_session}, current session is {current_session}"));
    }
    let handler_path = dolphin
        .parent()
        .ok_or("configured Dolphin executable has no parent directory")?
        .join("Sys")
        .join("codehandler.bin");
    let handler = fs::read(&handler_path)
        .map_err(|error| format!("cannot read Dolphin's 2609a codehandler: {error}"))?;
    let handler_hash = sha256(&handler)
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect::<String>();
    if handler.len() != 2880 || handler_hash != EXPECTED_HANDLER_SHA256 {
        return Err(format!(
            "Dolphin 2609a codehandler validation failed (size={}, sha256={handler_hash})",
            handler.len()
        ));
    }
    let mut memory = stock_memory::StockMem1::open_dolphin_read_only(pid).map_err(|error| {
        format!("cannot open compatible Dolphin MEM1 mapping read-only: {error:?}")
    })?;
    player_probe::verify_live_pal_signatures(&mut memory, &dol)?;
    scheduler_observer::verify_live_signatures(&mut memory, &dol)?;
    callback_observer::verify_live_signatures(&mut memory, &dol)?;
    let sample = callback_observer::observe(&mut memory)?;
    player_probe::verify_live_pal_signatures(&mut memory, &dol)?;
    scheduler_observer::verify_live_signatures(&mut memory, &dol)?;
    callback_observer::verify_live_signatures(&mut memory, &dol)?;
    println!(
        "callback_observer=validated_read_only pid={pid} game=RMGP01 revision=0 scene=0x{:08x} scene_object_holder=0x{:08x} all_live_actor_group=0x{:08x} scene_list_executor=0x{:08x} movement_list=0x{:08x} movement_category={} movement_info=0x{:08x} movement_callbacks=0x{:08x} movement_count={} movement_capacity={} movement_remaining={} movement_check_count={} calc_anim_list=0x{:08x} calc_anim_category={} calc_anim_info=0x{:08x} calc_anim_callbacks=0x{:08x} calc_anim_count={} calc_anim_capacity={} calc_anim_remaining={} calc_anim_check_count={} draw_list=0x{:08x} draw_categories=0x{:08x} draw_category_slots={} draw_category={} draw_buffer_holder=0x{:08x} draw_group_table=0x{:08x} draw_group_table_capacity={} draw_buffer_type=0x10 draw_group=0x{:08x} draw_executors=0x{:08x}:{} / {} remaining={} draw_active=0x{:08x}:{} / {} remaining={} execute_holder=0x{:08x} execute_count={} execute_capacity={} pending_flags={:02x?}",
        sample.scene,
        sample.scene_object_holder,
        sample.all_live_actor_group,
        sample.scene_list_executor,
        sample.movement.list,
        sample.movement.identity,
        sample.movement.address,
        sample.movement.array,
        sample.movement.count,
        sample.movement.capacity,
        sample.movement.remaining,
        sample.movement.check_count,
        sample.calc_anim.list,
        sample.calc_anim.identity,
        sample.calc_anim.address,
        sample.calc_anim.array,
        sample.calc_anim.count,
        sample.calc_anim.capacity,
        sample.calc_anim.remaining,
        sample.calc_anim.check_count,
        sample.draw_list,
        sample.draw_categories,
        sample.draw_category_count,
        sample.draw_category,
        sample.draw_buffer_holder,
        sample.draw_group_table,
        sample.draw_group_table_capacity,
        sample.draw_buffer.address,
        sample.draw_buffer.executors_array,
        sample.draw_buffer.executors,
        sample.draw_buffer.executor_capacity,
        sample.draw_buffer.executor_remaining,
        sample.draw_buffer.active_array,
        sample.draw_buffer.active,
        sample.draw_buffer.active_capacity,
        sample.draw_buffer.active_remaining,
        sample.execute_holder,
        sample.execute_count,
        sample.execute_capacity,
        sample.pending_flags,
    );
    Ok(())
}

#[cfg(not(windows))]
fn run_observe_heaps(_config_path: &std::path::Path, _pid: u32) -> Result<(), String> {
    Err("observe-heaps requires official stock Dolphin 2609a on Windows".into())
}

#[cfg(not(windows))]
fn run_observe_scheduler(_config_path: &std::path::Path, _pid: u32) -> Result<(), String> {
    Err("observe-scheduler requires official stock Dolphin 2609a on Windows".into())
}

#[cfg(not(windows))]
fn run_observe_callback_lists(_config_path: &std::path::Path, _pid: u32) -> Result<(), String> {
    Err("observe-callback-lists requires official stock Dolphin 2609a on Windows".into())
}

#[cfg(not(windows))]
fn run_observe_penguins(_config_path: &std::path::Path, _pid: u32) -> Result<(), String> {
    Err("observe-penguins requires official stock Dolphin 2609a on Windows".into())
}

#[cfg(not(windows))]
fn run_inspect_memory(
    _config_path: &std::path::Path,
    _pid: u32,
    _guest_address: u32,
    _length: usize,
    _format: memory_inspection::ValueFormat,
) -> Result<(), String> {
    Err("inspect-memory currently requires official stock Dolphin 2609a on Windows".into())
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
    gecko_bridge_probe: bool,
    trampoline_dry_run: bool,
) -> Result<(), String> {
    let config_dir = user_dir.join("Config");
    fs::create_dir_all(&config_dir)
        .map_err(|e| format!("cannot create isolated Dolphin config directory: {e}"))?;
    fs::write(
        config_dir.join("Dolphin.ini"),
        dolphin_config(gdb_port, mute, true, development),
    )
    .map_err(|e| format!("cannot write isolated Dolphin configuration: {e}"))?;
    if development {
        fs::write(
            config_dir.join("GFX.ini"),
            "[Settings]\nShowFPS = True\nShowVPS = True\nShowSpeed = True\n",
        )
        .map_err(|e| format!("cannot enable isolated performance metrics: {e}"))?;
    }
    let settings_dir = user_dir.join("GameSettings");
    fs::create_dir_all(&settings_dir)
        .map_err(|e| format!("cannot create isolated GameSettings directory: {e}"))?;
    let mut settings = controller_patch::allstars_game_settings(patch).to_owned();
    if gecko_bridge_probe || trampoline_dry_run {
        // Gecko codes are loaded into the codehandler cave in ini order, so
        // our block must come FIRST: it then starts right after the 2880-byte
        // codehandler (0x80002340) and stays below the AllStars cave writes at
        // 0x80002790. Appending after AllStars placed our stub inside the
        // AllStars-written region (Stage 1 live failure, see
        // docs/references/draw-only-hook-contract.md).
        let mut gecko = String::new();
        gecko_bridge::append_code_with(&mut gecko, trampoline_dry_run);
        settings = format!("{gecko}{settings}");
    }
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

#[cfg(windows)]
fn run_interactive_gecko_bridge_probe(
    child: &mut process::Child,
    dolphin: &std::path::Path,
    identity_timeout: Duration,
) -> Result<(), String> {
    const EXPECTED_HANDLER_SHA256: &str =
        "2b2ff0716aeb9b806cc61181e559041312957f25f04c435bd7da80a78917b33b";
    let pid = child.id();
    let sys_handler = dolphin
        .parent()
        .unwrap_or_else(|| std::path::Path::new("."))
        .join("Sys")
        .join("codehandler.bin");
    let handler_bytes = fs::read(&sys_handler).map_err(|error| {
        format!(
            "cannot inspect stock Dolphin Gecko codehandler {}: {error}",
            sys_handler.display()
        )
    })?;
    let handler_size = handler_bytes.len();
    let handler_sha256 = sha256(&handler_bytes)
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect::<String>();
    if handler_size != 2880 || handler_sha256 != EXPECTED_HANDLER_SHA256 {
        return Err(format!(
            "Dolphin codehandler.bin did not match the verified 2609a input (size={handler_size}, sha256={handler_sha256}); refusing to guess a Gecko RAM layout"
        ));
    }
    println!("Stock Dolphin 2609a PAL mailbox probe; PID={pid}; handler_bytes={handler_size}; handler_sha256={handler_sha256}. Counter/ACK is active; timestamped Mario X/Y/Z is printed only for a stable validated snapshot. No GDB server is enabled. Type Q to stop.");
    println!("Waiting for Dolphin's named MEM1 mapping and the isolated-profile Gecko probe...");
    let (tx, rx) = std::sync::mpsc::channel::<String>();
    std::thread::spawn(move || {
        let stdin = std::io::stdin();
        loop {
            let mut line = String::new();
            if stdin.read_line(&mut line).is_err() || tx.send(line.trim().to_owned()).is_err() {
                break;
            }
        }
    });

    let probe_started = Instant::now();
    let mapping_deadline = probe_started + identity_timeout;
    let mut gecko_deadline = None;
    let mut memory = None;
    let mut mailbox = None;
    let mut next_discovery = Instant::now();
    let mut last_counter = None;
    let mut last_counter_change = Instant::now();
    let mut stalled_reported = false;
    let mut last_stall_log = None;
    let mut last_ack_counter = None;
    let mut pending_ack: Option<(u32, Instant, u32)> = None;
    let mut report_started = Instant::now();
    let mut report_frames = 0u64;
    let mut report_acks = 0u64;
    let mut report_rtt_total = Duration::ZERO;
    let mut report_rtt_max = Duration::ZERO;
    let mut report_read_total = Duration::ZERO;
    let mut report_write_total = Duration::ZERO;
    let mut report_validation_total = Duration::ZERO;
    let mut report_reads = 0u64;
    let mut report_writes = 0u64;
    let mut report_validations = 0u64;
    let mut all_ack_latencies_ms = Vec::new();
    let mut discovery_notice = Instant::now();
    let mut last_player_status: Option<&'static str> = None;

    loop {
        if let Some(status) = child.try_wait().map_err(|error| {
            format!("cannot inspect Dolphin while bridge probe is running: {error}")
        })? {
            return Err(format!(
                "Dolphin exited while bridge probe was active ({status})"
            ));
        }
        match rx.recv_timeout(Duration::from_millis(1)) {
            Ok(command)
                if command.eq_ignore_ascii_case("q") || command.eq_ignore_ascii_case("quit") =>
            {
                break
            }
            Ok(command) if !command.is_empty() => {
                println!("unknown command {command:?}; type Q to stop")
            }
            Err(std::sync::mpsc::RecvTimeoutError::Disconnected) => break,
            _ => {}
        }

        if memory.is_none() && Instant::now() >= next_discovery {
            match stock_memory::StockMem1::open_dolphin(pid) {
                Ok(view) => {
                    let view_bytes = view.mapped_view_bytes();
                    let remaining_identity_timeout =
                        mapping_deadline.saturating_duration_since(Instant::now());
                    if remaining_identity_timeout.is_zero() {
                        return Err(format!("timed out waiting {:?} for the Dolphin MEM1 mapping and PAL identity; pid={pid} mapping_view_bytes={view_bytes} validation=timeout elapsed_ms={}", identity_timeout, probe_started.elapsed().as_millis()));
                    }
                    println!("elapsed_ms={} pid={pid} mapping_status=valid mapping_view_bytes={view_bytes} validation=game_identity_pending", probe_started.elapsed().as_millis());
                    let mut last_identity_log = None;
                    if let Err(error) = bridge_identity::wait_for_pal_identity(
                        remaining_identity_timeout,
                        Duration::from_millis(250),
                        || {
                            let bytes = view.read_guest(0x8000_0000, 6)?;
                            bytes.try_into().map_err(|_| {
                                "MEM1 returned an unexpected game ID length".to_owned()
                            })
                        },
                        |elapsed, observed, status| {
                            if status == bridge_identity::IdentityStatus::Ready
                                || last_identity_log.map_or(true, |last: Instant| {
                                    last.elapsed() >= Duration::from_secs(2)
                                })
                            {
                                println!("elapsed_ms={} identity_poll_elapsed_ms={} pid={pid} mapping_status=valid mapping_view_bytes={view_bytes} validation={status:?} observed_game_id={}", probe_started.elapsed().as_millis(), elapsed.as_millis(), bridge_identity::describe_game_id(observed));
                                last_identity_log = Some(Instant::now());
                            }
                        },
                    ) {
                        return Err(format!("pid={pid} mapping_status=valid mapping_view_bytes={view_bytes} validation=identity_failed: {error}"));
                    }
                    println!("elapsed_ms={} pid={pid} mapping_status=valid mapping_view_bytes={view_bytes} validation=PAL_RMGP01_verified", probe_started.elapsed().as_millis());
                    memory = Some(view);
                    gecko_deadline = Some(Instant::now() + Duration::from_secs(60));
                    next_discovery = Instant::now();
                }
                Err(stock_memory::MappingOpenError::NotReady(error)) => {
                    if Instant::now() >= mapping_deadline {
                        return Err(format!("timed out waiting {:?} for Dolphin's named MEM1 mapping; pid={pid} mapping_view_bytes=0 validation=mapping_not_created elapsed_ms={} detail={error}", identity_timeout, probe_started.elapsed().as_millis()));
                    }
                    if discovery_notice.elapsed() >= Duration::from_secs(2) {
                        println!("elapsed_ms={} pid={pid} mapping_status=waiting mapping_view_bytes=0 validation=mapping_not_created detail={error}", probe_started.elapsed().as_millis());
                        discovery_notice = Instant::now();
                    }
                    next_discovery = Instant::now() + Duration::from_millis(250);
                }
                Err(stock_memory::MappingOpenError::Invalid(error)) => {
                    return Err(format!("invalid Dolphin MEM1 mapping; pid={pid} mapping_view_bytes=0 elapsed_ms={} detail={error}; refusing all writes", probe_started.elapsed().as_millis()));
                }
            }
        }

        if memory.is_some() && Instant::now() >= next_discovery {
            let view = memory.as_ref().expect("mapping checked above");
            let validation_started = Instant::now();
            let game_id: [u8; 6] = view
                .read_guest(0x8000_0000, 6)?
                .try_into()
                .map_err(|_| "MEM1 returned an unexpected game ID length".to_owned())?;
            match bridge_identity::classify_game_id(game_id) {
                bridge_identity::IdentityStatus::Ready => {}
                bridge_identity::IdentityStatus::Uninitialized => {
                    if mailbox.take().is_some() {
                        println!("elapsed_ms={} pid={pid} mapping_status=valid mapping_view_bytes={} validation=game_id_uninitialized observed_game_id={}", probe_started.elapsed().as_millis(), view.mapped_view_bytes(), bridge_identity::describe_game_id(game_id));
                    }
                    last_counter = None;
                    pending_ack = None;
                    gecko_deadline = Some(Instant::now() + Duration::from_secs(60));
                    next_discovery = Instant::now() + Duration::from_millis(500);
                    continue;
                }
                bridge_identity::IdentityStatus::WrongGame => {
                    return Err(format!("runtime game identity changed; pid={pid} validation=wrong_game observed_game_id={}", bridge_identity::describe_game_id(game_id)));
                }
                bridge_identity::IdentityStatus::InvalidMapping => {
                    return Err(format!("runtime MEM1 identity became invalid; pid={pid} validation=invalid_mapping observed_game_id={}", bridge_identity::describe_game_id(game_id)));
                }
            }
            let length = (gecko_bridge::HANDLER_END - gecko_bridge::HANDLER_BASE) as usize;
            let bytes = view.read_guest(gecko_bridge::HANDLER_BASE, length)?;
            report_validation_total += validation_started.elapsed();
            report_validations += 1;
            match gecko_bridge::locate_mailbox(gecko_bridge::HANDLER_BASE, handler_size, &bytes) {
                Ok(Some(found)) => {
                    if mailbox != Some(found) {
                        println!(
                            "state=mailbox_ready counter=0x{:08x} ack=0x{:08x} echo=0x{:08x}",
                            found.counter_address(),
                            found.acknowledgement_address(),
                            found.echo_address()
                        );
                        mailbox = Some(found);
                        gecko_deadline = None;
                        last_counter = None;
                        pending_ack = None;
                        last_counter_change = Instant::now();
                        stalled_reported = false;
                        report_started = Instant::now();
                    }
                }
                Ok(None) => {
                    if gecko_deadline.is_some_and(|deadline| Instant::now() >= deadline) {
                        return Err(format!("timed out waiting 60 seconds for the Gecko mailbox; pid={pid} mapping_view_bytes={} validation=PAL_RMGP01_verified gecko_signature=not_found elapsed_ms={}", memory.as_ref().map_or(0, stock_memory::StockMem1::mapped_view_bytes), probe_started.elapsed().as_millis()));
                    }
                    if mailbox.take().is_some() {
                        println!("elapsed_ms={} pid={pid} mapping_status=valid mapping_view_bytes={} validation=mailbox_unavailable; writes_suspended=true", probe_started.elapsed().as_millis(), view.mapped_view_bytes());
                        last_counter = None;
                        pending_ack = None;
                        gecko_deadline = Some(Instant::now() + Duration::from_secs(60));
                    }
                    if discovery_notice.elapsed() >= Duration::from_secs(2) {
                        println!("elapsed_ms={} pid={pid} mapping_status=valid mapping_view_bytes={} validation=PAL_RMGP01_verified gecko_signature=waiting", probe_started.elapsed().as_millis(), memory.as_ref().map_or(0, stock_memory::StockMem1::mapped_view_bytes));
                        discovery_notice = Instant::now();
                    }
                }
                Err(error) => {
                    return Err(format!(
                        "could not validate Gecko mailbox in stock Dolphin RAM: {error}"
                    ))
                }
            }
            next_discovery = Instant::now() + Duration::from_millis(500);
        }

        if let Some(active_mailbox) = mailbox {
            let view = memory.as_ref().expect("mailbox requires a mapped view");
            let read_started = Instant::now();
            let counter = view.read_guest_word(active_mailbox.counter_address())?;
            let echo = view.read_guest_word(active_mailbox.echo_address())?;
            let read_time = read_started.elapsed();
            report_read_total += read_time;
            report_reads += 1;
            let counter_changed = last_counter != Some(counter);
            if counter_changed {
                if let Some(previous) = last_counter {
                    let delta = counter.wrapping_sub(previous);
                    if delta < 0x8000_0000 {
                        report_frames = report_frames.saturating_add(delta as u64);
                    }
                }
                last_counter = Some(counter);
                last_counter_change = Instant::now();
                if stalled_reported {
                    println!("unix_ms={} state=live_recovered counter={counter}; mailbox writes resumed after game heartbeat recovery", timestamp_unix_ms());
                    stalled_reported = false;
                    last_stall_log = None;
                }

                // Read a whole versioned snapshot, then re-read its sequence
                // and counter. A racing/paused/stale sample is never emitted
                // as live coordinates.
                let position_game_id: [u8; 6] = view
                    .read_guest(0x8000_0000, 6)?
                    .try_into()
                    .map_err(|_| "MEM1 returned an unexpected game ID length".to_owned())?;
                let snapshot_state = match bridge_identity::classify_game_id(position_game_id) {
                    bridge_identity::IdentityStatus::Ready => {
                        let bytes = view.read_guest(active_mailbox.snapshot_address(), 12 * 4)?;
                        let sequence_after =
                            view.read_guest_word(active_mailbox.snapshot_address() + 20)?;
                        let counter_after =
                            view.read_guest_word(active_mailbox.counter_address())?;
                        if counter_after != counter {
                            Err(gecko_bridge::SnapshotState::Inconsistent)
                        } else {
                            gecko_bridge::decode_snapshot(&bytes, sequence_after)?
                        }
                    }
                    bridge_identity::IdentityStatus::Uninitialized => {
                        Err(gecko_bridge::SnapshotState::Unavailable)
                    }
                    bridge_identity::IdentityStatus::WrongGame => {
                        return Err(format!("runtime game identity changed before position read; validation=wrong_game observed_game_id={}", bridge_identity::describe_game_id(position_game_id)));
                    }
                    bridge_identity::IdentityStatus::InvalidMapping => {
                        return Err(format!("runtime MEM1 identity became invalid before position read; validation=invalid_mapping observed_game_id={}", bridge_identity::describe_game_id(position_game_id)));
                    }
                };
                match snapshot_state {
                    Ok(sample) if sample.counter == counter => {
                        last_player_status = Some("live");
                        println!("unix_ms={} state=player_position counter={} actor=0x{:08x} x={:.4} y={:.4} z={:.4}", timestamp_unix_ms(), sample.counter, sample.actor, sample.x, sample.y, sample.z);
                    }
                    Err(gecko_bridge::SnapshotState::Unavailable) => {
                        if last_player_status != Some("unavailable") {
                            println!("unix_ms={} state=player_unavailable counter={} reason=mario_holder_or_actor_not_ready", timestamp_unix_ms(), counter);
                        }
                        last_player_status = Some("unavailable");
                    }
                    Err(gecko_bridge::SnapshotState::Inconsistent) => {
                        if last_player_status != Some("inconsistent") {
                            println!("unix_ms={} state=player_snapshot_waiting counter={} reason=seqlock_or_counter_changed", timestamp_unix_ms(), counter);
                        }
                        last_player_status = Some("inconsistent");
                    }
                    Err(gecko_bridge::SnapshotState::Invalid) => {
                        if last_player_status != Some("invalid") {
                            println!("unix_ms={} state=player_unavailable counter={} reason=invalid_actor_or_coordinates", timestamp_unix_ms(), counter);
                        }
                        last_player_status = Some("invalid");
                    }
                    Ok(_) => {
                        if last_player_status != Some("inconsistent") {
                            println!("unix_ms={} state=player_snapshot_waiting counter={} reason=counter_advanced_during_read", timestamp_unix_ms(), counter);
                        }
                        last_player_status = Some("inconsistent");
                    }
                }
            } else if last_counter_change.elapsed() > Duration::from_secs(3) {
                if !stalled_reported
                    || last_stall_log.map_or(true, |last: Instant| {
                        last.elapsed() >= Duration::from_secs(2)
                    })
                {
                    println!("unix_ms={} state=stalled counter={counter} writes_suspended=true; waiting for Dolphin/game heartbeat", timestamp_unix_ms());
                    stalled_reported = true;
                    last_stall_log = Some(Instant::now());
                }
                if last_player_status != Some("stalled") {
                    println!("unix_ms={} state=player_unavailable counter={} reason=game_heartbeat_stalled", timestamp_unix_ms(), counter);
                    last_player_status = Some("stalled");
                }
            }

            if let Some((expected, sent_at, sent_counter)) = pending_ack {
                if echo == expected {
                    let rtt = sent_at.elapsed();
                    if rtt >= Duration::from_millis(50) {
                        println!(
                            "unix_ms={} state=ack_latency_outlier sent_counter={} current_counter={} ack=0x{expected:08x} rtt_ms={:.3} heartbeat_age_ms={}",
                            timestamp_unix_ms(), sent_counter, counter, rtt.as_secs_f64() * 1000.0,
                            last_counter_change.elapsed().as_millis()
                        );
                    }
                    report_acks += 1;
                    report_rtt_total += rtt;
                    report_rtt_max = report_rtt_max.max(rtt);
                    all_ack_latencies_ms.push(rtt.as_secs_f64() * 1000.0);
                    pending_ack = None;
                }
            }
            if pending_ack.is_none() && counter_changed && last_ack_counter != Some(counter) {
                let acknowledgement = counter.rotate_left(7) ^ 0x4d47_3141;
                let live_id: [u8; 6] = view
                    .read_guest(0x8000_0000, 6)?
                    .try_into()
                    .map_err(|_| "MEM1 returned an unexpected game ID length".to_owned())?;
                match bridge_identity::classify_game_id(live_id) {
                    bridge_identity::IdentityStatus::Ready => {}
                    bridge_identity::IdentityStatus::Uninitialized => {
                        mailbox = None;
                        pending_ack = None;
                        last_counter = None;
                        gecko_deadline = Some(Instant::now() + Duration::from_secs(60));
                        println!("unix_ms={} validation=game_id_uninitialized observed_game_id={} writes_suspended=true", timestamp_unix_ms(), bridge_identity::describe_game_id(live_id));
                        continue;
                    }
                    bridge_identity::IdentityStatus::WrongGame => {
                        return Err(format!("runtime game identity changed before mailbox write; validation=wrong_game observed_game_id={}", bridge_identity::describe_game_id(live_id)));
                    }
                    bridge_identity::IdentityStatus::InvalidMapping => {
                        return Err(format!("runtime MEM1 mapping became invalid before mailbox write; validation=invalid_mapping observed_game_id={}", bridge_identity::describe_game_id(live_id)));
                    }
                }
                let handler_len = (gecko_bridge::HANDLER_END - gecko_bridge::HANDLER_BASE) as usize;
                let validation_started = Instant::now();
                let live_handler = view.read_guest(gecko_bridge::HANDLER_BASE, handler_len)?;
                let mailbox_still_valid = gecko_bridge::snapshot_matches_mailbox(
                    gecko_bridge::HANDLER_BASE,
                    handler_size,
                    &live_handler,
                    active_mailbox,
                )?;
                if !mailbox_still_valid {
                    mailbox = None;
                    pending_ack = None;
                    last_counter = None;
                    gecko_deadline = Some(Instant::now() + Duration::from_secs(60));
                    println!(
                        "unix_ms={} validation=mailbox_signature_lost writes_suspended=true",
                        timestamp_unix_ms()
                    );
                    continue;
                }
                report_validation_total += validation_started.elapsed();
                report_validations += 1;
                let write_started = Instant::now();
                view.acknowledge(active_mailbox, acknowledgement)?;
                report_write_total += write_started.elapsed();
                report_writes += 1;
                pending_ack = Some((acknowledgement, write_started, counter));
                last_ack_counter = Some(counter);
            }

            if report_started.elapsed() >= Duration::from_secs(1) {
                let elapsed = report_started.elapsed().as_secs_f64();
                let avg_rtt_ms = if report_acks == 0 {
                    0.0
                } else {
                    report_rtt_total.as_secs_f64() * 1000.0 / report_acks as f64
                };
                let avg_read_ms = if report_reads == 0 {
                    0.0
                } else {
                    report_read_total.as_secs_f64() * 1000.0 / report_reads as f64
                };
                let avg_write_ms = if report_writes == 0 {
                    0.0
                } else {
                    report_write_total.as_secs_f64() * 1000.0 / report_writes as f64
                };
                let avg_validation_ms = if report_validations == 0 {
                    0.0
                } else {
                    report_validation_total.as_secs_f64() * 1000.0 / report_validations as f64
                };
                println!(
                    "unix_ms={} state=live counter={} game_update_hz={:.1} ack_hz={:.1} read_hz={:.1} ack_rtt_avg_ms={:.3} ack_rtt_max_ms={:.3} shared_read_avg_ms={:.6} shared_validation_avg_ms={:.6} shared_write_avg_ms={:.6}",
                    timestamp_unix_ms(), counter, report_frames as f64 / elapsed,
                    report_acks as f64 / elapsed, report_reads as f64 / elapsed, avg_rtt_ms, report_rtt_max.as_secs_f64() * 1000.0,
                    avg_read_ms, avg_validation_ms, avg_write_ms
                );
                report_started = Instant::now();
                report_frames = 0;
                report_acks = 0;
                report_rtt_total = Duration::ZERO;
                report_rtt_max = Duration::ZERO;
                report_read_total = Duration::ZERO;
                report_write_total = Duration::ZERO;
                report_validation_total = Duration::ZERO;
                report_reads = 0;
                report_writes = 0;
                report_validations = 0;
            }
            let _ = std::io::stdout().flush();
            std::thread::sleep(Duration::from_millis(1));
        } else {
            std::thread::sleep(Duration::from_millis(20));
        }
    }
    if let Some(summary) = latency_stats::summarize(&all_ack_latencies_ms) {
        println!("bridge_ack_latency_summary count={} mean_ms={:.3} stddev_ms={:.3} p50_ms={:.3} p95_ms={:.3} p99_ms={:.3} max_ms={:.3}", summary.count, summary.mean_ms, summary.stddev_ms, summary.p50_ms, summary.p95_ms, summary.p99_ms, summary.max_ms);
    } else {
        println!("bridge_ack_latency_summary count=0; no acknowledgements were verified");
    }
    println!("bridge_probe=stopped; named MEM1 view unmapped");
    Ok(())
}

#[cfg(windows)]
fn run_sync_client(
    config_path: &std::path::Path,
    pid: u32,
    address: std::net::SocketAddr,
    player: sync_net::PlayerId,
) -> Result<(), String> {
    const EXPECTED_HANDLER_SHA256: &str =
        "2b2ff0716aeb9b806cc61181e559041312957f25f04c435bd7da80a78917b33b";
    let config_text = fs::read_to_string(config_path)
        .map_err(|e| format!("cannot read development config: {e}"))?;
    let config = dev_config::parse(&config_text)?;
    validate_dev_inputs(&config)?;
    let pal_dol = fs::read(&config.pal_dol).map_err(|e| format!("cannot read PAL DOL: {e}"))?;
    let pal_symbols =
        fs::read(&config.pal_symbols).map_err(|e| format!("cannot read PAL symbols: {e}"))?;
    player_probe::verify_pal_inputs(&pal_dol, &pal_symbols)?;
    let dolphin = config
        .dolphin
        .canonicalize()
        .map_err(|e| format!("cannot resolve configured Dolphin: {e}"))?;
    let process_image = startup::process_image_path(pid)?
        .canonicalize()
        .map_err(|e| format!("cannot resolve Dolphin PID {pid} image path: {e}"))?;
    if !process_image
        .to_string_lossy()
        .eq_ignore_ascii_case(&dolphin.to_string_lossy())
    {
        return Err(format!(
            "PID {pid} belongs to {}, expected configured stock Dolphin {}",
            process_image.display(),
            dolphin.display()
        ));
    }
    let handler_path = dolphin
        .parent()
        .unwrap_or_else(|| std::path::Path::new("."))
        .join("Sys")
        .join("codehandler.bin");
    let handler = fs::read(&handler_path).map_err(|e| {
        format!(
            "cannot read Dolphin Gecko handler {}: {e}",
            handler_path.display()
        )
    })?;
    let handler_hash = sha256(&handler)
        .iter()
        .map(|v| format!("{v:02x}"))
        .collect::<String>();
    if handler.len() != 2880 || handler_hash != EXPECTED_HANDLER_SHA256 {
        return Err(format!("Dolphin handler does not match the validated 2609a fingerprint (size={}, sha256={handler_hash})", handler.len()));
    }
    let view = stock_memory::StockMem1::open_dolphin_read_only(pid)
        .map_err(|e| format!("cannot open Dolphin PID {pid} MEM1 read-only: {e:?}"))?;
    let identity_deadline = Instant::now() + Duration::from_secs(30);
    loop {
        let id = view.read_guest(0x8000_0000, 6)?;
        if id == b"RMGP01" {
            break;
        }
        if bridge_identity::classify_game_id(id.as_slice().try_into().unwrap_or([0; 6]))
            == bridge_identity::IdentityStatus::WrongGame
        {
            return Err(format!(
                "Dolphin PID {pid} is not PAL RMGP01: {}",
                bridge_identity::describe_game_id(id.try_into().unwrap_or([0; 6]))
            ));
        }
        if Instant::now() >= identity_deadline {
            return Err(format!(
                "timed out waiting for RMGP01 in Dolphin PID {pid}; observed_game_id={}",
                bridge_identity::describe_game_id(id.try_into().unwrap_or([0; 6]))
            ));
        }
        thread::sleep(Duration::from_millis(100));
    }
    let handler_addr_len = (gecko_bridge::HANDLER_END - gecko_bridge::HANDLER_BASE) as usize;
    let mailbox_deadline = Instant::now() + Duration::from_secs(60);
    let mailbox = loop {
        let bytes = view.read_guest(gecko_bridge::HANDLER_BASE, handler_addr_len)?;
        match gecko_bridge::locate_mailbox(gecko_bridge::HANDLER_BASE, handler.len(), &bytes)? {
            Some(mailbox) => break mailbox,
            None if Instant::now() < mailbox_deadline => thread::sleep(Duration::from_millis(250)),
            None => {
                return Err(format!(
                    "timed out waiting for validated Gecko mailbox in Dolphin PID {pid}"
                ))
            }
        }
    };
    println!("sync_local=validated pid={pid} player={} game=RMGP01 revision=0 mailbox=0x{:08x} mapping=read_only", player.name(), mailbox.snapshot_address());

    struct Remote {
        frame: sync_net::Frame,
        received: Instant,
        updates: u64,
        rate_started: Instant,
    }
    let mut sequence = 0u64;
    let mut last_observed_counter = None;
    let mut last_local_status: Option<&'static str> = None;
    let mut next_process_check = Instant::now();
    loop {
        let mut stream = match sync_net::connect_local(address) {
            Ok(stream) => stream,
            Err(error) => {
                println!("sync_network=retry player={} reason={error}", player.name());
                thread::sleep(Duration::from_secs(1));
                continue;
            }
        };
        sync_net::write_frame(
            &mut stream,
            &sync_net::Frame::Hello {
                player,
                game_id: *b"RMGP01",
                revision: 0,
                dolphin_pid: pid,
            },
        )?;
        let mut reader = stream
            .try_clone()
            .map_err(|e| format!("clone sync socket: {e}"))?;
        let (event_tx, event_rx) = std::sync::mpsc::channel::<Result<sync_net::Frame, String>>();
        thread::spawn(move || loop {
            match sync_net::read_frame(&mut reader) {
                Ok(frame) => {
                    if event_tx.send(Ok(frame)).is_err() {
                        break;
                    }
                }
                Err(error) => {
                    let _ = event_tx.send(Err(error));
                    break;
                }
            }
        });
        println!(
            "sync_network=connected server={address} player={}",
            player.name()
        );
        let mut remote: HashMap<sync_net::PlayerId, Remote> = HashMap::new();
        let mut last_sequence: HashMap<sync_net::PlayerId, u64> = HashMap::new();
        let mut next_sample = Instant::now();
        let mut next_report = Instant::now() + Duration::from_secs(1);
        let mut disconnected = None;
        while disconnected.is_none() {
            while let Ok(event) = event_rx.try_recv() {
                match event {
                    Err(error) => {
                        disconnected = Some(error);
                        break;
                    }
                    Ok(sync_net::Frame::PeerState {
                        player: remote_id,
                        sequence: remote_seq,
                        timestamp_ms,
                        counter,
                        x,
                        y,
                        z,
                    }) => {
                        if remote_id == player
                            || !sync_net::fresh_timestamp(timestamp_ms, sync_net::now_ms())
                            || last_sequence
                                .get(&remote_id)
                                .is_some_and(|last| remote_seq <= *last)
                            || !sync_net::valid_position(x, y, z)
                        {
                            disconnected = Some(format!("rejected stale, malformed, self, or out-of-order peer snapshot from {}", remote_id.name()));
                            break;
                        }
                        last_sequence.insert(remote_id, remote_seq);
                        let entry = remote.entry(remote_id).or_insert(Remote {
                            frame: sync_net::Frame::PeerState {
                                player: remote_id,
                                sequence: remote_seq,
                                timestamp_ms,
                                counter,
                                x,
                                y,
                                z,
                            },
                            received: Instant::now(),
                            updates: 0,
                            rate_started: Instant::now(),
                        });
                        entry.frame = sync_net::Frame::PeerState {
                            player: remote_id,
                            sequence: remote_seq,
                            timestamp_ms,
                            counter,
                            x,
                            y,
                            z,
                        };
                        entry.received = Instant::now();
                        entry.updates += 1;
                    }
                    Ok(sync_net::Frame::PeerGone { player: remote_id }) => {
                        if remote_id == player {
                            disconnected = Some("server reported this client disconnected".into());
                            break;
                        }
                        if remote.remove(&remote_id).is_some() {
                            println!("sync_remote=gone player={}", remote_id.name());
                        }
                        last_sequence.remove(&remote_id);
                    }
                    Ok(sync_net::Frame::Reject { code, .. }) => {
                        return Err(match code {
                            1 => "server rejected duplicate player ID".into(),
                            2 => "server lobby already has two players".into(),
                            3 => "server rejected both clients attaching to the same Dolphin PID"
                                .into(),
                            _ => format!("server rejected client (code {code})"),
                        });
                    }
                    Ok(_) => {
                        disconnected =
                            Some("server sent a client-only or malformed message".into());
                        break;
                    }
                }
            }
            if disconnected.is_some() {
                break;
            }

            if Instant::now() >= next_sample {
                next_sample = Instant::now() + Duration::from_millis(16);
                if Instant::now() >= next_process_check {
                    let current_image = startup::process_image_path(pid)?
                        .canonicalize()
                        .map_err(|e| format!("cannot resolve Dolphin PID {pid} image: {e}"))?;
                    if !current_image
                        .to_string_lossy()
                        .eq_ignore_ascii_case(&dolphin.to_string_lossy())
                    {
                        return Err(format!(
                            "Dolphin PID {pid} changed image to {}; stopping sync client",
                            current_image.display()
                        ));
                    }
                    next_process_check = Instant::now() + Duration::from_secs(1);
                }
                let game_id: [u8; 6] = view.read_guest(0x8000_0000, 6)?.try_into().unwrap();
                if game_id != *b"RMGP01" {
                    return Err(format!(
                        "Dolphin PID {pid} game identity changed to {}",
                        bridge_identity::describe_game_id(game_id)
                    ));
                }
                if Instant::now() >= next_report {
                    let bytes = view.read_guest(gecko_bridge::HANDLER_BASE, handler_addr_len)?;
                    if !gecko_bridge::snapshot_matches_mailbox(
                        gecko_bridge::HANDLER_BASE,
                        handler.len(),
                        &bytes,
                        mailbox,
                    )? {
                        return Err(
                            "Dolphin Gecko mailbox signature changed; stopping sync client".into(),
                        );
                    }
                }
                let counter = view.read_guest_word(mailbox.counter_address())?;
                let heartbeat_advanced = last_observed_counter.is_some_and(|last| last != counter);
                last_observed_counter = Some(counter);
                let bytes = view.read_guest(mailbox.snapshot_address(), 12 * 4)?;
                let sequence_after = view.read_guest_word(mailbox.snapshot_address() + 20)?;
                let counter_after = view.read_guest_word(mailbox.counter_address())?;
                let snapshot = gecko_bridge::decode_snapshot(&bytes, sequence_after)?;
                match snapshot {
                    Ok(sample)
                        if heartbeat_advanced
                            && sample.counter == counter
                            && counter_after == counter =>
                    {
                        if last_local_status != Some("live") {
                            println!(
                                "sync_local=position_ready player={} counter={}",
                                player.name(),
                                sample.counter
                            );
                        }
                        last_local_status = Some("live");
                        sequence = sequence.wrapping_add(1).max(1);
                        let frame = sync_net::snapshot_frame(player, sequence, sample);
                        if let Err(error) = sync_net::write_frame(&mut stream, &frame) {
                            disconnected = Some(error);
                        }
                    }
                    Ok(_) => {
                        if last_local_status != Some("waiting") {
                            println!("sync_local=waiting player={} reason=awaiting_new_frame_or_consistent_snapshot", player.name());
                        }
                        last_local_status = Some("waiting");
                    }
                    Err(gecko_bridge::SnapshotState::Unavailable) => {
                        if last_local_status != Some("unavailable") {
                            println!("sync_local=unavailable player={} reason=mario_holder_or_actor_not_ready", player.name());
                        }
                        last_local_status = Some("unavailable");
                    }
                    Err(gecko_bridge::SnapshotState::Inconsistent) => {
                        if last_local_status != Some("waiting") {
                            println!(
                                "sync_local=waiting player={} reason=seqlock_inconsistent",
                                player.name()
                            );
                        }
                        last_local_status = Some("waiting");
                    }
                    Err(gecko_bridge::SnapshotState::Invalid) => {
                        if last_local_status != Some("invalid") {
                            println!("sync_local=invalid player={} reason=actor_or_coordinate_validation_failed", player.name());
                        }
                        last_local_status = Some("invalid");
                    }
                }
            }
            let now = Instant::now();
            let stale: Vec<_> = remote
                .iter()
                .filter_map(|(id, state)| {
                    (now.duration_since(state.received) > Duration::from_secs(1)).then_some(*id)
                })
                .collect();
            for id in stale {
                remote.remove(&id);
                println!(
                    "sync_remote=expired player={} reason=no_fresh_position",
                    id.name()
                );
            }
            if now >= next_report {
                for (id, state) in &mut remote {
                    let elapsed = state.rate_started.elapsed().as_secs_f64().max(0.001);
                    let hz = state.updates as f64 / elapsed;
                    state.updates = 0;
                    state.rate_started = Instant::now();
                    if let sync_net::Frame::PeerState {
                        x,
                        y,
                        z,
                        sequence: peer_seq,
                        ..
                    } = state.frame
                    {
                        println!("sync_remote=position player={} seq={} x={x:.4} y={y:.4} z={z:.4} age_ms={} update_hz={hz:.1}", id.name(), peer_seq, state.received.elapsed().as_millis());
                    }
                }
                next_report = now + Duration::from_secs(1);
            }
            thread::sleep(Duration::from_millis(2));
        }
        println!(
            "sync_network=disconnected player={} reason={}; reconnecting in 1s",
            player.name(),
            disconnected.unwrap_or_default()
        );
        remote.clear();
        last_sequence.clear();
        thread::sleep(Duration::from_secs(1));
    }
}

#[cfg(not(windows))]
fn run_sync_client(
    _config_path: &std::path::Path,
    _pid: u32,
    _address: std::net::SocketAddr,
    _player: sync_net::PlayerId,
) -> Result<(), String> {
    Err("sync-client requires official stock Dolphin 2609a on Windows".into())
}

fn run() -> Result<(), String> {
    let args: Vec<_> = env::args_os().skip(1).collect();
    if args.len() == 3 && args[0] == "observe-penguins" {
        let pid = args[2]
            .to_str()
            .ok_or("Dolphin PID must be valid UTF-8")?
            .parse::<u32>()
            .map_err(|_| "Dolphin PID must be a positive integer")?;
        if pid == 0 {
            return Err("Dolphin PID must be positive".into());
        }
        return run_observe_penguins(std::path::Path::new(&args[1]), pid);
    }
    if args.len() == 3 && args[0] == "observe-heaps" {
        let pid = args[2]
            .to_str()
            .ok_or("Dolphin PID must be valid UTF-8")?
            .parse::<u32>()
            .map_err(|_| "Dolphin PID must be a positive integer")?;
        if pid == 0 {
            return Err("Dolphin PID must be positive".into());
        }
        return run_observe_heaps(std::path::Path::new(&args[1]), pid);
    }
    if args.len() == 3 && args[0] == "observe-mario-model" {
        let pid = args[2]
            .to_str()
            .ok_or("Dolphin PID must be valid UTF-8")?
            .parse::<u32>()
            .map_err(|_| "Dolphin PID must be a positive integer")?;
        if pid == 0 {
            return Err("Dolphin PID must be positive".into());
        }
        return run_observe_mario_model(std::path::Path::new(&args[1]), pid);
    }
    if args.len() == 3 && args[0] == "observe-resource-holder" {
        let pid = args[2]
            .to_str()
            .ok_or("Dolphin PID must be valid UTF-8")?
            .parse::<u32>()
            .map_err(|_| "Dolphin PID must be a positive integer")?;
        if pid == 0 {
            return Err("Dolphin PID must be positive".into());
        }
        return run_observe_resource_holder(std::path::Path::new(&args[1]), pid);
    }
    if args.len() == 3 && args[0] == "observe-model-joints" {
        let pid = args[2]
            .to_str()
            .ok_or("Dolphin PID must be valid UTF-8")?
            .parse::<u32>()
            .map_err(|_| "Dolphin PID must be a positive integer")?;
        if pid == 0 {
            return Err("Dolphin PID must be positive".into());
        }
        return run_observe_model_joints(std::path::Path::new(&args[1]), pid);
    }
    if args.len() == 3 && args[0] == "verify-hook" {
        let pid = args[2]
            .to_str()
            .ok_or("Dolphin PID must be valid UTF-8")?
            .parse::<u32>()
            .map_err(|_| "Dolphin PID must be a positive integer")?;
        if pid == 0 {
            return Err("Dolphin PID must be positive".into());
        }
        return run_verify_hook(std::path::Path::new(&args[1]), pid);
    }
    if args.len() == 4 && args[0] == "patch-image" {
        return run_patch_image(
            std::path::Path::new(&args[1]),
            std::path::Path::new(&args[2]),
            std::path::Path::new(&args[3]),
        );
    }
    if args.len() == 3 && args[0] == "observe-scheduler" {
        let pid = args[2]
            .to_str()
            .ok_or("Dolphin PID must be valid UTF-8")?
            .parse::<u32>()
            .map_err(|_| "Dolphin PID must be a positive integer")?;
        if pid == 0 {
            return Err("Dolphin PID must be positive".into());
        }
        return run_observe_scheduler(std::path::Path::new(&args[1]), pid);
    }
    if args.len() == 3 && args[0] == "observe-callback-lists" {
        let pid = args[2]
            .to_str()
            .ok_or("Dolphin PID must be valid UTF-8")?
            .parse::<u32>()
            .map_err(|_| "Dolphin PID must be a positive integer")?;
        if pid == 0 {
            return Err("Dolphin PID must be positive".into());
        }
        return run_observe_callback_lists(std::path::Path::new(&args[1]), pid);
    }
    if (args.len() == 5 || args.len() == 6) && args[0] == "inspect-memory" {
        let pid = args[2]
            .to_str()
            .ok_or("Dolphin PID must be valid UTF-8")?
            .parse::<u32>()
            .map_err(|_| "Dolphin PID must be a positive integer")?;
        if pid == 0 {
            return Err("Dolphin PID must be positive".into());
        }
        let address = u32::from_str_radix(
            args[3]
                .to_str()
                .ok_or("guest address must be valid UTF-8")?
                .trim_start_matches("0x")
                .trim_start_matches("0X"),
            16,
        )
        .map_err(|_| "guest address must be a hexadecimal 32-bit address")?;
        let length = args[4]
            .to_str()
            .ok_or("read length must be valid UTF-8")?
            .parse::<usize>()
            .map_err(|_| "read length must be a positive decimal integer")?;
        let format = if args.len() == 6 {
            memory_inspection::ValueFormat::parse(
                args[5].to_str().ok_or("read format must be valid UTF-8")?,
            )?
        } else {
            memory_inspection::ValueFormat::Bytes
        };
        return run_inspect_memory(std::path::Path::new(&args[1]), pid, address, length, format);
    }
    if args.len() == 2 && args[0] == "sync-server" {
        let address = args[1]
            .to_str()
            .ok_or("server address must be valid UTF-8")?
            .parse::<std::net::SocketAddr>()
            .map_err(|_| "server address must be an IP:port socket address")?;
        return sync_net::run_server(address);
    }
    if args.len() == 5 && args[0] == "sync-client" {
        let pid = args[2]
            .to_str()
            .ok_or("Dolphin PID must be valid UTF-8")?
            .parse::<u32>()
            .map_err(|_| "Dolphin PID must be a positive integer")?;
        let address = args[3]
            .to_str()
            .ok_or("server address must be valid UTF-8")?
            .parse::<std::net::SocketAddr>()
            .map_err(|_| "server address must be an IP:port socket address")?;
        let player =
            sync_net::PlayerId::parse(args[4].to_str().ok_or("player ID must be valid UTF-8")?)?;
        return run_sync_client(std::path::Path::new(&args[1]), pid, address, player);
    }
    if args.len() == 4 && args[0] == "bench-dolphin" {
        let pid = args[1]
            .to_str()
            .ok_or("Dolphin PID must be valid UTF-8")?
            .parse::<u32>()
            .map_err(|_| "Dolphin PID must be a positive integer")?;
        let seconds = args[2]
            .to_str()
            .ok_or("duration must be valid UTF-8")?
            .parse::<u64>()
            .map_err(|_| "duration must be an integer number of seconds")?;
        let condition = args[3].to_str().ok_or("condition must be valid UTF-8")?;
        if !perf_bench::valid_condition(condition) {
            return Err(
                "condition must be normal, gdb-unattached, gdb-idle, gdb-sampling, stock-bridge-off, or stock-bridge-on".into(),
            );
        }
        println!("benchmark condition={condition} pid={pid} duration_s={seconds}; read-only process CPU sampling starts now");
        let samples = perf_bench::sample(pid, seconds)?;
        let average_cpu = samples
            .iter()
            .map(|sample| sample.total_cpu_percent)
            .sum::<f64>()
            / samples.len() as f64;
        println!("Enter OSD averages as: SpeedPercent FPS VPS (or press Enter if unavailable)");
        let mut metrics = String::new();
        std::io::stdin()
            .read_line(&mut metrics)
            .map_err(|e| format!("cannot read OSD metrics: {e}"))?;
        let values: Vec<_> = metrics.split_whitespace().collect();
        if !values.is_empty() && values.len() != 3 {
            return Err("enter exactly three values: SpeedPercent FPS VPS".into());
        }
        let (speed, fps, vps) = if values.is_empty() {
            (String::new(), String::new(), String::new())
        } else {
            for value in &values {
                value
                    .parse::<f64>()
                    .map_err(|_| format!("invalid numeric OSD value {value:?}"))?;
            }
            (
                values[0].to_owned(),
                values[1].to_owned(),
                values[2].to_owned(),
            )
        };
        println!(
            "timestamp_unix_ms,condition,pid,duration_seconds,avg_cpu_percent,speed_percent,fps,vps"
        );
        println!(
            "{},{},{},{},{:.3},{},{},{}",
            timestamp_unix_ms(),
            condition,
            pid,
            seconds,
            average_cpu,
            speed,
            fps,
            vps
        );
        return Ok(());
    }
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
            false,
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
        let mut gdb_unattached = false;
        let mut mute = true;
        let mut gecko_bridge_probe = false;
        let mut trampoline_dry_run = false;
        let mut patched_image: Option<String> = None;
        let mut bridge_startup_timeout = Duration::from_secs(60);
        let mut custom_bridge_timeout = false;
        let mut option_index = 4usize;
        while option_index < args.len() {
            let option_text = args[option_index]
                .to_str()
                .ok_or("dev-launch option must be valid UTF-8")?;
            if let Some(timeout) = bridge_identity::parse_timeout_option(option_text)? {
                if custom_bridge_timeout {
                    return Err("bridge startup timeout may be specified only once".into());
                }
                bridge_startup_timeout = timeout;
                custom_bridge_timeout = true;
                option_index += 1;
                continue;
            }
            match option_text {
                "--debug" if !debug && !gdb_unattached => debug = true,
                "--debug-unattached" if !debug && !gdb_unattached => gdb_unattached = true,
                "--unmute" if mute => mute = false,
                "--gecko-bridge-probe" if !gecko_bridge_probe => gecko_bridge_probe = true,
                "--trampoline-dry-run" if !trampoline_dry_run => trampoline_dry_run = true,
                "--patched-image" => {
                    let Some(value) = args.get(option_index + 1) else {
                        return Err("--patched-image requires a path".into());
                    };
                    if patched_image.is_some() {
                        return Err("--patched-image may be specified only once".into());
                    }
                    patched_image = Some(value.to_string_lossy().into_owned());
                    option_index += 1;
                }
                _ => {
                    return Err(
                        "dev-launch options: [--debug | --debug-unattached] [--unmute] [--gecko-bridge-probe] [--trampoline-dry-run] [--patched-image <path>] [--bridge-startup-timeout-secs=5..300]".into(),
                    )
                }
            }
            option_index += 1;
        }
        if gecko_bridge_probe && (debug || gdb_unattached) {
            return Err("--gecko-bridge-probe uses stock Dolphin's named MEM1 mapping and must run without --debug or --debug-unattached".into());
        }
        if trampoline_dry_run && (debug || gdb_unattached) {
            return Err(
                "--trampoline-dry-run is a stock-Dolphin Gecko path and must run without --debug or --debug-unattached".into(),
            );
        }
        if trampoline_dry_run && gecko_bridge_probe {
            return Err(
                "--trampoline-dry-run cannot be combined with --gecko-bridge-probe in Stage 1"
                    .into(),
            );
        }
        if custom_bridge_timeout && !gecko_bridge_probe {
            return Err(
                "--bridge-startup-timeout-secs is only valid with --gecko-bridge-probe".into(),
            );
        }
        let gdb_enabled = debug || gdb_unattached;
        if gdb_enabled && (port == 0 || !port_is_available(port)) {
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
        let symbols = fs::read(&config.pal_symbols)
            .map_err(|e| format!("cannot read Petari symbols: {e}"))?;
        if trampoline_dry_run {
            hook_trampoline::verify_hook_layout(&dol, &symbols)?;
        }
        let patch = controller_patch::prepare_allstars(&config.patch_folder, &dol)?;
        if trampoline_dry_run {
            let writes = parse_onframe_patch(&patch.settings)
                .map_err(|e| format!("invalid prepared AllStars patch: {e}"))?;
            hook_trampoline::ensure_hook_disjoint(&writes)?;
        }
        let probe_map = if debug {
            Some(player_probe::verify_pal_inputs(&dol, &symbols)?)
        } else {
            None
        };
        configure_isolated_profile(
            &user_dir,
            &patch,
            Some(&config.controller_profile),
            true,
            if gdb_enabled { Some(port) } else { None },
            mute,
            gecko_bridge_probe,
            trampoline_dry_run,
        )?;
        let game_path = if let Some(ref patched) = patched_image {
            let manifest =
                image_patch::validate_patched_image(std::path::Path::new(patched), &dol)?;
            println!(
                "patched image validated: patch_id={} output_image_sha256={}",
                manifest.patch_id, manifest.output_image_sha256
            );
            std::path::PathBuf::from(patched)
        } else {
            config.game.clone()
        };
        let mut child = spawn_game(
            &config.dolphin,
            &user_dir,
            &game_path,
            gdb_enabled,
            port,
            mute,
            &patch,
        )?;
        if trampoline_dry_run {
            println!("trampoline stage=dry-run; Gecko C0 installer arms 0x802B62E4 -> stub(bl 0x802A6B20; b 0x802B62E8); no duplicate draw, no j3dSys writes");
            #[cfg(windows)]
            {
                use std::time::Instant;
                // Diagnostic pre-flight: poll the hook word every ~500ms and
                // log the full value timeline (zero -> loader copy -> original
                // -> install -> corrupt) until the deadline. Boot includes the
                // Wii safety screen + intro and can exceed 30s, so allow 180s.
                // Pass only when the word is acceptable (original or verified
                // installed branch) AND unchanged across several consecutive
                // polls - not on a one-shot match.
                const PREFLIGHT_DEADLINE: Duration = Duration::from_secs(180);
                const STABLE_POLLS: usize = 6;
                let started = Instant::now();
                let deadline = started + PREFLIGHT_DEADLINE;
                let mut timeline: Vec<String> = Vec::new();
                let mut stable_run: Option<(u32, usize)> = None;
                loop {
                    let elapsed_ms = started.elapsed().as_millis();
                    if let Ok(Some(status)) = child.try_wait() {
                        child.kill().ok();
                        println!(
                            "trampoline pre-flight timeline ({} samples):",
                            timeline.len()
                        );
                        for line in &timeline {
                            println!("  {line}");
                        }
                        return Err(format!(
                            "Dolphin exited during pre-flight (status {status}); not retrying"
                        ));
                    }
                    let attempt = stock_memory::StockMem1::open_dolphin_read_only(child.id())
                        .map_err(|error| format!("{error:?}"))
                        .and_then(|mut memory| {
                            player_probe::verify_live_pal_signatures(&mut memory, &dol)?;
                            hook_trampoline::check_live(&mut memory)
                        });
                    match attempt {
                        Ok(report) => {
                            // Log the stub words at the decoded branch target on
                            // every sample where the word is a branch (acceptable
                            // or not) so Addendum 3's discriminator always fires.
                            let stub_note = match &report.stub_words {
                                Some(words) => format!(
                                    " stub=[{}] match={}",
                                    words
                                        .iter()
                                        .map(|w| format!("{w:08x}"))
                                        .collect::<Vec<_>>()
                                        .join(" "),
                                    words == &hook_trampoline::stub_template()
                                ),
                                None => String::new(),
                            };
                            let line = format!(
                                "t={:>6}ms hook_word=0x{:08x} {}{stub_note}",
                                elapsed_ms,
                                report.hook_word,
                                report.describe()
                            );
                            println!("trampoline preflight {line}");
                            timeline.push(line);
                            if report.is_acceptable() {
                                stable_run = Some(match stable_run {
                                    Some((word, count)) if word == report.hook_word => {
                                        (word, count + 1)
                                    }
                                    _ => (report.hook_word, 1),
                                });
                                if let Some((_, count)) = stable_run {
                                    if count >= STABLE_POLLS {
                                        println!(
                                            "trampoline pre-flight passed: {} stable for {STABLE_POLLS} polls",
                                            report.describe()
                                        );
                                        break;
                                    }
                                }
                            } else {
                                stable_run = None;
                            }
                        }
                        Err(error) => {
                            let line = format!("t={elapsed_ms:>6}ms open/signature error: {error}");
                            println!("trampoline preflight {line}");
                            timeline.push(line);
                            stable_run = None;
                        }
                    }
                    if Instant::now() >= deadline {
                        child.kill().ok();
                        println!(
                            "trampoline pre-flight timeline ({} samples):",
                            timeline.len()
                        );
                        for line in &timeline {
                            println!("  {line}");
                        }
                        return Err(
                            "trampoline pre-flight failed within 180s or word never stabilized, Dolphin stopped"
                                .into(),
                        );
                    }
                    // Bracket the boot window tightly: poll fast for the first
                    // ~10s (loader copy / installer arm happen there), then
                    // settle to 500ms.
                    let poll_ms = if started.elapsed() < Duration::from_secs(10) {
                        100
                    } else {
                        500
                    };
                    std::thread::sleep(Duration::from_millis(poll_ms));
                }
            }
        }
        if gecko_bridge_probe {
            println!("bridge stage=stock-mem1; Dolphin is running without GDB; opening a bounded read/write view of its 2609a MEM1 mapping");
            log_process_windows(child.id());
            #[cfg(windows)]
            run_interactive_gecko_bridge_probe(
                &mut child,
                &config.dolphin,
                bridge_startup_timeout,
            )?;
            #[cfg(not(windows))]
            return Err("the stock Dolphin named-MEM1 bridge prototype currently supports Windows Dolphin 2609a only".into());
        } else if debug {
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
            if gdb_unattached {
                println!("debug stage=unattached; GDB is listening on port {port}, but Dolphin is paused waiting for its single GDB client. This condition cannot produce comparable running-game FPS.");
            }
            drop(child);
        }
        return Ok(());
    }
    if args.len() == 3 && args[0] == "validate-mario-map" {
        let dol = fs::read(&args[1]).map_err(|e| format!("cannot read PAL main.dol: {e}"))?;
        let symbols = fs::read(&args[2]).map_err(|e| format!("cannot read Petari symbols: {e}"))?;
        let map = player_probe::verify_pal_inputs(&dol, &symbols)?;
        mario_model_observer::verify_pal_layout(&dol, &symbols)?;
        heap_observer::verify_pal_heap_layout(&dol, &symbols)?;
        scheduler_observer::verify_pal_layout(&dol, &symbols)?;
        callback_observer::verify_pal_layout(&dol, &symbols)?;
        println!("PAL RMGP01 Rev 0 DOL and Petari symbol file fingerprints verified.");
        println!(
            "PAL MarioActor -> ModelManager -> J3DModelX/model-data/matrix-buffer chain verified."
        );
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
        println!(
            "PAL JKRHeap current-heap global and HeapMemoryWatcher scene heap layout verified."
        );
        println!("PAL NameObjExecuteHolder constructor, 4096 capacity, 12-byte entry stride, unchecked registration path, SceneObj slot, and scene access signatures verified.");
        println!("PAL scene executor, callback category layouts, draw-buffer group table, and Penguin scheduler paths verified.");
        return Ok(());
    }
    if args.len() == 3 && args[0] == "inspect-render-prototype" {
        let dol = fs::read(&args[1]).map_err(|e| format!("cannot read PAL main.dol: {e}"))?;
        let symbols = fs::read(&args[2]).map_err(|e| format!("cannot read Petari symbols: {e}"))?;
        player_probe::verify_pal_inputs(&dol, &symbols)?;
        let calls = multigalaxy_patch::audit_pal_shared_model_calls(&dol)
            .map_err(|error| error.to_string())?;
        println!(
            "PAL RMGP01 Rev 0 shared-model render callsites: {} verified",
            calls.len()
        );
        for call in calls {
            println!(
                "{}: PC=0x{:08x} instruction=0x{:08x} target=0x{:08x}",
                call.name, call.address, call.instruction, call.target
            );
        }
        println!("Audit only: no hook bytes, Gecko codes, or executable patch were generated.");
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
            false,
            false,
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
        return Err("usage: multigalaxy inspect-render-prototype <PAL-main.dol> <Petari-symbols.txt> | multigalaxy observe-heaps <development.local.toml> <Dolphin-pid> | multigalaxy observe-mario-model <development.local.toml> <Dolphin-pid> | multigalaxy observe-resource-holder <development.local.toml> <Dolphin-pid> | multigalaxy observe-model-joints <development.local.toml> <Dolphin-pid> | multigalaxy verify-hook <development.local.toml> <Dolphin-pid> | multigalaxy patch-image <development.local.toml> <source-image> <output-image> | multigalaxy observe-scheduler <development.local.toml> <Dolphin-pid> | multigalaxy observe-callback-lists <development.local.toml> <Dolphin-pid> | multigalaxy observe-penguins <development.local.toml> <Dolphin-pid> | multigalaxy inspect-memory <development.local.toml> <Dolphin-pid> <guest-hex-address> <length> [bytes|u32|pointer|f32] | multigalaxy sync-server <127.0.0.1:port> | multigalaxy sync-client <development.local.toml> <Dolphin-pid> <127.0.0.1:port> <Mario|Luigi> | multigalaxy dev-setup <development.local.toml> <fresh-user-dir> | multigalaxy dev-launch <development.local.toml> <prepared-user-dir> <gdb-port-or-0> [--debug | --debug-unattached] [--unmute] [--gecko-bridge-probe] [--trampoline-dry-run] [--patched-image <path>] [--bridge-startup-timeout-secs=5..300] | multigalaxy bench-dolphin <pid> <seconds> <normal|gdb-unattached|gdb-idle|gdb-sampling|stock-bridge-off|stock-bridge-on> | multigalaxy launch <Dolphin.exe> <fresh-user-dir> <game.rvz> <gdb-port-or-0> <AllStars-patch-folder> <PAL-main.dol> [--mute] [--gdb|--no-gdb] | multigalaxy validate-pal-game <Dolphin.exe> <game.rvz> | multigalaxy inspect-allstars <patch-folder> <PAL-main.dol> | multigalaxy readmem <port> <guest-hex-address> <length> | multigalaxy validate-mario-map <PAL-main.dol> <Petari-symbols.txt> | multigalaxy probe-mario <port> <PAL-main.dol> <Petari-symbols.txt> [interval-ms] | multigalaxy verify-loaded <port> <main.dol> | multigalaxy verify-loaded-full <port> <main.dol>".into());
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
            false,
            false,
        )
        .unwrap();
        let dolphin_ini = fs::read_to_string(user.join("Config/Dolphin.ini")).unwrap();
        let gfx_ini = fs::read_to_string(user.join("Config/GFX.ini")).unwrap();
        let game_ini = fs::read_to_string(user.join("GameSettings/RMGP01.ini")).unwrap();
        let copied_profile =
            fs::read(user.join("Config/Profiles/Wiimote/Pro Con to Classic Con.ini")).unwrap();
        assert!(dolphin_ini.contains("GFXBackend = D3D12"));
        assert!(dolphin_ini.contains("GDBPort = 55221"));
        assert!(gfx_ini.contains("ShowFPS = True"));
        assert!(gfx_ini.contains("ShowVPS = True"));
        assert!(gfx_ini.contains("ShowSpeed = True"));
        assert!(game_ini.contains("[OnFrame_Enabled]"));
        assert!(game_ini.contains("WiimoteSource0 = 1"));
        assert!(game_ini.contains("WiimoteProfile1 = Pro Con to Classic Con"));
        assert_eq!(copied_profile, profile_bytes);
        assert_eq!(fs::read(&source_profile).unwrap(), profile_bytes);
        configure_isolated_profile(
            &user,
            &patch,
            Some(&source_profile),
            true,
            Some(55221),
            true,
            true,
            false,
        )
        .unwrap();
        let probe_ini = fs::read_to_string(user.join("GameSettings/RMGP01.ini")).unwrap();
        assert!(probe_ini.contains("[OnFrame_Enabled]"));
        assert!(probe_ini.contains("AllStars"));
        assert!(probe_ini.contains("[Gecko_Enabled]"));
        assert!(probe_ini.contains("MultiGalaxy1 Stock Dolphin Mailbox Probe"));
        // The trampoline variant must not contain the stub; the plain probe must.
        assert!(!probe_ini.contains("4E800421"));
        configure_isolated_profile(
            &user,
            &patch,
            Some(&source_profile),
            true,
            Some(55221),
            true,
            false,
            true,
        )
        .unwrap();
        let trampoline_ini = fs::read_to_string(user.join("GameSettings/RMGP01.ini")).unwrap();
        assert!(trampoline_ini.contains("[Gecko_Enabled]"));
        // The Stage 1 installer path emits the C0 block containing the stub's
        // bctrl/bctr pair, proving the launch config invokes the installer.
        assert!(
            trampoline_ini.contains("4E800421 4E800420")
                || trampoline_ini.contains("4E800421\n")
                || trampoline_ini.lines().any(|line| line.contains("4E800421"))
        );
        let expected_pairs = gecko_bridge::payload_trampoline_word_count_for_test() / 2;
        assert!(trampoline_ini.contains(&format!("C0000000 {expected_pairs:08X}")));
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
