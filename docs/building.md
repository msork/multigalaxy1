# Building and Dolphin test runs

Host tooling is a Cargo workspace. Install Rust/Cargo, then run `cargo test --workspace`, `cargo build --workspace`, or compare DOLs with `cargo run -p multigalaxy -- <original.dol> <modified.dol>`. The current test suite has 11 passing unit tests.

Revision inspection uses `cargo run -p multigalaxy -- identify <disc-header.bin> <main.dol>`, where the first file is the user's 0x80-byte raw Wii disc header. Exact PAL ID/revision and main.dol SHA-256 fingerprints are required; no verified production PAL hook map is registered yet.

## Isolated Dolphin debugging

The Rust launcher can start Dolphin using a fresh, empty user directory so debug configuration and saves remain separate from the user's ordinary Dolphin profile:

```powershell
cargo run -p multigalaxy -- launch 'C:\path\Dolphin.exe' 'C:\temp\mg1-dolphin-test' 'C:\path\game.rvz' 55176 --mute
```

`--mute` is an optional **test/debug-only** switch. It sets Dolphin's `Main.DSP.Muted=True` and volume to zero in the isolated test configuration. Omitting `--mute` leaves those settings alone and preserves normal Dolphin audio behavior; production launch must not add the flag by default.

The launcher enables Dolphin's optional GDB stub for memory inspection. Read a guest range with `cargo run -p multigalaxy -- readmem 55176 80000000 16`, or compare a candidate DOL's loaded sections with `cargo run -p multigalaxy -- verify-loaded 55176 <main.dol>`. GDB memory reads are a reverse-engineering aid, not a supported game bridge. The Rust client connects only to localhost and exposes read operations only. Dolphin 2609a's observed listener bound to `0.0.0.0`, however, so keep debug sessions brief and stop the test Dolphin instance when finished; do not expose the debug port to untrusted networks.

The launcher refuses a non-empty user directory and never changes the supplied RVZ. Use a new temporary path for each run if Dolphin has populated the previous one. Injected Wii runtime builds are not yet defined; do not use unverified addresses.
