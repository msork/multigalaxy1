# Duplicate Mario actors and remote-player rendering

Research date: 2026-11-09. Scope: Super Mario Galaxy 1 PAL RMGP01 Rev 0, stock Dolphin 2609a, online-only MultiGalaxy1. This is a research note; it adds no hooks, writes, actor creation, or runtime behavior.

## Decision in one sentence

For MultiGalaxy1's first remote player, investigate a **cosmetic render path** and do not instantiate a second `MarioActor`: each client already owns and controls its local Mario, while remote players initially need only visible character poses. Rendering independently avoids importing a second gameplay actor into Mario-specific global state. This choice remains **NO-GO for runtime** until the PAL draw ABI and J3D/GX state lifetime gates below are closed.

## What the other projects establish

### AllReds SMG1 multiplayer

The published AllReds page describes a Mario/Luigi split-screen SMG multiplayer release. Its downloadable artifact is a modified `main.dol`, not source. The project page establishes that a compiled SMG1 modification exists; it does not expose enough implementation evidence to prove how it creates a second actor, separates player state, or handles lifetime. We therefore use AllReds as a binary-analysis reference only and do not attribute an actor architecture to it without independently verified DOL evidence. This project's target remains independent full-screen Dolphin clients, not AllReds' local presentation.

### SMG2 split-screen multiplayer and `multiplayer.S`

