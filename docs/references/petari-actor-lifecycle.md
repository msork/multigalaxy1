# Petari actor lifecycle research (SMG1 PAL)

This note supports [M5 remote-player rendering](../../specs/M5-remote-player-rendering.md). It separates source-level semantics from PAL binary/runtime validation.

## Evidence levels

- **Source-verified:** current Petari source declares/implements the described classes and behavior. Petari is a decompilation/reference project, not proof that a proposed MultiGalaxy1 actor works in the running game.
- **PAL symbol-map evidence:** `target/petari-rmgp01-symbols.txt` names an entry and gives a PAL Rev 0 DOL address associated with Petari's RMGP01 build hash. Not every address has had instruction bytes or call sites verified.
- **Static deduction:** architectural consequences drawn from source and symbols.
- **Runtime-unverified:** requires inspection or a controlled Dolphin experiment.

## Actor and scheduler model

Source-verified in Petari headers:

- `NameObj` is a named object with virtual initialization, after-placement, movement, draw, animation, and view/entry methods. `LiveActor` derives from it and adds actor lifecycle methods, transform, velocity, gravity, model/animation, and collision-related members.
- `LiveActor` declares position at `+0x0C`, rotation at `+0x18`, velocity at `+0x30`, gravity at `+0x3C`, model manager at `+0x48`, animation keeper at `+0x4C`. Offsets are relative to the LiveActor object layout and are not generic RAM addresses.
- `NameObjExecuteHolder` has connect/disconnect operations; `NameObjExecuteInfo` separates scene scheduling and draw connection; `NameObjListExecutor` registers LiveActors in draw buffers. This indicates explicit registration and teardown rather than rendering every allocated object automatically.
- Petari exposes scene categories including player, player decoration, NPC, and enemy, plus corresponding calc-animation/draw paths in its reverse-engineering viewer. The viewer is corroborating reference material, not the original engine implementation.

Relevant PAL symbol-map candidates:

| Symbol | Address | Size |
|---|---:|---:|
| `__ct__9LiveActorFPCc` | `0x80165204` | `0xE8` |
| `initModelManagerWithAnm__9LiveActorFPCcPCcb` | `0x801659C4` | `0xA0` |
| `__ct__12ModelManagerFv` | `0x80168F5C` | `0x30` |
| `init__12ModelManagerFPCcPCcb` | `0x80169AD0` | `0x15C` |
| `connectToScene__18NameObjExecuteInfoFv` | `0x80261F48` | `0x7C` |
| `disconnectToScene__18NameObjExecuteInfoFv` | `0x80261FC4` | `0x7C` |
| `connectToScene__20NameObjExecuteHolderFP7NameObj` | `0x802621F8` | `0x44` |
| `disconnectToScene__20NameObjExecuteHolderFP7NameObj` | `0x80262280` | `0x48` |
| `registerDrawBuffer__19NameObjListExecutorFP9LiveActori` | `0x8026C004` | `0x8` |
| `init__10MarioActorFRC12JMapInfoIter` | `0x802AEC60` | `0x120` |
| `movement__10MarioActorFv` | `0x802AFD9C` | `0x9D4` |
| `calcAnim__10MarioActorFv` | `0x802B2A44` | `0x7F4` |
| `draw__10MarioActorCFv` | `0x802B6D18` | `0xB8` |
| `connectToScene__2MRFP9LiveActoriiii` | `0x803EEF08` | `0x4` |
| `connectToSceneNpc__2MRFP9LiveActor` | `0x803EF020` | `0x14` |

The `0x4` size on the generic `connectToScene` map record is a stub/thunk-sized symbol and must not be treated as the implementation without following its branch. The NPC helper is a symbol candidate, not yet proven to be the correct category for a remote player. Likewise, the `NameObj` scheduler symbols are not approved hook points.

## Mario is not a safe proxy

Petari `MarioActor` constructor allocates a `Mario` movement object, sets health and player modes, and initializes extensive state. Its movement and animation methods are large. The actor is integrated with MarioHolder/player-specific controllers and effects. Therefore, constructing a second MarioActor solely to draw a remote participant is an unsafe design assumption. Reusing or replacing the local Mario pointer is explicitly disallowed.

Preferred design deduction: a distinct cosmetic actor with its own model manager and no collision/sensors/binder/player/camera registration, owned by active-scene lifetime and registered only in verified movement/calcAnim/draw scheduler paths. Use a normal existing resource as a temporary visual if its archive and model initialization requirements are proven. Do not clone an actor's resource pointers or model manager until ownership rules are known.

## Address, ABI and hook constraints

Addresses above are PAL Rev 0 DOL virtual addresses and may be used only after exact game ID, revision, DOL hash, and surrounding instruction bytes match. They are not actor instance addresses. No remote actor object size, allocator entry point, constructor signature for a candidate placeholder, full call prototype, caller stack layout, vtable, resource name, or safe hook location is currently certified by this research note.

Petari declarations use the Wii PowerPC C++ ABI/toolchain conventions. A future Rust/PPC or assembly bridge must verify the actual PAL calling convention at every boundary: 32-bit big-endian pointers, argument registers/stack placement, return register, LR/stack frame preservation, callee-saved registers, object/vtable layout, and constructor/destructor behavior. Do not call these methods through guessed Rust `extern` signatures. The minimal integration layer should be a version-checked PPC trampoline or compatible C++ shim only after disassembly and call-site evidence.

Actor memory must come from a verified game allocator and be released according to the same heap/lifetime rules as native actors. Scene teardown must disconnect update and draw scheduling before resource/model destruction. Until this ordering is verified, no allocation or scene registration is safe.

## Smallest next experiment

Before writing guest memory or spawning anything, use read-only tooling in a PAL playable scene to trace one existing non-player `LiveActor`: identify its object pointer from a verified registry, inspect its vtable and transform ranges, and observe scheduler/draw-list membership across appear/kill and a scene transition. Correlate those observations with exact PAL disassembly and source. This validates object layout/lifetime without changing game state. Only after this succeeds should a separately reviewed local-only experiment create one noninteractive placeholder through the game's verified allocator and normal scene scheduler.

## References

- [Petari `LiveActor.hpp`](https://github.com/SMGCommunity/Petari/blob/master/include/Game/LiveActor/LiveActor.hpp)
- [Petari `NameObj.hpp`](https://github.com/SMGCommunity/Petari/blob/master/include/Game/NameObj/NameObj.hpp)
- [Petari `MarioActor.cpp`](https://github.com/SMGCommunity/Petari/blob/master/src/Game/Player/MarioActor.cpp)
- [Petari `MarioHolder.cpp`](https://github.com/SMGCommunity/Petari/blob/master/src/Game/Player/MarioHolder.cpp)
- [Petari `MarioAccess.cpp`](https://github.com/SMGCommunity/Petari/blob/master/src/Game/Player/MarioAccess.cpp)
- [Petari RMGP01 build hash](https://github.com/SMGCommunity/Petari/blob/master/config/RMGP01/build.sha1)
- [noclip.website SMG NameObj/scheduler model](https://github.com/magcius/noclip.website/blob/main/src/SuperMarioGalaxy/NameObj.ts) (independent viewer implementation; corroboration only)
