# Draw-only hook safety contract: single `drawMarioModel` callsite `0x802B62E4`

Static derivation and specification only, bound to the exact PAL RMGP01 Rev 0 DOL (SHA-256 `8200fb72…fbee`) and Petari symbol hash. **No runtime hook, game write, patch generation, M4 change, or AllStars change is authorized by this document.** The runtime experiment in §6 returns for separate approval.

## 1. Callsite ABI context (`MarioActor::drawMarioModel`, `0x802B612C`, size `0x27C`)

Frame and register state at the callsite, from the PAL disassembly:

| Item | Value | Evidence |
|---|---|---|
| Prologue | `stwu r1,-0x20(r1)`; LR saved at `36(r1)`; `r11 = r1+0x20` | `0x802B612C..0x802B6138` |
| `r30` | `this` (MarioActor) | `mr r30,r3` at `0x802B6144` |
| `r31` | `0x805A3AC0` (`lbl_805A3AC0`, local `.data` table) | `addis/addi r31` at `0x802B6140/48` |
| `r29` | the active `J3DModelX*` model | `lwz r29,0x0A28(r3)` path at `0x802B61F4` |
| Callsite | `0x802B62DC: mr r3,r29` / `0x802B62E0: li r4,0` / **`0x802B62E4: bl 0x802A6B20`** | audit + disassembly; instruction word `0x4BFF083D` |
| After return | `0x802B62E8: li r0,0` / `0x802B62EC: stw r0,0x1B0(r29)` (clears model flag word) | disassembly |
| Second directDraw callsite | `0x802B636C: bl 0x802A6B20` (reflection model) | disassembly |

Displaced-instruction analysis: the hook point `0x802B62E4` is a **single 4-byte `bl`** (`0x4BFF083D`). Nothing else is displaced. At the hook, volatile `r3`=model, `r4`=0; nonvolatile `r29`=model, `r30`=actor, `r31`=data table are live and must be preserved by any trampoline. The `r1` frame is 32 bytes with LR at `36(r1)`; a cave should clobber only `r0,r3-r12`.

