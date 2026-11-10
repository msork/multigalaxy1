# Stock Dolphin runtime bridge feasibility

## Decision and current evidence

MultiGalaxy1 uses official, unmodified Dolphin only. No Dolphin fork or custom Dolphin binary is built or distributed. The mailbox is a small PAL Gecko PPC routine plus a Rust host reader for Dolphin's Windows named memory mapping. This is an **experimental, undocumented Dolphin implementation detail**, not a supported Dolphin API. The general launcher has no release or executable-hash lock. Mailbox use requires the known handler capability, live mapping extent, `RMGP01` identity, exact PPC signature, address bounds, and AllStars overlap checks. Static source and live mailbox evidence are from 2609a; other builds are not claimed compatible and must fail closed when a capability check differs.

Implemented locally:

- The launcher adds a reversible Gecko C0 test routine only to the selected isolated profile's `GameSettings/RMGP01.ini`; it does not alter the RVZ, DOL, Dolphin installation, or regular profile. Mandatory PAL AllStars and both requested shoulder swaps stay enabled.
- The routine increments an inline 32-bit counter on each Gecko OnFrame execution and copies a host-written acknowledgement into a game-observed echo word.
- Rust opens only `dolphin-emu.<PID>`, validates that the mapping spans minimum retail MEM1, maps the first 64 KiB, verifies live game ID `RMGP01`, checks the mailbox's known handler capability (2880 bytes and SHA-256 `2b2ff0716aeb9b806cc61181e559041312957f25f04c435bd7da80a78917b33b`), searches the exact handler range for the unique PPC signature, and restricts writes to the validated acknowledgement word before the first observed AllStars code-cave write at `0x80002790`. This Gecko resource fingerprint is not a Dolphin release or Dolphin.exe identity requirement.
- Game ID reads now tolerate all-zero and partially copied IDs until a bounded deadline (60 seconds by default; `--bridge-startup-timeout-secs=5..300` overrides it). Logs include elapsed time, PID, the 64 KiB mapped-view size, observed ID bytes, and validation status. A complete printable non-PAL ID fails immediately as `wrong_game`; nonprintable unexpected bytes or read failures report `invalid_mapping`; persistent zero/partial IDs report `timeout`. The full Dolphin section size is not queried: only the intentionally bounded view size is reported.
- The Rust probe reports update rate, read/write cost, acknowledgement rate and round-trip latency; `Q` unmaps the view. It enables no GDB server.
- Windows synthetic tests create a separate named mapping and verify counter-read / acknowledgement-write visibility across two mapped views. This proves the Rust mapping mechanics only, **not Dolphin behavior**.

## Verified user-observed live evidence

After commit `e836776`, the user reported a visible interactive run on stock Dolphin 2609a with PAL RMGP01 Rev 0 and the isolated mandatory-AllStars profile. The console showed `state=live`, game update rate about 59–60.5 Hz, acknowledgement rate about 59–60 Hz, mean acknowledgement round trip about 16.7 ms, and shared-memory read and write calls around 2 microseconds each. The user also observed smooth gameplay at 100% emulation speed during this run. Treat these as verified **user-observed live results on that tested build**, not an independently captured trace or controlled performance benchmark; this does not establish a Dolphin version requirement.

The user reported per-window maximum acknowledgement values around 30–47 ms. This indicates occasional multi-frame jitter relative to the ~16.7 ms average; the underlying individual samples were not supplied, so p50/p95/p99 or standard deviation cannot be recovered from those aggregate reports. The probe now prints a session summary on `Q` with count, mean, population standard deviation, p50, p95, p99, and max. No controlled bridge-on/off Speed/FPS/VPS/CPU comparison is complete yet.

The host now rechecks RMGP01 and the complete Gecko handler signature before every acknowledgement write, and periodically rescans the handler to detect mailbox loss. During a paused/stalled game it suspends writes and waits for the counter to resume; if the mailbox disappears it waits up to 60 seconds for a validated mailbox to return. This recovery behavior is covered by static/synthetic regression tests but still needs the interactive pause/resume and scene-transition checks below.

## Dolphin 2609a source findings

These findings were checked against the local 2609a source tree; direct source links are provided below. They are historical source evidence, not a product-wide release allowlist.

