# Architecture

## Product shape

MultiGalaxy1 is an online-only Rust launcher/mod stack. Each participant runs a separate local PAL Galaxy 1 session in Dolphin and renders their own full-screen camera. There is no local split-screen and no Dolphin NetPlay. Game simulation executes in each client; the custom online layer communicates actor snapshots and shared events.

```text
Rust launcher/client
  ├─ lobby, identity, game/revision/localization validation
  ├─ Rust transport, prediction, and reconciliation
  ├─ Dolphin process lifecycle (one instance per player)
  └─ versioned local IPC
       └─ injected game-side bridge (verified PPC/C++ shim; Rust where target permits)
            └─ SMG1 actor, input, progression, and rendering hooks

Authoritative session server
  ├─ lobby/membership/character reservations
  ├─ tick and protocol negotiation
  ├─ shared progression and contested interaction arbitration
  └─ validation, snapshots, reconnect policy
```

## Rust-first workspace

- `multigalaxy-patch`: safe DOL parsing, PAL revision fingerprints, hook conflict validation.
- `multigalaxy` CLI/launcher: game validation, Dolphin lifecycle, local IPC startup.
- Planned `multigalaxy-protocol`: versioned wire messages and state types.
- Planned `multigalaxy-network`: client/server transport, lobby, authoritative session model.
- Planned `multigalaxy-game-bridge`: launcher-side IPC client and bridge protocol.
- Game runtime modules contain only narrow verified hooks/bindings. Keep portable character rules in Rust when Wii toolchain capabilities permit; C++/PPC may be unavoidable.

No standard Rust target establishes Wii Broadway ABI/link compatibility. Rust runtime support is conditional on proving big-endian PPC EABI, compiler/linker, runtime, panic handling, and DOL integration. Launcher, networking, protocol, tests, and patch tools remain Rust.

## State and authority

Player slots are stable session identities and own transform, velocity, facing, animation, gravity frame, ability state, health, and life state. Bridge messages include session tick and build identity. Camera and locale are per-client presentation state and excluded from authoritative simulation hashes.

Clients predict responsive local movement and cosmetic animation where correction is safe. The server arbitrates progression, stars, boss phase transitions, shared item ownership, death/respawn, stage transitions, and contested interactions. It publishes sequenced authoritative events and periodic snapshots. Client reconciliation occurs through verified bridge hooks; never write arbitrary game memory through guessed offsets.

## Dolphin integration

A separate launcher cannot expose game actors by itself. The minimal runtime proof needs a game-side bridge to read local Mario state, represent remote characters as rendered actors, and accept authoritative events. BSMSO documents a RAM-mailbox bridge between its launcher and game module, with a custom PPC module and relay server. It is a reference only; retain its GPL-3.0 license and do not incorporate its code without a separate review. Independently investigate Dolphin debugger, memory and source IPC options before selecting a versioned mailbox/injected bridge or a small emulator fork. Do not rely on brittle UI automation or unbounded raw memory scanning.

## PAL and localization identity

Support only PAL SMG1 builds with exact verified ID/revision/DOL maps. Reject unknown versions. Gameplay payloads must match across peers. Russian, French, and English presentation assets remain local and can differ only after audit proves they cannot affect simulation. See `docs/localization.md` and `docs/references/russian-patch.md`.

## Runtime investigation status

The launcher has an opt-in `--mute` argument strictly for test/debug sessions. It writes audio mute options into only the isolated Dolphin test profile; normal launch omits these options. Dolphin 2609a GDB memory reads have been verified against a live PAL session, but the available candidate `main.dol` mismatches loaded memory at `0x800041a3`. Until an exact hash-linked PAL DOL and symbol map are available, coordinate access and injection remain unimplemented. The GDB stub bound to all interfaces during the observed test, so use it only briefly and stop Dolphin after inspection. See [runtime research](research.md) and [testing evidence](testing.md).
