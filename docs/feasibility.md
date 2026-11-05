# Feasibility

## Current assessment

Four-player online play depends on game-engine integration. Each client runs its own official Dolphin and must render remote actors locally. SMG1 assumes one Mario and global state; actor lifecycle, collision, gravity, interactions, camera ownership, HUD, and progression need reverse engineering.

BSMSO is a precedent for a game module, launcher mailbox, and relay server, but its architecture relies on game-specific integration and its GPL-3.0 code is not incorporated. It does not prove a stock SMG1 bridge.

## Stock Dolphin go/no-go

MultiGalaxy1 uses official, unmodified Dolphin only. A reversible Gecko counter/echo routine and Dolphin 2609a's existing named Windows MEM1 mapping form the current bridge proof. Dolphin source verifies the mapping name and MEM1-at-offset-zero layout. This is an undocumented internal detail, not a supported API; live cache/synchronization semantics and performance remain unverified. GDB remains a development diagnostic. Windows 2609a excludes MemoryWatcher. See [stock bridge feasibility](stock-dolphin-bridge.md).

Do not implement online actor synchronization until interactive testing verifies PAL Gecko activation, bidirectional exchange near 60 Hz, and paired gameplay performance. Synthetic mapping tests are not Dolphin runtime evidence. If the proof fails, document the limitation and alternatives; do not reintroduce a Dolphin fork.

## Controller and Rust feasibility

Classic Controller input uses externally supplied Dolphin-compatible AllStars patches, including both requested shoulder swaps. Exact PAL Rev 0 DOL composition is statically checked; runtime behavior and conventional controller mapping need gameplay validation. Patch files remain external.

Rust remains primary for launcher, networking, protocol, patch tooling, simulation, and tests. Prove Wii Broadway ABI/compiler/linker support before using Rust in injected runtime; narrow C/PPC hooks remain possible if needed.

## Not established

- Live mailbox exchange, Mario coordinates, verified actor hooks, or remote actor rendering.
- Performance cost or 60 Hz cadence for the stock mapping probe.
- Online latency, four-player stability, progression, or localization isolation.
- Rust Wii runtime compilation.
- Russian patch contents, revision, and license.
