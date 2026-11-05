# PAL placement allocation and scene lifetime

Status: **research only**, PAL RMGP01 Rev 0. No game-memory writes, hooks, injection, actor creation, Dolphin changes, or M4 changes were made. This note resolves the normal Penguin placement allocation/heap path as far as the PAL DOL and matching Petari source permit. It does not authorize a runtime spawn.

## Evidence and scope

- Target DOL: `reference/original/main.dol`, RMGP01 Rev 0, SHA-256 `8200fb72b3170069bb71c8b3dc21b49e429977ce8ae5159bb95ea6a19de0fbee`, SHA-1 `9bd1daa520bd1f573b6a4b82e03336e9c51b511f`.
- Matching symbol map: `target/petari-rmgp01-symbols.txt`, associated with SHA-1 `9BD1DAA520BD1F573B6A4B82E03336E9C51B511F`.
- The DOL text section mapping used here is file offset `0x25E0` → guest address `0x800070A0`. File offsets below are derived from the PAL DOL section table and checked against that file.
- **PAL DOL verified** means the symbol-addressed instructions/call edges were inspected in this exact DOL. **Petari source** supplies matching-build source semantics. **Deduction** is an interpretation of those facts. No new live heap pointer or actor was inspected during this research pass.

## Heap observer address/layout verification

The opt-in `observe-heaps` command uses only exact PAL Rev 0 symbols and instruction signatures. Static addresses below are in the PAL DOL/BSS; they are not host pointers.

| Field/access | PAL address | DOL offset or BSS | Evidence and interpretation |
|---|---:|---:|---|
| `SingletonHolder<HeapMemoryWatcher>::sInstance` pointer slot | `0x806A245C` | BSS | Exact symbol-map entry, size 4/alignment 4; validated to lie inside the exact PAL DOL BSS. This slot contains the emulated address of the watcher object. |
| `JKRHeap::sCurrentHeap` pointer slot | `0x806A26DC` | BSS | Exact symbol-map entry, size 4/alignment 4; validated inside PAL DOL BSS. The `JKRHeap::alloc` null-heap path loads this SDA-backed global at function `0x80409698` (file offset `0x404BB8 + 0x20`, bytes `80 6D DA 3C`). |
| `HeapMemoryWatcher::mSceneHeapNapa` handle | watcher `+0x14` | `createSceneHeapOnGameHeap` `0x8039EBFC`, file `0x39A13C`; exact 0x48-byte routine signature checked | PAL stores the first created scene heap at `+0x14`; `getSceneHeapNapa` (`0x803E7460`, file `0x3E29A0`) loads the singleton then `lwz r3,0x14(r3)`. `setCurrentHeapToSceneHeap` (`0x8039EC94`, file `0x39A1D4`) also loads `+0x14` before calling `MR::becomeCurrentHeap`. |
| `HeapMemoryWatcher::mSceneHeapGDDR3` handle | watcher `+0x18` | `createSceneHeapOnGameHeap` `0x8039EBFC`, file `0x39A13C`; `getHeapGDDR3` `0x8039EB7C`, file `0x39A0BC` | PAL stores the second created scene heap at `+0x18`; `getHeapGDDR3` compares the queried heap with `+0x18`, identifying this field. |
| Watcher constructor initialization | `0x8039EF00` | file `0x39A440` | Constructor clears fields through `+0x24`, including both scene heap slots. |
| Scene heap destruction | `0x8039EC9C` | file `0x39A1DC` | Exact 0x38-byte signature checked; passes `+0x14` and `+0x18` to heap-destroy-and-null helpers. |

The Rust verifier checks the two exact BSS symbol records, relevant function symbol records, DOL BSS containment, and exact PAL instruction signatures for the heap getter, scene heap create/set/destroy paths, and JKRHeap allocation path. It does not accept an address from user input or a runtime-discovered code location. The game-memory observer reads the watcher pointer, current heap pointer, and the two watcher fields as four-byte big-endian values from the existing bounded MEM1 mapping. It accepts aligned opaque handles only inside retail Wii MEM1 (`0x80000000..0x81800000`) or MEM2/GDDR3 (`0x90000000..0x94000000`) ranges, based on Dolphin 2609a `Source/Core/Core/HW/Memmap.h`; it **never dereferences a heap pointer**. MEM2 support here validates pointer values only; no MEM2 memory view is opened.

