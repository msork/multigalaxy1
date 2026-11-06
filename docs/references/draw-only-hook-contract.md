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

## 8. Stage 1 implemented: trampoline dry-run (delivery proof only)

Implemented as an opt-in `dev-launch --trampoline-dry-run` flag (not combinable with `--debug`/`--debug-unattached`/`--gecko-bridge-probe` in Stage 1). When set, `configure_isolated_profile` appends the trampoline variant of the Gecko C0 block to the isolated profile's `GameSettings/RMGP01.ini` only (`gecko_bridge::append_code_with`). Nothing changes by default.

Guest side (per-frame C0 installer, scratch regs r0/r10/r11/r12 only): compute the runtime stub address PC-relative, then store `b <stub>` at `0x802B62E4` **only if** the current word is the original `0x4BFF083D` or the already-installed branch — anything else is left untouched. The 8-word stub is constant and sits after the mailbox data inside the same C0 block, below the AllStars cave `0x80002790` (layout test enforced):

```
lis r11,0x802A; ori r11,r11,0x6B20; mtctr r11; bctrl   ; call original directDraw
lis r11,0x802B; ori r11,r11,0x62E8; mtctr r11; bctr    ; jump to 0x802B62E8
```

The cave executes **only** the original `bl 0x802A6B20` — no duplicate draw, no j3dSys writes. Rollback is relaunch without the flag (the ini is regenerated; MEM1 reloads the original DOL word).

Gates before any of this runs: `hook_trampoline::verify_hook_layout` (exact PAL DOL/symbol hashes; DOL word at `0x802B62E4` must equal `0x4BFF083D`), AllStars disjointness (`ensure_hook_disjoint` rejects any patch write overlapping the hook word; the cave layout test rejects overlap with `0x80002790`), Dolphin 2609a codehandler size/SHA-256, live RMGP01 identity.

Host-side read-only verification: `multigalaxy verify-hook <development.local.toml> <Dolphin-pid>` classifies the live hook word as `original` (rollback confirmed: `0x4BFF083D`), `installed` (plain `b` whose target holds the exact stub template), or `UNEXPECTED`.

Caveat for the acceptance run: this proves the bytes are installed and the trampoline is semantically the original call; whether Dolphin's JIT picks up the runtime-written branch is only observable via your visual check (identical rendering with hook on/off in Beach Bowl, stable across ≥1 transition).

## 9. Stage 1 live failure analysis (Dolphin PID 31336) — delivery is NO-GO

Live evidence: Dolphin reported `Unknown instruction 03d4cb34 at PC=802b62e4 last_PC=8132ffc8 LR=802b62dc`; `verify-hook` read `0x03D4CB34` at `0x802B62E4` consistently (menus, Beach Bowl, Observatory), so reader and CPU agree on the corrupted word. After Ignore, Mario lost all materials except face/gloves/shadow for the rest of the session.

### Q1 — what wrote `0x03D4CB34`?

Not the installer. The installer computes `r0 = 0x48000000 | ((stub - 0x802B62E4) & 0x03FF_FFFC)`, so any word it stores has top byte `0x48`–`0x4B` (a `b`/`bl`). `0x03D4CB34` has top byte `0x03` — impossible from that sequence. Notably `0x03D4CB34 = 0x4BD4CB34 & 0x03FFFFFF`, i.e. a *valid-looking branch word with its top byte masked off*. Static checks: the AllStars diff (`target/allstars-pal-rev0-diff.txt`) contains no write at or near `0x802B62E4`, and the payload word count is even (no dropped trailing word). Conclusion: the hook word was overwritten *after* installation by code that executed at a wrong stub address (see Q2) — the installer's guard was correct but not sufficient, because the guard protects the store, not the memory afterwards.

### Q2 — where is the C0 stub actually placed at runtime?

The layout test assumed our block loads at `HANDLER_BASE + 2880 + 16 = 0x80002350`. That is only true if our code is the *first* Gecko code in the ini. But `configure_isolated_profile` **appended** our `[Gecko]` block after the AllStars `[OnFrame]` section, and Dolphin loads codes into the cave in ini order — so our block (installer + stub) landed **after** AllStars' code, at/after the `0x80002790` region that AllStars' own runtime cave writes use. The stub was therefore in a region subject to AllStars cave writes, and the observed `last_PC=0x8132ffc8` shows execution left the cave entirely (garbage executed as code), consistent with the stub having been overwritten. Fix applied: the Gecko block is now emitted **first** in `RMGP01.ini`, so it loads at the lowest cave address (right after the 2880-byte codehandler) and stays below `0x80002790`; the layout test now also asserts pair parity (odd word counts silently drop the last word in the ini emitter).

