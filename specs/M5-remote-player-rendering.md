# M5 Phase 1: Remote-player rendering specification

Status: research plus opt-in read-only actor-class and scheduler-metadata observers. No game-memory writes, executable patches, hooks, actor creation, or runtime rendering changes are authorized by this phase.

M5 Phase 1.10 update: user-reported transition values (PID 86776) show Beach Bowl group `0x80FA07D8` (1164 entries, 16 Penguins) and destination group `0x80FA074C` (166 entries, zero Penguins); scene `0x80F8EF70` and Scene Napa/current-heap `0x80F8EEF0` stayed numerically unchanged. This does not prove heap destruction, scene-instance continuity, or constructor-time heap identity. The new opt-in `observe-scheduler` is read-only and validates the exact PAL execute holder's vptr, `+0x0C` array pointer, `+0x10` capacity, `+0x14` count, bounded 4096*12-byte MEM1 span, and five pending-operation bytes. It recomputes the scene chain and double-samples metadata; it never reads records or callback lists. Synthetic tests pass; live scheduler metadata remains unverified until user execution. PAL `registerActor` has no capacity check, so observer-reported headroom does not authorize registration. Model/archive residency, transitive resource ownership, callback list capacity, and teardown ordering remain blockers. See [Penguin live investigation](../docs/references/penguin-live-investigation.md), [Penguin actor/render path](../docs/references/penguin-rendering-path.md), and [PAL placement heap/lifetime trace](../docs/references/pal-placement-heap-lifetime.md).

## 1. Objective

Define a safe path to display one remote participant in the local full-screen SMG1 scene, driven by the existing M4 Rust position relay. A remote visual must be a separate scene object: it must not replace Mario, register as the local player, own the camera, read local controls, or participate in gameplay collisions/progression.

Initial target: PAL RMGP01 Rev 0, official unmodified Dolphin 2609a, existing mandatory PAL AllStars configuration, and the isolated development profile. M4 networking and the current Gecko counter/ACK mailbox remain unchanged.

## 2. Verified research

The local `target/petari-rmgp01-symbols.txt` is for Petari's PAL map. Its associated build SHA-1 is `9BD1DAA520BD1F573B6A4B82E03336E9C51B511F`, matching the verified PAL DOL described in `docs/research.md`. Petari source defines `LiveActor : NameObj`, with virtual `init`, `movement`, `calcAnim`, `calcViewAndEntry`, `appear`, `kill`, `makeActorAppeared`, and `makeActorDead`; its documented fields include position `+0x0C`, rotation `+0x18`, velocity `+0x30`, gravity `+0x3C`, model manager `+0x48`, animation keeper `+0x4C`, and spine `+0x50` relative to the LiveActor subobject. The matching Mario pointer chain is already statically validated by `player_probe.rs`; it does not establish the remote actor lifecycle.

Petari source for `MarioHolder` initializes its actor pointer to null and exposes `setMarioActor`/`getMarioActor`; `MR::getMarioHolder()` resolves it through the scene object registry. `MarioActor` constructs a separate `Mario` movement object, and its source has substantial player-specific movement, animation, sensors, camera/control and state behavior. This is evidence against cloning/constructing another `MarioActor` as a cosmetic proxy.

PAL symbol-map candidates (addresses are DOL virtual addresses, not RAM allocation addresses):

