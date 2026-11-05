# Penguin live-object investigation (M5 Phase 1.7)

Status: **static group-discovery path derived; live group/Penguin observation pending**. The read-only `inspect-memory` path passed the user's game-ID and code-signature checks, but no Dolphin process was available during this investigation to observe the live group or a Penguin. No actor-array entries or actor object fields are read in this phase. No game writes, hooks, injected code, actor creation, profile changes, or Dolphin changes were made.

## Evidence categories

### Verified live by the user

On Dolphin 2609a with PAL RMGP01 Rev 0, the user reports:

- guest `0x80000000` returned `524d47503031` (`RMGP01`);
- guest `0x800103A0` returned `9421fe907c0802a69001017439610170`, matching the validated PAL DOL.

These validate the explicit read path and two known bytes/identity checks. They do not validate heap pointers, actor layout, or a Penguin.

### Static PAL/source evidence

The exact PAL DOL and Petari symbol file pass `validate-mario-map`. The existing `player_probe.rs` validates the PAL Mario-root chain's instruction signatures and uses these fields:

`GameSystem singleton (0x806A1228) -> GameSystem +0x24 -> SceneController +0xAC -> Scene +0x10 -> SceneObjHolder`.

The Petari scene-object enum identifies `SceneObj_AllLiveActorGroup` as ID `0x30`, so its pointer slot is statically `SceneObjHolder + 0xC0`. The matching source says `AllLiveActorGroup` derives from `LiveActorGroup`, initializes a capacity of 2560, and `LiveActorGroup::getActor(index)` returns `mObjects[index]`. These establish a plausible discovery route, not a live object.

The PAL symbol file names `getAllLiveActorGroup`, the group constructors/accessors, `Penguin` constructor/init, and `__vt__7Penguin` at `0x8059C884`. These are code/data symbols in the DOL, not heap actor addresses. A vtable symbol address alone does not establish the exact ABI address-point or prove that the first word of a live object equals that value.

The exact PAL DOL places `getActor__14LiveActorGroupCFi` at `0x80166110`, text section 1 file offset `0x161650`, size `0x10`. Its bytes are `80 63 00 14 54 80 10 3A 7C 63 00 2E 4E 80 00 20`, which decode to `lwz r3,0x14(r3); rlwinm r0,r4,2,0,29; lwzx r3,r3,r0; blr`. This statically shows that the accessor loads an array pointer from `this + 0x14` and indexes four-byte entries.

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

The array's allocation size is statically `capacity * 4 = 10,240` bytes. A live read of the pointer plus checked arithmetic can verify that the claimed full capacity span lies in the conservative MEM1 range, but this phase will not read any array entries. `capacity` is not `count`; count must be read separately and rejected if greater than capacity. The exact instructions establish field semantics, while the matching Petari source explains the group/accessor lifecycle. These facts have not yet been observed in live heap memory.

The allocation/destruction call targets are in the same verified DOL: `initObjArray` calls `__nwa__FUl` at `0x80409B38` after multiplying capacity by four; `NameObjGroup::~NameObjGroup` loads the array from `+0x14` and calls `__dla__FPv` at `0x80409B64`. This supports array ownership by the NameObjGroup base. It does not establish a safe lifetime beyond the owning scene.

### Unverified

- No live GameSystem, SceneController, Scene, SceneObjHolder, group, or Penguin pointer/metadata was observed in this turn.
- No reliable identifier currently selects the Beach Bowl Sunken Treasure Penguin among group entries.
- No reliable identifier currently selects the Beach Bowl Sunken Treasure Penguin among other group members.
- Penguin live vtable address-point semantics, name storage, model pointer, scheduler membership, and lifetime through pause/transition remain unverified.

The source references are Petari's [`AllLiveActorGroup.cpp`](https://github.com/SMGCommunity/Petari/blob/master/src/Game/LiveActor/AllLiveActorGroup.cpp) and [`LiveActorGroup.cpp`](https://github.com/SMGCommunity/Petari/blob/master/src/Game/LiveActor/LiveActorGroup.cpp). The PAL constructors and accessors now statically derive the group fields and array ownership; they do not identify a Penguin entry. Capacity 2560 is not a live count and is not permission to enumerate.

## Safe candidate path and stop point

The narrow path to test is:

1. Read the GameSystem singleton pointer at the statically verified PAL BSS address `0x806A1228`.
2. After `inspect-memory` validates it as an aligned MEM1 pointer, read the pointer at `GameSystem + 0x24`.
3. Resolve `SceneController + 0xAC`, then `Scene + 0x10`, validating each returned pointer before using it as the next base.
4. Read only the pointer at `SceneObjHolder + 0xC0`, the AllLiveActorGroup slot.
5. Read the candidate group's first word and require the validated `AllLiveActorGroup` vptr `0x8057327C`. Stop on mismatch.
6. Only after the vptr matches, read capacity at `+0x0C` (must be `0xA00`), registered count at `+0x10` (must be `<= capacity`), and array pointer at `+0x14` (non-null, aligned, canonical MEM1). Checked arithmetic must confirm `array + capacity*4 <= 0x81800000`.
7. **Stop after metadata.** Do not read array entries or actor fields until a separate bounded entry-observation step is reviewed. The vptr/capacity/count/array checks can validate the group container but cannot identify a Penguin.

