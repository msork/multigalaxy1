# Draw-only shared-model renderer: contract and gates

Target: PAL RMGP01 Rev 0, stock Dolphin, one static-offset copy of the active local Mario pose. This is research/specification only. No hook, actor, model object, matrix allocation, memory write, DOL patch, or Dolphin change is implemented.

## Evidence and provenance

The local DOL and Petari map are checked by `validate-mario-map`. Relevant exact PAL symbols include:

| Function | PAL address | Size | Intended evidence |
|---|---:|---:|---|
| `J3DModelX::directDraw(J3DModel*)` | `0x802A6B20` | `0x144` | Render and mutable-state path |
| `J3DModelX::viewCalcRef(u32,J3DModel*)` | `0x802A668C` | `0x90` | Candidate pose-to-independent-draw-matrix path |
| `J3DModelX::viewCalc3(u32,Mtx*)` | `0x802A65AC` | `0xE0` | Existing extra-view matrix path |
| `J3DModelX::copyAnmMtxBuffer(const J3DModelX*)` | `0x802A6598` | `0x14` | Animation matrix pointer alias helper |
| `J3DModelX` constructor | `0x802A702C` | `0xDFC` | ModelX construction and GX display-list initialization |
| `J3DModelX` destructor | `0x802A8070` | `0x60` | Teardown entry |
| `J3DMtxBuffer::create` | `0x804303A0` | `0x108` | Buffer allocation dispatch |
| `J3DMtxBuffer::createDoubleDrawMtx` | `0x80430598` | `0xF4` | Two-view matrix-array allocation |
| `J3DMtxBuffer` destructor | `0x80431FE0` | `0x40` | Buffer cleanup candidate |
| `J3DModel::entryModelData` | `0x8043111C` | `0x170` | Model data, buffer, packet construction |
| `J3DModel::viewCalc` | `0x80431C0C` | `0x224` | Normal matrix, cache, packet preparation path |
| `J3DModel::prepareShapePackets` | `0x80431F44` | `0x64` | Shape packet buffer binding |
| `DCStoreRange` / `DCStoreRangeNoSync` | `0x804A2F50` / `0x804A2FAC` | `0x30` / `0x2C` | Cache maintenance entry points |

Source corroboration is from Petari's [`J3DModelX.cpp`](https://github.com/SMGCommunity/Petari/blob/master/src/Game/Player/J3DModelX.cpp), [`MarioActorDraw.cpp`](https://github.com/SMGCommunity/Petari/blob/master/src/Game/Player/MarioActorDraw.cpp), [`J3DModel.cpp`](https://github.com/SMGCommunity/Petari/blob/master/src/JSystem/J3DGraphAnimator/J3DModel.cpp), and [`J3DMtxBuffer.cpp`](https://github.com/SMGCommunity/Petari/blob/master/src/JSystem/J3DGraphAnimator/J3DMtxBuffer.cpp). The current source files are useful behavioral references; source-level details below are not represented as runtime tests. PAL addresses remain bound to the local exact DOL/symbol fingerprint. No Petari code was copied.

## Candidate static-offset design

Investigate a **separate J3DModelX render object** initialized from the local Mario's existing `J3DModelData` and resources, but with its own J3DModel base fields, `J3DMtxBuffer`, draw/normal arrays, shape packets, matrix-view storage, and transient J3DModelX fields. Each frame, calculate the duplicate's draw matrices from the local model's animation matrices using the PAL `viewCalcRef` path and a base transform translated by the fixed offset. Render via the duplicate model's own `directDraw(nullptr)`. It remains outside `LiveActor`, `NameObj`, MarioHolder, scheduler, camera, collision, and input systems.

Use the local model's current pose as the source; do not construct a second MarioAnimator/XanimePlayer for this static-pose proof. The intended view must be private to the duplicate object. Do not borrow one of the local Mario model's existing extra views: the game sets `_DD=8` and allocates eight `0xC00`-byte aligned extra matrix blocks for its own view/reflection/blur features.

Suggested draw ordering at the audited `drawMarioModel` call is:

1. Validate the current scene generation, model identity, and ready duplicate state. Otherwise skip duplicate drawing.
2. Update the duplicate's fixed-offset base transform and calculate its distinct pose matrices from the current local animation data.
3. Draw the duplicate once through its own initialized model state.
4. Invoke the original PAL `J3DModelX::directDraw` exactly once with the original call arguments and return to the caller's original continuation.

