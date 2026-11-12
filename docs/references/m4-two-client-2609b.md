# M4 live two-client checkpoint — Dolphin 2609b

Date recorded: 2026-11-11. Evidence source: user-confirmed interactive run; raw console logs were not supplied in this report.

## Configuration and result

- PAL RMGP01 Rev 0 on official Dolphin 2609b.
- Mario Dolphin PID `9312`; Luigi Dolphin PID `13420`.
- Separate isolated Dolphin profiles and separate Gecko ACK-writer processes.
- Both read-only Rust sync clients connected to `127.0.0.1:39845`.
- Mario→Luigi and Luigi→Mario position reception were verified.
- Luigi movement produced changing X/Y/Z in Mario's client, generally about 58 updates/s; reported received-snapshot ages were 0–16 ms.

## Evidence boundary

This is a **user-confirmed live M4 position-relay pass** across two Dolphin instances. It establishes that the current clients can read their respective validated PAL mailboxes and relay position snapshots in both directions in the reported setup.

It does not establish:

- Remote-character rendering or actor creation.
- Game-side character identity detection. Mario/Luigi are client protocol identities, not a read of the visible actor's character type.
- Gameplay synchronization: collision, input, abilities, health, death/respawn, interactions, stars, progression, or deterministic state.
- Pause/resume, scene-transition recovery, disconnect cleanup, or behavior outside the reported movement exchange.
- A controlled performance result, stable 60 Hz, or broader Dolphin-build compatibility.
- Runtime activation of AllStars on 2609b.

No changes to M4 code or memory protocol are implied by this checkpoint. The existing repeat procedure is in [networking.md](../networking.md#two-instance-interactive-workflow). The next M4 follow-up is a separate user-run resilience pass for pause/scene transition and disconnect cleanup, with per-client preflight output retained.
