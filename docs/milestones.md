# Milestones

| Milestone | Scope | Status |
|---|---|---|
| M0 | Research, reverse engineering, PAL feasibility, architecture | In progress; official DolphinTool validates RMGP01 Rev 0; stock bridge remains unproven |
| M1 | Reproducible Rust patch tooling and verified PAL maps | Partial; DOL parser/differ and safe patch composition tests exist; no runtime hook is accepted |
| M2 | Stock Dolphin bridge feasibility | In progress; user-reported paired bridge-off/on playable benchmark is 100%/59.94 FPS/VPS in both runs, CPU 3.289% vs 3.383%; title/menu, pause/resume, and transition lifecycle checks remain pending |
| M3 | Two-client state exchange and visible remote character | Not started; PAL position mailbox is implemented with synthetic tests, but real Dolphin X/Y/Z movement validation is still required before client state exchange |
| M4 | Generalized four-player engine | Not started |
| M5 | Independent per-client camera/render integration | Not started |
| M6 | Wario/Waluigi models and abilities | Not started |
| M7 | Online authority and synchronization | Not started |
| M8 | AllStars Classic Controller and conventional gamepads | Static AllStars + shoulder-swap validation and isolated profile support exist; runtime input validation pending |
| M9 | Campaign, cutscenes, progression, stability | Not started |
| M10 | Performance, packaging, release | Not started; counter/ACK benchmark is recorded, coordinate payload and release performance remain unverified |

No milestone is complete until its acceptance criteria and evidence are recorded. Dolphin NetPlay and local split-screen are permanently out of scope. Official Dolphin binaries and the user's RVZ remain unmodified.
