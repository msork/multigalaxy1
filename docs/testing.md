# Testing and evidence

Evidence labels: **research** (source documentation), **static** (binary/source inspection), **unit** (synthetic fixtures), **build** (successful compilation), **Dolphin boot**, **two-instance state exchange**, and **online gameplay**. Do not promote a claim beyond its evidence.

## Current verified evidence

- `cargo test --workspace`: current test result is recorded below; one live-Dolphin integration test is ignored by default. Its opt-in run now passes against a fresh Dolphin 2609a process.
- `cargo build --workspace`: succeeds.
- Dolphin 2609a and a PAL RVZ launched with isolated user data. DolphinTool identified it as `RMGP01`, PAL revision 0. RVZ was left unchanged.
- Rust GDB read `RMGP01` at `0x80000000`. Sampled section validation and the opt-in read-only integration test passed against Dolphin 2609a. Earlier full-section verification also passed, but the current fast mode samples each section at start/middle/end and is not a full hash comparison.
- The current reference DOL is PAL `RMGP01` Rev 0 and matches all loaded text/data sections in Dolphin. Its SHA-1 is `9bd1daa520bd1f573b6a4b82e03336e9c51b511f`; SHA-256 is `8200fb72b3170069bb71c8b3dc21b49e429977ce8ae5159bb95ea6a19de0fbee`.
- `--mute` writes mute settings only for an isolated test/debug launch. The default launch configuration omits DSP mute settings. No audible verification was performed.

## Online acceptance matrix

| Case | Status |
|---|---|
| PAL Dolphin sampled section validation | Passed; all 10 DOL sections sampled, 0.34 s, Dolphin 2609a |
| Opt-in PAL live integration test | Passed against fresh Dolphin process; 1 test, read-only |
| Full DOL section comparison | Passed in a prior run; slower scan now available as `verify-loaded-full` |
| Two Dolphin instances exchange bridge-derived state and visibly render each other | Not implemented/tested |
| Live Mario coordinates extracted from the game | Not verified; static PAL pointer path is validated, but live GameSystem singleton was null in the last probe |
| Independent local camera and controller input | Not tested |
| Four players and all characters | Not tested |
| Authority, prediction, and reconciliation | Not implemented/tested |
| Russian/French/English PAL clients | Not tested; Russian archive unaudited |
| Character abilities, star collection, death/respawn, transitions | Not tested |
| Loss, latency, reconnect, duplicate event handling | Not tested |

No controlled in-game movement, two-instance exchange, or multiplayer result is established.

## PAL live-memory work

The current `reference/original/main.dol` is the user-supplied PAL build with SHA-1 `9bd1daa520bd1f573b6a4b82e03336e9c51b511f`. It matches Petari's `config/RMGP01/build.sha1` and the live Dolphin `RMGP01` identity. Sampled validation compares 16 bytes at each DOL section's start, midpoint, and end (deduplicated); use `verify-loaded-full` for a byte-by-byte comparison. The previous USA DOL is no longer retained.

The Petari-backed static chain and `MarioActor::mPosition` offset are validated against this PAL DOL and symbol file. A persistent read-only `probe-mario` command reuses one GDB connection. On the last run, the read at the verified GameSystem singleton slot returned null, so this is only a waiting-state test: no coordinates or movement have been observed. To validate movement, start an isolated Dolphin session with `--gdb` and sampler as below. GDB mode intentionally starts the emulated CPU paused; attach the sampler and resume from Dolphin. For normal gameplay checks, omit `--gdb`.

```powershell
# Terminal 1: new empty user directory; test/debug is muted only.
cd C:\Users\maxim\Projects\multigalaxy1
.\target\debug\multigalaxy.exe launch 'C:\Users\maxim\Games\Dolphin-x64\Dolphin.exe' 'C:\Users\maxim\Projects\multigalaxy1\target\mg1-coordinate-manual' 'C:\Users\maxim\Games\Wii Games\Super Mario Galaxy (Europe, Australia) (En,Fr,De,Es,It).rvz' 55218 --mute

# Terminal 2:
cd C:\Users\maxim\Projects\multigalaxy1
.\target\debug\multigalaxy.exe probe-mario 55218 reference\original\main.dol target\petari-rmgp01-symbols.txt 200
```

Type `START` in Terminal 2, focus Dolphin, enter a playable scene, then stand still for five seconds, walk in one direction for five seconds, and jump twice. Return to Terminal 2, type `STOP`, then `Q`. Save/copy the `state=ready` lines and timestamps. `state=waiting` means no player state was resolved; do not infer coordinates from it. The probe performs no emulated-memory writes. The launcher refuses an existing non-empty test profile and does not alter the RVZ or normal Dolphin profile.

The GDB client bounds each response by one total timeout deadline and reports the failing guest range. The fast validator reads small samples instead of scanning each whole section; the optional full scan logs progress every 64 KiB. A full scan without progress output can look stalled while making thousands of requests. Dolphin 2609a was observed to stop accepting new GDB connections after the first client disconnects, so run one integration test per fresh Dolphin process. Each new CLI read command therefore needs a fresh Dolphin instance; a persistent probe should keep one GDB connection open. The ignored test reads only and compares sampled bytes. For example:

```powershell
$env:MG1_GDB_PORT = '55213'
$env:MG1_PAL_DOL = 'C:\path\to\main.dol'
cargo test -p multigalaxy live_dolphin_matches_the_pal_dol_read_only -- --ignored --nocapture
Remove-Item Env:\MG1_GDB_PORT, Env:\MG1_PAL_DOL
```

A controlled movement test was not performed. The opt-in test verified identity and static executable samples only; it did not read Mario coordinates. Petari exposes the static `LiveActor::mPosition` field at `+0x0C`, but the runtime scene-object singleton address and actor pointer remain unresolved. Coordinates, rotations, gravity state, pointer validation for an actor, timestamped sampling, protocol handoff, and two-instance exchange remain unverified and unimplemented. The current blocker is static tooling/symbol extraction to resolve the PAL singleton root; after that, manual gameplay will be required to validate changing coordinates.
