# Read-only PAL Mario model observer

This is an opt-in, one-shot observer for the existing Mario model under official stock Dolphin 2609a. It reads only the validated read-only MEM1 mapping. It does not pause Dolphin, write guest memory, invoke callbacks, destroy a model, or retain pointers between invocations. No live run has yet been recorded for this command.

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
| `J3DMtxBuffer` vptr | `0x805D4F5C` at buffer `+0` | Exact PAL vtable symbol and `entryModelData` construction path |

`ModelManager::getJ3DModel` returns a `J3DModel*` at the API boundary. The live object is required to have the exact `J3DModelX` dynamic vptr because both the PAL `newXanimePlayer` route and no-Xanime `newJ3DModel` factory construct that type. Unknown model types fail closed. The exact accessor, constructor, member-store, and factory-branch bytes are checked in both the local DOL and live memory; the mandatory AllStars patch is checked for overlap with those signatures.

Petari source is used for names/member interpretation and corroboration: [`LiveActor.hpp`](https://github.com/SMGCommunity/Petari/blob/master/include/Game/LiveActor/LiveActor.hpp), [`ModelManager.hpp`](https://github.com/SMGCommunity/Petari/blob/master/include/Game/LiveActor/ModelManager.hpp), [`ModelManager.cpp`](https://github.com/SMGCommunity/Petari/blob/master/src/Game/LiveActor/ModelManager.cpp), and [`J3DModel.cpp`](https://github.com/SMGCommunity/Petari/blob/master/src/JSystem/J3DGraphAnimator/J3DModel.cpp). PAL bytes and symbols remain authoritative for this observer.

## Sampling and failure rules

One invocation starts from `GameSystem::sInstance`, resolves the complete Mario chain, validates the MarioActor vptr, and follows only fixed, checked fields. It then uses the existing heap observer to re-resolve the scene and actor-group metadata, checks model/data/matrix-buffer vptrs, re-resolves the Mario root chain before re-reading the model fields, and checks both roots and heap identity again afterward. Live PAL signatures are checked immediately before and after the sample. Any invalid pointer, null required object, unexpected vptr, signature mismatch, scene/group/heap change, or model-pointer change rejects the whole observation.

The command is:

```powershell
Set-Location C:\Users\maxim\Projects\multigalaxy1
.\target\debug\multigalaxy.exe observe-mario-model .\development.local.toml $DolphinPid
```

Use the PID of the already-running stock Dolphin process from the same interactive Windows session. The command verifies its executable, process session, Dolphin 2609a codehandler, PAL DOL/map, and mandatory AllStars patch input. It does not launch Dolphin or change its configuration.

Successful output labels the scene, group, actor, manager, optional Xanime player, model, model data, matrix buffer, Scene Napa/GDDR3 heaps, and the sample-time `sCurrentHeap`. `model_allocation_heap=unverified` is intentional: the code does not retain allocator provenance, and the sample-time heap is not proof of the heap used to create the model.

For a manual transition comparison, invoke the command once in a stable playable scene, complete one ordinary in-game transition, wait until the destination scene is active, then invoke it again with the same Dolphin PID. Compare the printed identities and timestamp. Each run is a separate bounded sample; the tool does not keep watching or cache the first run's pointers. If a required model object is outside the validated MEM1 view, the command rejects it rather than opening a broader mapping.

## Lifetime interpretation limits

The stable tuple of GameSystem, controller, scene, holder, actor group, MarioHolder, MarioActor, model, and heap pointers only means the bounded reads agreed during this invocation. SMG1 has no generation counter verified by this observer. Scene and heap addresses may be reused; identical pointer values on two runs do not prove the same object lifetime. Changed pointers show identity changed, but do not prove that a destructor ran, that an allocation was freed, or that it was not reused. A model pointer disappearing after a scene transition is consistent with reclamation, but cannot distinguish explicit destruction from scene-heap bulk reclamation.

Because the emulator continues concurrently, repeated identity checks cannot provide an atomic snapshot or mathematically eliminate a transition in the small interval between validation and a field read. If transitions or model replacement occur during a sample, the command should reject on the next consistency check and perform no further old-object reads. A successful sample is evidence of a consistent read window only, not a lock, lifetime guarantee, allocation owner, or destructor trace.

## Synthetic coverage and live status

Synthetic tests cover the Xanime and non-Xanime ModelManager branches, expected object vptrs, invalid/null pointers, model-pointer changes, scene/group changes, and current-heap interpretation. These fixtures validate the Rust reader's bounds and consistency behavior only. They do not prove live model layout or destruction. No live Mario-model transition observation has been performed yet; do not treat the observer's compilation or synthetic tests as runtime evidence.
