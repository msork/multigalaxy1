# Milestones

| Milestone | Scope | Status |
|---|---|---|
| M0 | Research, reverse engineering, PAL feasibility, architecture | In progress; official DolphinTool and exact Petari map validate PAL RMGP01 Rev 0; broader localization and lifecycle evidence remain open |
| M1 | Reproducible Rust patch tooling and verified PAL maps | Partial; DOL parser/differ, safe patch composition, and exact PAL map checks pass; no gameplay actor injection is accepted |
| M2 | Stock Dolphin bridge feasibility | In progress; user-reported live mailbox and paired bridge-off/on benchmark are recorded; title/menu, pause/resume, and transition lifecycle checks remain pending |
| M3 | Verified PAL Mario coordinate extraction | User reports the mailbox yielded real X/Y/Z at ~60 Hz; source/DOL checks and synthetic snapshot tests are recorded, while this turn did not independently repeat gameplay |
| M4 | Two-player online position relay | In progress; Rust localhost binary server/client and synthetic two-client reconnect/removal tests pass; separate live Dolphin clients remain unverified |
| M5 | Remote-player rendering on stock Dolphin | Phase 1 research/spec complete; Phase 1.5 selected a Penguin observation target and confirmed group/scheduler leads, but is blocked before live pointer inspection. Phase 1.6 adds a bounded read-only MEM1 inspection command with source-backed mapping rules and synthetic tests; no live arbitrary-range read or actor inspection has been verified, and no hook or actor runtime is implemented |
| M6 | Independent per-client camera/render integration | Not started; each client keeps its own full-screen camera, remote render actor must not affect it |
| M7 | Wario/Waluigi models and abilities | Not started |
| M8 | Online authority, progression, and synchronization | Not started; M4 relays positions only and does not synchronize shared progression |
| M9 | AllStars Classic Controller and conventional gamepads | Static AllStars + shoulder-swap validation and isolated profile support exist; runtime input validation pending |
| M10 | Campaign, cutscenes, progression, stability | Not started |
| M11 | Performance, packaging, release | Not started; counter/ACK benchmark is recorded, coordinate relay performance remains unverified |

No milestone is complete until its acceptance criteria and evidence are recorded. Dolphin NetPlay and local split-screen are permanently out of scope. Official Dolphin binaries and the user's RVZ remain unmodified.
