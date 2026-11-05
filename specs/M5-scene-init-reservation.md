# M5 scene-initialization reservation architecture

Status: research-only design for up to three separate remote cosmetic actors per local Dolphin client. No actor, reservation, memory write, hook, injection, or Dolphin change is implemented or authorized here. Target is PAL RMGP01 Rev 0 and stock Dolphin.

## 1. Objective

Determine whether up to three remote visuals can be constructed as inert, scene-owned objects before scheduler storage allocation, then activated on remote presence and hidden on disconnect. Existing campaign actors, including Penguins, remain untouched.

## 2. Verified PAL ordering

Addresses below are PAL RMGP01 Rev 0 guest addresses; file offsets refer to the validated DOL in `reference/original/main.dol`. Direct call-site facts are DOL evidence. Field semantics marked Petari are source corroboration, not independent runtime proof.

1. `GameSystemSceneController::initializeScene` (`0x8039CEAC`, file `0x3983EC`) creates/selects the Scene heap, installs a Scene, initializes its SceneNameObjListExecutor and SceneObjHolder, then invokes the Scene's virtual `init` at `0x8039CF54`.
2. The normal GameScene init path is `GameScene::init` (`0x8033E510`, file `0x339A50`). It calls `SceneFunction::startActorPlacement` at `0x8033E6A0`, which calls `SceneDataInitializer::startActorPlacement` (`0x80343A68`); that eight-byte thunk branches to `StageDataHolder::initPlacement` (`0x80346A4C`, file `0x341F8C`). The latter calls five `PlacementInfoOrdered::initPlacement` routines at `0x80346A74`, `0x80346A7C`, `0x80346A88`, `0x80346A90`, and `0x80346A98`.
3. `PlacementInfoOrdered::initPlacement` (`0x8034207C`, file `0x33D5BC`) invokes the selected creator at `0x80342100`, calls actor-system initialization at `0x8034210C`, calls the returned object's virtual `init` at `0x80342124`, then updates actor-system information at `0x8034212C`. A LiveActor base constructor registers the object with AllLiveActorGroup and ClippingDirector before derived initialization; Petari `LiveActor.cpp` corroborates this order.
4. In the normal data-loading path, `SceneDataInitializer::startActorFileLoadCommon` (`0x80343A58`) and `startActorFileLoadScenario` (`0x80343A60`) branch to StageDataHolder file-load routines (`0x803469BC`, `0x80346A00`), which call `PlacementInfoOrdered::requestFileLoad` (`0x80341FD4`) before placement construction. That requests/mounts the factory's actor archives. Runtime creation outside this path has no implied archive residency.
5. A Penguin's `init` calls `initModelManagerWithAnm` and then `connectToSceneNpc` (`0x803EF020`). That calls `registerNameObjToExecuteHolder` (`0x8026266C`), which reaches `NameObjExecuteHolder::registerActor` (`0x80262140`) and `NameObjExecuteInfo::setConnectInfo` (`0x80261C50`). `setConnectInfo` registers the draw-buffer/model executor first, then increments non-`-1` movement, calcAnim, and draw category preparation counts. The existing Penguin tuple is movement `0x28`, calcAnim `0x06`, draw-buffer `0x10`, draw `-1`; it is evidence for Penguin only.
6. After the Scene virtual `init` returns, `initializeScene` calls `SceneFunction::allocateDrawBufferActorList` at `0x8039CF58` (file `0x398498`). The function symbol starts at `0x80344808` (file `0x33FD48`); its call at `0x80344820` invokes `NameObjListExecutor::allocateDrawBufferActorList` (`0x8026C00C`, file `0x26754C`). That allocates movement/calcAnim/draw category buffers, then draw-buffer actor arrays. Its next call, at `0x80344824`, is `MR::initConnectting` (`0x802626D0`, file `0x25DC10`).
7. `initConnectting` walks the registered execute-info records (`NameObjExecuteHolder::initConnectting`, `0x8026219C`; `NameObjExecuteInfo::initConnectting`, `0x80261D24`). Petari source says `setConnectInfo` starts both connection states at 2. The initial pass connects a state requested as 1 and changes other states to 5 (disconnected). Later `LiveActor::makeActorAppeared` requests connection; `makeActorDead` requests deferred disconnection. Actual callback appends/removals occur through the scheduler paths after those requests.

For this GameScene path, DOL call order establishes: synchronous placement registration occurs during virtual scene init; the bulk list allocation and initial connect sweep occur after that init returns. Other Scene subclasses must be traced separately before relying on this ordering.

Critical branch/call-site words independently read from the pinned DOL are:

