# M5 Phase 1: Remote-player rendering specification

Status: research plus opt-in read-only actor-class and scheduler-metadata observers and a synthetic DOL callsite audit. No game-memory writes, executable patches, hooks, actor creation, or runtime rendering changes are authorized by this phase.

M5 Phase 1.25: user-validated read-only model observations from Dolphin PID `119844` across Beach Bowl → Comet Observatory show changed actor-group/Mario/model-instance pointers, but unchanged `J3DModelData` `0x80D6874C`, embedded `J3DJointTree` `0x80D6875C`, Scene Napa heap `0x80F8EEF0`, and current heap matching Napa. This confirms the corrected first-word interpretation in a live sample and records pointer-value continuity only. Static PAL traces show `ModelManager` resolves ModelData through a ResourceHolder `ResTable`, `newJ3DModel` clears per-joint matrix-procedure/callback fields in the shared data, and scene teardown removes heap-matched holders before heap destruction. Whether the holder persisted or reloaded at reused addresses is unknown. Shared-data rendering remains **NO-GO**; the next bounded experiment is a statically derived, read-only ResourceHolder/ResTable identity observation before and after a scene transition. See [Mario model observer research](../docs/references/mario-model-observer.md).

M5 Phase 1.24: corrected the read-only Mario model observer after a live attempt rejected `0x80D6875C` as a `J3DMtxBuffer` vptr at object `0x810B997C`. PAL DOL evidence establishes that buffer `+0` is `mJointTree` (`J3DModelData+0x10`), while its vtable pointer is stored at `+0x38`. The observer now validates the embedded `J3DJointTree` vptr and pointer identity separately, and validates `J3DMtxBuffer` vptr at `+0x38`. Synthetic regression tests cover valid layout and wrong tree/vptr cases. This corrects a statically proven observer bug; no live rerun has yet correlated the reported value with the same sample's model-data pointer. Rendering remains **NO-GO**.

M5 Phase 1.23: [read-only Mario model observer](../docs/references/mario-model-observer.md) traces MarioActor `+0x48` → ModelManager's PAL Xanime/non-Xanime model selection → dynamic J3DModelX → model data `+0x04` and matrix buffer `+0x84`. The opt-in one-shot command revalidates the actor/scene/group/heap tuple and expected vptrs. Synthetic tests pass; no live transition sample has been run, and a pointer tuple is not a scene serial or destructor proof. Runtime rendering remains **NO-GO**.

