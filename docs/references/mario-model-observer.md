# Read-only PAL Mario model observer

This is an opt-in, one-shot observer for the existing Mario model under official stock Dolphin 2609a. It reads only the validated read-only MEM1 mapping. It does not pause Dolphin, write guest memory, invoke callbacks, destroy a model, or retain pointers between invocations. The first attempted live run exposed an observer field-layout error, corrected in commit `83f6df3`. Two later user-validated read-only samples now confirm the corrected matrix-buffer/tree relationship across a Beach Bowl → Comet Observatory transition.

## PAL RMGP01 Rev 0 pointer path

All addresses below are bound to the repository's exact PAL DOL SHA-256 and exact Petari symbol-file hash. The command verifies those inputs before it reads Dolphin.

| Edge / field | Address or offset | Evidence |
|---|---:|---|
| `SingletonHolder<GameSystem>::sInstance` | `0x806A1228` | Existing exact-hash PAL map validation |
| `GameSystem::mSceneController` | `GameSystem + 0x24` | PAL accessor signature already checked by `player_probe` |
| active `Scene` | `SceneController + 0xAC` | PAL accessor signature already checked by `player_probe` |
| `Scene::mSceneObjHolder` | `Scene + 0x10` | PAL accessor signature already checked by `player_probe` |
| `SceneObj_MarioHolder` slot | `SceneObjHolder + 0x50` | PAL `MarioHolder` accessor and verified scene-object index |
| `MarioHolder::mActor` | `MarioHolder + 0x0C` | PAL `getMarioActor` load |
| `MarioActor` vptr | `0x805A399C` | `MarioActor` constructor's PAL vptr store at `0x802AE898..0x802AE89C`; exact Petari vtable symbol |
| `LiveActor::mModelManager` | `MarioActor + 0x48` | PAL `initModelManagerWithAnm` stores to `+0x48` at `0x801659FC` and loads it at `0x80165A40`; Petari field layout |
| `ModelManager::mXanimePlayer` | `ModelManager + 0x18` | `getJ3DModel` PAL code at `0x80169744` loads `+0x18`; Petari member layout |
| `XanimePlayer::mModel` | `XanimePlayer + 0x00` | PAL `getJ3DModel` loads the first word when the Xanime player exists; constructor at `0x8001AE90` stores its model argument at `+0x00` |
| no-Xanime `ModelManager::mModel` | `ModelManager + 0x1C` | `getJ3DModel` PAL branch loads `+0x1C` when `mXanimePlayer` is null |
| Xanime model construction | `0x803E7EFC → 0x803E9668` | Verified PAL branch instruction in `newXanimePlayer` targets `MR::newJ3DModel` |
| dynamic model type | `J3DModelX` vptr `0x805A31B0` at model `+0` | PAL J3DModelX constructor stores this vptr at `0x802A709C`; factory construction path above |
| `J3DModel::mModelData` | model `+0x04` | PAL `J3DModel::entryModelData` store at `0x80431130`; `getJ3DModelData` accessor at `0x80169760` loads `+0x04` |
| `J3DModel::mMtxBuffer` | model `+0x84` | PAL `entryModelData` stores the created matrix buffer at `0x80431168` |
| `J3DModelData` vptr | `0x805D4F18` at model-data `+0` | Exact PAL vtable symbol |
| embedded `J3DModelData::mJointTree` | model-data `+0x10` | `J3DModelData` constructor at `0x804300A4` adds `0x10` and branches to `J3DJointTree` constructor `0x8042FCF8`; destructor at `0x80430324` passes the same embedded address to `J3DJointTree` destruction |
| `J3DJointTree` vptr | `0x805D4F04` at embedded tree `+0` | PAL symbol and constructor store at `0x8042FD1C` |
| `J3DMtxBuffer::mJointTree` | buffer `+0x00` | `J3DMtxBuffer::create` at `0x804303B4..0x804303C4` stores `J3DModelData + 0x10` into buffer `+0`; `entryModelData` passes the model-data pointer as argument |
| `J3DMtxBuffer` vptr | `0x805D4F5C` at buffer `+0x38` | `entryModelData` at `0x80431158..0x80431164` materializes this vtable address and stores it at `+0x38`; the object allocation is `0x3C` bytes |

### Diagnosis of the first live failure

