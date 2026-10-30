# Dolphin runtime evidence

The Windows development environment has Dolphin `2609a` at the user-supplied installation path and a user-supplied PAL RVZ. DolphinTool reports disc ID `RMGP01`, PAL region, revision 0, country Europe, internal name `SUPER MARIO GALAXY`; RVZ verification reported no problems and raw-image SHA-1 `4ad985eb39b6f60be00a44c3a41bc3ed31b083c2` (the tool also printed `Error creating key`, so this verification output is recorded with that caveat). The RVZ was not modified.

A Rust GDB Remote Serial Protocol client successfully read 16 bytes at guest address `0x80000000` from the running Dolphin instance. The bytes decode to `RMGP01` followed by zeroes. This proves the local GDB memory-read path, not player-coordinate extraction or gameplay.

The current user-supplied `reference/original/main.dol` is 6,283,264 bytes (SHA-1 `9bd1daa520bd1f573b6a4b82e03336e9c51b511f`, SHA-256 `8200fb72b3170069bb71c8b3dc21b49e429977ce8ae5159bb95ea6a19de0fbee`). DolphinTool identifies the loaded disc as `RMGP01`, PAL revision 0. A read-only full section comparison against Dolphin 2609a matched every Text and Data section. This verifies the supplied DOL against the running PAL session at comparison time. No player global, coordinate structure, function address, or hook has yet been mapped.

Dolphin's GDB stub is configured through `Main.General.GDBPort`; its implementation supports memory reads and writes at the protocol level. The MultiGalaxy Rust client intentionally implements reads only. On this Dolphin 2609a test, the listener appeared on `0.0.0.0`, not just loopback, even though the client connects to `127.0.0.1`. Dolphin must be stopped after debugging. Dolphin's MemoryWatcher implementation was not validated for this Windows build; it is not used by the bridge.

Research references:

