# Penguin live-object investigation (M5 Phase 1.7)

Status: **group path and Penguin class candidates user-validated; opt-in read-only position observer implemented**. It reads registered actor pointers, candidate vptrs, and only the verified `LiveActor::mPosition` vector at `+0x0C`. It does not read names, models, scheduler state, or other actor fields. No game writes, hooks, injected code, actor creation, profile changes, or Dolphin changes are made.

## Evidence categories

### Verified live by the user

On Dolphin 2609a with PAL RMGP01 Rev 0, the user reports:

- guest `0x80000000` returned `524d47503031` (`RMGP01`);
- guest `0x800103A0` returned `9421fe907c0802a69001017439610170`, matching the validated PAL DOL.

These validate the explicit read path and two known bytes/identity checks. They do not validate heap pointers, actor layout, or a Penguin.

The user later supplied this live observation from Dolphin 2609a, PAL RMGP01 Rev 0, Beach Bowl Galaxy / Sunken Treasure:

- AllLiveActorGroup object: `0x80FA07D8`;
- vptr: `0x8057327C`;
- capacity: `0xA00` (2560);
- registered count: `0x48C` (1164), distinct from capacity;
- owned array pointer: `0x80FA07F4`;
- two manually inspected registered entries were identified as `MessageSensorHolder` and `CenterScreenBlur`.

These are **user-observed live values**, not a trace independently captured by this tool. No entry indexes or addresses were supplied for those two entries. The values confirm the static group metadata for that session; they do not identify a Penguin or establish pause/transition lifetime. The observed array pointer is 28 bytes after the group object; this adjacency is recorded only as an observation and is not assumed by the reader.

The user later reported a successful Penguin-vptr observation in that PAL scene: scene `0x80F8EF70`, group `0x80FA07D8`, capacity 2560, count 1164, and 16 reported matches for `__vt__7Penguin = 0x8059C884` at indexes within `1013..1058`. This is user-observed evidence; the CLI has not independently reproduced it. No actor addresses or coordinates accompanied the report, and the index range does not mean every index matched.

### Static PAL/source evidence

The exact PAL DOL and Petari symbol file pass `validate-mario-map`. The existing `player_probe.rs` validates the PAL Mario-root chain's instruction signatures and uses these fields:

`GameSystem singleton (0x806A1228) -> GameSystem +0x24 -> SceneController +0xAC -> Scene +0x10 -> SceneObjHolder`.

The Petari scene-object enum identifies `SceneObj_AllLiveActorGroup` as ID `0x30`, so its pointer slot is statically `SceneObjHolder + 0xC0`. The matching source says `AllLiveActorGroup` derives from `LiveActorGroup`, initializes a capacity of 2560, and `LiveActorGroup::getActor(index)` returns `mObjects[index]`. These establish a plausible discovery route, not a live object.

The PAL symbol file names `getAllLiveActorGroup`, the group constructors/accessors, `Penguin` constructor/init, and `__vt__7Penguin` at `0x8059C884`. The Penguin constructor's verified instruction sequence stores that exact address in `this + 0`; this resolves the vptr address-point uncertainty for this build. This remains static evidence until a live candidate is observed.

The exact PAL DOL places `getActor__14LiveActorGroupCFi` at `0x80166110`, text section 1 file offset `0x161650`, size `0x10`. Its bytes are `80 63 00 14 54 80 10 3A 7C 63 00 2E 4E 80 00 20`, which decode to `lwz r3,0x14(r3); rlwinm r0,r4,2,0,29; lwzx r3,r3,r0; blr`. This statically shows that the accessor loads an array pointer from `this + 0x14` and indexes four-byte entries.

### Penguin vptr address-point check (static PAL evidence)

The PAL symbol map identifies `__ct__7PenguinFPCc` at `0x802790E0` (text section 1 file offset `0x274620`, size `0x4C`) and `__vt__7Penguin` at `0x8059C884` (data symbol size `0x78`). Constructor bytes at `+0x10` through `+0x2B` are `7C 7F 1B 78 48 02 13 BD 3C 60 80 5A 38 80 FF FF 38 63 C8 84 38 00 00 00 90 7F 00 00`: `mr r31,r3` saves `this`; after the base constructor, `lis/addi` forms `0x8059C884` in r3; `stw r3,0(r31)` stores it at object offset zero. Thus the primary Penguin vptr comparison is exactly `0x8059C884`; the constructor resolves the address-point uncertainty for this PAL build. The observer requires the exact PAL DOL/symbol fingerprint and these constructor bytes, and accepts no nearby vptr values.