The earlier reader interpreted the word at `J3DMtxBuffer + 0` as its vptr and rejected `0x80D6875C` because it did not equal `0x805D4F5C`. That check was wrong: the first word is `mJointTree`, while the vptr is at `+0x38`. The reported value is a plausible MEM1 address for the embedded joint tree; it would exactly equal `J3DModelData + 0x10` if that run's `J3DModelData` pointer was `0x80D6874C`. The failed diagnostic did not include that model-data pointer, so this exact equality is not claimed as a live observation. The corrected observer reads and verifies both independent facts: embedded tree pointer/vptr, then matrix-buffer tree identity and vptr at `+0x38`. It does not relax pointer, MEM1-bound, DOL-signature, vptr, or scene-consistency validation.

The PAL code also answers the polymorphism/layout question: the symbol map has a `J3DMtxBuffer` vtable (`0x805D4F5C`) and virtual destructor entry (`+0x08`, audited in the draw-only contract), and `entryModelData` explicitly installs that address at object offset `+0x38`. The layout is nonstandard relative to a presumed first-word vptr, so the observer now follows the actual PAL stores rather than a generic C++ object assumption.

`J3DMtxBuffer::initialize` at `0x8043035C` begins by storing zero at `this+0`, then initializes the remaining fields through `+0x34`. `J3DMtxBuffer::create` later stores `modelData+0x10` at `this+0`. This is why checking the first word as a vptr produced the exact false mismatch.

### User-observed live transition samples

The user reports two successful observer samples from Dolphin PID `119844`, in PAL RMGP01 Rev 0. These are live user observations, not independently recaptured in this documentation pass:

| Field | Beach Bowl sample | Comet Observatory sample |
|---|---:|---:|
| `AllLiveActorGroup` | `0x80FA07D8` | `0x80FA074C` |
| `MarioActor` | `0x810B29CC` | `0x810AB5FC` |
| `ModelManager` | `0x810B96F4` | `0x810B2334` |
| `XanimePlayer` | `0x8111C7F4` | `0x810F63D4` |
| `J3DModel` | `0x810B9794` | `0x810B23D4` |
| `J3DMtxBuffer` | `0x810B997C` | `0x810B25BC` |
| `J3DModelData` | `0x80D6874C` | `0x80D6874C` |
| embedded `J3DJointTree` | `0x80D6875C` | `0x80D6875C` |
| Scene Napa heap | `0x80F8EEF0` | `0x80F8EEF0` |
| sample-time current heap | matches Napa | matches Napa |

The first-sample matrix buffer `0x810B997C` therefore has the reported `mJointTree` value `0x80D6875C`, equal to that sample's `J3DModelData + 0x10`. This resolves the original mismatch: `0x80D6875C` is the embedded joint-tree address, not the matrix-buffer vptr. The corrected checks also accept the second sample's buffer at `0x810B25BC` with the same ModelData/tree identity.

The group, actor, manager, Xanime, model, and matrix-buffer addresses changed across the transition, while the ModelData/tree and Scene Napa/current-heap values were equal. This proves pointer-value continuity in the two reported samples only. It does not establish that the same resource-holder object remained alive: the model data may have remained cached, or the old allocation may have been released and a new resource reloaded at the same guest address. The scene heap handle's numeric value was also reused or retained; equal heap pointers do not establish heap lifetime continuity. No archive pointer, ResourceHolder identity, manager entry, allocation generation, or destructor event was sampled.

### PAL resource loading, lookup, and invalidation trace

Static evidence is from the exact PAL RMGP01 Rev 0 DOL and the matching symbol map. Petari supplies class/member semantics; the PAL code establishes the addresses and call/data flow.