Every command below opens a new bounded read-only view and rechecks the Dolphin PID/executable/session, mapping extent, `RMGP01`, PAL code signatures, local DOL fingerprint, and mandatory AllStars compatibility. `pointer` decoding rejects null only as unavailable and rejects nonaligned/out-of-range non-null pointers. It never follows a pointer itself. The PowerShell helper follows only the explicitly reviewed offsets above, one read at a time, and recomputes the path on every sample.

## Interactive PowerShell observation

Use the existing isolated Dolphin profile and a Dolphin process on the interactive desktop. Set `$DolphinPid` to that exact process. Enter **Beach Bowl Galaxy > Sunken Treasure**, bring a Penguin into view, then run:

```powershell
Set-Location C:\Users\maxim\Projects\multigalaxy1
$DolphinPid = 12345 # replace with the isolated Dolphin PID

function Read-GuestPointer([string]$AddressHex, [string]$Label) {
    $addressValue = [Convert]::ToUInt64(($AddressHex -replace '^0[xX]'), 16)
    $address = '0x{0:x8}' -f $addressValue
    $output = & cargo run -q -p multigalaxy -- inspect-memory .\development.local.toml $DolphinPid $address 4 pointer 2>&1
    if ($LASTEXITCODE -ne 0) {
        $output | ForEach-Object { Write-Host $_ }
        throw "Read failed at $address ($Label); stop this sample."
    }
    $line = $output | Where-Object { "$_" -match '^memory_read=validated .* pointer=' } | Select-Object -First 1
    if (-not $line) { throw "No validated pointer result for $Label at $address; stop this sample." }
    if ("$line" -match 'pointer=null') {
        Write-Host "$Label=null (unavailable)"
        return $null
    }
    if ("$line" -notmatch 'pointer=(0x[0-9a-fA-F]{8})') { throw "Malformed pointer output for $Label; stop this sample." }
    $pointer = [Convert]::ToUInt64($Matches[1].Substring(2), 16)
    Write-Host ("{0}=0x{1:x8}" -f $Label, $pointer)
    return $pointer
}

function Read-GuestU32([string]$AddressHex, [string]$Label) {
    $addressValue = [Convert]::ToUInt64(($AddressHex -replace '^0[xX]'), 16)
    $address = '0x{0:x8}' -f $addressValue
    $output = & cargo run -q -p multigalaxy -- inspect-memory .\development.local.toml $DolphinPid $address 4 u32 2>&1
    if ($LASTEXITCODE -ne 0) {
        $output | ForEach-Object { Write-Host $_ }
        throw "Read failed at $address ($Label); stop this sample."
    }
    $line = $output | Where-Object { "$_" -match '^memory_read=validated .* u32=' } | Select-Object -First 1
    if (-not $line -or "$line" -notmatch 'u32=0x([0-9a-fA-F]{8})') { throw "No validated u32 result for $Label at $address; stop this sample." }
    $value = [Convert]::ToUInt32($Matches[1], 16)
    Write-Host ("{0}=0x{1:x8}" -f $Label, $value)
    return $value
}

function Sample-PenguinDiscoveryRoot {
    $sampleTime = (Get-Date).ToString('o')
    $gameSystem = Read-GuestPointer '806A1228' 'game_system'
    if ($null -eq $gameSystem) { Write-Host "sample_time=$sampleTime group_slot=unavailable"; return }
    $sceneController = Read-GuestPointer ('{0:x8}' -f ($gameSystem + 0x24)) 'scene_controller'
    if ($null -eq $sceneController) { Write-Host "sample_time=$sampleTime group_slot=unavailable"; return }
    $scene = Read-GuestPointer ('{0:x8}' -f ($sceneController + 0xAC)) 'scene'
    if ($null -eq $scene) { Write-Host "sample_time=$sampleTime group_slot=unavailable"; return }
    $sceneHolder = Read-GuestPointer ('{0:x8}' -f ($scene + 0x10)) 'scene_object_holder'
    if ($null -eq $sceneHolder) { Write-Host "sample_time=$sampleTime group_slot=unavailable"; return }
    $group = Read-GuestPointer ('{0:x8}' -f ($sceneHolder + 0xC0)) 'all_live_actor_group_slot'
    if ($null -eq $group) { Write-Host "sample_time=$sampleTime group_slot=null"; return }
    $vtable = Read-GuestPointer ('{0:x8}' -f $group) 'group_vptr'
    if ($null -eq $vtable -or $vtable -ne [Convert]::ToUInt64('8057327C', 16)) {
        Write-Host ("sample_time={0} state=stop reason=group_vptr_mismatch" -f $sampleTime)
        return
    }
    $capacity = Read-GuestU32 ('{0:x8}' -f ($group + 0x0C)) 'group_capacity'
    if ($capacity -ne 0xA00) {
        Write-Host ("sample_time={0} state=stop reason=capacity_mismatch actual=0x{1:x8}" -f $sampleTime, $capacity)
        return
    }
    $count = Read-GuestU32 ('{0:x8}' -f ($group + 0x10)) 'registered_count'
    if ($count -gt $capacity) {
        Write-Host ("sample_time={0} state=stop reason=count_exceeds_capacity count=0x{1:x8} capacity=0x{2:x8}" -f $sampleTime, $count, $capacity)
        return
    }
    $array = Read-GuestPointer ('{0:x8}' -f ($group + 0x14)) 'owned_array'
    if ($null -eq $array) {
        Write-Host ("sample_time={0} state=stop reason=array_pointer_null" -f $sampleTime)
        return
    }
    $arrayEnd = [uint64]$array + ([uint64]$capacity * 4)
    $mem1End = [Convert]::ToUInt64('81800000', 16)
    if ($arrayEnd -gt $mem1End) {
        Write-Host ("sample_time={0} state=stop reason=array_capacity_span_outside_mem1" -f $sampleTime)
        return
    }
    $gameSystemAgain = Read-GuestPointer '806A1228' 'game_system_recheck'
    if ($null -eq $gameSystemAgain -or $gameSystemAgain -ne $gameSystem) { Write-Host "sample_time=$sampleTime state=stop reason=game_system_changed_during_sample"; return }
    $controllerAgain = Read-GuestPointer ('{0:x8}' -f ($gameSystemAgain + 0x24)) 'scene_controller_recheck'
    if ($null -eq $controllerAgain) { Write-Host "sample_time=$sampleTime state=stop reason=scene_controller_disappeared"; return }
    $sceneAgain = Read-GuestPointer ('{0:x8}' -f ($controllerAgain + 0xAC)) 'scene_recheck'
    if ($null -eq $sceneAgain) { Write-Host "sample_time=$sampleTime state=stop reason=scene_disappeared"; return }
    if ($controllerAgain -ne $sceneController -or $sceneAgain -ne $scene) { Write-Host "sample_time=$sampleTime state=stop reason=scene_changed_during_sample"; return }
    $holderAgain = Read-GuestPointer ('{0:x8}' -f ($sceneAgain + 0x10)) 'scene_object_holder_recheck'
    if ($null -eq $holderAgain) { Write-Host "sample_time=$sampleTime state=stop reason=scene_object_holder_disappeared"; return }
    $groupAgain = Read-GuestPointer ('{0:x8}' -f ($holderAgain + 0xC0)) 'all_live_actor_group_recheck'
    if ($null -eq $groupAgain) { Write-Host "sample_time=$sampleTime state=stop reason=group_disappeared"; return }
    if ($holderAgain -ne $sceneHolder -or $groupAgain -ne $group) { Write-Host "sample_time=$sampleTime state=stop reason=group_changed_during_sample"; return }
    Write-Host ("sample_time={0} group_layout=validated vptr=0x{1:x8} capacity=0x{2:x8} count=0x{3:x8} array=0x{4:x8} span_bytes={5} actor_entries=not_read" -f $sampleTime, $vtable, $capacity, $count, $array, ($capacity * 4))
}

Sample-PenguinDiscoveryRoot
```

