# M5: Read-only ResourceHolder/ResTable identity observer

Status: implemented and statically validated; live sampling pending. The observer is opt-in and reads stock Dolphin's validated read-only MEM1 mapping only. It performs no game-memory writes, holder destruction, callback invocation, hooks, actor creation, manager-list enumeration, or M4/AllStars changes.

## Objective

Take one bounded snapshot of the ResourceHolder/ResTable identities behind the existing Mario model — `ModelManager+0x20` holder, `holder+0x00` ResTable, `holder+0x48` archive, ResTable entries, and the entry resolving to the sampled `J3DModelData` — alongside the already-validated Mario model chain and scene/current heap identities, so that two samples across a Beach Bowl → Comet Observatory transition can distinguish a persistent resource holder from reload-at-reused-address. `observe-mario-model` output stays byte-stable for comparison with the PID 119844 samples.

## Verified research

- PAL `ModelManager::getModelResourceHolder` (`0x8016973C`) returns `ModelManager+0x20`; `initModelAndAnimation` (`0x8016979C`) stores the model holder at `+0x20` (`0x801697B0`) and, on the Xanime branch, the animation holder at `+0x14`.
- PAL `MR::newJ3DModel` (`0x803E9668`) loads `ResourceHolder+0x00` as the `ResTable*` (`0x803E9680`) and resolves the model data via `ResTable::getRes(char const*)` (`bl 0x803A73E0` at `0x803E9690`).
- `ResTable::getRes(u32)` (`0x803A73D0`) reads the entry array from `ResTable+0x00` with 0x18-byte entries and returns entry `+0x00`; `getResName` reads entry `+0x10`; `getResIndex` (`0x803A74B0`) compares entry `+0x14` hashes. ResTable constructor (`0x803A72EC`) zeroes `+0x00`/`+0x04`.
- `ResourceHolder` is 0x50 bytes (`createResourceHolder` `0x803A6F28` calls `__nw(0x50)`), stores the archive at `+0x48` (ctor `0x803A5EF8`, constructed with `-1` pre-mount), and has no `__vt__14ResourceHolder` vtable, so no vptr check applies.
- Manager entries are 0x10-byte `{name, holder, hash, heap}` records (`add` at `0x803A72A8`, `find` hash compare at `0x803A7150`); the recorded heap is entry `+0x0C`, matched against the scene heap by `removeIfIsEqualHeap` (`0x803A6BCC`, compare at `0x803A6BFC`). `createAndAdd` (scene-heap-keyed) and `createAndAddStationed` (persistent) are the two construction paths.
- All offsets/signatures are pinned in `verify_pal_layout` against the exact PAL DOL and Petari symbol hash; live signatures are checked before and after sampling; AllStars overlap is rejected.

## Functional requirements

- New opt-in command `observe-resource-holder <development.local.toml> <Dolphin-pid>`; `observe-mario-model` output unchanged.
- Validate configured PAL DOL, exact Petari symbols, stock Dolphin 2609a executable/session/codehandler, and mandatory AllStars inputs exactly as the other observers do.
- Re-resolve the full Mario model chain via `mario_model_observer::observe` before and after the holder reads; reject any change to scene, actor group, MarioActor, ModelManager, model, model data, matrix buffer, or heap identities.
- Read only fixed, PAL-verified fields: `ModelManager+0x20` (required), `+0x14` (nullable), holder span 0x50 bytes, `holder+0x00`, `holder+0x48`, ResTable header 8 bytes, and a bounded probe of at most 16 leading ResTable entries to locate the entry whose resource pointer equals the sampled `J3DModelData`.
- Report `heap_free_list=unavailable` and `scene_serial_generation=unavailable`; pointer equality is labeled `not_lifetime_proof`.
- Keep `probe-mario`, M4 networking, AllStars, and launcher character selection unchanged.

## Risks

- Emulation continues during reads; double resolution reduces but cannot eliminate the race, and any mismatch fails closed.
- Pointer equality across two samples supports persistence but cannot prove uninterrupted lifetime or destructor execution; the heap free-list discriminator is intentionally out of scope unless the holder evidence is ambiguous.
- The archive field may hold the pre-mount sentinel; it is range-checked but never dereferenced.

## Acceptance criteria

- `verify_pal_layout` accepts only the exact PAL Rev 0 DOL/symbol set; synthetic tests cover both holder branches, the entry probe (hit/miss/window), invalid pointers, span overflow, and patch overlap.
- Output includes holder, ResTable, entries, count, entry index, archive, and the re-validated chain/heap tuple with a timestamp.
- No runtime claim is made until the user runs the command twice around one ordinary transition in an interactive Dolphin session.

## Testing strategy

`cargo fmt --all -- --check`, `cargo test --workspace`, `cargo build --workspace`, `validate-mario-map`, the render-callsite audit, and `git diff --check`. During development `verify_pal_layout` was also run directly against the repository's PAL DOL and Petari map. No Dolphin instance is launched by this work.
