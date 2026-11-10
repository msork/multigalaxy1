# Building and Dolphin test runs

Host tooling is a Cargo workspace. Run `cargo fmt --all -- --check`, `cargo test --workspace`, and `cargo build --workspace`.

## Official Dolphin only

Use an official, unmodified Dolphin installation and a game image you are entitled to use. MultiGalaxy does not build or alter Dolphin or DolphinTool and never edits the supplied game image in place. The separate ISO rebuild command below can create a new user-owned output image. The launcher takes explicit paths and writes emulator settings only under the selected isolated user directory.

The launcher has no Dolphin release-number or executable-hash allowlist. Discovery checks that the configured executable is a non-empty Dolphin binary and reports whether the adjacent DolphinTool and Gecko handler files are present. PAL identity is checked by actually invoking DolphinTool's header operation; Wii support and isolation are exercised by the requested launch path. Check an installation without starting Dolphin:

```powershell
cargo run -p multigalaxy -- discover-dolphin '<Dolphin installation directory>'
```

The command reports a candidate executable and presence-only optional tool files, or fails on missing/ambiguous executables. Presence is not presented as runtime capability validation. Put the executable path in the local config. AllStars remains mandatory; setup verifies the patch data and the launcher requires a non-empty Dolphin Gecko handler file. A GDB request is validated by attempting the configured RSP handshake after launch. The M4 mailbox additionally requires its known codehandler fingerprint, a bounded MEM1 mapping of expected size, live PAL identity/signatures, and a unique mailbox signature; any failure disables/refuses that optional bridge without blocking ordinary launches. Its live layout evidence is from Dolphin 2609a, so compatibility on other builds is unclaimed unless these checks pass and is not yet live-tested. See [Dolphin capability compatibility](references/dolphin-version-migration.md).

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

The completed local test verifies PAL RMGP01 revision 0, the pinned source DOL, WIT partition integrity before/after, output DOL and game-file hashes, source immutability, and deterministic recomposition. `dev-launch --patched-image` accepts only a WIT `rebuild-iso-dol` manifest and revalidates both ISO extractions, image/DOL hashes, actual DolphinTool PAL-header operations, WIT partition/extraction operations, VCDIFF, and AllStars non-overlap before configuring the isolated profile. Historical DolphinTool/WIT binary hashes remain provenance fields; current tools are checked for stable identity during validation and by their actual operations, not against historical binary hashes or versions. It does not enable the M4 mailbox unless `--gecko-bridge-probe` is separately provided. The changed-byte ISO has a user-confirmed live boot regression on 2609a; this is not trampoline validation. See [Wii image rebuild evidence](references/wii-image-rebuild.md).

For a separately built and hash-pinned candidate DOL, use `rebuild-iso-dol` with the exact PAL source DOL, symbol map, candidate DOL, candidate SHA-256, and external AllStars folder. It rejects changes outside DOL text or overlapping AllStars writes and verifies the re-extracted target bytes. This is not a trampoline generator and is not ready for Dolphin boot until the changed output has passed an offline WIT round trip and the hook's code location is verified. The exact candidate command is recorded in [Wii image rebuild evidence](references/wii-image-rebuild.md).

Before launch, validate a WIT-rebuilt image without starting Dolphin:

```powershell
$DolphinTool = '<path to DolphinTool.exe beside your Dolphin installation>'
$Wit = '<path to Wiimms ISO Tool wit.exe>'
$AllStars = '<path to the external AllStars PAL patch folder>'

cargo run --release -p multigalaxy -- validate-rebuilt-iso `
  $DolphinTool `
  $Wit `
  'target\mg1-disc-pipeline\source.iso' `
  'target\mg1-disc-pipeline\changed-neutral-nop.iso' `
  'reference\original\main.dol' `
  $AllStars
```

The disposable changed-byte candidate can be launched from an interactive PowerShell on the visible desktop after `dev-setup` has prepared `target\mg1-dev-profile`:

```powershell
$Wit = '<path to Wiimms ISO Tool wit.exe>'

cargo run --release -p multigalaxy -- dev-launch development.local.toml `
  'target\mg1-dev-profile' 0 `
  --patched-image 'target\mg1-disc-pipeline\changed-neutral-nop.iso' `
  --source-iso 'target\mg1-disc-pipeline\source.iso' `
  --wit $Wit
```

The launcher infers DolphinTool beside the configured Dolphin executable. It hashes the explicit source/output ISOs, captures current tool identities to detect replacement during validation, verifies PAL identity and WIT partitions, extracts both images, confirms the source DOL and exact authorized candidate DOL, checks deterministic delta and unchanged DATA files, and rejects AllStars overlap. Historical tool hashes in the manifest do not lock later validators to those binaries. The operation is intentionally disk intensive. It keeps mandatory AllStars and the copied controller profile; M4 remains off unless `--gecko-bridge-probe` is explicitly added. The live 2609a baseline and capability policy are in [Dolphin capability compatibility](references/dolphin-version-migration.md).

## Local Windows development profile

Copy `development.local.example.toml` to the git-ignored `development.local.toml` and set local paths. Development-only D3D12, controller profile, save import, and mute configuration are written to a fresh isolated Dolphin user directory. The regular profile, save source, and original game image are not modified. `dev-setup` uses Dolphin's supported UI for save import.

Start a visible debug launch from an interactive PowerShell on `WinSta0\Default`:

```powershell
cargo run -p multigalaxy -- dev-launch development.local.toml 'target\mg1-dev-profile' 55221 --debug
```

Debug mode connects to one persistent GDB session, validates the live `RMGP01` identity, and resumes emulation. GDB is for development diagnostics only. Use `STOP`/`START` to pause/resume read sampling and `Q` to close it. `--debug-unattached` leaves emulation paused and is not a gameplay benchmark. A GDB read has not yet yielded verified moving Mario coordinates.

The benchmark command accepts `normal`, `gdb-unattached`, `gdb-idle`, and `gdb-sampling`. The `gdb-unattached` case is paused and not comparable to gameplay. Follow [performance.md](performance.md) for interactive paired runs. No numeric stock-GDB performance result is currently claimed.

## Stock bridge experiment

The launcher includes an opt-in, reversible mailbox experiment using a Windows named MEM1 mapping. It enables a Gecko counter routine only in the selected isolated profile; it does not enable GDB. The experiment has live evidence on Dolphin 2609a and is not a general Dolphin API. Before memory access it requires the current handler fingerprint, mapping extent, matching Dolphin process/session, PAL DOL signatures, live game ID, and validated mailbox structure. A different build is refused if any prerequisite or runtime signature differs. First use a fresh isolated profile already prepared by `dev-setup`, with external mandatory AllStars patch inputs configured. From interactive PowerShell on the visible Windows desktop, launch the baseline and then the probe (close Dolphin between runs):

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
