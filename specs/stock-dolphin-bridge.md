# Specification: Stock Dolphin mailbox proof

## Objective

Prove a safe, reversible, bidirectional guest/host mailbox at frame cadence using official unmodified Dolphin 2609a, PAL RMGP01 Rev 0, mandatory AllStars, and an isolated Dolphin profile.

## Verified research

- Dolphin 2609a Windows source creates `dolphin-emu.<PID>` as a page-file mapping; its MEM1 region occupies shared mapping offset zero.
- Gecko C0 code can execute PPC code on frame updates; it does not itself expose host IPC.
- Dolphin's GDB write path causes instruction-cache/disassembly side effects and startup pause; it is excluded from this prototype.
- Windows 2609a does not build MemoryWatcher.
- Source references and exact limitations are maintained in `docs/stock-dolphin-bridge.md`.

## Evidence status

The user reported visible live counter/ACK exchange on stock Dolphin 2609a with PAL RMGP01 Rev 0 at ~59–60.5 updates/s, ~59–60 ACKs/s, ~16.7 ms mean ACK round trip, and ~2 microseconds per shared read/write. Smooth 100% speed was also user-observed. This verifies interactive bidirectional mailbox behavior by user report. It is not a controlled bridge-on/off gameplay performance benchmark. Reported per-window maxima of 30–47 ms lack raw samples; jitter percentiles are not inferred from them.

## Functional requirements

- The launcher may append one test Gecko routine only to the selected isolated `RMGP01.ini` profile.
- The routine increments a counter and copies a host acknowledgement into a game-readable echo.
- Rust opens the spawned stock Dolphin's named mapping, verifies RMGP01 identity, exact handler bounds/signature, and a unique mailbox before a single aligned acknowledgement write. It rechecks game ID and handler signature before each write; a stalled game suspends writes, and a missing mailbox is re-scanned for up to 60 seconds.
- Reject unexpected handler size, unknown game, duplicate/corrupt code, out-of-view pointers, and overlap with the observed AllStars cave.
- No writes to game images, Dolphin binaries, or regular profile. No GDB for the bridge path.
- Preserve mandatory AllStars code and requested shoulder swaps.

## Technical design

Portable Gecko code generation and signature validation live in Rust. Windows-specific mapping code is isolated in `stock_memory.rs` and maps only the first 64 KiB. Mapping access is an undocumented 2609a implementation detail and must remain version-gated. The PPC routine has no Mario addresses or game-state writes. A polling host loop reports counter cadence and acknowledgement round-trip latency.

## Dependencies

Official Dolphin 2609a on Windows, local PAL Rev 0 game, externally provided verified AllStars patch files, a prepared isolated profile, and a user-visible interactive PowerShell session.

## Risks

The host mapping is unsupported and may change. External writes can race the CPU; volatile access does not prove emulated cache coherence. Gecko execution or handler placement may conflict with AllStars. The code may execute at a cadence other than VI. Polling and Gecko instruction cost may affect gameplay.

## Acceptance criteria

The user-observed counter/ACK exchange at approximately 60 Hz satisfies the live bidirectional handshake portion. Remaining acceptance: controlled playable bridge-on/off FPS/VPS/speed/CPU comparison, title/menu and pause/resume/scene-transition recovery checks, plus per-session jitter summary. No performance acceptance is claimed from anecdotal smoothness.

## Testing strategy

Rust tests cover code generation, memory bounds, signature verification, mapped ACK isolation, latency statistics, and corruption/overlap rejection. User-side paired gameplay runs compare the same scene/profile with probe disabled/enabled. Record user observation separately from controlled measurements and synthetic tests. Full milestone acceptance remains pending.
