# Stock Dolphin runtime bridge feasibility

## Decision and current evidence

MultiGalaxy1 uses official, unmodified Dolphin only. No Dolphin fork or custom Dolphin binary is built or distributed. The preferred proof is a small PAL Gecko PPC routine plus a Rust host reader for Dolphin 2609a's existing Windows named memory mapping. This is an **experimental, undocumented Dolphin implementation detail**, not a supported Dolphin API. It is version-gated by mapping name/layout, live `RMGP01` identity, handler size, exact PPC signature, address bounds, and AllStars overlap checks. Do not use this path with other Dolphin versions until revalidated.

Implemented locally:

- The launcher adds a reversible Gecko C0 test routine only to the selected isolated profile's `GameSettings/RMGP01.ini`; it does not alter the RVZ, DOL, Dolphin installation, or regular profile. Mandatory PAL AllStars and both requested shoulder swaps stay enabled.
- The routine increments an inline 32-bit counter on each Gecko OnFrame execution and copies a host-written acknowledgement into a game-observed echo word.
- Rust opens only `dolphin-emu.<PID>`, maps the first 64 KiB, verifies live game ID `RMGP01`, checks the Dolphin 2609a handler size (2880 bytes) and SHA-256 (`2b2ff0716aeb9b806cc61181e559041312957f25f04c435bd7da80a78917b33b`), searches the exact handler range for the unique PPC signature, and restricts writes to the validated acknowledgement word before the first observed AllStars code-cave write at `0x80002790`.
- The Rust probe reports update rate, read/write cost, acknowledgement rate and round-trip latency; `Q` unmaps the view. It enables no GDB server.
- Windows synthetic tests create a separate named mapping and verify counter-read / acknowledgement-write visibility across two mapped views. This proves the Rust mapping mechanics only, **not Dolphin behavior**.

No live Dolphin exchange, 60 Hz cadence, visible gameplay, or performance comparison has yet been verified. The C0 code's runtime execution, Dolphin's synchronization/cache visibility for external shared-memory writes, and Gecko ordering relative to AllStars still require the interactive test below. The prototype fails closed if validation does not match.

## Dolphin 2609a source findings

These findings were checked against the local 2609a source tree; direct source links are provided below.

- `MemArenaWin.cpp` creates a page-file mapping named `dolphin-emu.<current PID>` with `PAGE_READWRITE`.
- `Memmap.cpp` assigns the first shared-memory segment to MEM1 at offset 0, then maps that segment as Dolphin's RAM backing. Guest `0x80000000` maps to MEM1 offset 0 in the normal Wii MEM1 address mapping.
- This permits an external process in the same user/session to open an existing mapping without `ReadProcessMemory`, scanning host pointers, or changing Dolphin. It still bypasses a documented API, can race CPU execution, and may change across releases. The launcher therefore exposes a narrow fixed view, volatile aligned reads, and one acknowledgement write; this is not yet accepted as production transport. The handler SHA-256 is pinned, but Dolphin exposes no stable source-version API here, so the handler fingerprint does not prove that the Dolphin executable is official or unmodified; the local development path is expected to be the user's verified stock 2609a installation.
- Dolphin 2609a GDB processes requests from a periodic CPU-thread callback (100,000 emulated cycles). Its `M` write path uses the guarded memory range but also unconditionally resets PPC instruction cache state and refreshes disassembly UI. GDB remains a development tool and is excluded from this bridge benchmark.
- MemoryWatcher is compiled only for UNIX in this release. Its AF_UNIX datagram implementation sends changed values outward and does not provide Windows bidirectional IPC.
- Gecko C0 executes guest PPC code per frame. It provides the guest half of this test but no host channel by itself.

## Safe interactive proof

Use an interactive PowerShell on the user's normal desktop (`WinSta0\Default`), not an automation desktop. Ensure Dolphin is closed before each launch. Use the same fresh isolated profile and playable scene for both runs; AllStars is enabled in both. Development config remains local and git-ignored.

```powershell
cd C:\Users\maxim\Projects\multigalaxy1
cargo run -p multigalaxy -- dev-launch development.local.toml 'target\mg1-stock-bridge-test' 0
```

Start the game, enter the same playable scene, and leave Mario idle for 30 seconds. Record Dolphin OSD Speed/FPS/VPS and PID. In a second PowerShell, sample CPU for the same interval:

```powershell
cargo run -p multigalaxy -- bench-dolphin <PID> 30 stock-bridge-off
```

Close Dolphin, then launch with the probe:

```powershell
cargo run -p multigalaxy -- dev-launch development.local.toml 'target\mg1-stock-bridge-test' 0 --gecko-bridge-probe
```

Enter the same scene and record the console's `state=live` lines plus matching OSD and CPU measurements:

```powershell
cargo run -p multigalaxy -- bench-dolphin <PID> 30 stock-bridge-on
```

Use `Q` in the launcher console to unmap/stop. A live proof requires a changing counter, matching acknowledgements/echoes, approximately 60 game updates per second, stable gameplay, and paired FPS/VPS/speed/CPU data. Keep logs and report both the synthetic and live evidence separately. If no mailbox appears within 60 seconds, the launcher reports the failure; check the isolated Gecko enable section and whether the game reached its title/menu. Do not disable AllStars or GDB silently to work around failure.

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

## References

- [Dolphin 2609a Windows MemArena](https://github.com/dolphin-emu/dolphin/blob/2609a/Source/Core/Common/MemArenaWin.cpp)
- [Dolphin 2609a memory map](https://github.com/dolphin-emu/dolphin/blob/2609a/Source/Core/Core/HW/Memmap.cpp)
- [Dolphin 2609a GDB stub](https://github.com/dolphin-emu/dolphin/blob/2609a/Source/Core/Core/PowerPC/GDBStub.cpp)
- [Dolphin 2609a Gecko code support](https://github.com/dolphin-emu/dolphin/blob/2609a/Source/Core/Core/GeckoCode.cpp)
- [Dolphin 2609a Gecko patch engine](https://github.com/dolphin-emu/dolphin/blob/2609a/Source/Core/Core/PatchEngine.cpp)
- [Dolphin 2609a MemoryWatcher build condition](https://github.com/dolphin-emu/dolphin/blob/2609a/CMakeLists.txt)
