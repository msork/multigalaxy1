# Research status

## SMG1 reverse engineering

Petari is a public work-in-progress SMG1 decompilation with a CC0-1.0 repository license. It requires a user-supplied regional `main.dol` and builds with Python/Ninja. Prefer it for symbols and structures, then verify all mappings against the exact supported executable. Its engine pipeline is C++, not a Rust replacement.

Bussun is a WIP SMG1 injector with region maps and a Kamek/toolchain workflow. Its README requires CodeWarrior PPC EABI for C++ ABI compatibility and says its loader supports USA only. No root license was visible; do not reuse its code without license clarification.

The SMG2 multiplayer repository is CC0-1.0 and contains PPC hooks and SMG2-specific maps. It is conceptual comparison only; no SMG1 compatibility is assumed and game-derived assets must not be redistributed.

Local original and AllReds DOLs permit reproducible static comparison. The Rust CLI reproduced their hashes, DOL sections, changed bytes/runs, and branch targets. AllReds is a compiled binary, not source. Do not copy it into releases. No production PAL revision mapping exists yet because the known original DOL hash is not tied to a verified disc ID/revision.

## Rust runtime feasibility

Rust is primary for host tools, launcher, network, protocol, portable game rules, and tests. `powerpc-unknown-linux-gnu` is not a Wii bare-metal target and no target/toolchain has been validated for Galaxy runtime injection. Before Rust runtime use, prove Broadway code generation, big-endian PPC EABI, linker relocations, runtime symbols, panic behavior, and DOL integration. Keep narrow C/C++/assembly shims when the engine/toolchain requires them. See [Rust platform support](https://doc.rust-lang.org/rustc/platform-support.html).

## Online architecture

The selected product architecture is custom online-only, one Dolphin instance per participant, with independent full-screen cameras. Dolphin's built-in NetPlay and local split-screen are not requirements. An injected game-side bridge must expose local actor state and remote-actor operations through versioned IPC; launcher orchestration alone cannot instantiate in-game characters. No supported generic remote game-state API has been verified. Inspect Dolphin's debugger/memory facilities and source interfaces before deciding whether a narrow Dolphin fork is required. Do not use brittle memory scanning as a product interface.

SM64CoopDX documents client/server, direct/relay transports, network player records, reliable packet support, and game/mod synchronization hooks. That suggests separating transport from a game-level sync API; SM64's engine logic is not portable to Galaxy. Better Sunshine Online (BSMSO) is an especially close reference: its README describes one Dolphin per player, a dedicated state relay, a launcher bridging Dolphin memory through a RAM mailbox, a PPC game module exporting Mario state/rendering remote players, and TCP+UDP server networking. Its repository labels the code GPL-3.0. This is evidence the architecture is plausible, not evidence that Galaxy hooks or integration exist. MultiGalaxy1 currently copies no BSMSO code; any future reuse must preserve GPL obligations and undergo a license compatibility review against this project's MIT license, or be independently implemented.

Russian, French, and English are client-local presentation targets. The requested Russian archive is unaudited; see `docs/references/russian-patch.md`. No multiplayer or locale behavior is runtime verified.

## Sources

- [Petari](https://github.com/SMGCommunity/Petari) — WIP SMG1 decompilation; CC0-1.0; user-provided game executable and Python/Ninja build.
- [Bussun](https://github.com/SMGCommunity/Bussun) — WIP SMG1 injector; USA loader and CodeWarrior PPC EABI constraints per README; license unclear.
- [SMG2 multiplayer](https://github.com/galaxymaster2007/smg2-multiplayer) — comparative source only; no SMG1 compatibility.
- [Luma's Workshop guides](https://lumasworkshop.com/guides/) — Galaxy modding research; verify each game-specific claim.
- [SM64CoopDX](https://github.com/coop-deluxe/sm64coopdx) and [sync hooks](https://github.com/coop-deluxe/sm64coopdx/blob/main/docs/lua/guides/hooks.md) — multiplayer/synchronization reference; verify license before code reuse.
- [Better Super Mario Sunshine Online](https://github.com/Daytendo64/Better-Super-Mario-Sunshine-Online-BSMSO-) — Dolphin-era architecture reference; verify license before code reuse.
- [Galaxy Patch](https://github.com/Wii-Patches/Galaxy-Patch) and [MIT license](https://github.com/Wii-Patches/Galaxy-Patch/blob/main/LICENSE) — SMG1 controller patch reference; exact four-player integration remains unverified.
- [Dolphin source](https://github.com/dolphin-emu/dolphin) — inspect debugger and source integration options for the game-side bridge.
