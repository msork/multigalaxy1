# Repository guidance

- MultiGalaxy1 is PAL-only and **online-only**. Do not add Dolphin NetPlay or local split-screen requirements/plans. Each participant runs one Dolphin instance with a full-screen local camera.
- Rust is default for portable logic, launcher, transport, protocol, patch tooling, and tests. Use minimal C/C++/PPC only for verified engine integration; isolate and document FFI and `unsafe`.
- Never apply guessed addresses or offsets. PAL maps bind exact game ID, disc revision, DOL hash, provenance, and original instruction signatures. Unknown builds fail closed.
- The online design needs a game-side bridge: Dolphin does not provide an established supported general-purpose remote game-state API. Investigate debugger/memory/source APIs; prefer versioned IPC from an injected bridge over brittle memory scanning.
- Network identity, transport, lobby, prediction, interpolation, authority, and resync belong in Rust crates. The server arbitrates shared progression and contested interactions; clients may predict local movement and reconcile authoritative state.
- Every player has independent controls, character, camera, and client-local presentation. Locale assets must not affect gameplay state. Russian/French/English PAL is a required test, not a compatibility claim.
- Character slots: Mario, Luigi, Wario, Waluigi. Keep portable character rules separate from engine bindings.
- Do not commit game executables, disc images, extracted proprietary assets, secrets, or generated binaries. Do not run unknown installers.
- Specs precede implementation; milestone status must match evidence. Compilation does not establish Dolphin boot or online gameplay.
- Do not push commits.
- Original MultiGalaxy1 code uses WTFPL Version 2. Preserve all third-party licenses, copyright notices, and attribution; never imply project licensing applies to third-party code, patches, or assets.
- Preferred Classic Controller path is Starlightbotanist's SMG/SMG2 Dolphin patch repository, with original patch attribution to SuperHackio. Do not bundle its patch files unless permission/license is verified. Validate the exact PAL build and conflicts before enabling alongside game hooks.
