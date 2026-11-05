# Penguin actor and rendering path (M5 research)

Status: **static research plus user-reported read-only live position observation**. Research only: no guest-memory writes, hook, injection, actor creation, or Dolphin modification was performed. M4 networking and the existing Gecko mailbox are unchanged.

## Evidence basis

- Target: PAL RMGP01 Rev 0, `reference/original/main.dol`, SHA-256 `8200fb72b3170069bb71c8b3dc21b49e429977ce8ae5159bb95ea6a19de0fbee`; exact-hash-associated Petari symbol map SHA-1 `9BD1DAA520BD1F573B6A4B82E03336E9C51B511F`.
- DOL text section 1 maps file offset `0x25E0` to guest `0x800070A0`; all file offsets below are calculated from this section header and were checked against the bytes in the user-supplied PAL DOL.
- Source semantics are from Petari's [`NameObjFactory.cpp`](https://github.com/SMGCommunity/Petari/blob/master/src/Game/NameObj/NameObjFactory.cpp), [`PlacementInfoOrdered.cpp`](https://github.com/SMGCommunity/Petari/blob/master/src/Game/Scene/PlacementInfoOrdered.cpp), [`StageDataHolder.cpp`](https://github.com/SMGCommunity/Petari/blob/master/src/Game/Scene/StageDataHolder.cpp), [`LiveActor.cpp`](https://github.com/SMGCommunity/Petari/blob/master/src/Game/LiveActor/LiveActor.cpp), [`LiveActorGroup.cpp`](https://github.com/SMGCommunity/Petari/blob/master/src/Game/LiveActor/LiveActorGroup.cpp), [`AllLiveActorGroup.cpp`](https://github.com/SMGCommunity/Petari/blob/master/src/Game/LiveActor/AllLiveActorGroup.cpp), and the matching PAL symbol map. Petari describes the matching game build; source-level behavior does not by itself establish a safe runtime extension point.
- **User-observed live evidence:** in Beach Bowl / Sunken Treasure, Dolphin 2609a with PAL RMGP01 Rev 0 reported 16 exact Penguin vptr matches and 0 invalid entries with stable scene/group. Candidate index 1046 at `0x813EECB8` changed from `(-272.663,-663.709,453.593)` to `(-162.820,-663.342,73.944)`. No writes were performed. See [Penguin live investigation](penguin-live-investigation.md). This is not an independent capture by the tool.

## PAL DOL call-path findings

