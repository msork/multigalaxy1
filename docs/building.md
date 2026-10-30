# Building and Dolphin test runs

Host tooling is a Cargo workspace. Install Rust/Cargo, then run `cargo test --workspace`, `cargo build --workspace`, or compare DOLs with `cargo run -p multigalaxy -- <original.dol> <modified.dol>`. The current suite has 16 passing unit tests plus one opt-in live Dolphin integration test.

Revision inspection uses `cargo run -p multigalaxy -- identify <disc-header.bin> <main.dol>`, where the first file is the user's 0x80-byte raw Wii disc header. The exact Petari-matched PAL Rev 0 `main.dol` identity is registered; no production PAL hook or runtime actor map is registered.

## Isolated Dolphin debugging

The Rust launcher can start Dolphin using a fresh, empty user directory so debug configuration and saves remain separate from the user's ordinary Dolphin profile:

```powershell
cargo run -p multigalaxy -- launch 'C:\path\Dolphin.exe' 'C:\temp\mg1-dolphin-test' 'C:\path\game.rvz' 55176 --mute
```

`--mute` is an optional **test/debug-only** switch. It sets Dolphin's `Main.DSP.Muted=True` and volume to zero in the isolated test configuration. Omitting `--mute` leaves those settings alone and preserves normal Dolphin audio behavior; production launch must not add the flag by default.

The launcher enables Dolphin's optional GDB stub for memory inspection. `verify-loaded` checks the `RMGP01` ID and samples 16 bytes at each DOL section's start, midpoint, and end. `verify-loaded-full` compares complete sections in 1 KiB chunks and reports progress; it is much slower. Each GDB response has a bounded total timeout. Dolphin 2609a was observed accepting one GDB client and then refusing reconnects after it disconnects, so use one read session per freshly launched Dolphin process. GDB is a reverse-engineering aid, not a supported game bridge. The Rust client connects only to localhost and exposes read operations only. Dolphin 2609a's listener was observed bound to `0.0.0.0`, so keep debug sessions brief and do not expose the debug port to untrusted networks.

The launcher redirects Dolphin stdout/stderr to log files in its fresh isolated user directory, avoiding inherited terminal handles that can make launcher invocations appear to hang. Stop the exact test Dolphin PID when finished; the supplied RVZ and regular Dolphin profile are not changed.

The launcher refuses a non-empty user directory and never changes the supplied RVZ. Use a new temporary path for each run if Dolphin has populated the previous one. Injected Wii runtime builds are not yet defined; do not use unverified addresses.

### Optional PAL AllStars controller patch

The patch is off by default. Run the read-only validator with the local upstream patch folder and the user's PAL DOL:

```powershell
.\target\debug\multigalaxy.exe inspect-allstars 'C:\path\SMG\PAL\Individual Patches' 'reference\original\main.dol'
```

To opt in, use the same paths with `launch` and choose a new empty isolated profile:

```powershell
.\target\debug\multigalaxy.exe launch 'C:\path\Dolphin.exe' 'C:\temp\mg1-allstars-test' 'C:\path\game.rvz' 55176 --mute --allstars 'C:\path\SMG\PAL\Individual Patches' 'reference\original\main.dol'
```

The launcher requires the pinned SMG PAL AllStars, Swap L/ZL, and Swap R/ZR files, validates the exact RMGP01 Rev 0 DOL fingerprint, merges the overlapping swap writes, and enables the resulting Dolphin `[OnFrame]` patch only in the isolated profile. It enables cheats there only. No upstream files are copied into the repository. Runtime behavior on Dolphin 2609a remains unverified.
