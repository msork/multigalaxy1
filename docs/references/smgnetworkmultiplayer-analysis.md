# SMGNetworkMultiplayer rendering architecture review

Review date: 2026-11-05. Target comparison: PAL RMGP01 Rev 0, stock Dolphin, current M5 shared-render and scene-reservation research. Upstream source reviewed from `Headpenguin/SMGNetworkMultiplayer` default branch `master`, tree `a67c85fd76715a06811221cf749099c1bf43bd50`; relevant file blobs: `source/multimodel.cpp` `ba45ec702ba5fcf82661206585103b6b5fc3fd77`, `source/netActor.cpp` `dd8dcb50ae872d606ff61a39bf648348b6f7341b`, `include/netActor.hpp` `5eb8b93959e04ae02fcfa3869ce3c0ab4cffe122`.

This is source research only. No upstream source was copied, no game memory was changed, and no hook or actor was installed.

## Repository and target scope

The project README names its shipped files `riivo_USA.xml` and `CustomCode_USA.bin`, and asks builders to use multiplayer branches of its Bussun, Petari, and Kamek forks. This is evidence that the published workflow targets the USA release. The README does not claim PAL support. Our exact-hash PAL map does contain corresponding Mario/J3D symbols and the render callsites below resolve to the same named callees, but that does not establish that the upstream USA binary, offsets, data structures, or patch package work on PAL.