### Position-field verification and observer limits

The exact Petari PAL symbols identify `__ct__9LiveActorFPCc` at `0x80165204`, size `0xE8`. Its DOL bytes at function `+0x3C` / file offset `0x160780` are `D0 5E 00 0C D0 5E 00 10 D0 5E 00 14`, three `stfs` operations initializing a three-float member at `this+0x0C..+0x17`. Petari's `LiveActor` layout names that member `mPosition`. The observer verifies these bytes and reads one 12-byte vector at actor `+0x0C`, decoding finite big-endian floats. It reports these as the actor's game-space position components; this CLI has not yet validated their live movement behavior.

Before reading coordinates it checks the candidate vptr again. After reading, it recomputes the entire scene/group chain and rescans registered entries; any scene, group, entry, or identity change rejects the whole sample. This guards against stale/recycled pointers, but is not an atomic frame snapshot: the running game may update coordinates during the read.

### SceneObjHolder root and object layout (static PAL evidence)

There is **no standalone SceneObjHolder singleton** in the exact Petari symbol map. It is allocated per active `Scene` and stored at `Scene + 0x10`. The global accessor reaches it through the GameSystem singleton:

1. `SingletonHolder<GameSystem>::sInstance` is in `.sbss` at `0x806A1228`. PAL `__init_registers` at `0x800041A8` sets `r13 = 0x806A4CA0` (`lis r13,0x806A; addi r13,r13,0x4CA0`).
2. `MR::getSceneObjHolder()` at `0x80344AC4` begins with `lwz r3,-0x3A78(r13)`. `0x806A4CA0 - 0x3A78 = 0x806A1228`, so this loads that GameSystem singleton pointer. It then loads `GameSystem + 0x24` and calls `GameSystemSceneController::getSceneObjHolder()`.
3. `GameSystemSceneController::getSceneObjHolder()` at `0x8039D468` is exactly `lwz r3,0xAC(r3); lwz r3,0x10(r3); blr`: scene controller -> active Scene -> per-scene SceneObjHolder.
4. `Scene::initSceneObjHolder()` at `0x80343890` requests `0x1EC` bytes, calls the allocator and stores the returned pointer at `Scene + 0x10`. `0x1EC / 4 = 123` pointer slots. `SceneObjHolder`'s constructor initializes 123 slots; `getObj(id)` indexes the inline array at `this + id*4`.
5. `getAllLiveActorGroup()` at `0x8015CDB8` calls `MR::getSceneObjHolder()`, passes ID `0x30` to `SceneObjHolder::getObj`, and returns that slot. ID `0x30` therefore maps to holder offset `0xC0`.

These addresses are code/data in the PAL DOL. The pointers returned by these loads are live emulated heap/object addresses and must be obtained afresh from the current process; do not substitute the static symbol addresses for them.

The executable locations were mapped through the validated DOL text-section table, not inferred from the symbols alone:

| Function | PAL address | DOL file offset | Relevant verified instruction bytes |
|---|---:|---:|---|
| `__init_registers` | `0x800041A8` | `0x2A8` | at `+0x84`: `3D A0 80 6A`; at `+0x88`: `61 AD 4C A0` (`r13 = 0x806A4CA0`) |
| `MR::getSceneObjHolder` | `0x80344AC4` | `0x340004` | `80 6D C5 88 80 63 00 24 48 05 89 9C` |
| `GameSystemSceneController::getSceneObjHolder` | `0x8039D468` | `0x3989A8` | `80 63 00 AC 80 63 00 10 4E 80 00 20` |
| `Scene::initSceneObjHolder` | `0x80343890` | `0x33EDD0` | begins `... 38 60 01 EC ...`; stores allocated pointer at `Scene + 0x10` |
| `SceneObjHolder` constructor | `0x803449F4` | `0x33FF34` | `38 00 00 7B ... 7C A3 21 2E ...` (123 slots, zeroed in a loop) |
| `SceneObjHolder::getObj` | `0x80344A70` | `0x33FFB0` | `54 80 10 3A 7C 63 00 2E 4E 80 00 20` |
| `MR::getAllLiveActorGroup` | `0x8015CDB8` | `0x1582F8` | calls holder getter, uses ID `0x30`, calls `getObj` |

