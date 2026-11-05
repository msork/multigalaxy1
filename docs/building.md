# Building and Dolphin test runs

Host tooling is a Cargo workspace. Run `cargo fmt --all -- --check`, `cargo test --workspace`, and `cargo build --workspace`.

## Official Dolphin only

Use an official, unmodified Dolphin installation and a game image you are entitled to use. MultiGalaxy does not build or alter Dolphin, DolphinTool, or game images. The launcher takes explicit paths and writes emulator settings only under the selected isolated user directory.

Validate the PAL image read-only with the matching official DolphinTool:

```powershell
cargo run -p multigalaxy -- validate-pal-game '<official Dolphin.exe>' '<PAL game.rvz>'
```

Expected identity is PAL `RMGP01`, revision `0`. The launcher requires the third-party AllStars PAL patch files (including both shoulder swaps) and validates fingerprints before launch; those files are supplied externally and not bundled.

## Local Windows development profile

Copy `development.local.example.toml` to the git-ignored `development.local.toml` and set local paths. Development-only D3D12, controller profile, save import, and mute configuration are written to a fresh isolated Dolphin user directory. The regular profile, save source, and original game image are not modified. `dev-setup` uses Dolphin's supported UI for save import.

Start a visible debug launch from an interactive PowerShell on `WinSta0\Default`:

```powershell
cargo run -p multigalaxy -- dev-launch development.local.toml 'target\mg1-dev-profile' 55221 --debug
```

Debug mode connects to one persistent GDB session, validates the live `RMGP01` identity, and resumes emulation. GDB is for development diagnostics only. Use `STOP`/`START` to pause/resume read sampling and `Q` to close it. `--debug-unattached` leaves emulation paused and is not a gameplay benchmark. A GDB read has not yet yielded verified moving Mario coordinates.

The benchmark command accepts `normal`, `gdb-unattached`, `gdb-idle`, and `gdb-sampling`. The `gdb-unattached` case is paused and not comparable to gameplay. Follow [performance.md](performance.md) for interactive paired runs. No numeric stock-GDB performance result is currently claimed.

## Stock bridge status

The Dolphin source patch and build scripts are retired. Do not apply or build them. Gecko/DOL guest hooks, GDB, MemoryWatcher, and external memory limitations are described in [stock-dolphin-bridge.md](stock-dolphin-bridge.md). PAL image validation with official DolphinTool is the minimal stock-Dolphin proof currently available; visible game boot and live bridge exchange require a user-visible interactive test.
