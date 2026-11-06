# Read-only PAL ResourceHolder/ResTable observer

This documents the opt-in, one-shot `observe-resource-holder` command: the static PAL RMGP01 Rev 0 derivation behind it, the holder/ResTable structures it reads, the construction paths that decide whether a holder can survive a scene transition, the persist-vs-reload discriminator table, and the interpretation limits. It reads only the validated read-only MEM1 mapping, performs no writes, invokes no callbacks, and retains no pointers between invocations. It is the bounded next experiment named in the [M5 milestone](../milestones.md) M5 row: derive and observe exact ResourceHolder/ResTable identities across a Beach Bowl → Observatory transition.

All addresses and signatures are bound to the repository's exact PAL DOL SHA-256 (`8200fb72b3170069bb71c8b3dc21b49e429977ce8ae5159bb95ea6a19de0fbee`) and the matching Petari symbol-file hash. `verify_pal_layout` rejects any other build before any read; live code signatures are re-checked before and after each sample, and the mandatory AllStars patch is rejected if it overlaps any pinned signature.

## Static PAL derivation (Phase 0)

Instruction traces below were read from the exact PAL DOL (text base `0x800070A0` = file `0x25E0`) and cross-checked against Petari symbols; PAL bytes remain authoritative.

### Reaching the holder from the validated model chain

| Edge / field | Address / offset | PAL evidence |
|---|---:|---|
| `ModelManager::getModelResourceHolder` | `0x8016973C` | `lwz r3,0x20(r3); blr` — returns `ModelManager+0x20` |
| `ModelManager::getResourceHolder` | `0x80169720` | loads `ModelManager+0x14`; if non-null returns `[anmHolder+0x6C]`, else `ModelManager+0x20` |
| `ModelManager::initModelAndAnimation` | `0x8016979C` | `stw r4,0x20(r3)` at `0x801697B0` stores the model holder; the Xanime branch stores the animation holder at `+0x14` (`0x801697F8`) and the `XanimePlayer*` at `+0x18` (`0x80169814`); the non-Xanime branch calls `newJ3DModel` at `0x801697E4` (`bl 0x803E9668`) and stores the model at `+0x1C` |
| `MR::newJ3DModel` holder load | `0x803E9668` | `lwz r3,0(r3)` at `0x803E9680` reads `ResourceHolder+0x00` as the `ResTable*`, then `bl 0x803A73E0` (`getRes(char const*)`) at `0x803E9690` resolves the `J3DModelData*` that `entryModelData` stores at model `+0x04` |

So the observer reaches the model holder as `ModelManager+0x20` and the `ResTable` as `holder+0x00`, reusing the already-validated MarioActor → ModelManager chain from `mario_model_observer`.

### ResourceHolder layout (size 0x50, not polymorphic)

| Field | Offset | PAL evidence |
|---|---:|---|
| `mResTable` (first ResTable) | `+0x00` | `newJ3DModel` `lwz r3,0(r3)` at `0x803E9680`, passed to `ResTable::getRes` |
| `mArchive` (`JKRArchive*`) | `+0x48` | ResourceHolder constructor `stw r3,0x48(r30)` at `0x803A5EF8`; `initializeArc` `lwz r4,0x48(r28)` at `0x803A6240` region; creation path constructs with archive `-1` (pre-mount sentinel `0xFFFFFFFF`), so the observer accepts null/sentinel or a range-checked pointer and never dereferences it |
| object size | `0x50` | `ResourceHolderManager::createResourceHolder` `0x803A6F28` calls `__nw(0x50)` at `0x803A6F60` |
| vtable | none | the symbol map has no `__vt__14ResourceHolder`; the observer checks no vptr on the holder and validates it structurally (bounded 0x50-byte span, valid ResTable pointer) instead |

### ResTable layout

| Field | Offset | PAL evidence |
|---|---:|---|
| `mEntries` (entry array) | `+0x00` | `ResTable::getRes(u32)` `0x803A73D0`: `lwz r3,0(r3); lwzx r3,r3,r0` with `mulli r0,r4,0x18` |
| count field | `+0x04` | `__ct__8ResTableFv` `0x803A72EC` zeroes `+0x00` and `+0x04`; Petari `ResTable` member corroboration |
| entry size | `0x18` | `mulli r0,r4,0x18` in `getRes(u32)` and the `addi r4,r4,0x18` stride in `getResIndex` `0x803A74B0` |
| entry `+0x00` resource pointer | `+0x00` | returned by `getRes(u32)` |
| entry `+0x10` name pointer | `+0x10` | `getResName(u32)` `0x803A73BC`: `lwz r3,0x10(r3)` |
| entry `+0x14` name hash | `+0x14` | `getResIndex` compares `lwz r0,0x14(r5)` against the hashed name |

