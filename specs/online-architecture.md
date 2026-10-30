# Online architecture specification

## Objective

Implement online-only co-op for up to four PAL SMG1 players. Every player runs their own Dolphin instance with a full-screen camera. Dolphin NetPlay and local split-screen are excluded.

## Verified research

- Dolphin runs game instances but the project has not established a supported generic remote game-state API; a verified game-side bridge is required to create remote actors.
- SM64CoopDX documents a client/server model, network player records, reliable packets, and mod/game-level synchronization. Better Sunshine Online is a relevant GameCube-era reference; its code/license need direct audit before reuse.
- Galaxy 1 engine hooks and exact supported PAL revision remain unverified. Rust PPC runtime compatibility is not proven.
- Russian patch archive `SuperMarioGalaxy_wii.rar` is not locally available; its contents/revision/license remain unaudited.

## Functional requirements

- Host/join lobby supports 1–4 uniquely identified players and reserves Mario/Luigi/Wario/Waluigi.
- Every client operates one local Dolphin process and has independent input, camera, locale, and rendering.
- Clients exchange sequenced transforms, orientation, animation, gravity, abilities, health/life state, and stage identity.
- Server arbitrates shared rewards, progression, contested interactions, boss phases, deaths/respawns, and transitions.
- Clients predict local motion where safe and reconcile against server state.
- Russian/French/English PAL assets stay client-side and are admitted only through a manifest/allowlist after gameplay isolation audit.

## Technical design

Rust workspace owns launcher, protocol, networking, lobby, state validation, prediction/reconciliation, patching, and tests. A verified injected game-side bridge reads local state, accepts remote state/events, and communicates over versioned local IPC. Minimal C/C++/PPC code may be needed at engine ABI boundaries. Never use guessed memory offsets.

## Dependencies

Exact PAL game/revision reference; Dolphin executable/source investigation; compatible injection and bridge mechanism; controller patch integration; local game files supplied by users; licensed/distributable character assets.

## Risks

Engine singleton assumptions; actor creation/render integration; incompatible per-client translated gameplay data; latency and gravity-dependent movement; desyncs; revision fragmentation; Rust Wii toolchain/ABI; scope of Dolphin fork.

## Acceptance criteria

Two Dolphin instances join one local server, exchange real bridge-derived player state, and visibly render each other's character while retaining independent local cameras/controls. Four-player lobby and authority tests pass. Russian, French, and English PAL clients can share one session without cross-locale UI changes or gameplay divergence. No completion claim before runtime evidence.

## Testing strategy

Protocol and authority tests use synthetic states and loss/reorder fixtures. Runtime testing uses two then four Dolphin processes and a legally supplied matching PAL build. Record boot, remote rendering, latency, reconnect, progression, language, and desync outcomes separately.
