# Runtime Memory Bridge and GDB Performance Benchmark

## Objective

Measure Dolphin's actual runtime cost with no GDB, GDB enabled but unattached, GDB attached and idle, and active Rust sampling. Select a production memory transport that supports frequent state transfer without materially reducing SMG1 emulation speed.

## Verified research

- PAL target remains RMGP01 Rev 0; no new game addresses are introduced by this work.
- Dolphin's upstream `GDBStub.cpp` schedules `UpdateCallback` at `GDB_UPDATE_CYCLES = 100000`. The callback invokes `ProcessCommands(false)` and reschedules while the GDB socket is active. `ProcessCommands` polls socket readiness when no command is available. GDB remains active while its client socket remains connected. This suggests recurring core-timing work even when the Rust sampler is stopped; it does not quantify the actual slowdown on Dolphin 2609a.
- Dolphin's GDB initialization waits in `accept` before scheduling its update callback. Existing RMGP01 runtime observation showed GDB-enabled Dolphin stayed paused/black until the Rust client attached and sent continue. Therefore the unattached condition is a pause/startup control, not comparable running gameplay.
- Upstream Dolphin's `MemoryWatcher` is a compile-time optional `USE_MEMORYWATCHER` facility called from `OnFrameEnd` under `CPUThreadGuard`. It reads configured values using `PowerPC::MMU::HostRead` and sends changed values over an AF_UNIX datagram socket. It is a debug watcher, not a stable Windows gameplay API; its current header uses POSIX Unix-domain socket headers.
- Dolphin exposes internal, guarded `HostTryRead`/`HostTryWrite` routines for debugger/cheat use. These are source integration points, not an external public plugin API.
- The upstream source is GPL-2.0-or-later. Any Dolphin fork changes must preserve that license and attribution; WTFPL applies only to original MultiGalaxy1 code.

## Functional requirements

- Keep a reproducible four-condition benchmark using identical PAL RMGP01 scene, controller, graphics, save, AllStars patch, and isolated profile settings.
- Record Dolphin process CPU utilization and observed OSD Speed, FPS, and VPS per timed run.
- Keep OSD metrics enabled only in isolated development profiles; production settings remain user-controlled.
- Treat unattached GDB as paused/non-gameplay in 2609a and record this limitation instead of fabricating FPS.
- Do not use GDB for production multiplayer transport.
- No guest-memory writes, guessed host pointers, or guessed game offsets in the prototype.

## Technical design

The Rust `bench-dolphin` command samples the verified Dolphin PID's process CPU time at one-second intervals, then records the operator-read Speed/FPS/VPS values in a CSV row. Dolphin's development-only GFX OSD displays those metrics. The four conditions are run sequentially by the operator from the interactive Windows desktop so identical gameplay and the user's actual graphics load can be held constant. A separate `SharedSnapshot` host prototype defines a versioned atomic seqlock payload and Windows named mapping; the `bridge-prototype` commands exercise this synthetic IPC without reading or writing emulated memory. It establishes the transport ABI only, not Dolphin integration or an end-to-end speedup.

The preferred production prototype is a minimal GPL-compatible Dolphin fork bridge. A 2609a patch now uses `Core::OnFrameEnd` and guarded `HostTryRead` to publish identity, callback heartbeat, and a single read-only MEM1 word. Mapping setup is outside the callback; Rust reads the fixed ABI. The patch has not yet been applied or compiled. Remote-state commands and all emulated writes remain out of scope. Use source-level MMU access, not GDB polling or external-process scanning.

Direct Windows `ReadProcessMemory` remains a diagnostic possibility only. It would require a version-stable, validated way to discover the host mapping of emulated RAM and coherent snapshots while the CPU runs. Dolphin's relocatable host pointer is not derivable from a guest address alone, and external reads race game updates. Do not scan or write memory until that mapping and consistency contract are demonstrated.

## Dependencies

- Rust standard library for the benchmark harness.
- Windows `GetProcessTimes`/process query rights for host CPU sampling.
- Dolphin 2609a with the local isolated development profile for runtime measurements.
- For the eventual bridge, a Dolphin source checkout and its GPL-2.0-or-later build dependencies.

## Risks

- The source-observed GDB polling mechanism may differ from Dolphin 2609a's exact revision; runtime benchmarks must confirm the performance impact.
- CPU utilization alone does not show emulation speed. OSD Speed/FPS/VPS must be recorded from the same run.
- OSD and debug overlays can perturb performance slightly; enable them identically in all four conditions.
- The current automation desktop cannot run a useful visible benchmark. Dolphin launch must be performed by the user's interactive PowerShell handoff.
- A shared-memory publication path must not block the emulation thread or expose arbitrary game-memory writes.

## Acceptance criteria

- Rust benchmark command produces CPU samples and a machine-readable summary for each condition.
- Synthetic shared-memory writer/reader demonstrate cross-process snapshot consistency and bounded throughput without guest-memory access.
- The 2609a Dolphin patch builds and emits a live heartbeat and guarded RAM sample through the same Rust ABI.
- Isolated dev profile enables Speed/FPS/VPS OSD in all modes.
- All running conditions have data from the same playable scene; unattached mode is explicitly marked paused if 2609a does not run.
- Bridge-enabled and disabled gameplay runs show no material slowdown at the measured PAL field cadence.
- Any measured GDB regression is documented with per-condition results and causes the production architecture to use source-level IPC instead.
- No Dolphin gameplay or coordinate claim is made without visible runtime evidence.

## Testing strategy

- Unit-test CPU-percent math, condition parsing, and CSV escaping.
- Run Cargo format, workspace tests, and build.
- Runtime-test the harness against an actual Dolphin process from interactive PowerShell.
- Capture OSD Speed/FPS/VPS and Task Manager-equivalent process CPU in 30-second samples; repeat each condition three times after scene warm-up.