| Symbol | PAL Rev 0 address | Use in research |
|---|---:|---|
| `__ct__9LiveActorFPCc` | `0x80165204` | Base actor constructor symbol |
| `initModelManagerWithAnm__9LiveActorFPCcPCcb` | `0x801659C4` | Model/animation setup candidate |
| `__ct__12ModelManagerFv` | `0x80168F5C` | Model manager constructor candidate |
| `init__12ModelManagerFPCcPCcb` | `0x80169AD0` | Resource-backed model init candidate |
| `connectToScene__18NameObjExecuteInfoFv` | `0x80261F48` | Scheduler connect path |
| `disconnectToScene__18NameObjExecuteInfoFv` | `0x80261FC4` | Scheduler disconnect path |
| `connectToScene__20NameObjExecuteHolderFP7NameObj` | `0x802621F8` | Object scheduler registration candidate |
| `disconnectToScene__20NameObjExecuteHolderFP7NameObj` | `0x80262280` | Object scheduler removal candidate |
| `registerDrawBuffer__19NameObjListExecutorFP9LiveActori` | `0x8026C004` | Draw buffer registration candidate |
| `init__10MarioActorFRC12JMapInfoIter` | `0x802AEC60` | Local actor initialization reference |
| `movement__10MarioActorFv` | `0x802AFD9C` | Local actor update reference |
| `calcAnim__10MarioActorFv` | `0x802B2A44` | Local actor animation reference |
| `draw__10MarioActorCFv` | `0x802B6D18` | Local actor draw reference |
| `connectToScene__2MRFP9LiveActoriiii` | `0x803EEF08` | Scene category connection candidate |
| `connectToSceneNpc__2MRFP9LiveActor` | `0x803EF020` | NPC category connection candidate |

These are names/addresses from the exact-hash-associated symbol map. They are **not** instruction-signature-verified hook sites, calling-convention validation, or runtime-safe APIs. Before use, bind each call site/function to PAL DOL bytes and confirm the function prototype and all side effects from Petari implementation/disassembly.

Petari's `NameObj` has lifecycle methods `init`, `initAfterPlacement`, `movement`, `draw`, `calcAnim`, and `calcViewAndEntry`. `NameObjExecuteHolder` and `NameObjListExecutor` expose separate connect/disconnect and draw-buffer scheduling paths. This supports a design based on a separately owned scene object registered into the normal scheduler, rather than injecting drawing into Mario's actor. The exact safe scheduler category and registration timing still need PAL call-chain and runtime confirmation.

## 3. Functional requirements

- Show one remote avatar without changing local Mario's holder, input, camera target, movement, health, collision, or progression.
- Keep the remote actor cosmetic and non-interactive in the first implementation: no hit sensors, binder, damage, pickup, switch, star, gravity, or collision effects.
- Consume only validated remote state from M4. Invalid, stale, wrong-session, wrong-scene, or absent state hides the actor.
- Preserve local camera and the participant's localized game assets. Do not ship Nintendo assets or custom character assets in this phase.
- Keep actor creation/destruction scoped to the active scene; repeated transitions must not leak scheduler entries or model resources.

## 4. Technical design

### Host-side validated mailbox

M4 TCP relay remains authoritative for remote presence and positions. The Rust client validates message framing, player ID, monotonic sequence, finite coordinates, freshness, and scene identity before replacing a fixed-size latest-value slot. The game-facing bridge consumes a versioned snapshot. The intended game-facing record must be bounded and self-describing:

`magic[4] | ABI u16 | size u16 | generation u32 | scene_epoch u32 | player_id u8 | flags u8 | reserved[2] | sequence u32 | source_tick u64 | xyz f32[3] | rotation f32[3] | gravity/up f32[3] | state u32 | checksum u32`

This is a proposed layout, not an implemented guest-memory structure. All fields are big-endian if read by PPC code; Rust encoding/decoding must use explicit endian conversion, fixed maximum size, finite/range checks, generation consistency, checksum, and a double-buffer or sequence-lock pattern so the guest cannot observe a torn snapshot. Producer updates only its own slot; no unbounded queue. A reader must reject unknown ABI/version/size, repeated generation, invalid player ID, bad checksum, NaN/Inf, unreasonable world values, stale timestamps, or mismatched scene epoch.

The current phase does not allocate this guest mailbox or write it. Reuse of the existing ACK word is prohibited: ACK memory is a distinct protocol field and does not imply that nearby bytes are safe for remote-state payload.

### Actor ownership and scheduling

