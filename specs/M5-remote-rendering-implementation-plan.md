# M5 remote visual implementation plan

Status: **NO-GO for runtime rendering, hook installation, and emulated-memory writes.** This document orders future work; it does not authorize a Dolphin launch or patch.

## 1. Objective

Display one remote Mario visual in the local PAL RMGP01 Rev 0 game while leaving the local Mario's actor, camera, controls, campaign behavior, and original draw intact. The first visual experiment is a fixed, harmless offset and has no network dependency. Expand to network-fed transforms and additional characters only after the one-slot visual is safe.

M4's user-confirmed two-client position relay on Dolphin 2609b is an input-data milestone only. It does not make M5 rendering ready. Keep the M4 protocol, ACK ownership, AllStars inputs, and normal launch path unchanged.

## 2. Verified research and evidence boundaries

| Evidence | What it establishes | What it does not establish |
|---|---|---|
| Exact PAL `drawMarioModel` callsite at `0x802B62E4`, original `bl 0x802A6B20` (`0x4BFF083D`), caller `r3 = r29` model and `r4 = 0`; return ignored | One statically identified draw boundary and its local argument setup | A safe trampoline, executable code placement, or a correct installed hook |
| PAL ABI/callsite audit in `docs/references/pal-render-callsite-abi.md` | One-word call replacement would bypass required `directDraw`; wrapper must preserve frame/LR/ABI and invoke the original exactly once | Full dynamic register/GX/J3D state preservation |
| PAL DOL analysis reports `directDraw` mutating J3D system globals, model fields and GX state in helper paths | A second direct draw has observable state risks | Complete write set, snapshots, restore order, cache protocol or safe duplicate call |
| Live read-only Mario model/resource observations across a scene transition | Sampled pointers and resource holder values; observations support a cached/persistent ModelData interpretation | Allocation identity, destructor execution, no address reuse, or lifetime guarantee |
| `newJ3DModel` invalidation paths reach joint fields in `J3DModelData`; live samples found the observed 30 joint callback/calc fields zero | A specific initialization mutation and bounded live endpoint observations | All shared-data fields are immutable, or independent animation/draw users are safe |
| PAL ownership research traces scene-current-heap allocations and incomplete explicit child destruction | Scene arena reclamation is relevant; current heap at delete may differ from allocation heap | Safe individual destroy, exact owner dispatch, or safe teardown order for a new model |
| Previous `--trampoline-dry-run` attempt reported hook word `0x03D4CB34` at `0x802B62E4`; Gecko/AllStars load order was implicated and delivery is recorded NO-GO pending a clean validation | The prior Stage 1 path had a real failure and must be treated as suspect | That a source-level fix or static test has passed at runtime |

Primary references: [PAL callsite ABI](../docs/references/pal-render-callsite-abi.md), [draw-only contract and J3D ownership tables](../docs/references/draw-only-render-contract.md), [Mario model observer](../docs/references/mario-model-observer.md), [resource-holder observer](../docs/references/resource-holder-observer.md), and [M5 research history](M5-remote-player-rendering.md).

## 3. Functional constraints

1. The original local Mario `directDraw` must execute exactly once on every path.
2. Remote visual state is presentation-only. No remote actor may replace or alter MarioAccess/MarioHolder, controller identity, collision, progression, or camera.
3. Invalid/missing/stale remote state hides the visual and falls through to original rendering.
4. A scene/model generation change, death/respawn, model-form change, pause/reset, or resource mismatch invalidates all remote pointers before dereference.
5. No Rust network thread writes game memory or touches J3D/GX state. Any eventual game-side work runs on the established emulation/render thread boundary.
6. Each active visual owns every mutable matrix/packet/draw field it changes, or the change must be proven restored before returning to the local draw path.
7. Preserve AllStars code, M4 mailbox and client behavior, original ISO, and regular Dolphin profile.

## 4. Technical design under evaluation

Continue with one cosmetic draw-only slot rather than a second `MarioActor`; a full actor adds unproven Mario globals, scheduler, controller, collision, factory and teardown dependencies. Use local Mario only as an appearance/resource reference. Do not call `MR::newJ3DModel` against shared `J3DModelData` until all shared-joint and callback ownership is proven; current evidence is insufficient.

Before implementation, resolve whether an independent model instance requires a deep/partial resource clone or can safely reference shared data. Determine exact constructor/destructor and heap semantics, complete mutable writes from `directDraw` and helpers, matrix-buffer array shape/flags, packet/display-list mutation, GX/J3D global save/restore, cache flush/order, and scene teardown. If any mutable shared field cannot be isolated or restored, stop and evaluate a different render route (for example a separately owned minimal model/resource), without changing M4.

## 5. Staged work and go/no-go gates

### Gate A — Freeze and validate the baseline (offline only)