- `MemArenaWin.cpp` creates a page-file mapping named `dolphin-emu.<current PID>` with `PAGE_READWRITE`.
- `Memmap.cpp` assigns the first shared-memory segment to MEM1 at offset 0, then maps that segment as Dolphin's RAM backing. Guest `0x80000000` maps to MEM1 offset 0 in the normal Wii MEM1 address mapping.
- `HW::Init` calls `MemoryManager::Init` before CPU/guest execution. The mapping may therefore exist before boot code populates guest RAM. `MEM1_BASE_ADDR` is `0x80000000`; `GetSpanForAddress` clears the top address bits, mapping it to physical offset 0. The same source's boot setup labels `0x80000000` the GameID address. Previously recorded live GDB reads at this address returned `RMGP01` after boot. The current all-zero ID is consistent with a startup race, but alone does not prove a race rather than failed boot or a mapping problem.
- This permits an external process in the same user/session to open an existing mapping without `ReadProcessMemory`, scanning host pointers, or changing Dolphin. It still bypasses a documented API, can race CPU execution, and may change across releases. The launcher therefore exposes a narrow fixed view, volatile aligned reads, and one acknowledgement write; this is not yet accepted as production transport. The handler SHA-256 is pinned, but Dolphin exposes no stable source-version API here, so the handler fingerprint does not prove that the Dolphin executable is official or unmodified; the executable itself is not release-pinned; the requested mapping and game capabilities must pass runtime checks.
- Dolphin 2609a GDB processes requests from a periodic CPU-thread callback (100,000 emulated cycles). Its `M` write path uses the guarded memory range but also unconditionally resets PPC instruction cache state and refreshes disassembly UI. GDB remains a development tool and is excluded from this bridge benchmark.
- MemoryWatcher is compiled only for UNIX in this release. Its AF_UNIX datagram implementation sends changed values outward and does not provide Windows bidirectional IPC.
- Gecko C0 executes guest PPC code per frame. It provides the guest half of this test but no host channel by itself.

## Safe interactive proof

Use an interactive PowerShell on the user's normal desktop (`WinSta0\Default`), not an automation desktop. Ensure Dolphin is closed before each launch. Use the same fresh isolated profile and playable scene for both runs; AllStars is enabled in both. Development config remains local and git-ignored.

```powershell
cd C:\Users\maxim\Projects\multigalaxy1
cargo run -p multigalaxy -- dev-setup development.local.toml 'target\mg1-stock-bridge-test'
```

If Dolphin asks for save import, import using Dolphin's UI, then close setup Dolphin. Use the same isolated profile for both baseline and probe:

```powershell
cargo run -p multigalaxy -- dev-launch development.local.toml 'target\mg1-stock-bridge-test' 0
```

Start the game, enter the same playable scene, and perform the same repeatable movement/jump route for 60 seconds in each condition. Record Dolphin OSD Speed/FPS/VPS and PID. In a second PowerShell, sample CPU for the same interval:

```powershell
cargo run -p multigalaxy -- bench-dolphin <PID> 30 stock-bridge-off
```

Close Dolphin, then launch with the probe:

```powershell
cargo run -p multigalaxy -- dev-launch development.local.toml 'target\mg1-stock-bridge-test' 0 --gecko-bridge-probe
```

Enter the same scene and repeat the same movement/jump route. Record the console's `state=live` lines plus matching OSD and CPU measurements:

```powershell
cargo run -p multigalaxy -- bench-dolphin <PID> 30 stock-bridge-on
```

Use `Q` in the launcher console to unmap/stop and print aggregate jitter statistics. A clean benchmark compares the same scene, route, resolution, controller, and saved state. The baseline and bridge trials must each report OSD Speed/FPS/VPS and 30–60 seconds of process CPU samples. Keep user-observed smoothness separate from this controlled comparison.

For lifecycle checks, run the bridge probe and separately verify (a) title screen/menu counter behavior, (b) enter and leave a playable scene, (c) pause for at least 10 seconds and resume, and (d) transition between two scenes. Expected behavior: `state=stalled` with writes suspended during pause, `state=live_recovered` after resume, and `mailbox_unavailable`/`mailbox_ready` during code-handler loss/reinstallation. Do not claim these have passed until observed interactively. On a wrong game ID, invalid mapping, lost signature before a write, or Dolphin exit, the probe fails closed; on a temporarily absent mailbox it waits for a validated return.

## Interface comparison

| Method | Stock Windows 2609a | Direction | Assessment |
|---|---|---|---|
| Named Dolphin MEM1 mapping + Gecko mailbox | Yes, internal implementation detail | Bidirectional | Preferred proof; undocumented, race/cache semantics and overhead unverified |
| GDB RSP | Yes | Bidirectional protocol | Development-only; startup pause and per-write cache/UI costs make it poor for 60 Hz |
| MemoryWatcher | Not compiled in Windows build | Dolphin to client | Unix-only changed-value datagrams; not bidirectional |
| `ReadProcessMemory` / `WriteProcessMemory` | Windows OS APIs | Potentially bidirectional | No separate benefit over mapping, easy to race; not selected |
| Gecko/DOL hook alone | Yes | Guest only | Can execute code, cannot reach host IPC by itself |
| Dolphin fork/source patch | Explicitly prohibited | Bidirectional | Retired; not an option under current project decision |

