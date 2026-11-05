# Stock Dolphin bridge feasibility

## Decision

MultiGalaxy1 will use official, unmodified Dolphin only. The former Dolphin 2609a fork/shared-memory source patch and custom build pipeline are retired. Their code and build scripts are removed from the active repository; these notes preserve the investigation. The ignored local build/source trees are outside the project and were not touched. No stock-Dolphin transport is currently verified as a low-overhead, bidirectional game-state bridge.

## Verified stock interfaces

### Gecko and DOL hooks

Dolphin supports Action Replay/Gecko patches, and its patch engine can alter guest memory/code. A verified PAL hook could run game-side PPC code without modifying Dolphin. That solves execution inside the guest only. It does not provide a host IPC endpoint: the guest still needs an available game/IOS networking path, a supported Dolphin IPC, or another reviewed channel. The mandatory AllStars Gecko patch is a separate controller patch and remains required. No MultiGalaxy hook is enabled until exact PAL signatures, overlap checks, and runtime behavior are verified.

### GDB Remote Serial Protocol

Dolphin 2609a has a GDB server with guest memory read/write protocol operations and periodic command processing. MultiGalaxy's Rust client deliberately implements read-only sampling. The server accepts one persistent client and GDB startup pauses the game until the client continues execution. This remains useful for development diagnostics, but GDB is not selected for production transport: user testing reported slowdown even after sampler STOP, and this workspace has no numerical controlled gameplay benchmark to quantify it. Do not convert that report into an invented FPS result.

### MemoryWatcher

Dolphin 2609a's MemoryWatcher is conditionally compiled only under UNIX in its top-level CMake configuration. Its implementation observes configured addresses at frame end and sends changed values through an AF_UNIX datagram socket. Therefore the official Windows 2609a build does not include this facility, and even on supported builds it is an outbound changed-value watcher, not a general bidirectional state API.

### External process memory

ReadProcessMemory/WriteProcessMemory are Windows OS mechanisms, not a Dolphin-supported guest-memory API. Guest RAM backing pointers are internal and relocatable; external sampling can race emulation and requires a verified mapping from guest addresses to host memory. No such mapping is accepted or tested here. Do not scan for host pointers or write through guessed addresses.

## Feasibility test matrix

| Method | Stock Dolphin | Direction | Windows 2609a status | Current disposition |
|---|---|---|---|---|
| Gecko/DOL guest hook | Yes | Guest execution only | Supported patch mechanism | Candidate for verified game logic; IPC unresolved |
| GDB RSP | Yes | Read and protocol-level write | Available, pauses on startup; overhead unmeasured | Development-only, read-only in this project |
| MemoryWatcher | Yes, conditional build | Dolphin to client | Not compiled by Windows 2609a; Unix socket, changed values | Not suitable for current Windows target |
| External process memory | OS-level | Potentially both | Unsupported mapping/race problem | No implementation until mapping and safety proven |
| Stock Dolphin IPC | No general game-state API identified | — | No supported API identified | Research only |

## Minimal stock proof and benchmark

Verified locally with the official installed DolphinTool: `validate-pal-game <stock Dolphin.exe> <PAL RVZ>` reported PAL `RMGP01` revision 0. This reads the image and does not modify it. The official Dolphin binary itself was not modified.

No matched gameplay benchmark could be run from the automation desktop: Dolphin GUI processes created there are on a noninteractive desktop and are not visible to the user. The launcher preserves its guard and will not pretend such a run is a valid visual/runtime test. The needed interactive benchmark is normal stock Dolphin versus GDB-attached-idle versus active sampling, on the same scene and profile; capture Dolphin Speed/FPS/VPS and process CPU for repeated 30-second samples. GDB unattached is paused and is not a gameplay comparison. No numerical results are claimed.

## Next gate

Before writing a transport implementation, produce a stock-only prototype that demonstrates (a) guest hook activation on exact PAL Rev 0, (b) safe bidirectional exchange at approximately 60 Hz, and (c) no noticeable gameplay slowdown. If no supported stock channel can satisfy these constraints, document that online actor synchronization cannot be delivered under the stock-only requirement and present alternatives for user decision; do not silently restore a Dolphin fork.

## References

- [Dolphin 2609a GDBStub.cpp](https://github.com/dolphin-emu/dolphin/blob/2609a/Source/Core/Core/PowerPC/GDBStub.cpp)
- [Dolphin 2609a top-level CMakeLists.txt](https://github.com/dolphin-emu/dolphin/blob/2609a/CMakeLists.txt)
- [Dolphin 2609a MemoryWatcher header](https://github.com/dolphin-emu/dolphin/blob/2609a/Source/Core/Core/MemoryWatcher.h)
- [Dolphin patch engine](https://github.com/dolphin-emu/dolphin/blob/2609a/Source/Core/Core/PatchEngine.cpp)
- [Dolphin progress report on Gecko patch behavior](https://dolphin-emu.org/blog/2018/11/01/dolphin-progress-report-sept-and-oct-2018/)
