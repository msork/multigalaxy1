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