Trampoline/branch plan (byte-exact): replace the word at `0x802B62E4` with `b <cave>` = `0x48000000 | ((cave - 0x802B62E4) & 0x03FF_FFFC)` (the repo's `gecko_bridge` already emits exactly this form via `FixupKind::Branch`). The cave executes the duplicate draw, then the displaced original as `bl 0x802A6B20`, then `blr`. Rollback = restore `0x4BFF083D`.

## 2. `J3DModelX::directDraw` (`0x802A6B20`, size `0x144`) mutation inventory

Globals touched (all writes enumerated from the PAL body; `j3dSys` = `.bss:0x805F8B80`, size `0x11C`):

| Site | Mutation | Kind |
|---|---|---|
| `0x802A6B44` | `j3dSys+0x38` = current model (`stw r3,56(r5)`) | global current-model pointer |
| `0x802A6B54..6C` | `j3dSys+0x34` flag bits `0x4`/`0x8` set/cleared from model `+0x08` | global flags |
| `0x802A6BA8` | `bl syncJ3DSysFlags__12J3DModelDataCFv` (`0x80430278`) on model-data `+0x04` | global flag sync |
| `0x802A6BC0..C4` | `j3dSys+0x58` = model-data `+0x6C` (texture) | global current-texture |
| `0x802A6BC8..BDC` | model `+0x1D0` fog counter decrement (`lbz`/`stb`) | per-instance |
| `0x802A6BE8..C40` | per-material loop over model-data `+0x60`; helper `0x802A6C64` per material; model `+0x1C0` material index store | per-instance + helper-internal GX |
| `0x802A6C4C` | `stw r0, -0x23E8(r13)` = 0 — SDA2 global reset | global (SDA2) |

A duplicate draw therefore mutates `j3dSys+0x38/+0x34/+0x58` and the SDA2 global, plus GX/FIFO state inside the per-material helper. Required save/restore around a duplicate draw: save `j3dSys+0x34/+0x38/+0x58` and the SDA2 word, run the duplicate `directDraw`, then run the original `directDraw` **last** so the original call restores those globals to local-Mario values (the existing candidate order in `draw-only-render-contract.md`); GX FIFO commands already emitted cannot be un-emitted — ordering is the only mitigation, and equivalence is unproven.

## 3. Delivery on stock Dolphin 2609a (Gecko codehandler path)

- Mechanism: a Gecko code list in the **isolated profile's** `GameSettings/RMGP01.ini` only (the established bridge pattern in `docs/stock-dolphin-bridge.md`); the RVZ, DOL, Dolphin install, and regular profile are never touched. Gecko C0 OnFrame already executes guest PPC per frame; a one-shot hook write uses Gecko codetype `06` (hook) or a precomputed codetype `00/02` word write of the `b <cave>` instruction at `0x802B62E4`.
- Placement constraints: the cave must live in the Gecko codehandler cave region; the project already treats `0x80002790` as the first observed AllStars cave address and rejects any generated block that crosses it, and `ensure_patch_disjoint`-style checks reject overlap with AllStars writes and with every pinned observer signature (model chain, holder/ResTable, joint invalidators).
- Coexistence: AllStars stays mandatory and enabled; the hook list must pass the same PAL DOL/signature validation, live `RMGP01` identity, codehandler size/SHA-256 (2880 bytes, `2b2ff071…b33b`), and AllStars overlap checks before any write; it is opt-in per launch and reversible by restoring the original word.
- The existing C0 mailbox (counter/ACK/echo + position snapshot) is untouched by this contract.

## 4. Per-instance state for the duplicate

- ModelData is shared and persistent (Phase 1.25/1.29: holder/ResTable/archive/ModelData stable across transitions; joint `+0x04`/`+0x54` zero in live scenes, so factory invalidation is idempotent w.r.t. local Mario).
- The duplicate must own an **independent `J3DMtxBuffer`** (model `+0x84` pattern), shape packets (`+0xC4`), material packets (`+0xC0`), and the ModelX transient arrays (`+0x1C4/+0x1C8/+0x1CC`), allocated via `__nw/__nwa` on `sCurrentHeap` — during scene activity that is the Scene Napa heap (`0x80F8EEF0` in the PID 119844/113428 samples), so scene-scoped bulk reclamation is the evidenced cleanup boundary; no per-object destructor path is proven.
- The duplicate's base transform is the fixed offset; pose matrices derive from the local model's animation matrices (candidate `viewCalcRef` path, `0x802A668C`).

## 5. Go/no-go table

| Risk | Evidence | Verdict | Flips on |
|---|---|---|---|
| Shared-joint mutation (`+0x04`/`+0x54`) | Live: zero in both scenes, both passes (PID 107916) | **GO (bounded)** — idempotent w.r.t. local Mario | a live sample showing non-zero re-arm, or an audit of `enableJointTransform` writing these fields |
| Resource persist/reload | Live: holder/ResTable/archive/ModelData identical across transition (PID 113428) | **GO (support, not proof)** | a sample with changed holder/archive identity |
| Callsite ABI | single displaced `bl`, register state pinned above | **GO for static hook shape** | runtime register mismatch at `0x802B62E4` |
| j3dSys/GX global restore after duplicate draw | inventory in §2; original draw last is a hypothesis | **NO-GO** | proven postcondition that j3dSys/GX state equals the no-hook run |
| Per-instance buffer/packet lifetime | allocation on scene heap evidenced; no destructor path | **NO-GO** | a traced owner/teardown path, or scene-scoped lifetime accepted with invalidation before holder removal |
| Cache coherency (`DCStoreRange*` ordering) | `viewCalcRef` uses `DCStoreRangeNoSync`; normal/bump unresolved | **NO-GO** | exact cache sequence proven for the clone's arrays |
| AllStars/Gecko coexistence | existing disjointness checks + cave placement rules | **GO (mechanism exists)** | any overlap detection at validation time (fails closed) |
| Hook persistence/rollback | restore `0x4BFF083D` | **GO** | — |

## 6. Recommended smallest experiment (for later approval)

One opt-in Gecko-delivered hook at `0x802B62E4` in one scene (Beach Bowl): the cave draws **one fixed-offset duplicate Mario** using a separately allocated `J3DModelX`+`J3DMtxBuffer` on the scene heap, then executes the original `bl 0x802A6B20` exactly once; accept iff the local frame renders identically (visual + OSD) with and without the hook and all validation gates pass. No networking, no second scene, no per-frame animation of the duplicate.

## 7. Status

Static contract only. Rendering remains **NO-GO** until the §5 NO-GO rows are cleared and the §6 experiment is separately approved and observed.