Putting the normal local draw last is a candidate mitigation: its original call path reestablishes the local model's normal drawing state after the duplicate's draw. It does **not** prove that GX FIFO state, global J3DSys pointers, shape caches, asynchronous writes, or callbacks are restored. The order is a hypothesis to test, not an approved hook implementation.

The duplicate model constructor and its setup cannot run lazily inside draw. A later implementation must identify a verified model-ready initialization boundary, select the correct heap, confirm model data/archive residency, initialize all per-instance buffers, and have a teardown policy before enabling the draw branch.

## Ownership and mutable state inventory

### Model data and model instance

Petari `J3DModel::entryModelData` assigns the supplied `J3DModelData*`, allocates a new `J3DMtxBuffer`, creates per-instance shape packets and material packets, and binds its vertex buffer to model data. Thus a separate render instance can borrow the local model data while owning packets and matrices, but only while the source archive/model data remains resident. Model data includes shared joints, materials, shapes, vertex arrays, textures, and possibly shared display lists; the clone must not mutate these as private state.

`J3DModel::viewCalc` swaps double-buffered draw and normal arrays, computes draw matrices, then normal/billboard/bump matrices as the model flags require. It performs cache stores and prepares shape packets. `prepareShapePackets` writes each packet's matrix-buffer pointer and base-matrix pointer. These operations are per-instance only when the packet and buffer objects are truly separate.

### J3DMtxBuffer allocations and arrays

Petari `J3DMtxBuffer::create` stores `mMtxNum`, borrows the model's joint tree, allocates animation and weight-envelope matrices when counts are nonzero, selects no-use or double draw matrices according to model-data flags, and may allocate bump matrices. `createDoubleDrawMtx` allocates two view arrays of draw-matrix pointers and normal-matrix pointers, then allocates `0x20`-aligned matrix arrays sized by `getDrawMtxNum()` for each requested matrix slot. Exact counts depend on the PAL Mario `J3DModelData` flags/counts and the constructor's buffer argument; they must be queried from the validated model, never hardcoded from another character/model.

The source `J3DMtxBuffer2::rotationMtx` redirects one view's draw-matrix pointer to an extra-matrix block. `J3DModelX::viewCalcRef` uses that view, reads animation matrices from its source `J3DModel`, computes destination draw matrices using the destination's base transform, flushes the draw-matrix range with `DCStoreRangeNoSync`, and prepares destination shape packets. It does not visibly recalculate normal or bump matrices in that function. For a pure translation offset the normal matrices should be unchanged mathematically, but valid destination normal data and the downstream GX matrix-load path must still be established from PAL evidence.

Petari's Mario model initializer sets `_DD=8` and allocates eight aligned extra matrix blocks of `0xC00` bytes each. This is local Mario's allocation and must remain untouched. Any clone allocation count/size must derive from the clone's own validated model data and selected view count.

`copyAnmMtxBuffer` aliases the destination animation-matrix pointer to the source buffer. This would create a borrowed pointer with strict frame/model lifetimes and an allocation/destructor ownership hazard. Prefer `viewCalcRef(sourceModel)` if PAL disassembly confirms it reads the source matrices and writes only clone-owned draw matrices; do not use the alias helper unless destructor ownership is explicitly handled.

### Direct draw's mutations and obligations

Petari `J3DModelX::directDraw` is not side-effect-free. It:

- sets global `j3dSys` model, flags, texture, and then per-material packet;
- reads model flags and synchronizes model-data J3D flags;
- decrements the per-instance `_1D0` fog counter;
- updates per-instance `_1C0` material index in the loop;
- calls material packet/display-list code, optional shape user data, model display lists, queued one-shot `_1B8`/`_1B4` and per-material `_1C8`/`_1C4` lists, and `_120` callback;
- may swap a packet's shape pointer under `_12C`, copy/alter shape matrix-related fields, draw, then restore only the packet's shape pointer;
- emits GX commands and changes global shape/J3D/GX caches, then resets the global VCD/VAT cache at function end.