M5 Phase 1.22: [PAL destructor and shared-data audit](../docs/references/draw-only-render-contract.md#phase-122-pal-destructor-dispatch-actor-teardown-and-shared-joint-mutations) maps J3DModelX/J3DModel/J3DMtxBuffer/packet vtable destructor entries and audited actor/scene teardown callers. The Scene heap is current during normal placement allocation, but `__dl` resolves `sCurrentHeap` again at deletion; no allocator heap identity is preserved. LiveActor destruction does not destroy its ModelManager, and traced scene teardown bulk-reclaims its heap after resource-holder removal without iterating actors. `newJ3DModel` writes to joint fields reachable through shared `J3DModelData`; independent instances are therefore not proven safe. Runtime remains **NO-GO**. One bounded read-only model/scene/heap observation is specified; it cannot establish destructor dispatch.

M5 Phase 1.21: [native J3D ownership table](../docs/references/draw-only-render-contract.md#native-ownership-and-release-matrix) traces ModelManager → `newJ3DModel` → `J3DModelX` → `entryModelData`, default current-heap allocation, per-instance matrix/packet arrays, destructor bodies, and scene-heap teardown. Sharing the `J3DModelData*` is API-supported but not safe to call immutable: `newJ3DModel` invokes model-data invalidation routines; exact owners of packet/matrix cleanup remain unresolved. The host tests now model scene-arena allocation, rollback, heap identity, borrowed-resource lifetime, detachment, and bulk teardown. Runtime remains **NO-GO**. Next: bounded offline PAL vtable/destructor-owner audit.

M5 Phase 1.20: [J3D instance ownership findings](../docs/references/draw-only-render-contract.md#pal-construction-and-cleanup-evidence) trace the PAL constructor into `J3DModel::entryModelData`, per-instance matrix-buffer and packet creation, and the `viewCalcRef` candidate. A host-only Rust ownership model tests separate mutable buffers/packets, shared immutable resource identity, and rollback. This establishes only the model's own invariants; it does not prove the actual PAL allocator/destructor pair, resource immutability, or GX/cache safety. Runtime remains **NO-GO**. The next bounded experiment is an offline PAL DOL owner/callsite audit for all `J3DModelX` construction and destruction callers, to identify who owns `J3DModelData` and scene-heap allocations before considering runtime observation.

M5 Phase 1.19: [draw-only renderer contract](../docs/references/draw-only-render-contract.md) audits a static-offset duplicate using a separate `J3DModelX`, independent matrix/packet state, and `viewCalcRef` as a research lead. Direct draw mutates model, packet, global J3D, and GX state; cache ordering and resource teardown remain unproven, so runtime remains **NO-GO**. The optional xdelta route is evaluated in [deployment assessment](../docs/references/xdelta-deployment-assessment.md): a future DOL-scoped delta may improve deterministic installation but cannot remove renderer safety blockers; no patch is generated or applied.

M5 Phase 1.18: [PAL render callsite ABI audit](../docs/references/pal-render-callsite-abi.md) records caller argument/return behavior for all four inspected callsites and sets an explicit **NO-GO** for runtime patching until matrix ownership, cache maintenance, model/GX state restoration, and teardown are verified. `inspect-render-prototype` now checks bounded caller context on both sides of each branch and has synthetic mutation tests. A static-offset duplicate does not require the animator init/calc hooks in principle, but the draw-only path is not yet safe to implement.

M5 Phase 1.17: the `inspect-render-prototype` command verifies four PAL shared-model callsite instructions, branch targets, and draw argument context against the exact DOL, after PAL map validation. It emits no patch bytes. An executable prototype remains blocked: safe displaced-instruction handling/caller ABI, independent `J3DMtxBuffer` construction and ownership, cache maintenance, model-state restoration, synchronization, and scene/model lifetime have not all been proven. See [shared renderer analysis](../docs/references/smgnetworkmultiplayer-analysis.md). A runtime patch must not proceed until these gates pass.

M5 Phase 1.16: [SMGNetworkMultiplayer rendering review](../docs/references/smgnetworkmultiplayer-analysis.md) recommends investigating a one-slot shared-Mario-model draw path before scene-owned actor registration. Its source draws remote Mario poses using per-slot `J3DMtxBuffer`/`XanimePlayer` state inside the local Mario callbacks, avoiding per-remote scheduler registrations. Four relevant upstream hook callsites resolve to expected callees in the exact PAL DOL; this is static callsite evidence only, not a PAL patch or runtime compatibility claim. Upstream sources have no repository license file, and its README targets USA packages. Wario/Waluigi require separate compatible model/animation resources and remain unverified.

M5 Phase 1.15: scene-init reservation architecture and exact GameScene placement → callback-buffer allocation → initial connection ordering are documented in [M5 scene-init reservation](M5-scene-init-reservation.md). Three inert reserved actors are a static design deduction only; capacity safety, resource ownership, and runtime activation/hide behavior remain unverified.

M5 Phase 1.14: user-reported live callback metadata from PID 119908 is documented in [Penguin rendering research](../docs/references/penguin-rendering-path.md): movement `0x28` 25/52, calcAnim `0x06` 13/18, draw-buffer `0x10` executors 7/80 and active 3/80, execute-holder 1702/4096, all five pending flags zero. These aggregate counts do not identify Penguins or prove spare slots safe to consume. The observer remains read-only; no registration/spawn was tested.

M5 Phase 1.13: added the opt-in callback-list metadata observer specified in [M5 callback-list observer](M5-callback-list-observer.md). It targets movement `0x28`, calcAnim `0x06`, and Penguin draw-buffer group `0x10`, and reads metadata only.

M5 Phase 1.12 update: user-reported read-only samples (PID 114728) show Beach Bowl execute holder `0x80F945C4`, array `0x80F945F4`, count 1702/4096, and destination holder `0x80F94538`, array `0x80F94568`, count 359/4096. All five pending flags were zero. AllLiveActorGroup changed `0x80FA07D8` → `0x80FA074C`; the Scene numeric address was reused and does not identify a persistent scene instance. PAL `NameObjListExecutor` and draw-buffer analysis finds callback and actor arrays pre-sized during scene setup from per-category/model counts; the append paths do not prove safe late growth. Holder headroom therefore does not make remote actor registration safe. No callback records or per-model draw entries were read; callback metadata remains aggregate and is not attributed to individual actors. Model/archive residency, transitive resource ownership, list reservation/resizing, and teardown remain blockers. See [Penguin live investigation](../docs/references/penguin-live-investigation.md), [Penguin actor/render path](../docs/references/penguin-rendering-path.md), and [PAL placement heap/lifetime trace](../docs/references/pal-placement-heap-lifetime.md).

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

Research-only actor path: the references above record exact-hash PAL evidence and Petari semantics for placement, heap selection, execute-info scheduling, callback list allocation, resource ownership, and teardown. User reports live group/holder transitions, including Scene address reuse; no heap destruction or constructor-time identity is inferred. `observe-scheduler` validated the holder before the latest user sample, which reports 1702/4096 and 359/4096 entries with zero pending flags. It does not inspect category or draw-buffer arrays. Static PAL analysis finds unchecked appends after capacity preparation, so no safe dynamic registration route is established. Model/archive residency and complete scene teardown/resource ownership remain unverified. No actor was spawned.

Read-only scheduler validation workflows (manual live check; do not launch a Dolphin instance from the sandbox):

```powershell
Set-Location C:\Users\maxim\Projects\multigalaxy1
$DolphinPid = 12345 # set the PID of the already-running isolated Dolphin.exe
cargo run -p multigalaxy -- observe-scheduler .\development.local.toml $DolphinPid
cargo run -p multigalaxy -- observe-callback-lists .\development.local.toml $DolphinPid
```

Run once in a playable PAL scene and again after a settled mission transition. Expected output includes `capacity=4096`, the live `count`, `remaining`, holder/array pointers, the five pending-operation flags, and `callback_lists=not_read`. A null slot is expected if the execute holder has not been lazily created in that scene. Results are live observations only after the command succeeds; static validation and synthetic tests are not live proof. Do not interpret remaining slots as authorization to call `registerActor`.

The callback-list command reports movement/calcAnim list identities, callback-array pointers, count/capacity/headroom/check counts, draw-buffer group executor/active-list metadata, and pending flags. Run it only in a settled playable scene and after a settled scene transition. It rejects nonzero pending flags and changing metadata. It never reads callback or actor entries. Treat output as live evidence only after it succeeds against the visible user-side stock Dolphin process.

Before actor implementation: produce a PAL instruction-signature manifest and resolve prototypes for constructor, model initialization, scheduler connect/disconnect, scene creation/destruction, and draw submission. Run `observe-heaps` before and after a normal mission transition with the same Dolphin PID; changed scene/group/heap addresses support an identity change, while reused addresses are inconclusive. These gameplay-time samples do not prove heap identity at constructor execution. Confirm execute-info setup/capacity, actor-system-info/clipping behavior, a scene-mounted model/archive, complete model-resource ownership, and teardown callback order from matching Petari source/disassembly. See the prerequisites and stop conditions in `docs/references/pal-placement-heap-lifetime.md`.

Runtime progression: first local-only synthetic mailbox state with a placeholder actor; then remote M4 state; then transition/disconnect/fault injection. Capture Dolphin version, DOL fingerprint, patch activation, scene name/epoch, mailbox generations, actor lifecycle log, FPS/VPS and emulation speed. No successful runtime result may be inferred from compilation.