| Operation | PAL symbol/address | DOL file offset | Static evidence |
|---|---:|---:|---|
| Factory creator specialization | `createNameObj<7Penguin>` `0x80263FD0` | `0x25F510` | `li r3,0x168`; calls `operator new`; null-checks; calls Penguin ctor at `0x802790E0`; returns pointer. This specialization allocates and constructs only; it does not run `init` or register scene execution/draw scheduling. |
| Penguin constructor | `__ct__7PenguinFPCc` `0x802790E0` | `0x274620` | Calls its base constructor at `0x8029A4B0`, then stores the exact vptr `0x8059C884` at object `+0`. |
| LiveActor base constructor | `__ct__9LiveActorFPCc` `0x80165204` | `0x160744` | At `0x801652B8` calls `MR::getAllLiveActorGroup` (`0x8015CDB8`); at `0x801652C0` calls `LiveActorGroup::registerActor` (`0x8016610C`); then at `0x801652CC` calls clipping registration (`0x8015F420`). Source initializes transform and related fields before these calls. |
| Penguin init | `init__7PenguinFRC12JMapInfoIter` `0x8027912C` | `0x27466C` | At `0x80279170` calls `LiveActor::initModelManagerWithAnm` (`0x801659C4`); at `0x80279178` calls `MR::connectToSceneNpc` (`0x803EF020`); then initializes light, hit sensors, NPC sensor, effects, sound and other NPC behavior. |
| Model setup | `initModelManagerWithAnm` `0x801659C4` | `0x160F04` | Allocates a `ModelManager` of size `0x28`, constructs it, stores it at actor `+0x48`, calls `ModelManager::init` (`0x80169AD0`) with the supplied model/animation arguments, sets scale/base matrix and calculates the J3D model; then attempts animation keeper and pad/camera helper setup. Petari source confirms these semantics. |
| NPC scene connection | `connectToSceneNpc` `0x803EF020` | `0x3EA560` | Supplies category arguments `0x28, 0x06, 0x10, -1` and branches to `registerNameObjToExecuteHolder` at `0x8026266C`. That routine resolves scene object ID `0x58`, obtains the execute holder, and calls its registration path (`0x80262140`). |
| Execute scheduling | `NameObjExecuteInfo::connectToScene` `0x80261F48` | `0x25D488` | Uses flags in execute info to add movement (`0x8026C07C`) and calc-animation (`0x8026C084`) callbacks. Holder `connectToScene` is `0x802621F8`. |
| Draw scheduling | `NameObjExecuteInfo::connectToDraw` `0x80262040` | `0x25D580` | Uses draw flags to add draw (`0x8026C094`) and draw-buffer (`0x8026C08C`) callbacks. Holder `connectToDraw` is `0x8026223C`. |
| Scene/draw disconnection | `disconnectToScene` `0x80261FC4`; `disconnectToDraw` `0x802620C0` | `0x25D504`; `0x25D600` | Read-only DOL trace shows the inverse callback removal calls for movement/calc-animation and draw/draw-buffer slots. |
| Temporary visibility scheduling | `connectToSceneTemporarily` `0x802626FC`; `disconnectToSceneTemporarily` `0x80262738`; draw pair `0x80262774`/`0x802627B0` | `0x25DC3C`; `0x25DC78`; `0x25DCB4`; `0x25DCF0` | Called by `LiveActor::makeActorAppeared` and `makeActorDead`. These require valid execute-info records; they do not create actor-system registrations. |
| Execute-info holder access | `NameObjExecuteHolder::connectToScene` `0x802621F8`; `connectToDraw` `0x8026223C` | `0x25D738`; `0x25D77C` | Resolves the NameObj's indexed execute-info record, connects the record and marks its scene/draw state. The holder owns records/storage, not the actor object. |
| Scheduler storage teardown | `NameObjExecuteHolder::~` `0x80262A58`; `NameObjListExecutor::~` `0x8026BE60` | `0x25DF98`; `0x2673A0` | PAL code releases holder/list/callback storage. The traced scene teardown does not call each actor destructor merely because the scheduler is destroyed. |
| Model view/render entry | `LiveActor::calcViewAndEntry` `0x8016563C` | `0x160B7C` | Checks actor/model state and tail-branches at `0x80165658` to `ModelManager::calcView` (`0x80169060`). This is one stage of rendering; a valid model, scene draw scheduling and render resources are also required. |
| Penguin destructor | `__dt__7PenguinFv` `0x80279B0C` | `0x27504C` | Calls `NPCActor` destructor (`0x8026C190`) at `0x80279B34`, then conditionally calls `operator delete` (`0x80409B5C`) based on the destructor flag. This does not establish that scene execute/draw lists or the live-actor group are safely unregistered before destruction. |

## Factory caller and scene lifetime

The prior note stopped at the creator specialization. The caller path is now traced in PAL DOL:

| Operation | PAL symbol/address | DOL file offset | Result |
|---|---:|---:|---|
| `PlacementInfoOrdered::requestFileLoad` | `0x80341FD4` | `0x33D514` | Iterates placement records and calls `NameObjFactory::requestMountObjectArchives` at `0x80342044` before actor construction. This is the normal archive prefetch path. |
| `PlacementInfoOrdered::initPlacement` | `0x8034207C` | `0x33D5BC` | Resolves each placement's creator at `0x803420BC` (`NameObjFactory::getCreator`, `0x8026ACE8`); establishes the placement zone, invokes the returned function pointer at `0x80342100` with the object name, then invokes `AllLiveActorGroup` actor-system initialization at `0x8034210C`, calls the returned object's virtual `init` slot (`vtable +0x0C`) at `0x80342124`, and updates actor-system info again at `0x8034212C`. |
| Scene destruction | `GameSystemSceneController::destroyScene` `0x8039CF80` | `0x3984C0` | If active, calls the scene's virtual destructor via the scene vtable's destructor entry, clears the active scene pointer, clears a NameObj holder array, runs teardown helpers, then calls `HeapMemoryWatcher::destroySceneHeap` at `0x8039D018`. |
| Base Scene destructor | `Scene::~Scene` `0x8034377C` | `0x33ECBC` | Replaces the vptr at `Scene +0x10` with the SceneObjHolder vptr, frees that holder allocation when non-null, invokes the object at `Scene +0x08` through its virtual destructor, then conditionally frees the Scene allocation. This routine does not visibly iterate AllLiveActorGroup and invoke each actor destructor. |
| Scene heap cleanup | `HeapMemoryWatcher::destroySceneHeap` `0x8039EC9C` | `0x39A1DC` | Calls the heap-destroy helper for handles at watcher offsets `+0x14` and `+0x18`. This establishes scene-heap teardown after the Scene destructor, but does not prove a particular actor was allocated from either heap. |

The table in Petari's `NameObjFactory.cpp` maps the stage object name `Penguin` to `createNameObj<Penguin>` and archive name `Penguin`. Its NPC archive-list callback is implemented by `NPCActor::makeArchiveList` at PAL `0x8029A620`; it gathers requested archives from NPC data. This is consistent with the prefetch-before-placement order above. The placement code itself calls the creator function and virtual `init`; it does not save the returned pointer in a distinct owner field in the instructions traced here. The `LiveActor` base constructor appends that pointer to the current scene's AllLiveActorGroup and registers it with ClippingDirector. Those are registries, not proof of owning references.

### Ownership conclusion and its limit

The normal placement allocation path is now traced through scene heap selection. `initializeScene` creates the scene heaps and selects the scene heap before `GameScene::init` starts placement. Penguin's creator uses ordinary `operator new(0x168)`, which passes a null heap and dispatches through `JKRHeap::sCurrentHeap`; the traced placement call chain does not switch heaps before construction. Model/resource-holder setup also uses the current heap when no explicit heap is passed. Thus normal placement allocations follow the selected scene heap in this static call path. This is not a live measurement and must not be generalized to a runtime hook running in another context. Scene teardown cleans resource holders associated with the Scene heap and bulk destroys the scene heaps; the traced path does not call each placed Penguin's destructor. See [PAL placement heap and lifetime trace](pal-placement-heap-lifetime.md) for addresses, offsets, limits, prototype gates, and rollback.

This distinction matters for resources. `requestMountObjectArchives` runs for placed actors before `initPlacement`; a runtime-created actor outside this pass cannot assume its archive is mounted. `ModelManager::init` may retain model/archive-backed references. The scene heap cleanup establishes a broad reclamation boundary, not the safe order for releasing a newly allocated model manager, actor model, animation keeper, archive handle, or scheduler record.

## What registration and destruction mean

Petari's `LiveActor` constructor calls `AllLiveActorGroup::registerActor` through `LiveActorGroup`; that group appends the pointer to the scene's actor array. The base constructor also registers with `ClippingDirector`. These registrations happen during construction, before Penguin `init`. A newly constructed LiveActor therefore changes scene-global bookkeeping even before it is visible.

`LiveActor::makeActorAppeared` (called by `appear`) validates applicable sensors/collision state and connects the actor temporarily to execution and draw scheduling. `makeActorDead` (called by `kill`) clears or invalidates gameplay helpers and disconnects temporary execution/draw scheduling. **Kill/hide is not destruction**: the object remains in the live actor group and has already registered with the clipping director. The Penguin destructor delegates to NPC/base destruction and may free storage. `Scene::~Scene` frees the SceneObjHolder allocation and tears down its executor; `GameSystemSceneController::destroyScene` releases the two scene heaps afterward. The traced Scene destructor does not walk AllLiveActorGroup and invoke each actor destructor. Therefore the normal scene boundary tears down scheduling structures and releases scene heaps, but does not establish safe individual deletion of a remote actor during a scene or prove that a remote actor allocation belongs to those heaps.