For a clone, all transient display-list queues, callbacks, `_12C`, and one-shot state must be initialized independently and left empty unless a verified need exists. Never call the local object's `directDraw` twice to render the duplicate: that would consume or advance its one-shot fields and callbacks. A distinct clone avoids those per-instance fields aliasing, but shared model-data/material/shape and global J3DSys/GX state remain shared risks. No CPU save/restore around the call can undo GX commands already queued to hardware.

State categories that must be accounted for before GO:

| State | Owner | Candidate action | Current status |
|---|---|---|---|
| local model pointer/base transform/scale and flags | local J3DModelX | never replace; do not change | Design invariant, no runtime test |
| local `J3DMtxBuffer`, double-buffer index, draw/normal/bump arrays | local model | never redirect or swap for clone | Design invariant |
| local animation matrices/tree | local model/model data | read only while local calc is complete; destination matrices remain private | Exact callback synchronization point unverified |
| clone base transform + draw/normal/bump arrays | clone | own and update only in clone | Allocation/teardown unverified |
| clone shape/material packets and packet matrix/base pointers | clone | own; prepare against clone buffer; restore/refresh each frame | PAL caller schedule unverified |
| shared model data, material/shape nodes, shared display lists | borrowed local resource | read-only; no shape pointer/field mutation permitted | `directDraw` callback/custom `_12C` hazards remain |
| clone J3DModelX transient fields (`_1D0`, `_1C0`, one-shot DL queues, callbacks) | clone | own; initialize neutral; no borrowing of local queue state | Constructor initializes some fields; complete state/cleanup unverified |
| `j3dSys` model/flags/texture/material packet and deferred texture globals | process-wide J3D | clone draw followed by exact original local draw is only a candidate reset order | Full restore/equivalence unproven |
| GX FIFO/register state, shape/VCD/VAT/LOD/matrix caches | graphics pipeline/global cache | use verified engine draw path; establish postcondition for next actor | No CPU snapshot can undo emitted FIFO commands; runtime isolation unproven |
| CPU-written draw/normal/bump/extra matrices | clone arrays consumed by GX | align; flush correct ranges; order cache operation before draw | `viewCalcRef` source uses `DCStoreRangeNoSync` for draw matrices; normal cache work unresolved |
| model/resource heap and scene generation | scene/heap/resource holder | bind a generation; invalidate before archive/heap teardown | Constructor-time heap and destruction order unresolved |

### Construction and destruction

Petari's `J3DModelX` constructor delegates to model setup, then generates/stores multiple GX display lists, allocates per-material transient arrays, and initializes callbacks/flags. Mario's separate helper allocates additional aligned per-view matrix blocks. These are not cheap POD fields that may be copied with `memcpy`.

Petari's `J3DModelX` destructor source body is empty, but the PAL deleting-destructor instructions have compiler/member cleanup: they call the embedded vertex-buffer destructor and conditionally delete the object. The inspected body does not call the PAL `J3DModel` destructor or `J3DMtxBuffer` destructor. The latter has a PAL symbol but its destructor body does not enumerate/free the internal arrays. Therefore neither source-level empty-body observation nor a base-class assumption supplies a complete cleanup sequence. Current constructor-time heap evidence only samples gameplay's current heap and does not prove the heap used at model construction. A clone may leak or outlive model data; scene-heap reclamation remains a hypothesis until owner/caller teardown is traced.

### PAL construction and cleanup evidence

The following PAL instruction trace uses `reference/original/main.dol` (SHA-256 `8200fb72b3170069bb71c8b3dc21b49e429977ce8ae5159bb95ea6a19de0fbee`), translated through its DOL section table and checked against `target/petari-rmgp01-symbols.txt` (matching SHA-1 recorded in `docs/research.md`). It confirms call ordering, object-field stores, and branch targets. Disassembly proves machine-code behavior; Petari source supplies class/member interpretation where explicitly stated.

