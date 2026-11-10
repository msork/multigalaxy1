# Building and Dolphin test runs

Host tooling is a Cargo workspace. Run `cargo fmt --all -- --check`, `cargo test --workspace`, and `cargo build --workspace`.

## Official Dolphin only

Use an official, unmodified Dolphin installation and a game image you are entitled to use. MultiGalaxy does not build or alter Dolphin or DolphinTool and never edits the supplied game image in place. The separate ISO rebuild command below can create a new user-owned output image. The launcher takes explicit paths and writes emulator settings only under the selected isolated user directory.

Validate the PAL image read-only with the matching official DolphinTool:

```powershell
cargo run -p multigalaxy -- validate-pal-game '<official Dolphin.exe>' '<PAL game.rvz>'
```

Expected identity is PAL `RMGP01`, revision `0`. The launcher requires the third-party AllStars PAL patch files (including both shoulder swaps) and validates fingerprints before launch; those files are supplied externally and not bundled.

## Partition-aware PAL ISO rebuild (offline identity validation)

The legacy `patch-image` command scans plaintext and is not for Wii ISO/RVZ deployment. For an ISO, `rebuild-iso` uses official DolphinTool for identity validation and a separately installed Wiimms ISO Tool (`wit`) for partition-aware extraction and composition. WIT is GPL-2.0-or-later and is not bundled. This command currently applies only the five-word Stage 1 identity VCDIFF; it does not install the trampoline or change any game bytes. RVZ is not accepted by this command. It writes a new ISO and adjacent manifest, refuses existing outputs, and keeps temporary extraction files separate.

```powershell
cargo run --release -p multigalaxy -- rebuild-iso `
  '<official Dolphin install>\DolphinTool.exe' `
  '<Wiimms ISO Tools>\wit.exe' `
  '<user-owned PAL source.iso>' `
  '<new output path\MultiGalaxy1.iso>' `
  '<verified PAL main.dol>' `
  '<matching Petari PAL symbols.txt>'
```

The completed local test verifies PAL RMGP01 revision 0, the pinned extracted DOL, WIT partition integrity before/after, output DOL and game-file hashes, source immutability, and deterministic recomposition. It has **not** been launched in Dolphin. The manifest is not currently understood by `dev-launch --patched-image`; do not use that flag for this output. See [Wii image rebuild evidence](references/wii-image-rebuild.md).

## Local Windows development profile

Copy `development.local.example.toml` to the git-ignored `development.local.toml` and set local paths. Development-only D3D12, controller profile, save import, and mute configuration are written to a fresh isolated Dolphin user directory. The regular profile, save source, and original game image are not modified. `dev-setup` uses Dolphin's supported UI for save import.

Start a visible debug launch from an interactive PowerShell on `WinSta0\Default`:

```powershell
cargo run -p multigalaxy -- dev-launch development.local.toml 'target\mg1-dev-profile' 55221 --debug
```

Debug mode connects to one persistent GDB session, validates the live `RMGP01` identity, and resumes emulation. GDB is for development diagnostics only. Use `STOP`/`START` to pause/resume read sampling and `Q` to close it. `--debug-unattached` leaves emulation paused and is not a gameplay benchmark. A GDB read has not yet yielded verified moving Mario coordinates.

The benchmark command accepts `normal`, `gdb-unattached`, `gdb-idle`, and `gdb-sampling`. The `gdb-unattached` case is paused and not comparable to gameplay. Follow [performance.md](performance.md) for interactive paired runs. No numeric stock-GDB performance result is currently claimed.

## Stock bridge experiment

The launcher includes an opt-in, reversible mailbox experiment for official Dolphin 2609a. It enables a Gecko counter routine only in the selected isolated profile and reads Dolphin's version-specific named MEM1 mapping; it does not enable GDB. First use a fresh isolated profile already prepared by `dev-setup`, with external mandatory AllStars patch inputs configured. From interactive PowerShell on the visible Windows desktop, launch the baseline and then the probe (close Dolphin between runs):

```powershell
cargo run -p multigalaxy -- dev-launch development.local.toml 'target\mg1-stock-bridge-test' 0
cargo run -p multigalaxy -- dev-launch development.local.toml 'target\mg1-stock-bridge-test' 0 --gecko-bridge-probe
```

The probe polls for the game ID for 60 seconds by default. Adjust this with `--bridge-startup-timeout-secs=90`; the supported range is 5–300 seconds. It logs elapsed time, PID, mapped-view bytes, observed ID bytes, and validation state. The bridge command prints changing counters, acknowledgements, validated X/Y/Z, update frequency, and round-trip timing. Type `Q` to stop. See [performance.md](performance.md) for user-reported bridge-on/off measurements and [stock-dolphin-bridge.md](stock-dolphin-bridge.md) for safeguards.

## M4 localhost position relay

M4 adds two-player TCP position relay but no remote actor spawning or gameplay writes. First prepare two profiles, each using the required external AllStars patch:

```powershell
cargo run -p multigalaxy -- dev-setup development.local.toml 'target\mg1-sync-mario'
cargo run -p multigalaxy -- dev-setup development.local.toml 'target\mg1-sync-luigi'
```

Use five PowerShell windows. Start the server:

```powershell
cargo run -p multigalaxy -- sync-server 127.0.0.1:39845
```

In two windows, launch separate Dolphins and note their printed PIDs:

```powershell
cargo run -p multigalaxy -- dev-launch development.local.toml 'target\mg1-sync-mario' 0 --gecko-bridge-probe
cargo run -p multigalaxy -- dev-launch development.local.toml 'target\mg1-sync-luigi' 0 --gecko-bridge-probe
```

In two other windows, attach a read-only network client to each matching PID:

```powershell
cargo run -p multigalaxy -- sync-client development.local.toml <MARIO_DOLPHIN_PID> 127.0.0.1:39845 Mario
cargo run -p multigalaxy -- sync-client development.local.toml <LUIGI_DOLPHIN_PID> 127.0.0.1:39845 Luigi
```

The clients validate the Dolphin executable path, PAL inputs, handler fingerprint, live game ID, and mailbox. The server binds only to localhost. Each distinct profile keeps its own configuration. Full interactive two-instance verification has not yet been recorded; synthetic loopback integration tests pass. See [networking.md](networking.md) and [specs/M4-two-player-position-sync.md](../specs/M4-two-player-position-sync.md) for protocol and recovery details.