## Why an existing Penguin is not the remote actor

The user-observed position sample proves that one exact-vptr candidate's `mPosition` changes. It does not make that NPC suitable for remote control. `Penguin::init` installs NPC-specific model, sensors, effects, sound, gravity/pose and state behavior; it can talk, move, collide, and participate in game logic. Reusing an existing entry would commandeer a campaign NPC and would not isolate network input from its AI. Replacing its vptr, position, or callbacks would also violate the requirement not to hijack existing actors.

Constructing another `Penguin` through its stock factory would still create a Penguin with full NPC initialization and scene side effects. Calling only its constructor or `initModelManagerWithAnm` would leave an incompletely initialized actor. Neither is an acceptable remote-avatar prototype.

## Smallest bounded future prototype (not authorized or implemented here)

The research points toward a **new, separately owned cosmetic actor type**, not reuse of a campaign actor. A future phase could test one local synthetic position in a controlled PAL scene, but only after resolving these gates:

1. Complete the ordinary actor owner trace: prove which heap `operator new` uses during placement creation, whether placement objects are individually destructed, and how their model/archive references are released. Use `PlacementInfoOrdered` and scene/heap source plus call-site disassembly; do not infer this from scene teardown alone.
2. Resolve capacity/system-info effects: the LiveActor base constructor appends to AllLiveActorGroup and ClippingDirector before derived construction finishes. Prove capacity handling and how `initLiveActorSystemInfo` prepares clipping state for a newly introduced actor outside normal placement loading.
3. Identify a simple model resource already mounted in the chosen test scene, its exact model/animation names, and resource lifetime. Runtime actor creation skips `requestMountObjectArchives`; verify archive residency independently. Do not copy a model-manager pointer from Mario or a Penguin.
4. Define a distinct remote visual actor with a verified PAL vtable/callback set and scene-lifetime owner. Use normal execute/draw registration once, with no sensors, binder, effects, sound, gameplay gravity, camera/controller helper, or campaign behavior. It must never enter MarioHolder, claim input/camera, or alter existing NPC entries.
5. Make the actor scene-scoped and at most one per remote slot. On disconnect/stale data, call the proven hide/dead path and retain the allocation until normal scene teardown; do not individually free it mid-scene. Before a transition destroys the scene, invalidate host-side handles by scene epoch and ensure no callback can touch the old actor. Recreate only after the new scene and resources validate.
6. Start from one local synthetic snapshot; validate finite/bounded coordinates and scene epoch; hide on stale input; prove scene-transition teardown/recreation and no duplicate registration. Only then connect M4 state.

The smallest safe prototype, once the listed gates are cleared, is one local-only, noninteractive, scene-scoped visual actor at a synthetic fixed position in a single PAL test scene. It should be a custom cosmetic type, never a full Penguin or an existing actor. Disconnect hides it but does not free it mid-scene; a scene transition invalidates all host handles and relies on normal scene teardown only after allocation/resource ownership is proven. The next research-only experiment is a read-only correlation of `sCurrentHeap` and the active Scene heap handles during gameplay and transitions, alongside one placed actor/group count. No runtime write is needed.

The opt-in read-only `observe-heaps` implementation and its exact PAL global/field evidence are documented in [PAL placement heap and lifetime](pal-placement-heap-lifetime.md). It reports a gameplay-time sample and compares current heap with Scene Napa; it makes no constructor-time claim.

## Scheduler and model-resource ownership assessment

### User-reported mission-transition sample

PID `86776` was sampled by the user on PAL RMGP01 Rev 0. In Beach Bowl, the live group was `0x80FA07D8` with 1164 registered actors and 16 Penguin-vptr matches. After entering the destination, the group was `0x80FA074C` with 166 registered actors and no Penguin matches. The scene address remained `0x80F8EF70`; the Scene Napa/current-heap value remained `0x80F8EEF0`.