| PAL address | DOL file offset / word | Observed instruction/call | Evidence and limit |
|---:|---|---|---|
| `0x802A7068/6C` | `0x2A25A8: 38630088`; `0x2A25AC: 4817AF0D` | adjusts `r3` by `+0x88`, then calls `0x80421F78` | `J3DVertexBuffer::init` on the embedded vertex-buffer field |
| `0x802A7074` | `0x2A25B4: 4818A021` | calls `0x80431094` | `J3DModel::initialize` |
| `0x802A7088` | `0x2A25C8: 4818A095` | calls `0x8043111C` | `J3DModel::entryModelData`; model-data argument and flags originate from constructor inputs |
| `0x80431144/48/64/78` | `0x42C684: 3860003C`; `0x42C688: 4BFD89CD`; `0x42C6A4: 4BFFF1F9`; `0x42C6B8: 4BFFF229` | requests `0x3C` bytes through `__nw__FUl` at `0x80409B14`, initializes, then calls `create` | Allocates a `J3DMtxBuffer`; initialize is `0x8043035C`, create is `0x804303A0` |
| `0x804303CC` | `0x42B90C: 480000DD` | calls `createAnmMtx` (`0x804304A8`) | Conditional allocation/setup selected from model-data counts |
| `0x804303E4` | `0x42B924: 48000129` | calls `createWeightEnvelopeMtx` (`0x8043050C`) | Conditional model-data path |
| `0x80430408/48` | `0x42B948: 48000165`; `0x42B988: 48000151` | selects no-use draw matrices or calls `createDoubleDrawMtx` (`0x80430598`) | Draw-buffer path depends on flags/arguments; do not assume a fixed count |
| `0x80430484` | `0x42B9C4: 48000209` | may call `createBumpMtxArray` (`0x8043068C`) | Conditional model-data path |
| `0x80431190/AC` | `0x42C6D0: 480000FD`; `0x42C6EC: 48000195` | calls shape-packet creation (`0x8043128C`) and material-packet creation (`0x80431340`) on success branches | These are model-instance packet paths; exact cardinalities derive from model data |
| `0x804311C4/CC` | `0x42C704: 4BFF0D6D`; `0x42C70C: 48000D79` | calls `J3DVertexBuffer::setVertexData` (`0x80421F30`) and `prepareShapePackets` (`0x80431F44`) | Connects vertex/model data and binds packets to the instance's matrix storage |
| `0x802A66C4/E0` | `0x2A1C04: 48001ACD`; `0x2A1C20: 48000171` | calls `J3DMtxBuffer2::rotationMtx` (`0x802A8190`) and `calcDrawMtx2` (`0x802A6850`) | `viewCalcRef` selects an extra draw-matrix view and calculates destination draw matrices from source model pose |
| `0x802A66F8/6700` | `0x2A1C38: 481FC8B5`; `0x2A1C40: 4818B845` | calls `DCStoreRangeNoSync` (`0x804A2FAC`) and `prepareShapePackets` (`0x80431F44`) | Draw-matrix cache writeback and packet refresh occur in this path; completion/order relative to GX and normal/bump buffers is not thereby proven |
| `0x802A80A0` | `0x2A35E0: 48179F19` | calls `J3DVertexBuffer::~J3DVertexBuffer` (`0x80421FB8`) on `this+0x88` | Observed in `J3DModelX` destructor body |
| `0x802A80B0` | `0x2A35F0: 48161AAD` | conditionally calls `__dl__FPv` (`0x80409B5C`) | Deletes the object when its deleting-destructor flag requests it |
| `0x80432004` | `0x42D544: 4BFD7B59` | `J3DMtxBuffer` deleting destructor may call `__dl__FPv` | The destructor body does not itself enumerate/free each matrix allocation |

The containing high-level path is also present in PAL: `ModelManager::initModelAndAnimation` (`0x8016979C`, file `0x164C8C`) calls `MR::newJ3DModel` (`0x803E9668`) at `0x801697E4` / file `0x164D24`. `newJ3DModel` obtains the named model-data pointer from a `ResTable` (`getRes` at `0x803E9690`), invokes `invalidateMtxCalc` and `invalidateJointCallback` on that pointer (`0x803E9698`, `0x803E96A0`), allocates a `J3DModelX` with ordinary `__nw` (`0x803E992C`, file `0x3E4E6C`), then calls its constructor (`0x803E9948`, file `0x3E4E88`). The function contains a second `__nw`/vertex-buffer-init/`entryModelData` path (`0x803E9970`–`0x803E99AC`); its branch predicate and relation to the first path are not yet fully audited. Do not collapse these into one unconditional construction route.