If direct shared mapping fails validation, is incoherent during live emulation, or imposes measurable overhead, stop and report. Remaining stock options are a Gecko guest routine that uses a game/IOS-supported network channel (requires separate feasibility research) or accepting that official Dolphin provides no adequate general-purpose bridge. Do not revive the fork without a new explicit project decision.

## PAL coordinate mailbox extension (static/synthetic; live test pending)

The current Rust generator extends the same C0 mailbox with a versioned position snapshot while preserving counter, ACK, and echo word ordering. The guest routine checks the PAL root `0x806A1228` and follows the reviewed fields: GameSystem `+0x24`, SceneController `+0xAC`, Scene `+0x10`, SceneObjHolder Mario slot `+0x50`, MarioHolder actor `+0x0C`, and MarioActor position `+0x0C`. Before each dereference it checks for null, 4-byte alignment, and that the complete next field lies in Wii MEM1 or MEM2. The routine uses the previously clobbered volatile scratch registers r0/r10/r11/r12 and leaves r3-r9 untouched. It writes only its own Gecko mailbox; the Rust host retains its existing sole write to the ACK word.

Mailbox version 1 contains counter, ACK, echo, `MG1P` marker, version, sequence, valid bit, actor address, and X/Y/Z float bits. The guest marks sequence odd and valid false before reading, publishes coordinates, then makes sequence even. An unavailable pointer path keeps valid false and still completes counter/echo processing. The reader requires the unchanged even sequence, expected version/marker, matching frame counter on both sides of the copy, aligned in-range actor, and finite bounded coordinates. It never repeats old coordinates as live. The generated block is checked against the first observed AllStars cave address `0x80002790` and the exact handler fingerprint before ACK writes.

Rust tests currently validate generated code words and labels, supported chain loads, even Gecko pair count, AllStars cave bounds, exact signature discovery, snapshot consistency, and invalid/unavailable state handling. This is only static and synthetic evidence. The expanded PPC block has **not** been run in Dolphin, so real pointer safety, frame cadence, visible gameplay, movement/jump changes, pause recovery, and added performance cost remain unverified. Follow `docs/testing.md` for the user-side test.

## Updated paired performance evidence

The user later supplied controlled playable OFF/ON measurements for the original counter/ACK payload: both 100% speed and 59.94 FPS/VPS; CPU was 3.289% off and 3.383% on. There were 6,667 ACK samples with mean 16.626 ms, p95 30.422 ms, p99 31.237 ms, and max 141.461 ms. These are user-reported measured results; the 0.094 percentage-point CPU change is derived from the two values. The maximum is about 8.5 nominal 60 Hz intervals, but aggregates do not identify its cause. The launcher now logs timestamped ACKs over 50 ms with counter and heartbeat age to gather diagnostic evidence on a future occurrence. Do not optimize or attribute the outlier before collecting such a trace. This benchmark does not cover the coordinate payload above.

## References

- [Dolphin 2609a Windows MemArena](https://github.com/dolphin-emu/dolphin/blob/2609a/Source/Core/Common/MemArenaWin.cpp)
- [Dolphin 2609a memory map](https://github.com/dolphin-emu/dolphin/blob/2609a/Source/Core/Core/HW/Memmap.cpp)
- [Dolphin 2609a hardware initialization](https://github.com/dolphin-emu/dolphin/blob/2609a/Source/Core/Core/HW/HW.cpp)
- [Dolphin 2609a boot setup](https://github.com/dolphin-emu/dolphin/blob/2609a/Source/Core/Core/Boot/Boot_BS2Emu.cpp)
- [Dolphin 2609a GDB stub](https://github.com/dolphin-emu/dolphin/blob/2609a/Source/Core/Core/PowerPC/GDBStub.cpp)
- [Dolphin 2609a Gecko code support](https://github.com/dolphin-emu/dolphin/blob/2609a/Source/Core/Core/GeckoCode.cpp)
- [Dolphin 2609a Gecko patch engine](https://github.com/dolphin-emu/dolphin/blob/2609a/Source/Core/Core/PatchEngine.cpp)
- [Dolphin 2609a MemoryWatcher build condition](https://github.com/dolphin-emu/dolphin/blob/2609a/CMakeLists.txt)