### Q3 — path from LR=0x802B62DC to PC=0x802B62E4

`0x802B62D8: bl 0x802A6560` returns to `LR=0x802B62DC`; fall-through runs `0x802B62DC: mr r3,r29`, `0x802B62E0: li r4,0`, then `PC=0x802B62E4` executes the corrupted word `0x03D4CB34` → unknown-instruction exception. The trampoline itself (had the word been intact) would have been: `b stub` → `bl 0x802A6B20` (bctrl, LR=stub+16) → `bctr 0x802B62E8`. The `bctr` return address `0x802B62E8` (callsite+4) is correct against the real runtime layout — the failure was the word at the hook, not the stub's return address.

### Pre-flight added

`dev-launch --trampoline-dry-run` now runs a host-side pre-flight after spawn (read-only MEM1, up to 30 s): `hook_trampoline::preflight_hook` accepts only the exact original `0x4BFF083D` or a verified installed branch (stub template match at the decoded target); on anything else it stops Dolphin and fails closed. The guest installer's only-write-if-original-or-installed guard remains as the second layer.

### Go/no-go update

Delivery (Gecko hook installation) is **NO-GO** until a rerun shows: pre-flight passing, `verify-hook` = `installed` with verified stub across menus + two scenes, no unknown-instruction, and identical rendering hook on/off. Stage 2 remains parked. Rendering remains **NO-GO**.

### Addendum: pre-flight abort on rerun (0x802e153c) — investigated, not an AllStars write

The rerun aborted in the new pre-flight with `live memory does not match PAL RMGP01 Rev 0 at 0x802e153c`. Cross-reference result: `0x802E153C` (`getMarioActor__11MarioHolderCFv`, first PAL runtime signature) has **zero overlapping writes** among all 419 parsed AllStars writes (nearest AllStars write is `0x803302F8`, ~323 KB away), and the raw AllStars patch files contain no reference to it. So per the stop rule this was treated as a real discrepancy and investigated before any change.

Finding: the address is inside DOL text section 1, which loads from disc during boot **after** the MEM1 mapping exists and after the game ID at `0x80000000` is readable. The pre-flight loop only retried when the MEM1 open failed; on a successful open it asserted the signatures once and aborted — so a read during early boot (text not yet loaded) produced the false mismatch. Every other startup path (bridge probe, observers) polls with a deadline for exactly this reason. Fix: the pre-flight now retries signature/pre-flight mismatches until the 30 s deadline and only then fails closed, stopping Dolphin. Regression test added (`preflight_accepts_original_and_installed_but_rejects_corruption`, including the observed `0x03D4CB34` word). Delivery remains **NO-GO** pending a clean rerun; Stage 2 parked.

### Addendum 2: timeline diagnostic pre-flight (rerun 2 abort) + corrupter analysis

The rerun's pre-flight hit the 30 s deadline with the hook word at `0x03D4CB34` — the same masked value as the crash run (`0x4BD4CB34 & 0x03FFFFFF`), unchanged by the ini-order fix. Notably, the unmasked `0x4BD4CB34` decodes as a `bl` from `0x802B62E4` to **`0x80002E1C`** — inside the Gecko cave below `0x80002790`, i.e. a plausible stub address. So the sequence was: installer armed a branch to the stub, then a separate writer masked the top byte (`0x4B`→`0x03`), turning a valid branch into an invalid opcode. The corrupter is independent of stub placement.

Diagnostic pre-flight implemented (no rerun yet): `dev-launch --trampoline-dry-run` now polls the hook word every ~500 ms for up to **180 s** (Wii safety screen + intro exceed 30 s), printing a timestamped timeline line per sample (`t=…ms hook_word=0x… original|installed|UNEXPECTED`), and passes only when the word is acceptable (original `0x4BFF083D` or branch-to-verified-stub) **and unchanged across 6 consecutive polls**. On deadline it stops Dolphin and dumps the full timeline.

Expected timeline shapes and what each proves:

| Observed sequence | Interpretation |
|---|---|
| `0x00000000` → `0x4BFF083D` → branch → (stable) | boot loader copy, then installer armed after copy; healthy |
| `0x00000000` → `0x03D4CB34` (never original) | writer acted on the unloaded region before the apploader copy; boot-race hypothesis confirmed |
| `0x4BFF083D` → branch → `0x03D4CB34` and stays | corrupter fires after install, independent of placement; hypothesis 2 confirmed |
| branch → `0x4BFF083D` → branch oscillation | guard re-arm fighting the loader copy |

The timeline discriminates hypotheses 1 and 2 by *when* the masked value first appears relative to the original word appearing.
