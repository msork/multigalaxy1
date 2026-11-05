# Feasibility

## Current assessment

Four-player online play depends on game-engine integration. Each client runs its own official Dolphin and must render remote actors locally. SMG1 assumes one Mario and global state; actor lifecycle, collision, gravity, interactions, camera ownership, HUD, and progression need reverse engineering.

BSMSO is a precedent for a game module, launcher mailbox, and relay server, but its architecture relies on game-specific integration and its GPL-3.0 code is not incorporated. It does not prove a stock SMG1 bridge.

## Stock Dolphin go/no-go

MultiGalaxy1 must use official, unmodified Dolphin. Do not restore the former source fork or build pipeline. Gecko/DOL patches can execute guest PPC code but do not supply host IPC. Dolphin 2609a GDB is available for development but pauses on startup and its performance impact is unquantified. Windows 2609a excludes MemoryWatcher, whose UNIX implementation is one-way. External process memory has no supported guest-RAM mapping and risks races. See [stock bridge feasibility](stock-dolphin-bridge.md).

Before implementing the online game bridge, test a stock-only option for verified PAL hook activation, bidirectional state at approximately 60 Hz, and no noticeable slowdown. If this cannot be achieved, document the limitation and alternatives rather than reintroducing a Dolphin fork.

## Controller and Rust feasibility

Classic Controller input uses externally supplied Dolphin-compatible AllStars patches, including both requested shoulder swaps. Exact PAL Rev 0 DOL composition is statically checked; runtime behavior and conventional controller mapping need gameplay validation. Patch files remain external.

Rust remains primary for launcher, networking, protocol, patch tooling, simulation, and tests. Prove Wii Broadway ABI/compiler/linker support before using Rust in injected runtime; narrow C/PPC hooks remain possible if needed.

## Not established

- Live Mario coordinates, verified actor hooks, or remote actor rendering.
- Stock Dolphin bidirectional bridge or its performance.
- Online latency, four-player stability, progression, or localization isolation.
- Rust Wii runtime compilation.
- Russian patch contents, revision, and license.
