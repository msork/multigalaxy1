# PAL shared-model render callsite ABI audit

Target: exact `RMGP01` revision 0 DOL and matching local Petari symbols. This is offline static analysis only. No instructions, DOL, Gecko codes, Dolphin memory, or runtime behavior are modified.

## Method and ABI basis

The four words, their caller context, and the function bounds were read from `reference/original/main.dol` after `validate-mario-map` accepted the DOL/symbol fingerprint. File offsets below use the matching DOL text section translation; the exact values are also checked by `inspect-render-prototype`. Function names and ranges come from `target/petari-rmgp01-symbols.txt`.

The game is PowerPC/Gekko 32-bit code using the Nintendo console/CodeWarrior EABI conventions. EABI provides r3-r10 for initial integer/pointer arguments, r3 for scalar/pointer returns, volatile r0/r3-r12 and LR across calls, and callee-preserved r14-r31 plus CR2-CR4. A caller that needs a volatile value after a call saves it or reloads it. Stack grows down from r1; linked `bl` sets LR to the instruction after the call. The [PowerPC EABI register/stack guide](https://class.ece.iastate.edu/arun/Cpre381_Sp06/lab/labw12a/eabi_app.pdf) is a convention reference; the specific registers/return uses below are additionally grounded in the PAL caller instructions, not inferred from function names alone.

### Four callsites

| Callsite | Caller and offset | DOL file offset / original word | Target | PAL caller evidence and return contract |
|---|---|---|---|---|
| Model acquisition | `initDrawAndModel__10MarioActorFv` at `0x802B5228`, `+0x214` | `0x2B097C`: `48132BF1` (`bl 0x803E802C`) | `MR::getJ3DModel`, `0x803E802C` | Immediately before: `mr r3,r30`; target receives actor pointer in r3. Immediately after: `li r0,8; mr r27,r3; stb r0,0xDD(r3)`. The returned model pointer in r3 is consumed and dereferenced. A replacement must call the original exactly once, return its r3 value unchanged, and preserve its side effects. |
| Animator initialization | `__ct__13MarioAnimatorFP10MarioActor` at `0x802CA768`, `+0x24` | `0x2C5CCC`: `4800001D` (`bl 0x802CA7A8`) | `MarioAnimator::init`, `0x802CA7A8` | Constructor stores `r3` (`this`) in r31, stores incoming constructor arguments at `[r3+4]` and `[r3]`, then calls `init` with the animator object in r3. The return value is ignored; next `mr r3,r31` restores constructor return `this`. A replacement must preserve initialization and constructor return semantics. |
| Animation calculation | `calcAnim__10MarioActorFv` at `0x802B2A44`, `+0x2A4` | `0x2AE228`: `48019649` (`bl 0x802CC330`) | `MarioAnimator::calc`, `0x802CC330` | Immediately before: `lwz r3,0x234(r30)` loads the animator receiver. The method is called with that object in r3; no other register argument is set by the local sequence. Caller ignores return and continues with actor state loads. The animation/matrix side effects are essential; replacing with a no-op loses them. |
| Model draw | `drawMarioModel__10MarioActorCFv` at `0x802B612C`, `+0x1B8` | `0x2B1824`: `4BFF083D` (`bl 0x802A6B20`) | `J3DModelX::directDraw`, `0x802A6B20` | Immediately before: `mr r3,r29; li r4,0`. For this C++ member call r3 is the `J3DModelX` receiver and r4 is its null `J3DModel*` argument (the method selects its current model). Return is ignored; the draw/GX/model-state side effects are required. Caller next clears `[r29+0x1B0]`. |

All four are relative branch-with-link instructions. A direct branch replacement is **not** behavior-preserving by itself: it bypasses the original target. A trampoline/wrapper needs to preserve the original call's argument state, call the original once when its semantics are required, preserve/return r3 for model acquisition, and return to the instruction after the original call with the expected LR. It must also preserve EABI nonvolatile registers, stack alignment/frame/linkage, r2/r13 small-data anchors if touched, and any CR fields it changes. A naked Gecko/PPC branch does not automatically do any of this.

### Frame and register observations

PAL function entries show normal stack frames: `initDrawAndModel` begins with `stwu r1,-0xD0(r1)` and saves LR; `MarioAnimator`'s constructor uses `-0x10`; `MarioActor::calcAnim` and `drawMarioModel` use their own frames (the latter begins `stwu r1,-0x20(r1)`, saves LR). The linked calls occur inside these frames. The callsites rely on live nonvolatile registers (`r30`, `r27`, `r29`, `r31`) for actor/model/constructor state. A hook must not repurpose those registers unless it restores them. Caller volatile argument registers need not survive a normal call, but a replacement must set up the original callee's exact arguments before chaining it.

## Rendering state and restoration obligations

The current audit establishes call-site ABI facts, not the undocumented internals needed to draw a second pose safely:

- **Model acquisition site:** `getJ3DModel` returns the local model selected by the actor/model-manager path. It is not established as a safe allocation point for additional persistent matrices or animator objects. The surrounding `initDrawAndModel` operation continues after this call to set local flags and model state. No ownership transfer is implied by the returned pointer.
- **Animator init site:** initializes the local animator and belongs to its constructor. The value of r3 is the live animator object; this is not a constructor for a second `XanimePlayer`. Calling init twice or changing constructor sequencing risks resetting local animation state. A static duplicate that copies the already-calculated local pose does not inherently need another animation player, so this callsite is not in the minimum render path.
- **Animator calc site:** computes the local pose. Any remote-specific pose evaluation here would need independent animation state and complete restoration of the local model, joint tree, matrix-buffer pointer, and active animator state. A fixed-offset duplicate that reuses the exact local pose may not need this hook if it can safely derive the additional transform at draw time. That matrix operation and its cache protocol are not yet proven.
- **Draw site:** draw receives `J3DModelX*` in r3 and null in r4. A duplicate must first preserve the regular local draw, then draw the duplicate with an independent transform/matrix view, and restore the local model pointer, base transform, matrix buffer, shape packet/visibility state, current J3D system model, GX state, and caller-visible state before continuing. The DOL callsite alone does not identify or guarantee that full state set.
- **Memory/cache:** the remote matrix storage must be correctly sized/aligned and live across frames, and the CPU writes must use the platform's required data-cache flush/synchronization before GX consumes them. No safe PAL allocation/free pairing or exact `J3DMtxBuffer` layout/lifetime has been verified. The existing local model and its model/animation resources are borrowed and must be reacquired after death, model change, or scene generation change; no borrowed pointer may survive teardown.

## Minimum hook subset and decision

For a **static-offset, same-pose Mario copy**, the animation-init and animation-calc callsites are not functionally required if the duplicate simply copies the current local pose. The only draw-time hook candidate is `drawMarioModel`'s `directDraw` call. However, draw-only is safe only after a separately owned/aligned matrix buffer and complete cache/state restoration strategy have already been created and validated. The model-acquisition site is a possible observation point for the local model pointer, but it is not proven to be a safe allocator/initializer boundary; it is not required if draw-time receives a validated model receiver.

Therefore the **minimum safe subset is currently none**: the one draw call has the smallest behavior surface, but it still lacks verified matrix representation, allocation/ownership, cache maintenance, GX/model state restoration, and teardown. The two init/calc hooks add unnecessary risk for a static pose. Do not use them in a first static-offset proof absent a later specific need.

## Go/no-go gates for first isolated runtime experiment

**Go only when every gate passes:**

1. Exact PAL DOL hash/map and all callsite plus context signatures validate; generated hook plan lists exact displaced words and a reversible restore path.
2. ABI wrapper is independently tested/disassembled: original draw runs exactly once; duplicate draw runs only with valid generation/model state; return to the original continuation is correct; r1/LR, r14-r31, CR2-CR4, and any touched volatile/GX globals meet ABI expectations.
3. PAL evidence establishes the matrix-buffer layout, independent buffer construction, alignment/size, resource ownership, and matching cleanup. No heap choice or pointer is guessed.
4. Draw operation is shown to restore all changed model/J3D/GX state and perform required cache maintenance. Local Mario remains visually and behaviorally unchanged if remote rendering is disabled or fails.
5. Hook writes are statically disjoint from mandatory AllStars and mailbox ranges. M4 protocol and launcher character selection are unchanged. The test image/profile are isolated and rollback can remove the opt-in patch and restore the unmodified DOL/profile without touching originals.
6. Only after offline checks pass, use one fixed offset, no networking, one scene, one local Mario model, and an explicit runtime opt-in. Confirm local rendering, pause/resume, death/respawn, and scene transition; any stale model pointer, state leak, visual regression, or memory error is immediate no-go and rollback.

Current decision: **NO-GO for runtime hook/patch**. Continue static source/DOL research on matrix ownership, cache behavior, and a reversible caller wrapper. No runtime write, hook, or experimental Dolphin launch is authorized by this audit.

## Offline tool coverage

`multigalaxy inspect-render-prototype <PAL-main.dol> <Petari-symbols.txt>` now validates each call instruction, target, known argument setup, and selected post-call consumption words. Synthetic tests reject changed instructions, call arguments, return-consumption context, truncated sites, and unmapped sites. This still does not emulate the function ABI or prove object/matrix safety.

## Preservation

The audit does not alter mandatory AllStars, M4 networking, the launcher character-selection specification, or MultiGalaxy1-Art assets. Upstream `Headpenguin/SMGNetworkMultiplayer` code remains unlicensed/uncertain and was not copied.
