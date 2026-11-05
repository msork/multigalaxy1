# Online networking

## Product decision

MultiGalaxy1 is online-only and does not use Dolphin NetPlay. Every participant runs a separate local PAL Galaxy 1 session in Dolphin and sees their own full-screen camera. MultiGalaxy1 owns the lobby, transport, game-side synchronization, and shared session state.

## Reference architecture research

SM64CoopDX describes a client/server network system with direct socket and relay-backed transports, network player records, reliable packet paths, and synchronization support exposed to game/mod state. Its key lesson is an explicit game-level sync API and transport abstraction. Better Sunshine Online is a closer GameCube-era reference; its current source, license, and client/server details must be verified before reuse. These projects are design references, not dependencies. Do not copy code until license and compatibility are verified. SM64 mechanics are not directly portable to Galaxy.

## State and authority

State snapshots carry stable player ID, character, tick, stage/scene, position, orientation, velocity, animation/movement state, gravity-relative frame, health/life state, and ability state. Reliable sequenced events carry interactions, damage/death, respawn, star/boss/progression changes, and stage transitions. Cadence, quantization, interpolation, loss handling, and tick rules need runtime measurements.

The server owns lobby membership, character reservations, session tick, and arbitration for shared progression and contested interactions. Clients may predict local movement and reconcile to authoritative snapshots. Progression, boss phases, shared rewards, respawn arbitration, and transitions use idempotent event IDs. Disconnect/reconnect must not duplicate rewards or corrupt progress.

## Dolphin boundary

A launcher can start one Dolphin process per player and configure local controls, locale, and game path, but cannot make game actors appear by itself. A game-side bridge is required to read local player state and create/update remote rendered actors. The preferred path is an opt-in Dolphin 2609a source patch: it publishes a small snapshot from `Core::OnFrameEnd` using Dolphin's guarded MMU read API. The Rust reader validates the shared ABI and is read-only; no game-state writes or Mario fields are implemented. GDB remains development-only. Do not scan process memory or write emulated memory.

The source patch is present at `tools/dolphin-bridge/` but has not been applied, compiled, or benchmarked because this repository has no Dolphin source checkout. Its Windows mapping cadence follows Dolphin's emulated field callback and therefore is not yet shown to reach 60 Hz. See [the patch specification and license notes](../tools/dolphin-bridge/README.md). Any distributed Dolphin fork must comply with Dolphin's GPL-2.0-or-later terms; this does not change the WTFPL license of original MultiGalaxy1 files.

Bridge messages need bounded sizes, protocol/build negotiation, lifecycle/error reporting, backpressure, and safe shutdown. Every game memory access must bind to an exact verified PAL revision and hook signature.

## Client-local localization

Each client chooses Russian, French, or English presentation locally. Locale assets may differ only after audit proves they do not affect simulation, timing, collision, branching, or progression. Session negotiation verifies common gameplay hashes and an audited locale package identity. No Dolphin NetPlay checks exist in this architecture; do not imply their absence is a compatibility validation.

## Two-Dolphin proof

1. Start two separate Dolphin processes using the same verified PAL build.
2. Connect two Rust clients to one local session server.
3. Read local player state through the game-side bridge and transmit sequenced snapshots.
4. Apply the remote state through a remote-actor bridge and render it in each full-screen view.
5. Measure latency, packet loss, correction, and teardown; repeat with different local languages.

Synthetic protocol tests do not pass this proof. Require Dolphin boot, a visible remote character on both clients, independent control, and correct player identity before claiming runtime multiplayer.
