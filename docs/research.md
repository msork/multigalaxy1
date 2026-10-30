# Research status

## SMG1 reverse engineering

Petari is the SMG1-specific community decompilation and is the most relevant source for named functions and structures. Treat its symbols as leads until revision and coverage are confirmed against the selected executable. The source repository and its license must be checked at a pinned revision before code or dependency reuse. Bussun and `smg2-multiplayer` are reference material only: SMG2 code and layouts are not presumed compatible with SMG1.

The local reference files provide a controlled binary comparison target. Current evidence confirms sizes and SHA-256; DOL header parsing and actual hook disassembly remain pending tool execution. The AllReds file is compiled code, not source. Do not copy it into release artifacts.

## Rust target feasibility

Rust is the default for new portable and host-side code. The host-side DOL library and CLI are Rust. Rust's official target list includes 32-bit `powerpc-unknown-linux-gnu`, but that target describes Linux/glibc and is not the Wii's bare-metal ABI. The current target list does not provide a standard bare-metal 32-bit PowerPC target. Therefore direct Rust injection is **not yet proven**; a custom target specification/build-std approach or another maintained PPC Rust toolchain may be necessary, and neither has been validated here. Before injecting Rust, verify code generation for the Wii Broadway CPU, big-endian PPC EABI/calling convention, relocations/linker, runtime symbols, panic behavior, and final DOL integration. Rust's PowerPC backend alone does not prove compatibility. Keep a C/C++ or assembly integration shim when the decompilation/build toolchain requires it; do not rewrite engine code merely to satisfy a language preference. See the [official Rust platform support list](https://doc.rust-lang.org/rustc/platform-support.html).

## Online model

Dolphin NetPlay forwards/synchronizes controller input through deterministic lockstep; it does not provide a generic game-state replication API. A mod can synchronize player actors if each peer deterministically simulates all actors from the same inputs and game state. This is an inference requiring in-game validation, especially across custom code, floating point, randomness, stage transitions, and saves. Per-client camera presentation may be local-only if view state is excluded from simulation determinism; full-screen per remote player is a presentation feature that ordinary NetPlay does not itself supply.

## Sources

- [Petari](https://github.com/SMGCommunity/Petari) — SMG1 decompilation.
- [Bussun](https://github.com/SMGCommunity/Bussun) — inspect before relying; no compatibility assumed.
- [SMG2 multiplayer](https://github.com/galaxymaster2007/smg2-multiplayer) — comparative reference only.
- [Dolphin NetPlay guide](https://dolphin-emu.org/docs/guides/netplay-guide/) — official operational constraints.
- [Dolphin NetPlay server source](https://github.com/dolphin-emu/dolphin/blob/master/Source/Core/Core/NetPlayServer.cpp) — current upstream implementation, subject to change.
- [Galaxy Patch](https://github.com/Wii-Patches/Galaxy-Patch) — SMG1 control patch description and region support; inspect exact source/license before integration.
- [Luma's Workshop guides](https://lumasworkshop.com/guides/) — community modding reference, verify individual guide content before relying.