| Stage | PAL evidence | What it establishes |
|---|---|---|
| Actor model setup | `LiveActor::initModelManagerWithAnm` `0x801659C4` allocates/stores a ModelManager at actor `+0x48`; `ModelManager::init` is `0x80169AD0`; `initModelAndAnimation` is `0x8016979C` | The model setup is mediated by a ModelManager and ResourceHolder inputs; an animated model takes the Xanime route |
| Resource-holder management | `ResourceHolderManager` singleton slot `sInstance` is `0x806A2480`; PAL symbols include `createAndAdd` `0x803A6A58`, `find` `0x803A7104`, and `removeIfIsEqualHeap` `0x803A6BCC`. `ModelManager::init` requests holders for model and, if separate, animation archives. The normal resource-holder creation path receives the current heap when no explicit heap is supplied. | A manager owns/records ResourceHolder entries and can find/create them; scene teardown removes entries associated with a heap. The live samples did not identify which entry supplied this Mario model or whether lookup hit a pre-existing entry. |
| Model-data lookup | `MR::newJ3DModel` at `0x803E9668` obtains `J3DModelData` from the holder's `ResTable`; its lookup path calls `getRes` at `0x803E9690` before ModelX allocation and `entryModelData` stores that pointer at model `+0x04` | The model instance borrows archive-backed ModelData; ModelX does not become its owner |
| Scene cleanup | `GameSystemSceneController::destroyScene` `0x8039CF80` destroys the Scene, calls heap-keyed resource/file-holder cleanup (`0x803CE104` → `ResourceHolderManager::removeIfIsEqualHeap` `0x803A6BCC`), then `HeapMemoryWatcher::destroySceneHeap` `0x8039EC9C` destroys the heap handles | Scene-associated holders are removed before their associated scene heap is destroyed on this path. It does not prove a specific holder was removed/recreated during this reported transition. |

The manager exposes creation, lookup, and heap-keyed removal, but this observer does not establish cache-key equality, archive base identity, a cache hit/miss, or any persistent holder identity across the two samples. Because the same ModelData address can be reused after a heap teardown, the equal address alone cannot distinguish a persistent holder from reload-at-reused-address. The samples show that the destination Mario resolved a structurally valid ModelData/tree at the same addresses after the transition; they do not show when or how those bytes were loaded.

#### Shared joint-tree mutation risk

`MR::newJ3DModel` calls `invalidateMtxCalc` (`0x803E7FC4`) and `invalidateJointCallback` (`0x803E7FF8`) on the resolved `J3DModelData*` before constructing the J3DModelX. PAL code traverses the joint array/count in ModelData and clears each joint's matrix-calculation procedure at `joint +0x54` and callback at `joint +0x04`. The ModelData's embedded `J3DJointTree` is at `+0x10`, and both observed samples expose the same ModelData/tree addresses.

Therefore, if two model instances use the same live ModelData at the same time, these factory invalidations act on shared joint objects rather than per-instance matrix buffers. A second model construction can clear joint procedures/callbacks that the first model relies on; later Xanime joint-transform setup also receives the ModelData. This is a concrete shared-mutable-state hazard. It does not prove a visible failure in every combination, nor that two Mario instances were simultaneously active in these two sequential samples. `XanimeCore::enableJointTransform` internals and all subsequent callback writes remain incompletely audited. Sharing this ModelData for a duplicate render instance remains **NO-GO**.

### Remaining unknowns and next bounded experiment

Still unknown: the resource-holder pointer and archive identity used by either sample; whether ResourceHolderManager returned an existing holder or created a new one; the holder's recorded heap in those runs; whether the same heap object survived the transition; and whether any other live model instance shared these joint objects. The samples do not prove constructor-time heap identity or destructor/reclamation events.

Smallest next experiment: offline-trace the PAL `ResourceHolderManager::find/createAndAddInner/removeIfIsEqualHeap` entry layout plus `ResourceHolder` archive/ResTable fields from exact instructions and matching Petari declarations. Only after those offsets are statically verified, extend the existing read-only one-shot observer to report the specific ModelManager ResourceHolder pointer, its archive/ResTable identity, its recorded heap identity if present, and the resolved ModelData pointer. Run once in Beach Bowl and once in Comet Observatory with the same Dolphin PID. Re-resolve the full Mario/scene chain around each fixed bounded read and reject scene changes. Do not scan manager entries, dereference any unverified field, infer persistence from pointer equality alone, or write memory. A stable holder/archive identity across the transition would support reuse but still would not prove uninterrupted lifetime; changed identities would establish replacement, not destructor timing.

`ModelManager::getJ3DModel` returns a `J3DModel*` at the API boundary. The live object is required to have the exact `J3DModelX` dynamic vptr because both the PAL `newXanimePlayer` route and no-Xanime `newJ3DModel` factory construct that type. Unknown model types fail closed. The exact accessor, constructor, member-store, and factory-branch bytes are checked in both the local DOL and live memory; the mandatory AllStars patch is checked for overlap with those signatures.