| File offset | Guest address | Instruction bytes | Meaning |
|---:|---:|---|---|
| `0x398494` | `0x8039CF54` | `4E800421` | Indirect Scene `init` call |
| `0x398498` | `0x8039CF58` | `4BFA78B1` | Call `SceneFunction::allocateDrawBufferActorList` |
| `0x339BE0` | `0x8033E6A0` | `48005FD9` | GameScene calls `SceneFunction::startActorPlacement` |
| `0x33FBD0` | `0x80344690` | `4BFFF3D9` | Calls SceneDataInitializer placement wrapper |
| `0x33EFAC` | `0x80343A6C` | `48002FE0` | Branches to StageDataHolder placement initialization |
| `0x341FB4` / `0x341FBC` / `0x341FC8` / `0x341FD0` / `0x341FD8` | `0x80346A74` / `0x80346A7C` / `0x80346A88` / `0x80346A90` / `0x80346A98` | `4BFFB609` / `4BFFB601` / `4BFFB5F5` / `4BFFB5ED` / `4BFFB5E5` | Five calls to `PlacementInfoOrdered::initPlacement` |
| `0x33FD60` | `0x80344820` | `4BF277ED` | Calls `NameObjListExecutor::allocateDrawBufferActorList` |
| `0x33FD64` | `0x80344824` | `4BF1DEAD` | Calls `MR::initConnectting` after allocation |
| `0x33D640` / `0x33D64C` / `0x33D664` | `0x80342100` / `0x8034210C` / `0x80342124` | `4E800421` / `4BE1AD05` / `4E800421` | Creator call, actor-system initialization call, and virtual actor `init` call within each placement |

## 3. Reservation ledger for three remote visuals

Counts below are additional requirements for three remote objects, subject to the exact callback set and model choice. They are accounting targets, not a safe capacity claim.

| Storage/registration | Per remote visual | Up-to-three planning bound | Safe-bound status |
|---|---:|---:|---|
| AllLiveActorGroup | 1 object registration in the LiveActor constructor | 3 entries | Petari gives fixed group capacity 2560. Runtime count at reservation time and PAL append bounds are not in the callback observer; validate constructor/system-info path before actor creation. |
| ClippingActorHolder | 1 ClippingActorInfo created/registered by the LiveActor constructor; it moves between dead/active clipping lists | 3 infos; list placement changes with appearance | Petari creates four lists with capacity 2560 each. Exact PAL `add` capacity behavior, active count, and safe group/view initialization for a synthetic actor remain unverified. |
| NameObjExecuteHolder | 1 execute-info record (12 bytes) | 3 records | PAL advertises 4096; `registerActor` appends without a count-vs-capacity guard. Must prove count+3 fits and actor index/lifecycle semantics. |
| Movement callback | 1 category preallocation increment and, while connected, 1 callback in the chosen category | 3 increments/callbacks | For a custom visual, category and callback must be chosen from its exact vtable behavior. Penguin category `0x28` is not automatically suitable. Category append has no PAL capacity guard; existing capacity may be reserved for disconnected campaign actors. |
| Calc-animation callback | 1 preallocation increment and, while connected, 1 callback in chosen category | 3 | Same constraints. Penguin category `0x06` is not automatically suitable. |
| Draw category | 0 only if the custom visual intentionally uses draw category `-1` | 0 | The remote visual must still be rendered through a valid model/draw-buffer path. Do not copy Penguin's `-1` by assumption. |
| DrawBufferGroup type `0x10` executor list | 1 executor if model-resource key is new; reuse only on exact key match | 1 shared placeholder executor or up to 3 distinct model executors | Static PAL table capacity 80. Vector append lacks an in-path bounds check. Current group count does not identify the Penguin or candidate executor. |
| Per-model DrawBufferExecuter actor list | 1 actor pointer in the chosen model executor | 3 for one shared model, or 1 in each of three model executors | Capacity is the precomputed model-specific `mDrawBufferCount`; no live model executor/count was inspected. `add` writes at count without a capacity check. |
| Active DrawBufferExecuter list | 1 entry when an executor transitions from zero active actors to active | At most 3 if models use distinct executors; 1 for one shared executor | Capacity 80 from group table. Append has no in-path bounds check. Already-active shared executor needs no extra active-executor entry, but this cannot be assumed from group-wide `3/80`. |
| DrawBufferHolder camera-group list | A type-0x10 group entry when first activated for its camera | Usually one for a shared draw group/camera, subject to the relevant vector | The observer does not report these camera vectors' live counts/capacities. Verify the type-0x10 camera route and exact list bounds before implementation. |