Preferred eventual design: a small custom `RemotePlayerActor` derived from `LiveActor` (or a verified minimal drawable `NameObj` implementation if it can use the model/draw path safely). It is allocated through the verified game allocator and owned by a scene-lifetime holder; initialized with a known existing model resource; registered in one verified update/calcAnim/draw category; and disconnected before destruction. It never enters `MarioHolder`, player camera targets, controller systems, or gameplay actor registries. It has no gameplay sensors/collision/binder. Hide/show uses lifecycle methods only after their side effects are verified.

Avoid invoking the full `MarioActor` constructor: Petari source shows it constructs `Mario` and initializes player state. Reusing a local Mario actor pointer, changing its transform, or redirecting MarioHolder would violate isolation.

### Placeholder model

First visual test should use an existing model/archive already loaded in the active scene and supported by a normal actor constructor. This avoids packaging assets and tests registration/rendering separately from character art. Do not nominate a specific actor/model until its PAL archive availability, draw scheduling, materials, animation requirements, and license/provenance are verified. Do not duplicate the local Mario's model manager by copying raw pointers; ownership and GPU/resource lifetime are unknown. A static existing NPC/map decoration model is a candidate, not yet verified.

### Transform, gravity and interpolation

The current M4 position relay carries X/Y/Z only. That is insufficient to orient a visible actor reliably on spherical/rotating-gravity surfaces. Before rendering, the protocol must add a scene/coordinate-space epoch, yaw or full orientation, and a gravity/up vector or an explicit representation tied to the local gravity frame. Never infer orientation from successive position samples alone.

Use bounded client-side interpolation between recent monotonic snapshots (initial target buffer 2–3 game frames, configurable after latency measurements). Cap extrapolation to a short fixed interval; then freeze/hide rather than extrapolating indefinitely. Do not interpolate across scene epochs, respawns/teleports, invalid samples, or large discontinuities. Coordinate units and valid-world bounds need runtime measurement and specification before implementation.

### Animation

Initial test may use idle or a fixed neutral pose only if the existing model permits it. Later animation state should be a compact enumerated visual state (idle, locomotion, jump/fall, spin, hurt/death) with validated transitions, never a remotely supplied arbitrary animation/resource name. Animation availability and API are not yet mapped for a non-Mario proxy.

## 5. Dependencies

- Exact PAL RMGP01 Rev 0 DOL and Petari symbol map with their hash provenance.
- Existing M4 Rust server/client and validated position relay.
- Existing stock Dolphin Gecko counter/ACK mechanism and isolated profile; no Dolphin binary changes.
- A verified reversible PPC hook/injection mechanism in the existing Gecko patch path, plus exact non-overlapping hook and mailbox ranges. These are currently unresolved.
- Verified PAL allocator, scene ownership, model resource, scheduler, update, animation and destruction APIs.

## 6. Risks

- Symbol names may be correct while inferred prototypes, instruction signatures, or call semantics are wrong.
- `connectToScene` can add execution/draw work at the wrong phase or twice; bad lifetime ordering can leave dangling scheduler entries.
- Existing Mario model resources may have actor-specific animation/state dependencies and cannot safely be cloned.
- Scene transitions and cutscenes can change scene objects, coordinate origin, gravity, pause state, or archive lifetime.
- PAL AllStars already modifies executable code; hook overlap must be checked against its exact patch set and the original PAL DOL before any implementation.
- Guest PPC and Rust host have different endianness, alignment, and timing. A torn/stale mailbox could render NaN/teleporting artifacts or crash the game.
- No online or in-game remote rendering acceptance has been met by static analysis or M4 network tests.

## 7. Acceptance criteria

Phase 1 is accepted when the evidence tiers and candidate APIs are documented, the mailbox contract and failure policy are specified, and no code/game files are changed.

Future runtime proof requires all of the following on PAL Rev 0, stock Dolphin 2609a, isolated profile, mandatory AllStars enabled:

1. Local Mario retains control, camera, animation, health and position behavior.
2. A separately allocated, noninteractive placeholder appears in a playable scene and tracks a synthetic validated remote snapshot.
3. Local Mario never moves when the remote snapshot changes; remote actor never consumes local controller input or moves the camera.
4. Invalid version/checksum/NaN/player/scene snapshots are rejected; stale or disconnected remote actor hides within the configured timeout.
5. Title screen, pause/resume, respawn, scene transition, repeated load/unload, and mailbox loss do not crash or leak visible stale actors.
6. Two Dolphin clients using M4 show each other's avatars driven by actual relayed positions; report runtime logs and frame/speed impact.
7. The test is reversible: disable the Gecko payload or restore the isolated profile's previous patch selection; original RVZ and regular Dolphin profile remain untouched.

## 8. Testing strategy

Phase 1: validate symbol-map names/addresses against the local pinned PAL map and source links; review all proposed state rejection cases; `git diff --check`. No game runtime changes.

Phase 1.5/1.7/1.8: `observe-penguins` is opt-in and uses stock Dolphin's read-only MEM1 mapping after PID, executable, session, mapping, RMGP01, DOL, symbols, and mandatory AllStars checks. It recomputes and validates the root/group chain, scans only `[0,count)`, matches exact constructor-verified vptr `0x8059C884`, reads only the three-float `mPosition` at `+0x0C`, then rechecks candidate identity, chain, and entries. Synthetic tests pass. The user reports one candidate's position changed while the scene/group remained stable; this is user-observed live evidence, not independently captured by the CLI. No remote actor rendering is verified.

Research-only actor path: `docs/references/penguin-rendering-path.md` and `docs/references/pal-placement-heap-lifetime.md` record exact-hash PAL evidence and Petari semantics for placement, heap selection, execute-info connection/disconnection, resource-holder ownership, and teardown. User-reported group/heap transition samples are recorded without inferring heap destruction or constructor-time identity. `observe-heaps` validates heap globals; the new `observe-scheduler` validates bounded execute-holder metadata without reading records. Its synthetic tests and static PAL DOL/symbol checks pass, but no live scheduler observation has yet been performed for this update. Callback list bounds, actor registration safety, and complete resource/teardown ownership remain unverified. No actor was spawned.

Read-only scheduler validation workflow (manual live check; do not launch a Dolphin instance from the sandbox):

```powershell
Set-Location C:\Users\maxim\Projects\multigalaxy1
$DolphinPid = 12345 # set the PID of the already-running isolated Dolphin.exe
cargo run -p multigalaxy -- observe-scheduler .\development.local.toml $DolphinPid
```

Run once in a playable PAL scene and again after a settled mission transition. Expected output includes `capacity=4096`, the live `count`, `remaining`, holder/array pointers, the five pending-operation flags, and `callback_lists=not_read`. A null slot is expected if the execute holder has not been lazily created in that scene. Results are live observations only after the command succeeds; static validation and synthetic tests are not live proof. Do not interpret remaining slots as authorization to call `registerActor`.

Before actor implementation: produce a PAL instruction-signature manifest and resolve prototypes for constructor, model initialization, scheduler connect/disconnect, scene creation/destruction, and draw submission. Run `observe-heaps` before and after a normal mission transition with the same Dolphin PID; changed scene/group/heap addresses support an identity change, while reused addresses are inconclusive. These gameplay-time samples do not prove heap identity at constructor execution. Confirm execute-info setup/capacity, actor-system-info/clipping behavior, a scene-mounted model/archive, complete model-resource ownership, and teardown callback order from matching Petari source/disassembly. See the prerequisites and stop conditions in `docs/references/pal-placement-heap-lifetime.md`.

Runtime progression: first local-only synthetic mailbox state with a placeholder actor; then remote M4 state; then transition/disconnect/fault injection. Capture Dolphin version, DOL fingerprint, patch activation, scene name/epoch, mailbox generations, actor lifecycle log, FPS/VPS and emulation speed. No successful runtime result may be inferred from compilation.

