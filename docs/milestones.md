# Milestones

| Milestone | Scope | Status |
|---|---|---|
| M0 | Research, reverse engineering, PAL feasibility, architecture | In progress; official DolphinTool validates RMGP01 Rev 0; stock bridge remains unproven |
| M1 | Reproducible Rust patch tooling and verified PAL maps | Partial; DOL parser/differ and safe patch composition tests exist; no runtime hook is accepted |
| M2 | Stock Dolphin bridge feasibility | In progress; user-observed live bidirectional counter/ACK at ~59–60.5 Hz on stock Dolphin 2609a; controlled playable bridge-on/off Speed/FPS/VPS/CPU and lifecycle validation pending |
| M3 | Two-client state exchange and visible remote character | Not started; gated on controlled M2 performance result and verified PAL Mario coordinate extraction |
| M4 | Generalized four-player engine | Not started |
| M5 | Independent per-client camera/render integration | Not started |
| M6 | Wario/Waluigi models and abilities | Not started |
| M7 | Online authority and synchronization | Not started |
| M8 | AllStars Classic Controller and conventional gamepads | Static AllStars + shoulder-swap validation and isolated profile support exist; runtime input validation pending |
| M9 | Campaign, cutscenes, progression, stability | Not started |
| M10 | Performance, packaging, release | Not started; no controlled bridge overhead benchmark yet |

No milestone is complete until its acceptance criteria and evidence are recorded. Dolphin NetPlay and local split-screen are permanently out of scope. Official Dolphin binaries and the user's RVZ remain unmodified.
