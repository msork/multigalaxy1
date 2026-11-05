# Penguin live-object investigation (M5 Phase 1.7)

Status: **live actor identity not established**. The read-only `inspect-memory` path now passes the user's two live checks, but there was no Dolphin process available during this investigation to observe a Penguin. No actor object fields are read in this phase. No game writes, hooks, injected code, actor creation, profile changes, or Dolphin changes were made.

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

The exact PAL DOL places `getActor__14LiveActorGroupCFi` at `0x80166110`, text section 1 file offset `0x161650`, size `0x10`. Its bytes are `80 63 00 14 54 80 10 3A 7C 63 00 2E 4E 80 00 20`, which decode to `lwz r3,0x14(r3); rlwinm r0,r4,2,0,29; lwzx r3,r3,r0; blr`. This statically shows that the accessor loads an array pointer from `this + 0x14` and indexes 4-byte entries. It does not by itself prove the base-subobject offset inside the live AllLiveActorGroup, the array allocation/bounds, the live object type, or which entry is the Beach Bowl Penguin. Those remain stop conditions; this field has not been read at runtime.

### Unverified

- No live GameSystem, SceneController, Scene, SceneObjHolder, group, or Penguin pointer was observed in this turn.
- The accessor's `this + 0x14` array-pointer load and 4-byte stride are verified from PAL instructions. The `NameObjGroup` base-subobject offset in `AllLiveActorGroup`, array allocation/bounds, live count offset, and safe entry enumeration limit are not yet verified.
- No reliable identifier currently selects the Beach Bowl Sunken Treasure Penguin among other group members.
- Penguin live vtable address-point semantics, name storage, model pointer, scheduler membership, and lifetime through pause/transition remain unverified.

The source references are Petari's [`AllLiveActorGroup.cpp`](https://github.com/SMGCommunity/Petari/blob/master/src/Game/LiveActor/AllLiveActorGroup.cpp) and [`LiveActorGroup.cpp`](https://github.com/SMGCommunity/Petari/blob/master/src/Game/LiveActor/LiveActorGroup.cpp). In particular, `getActor` indexes `mObjects` directly; without the inherited `NameObjGroup` layout, an external reader cannot safely calculate that array. Capacity 2560 is not a live count and is not permission to enumerate.

## Safe candidate path and stop point

The narrow path to test is:

1. Read the GameSystem singleton pointer at the statically verified PAL BSS address `0x806A1228`.
2. After `inspect-memory` validates it as an aligned MEM1 pointer, read the pointer at `GameSystem + 0x24`.
3. Resolve `SceneController + 0xAC`, then `Scene + 0x10`, validating each returned pointer before using it as the next base.
4. Read only the pointer at `SceneObjHolder + 0xC0`, the AllLiveActorGroup slot.
5. **Stop there for this experiment.** Do not read the candidate group's first word, `+0x14` array field, count, array contents, or any presumed actor entry until the live group type/base relationship and allocation bounds have been validated. The accessor disassembly is static evidence for one base-class field, not proof that the complete object pointer uses that base address.

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
    Write-Host ("sample_time={0} scene_chain=validated group_candidate=0x{1:x8} actor_entries=not_read" -f $sampleTime, $group)
}

Sample-PenguinDiscoveryRoot
```

Expected output is a set of `memory_read=validated ... pointer=0x...` records followed by `scene_chain=validated group_candidate=0x........ actor_entries=not_read`. Runtime pointer values are dynamic; no fixed values are expected. If any link is null, out of range, or fails validation, the sample stops at that link. At title/menu, an unavailable link is acceptable; do not substitute a guessed address.

Repeat `Sample-PenguinDiscoveryRoot` once with a Penguin visible, once while paused, once after resuming, and immediately after transitioning out of the mission. Save the timestamp and complete console output from each sample. The script recomputes from the singleton each time and caches no pointers. A changed scene or group candidate invalidates the previous values. Stable root pointers during pause or changed values during transitions are observations only; neither proves Penguin membership or actor lifetime.

Do not run the Gecko bridge probe for this experiment; it performs ACK writes. The `inspect-memory` commands above are read-only. Do not use `probe-mario` GDB or scan memory. The candidate actor list remains unread.

## Smallest next experiment to unblock Penguin identity

1. Run the root-only PowerShell sample above in the target mission, paused/resumed, and after a scene transition. This establishes whether the static root path yields live, validated pointers in the user's current runtime.
2. Obtain the exact Petari PAL `NameObjGroup`/`LiveActorGroup` headers, or derive and verify the relevant base offsets, count bound, and indexing instructions from the exact PAL DOL (`registerObj`, `getObjNum`, `getActor`) against the pinned symbol map. The `getActor` instruction bytes and file offset above are already verified; record remaining instruction evidence before any group-body read.
3. Verify the live group's type using the correct PPC vtable address-point semantics and validate the bounded count/array span. Only then design a read-only, capped entry observer.
4. For Penguin identity, correlate candidate entries with a verified type/name field or exact constructor registration path. Track only that identified entry across pause/resume/transition; discard all pointers when the scene changes.

No actor scan or actor-field read is authorized by this phase. If step 2 cannot establish the object layout, stop and obtain the matching source/header or better static analysis evidence rather than trying offsets interactively.