This is evidence that the observed group contents and group address differed between the two samples. It does **not** prove that the Scene heap was destroyed, that the same scene instance persisted, or that an allocation used that heap during construction. The observer compares addresses and has no scene-generation counter; address reuse is possible. `sCurrentHeap` was sampled during gameplay, not at an actor constructor/allocation call. No constructor-time heap identity or heap-destruction event is claimed. This is user-reported evidence, not a capture independently made in this documentation pass.

### Execute-info creation and scheduler registration (static PAL trace)

The ordinary registration route is scene-owned and record-based. It is not a general-purpose “add actor” API:

| Step | PAL symbol/address | DOL file offset | Verified behavior |
|---|---:|---:|---|
| Create the scene object registry | `Scene::initSceneObjHolder` `0x80343890` | `0x33EDD0` | Initializes the per-scene `SceneObjHolder`. |
| Lazily create scheduler holder | `SceneObjHolder::create` `0x80344A14` | `0x33FF54` | Constructs a requested SceneObj once, invokes its init path, then stores it in its slot. |
| Scheduler holder slot | SceneObj id `0x58` | holder base `+0x160` | `SceneObj_NameObjExecuteHolder`; Petari constructs `NameObjExecuteHolder(4096)` for this slot. |
| Allocate execute-info array | `NameObjExecuteHolder::NameObjExecuteHolder` `0x802629BC` | `0x25DEFC` | Sets max `4096` at holder `+0x10`, count `0` at `+0x14`, and allocates 12-byte records through the array pointer at `+0x0C`. |
| Append record | `NameObjExecuteHolder::registerActor` `0x80262140` | `0x25D680` | Uses current count to select `array + count*12`, calls `NameObjExecuteInfo::setConnectInfo`, stores the index in `NameObj::mExecutorIdx` at `+0x0A`, then increments count. **The PAL routine has no capacity check before append.** |
| Fill a record | `NameObjExecuteInfo::setConnectInfo` `0x80261C50` | `0x25D190` | Record: object pointer `+0x00`; connection/status bytes `+0x04/+0x05`; movement `+0x06`; calcAnim `+0x07`; draw `+0x08`; draw-buffer `+0x09`; draw-buffer result `+0x0A`. |
| Enter the common registration route | `registerNameObjToExecuteHolder` `0x8026266C` | `0x25DBAC` | Resolves SceneObj id `0x58` and calls holder registration. |
| Register execution/draw categories | `NameObjExecuteInfo::connectToScene` `0x80261F48`; `connectToDraw` `0x80262040` | `0x25D488`; `0x25D580` | Adds selected movement/calcAnim callbacks and draw/draw-buffer callbacks to separate `NameObjListExecutor` lists. Matching disconnect routines are `0x80261FC4` and `0x802620C0`. |

The category tuple passed by the existing `connectToSceneNpc` helper at `0x803EF020` is `(movement=0x28, calcAnim=0x06, drawBuffer=0x10, draw=-1)`. Its branch reaches `registerNameObjToExecuteHolder`. These values describe the existing NPC route only. They are **not** authorized defaults for a cosmetic proxy: category indices select scheduler lists and must be matched to the intended methods and side effects. `LiveActor::makeActorAppeared`/`makeActorDead` use temporary connect/disconnect operations on an already registered record; they do not create or remove the record, its AllLiveActorGroup entry, or clipping registration.

