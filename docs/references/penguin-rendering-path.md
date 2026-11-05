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

## Remaining blockers

- Normal placement's selected current heap and bulk scene-heap teardown are statically traced. Live heap correlation and ownership for every transitive model/archive allocation remain unverified; scene teardown does not show per-actor destructor calls.
- `connectToSceneNpc`'s NPC scheduler category is not established as suitable for a remote cosmetic actor.
- A standalone actor must be constructed with a correct vtable; no separate remote actor implementation exists in the game.
- Model/archive mount lifetime, animation choice, rendering resource setup, gravity frame/orientation, and safe temporary hiding are not established for a new actor.
- Scene teardown order is statically traced through scene executor/holder destruction, resource-holder cleanup keyed by heap, then scene-heap destruction. A custom actor's callback registration/unregistration order and its actual allocation/resource ownership remain unproven.
- The live candidate's world-space position is observed to move, but candidate-to-visible-Penguin identity and pause/transition persistence are not verified.
- No remote actor rendering, M4-driven in-game actor, multiplayer rendering, or runtime performance result is claimed.