The inspected repository tree contains `credits.txt` but no `LICENSE` file. Credits attribute Kamek, Petari, Bussun, and named contributors; this is attribution, not a license grant. Treat upstream implementation code as unavailable for copying, porting, or redistribution unless its author clarifies licensing. This analysis records behavior and does not import code. [README](https://github.com/Headpenguin/SMGNetworkMultiplayer/blob/master/README.md), [credits](https://github.com/Headpenguin/SMGNetworkMultiplayer/blob/master/credits.txt), [multimodel.cpp](https://github.com/Headpenguin/SMGNetworkMultiplayer/blob/master/source/multimodel.cpp), [netActor.cpp](https://github.com/Headpenguin/SMGNetworkMultiplayer/blob/master/source/netActor.cpp).

## `multimodel.cpp`: shared local Mario model

The file defines process-lifetime arrays for each remote slot: a `J3DMtxBuffer`, a base transform matrix, and `XanimePlayer` storage. The upstream `MAX_PLAYER_COUNT` is 8, so its arrays reserve seven remote slots. MultiGalaxy1 needs three remote slots for four total participants; this is only a capacity difference, not a PAL adaptation.

The implementation hooks the local Mario rendering/animation path:

1. After the local Mario model manager is initialized, it gets the local `J3DModelX`, copies its base `J3DMtxBuffer` into one buffer per remote, and calls `createDoubleDrawMtx` for each buffer using the local `J3DModelData`.
2. At `MarioAnimator` initialization it initializes the local animator and constructs an `XanimePlayer` per remote using the **same local model and the same local animation resource table**.
3. In the local Mario animation callback, it updates each active remote's pose and transform. It temporarily switches the one shared model's matrix-buffer pointer and base transform, evaluates that remote's animation/joint matrices, then restores the local base transform and matrix-buffer pointer and runs the local animator calculation.
4. In the local Mario model draw callback, it draws the local model, then loops active, non-clipped remotes. For each it points the shared model at that remote's precomputed matrix buffer, prepares shape packets, and calls `J3DModelX::directDraw` again.

**What this supports in principle:** separate remote positions/orientations are represented in per-slot matrices; `XanimePlayer` gives each slot animation playback state; active flags and a clipping check skip hidden/out-of-view slots. A remote visual does not need its own `LiveActor`, `NameObjExecuteInfo`, movement category entry, calcAnim category entry, draw-buffer executor, AllLiveActorGroup entry, or clipping registration. The local Mario's already scheduled animation and draw callbacks are reused. Every Dolphin client still draws at its own full-screen camera; this path does not split or replace the camera.

**What it does not provide:** independent model geometry, independent model/archive ownership, an engine actor lifetime, gameplay collision, sensors, gravity simulation, camera/control state, or a generic player renderer. The code renders multiple Mario poses from one local Mario model and its resources. It manually calculates and draws them inside the local Mario callbacks.

The model is shared mutable state. The code changes `_24`, `_84`, joint calculation state, `j3dSys.mCurrentModel`, and other model/animation internals around per-remote calculations. It also contains an explicit race-condition comment for activity status and an “inconsistent” comment on buffer creation. The source demonstrates the technique; it does not prove robust scene transitions, thread safety, correct GX state isolation, or absence of visual side effects. In particular, the per-remote `J3DMtxBuffer` allocations have no matching deallocation in this file, and their model-data/resource pointers are borrowed from the local Mario. Their lifetime across death, transformation, model replacement, and scene transition must be resolved.

There are also source-level details to check before treating the upstream implementation as a clean template: the loop in `createXanimes` indexes `playerXanimes[i]` for assignment but calls `playerXanimes->changeAnimation` and `playerXanimes->setDefaultAnimation`, which address slot zero each iteration. The storage wrapper is raw `u32` storage cast to `XanimePlayer*` and assigned into; alignment, construction/destruction, and object lifetime need explicit validation. The joint-tree calculation sets the shared `Spine1` joint's `mMtxCalc` to null. These may be intentional workarounds or defects; this source review cannot determine which. A new implementation should use correctly constructed objects and avoid copying these patterns without tests.

### PAL callsite comparison

The PAL DOL is the validated `RMGP01` Rev 0 input, matched by `validate-mario-map` to the local Petari symbol file. The following addresses were independently translated to DOL file offsets and words during this review. They establish PAL callsite semantics only; they do not validate an upstream USA patch or authorize hooks.

| Upstream source expression | PAL hook candidate | DOL file offset / word | Decoded PAL target | Finding |
|---|---:|---|---|---|
| `initDrawAndModel + 0x214` | `0x802B543C` | `0x2B097C`: `48132BF1` | `MR::getJ3DModel`, `0x803E802C` | Semantic callsite match |
| `MarioAnimator` ctor `+ 0x24` | `0x802CA78C` | `0x2C5CCC`: `4800001D` | `MarioAnimator::init`, `0x802CA7A8` | Semantic callsite match |
| `MarioActor::calcAnim + 0x2A4` | `0x802B2CE8` | `0x2AE228`: `48019649` | `MarioAnimator::calc`, `0x802CC330` | Semantic callsite match |
| `drawMarioModel + 0x1B8` | `0x802B62E4` | `0x2B1824`: `4BFF083D` | `J3DModelX::directDraw`, `0x802A6B20` | Semantic callsite match |
| `MarioActor::init2 + 0x698` in `netActor.cpp` | `0x802AF418` | `0x2AA958`: `9421FFC0` | Start of `MarioActor::initAfterPlacement` | Not a callsite; `kmBranch` overwrites a function-entry instruction. Do not port without tracing displaced-function behavior. |

The four rendering call offsets happen to identify expected PAL callees. A valid PAL patch still needs PAL-specific signature guards, exact modified-instruction/trampoline behavior, relocation validation, AllStars conflict analysis, and runtime tests. The fifth expression is especially unsafe to infer from function-size arithmetic: the PAL address is the next method's prologue, not code after the `init2` function body in a way that preserves that method automatically.

## `netActor.cpp`: separate static interaction actor

`netActor.cpp` has one static-storage `NetPlayerActor multiplayerActor`, placement-constructed from a Mario initialization hook. Its `NetActor` base is a `LiveActor`. `_init` makes hit sensors for remote slots, connects this one actor to scene scheduling with movement category `0x25` and the other category arguments `-1`, invalidates clipping, calls `appear`, and stores the local `MarioActor` from `MarioHolder`. Its movement path processes star-piece work and gravity-relative interaction state; message callbacks can cause Mario trample responses.

This is a gameplay/sensor bridge actor, **not** the set of rendered remote Marios. It still registers a LiveActor and participates in scene scheduling, has gameplay-facing sensors, and is tied to local Mario initialization. It does not establish a safe cosmetic actor factory or a PAL-safe lifecycle. Its hook at `init2 + 0x698` resolves to `initAfterPlacement` entry on PAL as shown above. Keep it separate from the shared-model rendering decision and do not reuse it for remote visuals.

## Comparison with MultiGalaxy1's scene-owned actor reservation plan

| Criterion | Shared local-model draw path | Separate scene-owned cosmetic actor |
|---|---|---|
| Remote actor objects | None; per-remote render state only | One LiveActor/NameObj per remote |
| Actor group/clipping reservations | Avoided for render-only copies | Required and currently bounds-unproven |
| Execute/movement/calcAnim/draw-list capacity | Avoided per remote by running work from local Mario hooks | Must be reserved before bulk allocation; append paths are not proven safe |
| Draw-buffer/per-model executor capacity | Avoided if direct drawing the existing local Mario model | Required for normal scheduled model drawing |
| Separate transform and animation state | Per-slot matrices and XanimePlayers; same model/model-data/resource table | Naturally isolated actor/model state if properly initialized |
| Visibility/disconnect | Skip drawing when remote inactive/stale; no actor creation/destruction | Appearance/death deferred through actor/scheduler lifecycle; teardown unproven |
| Same-character assets | Reuses local Mario model and animation archive | Needs actor/model resources per object or shared resource management |
| Wario/Waluigi geometry | Not supplied by this technique; needs separate model data and animation resources | Can use distinct models if legally distributable and safely loaded |
| Scene transitions | Must rebind/reset borrowed local Mario model/resource pointers and buffers | Scene-scoped objects/resources must quiesce before teardown |
| Implementation surface | Several high-risk Mario animation/draw hooks and J3D mutable state | Actor factory, system registration, capacities, resources, scheduler, teardown |
| PAL evidence | Four callsites semantically match the PAL named callees; no built PAL patch/runtime proof | GameScene reservation timing is known; actual safe extension/reservation remains unproven |
| Best first use | One cosmetic remote Mario visual in one test scene | Later option if independent actors or distinct model lifecycle require it |

## Recommendation: one-slot shared-model rendering proof

Prefer the shared-model path for the first future rendering prototype, rather than spending the first proof on actor-group/scheduler reservations. It has the smallest engine object footprint and directly answers whether the already-running local render pass can draw a remote pose while leaving local camera/control/player ownership alone. This choice is an architecture recommendation, not authorization to patch.

Proposed gated test after a separate implementation approval:

1. **Offline host-side proof:** synthetic bounded pose and active/stale state enter a 1-slot render-state object; verify matrix construction, sequence rejection, hide-on-stale, and scene-generation reset without Dolphin.
2. **PAL hook audit:** pin DOL SHA-1, exact symbols and all four callsite words above; inspect caller/callee register ABI and displaced instructions; verify the AllStars ranges are disjoint; do not use the `initAfterPlacement` hook. Require a reversible hook plan and clean unpatched boot fallback.
3. **One local render slot:** reuse the current local Mario model and its PAL animation resources; draw one neutral/idle remote Mario at a fixed synthetic pose with one separate matrix buffer/XanimePlayer. The render slot must not enter MarioHolder, controller/camera ownership, LiveActor/NameObj registries, clipping/sensor systems, or M4 networking.
4. **Lifecycle test:** toggle active/stale state, reconnect, pause/resume, Mario death/respawn, transformation/model replacement if reachable, and repeated scene transitions. Recreate only after a valid new local model/animation resource is bound. No pointer may survive a generation change. If the relevant local Mario/model is unavailable, hide the remote and fail closed.
5. **Scale to three Mario remotes** only after the one-slot test passes; each slot gets separate animation and matrix storage, with draw-state restoration checks and no queue/data races. Feed M4 samples only after synthetic input passes.

Acceptance requires visible motion and animation independently changing for the remote copy while local Mario remains correct; stale/disconnect hides within the specified timeout; scene transition never draws with old model/resource pointers; no scheduler counts change attributable to remotes; and full-screen local camera/campaign behavior remains intact. Compilation alone does not pass this gate.

## Wario/Waluigi and other model families

The shared Mario path is **not** a reusable multi-character asset path. Wario/Waluigi require distinct, legally usable model data, material/texture data, skeleton compatibility, animation resources, and per-character J3D model/matrix/animation instances. If assets exist in the user's game files, they may be investigated locally but must not be redistributed; the current SMG1 asset inventory has not verified such models. Otherwise only a legally distributable compatible asset can be considered.

Calling `directDraw` for a separate `J3DModelX` inside a local draw hook might avoid remote scheduler entries, but this is an inference. It requires proving how to load and retain each resource under the correct heap, construct model/animation state without camera/controller helpers, perform per-frame matrices and cache flushes, establish sort/transparency/lighting/GX state, and release or reset resources on scene/model changes. Run that as a distinct later feasibility test after one shared Mario succeeds. Until then, retain the scene-owned cosmetic actor path as a fallback research branch for models that cannot be drawn safely through the shared local model; its capacity ledger and teardown blockers remain.

## Evidence classes and remaining questions

### Local offline callsite audit addition

`multigalaxy inspect-render-prototype <PAL-main.dol> <Petari-symbols.txt>` checks the exact PAL input fingerprint/symbol map, then verifies the four candidate call instructions and decoded relative branch targets documented above. For the draw call it also checks the two preceding argument setup words (`mr r3,r29; li r4,0`). The Rust implementation has synthetic DOL fixtures for valid sites and mismatched/truncated/unmapped input. This is an offline static audit only: it neither writes a DOL nor creates a Gecko/PPC hook. Passing it confirms only those signatures, not safe register preservation, displaced call behavior, model state restoration, or runtime compatibility.

The independent matrix/animation state remains an unresolved lifetime and ABI blocker. In particular, Petari's `J3DMtxBuffer` source shape and allocator behavior do not prove that a copied `J3DMtxBuffer2`/model pointer is valid in PAL, that its allocations can be safely freed, or that cache/GX state is restored. Therefore no experimental executable patch is emitted or enabled.

- **Verified upstream source facts:** static matrix-buffer and XanimePlayer arrays; per-remote animation/matrix calculation; direct draw loop for active/non-clipped peers; one separate static `NetPlayerActor` used for sensors/interactions; upstream build/docs specify USA-oriented Riivolution package and multiplayer dependency forks.
- **Verified target DOL facts:** exact PAL function symbols, code words, file offsets, and decoded call targets in the table. The map tool validates the PAL DOL identity and symbols. This review did not create a PAL patch.
- **Static deductions:** the first model-sharing renderer can bypass one scheduler entry per remote because it executes inside local Mario's scheduled callbacks. It likely preserves each full-screen client's own camera because calls occur in that client's normal render pass.
- **Unverified:** upstream runtime quality across scene transitions; safe construction/destruction of the matrix/animation state; no disturbance to Mario's shared J3D model state; actual PAL ABI/hook safety; AllStars overlap; Wario/Waluigi asset availability or compatibility; any cross-language license for upstream code.

## Sources

- [SMGNetworkMultiplayer README](https://github.com/Headpenguin/SMGNetworkMultiplayer/blob/master/README.md)
- [SMGNetworkMultiplayer `multimodel.cpp`](https://github.com/Headpenguin/SMGNetworkMultiplayer/blob/master/source/multimodel.cpp)
- [SMGNetworkMultiplayer `netActor.cpp`](https://github.com/Headpenguin/SMGNetworkMultiplayer/blob/master/source/netActor.cpp)
- [SMGNetworkMultiplayer `netActor.hpp`](https://github.com/Headpenguin/SMGNetworkMultiplayer/blob/master/include/netActor.hpp)
- [SMGNetworkMultiplayer credits](https://github.com/Headpenguin/SMGNetworkMultiplayer/blob/master/credits.txt)
- [Kamek branch command](https://github.com/Treeki/Kamek/blob/master/Source/Commands/BranchCommand.cs) and [BranchHook](https://github.com/Treeki/Kamek/blob/master/Source/Hooks/BranchHook.cs): the branch command writes a PPC branch at the selected instruction; it is not a runtime trampoline that automatically preserves the displaced operation.
- Local PAL map: `target/petari-rmgp01-symbols.txt`; validation command in this repository's `specs/M5-scene-init-reservation.md`.