The PID 119908 sample (movement 25/52, calcAnim 13/18, group executors 7/80, active executors 3/80, execute holder 1702/4096) is aggregate user-reported evidence. The remaining numeric slots can be needed by registered-but-currently-disconnected campaign actors. No per-Penguin attribution or per-model draw occupancy was observed; none of the sample proves that three additions are safe.

## 4. Earliest and latest reservation windows

- **Actor-group and clipping registration:** a LiveActor constructor registers immediately, before its `init`. Capacity and initialization requirements must be validated before each constructor call. The normal `PlacementInfoOrdered` path then calls actor-system initialization before virtual `init`. The latest point for these registrations is therefore construction itself; a post-construction reservation cannot repair a missed group/clipping entry. A custom creation path must reproduce the normal actor-system setup or prove a smaller cosmetic-specific path.
- **Execute-info, category preparation, draw executor, per-model count:** the SceneNameObjListExecutor and its DrawBufferHolder exist before Scene virtual `init`. Registration via `setConnectInfo` must occur before `NameObjListExecutor::allocateDrawBufferActorList`; category and per-model counts are consumed by that allocator. The latest candidate window is during Scene init, after the ordinary placement registrations and before GameScene init returns to `initializeScene`. It is only a static ordering window: no hook, atomic reservation API, or validated custom factory is established.
- **Activation/visibility:** all inert actor records must be present before the post-allocation `initConnectting` sweep. A prepared record can remain unconnected if it has not requested appearance, then be connected on presence. This is a Petari-backed lifecycle deduction, not runtime validation for a new cosmetic type. A later disconnect requests removal from movement/draw lists; the deferred operations must complete while the old scene and executor still exist.
- **Latest overall point:** before Scene init returns to `initializeScene` and immediately before its call to `SceneFunction::allocateDrawBufferActorList`. Past that point, any added movement/calcAnim/model actor registration would consume arrays sized from earlier preparation; draw executor model arrays may be absent or undersized. The execute holder's fixed maximum does not make a late actor safe.

The earliest combined **candidate** is after SceneNameObjListExecutor/DrawBufferHolder initialization, after the required archives are available and actor/clipping services can be initialized, but during `GameScene::init` before list allocation. The latest combined candidate is after ordinary placement accounting has completed and before `GameScene::init` returns. No PAL extension hook, synthetic placement iterator, actor-system-info recipe, or safe reservation API is verified; these are timing bounds for investigation, not permission to spawn.

**Inert-preparation assessment:** static PAL control flow plus Petari state logic supports this as a design hypothesis. `setConnectInfo` initializes both connection state bytes to 2; the initial post-allocation `initConnectting` pass changes unrequested states to 5 (disconnected). A later appearance request can move 5→4 and be processed into callback registration; `makeActorDead` requests deferred disconnection while retaining the object and its actor-group entry. Therefore prebuilt, fully initialized, unappeared cosmetic actors could remain unconnected until a remote joins, then be hidden on disconnect. This remains untested and depends on valid model resources, group/clipping/system-info setup, safe callback categories, and deferred-work quiescence.

For AllLiveActorGroup, Petari sets the group capacity to 2560. For clipping, Petari's `ClippingActorHolder` constructs four `ClippingActorInfoList`s with capacity 2560; each LiveActor gets one ClippingActorInfo at construction, which moves between the lists as clipping/appearance state changes. The capacity constants are source evidence. The PAL list-add bound checks and the runtime occupancy needed to prove three free entries remain unverified.

## 5. Model/archive ownership and teardown

Normal placement requests actor archives before `initPlacement`. Penguin's normal creator allocates through `JKRHeap::sCurrentHeap`; the controller selects the Scene heap before Scene initialization, with no heap switch in the traced normal placement call chain. ModelManager/resource-holder setup also uses the current heap when no explicit heap is supplied. This is static evidence scoped to normal placement, not proof for a future callback.

The scene teardown path destroys Scene-owned scheduler/holder structures, removes resource holders associated with the Scene heap, then destroys the Scene heaps. The traced bulk teardown does not call every placed actor's destructor. A hidden actor must therefore remain scene-scoped; individual delete is unsafe while actor-group, clipping, scheduler, draw, or resource references might remain. Resource-holder cleanup does not yet account for every transitive J3D model, animation controller, display-list, and GX allocation, nor does it prove all destructors are unnecessary for a custom model. Scene numeric addresses can be reused; host references need a scene-generation token and must be invalidated before teardown starts.

## 6. Staged prototype and go/no-go gates

