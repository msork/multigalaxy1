# PAL actor lifecycle investigation (M5 Phase 1.5)

Status: **blocked before live actor-pointer inspection**. This work made no game-memory writes, hooks, patches, actor creation, allocator calls, scheduler changes, or Dolphin configuration changes. M4, the Gecko mailbox, AllStars, and isolated profiles are unchanged.

Evidence classification: symbol addresses and source layouts below are static evidence; the Beach Bowl/Penguin pairing is a reproducible test candidate; no live actor pointer, vtable, model pointer, scheduler entry, pause behavior, or transition behavior was observed in Dolphin during this phase.

## Target selected for a future read-only observation

Candidate: a `Penguin` NPC in the Beach Bowl Galaxy mission **Sunken Treasure**. That mission is reproducible by selecting Beach Bowl Galaxy and its first mission; gameplay references describe its beach/water setting and penguins. This is a scene-selection candidate, not a runtime confirmation that a particular `Penguin` instance is loaded in every PAL scenario.

The exact Petari PAL map contains these symbols:

| Symbol | PAL DOL address | Static meaning |
|---|---:|---|
| `__ct__7PenguinFPCc` | `0x802790E0` | Penguin constructor |
| `init__7PenguinFRC12JMapInfoIter` | `0x8027912C` | Penguin scene initialization |
| `__vt__7Penguin` | `0x8059C884` | Penguin vtable symbol |
| `__ct__17AllLiveActorGroupFv` | `0x8015CCD8` | Scene actor-group constructor |
| `initActorSystemInfo__17AllLiveActorGroupFRC12JMapInfoIter` | `0x8015CD28` | Actor-group setup |
| `getAllLiveActorGroup__2MRFv` | `0x8015CDB8` | Scene-object lookup helper |
| `__vt__17AllLiveActorGroup` | `0x8057327C` | Actor-group vtable symbol |
| `registerActor__14LiveActorGroupFP9LiveActor` | `0x8016610C` | Actor-group registration symbol |
| `getActor__14LiveActorGroupCFi` | `0x80166110` | Bounded-index accessor candidate |
| `__ct__14LiveActorGroupFPCci` | `0x801660D0` | Base actor-group constructor |

The PAL source map and DOL identity are checked by the existing map-validation command. The vtable symbol is a static address only: PowerPC ABI address-point details and the value stored in a live object's first word have not been confirmed. Do not compare live object word zero against `0x8059C884` until this is resolved.

## Known layout facts and chain leads

Source-verified Petari layout:

- `Scene` has its name-object list executor pointer at `+0x08` and `SceneObjHolder*` at `+0x10`.
- `SceneObjHolder` stores a fixed array of object pointers. `SceneObj_AllLiveActorGroup` is ID `0x30`, so its slot is at holder base `+0xC0`; `SceneObj_NameObjExecuteHolder` is ID `0x58`, slot `+0x160`.
- `LiveActor` derives from `NameObj`. Its documented transform/model fields are position `+0x0C`, rotation `+0x18`, velocity `+0x30`, gravity `+0x3C`, model manager pointer `+0x48`, and animation keeper pointer `+0x4C`. `NameObj` has the name pointer at `+0x04`, flags at `+0x08`, and executor index at `+0x0A`.
- `AllLiveActorGroup` constructs a `LiveActorGroup` named `AllLiveActorGroup` with capacity 2560. Its source iterates group entries to initialize actor system/clipping data. `LiveActorGroup::getActor(index)` returns an entry from its object array; group source also checks actor dead/alive state.
- `NameObjExecuteHolder` has an execute-info array pointer at `+0x0C`, max size `+0x10`, count `+0x14`. An execute-info begins with its executed `NameObj*`; it stores scheduler category fields after that pointer. The PAL source has separate scene and draw connect/disconnect paths.

Sources and local symbol map establish these fields and access patterns, but they do **not** establish the live `AllLiveActorGroup` allocation, its exact base-class array offsets/stride, live object-pointer membership, or scheduler array membership in the running scene. The relevant Petari base-class header is not present in this checkout, and its fetch was unavailable in this investigation. Those details must be validated from the matching header, exact PAL instruction bytes, and runtime identity checks before a host-side pointer walk is allowed.

## Safety assessment and blocker

The existing stock Dolphin shared-memory reader deliberately maps only `0x10000` bytes from the MEM1 base. It exposes the game ID and Gecko handler/mailbox region. It cannot directly read the GameSystem singleton at `0x806A1228`, the `SceneObjHolder`, dynamically allocated actor objects, model managers, or scheduler arrays. The M4 client consumes the already-published position snapshot and does not expose actor object pointers.

The existing GDB `probe-mario` is read-only and verifies PAL inputs plus the known Mario path, but it does not validate a non-player group layout or actor identity, and GDB performance has previously been a concern. Scanning RAM for a vtable-like word would not establish a live object: it risks matching stale heap data, interior pointers, copied vtables, or another instance of the class. A host pointer is never derived from a guest address; all future reads must remain explicit guest-address reads with bounded lengths and checks at each hop.

**Therefore no generic actor reader or lifecycle command was added.** Safe inspection is blocked until one of these is supplied:

1. A read-only guest-memory backend that can read bounded arbitrary MEM1 ranges efficiently while validating PID, Dolphin 2609a identity, RMGP01 Rev 0, and mapping size; and
2. Exact verified `AllLiveActorGroup`/`LiveActorGroup` member offsets and live object vtable address-point evidence, plus a validated way to distinguish the selected Beach Bowl `Penguin` placement from other instances.

Without those, the requested pause/transition lifecycle trace cannot be reported. No guessed offset or scanned pointer is used as a substitute.

## Candidate future diagnostic contract

Once unblocked, a diagnostic can read only the validated chain: `GameSystem -> active Scene -> SceneObjHolder -> AllLiveActorGroup -> bounded actor entry -> candidate Penguin`. It must cap group count at the verified capacity (and a smaller tool-side limit), check every 32-bit guest pointer for alignment and an allowed MEM1/MEM2 range before issuing a read, validate the actor type using verified live vtable evidence, validate the model-manager pointer similarly, and emit no data unless all checks pass. Reads are guest-memory API calls; the host must never cast a guest address into a host pointer.

Each record should include host timestamp, Dolphin PID, `RMGP01` identity status, scene pointer/epoch if verifiable, group count, actor pointer, vtable value, name pointer/string (bounded and terminated), position/rotation/gravity, model-manager pointer, and scheduler membership only if each path is independently validated. On invalid/missing evidence it must emit `unavailable` or a specific validation failure, never fall back to a memory scan.

Pause testing should show stable pointers and frozen values if the game freezes CPU execution. A scene transition may invalidate every scene/actor/model/scheduler pointer; discard all cached pointers on scene pointer or epoch change and resolve from the root again. Disconnected/unavailable/title-screen states must be reported as unavailable. These are expected behaviors, not observed results.

## Safe Windows PowerShell checks available now

From the repository root:

```powershell
Set-Location C:\Users\maxim\Projects\multigalaxy1
cargo run -p multigalaxy -- validate-mario-map reference\original\main.dol target\petari-rmgp01-symbols.txt
```

Expected result: PAL DOL and Petari RMGP01 map validate successfully. This checks static PAL map inputs, not the Penguin or its runtime lifecycle.

For the existing read-only live mailbox/M4 position client, with the game already running in the isolated stock Dolphin profile and the local mailbox available:

```powershell
# Terminal 1
cargo run -p multigalaxy -- sync-server 127.0.0.1:55221

# Terminal 2; set this to the isolated Dolphin.exe PID
$DolphinPid = 1234
cargo run -p multigalaxy -- sync-client development.local.toml $DolphinPid 127.0.0.1:55221 Mario
```

Expected client output includes `sync_local=validated ... mapping=read_only`, then `sync_local=position_ready` while a fresh mailbox is present. This exercises the existing Mario position mailbox only; it does **not** locate or inspect a Penguin. Pause Mario's game and then resume; the position client may stop publishing fresh frames while paused and resume afterward. A scene transition may temporarily report unavailable. No actor pointer should be inferred from those outputs.

Completed checks for this documentation-only phase:

- `cargo fmt --all -- --check`: passed.
- `cargo test --workspace`: 44 CLI tests passed, 12 patch-crate tests passed, 1 live-Dolphin test ignored by its opt-in requirement.
- `cargo build --workspace`: passed.
- `cargo run -p multigalaxy -- validate-mario-map reference\original\main.dol target\petari-rmgp01-symbols.txt`: passed; PAL RMGP01 Rev 0 DOL and Petari symbol fingerprints and existing Mario-chain signatures verified.
- No Dolphin lifecycle test was run because no safely validated non-player pointer path is implemented. No runtime observation is claimed.

To reproduce the visual target manually, launch the already-prepared isolated profile using the normal MultiGalaxy launcher, select **Beach Bowl Galaxy > Sunken Treasure**, and leave a Penguin in view. Do not use `--gecko-bridge-probe` for this read-only check because that diagnostic exchanges mailbox acknowledgements. The missing actor diagnostic means no actor-pointer fields will be printed yet. Pause/resume and transition out of and back into the galaxy; these are visual controls only in this phase, not actor lifecycle measurements.

## Sources

- [Petari `AllLiveActorGroup.cpp`](https://github.com/SMGCommunity/Petari/blob/master/src/Game/LiveActor/AllLiveActorGroup.cpp)
- [Petari `LiveActorGroup.cpp`](https://github.com/SMGCommunity/Petari/blob/master/src/Game/LiveActor/LiveActorGroup.cpp)
- [Petari `AllLiveActorGroup.hpp`](https://github.com/SMGCommunity/Petari/blob/master/include/Game/LiveActor/AllLiveActorGroup.hpp)
- [Petari `LiveActorGroup.hpp`](https://github.com/SMGCommunity/Petari/blob/master/include/Game/LiveActor/LiveActorGroup.hpp)
- [Petari `Scene.hpp`](https://github.com/SMGCommunity/Petari/blob/master/include/Game/Scene/Scene.hpp)
- [Petari `SceneObjHolder.hpp`](https://github.com/SMGCommunity/Petari/blob/master/include/Game/Scene/SceneObjHolder.hpp)
- [Petari `NameObjExecuteHolder.hpp`](https://github.com/SMGCommunity/Petari/blob/master/include/Game/NameObj/NameObjExecuteHolder.hpp)
- [Sunken Treasure mission overview](https://www.mariowiki.com/Sunken_Treasure)