The observer probes at most the first 16 entries (`ENTRY_PROBE_WINDOW`) with fully bounds-checked 0x18-byte reads to locate the entry whose resource pointer equals the sampled `J3DModelData`. This is a bounded fixed window over the model holder's own table, not a heap scan and not an enumeration of `ResourceHolderManager` entries.

### Holder construction paths (why this matters for transitions)

| Path | PAL evidence | Lifetime consequence |
|---|---|---|
| `ResourceHolderManager::createAndAdd(name, heap)` | `0x803A6A58` → `startCreateResourceHolderOnMainThread` `0x803A6D14` → `createAndAddInner` `0x803A6DE4` → `find` `0x803A7104`; on miss `createResourceHolder` `0x803A6F28` allocates the 0x50-byte holder, then `add` `0x803A7070` appends a manager entry | Manager entry layout is `{name, holder, hash, heap}` (0x10-byte stride; `add`'s assignment sequence at `0x803A72A8`; `find` compares entry `+0x08` at `0x803A7150`). The **recorded heap is the manager entry `+0x0C`**, compared against the scene heap by `removeIfIsEqualHeap` `0x803A6BCC` (`lwz r3,0xC(r29); cmplw r3,r28` at `0x803A6BFC`) |
| `createAndAddStationed(name)` | `0x803A6A8C` (stationed functor from `.data` `0x805C73E8`) | Registered as a persistent/stationed holder, not keyed to the scene heap |
| Scene teardown | `GameSystemSceneController::destroyScene` `0x8039CF80` → `removeIfIsEqualHeap` → `HeapMemoryWatcher::destroySceneHeap` `0x8039EC9C` | Holders whose manager entry records the Scene heap are removed before the heap is destroyed; others persist |

Interpretation: a holder recorded on the Scene heap is removed at scene teardown, so a *changed* holder pointer across a transition means replacement (reload), while an *identical* holder pointer plus an identical archive/ResTable/entries identity supports (but does not prove) persistence. The observer deliberately does not enumerate the manager to read entry `+0x0C`; the scene-heap identity it reports comes from the existing heap observer.

## Live command

```powershell
Set-Location C:\Users\maxim\Projects\multigalaxy1
.\target\debug\multigalaxy.exe observe-resource-holder .\development.local.toml $DolphinPid
```

Same preconditions as `observe-mario-model`: already-running official stock Dolphin 2609a on the interactive desktop, PAL RMGP01 Rev 0, validated DOL/symbols/AllStars inputs. Run once in a stable playable scene (e.g. Beach Bowl), perform one ordinary in-game transition, run once more in the destination scene (e.g. Comet Observatory) with the same PID, and compare the printed identities. `observe-mario-model` output is unchanged, so its PID 119844 samples remain directly comparable.

Sample output fields: `model_resource_holder`, `anm_resource_holder`, `holder_res_table`, `res_table_entries`, `res_table_count`, `model_data_entry_index`, `entry_probe_window`, `holder_archive`, plus the re-validated scene/group/actor/manager/model-data tuple and heap identities. Fixed labels: `holder_vptr=none_polymorphic`, `heap_free_list=unavailable` (dropped from the initial implementation per plan; JKRHeap free-list offsets are only derived if the holder/archive/ResTable evidence is ambiguous), `scene_serial_generation=unavailable`, `pointer_equality=not_lifetime_proof`.

## Persist vs reload-at-reused-address discriminators

SMG1/JKR has no verified generation counter or allocation timestamp, so the table below is ordered strongest to weakest:

| Evidence | Supports persist | Indicates reload at reused address |
|---|---|---|
| `model_resource_holder` identical across the transition | supports persistence | changed holder ⇒ holder was removed/recreated |
| `holder_archive` (`+0x48`) identical and valid | supports the same mounted archive | new archive pointer ⇒ remount |
| `holder_res_table` and `res_table_entries` identical, same `res_table_count`, same `model_data_entry_index` resolving to the same `J3DModelData` | supports a cache hit on the same table | new table/entries ⇒ rebuilt ResTable |
| holder's manager-recorded heap ≠ destroyed Scene heap (indirect: holder identity survived `removeIfIsEqualHeap`) | strong support | holder keyed to the scene heap ⇒ removed at teardown |
| heap free-list membership of the ModelData block | not implemented (`heap_free_list=unavailable`) | would be near-decisive if later derived |
| `J3DModelData` pointer identical alone | **insufficient** (pointer reuse is possible) | insufficient |

Pointer equality is reported as support, never proof of uninterrupted lifetime; changed identities establish replacement, not destructor timing. A successful sample is a consistent read window only — the emulator keeps running, and any scene/group/model/heap change during the sample rejects the whole observation.

## Phase 2 live samples (user-observed, Dolphin PID 113428)

The user ran `observe-resource-holder` twice with the same Dolphin PID (113428, PAL RMGP01 Rev 0 validated) around one ordinary Beach Bowl → Comet Observatory transition. These are user-observed live samples, not independently recaptured in this documentation pass.

| Field | Beach Bowl sample | Observatory sample | Set |
|---|---:|---:|---|
| `model_resource_holder` | `0x80D68630` | `0x80D68630` | persist |
| `holder_res_table` | `0x80D68690` | `0x80D68690` | persist |
| `res_table_entries` (count, model-data index) | `0x80D686B8` (1, 0) | `0x80D686B8` (1, 0) | persist |
| `holder_archive` | `0x80A32C90` | `0x80A32C90` | persist |
| `J3DModelData` | `0x80D6874C` | `0x80D6874C` | persist |
| `anm_resource_holder` | `0x810B971C` | `0x810B235C` | changed |
| `MarioActor` | `0x810B29CC` | `0x810AB5FC` | changed |
| `ModelManager` | `0x810B96F4` | `0x810B2334` | changed |
| `AllLiveActorGroup` | `0x80FA07D8` | `0x80FA074C` | changed |

The changed instance values match the PID 119844 model-observer round exactly. The persist set occupies the `0x80A3`–`0x80D6` region (archive-backed resource space), while the changed set sits in the `0x810x`/`0x80FA` scene-instance block.

Interpretation per the discriminator table: every persist signal holds — identical model holder, ResTable, entry array, single-entry count with the model data at index 0, archive, and `J3DModelData` — while the animation holder tracks the per-scene `ModelManager`. This is the strongest support yet for a persistent holder / cache hit over reload-at-reused-address. Per the standing rule, pointer equality is support, never proof: there is no generation counter and no free-list evidence (`heap_free_list=unavailable`), so reload-at-reused-address is not excluded, only unsupported by every available signal.

## Why `holder_recorded_heap` is not an output field

The approved plan listed "the holder's recorded heap identity if present". Static tracing shows it is **not present on the holder**: the heap is recorded in the `ResourceHolderManager` entry (`+0x0C` of the 0x10-byte `{name, holder, hash, heap}` record, compared by `removeIfIsEqualHeap` at `0x803A6BFC`), not in the 0x50-byte `ResourceHolder` object itself. Reaching that field for the model holder would require locating its manager entry via `ResourceHolderManager::find`, which needs the archive name's hash and a walk of the manager entry table — precisely the manager enumeration the plan prohibited ("do not scan manager entries"). The observer therefore reports the scene-heap identities from the existing heap observer instead, and the persist/reload interpretation uses holder/archive/ResTable/entry identity plus the indirect heap-keyed-removal reasoning in the construction-path table above. If the recorded heap ever becomes necessary, the bounded route is to compute the model archive name's hash from the pinned DOL string and call-equivalent `find` semantics offline, then read that one entry — not a general scan.

## Smallest next evidence step toward the shared-joint mutation risk

The remaining M5 hazard is `MR::newJ3DModel` calling `invalidateMtxCalc` (`0x803E7FC4`, zeroes joint `+0x54`) and `invalidateJointCallback` (`0x803E7FF8`, zeroes joint `+0x04`) on the shared `J3DModelData` joints (array at model-data `+0x28`, count at `+0x2C`), with re-arming attributed to `XanimeCore::enableJointTransform` (`0x80018D98`) via `ModelManager::initJointTransform` (`0x80169678`). The smallest next bounded, read-only experiment: extend the observer to sample the shared ModelData's joint count and each joint's `+0x04`/`+0x54` words across the same Beach Bowl → Observatory transition (fixed, plan-checked reads through the already-validated `J3DModelData` pointer). Non-zero and stable joint fields across the transition would show the invalidation is transient and re-armed per scene rather than a persistent shared-state hazard; zeroed fields would confirm the factory's clear is what the live model runs on. Either way it bounds the coexistence risk without constructing a second model. Static complement: trace all PAL write sites to joint `+0x04`/`+0x54` to confirm `enableJointTransform` is the only re-arm path. Rendering remains **NO-GO**.

## Joint-field observer (`observe-model-joints`)

The smallest next evidence step above is implemented as a sibling opt-in command, `observe-model-joints <development.local.toml> <Dolphin-pid>` (`crates/multigalaxy-cli/src/model_joint_observer.rs`). `observe-mario-model` and `observe-resource-holder` outputs are unchanged.

Static derivation (pinned by `verify_pal_layout` against the exact PAL DOL):

| Field / path | Offset / address | PAL evidence |
|---|---:|---|
| `J3DModelData` joint array | `+0x28` | full 32-bit `lwz r4,0x28(r3)` at `0x803E7FD4`/`0x803E8008` |
| `J3DModelData` joint count | `+0x2C` | **16-bit** `lhz r6,0x2C(r3)` at `0x803E7FC4`/`0x803E7FF8`; the upper half of the word is a different field and must never be read into the count |
| joint callback cleared by factory | joint `+0x04` | `stw r5,0x04(r4)` at `0x803E8020` (`invalidateJointCallback`) |
| joint matrix-calc procedure cleared by factory | joint `+0x54` | `stw r5,0x54(r4)` at `0x803E7FEC` (`invalidateMtxCalc`) |
| re-arm path | `0x80169678` → `0x80018D98` | `ModelManager::initJointTransform` calls `XanimeCore::enableJointTransform` (branch-target pinned), which reads the model-data joint array (`lwz r4,0x28(r30)` at `0x80018DE8`) |

The command samples the joint table twice within one invocation, 3 seconds apart, re-resolving the full Mario model chain around each sample and rejecting any scene/group/actor/model/heap change. The two within-scene samples plus `within_scene_stable` keep per-frame Xanime writes from being misread as transition effects; the user then runs the command once per scene across a transition for the cross-scene comparison.

Width correction (live failure, fixed): the first live run rejected with `joint count 1966093 exceeds the bounded observer cap 64` because the count word at `+0x2C` was read as 32 bits (`0x001E0005`); the PAL code loads it with `lhz`, so the observer now reads two bytes and the count is the low halfword (5 in that sample). The joint array at `+0x28` is a genuine 32-bit `lwz` and is unchanged. A synthetic regression test pins the halfword width; the fail-closed cap rejection itself worked correctly.

### Phase 1.29 live joint samples (user-observed, Dolphin PID 107916)

`observe-model-joints` run once per scene across a Beach Bowl → Comet Observatory transition, same Dolphin PID 107916, both PAL-validated. `joint_count=30`; all 30 joints report `callback=0x00000000` and `mtx_calc=0x00000000` in **both scenes and both within-scene passes** (`within_scene_stable=true`). User-observed live samples, not independently recaptured here.

Endpoint interpretation (per the approved rules; the factory clear itself was not traced):

- Local Mario runs on **cleared joints** — the `invalidateMtxCalc`/`invalidateJointCallback` clear is not transient, and these two words are not re-armed during live gameplay. The `XanimeCore::enableJointTransform` re-arm hypothesis is disconfirmed **for these specific fields**.
- Consequence: a second model construction's invalidation writes would clear already-zero words — **idempotent with respect to the running local Mario**. This is the first live counter-evidence against the shared-joint-mutation NO-GO reason.
- Residual limit: `enableJointTransform` internals and all other shared-joint fields remain unaudited, so this bounds only joint `+0x04`/`+0x54`; cleanup ordering, matrix-buffer/packet allocation lifetime, and GX state remain unresolved. Rendering remains **NO-GO**.

Interpretation rules (endpoint inferences only — the factory clear itself is not traced):

- joint `+0x04`/`+0x54` **non-zero in both scenes** → the factory clear is transient and re-armed per scene; supports the safety of a second instance on shared ModelData;
- **zeroed in live scenes** → local Mario itself runs on cleared joints, so a second construction would act on already-cleared state;
- pointer equality of joint fields is support, never proof.

## Synthetic coverage and live status

Synthetic tests cover holder/ResTable resolution, the model-data entry probe (hit, miss, window cap), null animation holder, the `0xFFFFFFFF` pre-mount archive sentinel, invalid/null/out-of-bounds pointers, holder span crossing the MEM1 end, and AllStars overlap rejection. `verify_pal_layout` was additionally run once against the repository's exact PAL DOL and Petari symbol map during development (all 15 pinned code signatures, 15 expected symbol lines, and 3 branch-target checks passed). Phase 2 live samples (PID 113428) are recorded above. Rendering remains **NO-GO**.
