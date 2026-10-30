# Feasibility

## Current assessment

Four locally simulated players are technically plausible but require substantial work: the original game is built around one Mario and global camera/render state. A second full actor is not obtained by allocating another Mario object; the actor lifecycle, update loops, collision, world interactions, HUD, and progression hooks must all be understood.

Online is not inherently ruled out by Dolphin NetPlay. Lockstep could make actors deterministic when all players run the same patched build and inputs are mapped to synchronized Wii Remote slots. Custom actors, save data, and local camera behavior still need verification. NetPlay does not synchronize mod state generically.

Classic Controller patches rewrite KPAD/game input handling and may overlap player-input hooks. Compose source-level changes or generate conflict-aware patches; never concatenate blind binary patches.

## Rust-first go/no-go gates

1. Confirm Rust target support and available PPC backend. The installed rustc lists Linux/FreeBSD/etc. PowerPC targets, but no standard bare-metal 32-bit PowerPC target; only the Windows host target is installed.
2. Produce a minimal object for the exact Wii ABI and inspect its sections/relocations.
3. Link through the selected Wii toolchain with known imports and no unexpected runtime symbols.
4. Load the hook in Dolphin with a legal local game and verify a harmless observable effect.

Failure at a gate selects a hybrid model: Rust portable algorithms and host tools, minimal C/C++ for engine access, and assembly for proven hooks. Rust remains primary by code volume and new functionality where reliable.

## Not yet established

- Exact game revision targeted. Hash match is suggestive but needs disc ID/revision corroboration.
- Availability of a Rust PPC cross toolchain in the current environment.
- AllReds hook targets and their intended semantics.
- NetPlay four-input behavior and custom actor determinism in this mod.
- Maximum stable local viewport count and rendering cost.
