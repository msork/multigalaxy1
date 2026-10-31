# Online networking

## Product decision

MultiGalaxy1 is online-only and does not use Dolphin NetPlay. Every participant runs a separate local PAL Galaxy 1 session in Dolphin and sees their own full-screen camera. MultiGalaxy1 owns the lobby, transport, game-side synchronization, and shared session state.

## Reference architecture research

SM64CoopDX describes a client/server network system with direct socket and relay-backed transports, network player records, reliable packet paths, and synchronization support exposed to game/mod state. Its key lesson is an explicit game-level sync API and transport abstraction. Better Sunshine Online is a closer GameCube-era reference; its current source, license, and client/server details must be verified before reuse. These projects are design references, not dependencies. Do not copy code until license and compatibility are verified. SM64 mechanics are not directly portable to Galaxy.

## State and authority

State snapshots carry stable player ID, character, tick, stage/scene, position, orientation, velocity, animation/movement state, gravity-relative frame, health/life state, and ability state. Reliable sequenced events carry interactions, damage/death, respawn, star/boss/progression changes, and stage transitions. Cadence, quantization, interpolation, loss handling, and tick rules need runtime measurements.

The server owns lobby membership, character reservations, session tick, and arbitration for shared progression and contested interactions. Clients may predict local movement and reconcile to authoritative snapshots. Progression, boss phases, shared rewards, respawn arbitration, and transitions use idempotent event IDs. Disconnect/reconnect must not duplicate rewards or corrupt progress.

## Dolphin boundary

A launcher can start one Dolphin process per player and configure local controls, locale, and game path, but cannot make game actors appear by itself. A game-side bridge is required to read local player state and create/update remote rendered actors. The Rust shared-memory snapshot prototype defines a fixed versioned transport ABI but has no Dolphin producer yet. Source research shows the upstream GDB stub runs core-timing polling while connected; GDB remains development-only, never the production bridge. No supported public external game-state API has been established. Prefer a minimal GPL-compatible Dolphin fork that uses guarded MMU reads on a safe emulation callback and nonblocking shared-memory publication. Do not scan process memory for unknown mappings, write emulated memory, or use UI automation.

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