`entryModelData` stores the incoming `J3DModelData*` at `J3DModel+0x04` (`stw r4,0x04(r3)`, `0x80431130`, file `0x42C670`). It stores the newly allocated `J3DMtxBuffer*` at `+0x84` (`0x80431168`, file `0x42C6A8`). The shape packet array is stored at `+0xC4` and material packet array at `+0xC0` in their respective creation routines. These fields are per-instance pointers; `J3DModelData` itself is passed in and borrowed by the model setup.

`J3DModelX` also makes three material-count-derived `__nwa` allocations and stores them at `this+0x1C4`, `+0x1C8`, and `+0x1CC`: allocation calls are `0x802A7D9C`, `0x802A7DA8`, `0x802A7DB4`, file offsets `0x2A32DC`, `0x2A32E8`, `0x2A32F4`. The preceding instructions load the material count from model data `+0x5C` and scale it for those arrays. They are not released by the observed `J3DModelX` deleting-destructor body.

The default allocation and delete operators bind the heap explicitly:

| Wrapper | PAL code | Verified behavior |
|---|---|---|
| `__nw__FUl` | `0x80409B14`, file `0x405054` | supplies alignment 4 and a null heap, then calls `JKRHeap::alloc` (`0x80409678`) |
| `__nwa__FUl` | `0x80409B38`, file `0x405078` | supplies alignment 4 and a null heap, then calls the same `JKRHeap::alloc` path |
| `JKRHeap::alloc(size,align,null)` | `0x80409678`, file `0x404BB8` | null heap path loads `JKRHeap::sCurrentHeap` at `0x80409698`, file `0x404BD8`; symbol slot `0x806A26DC` |
| `__dl__FPv` | `0x80409B5C`, file `0x40509C` | passes null heap to `JKRHeap::free` (`0x804096C8`); its null-heap path also reads `sCurrentHeap` |

Thus the **selected current heap at each allocation call** owns the model, buffer objects, matrix arrays, packets, and ModelX arrays. In the normal placement path, the Scene heap has been selected before actor/model initialization; `docs/references/pal-placement-heap-lifetime.md` records the scene-init and owner chain. The exact current-heap value during a proposed render-only clone constructor is still unknown. An explicit `delete` on a different current heap is unsafe by construction of these wrappers; never assume `__dl` finds the original heap from the pointer.

The trace supports this **partial construction order**: construct/initialize the vertex buffer and model fields; enter model data; allocate and initialize a `J3DMtxBuffer`; create model-data-selected animation/envelope/draw/bump storage; create shape/material packets; bind vertex data and prepare packets; then `J3DModelX` performs its derived display-list/transient setup. At render time `viewCalcRef` can write the extra draw-matrix view and refresh packets.

It does **not** support a complete object-by-object cleanup sequence. The inspected `J3DModelX` deleting destructor calls the embedded vertex-buffer destructor and object delete; it has no observed call to `J3DModel::~J3DModel` (`0x802A8134`) or `J3DMtxBuffer::~J3DMtxBuffer` (`0x80431FE0`). The `J3DModel` destructor itself calls the vertex-buffer destructor and conditional object delete, but no `J3DMtxBuffer` or packet-array cleanup. The `J3DMtxBuffer` deleting destructor only conditionally deletes the buffer object; it does not free its constituent arrays. No direct call to any of those model destructors was found in the relevant constructors/`ModelManager::init` path; virtual teardown ownership remains to be traced.

The broader PAL scene path supplies a plausible bulk lifetime for allocations made with the Scene heap: `GameSystemSceneController::destroyScene` destructs the Scene, removes `ResourceHolderManager` entries matching the Scene heap, then destroys the scene heap handles (`docs/references/pal-placement-heap-lifetime.md`, exact PAL sites). That bulk path is not an explicit per-model destructor sequence. It does not prove that model callbacks are detached before archive data is removed, or that a manually deleted model’s arrays are reclaimed immediately.

### Native ownership and release matrix

