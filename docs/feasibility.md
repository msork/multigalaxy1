# Feasibility

## Current assessment

Four-player online play is technically plausible but requires modifying the game engine path. Each user runs their own Dolphin and the mod renders remote actors locally. SMG1 was built around one Mario and global state; a second character requires understanding actor lifecycle, update, collision, gravity, interactions, camera ownership, HUD, and progression.

BSMSO provides a concrete GameCube/Dolphin precedent: a PPC game module exports local player state and renders remote players, a launcher communicates through a RAM mailbox, and a relay server forwards state. Its GPL-3.0 code is not incorporated into this MIT project. Galaxy-specific bridge feasibility remains unproven.

The largest integration risk is a reliable game-side data path. A launcher alone cannot see structured gameplay state. First investigate Dolphin source/debugger/memory APIs, then prefer an injected bridge with versioned IPC. A small Dolphin fork is an option only if the needed lifecycle, memory, or process APIs are unavailable upstream. Do not use unverified address scanning.

Classic Controller input uses the external Dolphin AllStars per-frame patch, with both requested shoulder swaps merged deterministically. The patch's sites are statically checked against the exact PAL Rev 0 DOL and future registered hooks; Dolphin 2609a runtime behavior and conventional pad mappings still need gameplay validation. The patch itself stays outside the repository.

## Rust-first go/no-go gates

1. Continue Rust for launcher, networking, protocol, patch tooling, simulation data, and host tests.
2. Prove object generation/linking for the Wii Broadway ABI before putting Rust in injected runtime.
3. Select narrow C/C++/PPC hooks if Rust target, ABI, or linker feasibility fails.
4. Boot a harmless verified hook on Dolphin before depending on it for multiplayer.

## Not established

- Exact PAL revision mapping and hook semantics.
- Dolphin bridge mechanism and any requirement for a fork.
- Real game-state exchange or remote actor rendering.
- Online latency, authority/prediction, four-player stability, progression, or locale separation.
- Rust Wii runtime compilation.
- Russian patch contents, revision, and license.
