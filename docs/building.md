# Building and Dolphin test runs

Host tooling is a Cargo workspace. Run `cargo fmt --all`, `cargo test --workspace`, and `cargo build --workspace`. Revision inspection uses `cargo run -p multigalaxy -- identify <disc-header.bin> <main.dol>`.

## Production launcher

All launches require user-selected Dolphin, game, isolated user directory, the third-party SMG1 PAL AllStars patch folder, and the verified PAL Rev 0 `main.dol`. AllStars plus both shoulder swap files are mandatory and fingerprint-checked before Dolphin starts. Missing or altered patch files fail closed. Production launch does not select a graphics backend, controller profile, or save location; these remain user-controlled.

```powershell
cargo run -p multigalaxy -- launch '<Dolphin.exe>' '<fresh-user-dir>' '<game.rvz>' 0 '<SMG\PAL\Individual Patches>' '<PAL-main.dol>'
```

Add `--gdb` only for a debug session and `--mute` only for test/debug audio. Dolphin starts the emulated CPU paused when GDBPort is set. In debug mode the Rust launcher now attaches to that GDB stub automatically, verifies live game ID `RMGP01`, sends the protocol continue packet, then retains that single connection for the interactive sampler. It does not silently fall back to a non-debug launch. Dolphin 2609a accepts one client per process; if attach, identity validation, or continue fails, the launcher stops only the Dolphin process it started and reports the failed stage. Normal production launch leaves GDB disabled.

The launcher uses only `-u <isolated-user-dir>` and `-e <game>` with Dolphin's executable directory as its working directory. It writes its settings and logs only inside that user directory, checks for a visible responsive window for at most 15 seconds, reports bounded log tails on failure, and leaves an unresponsive process open for inspection. A responsive window does not prove game rendering or gameplay.

## Local Windows development profile

Copy `development.local.example.toml` to the git-ignored `development.local.toml` and set local paths. No Windows path from that local file is compiled into the launcher or used as a production default. The development-only commands below configure D3D12, install the selected Dolphin controller profile under the isolated `Config/Profiles/Wiimote` directory and select it through the game's `WiimoteProfile1` setting, enable AllStars, and keep debug audio muted. They never write to the normal Dolphin profile.

```powershell
cargo run -p multigalaxy -- dev-setup development.local.toml 'target\mg1-dev-profile'
```

`dev-setup` requires a fresh isolated directory and opens Dolphin without a game. In that window use **Tools > Import Wii Save**, choose the local `wii_save` path, wait for Dolphin's success confirmation, and close Dolphin. DolphinTool does not offer a Wii save-import command; the supported importer is this Dolphin UI action. The `.bin` save stays at its source path and Dolphin imports into only the isolated profile's NAND. The launcher does not claim import succeeded until you complete that UI step.

Start a debug game session after the setup window is closed:

```powershell
cargo run -p multigalaxy -- dev-launch development.local.toml 'target\mg1-dev-profile' 55221 --debug
```

This starts Dolphin with GDB enabled, D3D12, muted test audio, the selected controller profile, mandatory AllStars, and isolated Speed/FPS/VPS metrics. The same command automatically attaches, resumes the emulated CPU, and begins sampling on its persistent GDB connection. Type `STOP`/`START` to pause/resume sampling and `Q` to close the sampler. Do not start `probe-mario` separately for this Dolphin: its single GDB connection is owned by `dev-launch`. Use `--unmute` only if needed for a local test. `--debug-unattached` is a benchmark-only control: Dolphin waits for a GDB client with the CPU paused, so it cannot produce running gameplay FPS on 2609a.

Run `bench-dolphin <PID> <seconds> <normal|gdb-unattached|gdb-idle|gdb-sampling|bridge-enabled|bridge-disabled>` from a second PowerShell to collect process CPU and then enter the OSD Speed/FPS/VPS averages. See [performance.md](performance.md) for the controlled comparison and `bridge-prototype` synthetic shared-memory transport commands.

The preferred production bridge is an opt-in Dolphin source patch. Apply it only to a clean Dolphin 2609a source checkout using [integrate-2609a.ps1](../tools/dolphin-bridge/integrate-2609a.ps1), then build Dolphin from source. The MultiGalaxy1 launcher sets `MULTIGALAXY_BRIDGE_NAME=rmgp01` for launched emulator processes. After Dolphin boots the PAL game, observe snapshots with `cargo run -p multigalaxy -- bridge-watch rmgp01 60`. This path does not use GDB. For a development-only comparison with the same patched Dolphin, launch once normally and once with `dev-launch ... --bridge-disabled`; this disables only the bridge publisher, while AllStars remains enabled. The bridge is not runtime-verified until the patched Dolphin is built and launched; see [the patch notes](../tools/dolphin-bridge/README.md).

To test normal unpaused gameplay without GDB, omit `--debug`. Runtime verification remains split into separate claims: patch activation is statically checked in the isolated GameSettings file; Dolphin window/game boot requires visual confirmation; live X/Y/Z requires sampling during controlled movement and has not yet been confirmed.

## Validation and artifacts

`inspect-allstars <patch-folder> <PAL-main.dol>` validates the three exact upstream patch files and their composition without starting Dolphin. No third-party patch file, game executable, RVZ, save, or controller profile is committed. Keep generated profiles under `target/` or another git-ignored local directory.

On Windows, GUI launch requires the current process to be in the active console session on `WinSta0\Default`. The launcher compares `ProcessIdToSessionId`, `WTSGetActiveConsoleSessionId`/WTS user identity, the process window station, the current thread desktop, and `OpenInputDesktop`. It refuses to start Dolphin when the current desktop is an automation desktop (for example `CodexSandboxDesktop-*`) and prints an exact PowerShell command for the user to run from their interactive PowerShell window. Dolphin inherits the launcher's session and desktop; the launcher does not create detached or alternate-desktop processes. When launch is allowed, diagnostics include PID/session, each owned HWND, thread desktop, visibility, styles, parent, owner, and title. This is required because a window visible to a sandbox desktop will not appear in the user's Alt+Tab or Task View.

### Debug startup diagnosis (Dolphin 2609a)

The black viewport was reproduced with the same absolute isolated profile, PAL RVZ, D3D12 backend, and mandatory AllStars settings. The `GDBPort` difference was isolated: with GDB enabled, Dolphin's render window initialized and named `Super Mario Galaxy (RMGP01)`, but the viewport stayed black until the GDB client sent `c`. The persistent Rust session then displayed the Wii safety-strap screen and continued rendering during repeated memory reads. With only GDB disabled, the same isolated setup rendered the SMG1 title screen. Thus the pause-on-start behavior is the cause; neither AllStars nor D3D12 caused the black viewport. `dev-launch --debug` now has explicit `window-ready`, `gdb-attach`, `verified`, and `continued` stages to prevent that paused state from being mistaken for a rendering failure.
