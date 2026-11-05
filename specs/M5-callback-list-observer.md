# M5 callback-list observer specification

Status: approved implementation scope for an opt-in, read-only PAL RMGP01 Rev 0 observer. No callback invocation, callback-record enumeration, game writes, hooks, injection, spawning, Dolphin changes, or M4 changes.

## 1. Objective

Report bounded movement, calc-animation, and Penguin draw-buffer registration metadata for the active scene. Use the existing validated stock Dolphin MEM1 reader and fail closed on unknown layouts or transitions.

## 2. Verified research

- The scene pointer is resolved through the existing validated GameSystem → scene-controller → active-scene chain. PAL `Scene::initNameObjListExecutor` (`0x80343830`, DOL file `0x33ED70`) allocates a 0x14-byte executor object, installs `SceneNameObjListExecutor` vptr `0x805BDCEC`, initializes it through `NameObjListExecutor::init` (`0x8026BF90`), and stores the pointer at `Scene+0x08`. Existing PAL symbol/DOL map validation pins these functions and the scene chain.
- Petari `NameObjListExecutor.hpp` declares `mBufferHolder` at `+0x04`, movement list at `+0x08`, calcAnim list at `+0x0C`, and draw list at `+0x10`.
- PAL `SceneNameObjListExecutor::initMovementList` (`0x80345A30`) constructs 46 category records; `initCalcAnimList` (`0x80345AA4`) constructs 22. For the Penguin NPC route, PAL `connectToSceneNpc` uses movement category `0x28`, calcAnim category `0x06`, draw-buffer type `0x10`, and draw category `-1`.
- `NameObjCategoryList::incrementCheck` (`0x80261870`) indexes category records at 20-byte stride and increments `CategoryInfo+0x10`. `allocateBuffer` (`0x8026188C`) allocates each callback pointer array from the prepared check count. `add` (`0x80261914`) reads/writes array pointer `+0`, capacity `+4`, count `+8`, increments count and stores the callback pointer without a capacity guard. The observer reads only those metadata words and the check-count; it never reads array elements.
- PAL `SceneNameObjListExecutor::initCalcViewAndEntryList` (`0x80345B18`, file `0x341058`) constructs a `DrawBufferHolder` of size `0x30`, stores it at executor `+0x04`, and calls `DrawBufferHolder::initTable` (`0x80396D70`) with table `0x80538A78` and count `0x29`. Petari `DrawBufferHolder.hpp` fixes `mBufferGroups` at `+0` and three camera executor vectors at `+0x08`; the table has 41 indexed groups. Petari `AssignableArray` is pointer plus maximum size at `+0/+4`, with no separate used count, so the observer reports table capacity and does not invent a live group count. The PAL row for type `0x10` at `0x80538AE8` (base plus row 7 x 16; DOL file `0x534BE8`) is `{type=0x10, executorCapacity=0x50, lightType=1, cameraType=0}`. Petari `DrawBufferGroup.hpp` fixes executor vectors at `+0` and active-executor vectors at `+0x0C`, followed by camera/light fields. Each `DrawBufferGroup` is 0x24 bytes. `Vector` stores backing AssignableArray at `+0` and used count at `+8`; backing maximum is at `+4`.
- PAL `DrawBufferGroup::registerDrawBuffer` (`0x80396610`), `DrawBufferExecuter::add` (`0x803963B8`), and `DrawBufferHolder::active` append to separately bounded arrays. The group metadata observer does not follow individual DrawBufferExecuter pointers, model names, or actor arrays.
- Live execute-holder values supplied by the user (PID 114728) are recorded in [Penguin rendering research](../docs/references/penguin-rendering-path.md). They are not callback-list measurements.

## 3. Functional requirements

- Resolve and recheck the complete scene/group/execute-holder chain for each observation.
- Validate SceneNameObjListExecutor vptr and scene ownership; validate list pointers, category spans, the type-0x10 draw-buffer group, all advertised array spans, count/capacity relations, and scheduler pending flags.
- Sample metadata twice and reject any change, invalid pointer, transitional pending operation, or inconsistent scene identity.
- Report movement `0x28`, calcAnim `0x06`, the 83-slot draw-category table identity, and draw-buffer group `0x10` identities/counts/capacities/headroom. State explicitly that draw category is `-1` for the known Penguin route, so no draw-category occupancy is reported.
- Do not read callback records or draw actor entries, and do not infer that headroom authorizes registration.

