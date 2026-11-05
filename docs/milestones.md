# Milestones

| Milestone | Scope | Status |
|---|---|---|
| M0 | Research, reverse engineering, PAL feasibility, architecture | In progress; official DolphinTool validates RMGP01 Rev 0; stock bridge remains unproven |
| M1 | Reproducible Rust patch tooling and verified PAL maps | Partial; DOL parser/differ and safe patch composition tests exist; no runtime hook is accepted |
| M2 | Stock Dolphin bridge feasibility | In progress; fork/shared-memory implementation retired; GDB/MemoryWatcher/Gecko/external-memory tradeoffs documented; interactive performance test pending |
| M3 | Two-client state exchange and visible remote character | Not started; blocked on M2 and verified game-side hooks |
| M4 | Generalized four-player engine | Not started |
| M5 | Independent per-client camera/render integration | Not started |
| M6 | Wario/Waluigi models and abilities | Not started |
| M7 | Online authority and synchronization | Not started |
| M8 | AllStars Classic Controller and conventional gamepads | Static AllStars + shoulder-swap validation and isolated profile support exist; runtime input validation pending |
| M9 | Campaign, cutscenes, progression, stability | Not started |
| M10 | Performance, packaging, release | Not started |

No milestone is complete until its acceptance criteria and evidence are recorded. Dolphin NetPlay and local split-screen are permanently out of scope. Official Dolphin binaries and the user's RVZ remain unmodified.
