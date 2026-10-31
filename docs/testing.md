# Testing and evidence

## Automated checks

Run `cargo fmt --all`, `cargo test --workspace`, and `cargo build --workspace`. Unit tests cover DOL parsing/revision checks, mandatory patch composition, bounded GDB requests, persistent continue-then-read RSP traffic, PAL pointer-chain decoding, and local development config parsing.

The live Dolphin integration test is opt-in. It validates the live disc identity and DOL memory samples; it does not establish player coordinates or gameplay.

## Current PAL evidence

The current `reference/original/main.dol` is the user-supplied PAL build with SHA-1 `9bd1daa520bd1f573b6a4b82e03336e9c51b511f`. It matches Petari's `config/RMGP01/build.sha1` and the live Dolphin `RMGP01` identity. Sampled validation compares 16 bytes at each DOL section's start, midpoint, and end (deduplicated); `verify-loaded-full` performs the slower complete section comparison.

The Petari-backed pointer chain and `MarioActor::mPosition` offset are statically checked against this PAL DOL and symbol map. The sampler validates `RMGP01`, keeps one GDB connection, sends the GDB `c` continue command, and reads the verified chain while Dolphin runs. It reports timestamped X/Y/Z values or a waiting state. No live actor pointer or position changes have yet been observed during controlled movement.

On 2026-10-30, Dolphin 2609a listened on `0.0.0.0:55221` from the isolated GDB profile. The Rust sampler attached, read live `RMGP01`, sent `c`, and completed repeated 200 ms memory reads while the emulated CPU ran. The active scene returned `MarioHolder has not been created in this scene`; this verifies the resume/read transport, not a Mario coordinate sample or controller input.

### Black-screen regression diagnosis

The render-window capture was taken from the window titled `Dolphin 2609a | JIT64 SC | Direct3D 12 | HLE | Super Mario Galaxy (RMGP01)`, not Dolphin's separate library window. With GDB enabled it was black before attachment; attaching and sending `c` changed the rendered frame to the Wii safety strap screen, and it remained rendered while reads continued. A normal launch using the same absolute isolated profile, D3D12 configuration, and AllStars patch displayed the title screen. This isolates the cause to GDB's pause-on-start state, not the Gecko patch or graphics backend. The new debug launch waits up to 15 seconds for GDB, checks the Dolphin child remains alive, validates `RMGP01`, and explicitly resumes. If any stage fails, it kills and reaps only the child it launched. The debug command itself owns the persistent GDB connection and sampler.

When a later PowerShell snapshot reported an empty `MainWindowHandle`, non-destructive reinspection found that Dolphin's actual command line was `Dolphin.exe -u <absolute isolated profile> -e <PAL RVZ>`, with no headless or UI suppression argument. Toolhelp identified its parent as the live `multigalaxy.exe` launcher. Stdout was empty, stderr contained only `Error creating key`, and the isolated `Logs/dolphin.log` was empty. GDB was still connected. `EnumWindows` then found visible, unminimized Dolphin GUI and SMG1 D3D12 render HWNDs owned by that Dolphin process; a fresh `Get-Process` query also returned a nonzero main handle. This did not reproduce a missing window and indicates the reported handle was a stale/transient process snapshot. The restarted debug launch with the new check logged a valid main GUI HWND; a second check found a separate visible SMG1 render HWND and an established GDB connection. The render capture showed the Wii safety screen, so playable gameplay is not verified. Windows denied foreground activation from the automation session even though the windows were visible. Startup logs the verified native HWND, restores it if minimized, and attempts foreground activation. A Windows test creates a temporary visible window and verifies enumeration returns a live visible handle belonging to the expected process.

## Manual debug movement test

Copy `development.local.example.toml` to ignored `development.local.toml` and put the user's local paths there. Create a fresh isolated test profile:

```powershell
.\target\debug\multigalaxy.exe dev-setup development.local.toml 'target\mg1-coordinate-manual'
```

In the isolated Dolphin window, use **Tools > Import Wii Save**, select the configured `wii_save`, wait for Dolphin's success confirmation, and close Dolphin. This is Dolphin's supported save-import UI; DolphinTool has no import command. The save source remains unchanged. `dev-setup` may be rerun for the same marked local profile to reopen its setup window; it refuses unmarked or differently configured directories.

Launch the muted debug game with D3D12, the isolated controller profile, and required PAL AllStars patch. Give it an absolute isolated user-directory path (the example resolves from the repository directory):

```powershell
.\target\debug\multigalaxy.exe dev-launch development.local.toml 'target\mg1-coordinate-manual' 55218 --debug
```

The command automatically attaches and resumes through GDB, then samples immediately. Type `STOP` to pause sampling, enter a playable scene using the configured controller, then type `START`; stand still for five seconds, walk for five seconds, and jump twice. Type `STOP`, then `Q`. Save timestamped output. `state=waiting` means a pointer in the verified chain was null; it is not a coordinate sample. Do not use a second `probe-mario` process against this Dolphin: Dolphin accepts only one GDB client.

Dolphin 2609a accepts one GDB client per process. If a read times out or the connection drops, the sampler exits with a restart instruction; restart the debug Dolphin process before reconnecting. The sampler never writes emulated memory.

## Verification levels

- AllStars activation: checked statically by source hashes, exact PAL DOL fingerprint, and isolated `GameSettings/RMGP01.ini` enable entries. Runtime controller behavior is unverified.
- Emulator startup: launcher checks process/window responsiveness and captures isolated logs. This is not proof of a rendered game frame.
- Game boot and save import: require visual confirmation in the isolated Dolphin profile.
- Coordinate extraction: requires real standing, walking, and jumping samples. Not yet verified.
- Multiplayer: not started; basic local gameplay and player-state extraction remain prerequisites.