The constructor/array functions used for the group offsets are also mapped in this DOL: AllLiveActorGroup ctor `0x8015CCD8` / file `0x158218`; LiveActorGroup ctor `0x801660D0` / file `0x161610`; NameObjGroup ctor `0x8026B85C` / file `0x266D9C`; `initObjArray` `0x8026B9AC` / file `0x266EEC`; `registerObj` `0x8026B930` / file `0x266E70`; NameObjGroup dtor `0x8026B8C0` / file `0x266E00`; and `getActor` `0x80166110` / file `0x161650`. The validated DOL fingerprint binds these section/file mappings to PAL RMGP01 Rev 0.

### Group count, capacity, and owned array (static PAL evidence)

The relevant exact PAL functions and instructions show:

- `AllLiveActorGroup` constructor at `0x8015CCD8` sets the capacity argument to `0x0A00` (2560) and calls `LiveActorGroup` with the unchanged `this` pointer. `LiveActorGroup` calls `NameObjGroup` without adjusting `this`, then installs its vtable. `AllLiveActorGroup` installs its vtable at object offset `+0` and initializes its separate actor-system-info progress member at `+0x18`.
- `NameObjGroup::initObjArray` at `0x8026B9AC` stores the constructor capacity at `+0x0C`, allocates `capacity * 4` bytes, stores the returned pointer at `+0x14`, and initializes each entry to null. Its destructor reads `+0x14` and calls the deallocator. The group owns this pointer array through the `NameObjGroup` base lifetime.
- `NameObjGroup::registerObj` at `0x8026B930` reads the current registered-object count at `+0x10`, stores the new object at `array + count*4`, then increments `+0x10`.
- Thus the statically derived layout is: `+0x00` vptr; `+0x0C` capacity (`0xA00` for this group); `+0x10` registered count (dynamic and not equal to capacity); `+0x14` owned pointer-array address; `+0x18` AllLiveActorGroup actor-system-info initialization progress, **not** the registered count.
- The AllLiveActorGroup constructor writes its vptr symbol value `0x8057327C` at `this + 0`. This gives a PAL-specific live type check for the group object before reading its count or array field.

The array's allocation size is statically `capacity * 4 = 10,240` bytes. The user's live observation confirms the metadata values for one session. `capacity` is not `count`; the observer reads them separately and rejects `count > capacity`.

The allocation/destruction call targets are in the same verified DOL: `initObjArray` calls `__nwa__FUl` at `0x80409B38` after multiplying capacity by four; `NameObjGroup::~NameObjGroup` loads the array from `+0x14` and calls `__dla__FPv` at `0x80409B64`. This supports array ownership by the NameObjGroup base. It does not establish a safe lifetime beyond the owning scene.

### Unverified

- Group values and 16 Penguin-class matches are user-observed; this tool has not independently reproduced them.
- No actor addresses or positions were included in the report. There is no reliable identifier selecting the visible Beach Bowl placement among multiple class matches.
- Penguin name storage, model pointer, scheduler membership, and lifetime through pause/transition remain unverified.

The source references are Petari's [`AllLiveActorGroup.cpp`](https://github.com/SMGCommunity/Petari/blob/master/src/Game/LiveActor/AllLiveActorGroup.cpp) and [`LiveActorGroup.cpp`](https://github.com/SMGCommunity/Petari/blob/master/src/Game/LiveActor/LiveActorGroup.cpp). The PAL constructors and accessors derive the group fields and array ownership; they do not identify a Penguin entry. Capacity 2560 is not a live count.

## Safe candidate path and stop point

The narrow path to test is:

1. Read the GameSystem singleton pointer at the statically verified PAL BSS address `0x806A1228`.
2. After `inspect-memory` validates it as an aligned MEM1 pointer, read the pointer at `GameSystem + 0x24`.
3. Resolve `SceneController + 0xAC`, then `Scene + 0x10`, validating each returned pointer before using it as the next base.
4. Read only the pointer at `SceneObjHolder + 0xC0`, the AllLiveActorGroup slot.
5. Read the candidate group's first word and require the validated `AllLiveActorGroup` vptr `0x8057327C`. Stop on mismatch.
6. Only after the vptr matches, read capacity at `+0x0C` (must be `0xA00`), registered count at `+0x10` (must be `<= capacity`), and array pointer at `+0x14` (non-null, aligned, canonical MEM1). Checked arithmetic must confirm `array + capacity*4 <= 0x81800000`.
7. Re-read the chain and metadata after enumeration; discard results if the scene, holder, group, array, count, or capacity changed.
8. The bounded entry observer authorized below reads only `[0,count)` pointers and, after validating each pointer, the first 4-byte object word. Invalid entries are counted and skipped without dereference. Compare that word exactly with constructor-verified `0x8059C884`; stop on any failed validation or mapping error.