- [Dolphin GDB stub](https://github.com/dolphin-emu/dolphin/blob/master/Source/Core/Core/PowerPC/GDBStub.cpp)
- [Dolphin main configuration settings](https://github.com/dolphin-emu/dolphin/blob/master/Source/Core/Core/Config/MainSettings.cpp)
- [Petari SMG1 Mario implementation](https://github.com/SMGCommunity/Petari/blob/master/src/Game/Player/Mario.cpp)
- [Petari RMGP01 decompilation status](https://decomp.fun/SMGCommunity/Petari/RMGP01/)

## Why the reference DOL differed from live PAL memory

Before the user replaced it, the file at `reference/original/main.dol` had SHA-1 `9a71008ae1ee9010e267fa67d1f0b0d4f0e895dd`, which the archived USA decompilation identifies as the USA `RMGE01` Rev 0 executable. That USA file is no longer retained. Its prior comparison to the PAL session differed at `0x800041a3`; that was a cross-region comparison, not evidence of RVZ corruption. The replacement PAL DOL now matches every loaded section (see the current fingerprint above).

A full section scan compared the replacement PAL candidate against live PAL RAM. Every Text and Data section matched; no code or data differences were observed at that sample. This is the exact PAL DOL fingerprint now used for further symbol and actor analysis.

The isolated launcher profile has GDB enabled and test audio mute only; it contains no game-specific cheats or Action Replay entries. Dolphin's generic `RMG.ini` only defines video hacks/enhancements, not code patches. GDB's `ReadMemory` implementation first checks that the guest address is RAM, then obtains the address with `GetPointerForRange` and hex-encodes those bytes. Thus it reads the current emulated RAM contents without applying a separate address translation at the client. It does not promise those bytes are still identical to the original DOL after the game has executed.

The earlier complete comparison also exposed a bug in our GDB client: an `m` packet's memory payload is hexadecimal and may begin with `E` (for example `ec02002a`). Treating every response whose first character was `E` as an error rejected valid RAM. The client now recognizes only the three-character `Ehh` RSP error form, and a synthetic regression test covers payloads beginning with `E`.

## Petari player-state path

Petari's current source semantics show a dynamic path rather than a fixed player singleton: `MR::getMarioHolder()` resolves a `MarioHolder` from the scene-object holder registry; `MarioHolder` owns a `MarioActor*`; `MarioActor` creates its `Mario` movement object and uses the inherited `LiveActor::mPosition`. The actor's position is therefore the first state field to target once the PAL instance address is resolved. Actor and holder pointers are allocated at runtime, so source names alone do not provide a stable memory address.

Petari's current `configure.py` lists `RMGP01` as a first-class version. Its PAL `config.yml` and `build.sha1` specify SHA-1 `9BD1DAA520BD1F573B6A4B82E03336E9C51B511F`, exactly matching the user-supplied DOL. Petari's PAL `splits.txt` therefore provides an exact-hash-matched static map. It places `MarioActor.cpp` code in `0x802AE6C8..0x802B5228`, `MarioHolder.cpp` in `0x802E1490..0x802E156C`, `MarioAccess.cpp` in `0x80302384..0x803048F4`, and `SceneObjHolder.cpp` in `0x803449F4..0x80345840`. These are translation-unit ranges, not individual function entries or runtime object addresses.

Petari source semantics provide a concrete investigation path: `MarioAccess::getPlayerActor()` calls `MR::getMarioHolder()->getMarioActor()`; `MR::getMarioHolder()` resolves `SceneObj_MarioHolder` from the scene-object holder; `MarioHolder` stores its `MarioActor*`; and the actor inherits `LiveActor::mPosition`. Static class layout sources expose `GameSystem::mSceneController` at `+0x24`, `GameSystemSceneController::mScene` at `+0xAC`, `Scene::mSceneObjHolder` at `+0x10`, `SceneObj_MarioHolder == 0x14` (slot at holder base `+0x50` for 32-bit pointers), and `LiveActor::mPosition` at `+0x0C`. `MarioHolder::mActor` follows its `NameObj` base; the expected `+0x0C` is an ABI-derived layout, not yet checked against a compiler map. Petari's `SingletonHolder<GameSystem>::sInstance` is the root of the source-level path, but its exact PAL address is not mapped here. The runtime chain is therefore incomplete and none of these offsets have yet been followed against live Mario memory.

No PAL disassembly/xref validation has been completed: this Windows environment currently has no Ghidra or PowerPC objdump available, and the PAL `symbols.txt` is too large for the connected text-file fetch to return. Petari's `splits.txt` provides only translation-unit ranges, not individual function starts. In particular, do not treat those ranges or a float-looking RAM value as the singleton root, actor pointer, or position.

The Rust revision catalog now recognizes this exact PAL Rev 0 DOL fingerprint as a verified build identity, with an empty hook list. That is identity evidence only; it does not make any player-state pointer or hook usable.

Sources:

- [Archived SMG USA decompilation and exact USA DOL SHA-1](https://github.com/doldecomp/smg)
- [Petari Mario access code](https://github.com/SMGCommunity/Petari/blob/master/src/Game/Player/MarioAccess.cpp)
- [Petari Mario holder implementation](https://github.com/SMGCommunity/Petari/blob/master/src/Game/Player/MarioHolder.cpp)
- [Petari Mario actor implementation](https://github.com/SMGCommunity/Petari/blob/master/src/Game/Player/MarioActor.cpp)
- [Petari Mario holder layout](https://github.com/SMGCommunity/Petari/blob/master/include/Game/Player/MarioHolder.hpp)
- [Petari LiveActor position field](https://github.com/SMGCommunity/Petari/blob/master/include/Game/LiveActor/LiveActor.hpp)
- [Petari scene-object holder layout](https://github.com/SMGCommunity/Petari/blob/master/include/Game/Scene/SceneObjHolder.hpp)
- [Petari game and scene controller layouts](https://github.com/SMGCommunity/Petari/blob/master/include/Game/System/GameSystem.hpp)
- [Petari PAL build SHA-1](https://github.com/SMGCommunity/Petari/blob/master/config/RMGP01/build.sha1)
- [Petari PAL configuration](https://github.com/SMGCommunity/Petari/blob/master/config/RMGP01/config.yml)
- [Petari PAL source split ranges](https://github.com/SMGCommunity/Petari/blob/master/config/RMGP01/splits.txt)
- [Petari PAL version selection and build configuration](https://github.com/SMGCommunity/Petari/blob/master/configure.py)
- [Dolphin GDB memory-read implementation](https://github.com/dolphin-emu/dolphin/blob/master/Source/Core/Core/PowerPC/GDBStub.cpp)
