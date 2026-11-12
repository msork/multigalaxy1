# Milestones

| Milestone | Scope | Status |
|---|---|---|
| M0 | Research, reverse engineering, PAL feasibility, architecture | In progress; official DolphinTool and exact Petari map validate PAL RMGP01 Rev 0; broader localization and lifecycle evidence remain open |
| M1 | Reproducible Rust patch tooling and verified PAL maps | Partial; DOL parser/differ, safe patch composition, exact PAL map checks, identity ISO rebuild, and real changed-byte WIT rebuild/re-extract pass offline. User-confirmed changed-byte boot and controllable Mario on 2609a/2609b; AllStars on 2609b and the trampoline remain unverified. Persistent content-checked launch cache measured 192.1 s cold vs 52.4 s warm locally; WIT extraction is skipped only on a matching validated cache. See [image rebuild evidence](references/wii-image-rebuild.md) and [cache specification](../specs/rebuilt-iso-validation-cache.md) |
| M2 | Stock Dolphin bridge feasibility | In progress; user-reported live mailbox and paired bridge-off/on benchmark are recorded; title/menu, pause/resume, and transition lifecycle checks remain pending |
| M3 | Verified PAL Mario coordinate extraction | User reports the mailbox yielded real X/Y/Z at ~60 Hz; source/DOL checks and synthetic snapshot tests are recorded, while this turn did not independently repeat gameplay |
| M4 | Two-player online position relay | Bidirectional position relay passed in a user-confirmed two-instance Dolphin 2609b test, PAL RMGP01 Rev 0 (Mario PID 9312, Luigi PID 13420; separate isolated profiles/ACK writers; loopback `127.0.0.1:39845`). Luigi→Mario changing XYZ was generally ~58 Hz, 0–16 ms reported age. This confirms position relay only; gameplay sync, rendering, pause/transition recovery, disconnect cleanup, and performance regression remain unverified. See [M4 checkpoint](references/m4-two-client-2609b.md). |
| M5 | Remote-player rendering on stock Dolphin | **NO-GO for runtime rendering.** PAL callsite/ABI and read-only actor/model/resource observations exist, but they do not establish a safe second draw. `directDraw` changes J3D/GX/model state; independent matrix/packet lifetime, complete restoration/cache protocol, and safe scene teardown are unresolved. A previous opt-in Stage 1 dry-run reported a corrupted hook word; no clean pass-through hook run is established. Follow the staged gates in [M5 rendering implementation plan](../specs/M5-remote-rendering-implementation-plan.md). |
| Launcher | Persistent character selection | Specification added for four-character selection, saved preference, character identity separation, protocol migration, and PAL Mario/Luigi movement defaults. No UI, persistence code, physics patch, or multiplayer protocol change |
| M6 | Independent per-client camera/render integration | Not started; each client keeps its own full-screen camera, remote render actor must not affect it |
| M7 | Wario/Waluigi models and abilities | Not started |
| M8 | Online authority, progression, and synchronization | Not started; M4 relays positions only and does not synchronize shared progression |
| M9 | AllStars Classic Controller and conventional gamepads | Static AllStars + shoulder-swap validation and isolated profile support exist; runtime input validation pending |
| M10 | Campaign, cutscenes, progression, stability | Not started |
| M11 | Performance, packaging, release | Not started; counter/ACK benchmark is recorded, coordinate relay performance remains unverified |

No milestone is complete until its acceptance criteria and evidence are recorded. Dolphin NetPlay and local split-screen are permanently out of scope. Official Dolphin binaries and the user's RVZ remain unmodified.
