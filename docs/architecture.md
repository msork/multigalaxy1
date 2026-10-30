# Architecture

## Language policy

Rust is primary. Use Rust for portable gameplay rules, player-slot and controller models, deterministic synchronization logic, camera planning, patch generation, host tools, and tests. PowerPC assembly is restricted to verified trampolines and ABI-sensitive hooks. C/C++ is limited to requirements imposed by the original engine, Petari, or Wii toolchains. Python is for exceptional supporting scripts only.

## Workspace

Current crates:

- `multigalaxy-patch`: safe DOL parsing and comparison; eventual validation and patch conflict model.
- `multigalaxy` CLI: host-facing inspection and patch commands.

Planned crates (introduced as their APIs become testable): `multigalaxy-core`, `multigalaxy-player`, `multigalaxy-characters`, `multigalaxy-input`, `multigalaxy-camera`, `multigalaxy-network`. Wii runtime integration belongs in `game/hooks`, `game/bindings`, and `game/runtime`; use `no_std` only after target feasibility passes.

## Boundaries

Portable Rust logic must not read arbitrary game memory. A narrow game binding layer exposes verified engine operations; every unsafe operation stays there with documented assumptions. Player identity is a fixed slot enum and per-player state is indexed by slot, avoiding accidental shared mutable controller or camera state. Deterministic gameplay state is separated from presentation state so each online peer may render a local camera arrangement.

## Engine integration

The initial implementation must identify one verified SMG1 revision. Hooks require source-backed symbols or reproducible disassembly, expected original instructions, overwrite size, ABI, and collision detection. The Rust code cannot replace the game's native C++ engine. A hook can dispatch to Rust only if the compiler ABI/linker/runtime are proven compatible.
