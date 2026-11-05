# Dolphin 2609a bridge source patch

This is an opt-in Windows source patch for the official Dolphin `2609a` tag. It is the first Dolphin-side producer for the Rust shared snapshot ABI. It does not access Mario structures, write emulated memory, alter the game's DOL/RVZ, or implement multiplayer.

## What it publishes

The launcher sets `MULTIGALAXY_BRIDGE_NAME=rmgp01` for Dolphin. On `Core::Init`, the patch checks the running title is exactly PAL `RMGP01` revision 0 and initializes a named mapping at `Local\MultiGalaxy1-rmgp01`. If the variable is absent, the patch does not create a mapping; `OnFrameEnd` retains one disabled-flag check but performs no MMU or mapping work. Mapping setup happens before the emulation thread starts.

`Core::OnFrameEnd` invokes the publisher from Dolphin's CPU thread. Each publication contains a 48-byte little-endian payload inside outer ABI version 1:

| Offset | Size | Field |
| ---: | ---: | --- |
| 0 | 4 | payload schema, 1 |
| 4 | 4 | game revision, 0 |
| 8 | 6 | game ID, `RMGP01` |
| 14 | 2 | reserved, zero |
| 16 | 4 | flags: identity-valid and RAM-sample-valid |
| 20 | 4 | reserved, zero |
| 24 | 8 | emulated frame-end callback counter |
| 32 | 8 | heartbeat counter (same monotonically increasing tick) |
| 40 | 4 | sample guest address, `0x80000000` |
| 44 | 4 | read-only word obtained through guarded `HostTryRead` |

The sample address is the standard MEM1 base confirmed by Dolphin's memory map; the MMU's `HostIsRAMAddress` check and optional `HostTryRead` run on the CPU thread. If either check fails, the heartbeat still publishes and the RAM-sample-valid flag is clear. The counter measures this callback cadence, not an independently verified game simulation frame number. PAL cadence may be below 60 per second; only runtime measurement can establish the rate.

The Windows mapping mirrors the Rust `#[repr(C, align(64))]` header. C++ static assertions verify offsets, alignment, and size. Its sequence counter uses lock-free `std::atomic_ref` fields, and payload publication is bounded to 48 atomic byte stores. A named owner event rejects a second producer. A missing environment variable, nonmatching game identity, mapping collision/failure, invalid ABI, or corrupt odd sequence disables the producer safely. No sockets, allocations, waits, file operations, or memory writes occur in the frame callback.

The Rust `bridge-watch rmgp01 60` command opens the mapping read-only, validates outer ABI and payload schema/identity/range, prints each new heartbeat, and reports stale after 500 ms without a changed heartbeat. Run it after Dolphin has booted; it currently exits with a clear mapping error if the producer has not created the mapping yet. For the disabled baseline, use `dev-launch ... --bridge-disabled`; the production `launch` command always enables publisher opt-in.

## Applying to Dolphin 2609a

The ignored development area contains the official Dolphin `2609a` Git checkout at commit `409881c34357d0f105fd473167d15ab0bd9c1628`, with pinned submodules. The integration helper applied the patch and copied both source files into this exact checkout; `git diff --check` passed. No Dolphin sources or build artifacts are tracked by MultiGalaxy1.

```powershell
.\tools\dolphin-bridge\integrate-2609a.ps1 -SourceRoot C:\path\to\dolphin
```

The script refuses other tags, dirty source trees, preexisting bridge files, or a patch context mismatch. It adds:

- `Source/Core/Core/SharedMemoryBridge.h` and `.cpp`
- an include, singleton, initialization, per-frame call, and shutdown in `Source/Core/Core/Core.cpp`
- the two new sources to `Source/Core/Core/CMakeLists.txt`

The Windows 2609a source has a hard compiler-version guard requiring Visual Studio 2026. The initial VS 2022/MSVC 19.44 build failed at `Source/PCH/pch.h` with Dolphin's explicit Visual Studio 2026 requirement. Official Build Tools 2026 18.11.3 (MSVC 14.51.36231) has since been installed. Using its x64 developer environment, Dolphin configured and built successfully with a local preset. `CMakeUserPresets.json` and its build directory exist only in the ignored Dolphin checkout to keep the earlier VS 2022 configure separate:

```cmd
call "C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\VC\Auxiliary\Build\vcvars64.bat"
cmake --preset mg1-vs26 -DUSE_SYSTEM_LIBS=OFF
cmake --build --preset mg1-build-vs26 --target dolphin-emu --parallel 6
```

This compiled and linked `build/release/x64-vs26/Binaries/Dolphin.exe` from the patched `core` target. For a fresh source checkout without a previous build cache, upstream's `cmake --workflow ninja-release-x64` preset is the standard alternative.

The C++ code requires C++20 (`std::atomic_ref`). Launch a successful build through MultiGalaxy1 with the isolated profile and mandatory AllStars patch, then in another PowerShell run:

```powershell
.\target\debug\multigalaxy.exe bridge-watch rmgp01 60
```

## License and distribution

The bridge files written for MultiGalaxy1 carry WTFPL v2. When incorporated into a Dolphin source tree or distributed in a Dolphin fork, the resulting Dolphin source/binary distribution must comply with Dolphin's GPL-2.0-or-later license and preserve Dolphin's existing copyright and license notices. Keep Dolphin source changes and build artifacts in a separate fork; do not describe the fork as WTFPL. The MultiGalaxy1 launcher and Rust code remain WTFPL. Do not bundle Nintendo game files, saves, or extracted assets.

## Verification status

- Rust payload decoder, stale heartbeat, and mapping layout tests pass in the workspace.
- Unified patch applies cleanly to exact Dolphin tag `2609a`; the helper enforces the exact tag and clean source tree.
- CMake configure completed with bundled dependencies.
- VS 2022/MSVC 19.44 was rejected by Dolphin's explicit compiler guard. VS Build Tools 2026 18.11.3/MSVC 14.51.36231 then compiled `SharedMemoryBridge.cpp` and patched `Core.cpp` and linked the release `Dolphin.exe` successfully.
- The GUI was not started from this automation desktop: it is `CodexSandboxDesktop-*`, not the interactive `WinSta0\Default` desktop. The launcher refused before starting Dolphin.
- Dolphin boot, AllStars runtime activation, live identity/heartbeat/MEM1 sample, callback cadence, and bridge-on/bridge-off performance remain unverified acceptance criteria.