Every command below opens a new bounded read-only view and rechecks the Dolphin PID/executable/session, mapping extent, `RMGP01`, PAL code signatures, local DOL fingerprint, and mandatory AllStars compatibility. `pointer` decoding rejects null only as unavailable and rejects nonaligned/out-of-range non-null pointers. It never follows a pointer itself. The PowerShell helper follows only the explicitly reviewed offsets above, one read at a time, and recomputes the path on every sample.

### Bounded entry-observation design review / authorization

The live group metadata supplied above matches the static PAL layout. This user request explicitly authorizes this one-shot, read-only observer:

1. Revalidate Dolphin PID/executable/session, 2609a codehandler, exact PAL RMGP01 Rev 0 DOL and symbols, mandatory AllStars input, live `RMGP01`, and PAL runtime signatures.
2. Recompute `GameSystem -> SceneController -> Scene -> SceneObjHolder -> AllLiveActorGroup` from the root; no heap pointer is reused between invocations.
3. Require group vptr `0x8057327C`, capacity `0xA00`, `count <= capacity`, aligned canonical MEM1 array pointer, and checked full-capacity span `array + capacity*4 <= 0x81800000`.
4. Read only `[0,count)` entries in bounded chunks. Skip null/misaligned/out-of-range actor pointers. For exact Penguin matches, recheck vptr and read only the 12-byte position at actor `+0x0C`; reject non-finite components.
5. Compare exactly to `0x8059C884`. Emit at most 16 matches containing index, actor pointer, vptr, and X/Y/Z; report aggregate null/invalid/nonmatching counts. Do not print nonmatching actor pointers.
6. Re-read the array and complete scene/group metadata. If any changed, discard all candidates and report an unstable scene/group. No candidate pointer survives the command.

The implementation is the separate opt-in `observe-penguins` command. It does not write Dolphin memory, install hooks/injection, create actors, modify Dolphin, change profiles, or affect M4/network behavior. Candidate pointers and positions exist only for the command invocation; it does not select a specific visible placement.

## Interactive PowerShell observation

Use the existing isolated Dolphin profile and a Dolphin process on the interactive desktop. Set `$DolphinPid` to that exact process. Enter **Beach Bowl Galaxy > Sunken Treasure**, bring a Penguin into view, then run the dedicated observer:

```powershell
Set-Location C:\Users\maxim\Projects\multigalaxy1
$DolphinPid = 12345 # replace with the isolated Dolphin PID
cargo run -q -p multigalaxy -- observe-penguins .\development.local.toml $DolphinPid
```

Expected output is one bounded summary with scene/group addresses, capacity/count, aggregate entry counts, and up to 16 records containing index, actor pointer, vptr, and X/Y/Z. A zero-match result means no exact Penguin vptr was found among registered entries; it does not prove no Penguin is visible. Wrong game, invalid metadata, unsafe pointer, non-finite coordinate, mapping failure, or unstable scene/entry list rejects the whole sample. At title/menu, an unavailable chain is expected; do not substitute addresses.

Run the command once with a Penguin visible, once while paused, once after resuming, and immediately after transitioning out of the mission. Save the command output each time. The command recomputes from the singleton on every invocation and performs an internal second observation before returning; it retains no pointers. A changed scene or entry list invalidates the whole result. Stable roots during pause or changed values during transitions are observations only; they do not establish a specific Penguin's lifetime.

Do not run the Gecko bridge probe for this experiment; it performs ACK writes. This command uses stock Dolphin's existing read-only MEM1 mapping. Do not use `probe-mario` GDB or scan memory. The observer reads only the registered array, candidate vptrs, and verified position vectors.

## Smallest next experiment to unblock Penguin identity

1. Run `observe-penguins` in the target mission while walking/jumping nearby, paused/resumed, and after a scene transition.
2. Compare coordinates across runs. An exact vptr identifies class only; it does not select a visible placement or prove actor lifetime.

Only the registered-entry scan, vptr check, and verified 12-byte position read are authorized. If a layout check fails, stop and resolve PAL evidence rather than relaxing offsets interactively.
