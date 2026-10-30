# Rendering

MultiGalaxy1 is online-only. Every participant runs one Dolphin instance and receives the original full-screen camera behavior locally. Multi-client rendering means rendering remote player actors in each independent game instance; it does not mean split-screen.

The game-side bridge must create/update remote character actors through verified Galaxy engine operations, including model, animation, gravity-relative orientation, and appropriate effects. Remote actors must not take over local camera ownership or consume local controller input. No engine addresses or actor APIs are known to be verified yet. Two-instance visible remote-actor rendering is the M3 runtime acceptance gate.