| Resource/allocation | Creation and owner evidence | Native release evidence | Hazards / status |
|---|---|---|---|
| `J3DModelData*` and archive-backed joints/materials/shapes | `newJ3DModel` gets the pointer from a `ResTable`; `entryModelData` stores it at model `+0x04`. `ModelManager::init` creates resource holders; ResourceHolderManager records current heap when none is supplied. | A `J3DModelData` destructor exists (`0x804302F4`) and calls material-table and joint-tree destructors, then conditional `__dl`; no call from `J3DModelX` teardown was found. Resource holders are removed before scene heap destruction. | Borrowed, not owned by a second model instance. `newJ3DModel` calls `invalidateMtxCalc` and `invalidateJointCallback` on the shared pointer before constructing, so strict immutability is already unproven. Do not run its destructor or share without field/callback mutation audit. |
| `J3DModelX` / `J3DModel` object and embedded vertex buffer | ModelManager path allocates the instance via `__nw`; constructor initializes the embedded vertex buffer at `+0x88`. Default operators select `sCurrentHeap`. | `J3DModelX::~` invokes the embedded vertex-buffer destructor; deleting variant calls `__dl(this)`. No observed base destructor call. | Object delete uses whichever heap is current. Individual deletion does not establish cleanup of the rest of the graph. |
| `J3DMtxBuffer` object | `entryModelData` requests 0x3C bytes with `__nw`, initializes it, stores pointer at `J3DModel+0x84`, then calls `create`. | `J3DMtxBuffer::~` can `__dl(this)`; no internal-array frees in the destructor body. `J3DModelX::~` does not invoke it in the inspected path. | Individual buffer deletion leaks its child arrays unless another owner frees them. Heap mismatch can route `__dl` incorrectly. |
| Animation, weight, double draw/normal, bump, and extra-view matrix arrays | `J3DMtxBuffer::create*` calls `__nwa` (including aligned `__nwa(size,align)` variants) on `sCurrentHeap`; counts/branches depend on model-data fields/flags. `viewCalcRef` redirects a view and writes draw matrices, then performs `DCStoreRangeNoSync`. | No matching per-array frees are present in `J3DMtxBuffer::~`. Scene heap teardown reclaims arena storage as a group. | Matrix count and flags must come from the actual model. Early object destruction can strand arrays until scene teardown; manual frees risk double-free or wrong-heap free. Normal/bump cache and complete `viewCalcRef` write set remain unaudited. |
| Shape packets | `createShapePacket` allocates/constructs an array using model-data shape count, stores it at model `+0xC4`. | `J3DShapePacket::~` exists, but no invocation from the model destructors was found. | Packet pointer/buffer bindings are mutable instance state. Ownership is the selected heap unless a separate owner path is proven. |
| Material packets and per-material display lists | `createMatPacket` allocates/constructs an array from material count, stores it at model `+0xC0`, then allocates/configures per-material display-list state. | `J3DMatPacket::~` exists, but no invocation from the model destructors was found. | Exact nested display-list cleanup is not traced. Reinitializing against shared materials may mutate data or duplicate resources. |
| ModelX arrays at `+0x1C4/+0x1C8/+0x1CC` | Three `__nwa` calls in the constructor; sizes derive from model-data material count. | No frees in the observed `J3DModelX` deleting destructor. | Individual ModelX deletion strands arrays in the heap; scene bulk reclamation is the only evidenced broad cleanup boundary. |
| Scene heap and resource holder | Scene setup selects current Scene heap before placement; resource holders track their heap. | Scene transition destroys Scene, removes matching resource/file holders, then destroys heap handles. | Must invalidate/detach all users before holder removal and heap destruction. A surviving remote model pointer becomes stale even if its scene-heap memory has not yet been reclaimed. |

**Conclusion:** a second `J3DModel` can be initialized around the same `J3DModelData*` and receive separate buffers/packets. We cannot call that a second Mario with immutable shared data or fully independent animation/draw state: the factory invalidates mutable model-data callback/calculation state, packet/display-list paths are not fully audited, and no complete per-object destruction path is established. The safe native ownership model currently supported by evidence is scene-heap allocation plus one scene-scoped instance, hidden/reused rather than individually destroyed, and invalidated before resource-holder/heap teardown. Even that remains a design deduction, not a runtime authorization.

`J3DModel::entryModelData` receives a `J3DModelData*` and builds instance-side buffers/packets around it. This establishes a plausible API-level arrangement where multiple model instances can point at one model-data resource while retaining separate `J3DMtxBuffer` and packet objects. It does **not** establish that Mario's particular model data is immutable during draw: `directDraw`, material/display-list callbacks, shared shape state, and `syncJ3DSysFlags`/other global synchronization paths must be read at the exact PAL callsites. Therefore the answer is **conditionally yes at the `entryModelData` boundary, but not yet yes for a safe duplicate Mario draw**. The source model data/archive must remain resident, every mutable field touched by the actual render path must be instance-local or proven idempotent/read-only, and callbacks must be absent or separately owned.