- Validate the pinned PAL DOL identity and symbols; run `inspect-render-prototype` and record the exact original callsite/context signature.
- Capture hashes of the disposable source DOL/image, AllStars input set, and the current M4/Gecko configuration used as the baseline. Never overwrite source inputs.
- Reproduce the prior dry-run failure from its saved logs/artifacts if present. Audit the generated Gecko command ordering, code-pair parity, stub destination, branch encoding, cave ownership, and read-only preflight. Do not run or regenerate the hook against Dolphin during this gate.
- **Go:** all inputs are identified and the previous failure mechanism is explained by code/tests. **No-go:** hook word or cave ownership remains ambiguous.

### Gate B — Complete the static render-state and ownership audit (offline only)

- Trace PAL `directDraw`, `viewCalcRef`, shape/material packet helpers, and every callee that mutates J3D system/GX state. Enumerate each changed field/register and prove either instance-local ownership or a bounded restore sequence.
- Resolve matrix counts, flags, alignment, cache line rounding, writeback/synchronization, and when GX consumes the data.
- Close the native allocation/lifetime graph: model, matrix buffer and arrays, shape/material packets, nested display lists, borrowed ModelData/archive, current heap, cleanup, and scene teardown ordering.
- Prove how the remote slot is invalidated before scene/model/resource reuse. Read-only pointer equality alone is not lifetime evidence.
- **Go:** the complete render and cleanup state can be bounded and independently reviewed. **No-go:** any unknown mutable field, free owner, cache boundary, or teardown ordering.

### Gate C — Host-side and artifact-only validation

- Add synthetic wrapper/state-machine tests for exactly one original draw, clone-skipped fallback, invalid-generation hide, disjoint local/remote buffers, unchanged local-state canaries, bounded cache ranges, and allocation failure rollback. Clearly label these as synthetic.
- Generate any candidate only from a disposable copy through the existing manifest-backed partition-aware pipeline. Verify source immutability, exact output DOL/image hashes, PAL identity, AllStars non-overlap, M4 mailbox non-overlap, and deterministic output. Do not patch the original image.
- Verify rollback offline by rebuilding a fresh disposable output from the pinned unmodified PAL source and comparing it byte-for-byte with the baseline artifact. Do not rely on editing an already mutated image in place.
- **Go:** all tests and artifact checks pass with no production configuration changes. **No-go:** unexpected bytes, ambiguous code ordering, or any overlap.

### Gate D — Pass-through hook qualification (future, separately approved runtime test)

Only after Gates A–C pass, propose a distinct user-controlled test using a disposable ISO and fresh isolated Dolphin profile. It must install no duplicate draw: wrapper calls original `directDraw` once and returns. Manually verify unchanged local rendering/controls, title-to-scene boot, pause/resume, death/respawn, and one scene transition. Read-only `verify-hook` must show only expected bytes before/after; disabling must restore exact original `0x4BFF083D`. Any mismatch, black screen, changed visual, hang, or state drift is immediate rollback. Do not launch Dolphin automatically.

The prior reported corrupted callsite word means this gate is currently **NO-GO**. Do not reuse the previous profile or assume the historical load-order fix is sufficient.

### Gate E — One static-offset duplicate draw (future, separately approved)

Proceed only after pass-through qualification and all Gate B state/lifetime proofs. Use one Mario visual, one scene, a fixed offset, no network, no actor creation, and an explicit disposable-profile opt-in. Compare with the pass-through baseline. Verify the original draw count remains one; remote draw occurs at most once; disabling/hiding the remote slot leaves local state canaries and visible output unchanged. Test valid/invalid resource, pause/resume, death/respawn, and scene transition. On any failure, close the test manually, discard disposable artifacts/profile, and return to the verified no-hook baseline.

### Gate F — Connect M4 data (future)

Only after Gate E passes, map the already validated network snapshot into a presentation-only transform. Exercise stale, malformed, disconnected, and scene-transition data; stale input must hide the remote visual without touching gameplay. M4 changes are not a prerequisite and are prohibited unless a separate demonstrated defect is found.

## 6. Risks and stop conditions

- No executable stub placement is currently established as safe; prior hook attempt had a corrupted callsite word.
- Reusing `J3DModelData` is not proven immutable; `newJ3DModel` mutates fields reachable from it.
- `J3DModelX`/matrix/packet cleanup and current-heap identity do not establish a complete destructor path.
- `directDraw` mutates global, per-model and GX state; complete restoration and cache ordering are unknown.
- A rendered duplicate might be visually correct while corrupting the next actor's GX/J3D state. Test a following actor and later scene, not only Mario's silhouette.
- If exact evidence is absent, do not infer it from compile success, host-side ownership tests, pointer reuse, or a normal game boot.

## 7. Testing and current decision

Current allowed work: static PAL analysis, read-only observers already present, host-only synthetic tests, and disposable artifact validation. This task adds no rendering implementation and performs no Dolphin launch or memory write.

Current decision: **M5 runtime rendering remains NO-GO.** The smallest next action is an offline, complete write-set audit of the `directDraw`/material/GX path and a separate audit explaining the previous trampoline corruption. Only if both close may a disposable pass-through test be proposed.
