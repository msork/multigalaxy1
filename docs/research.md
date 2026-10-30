# Dolphin runtime evidence

The Windows development environment has Dolphin `2609a` at the user-supplied installation path and a user-supplied PAL RVZ. DolphinTool reports disc ID `RMGP01`, PAL region, revision 0, country Europe, internal name `SUPER MARIO GALAXY`; RVZ verification reported no problems and raw-image SHA-1 `4ad985eb39b6f60be00a44c3a41bc3ed31b083c2` (the tool also printed `Error creating key`, so this verification output is recorded with that caveat). The RVZ was not modified.

A Rust GDB Remote Serial Protocol client successfully read 16 bytes at guest address `0x80000000` from the running Dolphin instance. The bytes decode to `RMGP01` followed by zeroes. This proves the local GDB memory-read path, not player-coordinate extraction or gameplay.

The previously available `reference/original/main.dol` is not confirmed to be the DOL extracted from this RVZ. Comparing its section bytes to loaded guest memory found the first mismatch in Text section 0 at guest address `0x800041a3`. Therefore its hash and any offsets derived from it must not be treated as the RVZ's verified executable mapping. No player global, coordinate structure, function address, or hook is approved from this evidence. The next reverse-engineering step is to acquire a hash-identified main.dol from this exact user-owned PAL revision and map it against Petari's verified symbols/build data.

Dolphin's GDB stub is configured through `Main.General.GDBPort`; its implementation supports memory reads and writes at the protocol level. The MultiGalaxy Rust client intentionally implements reads only. On this Dolphin 2609a test, the listener appeared on `0.0.0.0`, not just loopback, even though the client connects to `127.0.0.1`. Dolphin must be stopped after debugging. Dolphin's MemoryWatcher implementation was not validated for this Windows build; it is not used by the bridge.

Research references:

- [Dolphin GDB stub](https://github.com/dolphin-emu/dolphin/blob/master/Source/Core/Core/PowerPC/GDBStub.cpp)
- [Dolphin main configuration settings](https://github.com/dolphin-emu/dolphin/blob/master/Source/Core/Core/Config/MainSettings.cpp)
- [Petari SMG1 Mario implementation](https://github.com/SMGCommunity/Petari/blob/master/src/Game/Player/Mario.cpp)
- [Petari RMGP01 decompilation status](https://decomp.fun/SMGCommunity/Petari/RMGP01/)

## Why the reference DOL differed from live PAL memory

The reference file `reference/original/main.dol` has SHA-1 `9a71008ae1ee9010e267fa67d1f0b0d4f0e895dd` and SHA-256 `2c680585a8f58e1cc9c5521b579057f12b124ff0ef409e470a57606c50a93c09`. The archived SMG USA decompilation identifies that exact SHA-1 as its rebuilt retail `main.dol`, establishing this reference as the USA `RMGE01` Rev 0 executable. DolphinTool identifies the live RVZ independently as `RMGP01`, PAL revision 0. The prior comparison therefore compared two regions; it was not evidence of RVZ corruption.

A full section scan compared the USA candidate against live PAL RAM. Text sections differ substantially, while all listed DOL data sections matched in this run. This scan cannot distinguish normal region code differences from runtime code changes because the candidate is the wrong region and Dolphin reports current RAM. It should not be used as a PAL DOL fingerprint.

The isolated launcher profile has GDB enabled and test audio mute only; it contains no game-specific cheats or Action Replay entries. Dolphin's generic `RMG.ini` only defines video hacks/enhancements, not code patches. GDB's `ReadMemory` implementation first checks that the guest address is RAM, then obtains the address with `GetPointerForRange` and hex-encodes those bytes. Thus it reads the current emulated RAM contents without applying a separate address translation at the client. It does not promise those bytes are still identical to the original DOL after the game has executed.

The earlier complete comparison also exposed a bug in our GDB client: an `m` packet's memory payload is hexadecimal and may begin with `E` (for example `ec02002a`). Treating every response whose first character was `E` as an error rejected valid RAM. The client now recognizes only the three-character `Ehh` RSP error form, and a synthetic regression test covers payloads beginning with `E`.

## Petari player-state path

Petari's current source semantics show a dynamic path rather than a fixed player singleton: `MR::getMarioHolder()` resolves a `MarioHolder` from the scene-object holder registry; `MarioHolder` owns a `MarioActor*`; `MarioActor` creates its `Mario` movement object and uses the inherited `LiveActor::mPosition`. The actor's position is therefore the first state field to target once the PAL instance address is resolved. Actor and holder pointers are allocated at runtime, so source names alone do not provide a stable memory address.

The public Petari master config directory currently exposes an `RMGK01` map, not a verified PAL (`RMGP01`) address map. Semantic source findings are useful for analysis, but no Petari address or structure offset has been imported into the runtime reader. A PAL-specific mapping still needs the exact `RMGP01` DOL identity and matching symbols/structure layout. Rotation and state-field offsets are also not registered.

Sources:

- [Archived SMG USA decompilation and exact USA DOL SHA-1](https://github.com/doldecomp/smg)
- [Petari Mario access code](https://github.com/SMGCommunity/Petari/blob/master/src/Game/Player/MarioAccess.cpp)
- [Petari Mario holder implementation](https://github.com/SMGCommunity/Petari/blob/master/src/Game/Player/MarioHolder.cpp)
- [Petari Mario actor implementation](https://github.com/SMGCommunity/Petari/blob/master/src/Game/Player/MarioActor.cpp)
- [Dolphin GDB memory-read implementation](https://github.com/dolphin-emu/dolphin/blob/master/Source/Core/Core/PowerPC/GDBStub.cpp)
