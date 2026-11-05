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

The repository has no Dolphin source checkout, so the patch is supplied but not applied or compiled here. It targets exactly tag `2609a`. With an unmodified source checkout, run:

```powershell
.	ools\dolphin-bridge\integrate-2609a.ps1 -SourceRoot C:\path\to\dolphin
```

The script refuses other tags, dirty source trees, preexisting bridge files, or a patch context mismatch. It adds:

- `Source/Core/Core/SharedMemoryBridge.h` and `.cpp`
- an include, singleton, initialization, per-frame call, and shutdown in `Source/Core/Core/Core.cpp`
- the two new sources to `Source/Core/Core/CMakeLists.txt`

Then configure/build Dolphin using the official 2609a Windows instructions. The code requires C++20 (`std::atomic_ref`). The root project does not vendor Dolphin or its build dependencies. Launch the built Dolphin through MultiGalaxy1, then in another PowerShell run:

```powershell
.	arget\debug\multigalaxy.exe bridge-watch rmgp01 60
```

## License and distribution

The bridge files written for MultiGalaxy1 carry WTFPL v2. When incorporated into a Dolphin source tree or distributed in a Dolphin fork, the resulting Dolphin source/binary distribution must comply with Dolphin's GPL-2.0-or-later license and preserve Dolphin's existing copyright and license notices. Keep Dolphin source changes and build artifacts in a separate fork; do not describe the fork as WTFPL. The MultiGalaxy1 launcher and Rust code remain WTFPL. Do not bundle Nintendo game files, saves, or extracted assets.

## Verification status

- Rust payload decoder tests cover schema, PAL identity, revision, RAM address bounds, counters, and stale timeout.
- Rust mapping layout tests assert the 192-byte, 64-byte-aligned outer ABI, and existing synthetic cross-process mapping tests exercise the same mapping name/layout.
- Unified patch syntax parses with `git apply --stat`.
- No Dolphin source checkout/compiler is present in this workspace. The C++ patch has not been applied, compiled, run in Dolphin, or benchmarked. Therefore 60 Hz publication and full-speed gameplay remain unverified acceptance criteria.