1. **Static call-path gate:** retain exact PAL DOL/signature verification for the target Scene subclass, load/placement order, actor init, allocation, initial connection, and teardown. Go only if the intended pre-allocation insertion point is unambiguous and AllStars patch ranges remain disjoint.
2. **Capacity/registration gate:** resolve PAL bounds behavior and live counts for AllLiveActorGroup, clipping lists, execute holder, chosen movement/calcAnim categories, DrawBufferGroup, camera-group vector, and each selected model executor. Reserve three records and callbacks before allocation; show campaign actors retain their required future slots. Stop if any append can exceed capacity or registration ordering is uncertain.
3. **Resource gate:** choose a separate cosmetic actor type and exact model/archive/animation. Prove the archive is mounted before init; identify its model-resource key and executor reuse/new case; trace all resource allocations, heap ownership, and cleanup. Do not use an existing Penguin or Mario as the remote object.
4. **Lifecycle gate:** prove the actor can be registered inert, becomes connected only on appearance, and becomes hidden/disconnected on stale data. Confirm deferred disconnect queues drain before scene destruction; do not free the actor mid-scene. Stop if sensors, collision, gravity/AI, camera, input, or progression behavior can be invoked.
5. **First controlled runtime proof:** after a separately authorized implementation phase, use one PAL mission, one local synthetic bounded pose, and one scene-owned cosmetic actor. Test appearance, hide, reconnect, pause/resume, repeated scene transition, and rollback. Only after these pass expand to three reserved actors; M4/network positions come later.

No code, game memory, executable, or Dolphin configuration was modified for this specification.

## 7. Dependencies

- Exact PAL RMGP01 Rev 0 DOL and its bound Petari symbol map; stock Dolphin 2609a for any later live test.
- The existing read-only MEM1 and callback observers for identity/capacity metadata; their aggregate counts cannot attribute slots to Penguins or remote objects.
- Petari's matching `NameObjExecuteHolder.cpp`, `LiveActor.cpp`, `LiveActorGroup.cpp`, `ClippingActorHolder.cpp`, `NameObjListExecutor.cpp`, and model/resource-holder source as corroboration.
- A permitted and verified cosmetic model/archive selection for a future implementation; no asset is selected by this research.

## 8. Risks and unresolved assumptions

- Scene initialization order is pinned for the PAL GameScene path. Do not generalize it to menus, demo scenes, or other Scene subclasses without tracing their virtual `init` implementations.
- Reserved category capacity can represent disconnected campaign actors. Unused-at-this-frame callback entries are not spare reservation capacity.
- Registration state and callback work are deferred; scene teardown can invalidate an object while pending work or callback references remain.
- Petari source mirrors the target but does not prove the runtime behavior of a newly introduced actor class, ABI/vtable, archive state, gravity frame, render resource, or hidden/appearance path.
- `LiveActor::initModelManagerWithAnm` may create an `ActorPadAndCameraCtrl`; a cosmetic type must establish a model setup route with no camera or controller ownership.
- Heap equality for normal placement is not evidence that an actor initialized by a future bridge callback uses the Scene heap, nor that every transitive graphics allocation is scene-owned.

## 9. Acceptance criteria and testing strategy

This research phase is accepted when the DOL call sequence, per-actor storage ledger, earliest/latest candidate windows, resource ownership evidence, risks, and staged gates are recorded with verified/static/source/unverified labels. The phase does not accept actor spawning or claim visual rendering.

Run `cargo test --workspace`, `cargo run -p multigalaxy -- validate-mario-map reference\original\main.dol target\petari-rmgp01-symbols.txt`, and `git diff --check`. These validate existing tooling and the exact PAL map; they do not test actor creation. No Dolphin runtime test is appropriate in this research-only phase.

Petari source references: [`NameObjExecuteHolder.cpp`](https://github.com/SMGCommunity/Petari/blob/master/src/Game/NameObj/NameObjExecuteHolder.cpp), [`LiveActor.cpp`](https://github.com/SMGCommunity/Petari/blob/master/src/Game/LiveActor/LiveActor.cpp), [`LiveActorGroup.cpp`](https://github.com/SMGCommunity/Petari/blob/master/src/Game/LiveActor/LiveActorGroup.cpp), [`ClippingActorHolder.cpp`](https://github.com/SMGCommunity/Petari/blob/master/src/Game/LiveActor/ClippingActorHolder.cpp), [`NameObjListExecutor.cpp`](https://github.com/SMGCommunity/Petari/blob/master/src/Game/NameObj/NameObjListExecutor.cpp), [`DrawBufferGroup.cpp`](https://github.com/SMGCommunity/Petari/blob/master/src/Game/System/DrawBufferGroup.cpp), and [`ModelManager.cpp`](https://github.com/SMGCommunity/Petari/blob/master/src/Game/LiveActor/ModelManager.cpp). Source confirms the described API/lifecycle shape; the PAL DOL and bound symbol map establish target addresses and call order.