Petari source is used for names/member interpretation and corroboration: [`LiveActor.hpp`](https://github.com/SMGCommunity/Petari/blob/master/include/Game/LiveActor/LiveActor.hpp), [`ModelManager.hpp`](https://github.com/SMGCommunity/Petari/blob/master/include/Game/LiveActor/ModelManager.hpp), [`ModelManager.cpp`](https://github.com/SMGCommunity/Petari/blob/master/src/Game/LiveActor/ModelManager.cpp), and [`J3DModel.cpp`](https://github.com/SMGCommunity/Petari/blob/master/src/JSystem/J3DGraphAnimator/J3DModel.cpp). PAL bytes and symbols remain authoritative for this observer.

## Sampling and failure rules

One invocation starts from `GameSystem::sInstance`, resolves the complete Mario chain, validates the MarioActor vptr, and follows only fixed, checked fields. It then uses the existing heap observer to re-resolve the scene and actor-group metadata, checks the J3DModel/J3DModelData/J3DJointTree/J3DMtxBuffer identities, re-resolves the Mario root chain before re-reading the model fields, and checks both roots and heap identity again afterward. The matrix-buffer object span is checked as 0x3C bytes; its `+0` pointer must equal the model data's embedded tree at `+0x10`; the embedded tree's vptr must be `0x805D4F04`; and the matrix-buffer vptr at `+0x38` must be `0x805D4F5C`. Live PAL signatures are checked immediately before and after the sample. Any invalid pointer, null required object, unexpected vptr, signature mismatch, scene/group/heap change, or model-pointer change rejects the whole observation.

The command is:

```powershell
Set-Location C:\Users\maxim\Projects\multigalaxy1
.\target\debug\multigalaxy.exe observe-mario-model .\development.local.toml $DolphinPid
```

Use the PID of the already-running stock Dolphin process from the same interactive Windows session. The command verifies its executable, process session, Dolphin 2609a codehandler, PAL DOL/map, and mandatory AllStars patch input. It does not launch Dolphin or change its configuration.

Successful output labels the scene, group, actor, manager, optional Xanime player, model, model data, embedded joint tree and vptr, matrix buffer's joint-tree pointer, matrix-buffer vptr at `+0x38`, Scene Napa/GDDR3 heaps, and the sample-time `sCurrentHeap`. `model_allocation_heap=unverified` is intentional: the code does not retain allocator provenance, and the sample-time heap is not proof of the heap used to create the model.

For a manual transition comparison, invoke the command once in a stable playable scene, complete one ordinary in-game transition, wait until the destination scene is active, then invoke it again with the same Dolphin PID. Compare the printed identities and timestamp. Each run is a separate bounded sample; the tool does not keep watching or cache the first run's pointers. If a required model object is outside the validated MEM1 view, the command rejects it rather than opening a broader mapping.

## Lifetime interpretation limits

The stable tuple of GameSystem, controller, scene, holder, actor group, MarioHolder, MarioActor, model, and heap pointers only means the bounded reads agreed during this invocation. SMG1 has no generation counter verified by this observer. Scene and heap addresses may be reused; identical pointer values on two runs do not prove the same object lifetime. Changed pointers show identity changed, but do not prove that a destructor ran, that an allocation was freed, or that it was not reused. A model pointer disappearing after a scene transition is consistent with reclamation, but cannot distinguish explicit destruction from scene-heap bulk reclamation.

Because the emulator continues concurrently, repeated identity checks cannot provide an atomic snapshot or mathematically eliminate a transition in the small interval between validation and a field read. If transitions or model replacement occur during a sample, the command should reject on the next consistency check and perform no further old-object reads. A successful sample is evidence of a consistent read window only, not a lock, lifetime guarantee, allocation owner, or destructor trace.

## Synthetic coverage and live status

Synthetic tests cover the Xanime and non-Xanime ModelManager branches, expected object vptrs, the non-vptr first matrix-buffer word, embedded-tree identity, wrong joint-tree and matrix-buffer vptrs, invalid/null pointers, model-pointer changes, scene/group changes, and current-heap interpretation. These fixtures validate the Rust reader's bounds and consistency behavior only. They do not prove model destruction or ownership. The user reports two live samples after the correction, which validate the model/tree addresses across a transition; holder/cache identity and uninterrupted allocation lifetime remain unobserved.
