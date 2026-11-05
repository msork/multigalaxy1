# Penguin actor and rendering path (M5 research)

Status: **static research plus user-reported read-only live position observation**. Research only: no guest-memory writes, hook, injection, actor creation, or Dolphin modification was performed. M4 networking and the existing Gecko mailbox are unchanged.

## Evidence basis

- Target: PAL RMGP01 Rev 0, `reference/original/main.dol`, SHA-256 `8200fb72b3170069bb71c8b3dc21b49e429977ce8ae5159bb95ea6a19de0fbee`; exact-hash-associated Petari symbol map SHA-1 `9BD1DAA520BD1F573B6A4B82E03336E9C51B511F`.
- DOL text section 1 maps file offset `0x25E0` to guest `0x800070A0`; all file offsets below are calculated from this section header and were checked against the bytes in the user-supplied PAL DOL.
- Source semantics are from Petari's [`NameObjFactory.cpp`](https://github.com/SMGCommunity/Petari/blob/master/src/Game/NameObj/NameObjFactory.cpp), [`LiveActor.cpp`](https://github.com/SMGCommunity/Petari/blob/master/src/Game/LiveActor/LiveActor.cpp), [`LiveActorGroup.cpp`](https://github.com/SMGCommunity/Petari/blob/master/src/Game/LiveActor/LiveActorGroup.cpp), [`AllLiveActorGroup.cpp`](https://github.com/SMGCommunity/Petari/blob/master/src/Game/LiveActor/AllLiveActorGroup.cpp), and the matching PAL symbol map. Petari describes the matching game build; source-level behavior does not by itself establish a safe runtime extension point.
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

The factory table in Petari maps object name `Penguin` to `createNameObj<Penguin>` and archive name `Penguin`. This indicates the ordinary stage-object pipeline is responsible for selecting the factory and arranging the archive. The exact stage-loader caller sequence from factory return through `init`, appearance, and scene teardown has not yet been fully traced in this PAL binary. The factory helper alone is not a general-purpose runtime spawn API.

## What registration and destruction mean

Petari's `LiveActor` constructor calls `AllLiveActorGroup::registerActor` through `LiveActorGroup`; that group appends the pointer to the scene's actor array. The base constructor also registers with `ClippingDirector`. These registrations happen during construction, before Penguin `init`. A newly constructed LiveActor therefore changes scene-global bookkeeping even before it is visible.

`LiveActor::makeActorAppeared` (called by `appear`) validates applicable sensors/collision state and connects the actor temporarily to execution and draw scheduling. `makeActorDead` (called by `kill`) clears or invalidates gameplay helpers and disconnects temporary execution/draw scheduling. **Kill/hide is not destruction**: the object remains in the live actor group. The Penguin destructor delegates to NPC/base destruction and may free storage, but no verified evidence here proves safe removal from all group, clipping, execute, draw, model-resource, and archive ownership structures. A scene transition likely tears down scene-owned structures as a unit; exact ordering and heap ownership remain unresolved for a separately created object.

## Why an existing Penguin is not the remote actor

The user-observed position sample proves that one exact-vptr candidate's `mPosition` changes. It does not make that NPC suitable for remote control. `Penguin::init` installs NPC-specific model, sensors, effects, sound, gravity/pose and state behavior; it can talk, move, collide, and participate in game logic. Reusing an existing entry would commandeer a campaign NPC and would not isolate network input from its AI. Replacing its vptr, position, or callbacks would also violate the requirement not to hijack existing actors.

Constructing another `Penguin` through its stock factory would still create a Penguin with full NPC initialization and scene side effects. Calling only its constructor or `initModelManagerWithAnm` would leave an incompletely initialized actor. Neither is an acceptable remote-avatar prototype.

## Smallest bounded future prototype (not authorized or implemented here)

The research points toward a **new, separately owned cosmetic actor type**, not reuse of a campaign actor. A future phase could test one local synthetic position in a controlled PAL scene, but only after resolving these gates:

1. Trace the stage-loader/factory caller and scene ownership from creator return through `init`, appearance, and destruction using matching symbols and disassembly.
2. Prove the exact game allocator/heap expected by scene actors; the base constructor's AllLiveActorGroup and clipping registrations; capacity behavior; and cleanup ordering for group, clipping, execute, draw, model, and archive references.
3. Identify a simple model resource that is already mounted in the chosen test scene, its exact model/animation names, and resource lifetime. Do not copy a model-manager pointer from Mario or a Penguin. Prefer a minimal model setup with no sensors, binder, effects, sound, gravity, camera/controller helpers, or campaign behavior.
4. Define a distinct remote visual actor with a verified PAL vtable/callback set and scene-lifetime owner. Add it through the engine's normal scene update/draw registration once, and destroy it only after scheduler removal is proven. It must never enter MarioHolder, claim input/camera, or alter existing NPC entries.
5. Start from one local synthetic snapshot; validate finite/bounded coordinates and scene epoch; hide on stale input; prove scene-transition teardown/recreation and no duplicate registration. Only then connect M4 state.

Until then, no safe guest allocator call, constructor prototype, code hook, rendering call, or actor destruction routine is approved. The smallest safe next research experiment is to use exact PAL disassembly to trace the stock stage loader's factory/init/scene ownership and destruction chain, followed by read-only live observations of one known actor across pause and a scene transition. No runtime write is needed for that experiment.

## Remaining blockers

- Stage-loader call chain and object owner for ordinary factory-created actors are not fully identified.
- `connectToSceneNpc`'s NPC scheduler category is not established as suitable for a remote cosmetic actor.
- A standalone actor must be constructed with a correct vtable; no separate remote actor implementation exists in the game.
- Model/archive mount lifetime, animation choice, rendering resource setup, gravity frame/orientation, and safe temporary hiding are not established for a new actor.
- Destruction/unregistration order and safe scene-owned allocation/deallocation are not proven.
- The live candidate's world-space position is observed to move, but candidate-to-visible-Penguin identity and pause/transition persistence are not verified.
- No remote actor rendering, M4-driven in-game actor, multiplayer rendering, or runtime performance result is claimed.