The current heap may legitimately differ from the Scene Napa heap when sampled outside scene-placement setup. The report exposes `current_heap_matches_scene_napa` as true, false, or unavailable; false is a valid observation, not a validation failure. A sample during ordinary gameplay establishes only which pointer was in `sCurrentHeap` at that read, not which heap was active when a Penguin constructor ran.

## Normal scene and placement path

The PAL code selects the Scene heap before the normal placement calls:

| Stage | PAL symbol/address | DOL file offset | Verified behavior |
|---|---|---:|---|
| `GameSystemSceneController::initializeScene` | `0x8039CEAC` | `0x3983EC` | Calls file-cache heap creation, `HeapMemoryWatcher::createSceneHeapOnGameHeap`, then `setCurrentHeapToSceneHeap`, before installing/initializing the scene. |
| `HeapMemoryWatcher::createSceneHeapOnGameHeap` | `0x8039EBFC` | `0x39A13C` | Creates the two scene heap handles stored at watcher offsets `+0x14` and `+0x18`. |
| `HeapMemoryWatcher::setCurrentHeapToSceneHeap` | `0x8039EC94` | `0x39A1D4` | Loads watcher `+0x14` and branches to `MR::becomeCurrentHeap` (`0x803E7490`). |
| `GameScene::init` | `0x8033E510` | `0x339A50` | Calls `SceneFunction::startActorPlacement` (`0x80344678`). |
| `SceneFunction::startActorPlacement` | `0x80344678` | `0x33FBB8` | Forwards into `SceneDataInitializer::startActorPlacement` (`0x80343A68`). |
| `StageDataHolder::initPlacement` | `0x80346A4C` | `0x341F8C` | Invokes the five placement-table initializers, including `PlacementInfoOrdered::initPlacement` (`0x8034207C`). |
| `PlacementInfoOrdered::requestFileLoad` | `0x80341FD4` | `0x33D514` | Requests/mounts object archives before construction through `NameObjFactory::requestMountObjectArchives` (call at `0x80342044`). |
| `PlacementInfoOrdered::initPlacement` | `0x8034207C` | `0x33D5BC` | Resolves creator (`NameObjFactory::getCreator`, `0x8026ACE8`), invokes it at `0x80342100`, initializes actor-system info, calls returned object's virtual `init` slot at `0x80342124`, and updates actor-system info again. |

In this direct static call chain, no current-heap switch occurs between selecting the scene heap and placement construction. Therefore the normal placement creator's ordinary `operator new` runs with the Scene heap as the selected current heap. This is a **static call-path conclusion**, not a live measurement of `sCurrentHeap` at the instant of allocation. A runtime extension must not rely on this conclusion if it runs from another callback/context or changes heaps.

## Penguin allocation and resource path

| Operation | PAL address | DOL file offset | Evidence |
|---|---:|---:|---|
| `createNameObj<7Penguin>` | `0x80263FD0` | `0x25F510` | Loads size `0x168`, calls ordinary `__nw__FUl` (`0x80409B14`), checks null, calls Penguin constructor (`0x802790E0`), returns object. |
| ordinary `__nw__FUl` | `0x80409B14` | `0x405054` | Supplies alignment `4` and null heap to `JKRHeap::alloc(size, alignment, heap)` (`0x80409678`). |
| `JKRHeap::alloc(size,align,heap)` | `0x80409678` | `0x404BB8` | When heap is null, loads `JKRHeap::sCurrentHeap` (`0x806A26DC`) and dispatches allocation through that heap. |
| `MR::createAndAddResourceHolder` | `0x803EFED0` | `0x3EB410` | Calls `ResourceHolderManager::createAndAdd` (`0x803A6A58`) with no explicit heap. |
| Resource-holder creation inner path | `0x803A6DE4` | `0x3A2324` | If no heap was supplied, obtains `MR::getCurrentHeap` at `0x803A6E40` and uses it for resource creation/ownership. |
| `LiveActor::initModelManagerWithAnm` | `0x801659C4` | `0x160F04` | Allocates `ModelManager` size `0x28` with ordinary `__nw__FUl`, stores it at actor `+0x48`, calls `ModelManager::init` (`0x80169AD0`). |
| `ModelManager::init` | `0x80169AD0` | `0x165010` | Requests resource holders for model and, when separate, animation archives; initializes model and animation objects. |

