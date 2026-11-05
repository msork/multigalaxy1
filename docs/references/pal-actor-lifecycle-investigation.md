# PAL actor lifecycle investigation (M5 Phase 1.5)

Status: **static group layout derived; live metadata and Penguin identity/lifecycle pending**. M5 Phase 1.8 derives the SceneObjHolder root and AllLiveActorGroup fields from PAL instructions. No Dolphin process was running during this investigation and no live group pointer/metadata or actor pointer was captured. This work made no game-memory writes, hooks, patches, actor creation, allocator calls, scheduler changes, or Dolphin configuration changes. M4, the Gecko mailbox, AllStars, and isolated profiles are unchanged.

Evidence classification: symbol addresses and source layouts below are static evidence. The user has live-validated the read-only backend with RMGP01 at `0x80000000` and a PAL DOL code signature at `0x800103A0`; no live group pointer/metadata, Penguin identity, vtable, model pointer, scheduler entry, pause behavior, or transition behavior was observed. Beach Bowl/Sunken Treasure remains a reproducible test candidate.

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

The PAL source map and DOL identity are checked by the existing map-validation command. For `AllLiveActorGroup`, the constructor directly writes its vtable symbol `0x8057327C` at object offset zero, so that value can validate the live group container. The separate `Penguin` vtable symbol `0x8059C884` is still only a static address: its live address-point and object-word relationship have not been confirmed. Do not compare a live Penguin word against it until resolved.

## Known layout facts and chain leads

Source-verified Petari layout:

- `Scene` has its name-object list executor pointer at `+0x08` and `SceneObjHolder*` at `+0x10`.
- `SceneObjHolder` stores a fixed array of object pointers. `SceneObj_AllLiveActorGroup` is ID `0x30`, so its slot is at holder base `+0xC0`; `SceneObj_NameObjExecuteHolder` is ID `0x58`, slot `+0x160`.
- `LiveActor` derives from `NameObj`. Its documented transform/model fields are position `+0x0C`, rotation `+0x18`, velocity `+0x30`, gravity `+0x3C`, model manager pointer `+0x48`, and animation keeper pointer `+0x4C`. `NameObj` has the name pointer at `+0x04`, flags at `+0x08`, and executor index at `+0x0A`.
- `AllLiveActorGroup` constructs a `LiveActorGroup` named `AllLiveActorGroup` with capacity 2560. Its source iterates group entries to initialize actor system/clipping data. `LiveActorGroup::getActor(index)` returns an entry from its object array; group source also checks actor dead/alive state.
- `NameObjExecuteHolder` has an execute-info array pointer at `+0x0C`, max size `+0x10`, count `+0x14`. An execute-info begins with its executed `NameObj*`; it stores scheduler category fields after that pointer. The PAL source has separate scene and draw connect/disconnect paths.

Sources, the local symbol map, and PAL constructor/accessor bytes establish the AllLiveActorGroup base at offset zero, vptr at `+0`, capacity at `+0x0C`, registered count at `+0x10`, owned pointer array at `+0x14`, and array entry stride 4. The exact count and live pointer values are dynamic and not observed. The relevant Petari base-class header is not present in this checkout, but the field semantics are independently visible in the pinned PAL code. Object-pointer membership and scheduler-array membership in a running scene remain unverified.

## Safety assessment and blocker

The bounded `inspect-memory` command can now read explicit ranges throughout the conservative 24 MiB retail MEM1 region. The user validated the basic live path with game ID and DOL signature reads. This does not itself validate any live object pointer; it is not a scanner and does not follow pointers. The M4 client consumes the already-published position snapshot and does not expose non-player actor object pointers.

The existing GDB `probe-mario` is read-only and verifies PAL inputs plus the known Mario path, but it does not validate a non-player group layout or actor identity, and GDB performance has previously been a concern. Scanning RAM for a vtable-like word would not establish a live object: it risks matching stale heap data, interior pointers, copied vtables, or another instance of the class. A host pointer is never derived from a guest address; all future reads must remain explicit guest-address reads with bounded lengths and checks at each hop.

**Therefore no actor-entry reader or lifecycle command was added.** There is no standalone SceneObjHolder singleton: the PAL chain is `SingletonHolder<GameSystem>::sInstance (0x806A1228)` -> GameSystem `+0x24` -> SceneController `+0xAC` -> active Scene `+0x10` -> SceneObjHolder -> slot `+0xC0` for AllLiveActorGroup. Static disassembly derives group vptr/capacity/count/array fields; each live value still requires a separate bounded read and validation. The allowed experiment stops after group metadata and never reads array entries. See [Penguin live investigation](penguin-live-investigation.md) for exact PowerShell commands and stop conditions.

The smallest safe next step is for the user to run the group-metadata sample in Sunken Treasure, while paused/resumed, and after a transition. Record live values as user-observed runtime evidence. Do not read array entries until the output confirms vptr, capacity, count bounds, array pointer, and full-capacity span. Penguin identity and lifecycle remain unverified.

## Candidate future diagnostic contract

After a user live run corroborates the static group layout, a separately reviewed diagnostic may read a bounded group entry only after validating count, capacity, array span, pointer alignment/range, and the correct live type/address-point. It must use a small tool-side cap, distinguish the group code/vtable symbols from heap objects, and emit no actor data unless all checks pass. Reads are explicit guest-memory requests; the host must never cast a guest address into a host pointer.

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

For group-metadata observation, follow [Penguin live investigation](penguin-live-investigation.md). Launch the already-prepared isolated profile, select **Beach Bowl Galaxy > Sunken Treasure**, and sample while a Penguin is visible, paused/resumed, and after a transition. Do not use `--gecko-bridge-probe`; that diagnostic exchanges mailbox acknowledgements. The current commands stop after validating AllLiveActorGroup metadata and print no actor entries or Penguin fields.

## Sources

- [Petari `AllLiveActorGroup.cpp`](https://github.com/SMGCommunity/Petari/blob/master/src/Game/LiveActor/AllLiveActorGroup.cpp)
- [Petari `LiveActorGroup.cpp`](https://github.com/SMGCommunity/Petari/blob/master/src/Game/LiveActor/LiveActorGroup.cpp)
- [Petari `AllLiveActorGroup.hpp`](https://github.com/SMGCommunity/Petari/blob/master/include/Game/LiveActor/AllLiveActorGroup.hpp)
- [Petari `LiveActorGroup.hpp`](https://github.com/SMGCommunity/Petari/blob/master/include/Game/LiveActor/LiveActorGroup.hpp)
- [Petari `Scene.hpp`](https://github.com/SMGCommunity/Petari/blob/master/include/Game/Scene/Scene.hpp)
- [Petari `SceneObjHolder.hpp`](https://github.com/SMGCommunity/Petari/blob/master/include/Game/Scene/SceneObjHolder.hpp)
- [Petari `NameObjExecuteHolder.hpp`](https://github.com/SMGCommunity/Petari/blob/master/include/Game/NameObj/NameObjExecuteHolder.hpp)
- [Sunken Treasure mission overview](https://www.mariowiki.com/Sunken_Treasure)
