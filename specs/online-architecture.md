# Online architecture specification

## Objective

Implement online-only co-op for up to four PAL SMG1 players. Each participant runs official, unmodified Dolphin with their own full-screen camera. Dolphin NetPlay and local split-screen are excluded. The product requirement depends on proving a stock-Dolphin-compatible game bridge first.

## Verified research

- Stock Dolphin 2609a GDB provides guest-memory protocol operations, but pauses at startup and is development-only here; actual overhead is not numerically benchmarked.
- Windows Dolphin 2609a does not compile MemoryWatcher; upstream's UNIX implementation is one-way changed-value reporting.
- Gecko/DOL patches can execute guest code but do not inherently establish host IPC.
- External process memory is unsupported as a general guest-memory interface and may race emulation.
- SM64CoopDX and Better Sunshine Online are architecture references only; their licenses and engine/tool compatibility require separate audit before code reuse.
- Exact game hooks and remote actor lifecycle remain unverified.

## Functional requirements

- Lobby supports 1–4 player identities and Mario/Luigi/Wario/Waluigi reservations.
- Each user runs one official Dolphin, with independent input, camera, locale, and rendering.
- Clients exchange sequenced state and shared events through a verified bridge.
- Server arbitrates rewards, progression, boss phases, shared ownership, death/respawn, transitions, and contested interactions.
- Russian/French/English PAL presentation remains client-side only after gameplay isolation audit.

## Technical design

Rust owns launcher, protocol, networking, lobby, state validation, prediction/reconciliation, patching, and tests. No Dolphin source patch, fork, custom build, or modified Dolphin executable is allowed. The transport between injected guest code and Rust host remains an open feasibility question; investigate stock interfaces and benchmark before implementing it. Never use guessed guest or host addresses.

## Dependencies and risks

Needs exact PAL hooks, a stock bridge mechanism, actor/render integration, controller patch compatibility, latency/reconciliation design, and licensed character assets. Key risks are singleton assumptions, simulation divergence, locale data affecting gameplay, and the possibility that stock Dolphin has no supported low-overhead bidirectional channel.

## Acceptance

Two official Dolphin instances join one local server, exchange live verified game state, and visibly render each other's character with independent cameras and controls. Later acceptance covers four players, progression/reconnect, and Russian/French/English PAL. No compile-only claim satisfies this requirement.

## Testing

Use synthetic protocol tests first. Runtime verification must record official Dolphin build, PAL identity/revision, bridge cadence, gameplay Speed/FPS/VPS, network latency, state correction, locale, and desync behavior separately.
