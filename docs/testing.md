# Testing and evidence

Evidence labels: **research** (source documentation), **static** (binary/source inspection), **unit** (synthetic fixtures), **build** (successful compilation), **Dolphin boot**, **two-instance state exchange**, and **online gameplay**. Do not promote a claim beyond its evidence.

## Current verified evidence

- `cargo test --workspace`: 13 unit tests pass and one live-Dolphin integration test is ignored by default. The ignored PAL game-ID integration test was also run manually against Dolphin 2609a and passed.
- `cargo build --workspace`: succeeds.
- Dolphin 2609a and a PAL RVZ launched with isolated user data. DolphinTool identified it as `RMGP01`, PAL revision 0. RVZ was left unchanged.
- Rust GDB client read `RMGP01` at guest memory `0x80000000` from the actual running Dolphin instance; the opt-in live integration test repeated this assertion and passed.
- The reference DOL SHA-1 matches the known USA `RMGE01` Rev 0 executable, while the running RVZ is PAL `RMGP01` Rev 0. The loaded-memory mismatch at `0x800041a3` was therefore a cross-region comparison, not evidence of corruption. A PAL `main.dol` fingerprint and PAL symbol map are still needed before coordinates or hooks.
- `--mute` writes mute settings only for an isolated test/debug launch. The default launch configuration omits DSP mute settings. No audible verification was performed.

## Online acceptance matrix

| Case | Status |
|---|---|
| PAL Dolphin GDB game-ID integration (RMGP01 at 0x80000000) | Passed; read-only, tested on Dolphin 2609a |
| Two Dolphin instances exchange bridge-derived state and visibly render each other | Not implemented/tested |
| Live player coordinates extracted from the game | Not verified; no address map approved |
| Independent local camera and controller input | Not tested |
| Four players and all characters | Not tested |
| Authority, prediction, and reconciliation | Not implemented/tested |
| Russian/French/English PAL clients | Not tested; Russian archive unaudited |
| Character abilities, star collection, death/respawn, transitions | Not tested |
| Loss, latency, reconnect, duplicate event handling | Not tested |

No gameplay boot, coordinates, two-instance exchange, or multiplayer result is established.

## PAL live-memory work

The available `reference/original/main.dol` is confirmed by SHA-1 to be the USA `RMGE01` Rev 0 build, while Dolphin is running PAL `RMGP01` Rev 0. Do not compare that USA executable as a PAL integrity test. The full scan's text differences are expected to include region differences and possibly runtime changes; the scan is not a PAL fingerprint.

The CLI now scans all DOL section ranges in one GDB connection and reports capped byte-difference runs rather than stopping at the first byte. The GDB client now correctly accepts valid hex memory responses beginning with `E`; RSP error replies are recognized only as `Ehh` packets. The live Dolphin integration test is ignored by default and can be run against an already running local test instance with `MG1_GDB_PORT` set. It only reads the six-byte disc ID at `0x80000000` and checks for `RMGP01`.

A controlled movement test was not performed: Dolphin exposed no usable window handle in this session, and the PAL actor pointer is not yet verified. Coordinates, rotations, gravity state, pointer validation for an actor, timestamped samples, protocol handoff, and two-instance exchange remain unverified and unimplemented. Do not describe the GDB disc-ID integration test as player-state extraction.
