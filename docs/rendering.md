# Rendering

MultiGalaxy1 is online-only. Every participant runs one Dolphin instance and receives the original full-screen camera behavior locally. Multi-client rendering means rendering remote player actors in each independent game instance; it does not mean split-screen.

The game-side bridge will eventually create/update a separate cosmetic scene actor through verified Galaxy engine operations. It must not replace local Mario, take camera ownership, consume local controller input, or participate in collisions/progression. M5 Phase 1 found PAL symbol-map candidates for LiveActor construction/model setup and NameObj scene/draw scheduling; these are research leads, not approved hooks or runtime-verified APIs. See [M5 spec](../specs/M5-remote-player-rendering.md) and [Petari actor lifecycle research](references/petari-actor-lifecycle.md). No actor has been spawned and no remote rendering has been verified.

The M4 relay currently supplies position only. Rendering on SMG1's changing gravity frames also needs scene epoch and orientation/gravity data. Until that protocol extension exists, coordinates alone are not sufficient to orient or safely interpolate the remote visual. The next gate is read-only validation of a known non-player actor's object layout and scheduler lifetime in PAL Dolphin; no gameplay writes are authorized during M5 Phase 1.