Expected output is a set of `memory_read=validated ...` records followed by `group_layout=validated vptr=0x8057327c capacity=0x00000a00 count=0x........ array=0x........ span_bytes=10240 actor_entries=not_read`. Group count and heap pointers are dynamic; no fixed values are expected for them. If any link is null, out of range, or fails validation, the sample stops at that link. At title/menu, an unavailable link is acceptable; do not substitute a guessed address.

Repeat `Sample-PenguinDiscoveryRoot` once with a Penguin visible, once while paused, once after resuming, and immediately after transitioning out of the mission. Save the timestamp and complete console output from each sample. The script recomputes from the singleton each time and caches no pointers. A changed scene or group candidate invalidates the previous values. Stable root pointers during pause or changed values during transitions are observations only; neither proves Penguin membership or actor lifetime.

Do not run the Gecko bridge probe for this experiment; it performs ACK writes. The `inspect-memory` commands above are read-only. Do not use `probe-mario` GDB or scan memory. The candidate actor list remains unread.

## Smallest next experiment to unblock Penguin identity

1. Run the group-metadata PowerShell sample above in the target mission, paused/resumed, and after a scene transition. This tests the static root chain and live group layout without reading the array.
2. Review the returned live vptr/capacity/count/array pointer and checked span. Any mismatch stops the investigation; no field offset is relaxed to make the data fit.
3. Design a separately reviewed read-only entry observer only after the user supplies those outputs and a safe bound for reading individual entries is established.
4. For Penguin identity, correlate candidate entries with a verified type/name field or exact constructor registration path. Track only that identified entry across pause/resume/transition; discard all pointers when the scene changes.

No actor scan or actor-field read is authorized by this phase. If step 2 cannot establish the object layout, stop and obtain the matching source/header or better static analysis evidence rather than trying offsets interactively.
