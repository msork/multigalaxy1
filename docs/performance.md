# Dolphin performance and runtime bridge

## Current finding

Sampler frequency is not the only plausible cost. Dolphin upstream's GDB stub defines a 100,000-emulated-cycle update event. Its callback calls `ProcessCommands(false)` and reschedules while GDB is active; the command loop checks socket readiness even when no command is waiting. At a nominal 729 MHz Wii CPU clock, 100,000 cycles corresponds to about 7,290 callbacks per emulated second. This is a source-level mechanism that can remain active after `STOP`; it is not a measured slowdown figure for Dolphin 2609a.

GDB startup also blocks waiting for its client, and the prior PAL runtime test found that Dolphin was paused until the Rust client attached and sent continue. Therefore `--debug-unattached` is a paused diagnostic control with no comparable gameplay FPS. The meaningful comparison is normal vs attached-idle vs active-sampling.

## Four-condition benchmark

Development profiles enable Dolphin's supported `ShowFPS`, `ShowVPS`, and `ShowSpeed` graphics metrics only in the isolated `Config/GFX.ini`. The benchmark command records one-second process CPU samples and then asks for the average OSD values. CPU is reported as a percentage of total machine capacity, the scale used by Task Manager.

The shared-memory wire prototype can be exercised independently of Dolphin using two PowerShell windows:

```powershell
.\target\debug\multigalaxy.exe bridge-prototype write local-test 30
```

While the writer is running, use another window:

```powershell
.\target\debug\multigalaxy.exe bridge-prototype read local-test 20
```

This tests the versioned mapping, nonblocking snapshot read, and sequence validation across process views. Its payload is synthetic; it contains no Mario coordinates and is not Dolphin performance data. The platform-specific benchmark and snapshot prototype do not justify a fast-gameplay claim until they are connected to the user's Dolphin source fork and compared on the same scene.

### Synthetic Windows IPC check (2026-10-30)

Validated with two separate `multigalaxy.exe` processes: the writer published 1,222 frames in 20 seconds (61.10 Hz); while it was active, the reader completed 2,625 snapshot reads in 4 seconds (about 656 reads/s) and observed 245 new frames. This verifies cross-process mapping visibility and snapshot consistency for synthetic bytes only. It does **not** measure Dolphin overhead, game speed, or GDB-versus-IPC performance.

Use the launcher PID printed by `dev-launch`; do not select an arbitrary Dolphin process. Repeat the same scene and input sequence for 30 seconds per condition after warm-up:

1. **Normal, no GDB:** run `dev-launch <local-config> <prepared-profile> 55221`; enter the same playable scene, then in a second PowerShell run `multigalaxy.exe bench-dolphin <PID> 30 normal`.
2. **GDB enabled, unattached:** run `dev-launch <local-config> <prepared-profile> 55221 --debug-unattached`. Dolphin remains paused waiting for a client on 2609a; record its CPU and zero/no-game OSD, but do not treat it as a gameplay comparison.
3. **GDB attached, idle:** run `dev-launch <local-config> <prepared-profile> 55221 --debug`, type `STOP` in its sampler terminal, then run `bench-dolphin <PID> 30 gdb-idle` in another PowerShell.
4. **Active sampling:** on that same debug run type `START` and collect `bench-dolphin <PID> 30 gdb-sampling`.

At the end of each command, enter the average `SpeedPercent FPS VPS` shown by Dolphin's OSD, separated by spaces. The command prints a CSV row suitable for saving. Keep the same graphics backend, controller, AllStars, isolated save, level, and gameplay sequence for each running condition. Repeat each condition three times and compare medians. No runtime measurements are recorded yet because this automation desktop is not the user's interactive `Default` desktop; run the benchmark from the user-side PowerShell handoff documented in [building.md](building.md).

### Bridge on/off comparison

Use the same patched Dolphin build and isolated profile, with GDB disabled for both runs. Start `dev-launch <local-config> <prepared-profile> 55221` for the enabled case and record its PID with `bench-dolphin <PID> 30 bridge-enabled`. Stop Dolphin, then start the same command with `--bridge-disabled` and record `bench-dolphin <PID> 30 bridge-disabled`. AllStars remains enabled in both runs. Keep the same scene and inputs; repeat three times and compare median Speed/FPS/VPS and CPU. This comparison has not yet been performed.

## Runtime bridge research

Dolphin 2609a's [`Core.cpp`](https://github.com/dolphin-emu/dolphin/blob/2609a/Source/Core/Core/Core.cpp) calls its optional `MemoryWatcher` at `Core::OnFrameEnd` on the CPU thread. The watcher uses `CPUThreadGuard` and a POSIX datagram socket; that transport is not suitable as-is for Windows. The MultiGalaxy 2609a patch targets this callback and publishes through a named mapping instead.

Dolphin 2609a's [`MMU.h`](https://github.com/dolphin-emu/dolphin/blob/2609a/Source/Core/Core/PowerPC/MMU.h) provides guarded `HostTryRead` and `HostTryWrite` routines. This milestone uses `HostTryRead` only. The Rust fixed-layout snapshot and Windows mapping are mirrored by a minimal C++ publisher in [`tools/dolphin-bridge`](../tools/dolphin-bridge/README.md). Rust consumes the mapping read-only. The C++ patch has not yet been applied or compiled, and the callback has not been measured. No command queue or game writes are implemented.

External `ReadProcessMemory` is not viable as the primary bridge: the Dolphin host pointer backing guest RAM is process-local and relocatable, external snapshots can race the emulation thread, and no validated host-map discovery method is established for 2609a. Do not scan for or write to guessed pointers. The source fork avoids this race by reading under the emulation callback.

The source patch is based on official tag 2609a and its license/source obligations are recorded in the patch README. Dolphin remains GPL-2.0-or-later; standalone original MultiGalaxy1 files remain WTFPL v2. Actual source compilation, live heartbeat publication, and full-speed comparison remain pending an exact Dolphin source checkout and interactive gameplay session.
