# Testing and evidence

Evidence labels: **research** (source documentation), **static** (binary/source inspection), **unit** (synthetic fixtures), **build** (successful compilation), **Dolphin boot**, **two-instance state exchange**, and **online gameplay**. Do not promote a claim beyond its evidence.

## Current verified evidence

- `cargo test --workspace`: 11 unit tests pass, including DOL parsing/revision validation, GDB RSP memory-read framing and bounds, and test-only mute configuration.
- `cargo build --workspace`: succeeds.
- Dolphin 2609a and a PAL RVZ launched with isolated user data. DolphinTool identified it as `RMGP01`, PAL revision 0. RVZ was left unchanged.
- Rust GDB client read `RMGP01` at guest memory `0x80000000` from the actual running Dolphin instance.
- `verify-loaded` found a section mismatch against the available `reference/original/main.dol` at `0x800041a3`. That comparison candidate is therefore not established as the boot DOL for this RVZ. Coordinate extraction and game-side hook development remain blocked on an exact executable fingerprint and verified symbols.
- `--mute` writes mute settings only for an isolated test/debug launch. The default launch configuration omits DSP mute settings. No audible verification was performed.

## Online acceptance matrix

| Case | Status |
|---|---|
| Two Dolphin instances exchange bridge-derived state and visibly render each other | Not implemented/tested |
| Live player coordinates extracted from the game | Not verified; no address map approved |
| Independent local camera and controller input | Not tested |
| Four players and all characters | Not tested |
| Authority, prediction, and reconciliation | Not implemented/tested |
| Russian/French/English PAL clients | Not tested; Russian archive unaudited |
| Character abilities, star collection, death/respawn, transitions | Not tested |
| Loss, latency, reconnect, duplicate event handling | Not tested |

No gameplay boot, coordinates, two-instance exchange, or multiplayer result is established.
