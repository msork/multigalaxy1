# M5: Read-only Mario model and scene-heap observer

Status: implementation in progress. The observer is opt-in and reads stock Dolphin's validated PAL MEM1 mapping only. It performs no game-memory writes, model destruction, callback invocation, hooks, or actor creation.

## Objective

Take one bounded snapshot of the existing MarioActor → ModelManager → J3DModelX → J3DModelData/J3DMtxBuffer pointer graph and the active scene-heap/current-heap identities. Re-resolve all roots during each invocation so a previous scene's pointers are never cached into a later observation.

## Verified research

- The exact PAL RMGP01 Rev 0 DOL is accepted only by the existing DOL and Petari-symbol hashes.
- Existing PAL accessors resolve the scene's MarioActor; `LiveActor::mModelManager` is at `+0x48` in Petari's matching layout, and PAL init code stores/loads that member at the corresponding instructions.
- PAL `ModelManager::getJ3DModel` at `0x80169744` selects `mXanimePlayer->mModel` (`ModelManager+0x18`, `XanimePlayer+0`) or the non-Xanime `ModelManager+0x1C` field. PAL `getJ3DModelData` at `0x80169760` returns model `+0x04`.
- PAL `J3DModelX` construction writes its vptr at object `+0`; `J3DModel::entryModelData` stores the model-data pointer at `+0x04` and matrix-buffer pointer at `+0x84`.
- PAL layout correction: `J3DModelData+0x10` is an embedded `J3DJointTree` (constructor call at `0x804300A4` targets `0x8042FCF8`, whose constructor installs vptr `0x805D4F04`). `J3DMtxBuffer` is a 0x3C-byte object. Its `+0` word is `mJointTree`, initialized to zero then set to `J3DModelData+0x10` by `create` (`0x804303B4..0x804303C4`). `entryModelData` writes the `J3DMtxBuffer` vtable `0x805D4F5C` at `+0x38` (`0x80431158..0x80431164`). Thus the initial live failure was caused by the observer treating `+0` as a vptr; `0x80D6875C` is consistent with the member's pointer role, but its same-sample equality to ModelData+0x10 is not available from the failed diagnostic.
- Both Xanime and non-Xanime access paths converge on a `J3DModel*` and the same `entryModelData` layout; the corrected checks validate tree identity and the matrix-buffer vptr at `+0x38` for either branch.
- Petari supplies class/member names and layouts; exact PAL instructions and signatures are authoritative for address and offset validation.
- Scene and heap identities are sampled through the existing read-only observers. There is no verified monotonic scene-generation counter in this work.

## Functional requirements

- Validate the configured PAL DOL, exact Petari symbols, official stock Dolphin PID/image/session, Dolphin 2609a codehandler, and mandatory AllStars patch inputs.
- Check live game ID plus PAL and model-chain code signatures before and after the snapshot.
- Verify MarioActor, J3DModelX, J3DModelData, embedded J3DJointTree, and J3DMtxBuffer identity/vptr fields against exact PAL values. Validate pointer alignment/ranges and checked field arithmetic before each read.
- Re-resolve the full GameSystem → SceneController → Scene → SceneObjHolder → MarioHolder → MarioActor chain around model reads. Compare actor, group, scene heap, model, and child pointer identities; reject any mixed or transitional sample.
- Report pointer identities and sampled current heap only when the bounded reads are consistent. Never label the sampled current heap as the model's allocation owner.
- Keep the single-instance `probe-mario`, M4 networking, AllStars patch, and launcher character selection unchanged.

## Technical design

Add an opt-in `observe-mario-model <development.local.toml> <Dolphin-pid>` command backed by a portable synthetic-testable observer module and the existing Windows read-only MEM1 adapter. The observer reads only fixed, verified pointer fields, checks dynamic vptrs at PAL-verified offsets (including `J3DMtxBuffer+0x38`), validates `mJointTree` at buffer `+0` against `J3DModelData+0x10`, and re-resolves roots before and after inspecting the model. Each invocation starts from the global GameSystem singleton; no object pointer is retained between calls.

Scene consistency is represented by the tuple of GameSystem, SceneController, Scene, SceneObjHolder, AllLiveActorGroup, MarioHolder, actor, and scene heap handle identities. The tuple is a consistency token for the sample, not a true generation number: address reuse can make a later scene appear identical.

## Dependencies

- `player_probe` for exact PAL DOL/symbol verification, root-chain semantics, and bounded Wii-memory reads.
- `heap_observer` for repeated active scene/group and heap-handle checks.
- `stock_memory::StockMem1` for a read-only Dolphin mapping.
- Existing dev configuration, process/session checks, AllStars validation, and mandatory Dolphin codehandler hash.

## Risks

- Dolphin emulation continues while the host reads; double/repeated reads reduce but cannot eliminate a time-of-check/time-of-use race without pausing or synchronizing the game thread. Any observed mismatch fails closed.
- Scene pointer reuse is known; pointer equality cannot prove continuous object lifetime or destructor execution.
- `sCurrentHeap` is an observation-time global, not stored allocation provenance. The observer cannot identify the heap that allocated the Mario model.
- Petari member names/layouts need exact PAL instruction corroboration at every dereferenced field.

## Acceptance criteria

- The tool only accepts the verified PAL Rev 0 DOL/symbol set and live RMGP01 identity.
- The model path succeeds only for expected live vptrs and valid pointers; wrong vptrs, null objects, out-of-range addresses, or transitions are rejected.
- Output includes MarioActor, ModelManager, optional XanimePlayer, J3DModelX, J3DModelData, J3DMtxBuffer, group/scene/heap identities, and a timestamp; it labels the allocation heap as unknown.
- Synthetic tests cover both ModelManager branches, invalid pointers/vptrs, model pointer changes, scene/group changes, and sampled-current-heap interpretation.
- No runtime claim is made until a user runs the opt-in command in an interactive Dolphin gameplay session.

## Testing strategy

Run formatting, workspace tests/build, `validate-mario-map`, and the existing render-callsite audit. Unit fixtures emulate only the documented field offsets and vptr values; they do not establish live actor lifetime. A later user-side live sample may observe one ordinary scene transition, but pointer changes/reuse cannot prove destructor calls. No Dolphin instance is launched by this implementation task.