Petari source corroborates the record layout and callback-list behavior in [`NameObjExecuteHolder.hpp`](https://github.com/SMGCommunity/Petari/blob/master/include/Game/NameObj/NameObjExecuteHolder.hpp), [`NameObjExecuteHolder.cpp`](https://github.com/SMGCommunity/Petari/blob/master/src/Game/NameObj/NameObjExecuteHolder.cpp), and [`SceneObjHolder.cpp`](https://github.com/SMGCommunity/Petari/blob/master/src/Game/Scene/SceneObjHolder.cpp). The PAL disassembly independently establishes the addresses, record stride, holder fields, and unchecked append. Source semantics do not prove safe dynamic registration during arbitrary runtime phases.

Scene transition destroys the scene object holder and executor-owned scheduler tables before the scene heaps are destroyed. The execute-info array is owned by that scene scheduler; it is not an actor owner. A `NameObj`'s `mExecutorIdx` is meaningful only with the matching live holder/scene. Pending/deferred connection requirements and callback lists make mid-teardown registration or disconnection hazardous. Host-side actor/scheduler handles must be discarded before any possible scene teardown; numeric address equality cannot be used as a generation token.

### Resource/model prerequisites

The normal placement path calls `requestMountObjectArchives` before constructing and initializing actors (`PlacementInfoOrdered::requestFileLoad`, `0x80341FD4`; `initPlacement`, `0x8034207C`). A runtime-created remote visual would bypass this prefetch. `ModelManager::init` (`0x80169AD0`, file offset `0x165010`) creates one resource holder for its model archive and, when separate, another for animation. `ResourceHolderManager::createAndAddInner` (`0x803A6DE4`) obtains the current heap when no explicit heap is supplied; teardown removes holders associated with the Scene heap before bulk heap destruction (`removeIfIsEqualHeap`, `0x803A6BCC`). Petari [`ModelManager.cpp`](https://github.com/SMGCommunity/Petari/blob/master/src/Game/LiveActor/ModelManager.cpp) and [`ResourceHolderManager.cpp`](https://github.com/SMGCommunity/Petari/blob/master/src/Game/Util/ResourceHolderManager.cpp) describe this path.

Still unresolved: an archive known to be mounted in a selected test scene; complete transitive ownership of J3D model/animation/display-list/GX allocations; the scheduler category and minimal callback implementation for a non-gameplay visual; and whether dynamic registration during the intended phase is safe. The NPC category tuple must not be copied by assumption.

### Smallest future registration proof and stop conditions

Before any runtime write or spawn, a separately authorized phase must perform read-only checks that:

1. Re-resolve the active scene and SceneObjHolder chain and validate the execute-holder vptr, array pointer, record stride, count, and capacity for that exact scene. `registerActor` itself does not check `count < 4096`; stop if capacity cannot be proven with headroom. Never call it based only on the advertised maximum.
2. Confirm a candidate category tuple from exact PAL callbacks and prove each list’s capacity/registration behavior. Require a custom update path that only consumes a bounded snapshot and sets a transform; it must not invoke NPC AI, gameplay gravity, sensors, collision, camera, sound, or progression.
3. Confirm an already mounted model/archive and its ownership/cleanup path. Do not copy another actor’s model-manager pointer.
4. Prove the registration phase, deferred connect/disconnect processing, draw-buffer setup, and teardown ordering for a scene-scoped object. On disconnect, hide only after its side effects are known; retain storage until proven scene cleanup. Invalidate all host handles on scene transition.
5. Use one local synthetic bounded pose in one fixed mission first. Stop on scene/group/holder identity change, stale record index, pending scheduler state, count near capacity, missing archive, invalid heap context, callback after teardown, or any change to an existing actor/Mario/camera/input/gameplay state.

The opt-in `observe-scheduler` command now performs that targeted read-only sample. It derives the full scene/group chain for each pass, validates the holder vptr and metadata, checks the entire advertised record span against canonical MEM1 using checked arithmetic, and compares two samples for consistency. It reports the live count and remaining capacity but reads no record or callback list. Sample once in a playable scene and once after a mission transition; do not poll continuously. This validates metadata only, not safe registration or allocator ownership. Until callback/resource prerequisites are proven, dynamic registration and actor creation remain blocked.

Windows PowerShell, with official stock Dolphin 2609a already running in the isolated profile at a playable PAL scene:

```powershell
$DolphinPid = 12345 # replace with the isolated Dolphin.exe PID
cargo run -p multigalaxy -- observe-scheduler .\development.local.toml $DolphinPid
```

Expected prefix: `scheduler_observer=validated_read_only`, followed by scene, SceneObjHolder, AllLiveActorGroup, execute-holder and array addresses, `capacity=4096`, live `count`, `remaining`, five pending-operation flags, and `callback_lists=not_read`. A null SceneObj slot means the holder is not initialized in that scene; invalid vptrs, capacity/count, pointers, MEM1 span, or inconsistent double samples fail closed. This is a new command; no live result is claimed until the user runs it.

### Exact holder and registration evidence

The implementation validates exact PAL RMGP01 Rev 0 symbols and DOL bytes before reading memory, then compares the same scheduler signatures against live MEM1 before and after the sample. The DOL text mapping used by the repository is section 1 file offset `0x25E0` -> guest `0x800070A0`; the full DOL hash and symbol-file hash are enforced by `verify_pal_inputs`.

| Evidence | PAL address | DOL file offset | Result |
|---|---:|---:|---|
| `SceneObjHolder::create` | `0x80344A14` | `0x33FF54` | Exact function bytes validated; it returns an existing slot object or constructs/initializes it and stores it in the slot. |
| `SceneObjHolder::getObj` | `0x80344A70` | `0x33FFB0` | Exact index-times-four accessor validated. |
| `SceneObjHolder::newEachObj` | `0x80344B30` | `0x340070` | Exact symbol validated; Petari's `SceneObj_NameObjExecuteHolder` enum value is `0x58`, hence pointer slot `0x58*4 = +0x160`. |
| Factory case for SceneObj `0x58` | `0x80345410` | `0x340950` | Exact 24-byte sequence validated: allocates a `0x20`-byte object, loads constructor argument `0x1000`, then branches to the verified holder constructor at `0x802629BC`. |
| `NameObjExecuteHolder` constructor | `0x802629BC` | `0x25DEFC` | Full 0x9C bytes validated. Writes vptr `0x80593178`, array pointer `+0x0C`, max `4096` at `+0x10`, zero count at `+0x14`, and zeroed flags `+0x18..+0x1C`. |
| `NameObjExecuteHolder::registerActor` | `0x80262140` | `0x25D680` | Full 0x5C bytes validated. Uses count at `+0x14`, computes `count*12`, stores the NameObj executor index, increments count. No capacity comparison occurs before indexing/appending. |
| `NameObjExecuteInfo::setConnectInfo` | `0x80261C50` | `0x25D190` | Full 0xD4 bytes and symbol validated. Static record size is 12 bytes; fields are `NameObj* +0`, status `+4/+5`, movement `+6`, calcAnim `+7`, draw `+8`, draw-buffer `+9`, returned buffer index `+0xA`. |

The five holder bytes at `+0x18..+0x1C` are the source-documented deferred connect/disconnect flags for movement and draw, including delayed draw disconnection. The observer reports these bytes only after checking each is 0 or 1. They are **not callback-list counts**. The callback lists belong to `NameObjListExecutor`; this observer does not read its pointer, arrays, counters, or entries because their bounds and ownership have not been separately validated. The complete execute-info storage *address span* is checked as `array + 4096*12` within canonical MEM1. That does not recover an allocator boundary or prove that the memory was allocated for the array; it is only a safe mapped-address-range check. It does not imply any available slot is safe to use: the validated PAL `registerActor` path appends without checking the maximum.

The exact PAL `NameObjExecuteInfo` routines register movement and calc-animation callbacks separately from draw and draw-buffer callbacks according to fields in an existing execute-info record. Their matching disconnect routines remove those callback registrations. `NameObjExecuteHolder` resolves a record for a `NameObj` before connecting it. Petari's [`NameObjExecuteHolder.cpp`](https://github.com/SMGCommunity/Petari/blob/master/src/Game/NameObj/NameObjExecuteHolder.cpp) and [`LiveActor.cpp`](https://github.com/SMGCommunity/Petari/blob/master/src/Game/LiveActor/LiveActor.cpp) corroborate that lifecycle.

Static facts and deductions:

- `LiveActor::makeActorAppeared` calls temporary scene and draw connection helpers; `makeActorDead` calls the paired disconnect helpers and additional actor cleanup. This toggles an already initialized actor's scheduler work. It does not create a valid execute-info entry or reverse its AllLiveActorGroup and ClippingDirector registrations.
- Normal placement calls `MR::initLiveActorSystemInfo` around creator/init. A runtime-created object needs an equivalent verified path for execute-info and actor-system metadata. The fixed holder's capacity, free-slot selection, duplicate registration handling, and category semantics are not resolved for dynamic actors.
- `ModelManager::init` at `0x80169AD0` calls `MR::createAndAddResourceHolder` at `0x80169B14` and again at `0x80169B48` when model and animation archives are separate. `ResourceHolderManager::createAndAddInner` (`0x803A6DE4`) obtains `MR::getCurrentHeap` when no explicit heap is passed; `removeIfIsEqualHeap` (`0x803A6BCC`) later removes holders by their recorded heap before the scene heap is destroyed. Petari's [`ModelManager.cpp`](https://github.com/SMGCommunity/Petari/blob/master/src/Game/LiveActor/ModelManager.cpp) and [`ResourceHolderManager.cpp`](https://github.com/SMGCommunity/Petari/blob/master/src/Game/Util/ResourceHolderManager.cpp) corroborate these responsibilities.
- This establishes resource-holder association and cleanup, but not every transitive J3D model, animation controller, display-list, or GX allocation. Nor does it establish that initialization from a future runtime callback runs with the desired current heap. The user-reported `observe-heaps` value is a gameplay-time snapshot only.
- Scene teardown destroys scheduler-owned tables before scene heaps. That does not prove custom callbacks are disconnected before storage release or that a callback target cannot run during teardown. A scene transition must invalidate the host-side actor handle before the old scene's executor/heap cleanup.

### First visible prototype gates

The smallest eventual visual test is one local-only, noninteractive, purpose-built cosmetic actor at a bounded synthetic position in one PAL test mission. It must not reuse a Penguin, Mario, or another campaign actor. Before an implementation phase, prove: (1) PAL-correct construction and actor-system initialization; (2) safe execute-info slot allocation/capacity and the exact category that updates/draws without NPC/gameplay behavior; (3) one exact model/archive already mounted in the selected scene; (4) model/animation allocations and resource-holder cleanup are ordered before Scene heap release; (5) appearing/dead connections cannot leave callbacks or registry references; and (6) a scene epoch/rollback path prevents stale host handles and duplicate registrations.

Stop if any ABI/signature, capacity, archive residency, resource ownership, or teardown callback ordering is uncertain, or if the candidate would register gameplay sensors/collision, alter Mario/camera/input, or claim an existing actor. Current evidence supports a design but does **not** establish that runtime actor spawning is safe. No actor was created or rendered.

## Remaining blockers

- Normal placement's selected current heap and bulk scene-heap teardown are statically traced. Live heap correlation and ownership for every transitive model/archive allocation remain unverified; scene teardown does not show per-actor destructor calls.
- `connectToSceneNpc`'s NPC scheduler category is not established as suitable for a remote cosmetic actor.
- A standalone actor must be constructed with a correct vtable; no separate remote actor implementation exists in the game.
- Model/archive mount lifetime, animation choice, rendering resource setup, gravity frame/orientation, and safe temporary hiding are not established for a new actor.
- Scene teardown order is statically traced through scene executor/holder destruction, resource-holder cleanup keyed by heap, then scene-heap destruction. A custom actor's callback registration/unregistration order and its actual allocation/resource ownership remain unproven.
- The live candidate's world-space position is observed to move, but candidate-to-visible-Penguin identity and pause/transition persistence are not verified.
- No remote actor rendering, M4-driven in-game actor, multiplayer rendering, or runtime performance result is claimed.