## Host-side ownership model (synthetic only)

The CLI crate's `render_ownership` module models a validated immutable resource handle shared by `Arc`, plus exclusive instance-owned animation/draw/normal matrices and packets. Matrix/packet allocations belong to a synthetic scene-heap arena rather than the instance. Tests cover rollback to an arena checkpoint on every injected allocation/initialization failure, instance-state isolation, model-data lifetime, current-heap identity on explicit delete, refusing heap destruction while instances remain attached, and bulk arena reclamation after detachment.

These host tests prove Rust ownership behavior for this model only. `Arc`, host allocations, synthetic heap IDs, and RAII are not the Wii allocator ABI, JKRHeap semantics, GX cache protocol, Dolphin behavior, or proof that actual `J3DModelX` destruction releases PAL allocations. In particular, the synthetic transactional rollback is not evidence that PAL `entryModelData` rolls back partial allocations. The model deliberately refuses to encode an executable construction/cleanup recipe.

## Specific next experiment

Run one **bounded, read-only PAL destructor-owner audit**: inspect the exact `J3DModelX` vtable destructor slots, all direct constructor callers, `ModelManager`/actor teardown call paths, and the scene-destroy order already identified in the PAL DOL. Determine whether any owner explicitly invokes ModelX, J3DModel, J3DMtxBuffer, shape-packet, or material-packet destructors before heap destruction, and whether that invocation runs with the allocation heap current. This resolves the missing destructor edge without touching live memory. If virtual dispatch or heap identity still cannot be proven statically, the next permitted experiment is a bounded observer of the exact model/manager/resource-holder pointers plus scene heap identity across one transition—read-only, no dereference beyond separately verified layouts. Stop if their lifetime cannot be tied to the same scene generation; runtime remains NO-GO.

## Exact synchronization and lifecycle contract

Before any future implementation, establish these invariants:

1. Run only on the same emulation/render thread and after local Mario's animation matrices are complete for the frame; before the normal Mario directDraw call. Do not add a second thread or write these objects from Rust/network callbacks.
2. Clone/source/model-data pointers carry a monotonically increasing scene/model generation. Every draw checks generation and model identity. Generation changes, model-form changes, death/respawn, pause/reset, or missing resources hide the clone and prohibit dereference until reinitialized.
3. Source animation and shared model data are read-only during clone view calculation/draw. No recursive or concurrent J3D callbacks are permitted. Any callback-enabled clone is NO-GO until callback ownership is proven.
4. Duplicate output uses only clone-owned matrix storage and packets. No local `mpDrawMtxArr`, current view index, matrix buffer, model base transform, packet pointers, material/shape fields, transient queues, or local visibility flags are altered.
5. CPU matrix writes complete and the precisely required cache writeback/synchronization completes before GX reads. The second draw completes through the existing graphics command ordering before any clone storage could be freed/reused.
6. The ordinary original PAL call executes once and reaches the original continuation with ABI and caller-visible values intact. Clone failure skips the clone and takes the original path.
7. Scene transition invalidates the clone before either source model data or allocation heap is destroyed. No clone destructor frees borrowed resources; no alias survives into a new generation.

## Offline validation plan

1. Run `cargo run -p multigalaxy -- validate-mario-map reference/original/main.dol target/petari-rmgp01-symbols.txt`.
2. Run `cargo run -p multigalaxy -- inspect-render-prototype reference/original/main.dol target/petari-rmgp01-symbols.txt`; require all four existing call signatures/context checks.
3. Add a follow-up read-only DOL audit (before any patch code) for the exact PAL `J3DModelX::viewCalcRef`, `J3DModelX::directDraw`, `J3DModel::entryModelData`, `J3DModel::viewCalc`, `J3DMtxBuffer::createDoubleDrawMtx`, relevant destructors, and cache helper callsites. Verify function hashes, branch targets, pointer/array writes, and AllStars overlap. The current tool does not yet certify these internals.
4. Compare source-guided structure/allocation counts with PAL DOL call sequences and available model-data metadata. Synthetic matrix tests cover distinct local/clone buffers, translation-only transform, finite/aligned bounded allocation sizes, unchanged local checksums, cache-range rounding, and generation invalidation. Do not invent a matrix count for fixtures: inject a synthetic validated model descriptor.
5. Inspect draw packet callbacks and verify the clone has no callback/one-shot-list state that can consume local work. Demonstrate the original directDraw call count is exactly one in a host-side wrapper model and clone failure falls through unchanged.
6. Only after all static gates pass may an explicit runtime experiment be proposed. The current request authorizes no such runtime work.

