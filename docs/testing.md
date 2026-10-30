# Testing and evidence

Evidence labels: **research** (source documentation), **static** (binary/source inspection), **unit** (synthetic fixtures), **build** (successful compilation), **Dolphin boot**, **two-instance state exchange**, and **online gameplay**. Do not promote a claim beyond its evidence.

## Current verified evidence

- `cargo test --workspace`: 13 unit tests pass and one live-Dolphin integration test is ignored by default. A prior `verify-loaded` run against Dolphin 2609a matched every PAL DOL section. The new automated live comparison test is present but has not yet completed successfully in a repeat run.
- `cargo build --workspace`: succeeds.
- Dolphin 2609a and a PAL RVZ launched with isolated user data. DolphinTool identified it as `RMGP01`, PAL revision 0. RVZ was left unchanged.
- Rust GDB read `RMGP01` at `0x80000000`; the earlier `verify-loaded` command confirmed every loaded text/data section matches the user-supplied PAL DOL. The opt-in integration test now repeats that comparison but remains unverified.
- The current reference DOL is PAL `RMGP01` Rev 0 and matches all loaded text/data sections in Dolphin. Its SHA-1 is `9bd1daa520bd1f573b6a4b82e03336e9c51b511f`; SHA-256 is `8200fb72b3170069bb71c8b3dc21b49e429977ce8ae5159bb95ea6a19de0fbee`.
- `--mute` writes mute settings only for an isolated test/debug launch. The default launch configuration omits DSP mute settings. No audible verification was performed.

## Online acceptance matrix

| Case | Status |
|---|---|
| PAL Dolphin `verify-loaded` (RMGP01 and all DOL sections) | Passed in an earlier run; read-only, tested on Dolphin 2609a |
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

The current `reference/original/main.dol` is the user-supplied PAL build with SHA-1 `9bd1daa520bd1f573b6a4b82e03336e9c51b511f`. All sections match the loaded `RMGP01` Rev 0 DOL in Dolphin 2609a. The previous USA DOL is no longer retained.

The CLI now scans all DOL section ranges in one GDB connection and reports capped byte-difference runs rather than stopping at the first byte. The GDB client now correctly accepts valid hex memory responses beginning with `E`; RSP error replies are recognized only as `Ehh` packets. The live Dolphin integration test is ignored by default. Set `MG1_GDB_PORT` and `MG1_PAL_DOL`, then run it with `cargo test -p multigalaxy -- --ignored`; it reads the game ID and compares every parsed DOL text/data section against live RAM. It is read-only. On the latest retry, `verify-loaded` matched Text[0] but stalled before completing later sections, so the integration test is still pending further diagnosis.

A controlled movement test was not performed: Dolphin exposed no usable window handle in this session, and the PAL actor pointer is not yet verified. Coordinates, rotations, gravity state, pointer validation for an actor, timestamped samples, protocol handoff, and two-instance exchange remain unverified and unimplemented. Do not describe the DOL identity test as player-state extraction.