## 4. Technical design

The observer follows only statically validated pointers. Category metadata uses the PAL-fixed 20-byte `CategoryInfo` stride. A category list is accepted only when its PAL category table size, prepared count, and allocated capacity agree and used count does not exceed capacity. The draw-category table size is checked against the verified PAL initialization count of 83, but no draw-category entry is read because the known Penguin route supplies draw category `-1`. Draw-buffer metadata uses the 0x24-byte group stride and the PAL type-0x10 table row. `AssignableArray` contains a backing pointer and maximum size, with no separate live count; the observer reports its capacity (41), not a fabricated used count. Both executor and active-executor vector spans and their actual counts are checked. All guest pointer arithmetic uses checked operations and canonical MEM1 bounds.

The snapshot is repeated from the root scene chain. All fields, identities, and pending flags must match across passes; pending flags must be zero. PAL function bytes are checked live against the validated DOL before and after sampling. This is a read-only observation and runs only on explicit command invocation.

## 5. Dependencies

- Exact PAL RMGP01 Rev 0 DOL and Petari symbol map.
- Existing Windows stock Dolphin 2609a validation and bounded read-only MEM1 reader.
- Existing AllStars patch/profile checks used by the other PAL observers.
- Petari NameObj scheduler and draw-buffer layouts, cross-checked against PAL symbols and DOL instructions.

## 6. Risks

- A valid metadata snapshot does not prove allocated memory ownership beyond canonical MEM1 mapping.
- Camera/category table layouts may differ on another revision; the command must reject any non-PAL-RMGP01-Rev-0 inputs.
- Counts can be internally plausible while the game is mid-transition; double-sampling and zero pending flags reduce but do not eliminate that risk.
- Draw-buffer executor headroom is not per-model actor-array headroom. The individual executor and model-resource path remains intentionally unobserved.
- Static append paths do not guard every capacity. A successful observation is not permission to register an actor.

## 7. Acceptance criteria

- Unit tests cover bounds, malformed/null pointers, capacity/count mismatches, pending transitions, changed scene identity, and valid metadata.
- PAL input/map validation passes; the observer uses no write API and reads no callback/actor array entries.
- Documentation reports only static or user-observed evidence accurately. No live result is claimed until manually run against Dolphin.

## 8. Testing strategy

Use synthetic `GuestMemory` fixtures for all pointer and metadata conditions. Run `cargo fmt`, workspace tests/build, PAL map validation, and `git diff --check`. For live validation, the user manually runs `observe-callback-lists` on stock Dolphin 2609a with PAL RMGP01 Rev 0 in a stable playable scene, then repeats after a settled scene transition. Compare metadata only; never use the output to authorize spawning.

Petari source references: [`Array.hpp`](https://github.com/SMGCommunity/Petari/blob/master/include/Game/Util/Array.hpp), [`NameObjListExecutor.hpp`](https://github.com/SMGCommunity/Petari/blob/master/include/Game/NameObj/NameObjListExecutor.hpp), [`NameObjCategoryList.cpp`](https://github.com/SMGCommunity/Petari/blob/master/src/Game/NameObj/NameObjCategoryList.cpp), [`DrawBufferHolder.hpp`](https://github.com/SMGCommunity/Petari/blob/master/include/Game/System/DrawBufferHolder.hpp), [`DrawBufferHolder.cpp`](https://github.com/SMGCommunity/Petari/blob/master/src/Game/System/DrawBufferHolder.cpp), [`DrawBufferGroup.hpp`](https://github.com/SMGCommunity/Petari/blob/master/include/Game/System/DrawBufferGroup.hpp), and [`DrawBufferExecuter.hpp`](https://github.com/SMGCommunity/Petari/blob/master/include/Game/System/DrawBufferExecuter.hpp). The local DOL/symbol map validates PAL addresses/table bytes; Petari provides matching field semantics.