## Rollback and stop criteria

Any uncertain DOL signature, unverified object size, missing model identity, stale generation, invalid heap/resource owner, cache ambiguity, unbounded callback/packet path, changed local buffer checksum, wrong number of original draws, non-neutral clone state, or inability to establish following-actor GX/J3D state means hide the clone and use the unmodified original draw path. A future opt-in patch must be removable by restoring the exact original word(s), and must not overlap AllStars or the current mailbox. Never modify the user's original image/profile.

## Deployment: optional xdelta assessment

An xdelta/VCDIFF file is a delta between source and target byte streams; it does not solve rendering correctness. The official xdelta3 documentation supports separate source/target encoding and decoding and can include source/target digests, but the launcher must still validate its own manifest and resulting hash. See [xdelta3 command-line documentation](https://github.com/jmacd/xdelta/blob/main/site/docs/commandline.md) and [project overview/licensing](https://github.com/jmacd/xdelta/blob/main/site/docs/index.md).

| Route | Advantages | Costs/risks | Fit |
|---|---|---|---|
| Existing Gecko/OnFrame deployment | Works with current stock-Dolphin launcher/profile and mandatory AllStars; no disc-image rewrite; reversible | Per-frame patch mechanism; hook ABI and cache/runtime behavior still need proof | Safest for experiments while M5 remains unproven |
| xdelta over full RVZ/ISO | Can distribute a compact reproducible result when the exact base container matches; installs a static executable patch | Container/compression/hash changes invalidate source; whole-image hash makes language variants separate bases; producing a valid modified Wii partition and making Dolphin boot it needs an image-build/apply pipeline; patch does not reduce J3D draw state risk | Not recommended as initial path |
| xdelta over extracted PAL `main.dol` + launcher-side image overlay/rebuild | Small scope; leaves other files/local languages intact if only DOL is replaced; static code patch is independent of OnFrame reapplication | Dolphin needs a bootable disc image, so the launcher must safely create a temporary patched game copy/overlay and update Wii partition integrity metadata; RVZ extraction/repack path, legal asset separation, and localized-image policy need proof | Best future xdelta shape only after image pipeline feasibility and hook proof |

If implemented later, require a versioned manifest containing patch schema/tool version, patch SHA-256, source container or per-user source-image identity, verified PAL disc ID and revision, extracted source DOL SHA-256, expected output DOL/image hashes, exact file scope, AllStars compatibility fingerprint, and supported locale variants. Validate the complete source before decoding; decode to a new temp file, verify output size/hash and PAL DOL map/signatures, then atomically rename to a new user-owned output path. Never patch the input in place. Never include RVZ/ISO, extracted Nintendo assets, or reusable proprietary data in the delta. An exact full-image hash cannot be assumed identical across Russian/French/English localized installations; support those only through separately verified image identities and a DOL-scoped patch that leaves each client's localization files intact.

The xdelta route may make static PPC code installation/reproducibility easier after a hook is proven. It does **not** simplify model/packet/matrix ownership, scene lifecycle, or Wario/Waluigi resource loading, and it does not replace Rust launcher/network/bridge behavior or M4. Recommendation: keep Gecko as current experiment carrier; investigate DOL-scoped xdelta only after a safe hook exists and a temporary bootable-image overlay/rebuild is proven. No `.xdelta` is generated or applied now.

## Decision

**NO-GO for any runtime draw hook or model allocation.** The best static-offset design lead is a separate clone `J3DModelX`, per-instance view/matrices, `viewCalcRef` from local animation data, and a clone draw before the original local directDraw exactly once. DirectDraw has extensive global, per-model, packet, callback, and GX effects. ModelX construction allocates state and performs GX command setup; its derived destructor body is empty. Exact DOL ownership/cache proof, a verified model-init allocation boundary, and lifecycle teardown remain unresolved. The xdelta deployment option does not remove these blockers.
