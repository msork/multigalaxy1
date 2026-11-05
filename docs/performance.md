# Stock Dolphin performance and bridge benchmark

## Purpose

Test whether an unmodified official Dolphin can support the online mod's live state bridge. The retired shared-memory source patch and its benchmark modes are not part of the product.

## Known evidence

Dolphin 2609a's GDB stub schedules command processing every 100,000 emulated cycles while active and pauses at startup until a client continues execution. This is a source-level mechanism, not a measured performance cost. The user reported severe slowdown even after sampler STOP; no numeric FPS/CPU samples are recorded, so quantify with a controlled interactive test before making a performance claim.

MemoryWatcher is UNIX-only in the official 2609a CMake configuration and uses a one-way AF_UNIX datagram stream. External process-memory polling has no supported guest-RAM mapping and can race the emulation thread. Gecko injection can execute PPC code but needs a separate host communication path. Details and source links: [stock Dolphin bridge feasibility](stock-dolphin-bridge.md).

## Interactive benchmark

Use the same PAL scene, Dolphin backend, profile, controller, and input sequence. Run three 30-second samples after warm-up for each playable condition:

1. Official Dolphin, GDB off.
2. Official Dolphin, GDB attached and idle (`STOP`).
3. Official Dolphin, GDB actively sampling.

Record Dolphin Speed/FPS/VPS OSD and `bench-dolphin` CPU output. GDB unattached remains paused, so it is not a gameplay comparison. Perform this from interactive `WinSta0\Default`; the automation desktop is not user-visible and must not be used to claim runtime performance.

## Current test status

A synthetic Windows named-mapping experiment previously demonstrated about 61 publications/s between two Rust processes. This is only an IPC transport micro-test; it does not involve Dolphin, guest RAM, or emulation overhead. The former fork bridge comparison is retired. No matched stock-Dolphin benchmark has been performed. The stock proof presently verified is read-only PAL RMGP01 Rev 0 validation through official DolphinTool.

Do not implement a production bridge until a stock-only prototype demonstrates reliable bidirectional state exchange near 60 Hz and no noticeable slowdown. If that cannot be done through a supported stock interface, document the blocker and alternatives without restoring a fork.