Matching Petari source corroborates the factory mapping (`Penguin` → `createNameObj<Penguin>` with archive name `Penguin`), the model/archive setup, and the fact that `LiveActor` registers with `AllLiveActorGroup` and `ClippingDirector` during base construction. These registries contain references; they are not shown to own/delete the actor. Sources: [NameObjFactory.cpp](https://github.com/SMGCommunity/Petari/blob/master/src/Game/NameObj/NameObjFactory.cpp), [LiveActor.cpp](https://github.com/SMGCommunity/Petari/blob/master/src/Game/LiveActor/LiveActor.cpp), [ModelManager.cpp](https://github.com/SMGCommunity/Petari/blob/master/src/Game/LiveActor/ModelManager.cpp), and [LiveActorGroup.cpp](https://github.com/SMGCommunity/Petari/blob/master/src/Game/LiveActor/LiveActorGroup.cpp).

**Proven for normal placement:** the Penguin object allocation uses the currently selected heap; the traced normal scene path has selected the Scene heap. `ModelManager` and resource-holder operations also consult the current heap when no explicit heap is passed. Thus the normal placement chain associates these allocations with the scene heap, subject to the call-path scope above.

**Not proven:** that every transitive model/animation/GPU allocation uses this heap; that arbitrary runtime-created actors execute in the same heap context; or that all resource references are safe to abandon without the manager cleanup path. An injected constructor call outside the placement sequence would also skip normal archive prefetch and placement actor-system setup.

## Scene teardown and individual destructors

`GameSystemSceneController::destroyScene` (`0x8039CF80`, DOL file `0x3984C0`) calls the active Scene's virtual destructor, clears the active scene reference, performs holder cleanup, then calls `HeapMemoryWatcher::destroySceneHeap` (`0x8039EC9C`, file `0x39A1DC`). Before destroying the scene heaps it gets the Scene heap (`MR::getSceneHeapNapa`, `0x803E7460`) and calls `removeResourceAndFileHolderIfIsEqualHeap` (`0x803CE104`). That wrapper calls `ResourceHolderManager::removeIfIsEqualHeap` (`0x803A6BCC`), which walks manager entries, matches their recorded heap, invokes holder destruction, frees the holder and removes the entry. `destroySceneHeap` then destroys heap handles at watcher `+0x14` and `+0x18`.

The PAL `Scene::~Scene` (`0x8034377C`, file `0x33ECBC`) frees the SceneObjHolder, invokes the scene executor's virtual destructor, and conditionally frees the Scene allocation. The traced destructor path does **not** iterate `AllLiveActorGroup` and call each actor's virtual destructor. `NameObjGroup::~NameObjGroup` releases its pointer array, not the pointed-to actors. `Penguin::~Penguin` (`0x80279B0C`) and `LiveActor::~LiveActor` (`0x80037238`) exist and can call `operator delete`, but no call from the traced bulk scene teardown to those per-actor destructors was found.

**Conclusion:** for ordinary placement actors on the traced path, scene lifetime is implemented by scheduler/holder teardown, resource-manager cleanup keyed by heap, then bulk destruction of scene heaps. Individual Penguin destructors are not shown to run as part of that scene path. This is evidence for bulk reclamation of scene-heap allocations, not evidence that any arbitrary actor added at runtime belongs to the right heap or has been fully detached from every callback/registry.

## Remote actor implications

- **Scene-scoped allocation is plausible, but not yet safe to implement.** A custom actor created while the verified Scene heap is current could use the same reclamation boundary only if its allocation and every owned resource are proven to use that heap, and its scheduler/resource registrations are scene-owned.
- **Do not spawn a full Penguin.** Its init installs NPC model, sensors, effects, sound, gravity/pose and behavior. The stock factory only constructs; placement later performs actor-system initialization and virtual `init`. Calling a subset would leave an invalid actor; calling all of it creates campaign NPC behavior and requires archive placement setup.
- **Disconnect handling should hide, not free.** `LiveActor::makeActorDead`/`kill` disconnects temporary execution/draw scheduling and keeps the object/group registration. It is safer conceptually than running a destructor while registries may still hold the pointer, but the full side effects and remote-safe applicability still need verification. Reuse one actor per slot and retain it until scene teardown; do not allocate/free per network disconnect.
- **Scene transition invalidates host references.** Increment a host-side scene epoch and discard every actor/resource pointer before the old scene is torn down. The engine's scene destruction then releases scene heaps and matching resource holders. Recreate only after the new scene, archive residency, and heap context are validated. No old pointer may be inspected or scheduled after the epoch changes.
- **Scene teardown callback ordering remains a gate.** The static path destroys the Scene executor before the heap, but the exact relationship between a custom actor's execute/draw records and executor-owned storage must be demonstrated before registering one.

## Minimal prototype design after gates clear

The smallest acceptable runtime experiment is one local-only, noninteractive cosmetic actor in one fixed PAL playable scene, positioned from a synthetic bounded snapshot. It is a new custom visual actor, never an existing Penguin or Mario. It has no sensors, binder, collision, sound, effects, gravity gameplay, camera, controller, or progression behavior. It uses a verified already-mounted model/archive and a scene-owned update/draw registration path. It is allocated from the verified Scene heap, hidden on stale/disconnect by the verified dead/hide lifecycle, retained until scene teardown, and its host-side handle is invalidated before transition cleanup. No M4 state is connected until this passes.

### Prerequisites

1. Bind every future call/hook to exact PAL bytes and non-overlapping patch ranges; verify AllStars interaction.
2. Read-only live validation that `sCurrentHeap` (`0x806A26DC`) equals the active Scene heap during the intended creation callback, and changes/clears safely at teardown. Treat the two `HeapMemoryWatcher` fields at `+0x14/+0x18` as heap handles, not actor pointers.
3. Verify dynamic actor-system-info initialization, group/clipping capacity behavior, and the exact scheduler category and teardown behavior for a custom cosmetic actor.
4. Verify one model/archive is mounted, all its manager allocations are owned by the Scene heap, and resource cleanup precedes heap release.
5. Verify the hide/dead path can remove update and draw work without harmful NPC/gameplay side effects for the custom actor.
6. Use a scene epoch and fixed single-slot actor owner; prove transition invalidation and repeated load/unload do not retain stale handles or duplicate scheduler entries.

### Stop conditions

Stop before any write or spawn if the PAL DOL/hash/symbol provenance fails; the AllStars patch overlaps the proposed hook; heap current/owner does not match; group or scheduler capacity cannot be established; archive/model ownership is ambiguous; required callbacks can execute after actor/resource teardown; the snapshot is stale, malformed, nonfinite, or from another scene epoch; or any existing Mario/NPC state changes.

### Rollback

The future experiment must be isolated to a reversible patch/profile. On failure, stop emulation, disable/remove only the experimental payload, and relaunch the untouched PAL image with the prior isolated profile state. Never attempt to free the test actor mid-scene or recover by writing arbitrary heap/registry state. No Dolphin installation, regular profile, save, or original game file is modified. This research phase made no changes to those items.

## Smallest further experiment

The read-only `observe-heaps` command now performs a bounded, double-sampled correlation of `sCurrentHeap`, both Scene heap handles, active scene address, and AllLiveActorGroup address. Run it at separate user-controlled checkpoints: after entering a playable scene, while paused/resumed, and after a scene transition. Each invocation re-resolves the entire GameSystem → controller → scene → holder → group chain and discards the result if scene/group/heap pointers change during the sample. It does not observe the constructor call itself.

PowerShell from the repository root, with the user's isolated PAL Dolphin session already running:

```powershell
$DolphinPid = 12345 # replace with the PID of the isolated Dolphin instance
cargo run -p multigalaxy -- observe-heaps .\development.local.toml $DolphinPid
```

Expected output begins `heap_observer=validated_read_only` and includes `scene`, `group`, `watcher`, `sampled_gameplay_current_heap`, `scene_heap_napa`, `scene_heap_gddr3`, and `current_matches_scene_napa`. `constructor_time_heap=not_observed` is deliberate. Any invalid pointer, wrong game, incompatible Dolphin mapping, unstable scene/group, or live PAL signature mismatch fails closed. Do not supply a heap address to `inspect-memory` based only on a prior sample, and do not interpret pointer-range membership as allocation ownership. To establish constructor-time identity, a future authorized experiment would need a verified breakpoint/read-only trace at the placement creator or allocator call; this observer cannot establish it.

## Status

The ordinary placement allocation and bulk scene-heap cleanup path is statically traced. The heap observer is implemented and has synthetic coverage only; no live heap snapshot has yet been captured by this turn. It does **not** clear the runtime actor-spawn safety blockers. A remote Penguin is not approved, actor creation/rendering is not implemented, and M4 remains unchanged.