The 2012 GBAtemp project report says the team found that multiple `MarioActor` placements could be loaded, initially with both actors responding to P1, then remapped the second actor to a second controller. The author describes separate rendered worlds/cameras, but reports unresolved camera centering, voice selection, screen/HUD issues, and spin coupling; the prototype was reported at 30 FPS. This is a useful precedent for the *possibility* of multiple player actors in SMG2, but it is not proof for SMG1 or PAL RMGP01. [GBAtemp project report](https://gbatemp.net/threads/super-mario-galaxy-2-split-screen-multiplayer.328006/)

The public SMG2 repository's `multiplayer.S` shows the architecture at a high level:

- `spawntwomarios` writes a selector/state value in an `EMPTYMEM` area, avoids its extra-placement path in file select and world-map scenes, calls `initPlacementMario` for an additional Mario placement, then reaches the normal placement initializer.
- `marioorluigi` loads the selected player number, stores it in actor-associated state, then branches into `MarioActor::init`.
- `setmarioandluigiplayeractor` chooses one of two actor fields (`+0x14` or `+0x28`) based on a global player selector before continuing `MarioActor::init`.
- Controller hooks route joystick X/Y reads using the actor's player number; the other-player path can return neutral input. The companion `khooks.S` has per-player hooks for button/trigger/level reads (A/B/Z/C) and Nunchuk stick reads. It also contains attempted player-indexed spin hooks, while the GBAtemp author report says accelerometer/spin independence was not yet solved at that point and the two spins still coupled. Treat the feature as a partial, evolving implementation, not proof of complete independent controller support.
- A collision hook saves the actor pointer that triggered a collision in shared scratch memory before continuing into the collision handler.

These observations come from the SMG2 assembly and its comments, not the SMG1 PAL binary. The exact semantics/ownership of the two actor fields and scratch globals are not established by that file alone. The source repository declares CC0-1.0, but MultiGalaxy1 does not need to copy any code or assets; SMG2 addresses and structure layouts are not transferable to SMG1. [SMG2 multiplayer source](https://github.com/galaxymaster2007/smg2-multiplayer/blob/master/multiplayer.S), [repository license](https://github.com/galaxymaster2007/smg2-multiplayer)

The Super Mario Galaxy Object Database describes `MarioActor` as the player spawn/checkpoint controller and warns that placing it as a generic object creates a "glitchy clone." That supports the conclusion that ordinary placement can produce a second actor-like instance in at least the database's tested context; the warning is evidence against treating this as a safe remote-avatar factory. It does not document which global services the clone shares or prove PAL runtime behavior. [MarioActor object entry](https://smgcommunity.github.io/galaxydatabase/config_MarioActor.html)

### Headpenguin SMGNetworkMultiplayer

The existing [source analysis](smgnetworkmultiplayer-analysis.md) distinguishes two separate systems: `multimodel.cpp` draws multiple remote Mario poses through the local Mario animation/draw callbacks, while `netActor.cpp` creates one separate gameplay/sensor `LiveActor`. The model-rendering path avoids one `LiveActor` and scheduler registration per remote, but mutates shared J3D state and does not prove safe restoration, allocation, or scene lifetime. The standalone actor is not the remote Mario renderer. The upstream tree has no verified license grant, so this is inspiration only; no source is copied.

## Comparison for our target

| Concern | Clone a full `MarioActor` | Independent remote visual |
|---|---|---|
| Required behavior now | More than needed: full movement, input, sensors, collision, gravity, actor life cycle | Matches requirement: remote appearance driven by M4 position/state |
| Local player isolation | Must split MarioAccess/MarioHolder, controller selection, camera target, collision/sensor context, campaign flags, and actor-specific globals | Keeps local Mario as the only gameplay actor; the remote stays outside MarioHolder, input, camera ownership, and collision |
| Scene integration | Placement/factory, AllLiveActorGroup, clipping, execute holder, movement/calcAnim lists, draw buffer, model and archive load | Can reuse the local actor's already scheduled render path; still needs a safe draw hook and bounded render-owned state |
| Failure surface | Constructor side effects, actor registry capacities, interactions, death/respawn, and teardown | J3D mutable state, GX restoration, matrix/packet storage, and stale/scene-bound resource references |
| Different characters | A full actor still assumes Mario/Luigi-specific code and resources; Wario/Waluigi need distinct model/animation integration | Renderer needs a selected model/resource set; current shared-Mario idea only covers Mario-like visuals |
| Evidence fit | SMG2 precedent only; GalaxyDatabase explicitly calls generic-placement clone glitchy; PAL scene-owned spawn safety remains unresolved | M5 has exact PAL Mario/model pointers and a semantically matched PAL `drawMarioModel` callsite, but runtime drawing is unproven |

### PAL-specific facts already verified

- Target binding is exact PAL RMGP01 Rev 0 DOL plus its matching Petari map; no SMG2 address is used as a PAL address.
- `MarioHolder` is a scene object and its `mActor` field resolves the current MarioActor. `MarioAccess` exposes many player-centric services through the active player rather than an independent actor handle. Those global/single-player assumptions are documented in [Petari actor lifecycle research](petari-actor-lifecycle.md) and [Mario model observer](mario-model-observer.md).
- User-reported read-only samples observed different actor/model/matrix-buffer pointers in Beach Bowl and Observatory while `J3DModelData` and its embedded joint tree had equal numeric addresses. This is pointer-value evidence only; it does not establish allocation lifetime or that a duplicate model can share mutable joint data.
- The PAL `drawMarioModel` callsite candidate is `0x802B62E4` (DOL offset `0x2B1824`, original word `0x4BFF083D`) and resolves to `J3DModelX::directDraw` at `0x802A6B20`. This proves a relevant callsite in the verified PAL DOL, not a safe hook or safe second draw. The callsite ABI/state audit is in [PAL render callsite ABI](pal-render-callsite-abi.md); its remaining GX/model/matrix gates are still NO-GO.
- PAL `MR::newJ3DModel` invalidates joint calculation and callback fields in `J3DModelData` before creating the model. A second ordinary model-manager construction may therefore mutate shared model data. The independent-model path must not call that factory with Mario's borrowed model data until this mutation behavior is resolved or the data is safely independent.
- The actor scheduler and scene-lifetime studies show that constructing a LiveActor changes actor, clipping, and execution state. Count headroom is not proof of safe capacity. See [Penguin rendering path](penguin-rendering-path.md) and the M5 scheduler research.

## Recommendation: smallest route to one visible remote Mario

Keep the current direction: one client-local remote-render slot driven by validated remote state, initially using Mario's existing model/animation resources and a synthetic static offset. Do not create a second `MarioActor`, and do not repurpose a Penguin or another campaign actor.

The render slot should own only host-selected remote identity, active/stale state, scene generation, transform, and any independently allocated matrix/animation state. It must not register as a `LiveActor`/`NameObj`, enter MarioHolder, access controller/camera ownership, create collision sensors, or alter M4 networking. Disconnect or stale data hides the slot. A scene transition invalidates it and forces a fresh PAL model binding before rendering resumes.

This is an architectural recommendation only. The current single-model/directDraw approach is still **runtime NO-GO**. A second draw must prove it leaves the original model, animation pose, J3D global current-model state, shape/material packets, GX state, and subsequent actors unchanged. An independent `J3DModelX` is not automatically safer because the PAL factory mutates shared `J3DModelData`. If the minimal Mario-only render proof cannot pass without sharing unsafe mutable state, the next option is a model-data clone with explicit ownership—not a `MarioActor` clone by default.

## Unresolved dependencies and go/no-go gates

1. **Input scope:** prove by PAL source/disassembly which MarioAccess and singleton APIs always target the local actor. Before any actor duplicate is considered, identify all consumers of MarioHolder, player number, `MR::getPlayer*`, camera target, sensor/collision context, demo state, and campaign progression. No SMG2 field offsets substitute for this.
2. **Actor clone behavior:** inspect exact PAL `MarioActor` placement/factory and constructor/init paths; enumerate singleton/global writes and registration order. A second constructed MarioActor stays NO-GO until it can be proven not to replace local-player state and is intentionally required by gameplay.
3. **Draw ABI and displaced code:** establish the callsite's complete live register/stack contract, relocation/trampoline behavior, once-only original call, exception/failure behavior, AllStars disjointness, and rollback. The current callsite table is not an installation authorization.
4. **Rendering-state isolation:** trace all mutable fields touched by `directDraw`, model preparation, GX setup, and model switching; specify save/restore boundaries and validate downstream draw behavior. A second draw must leave the local draw byte-for-byte/state-equivalent at the next scheduled actor boundary where measurable.
5. **J3D resource ownership:** resolve `J3DModelX`, model, matrix arrays, packets, and model-data/joint-tree ownership. Prove either distinct safe model data or a safe draw-only reuse contract; allocation/destruction and scene heap must match.
6. **Animation and identity:** bind the correct character model and animation by protocol character ID, hide stale/disconnected IDs, and avoid implying that model choice changes physics. Wario/Waluigi require separately developed, distributable assets and a verified resource path.
7. **Lifecycle:** exercise pause/resume, local Mario death/respawn/transformation, scene transitions, remote disconnect/reconnect, and repeated scene reloads. No cached game pointer may survive its validated scene generation.

**Go/no-go:** research supports avoiding a full MarioActor for a remote visual. No path is authorized for runtime until gates 3–7 pass for one synthetic one-slot render and an independent visual check confirms the local Mario and subsequent actor draws remain unchanged. All work in this task was source/document review; no emulator was launched and no game memory was written.

## Sources and provenance

- [AllReds SMG1 Multiplayer page](https://allreds.itch.io/super-mario-galaxy-multiplayer) — product description/download listing; binary distribution, no source architecture proof.
- [GBAtemp SMG2 split-screen project thread](https://gbatemp.net/threads/super-mario-galaxy-2-split-screen-multiplayer.328006/) — creator description of second placements, controller remapping, and separate rendering/camera effort.
- [SMG2 `multiplayer.S`](https://github.com/galaxymaster2007/smg2-multiplayer/blob/master/multiplayer.S) and [repository](https://github.com/galaxymaster2007/smg2-multiplayer) — source is SMG2-specific; repository declares CC0-1.0. No code copied.
- [Headpenguin SMGNetworkMultiplayer](https://github.com/Headpenguin/SMGNetworkMultiplayer) — existing local source analysis; no verified license grant in reviewed tree. No code copied.
- [Petari](https://github.com/SMGCommunity/Petari) — SMG1 source reference; exact PAL claims remain tied to the repository's pinned DOL/symbol evidence.
- [Galaxy Database MarioActor entry](https://smgcommunity.github.io/galaxydatabase/config_MarioActor.html) — reports generic placement produces a glitchy player clone; not a lifecycle safety specification.
