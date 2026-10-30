# Research status

## SMG1 reverse engineering

Petari is a public, work-in-progress SMG1 decompilation with a CC0-1.0 repository license. Its README says it contains no game assets or assembly, requires the user's own regional `main.dol`, and builds through its Python configure script and Ninja. It is the preferred source for SMG1-specific symbols, structures, and function context; pin a revision and verify against the selected executable before use. It uses a C++ decompilation/build pipeline rather than a Rust-native engine replacement.

Bussun describes itself as a WIP SMG1 custom-code injector with region symbol maps, Kamek headers/linker, and a memory loader. Its README says CodeWarrior PPC EABI 4.1 is required for C++ ABI compatibility, and its loader currently only works with USA. GitHub's visible root file list has no license file, so do not copy/reuse its code until licensing is clarified. It is useful evidence for existing Galaxy 1 integration constraints, not a chosen dependency.

The SMG2 multiplayer repository is CC0-1.0 and contains PPC assembly hooks, multiple SMG2 symbol maps, and split-screen work. Use it only for conceptual comparison; those symbols and hook sites are not Galaxy 1 compatible. It also contains an `.arc` asset in the root; do not redistribute or assume the code license clears game-derived assets.

The local reference files provide a controlled binary comparison target. Current evidence confirms sizes and SHA-256; DOL header parsing and actual hook disassembly remain pending tool execution. The AllReds file is compiled code, not source. Do not copy it into release artifacts.

## Rust target feasibility

Rust is the default for new portable and host-side code. The host-side DOL library and CLI are Rust. Rust's official target list includes 32-bit `powerpc-unknown-linux-gnu`, but that target describes Linux/glibc and is not the Wii's bare-metal ABI. The current target list does not provide a standard bare-metal 32-bit PowerPC target. Therefore direct Rust injection is **not yet proven**; a custom target specification/build-std approach or another maintained PPC Rust toolchain may be necessary, and neither has been validated here. Before injecting Rust, verify code generation for the Wii Broadway CPU, big-endian PPC EABI/calling convention, relocations/linker, runtime symbols, panic behavior, and final DOL integration. Rust's PowerPC backend alone does not prove compatibility. Keep a C/C++ or assembly integration shim when the decompilation/build toolchain requires it; do not rewrite engine code merely to satisfy a language preference. See the [official Rust platform support list](https://doc.rust-lang.org/rustc/platform-support.html).

## Online model

Dolphin NetPlay forwards/synchronizes controller input through deterministic lockstep; it does not provide a generic game-state replication API. A mod can synchronize player actors if each peer deterministically simulates all actors from the same inputs and game state. This is an inference requiring in-game validation, especially across custom code, floating point, randomness, stage transitions, and saves. Per-client camera presentation may be local-only if view state is excluded from simulation determinism; full-screen per remote player is a presentation feature that ordinary NetPlay does not itself supply.

## Sources

- [Petari](https://github.com/SMGCommunity/Petari) — WIP SMG1 decompilation; CC0-1.0; uses user-provided game executable and Python/Ninja build setup.
- [Bussun](https://github.com/SMGCommunity/Bussun) — WIP SMG1 injector; no license file visible in repository root; USA-only current loader per README; CodeWarrior PPC EABI requirement.
- [SMG2 multiplayer](https://github.com/galaxymaster2007/smg2-multiplayer) — CC0-1.0 source, comparative reference only; no SMG1 compatibility assumed.
- [Luma's Workshop guides](https://lumasworkshop.com/guides/) — WIP Galaxy modding guide describing module-based code injection, models, animations, levels, and other topics; individual game-specific details still require verification.
- [Dolphin NetPlay guide](https://dolphin-emu.org/docs/guides/netplay-guide/) — official operational constraints.
- [Dolphin NetPlay server source](https://github.com/dolphin-emu/dolphin/blob/master/Source/Core/Core/NetPlayServer.cpp) — current upstream implementation, subject to change.
- [Galaxy Patch](https://github.com/Wii-Patches/Galaxy-Patch) — SMG1 control patch description and region support; inspect exact source/license before integration.
- [Galaxy Patch license](https://github.com/Wii-Patches/Galaxy-Patch/blob/main/LICENSE) — MIT.
