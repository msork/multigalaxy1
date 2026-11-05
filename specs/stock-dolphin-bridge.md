# Specification: Stock Dolphin mailbox proof

## Objective

Prove a safe, reversible, bidirectional guest/host mailbox at frame cadence using official unmodified Dolphin 2609a, PAL RMGP01 Rev 0, mandatory AllStars, and an isolated Dolphin profile.

## Verified research

- Dolphin 2609a Windows source creates `dolphin-emu.<PID>` as a page-file mapping; its MEM1 region occupies shared mapping offset zero.
- Gecko C0 code can execute PPC code on frame updates; it does not itself expose host IPC.
- Dolphin's GDB write path causes instruction-cache/disassembly side effects and startup pause; it is excluded from this prototype.
- Windows 2609a does not build MemoryWatcher.
- Source references and exact limitations are maintained in `docs/stock-dolphin-bridge.md`.

## Functional requirements

- The launcher may append one test Gecko routine only to the selected isolated `RMGP01.ini` profile.
- The routine increments a counter and copies a host acknowledgement into a game-readable echo.
- Rust opens the spawned stock Dolphin's named mapping, verifies RMGP01 identity, exact handler bounds/signature, and a unique mailbox before a single aligned acknowledgement write.
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

Interactive Dolphin displays playable PAL SMG1 with AllStars active. The counter changes at approximately 60 Hz, acknowledgements are observed by game code, state is timestamped, and paired FPS/VPS/speed/CPU measurements show no noticeable slowdown. No criterion is satisfied by synthetic tests alone.

## Testing strategy

Rust synthetic tests cover code generation, memory bounds, signature verification, mapping round-trip, and corruption/overlap rejection. User-side paired run compares same scene/profile with probe disabled/enabled. Record live results separately from tests and static source findings. The proof has not yet passed its interactive acceptance criteria.
