# Dolphin-Integrated Shared-Memory Bridge Prototype

## Objective

Provide a low-overhead path for Dolphin 2609a to publish verified emulator state to the Rust launcher. GDB remains development-only. The initial proof publishes game identity, callback heartbeat/frame count, and one guarded read-only RAM test word. Mario state and game writes are excluded.

## Verified research

- Dolphin 2609a exposes `Core::OnFrameEnd(Core::System&)` in `Source/Core/Core/Core.cpp`; the existing optional MemoryWatcher is stepped there with a CPU-thread guard.
- `Core::CPUThreadGuard` protects CPU-thread access. `PowerPC::MMU::HostIsRAMAddress` and `HostTryRead<T>` are declared in `Source/Core/Core/PowerPC/MMU.h`; `HostTryRead` is the non-alerting read path.
- Dolphin's `Memory::MemoryManager` in `Source/Core/Core/HW/Memmap.cpp` maps the emulated main RAM region. MultiGalaxy has already read the guest RAM base at `0x80000000` through the live PAL GDB session; this prototype uses that known base only, with the MMU range check before reading.
- `SConfig::GetGameID()` and `GetRevision()` are available in 2609a `Source/Core/Core/ConfigManager.h` for fail-closed identity gating.
- Dolphin source files carry GPL-2.0-or-later. Original MultiGalaxy1 glue retains WTFPL v2. A distributed Dolphin fork must meet Dolphin's GPL obligations and preserve upstream notices.

## Functional requirements

- Support only PAL `RMGP01` revision 0 in this prototype.
- Do all Windows mapping and owner-token setup before starting the emulation thread.
- Publish once at the existing frame-end CPU callback, without a socket, filesystem access, allocation, blocking wait, or emulated-memory write in the callback.
- Use the existing 192-byte, 64-byte-aligned outer snapshot ABI version 1 and a 48-byte versioned little-endian payload.
- Publish callback counter/heartbeat every callback. A failed RAM read clears its validity flag while preserving heartbeat publication.
- Rust opens a read-only mapping, validates ABI, schema, identity, bounds, and heartbeat freshness, and exits on malformed content.
- Disable the publisher on absent opt-in environment variable, unsupported platform, wrong game identity, mapping collision/failure, invalid shared ABI, or invalid sequence state.

## Technical design

`tools/dolphin-bridge/SharedMemoryBridge.{h,cpp}` supplies the Dolphin-side RAII publisher. `integration-2609a.patch` connects initialization in `Core::Init`, publication in `Core::OnFrameEnd`, cleanup in `Core::Shutdown`, and both files in `Source/Core/Core/CMakeLists.txt`. `integrate-2609a.ps1` refuses source trees not exactly at tag 2609a, dirty trees, preexisting integration files, and context mismatches.

The Rust `bridge_protocol` module decodes fixed payload fields and checks staleness using host monotonic time (500 ms timeout). `bridge-watch <name> <seconds>` samples a read-only Windows mapping. The launcher gives Dolphin the isolated mapping name `rmgp01`. The Dolphin C++ code requires C++20 `std::atomic_ref`; compile-time layout/lock-free assertions must match Rust tests before use.

## Dependencies

- Dolphin 2609a source and its normal Windows CMake/Visual Studio build dependencies for the actual emulator integration.
- Rust stable workspace toolchain for the reader, protocol checks, and tests.
- Windows named file mappings and lock-free 8/32/64-bit atomic operations.

## Risks

- Dolphin source is unavailable in this workspace, so patch application, C++ compilation, and runtime callback rate have not been verified.
- The emulated field callback may run at PAL cadence below 60 Hz. No independent timer may safely fabricate extra current game state snapshots; actual cadence must be measured.
- `std::atomic_ref` and Rust atomics share the documented fixed layout only on supported lock-free targets; layout assertions must fail the build elsewhere.
- The snapshot's sample word is a smoke-test field, not a meaningful gameplay value.
- AllStars remains applied by the MultiGalaxy launcher. The Dolphin publisher independently checks game ID and revision, while game-image/DOL hash validation remains the launcher's responsibility.
- A GPL Dolphin fork imposes GPL source/license obligations on Dolphin and its modifications. It does not change the WTFPL license of standalone original MultiGalaxy1 files.

## Acceptance criteria

- Rust tests reject unsupported ABI/schema/game revision, bad sample addresses, invalid flags, and stale heartbeat; layout tests match the C++ assertions.
- The source integration patch applies only to Dolphin 2609a and builds on Windows.
- A patched Dolphin produces a changed frame/heartbeat every emulated field and a valid guarded RAM sample.
- Dolphin's Speed/FPS and CPU measurements remain within measurement noise versus the same unpatched 2609a session.
- No Mario coordinates, game-state writes, or multiplayer functionality are claimed by this milestone.

## Testing strategy

- Run `cargo fmt --all`, `cargo test --workspace`, and `cargo build --workspace`.
- Validate unified patch syntax and check patch contexts on an exact clean 2609a checkout.
- Build patched Dolphin and use `bridge-watch rmgp01 60` while PAL SMG1 runs.
- Record callback rate, stale behavior after pause/stop, read validity, Dolphin OSD Speed/FPS, and process CPU with the bridge disabled/enabled in matched gameplay runs.
- Test ABI mismatch and duplicate publisher fail-safe behavior without touching the user's regular Dolphin profile, game image, or saves.
