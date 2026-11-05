# Networking

MultiGalaxy1 is online-only. Each participant runs their own official, unmodified Dolphin instance, full-screen local camera, and independent controller. Dolphin NetPlay and local split-screen are excluded.

## M4 two-player position relay

The implemented M4 prototype has one Rust loopback server and two independent Rust clients. Each client attaches to an explicit Dolphin PID, verifies the configured executable path, PAL RMGP01 identity, Dolphin 2609a Gecko handler fingerprint, unique mailbox signature, and consistent coordinate snapshot. The network client opens the MEM1 view read-only; a separate `dev-launch --gecko-bridge-probe` process owns the existing bounded ACK write. Each participant must use a different isolated Dolphin profile.

The server accepts one Mario ID and one Luigi ID. It authoritatively binds each TCP session to its Hello ID, validates PAL game ID/revision, protocol version, bounded frame shape, timestamp freshness, sequence order, and finite/range-bounded coordinates, then relays approved presence/position snapshots. It does not simulate the game or synchronize progression, health, input, interactions, or actors. The clients print remote identity, XYZ, sequence, receive age, and approximate update rate. No position is written into game memory and no remote actor is spawned.

Protocol version 1 is big-endian, framed, and bounded to a 26-byte header plus at most 32 payload bytes. Header fields are magic `MG1N`, version, message kind, player ID, sequence, Unix-millisecond timestamp, and payload length. Hello carries `RMGP01`, revision 0, and the local Dolphin PID; the server rejects both player IDs attached to one PID. Position carries mailbox frame counter and three `f32` values. The server binds only to loopback; there is no authentication/encryption or internet hosting claim. See [the M4 spec](../specs/M4-two-player-position-sync.md).

The server marks a peer absent after one second without a fresh position and emits PeerGone on disconnect. Each client also expires a peer after one second. TCP loss triggers client reconnect with a one-second retry delay; a duplicate player ID or full two-player lobby is rejected. During title screens, pause, missing MarioHolder, or scene transitions, no new position is sent; clients remove the remote state after the freshness timeout.

Automated tests cover codec round trips, malformed headers/lengths, unsupported versions/IDs, nonfinite/out-of-range values, wrong game/revision, stale/future timestamps, out-of-order sequence, live two-client relay, reconnect, disconnect, and stale-presence removal. These synthetic tests are not live Dolphin evidence. The user reports that the mailbox provides live Mario X/Y/Z at approximately 60 Hz; the live two-instance network relay and its effect on gameplay performance have not yet been observed.

## Two-instance interactive workflow

Prepare two different directories once so Dolphin configuration and saves are isolated:

```powershell
cargo run -p multigalaxy -- dev-setup development.local.toml 'target\mg1-sync-mario'
cargo run -p multigalaxy -- dev-setup development.local.toml 'target\mg1-sync-luigi'
```

Use separate interactive PowerShell windows. Start the local server in the first:

```powershell
cargo run -p multigalaxy -- sync-server 127.0.0.1:39845
```

In the second and third windows, launch one PAL game per distinct isolated profile with the required bridge and AllStars configuration. Record the PID printed by each launcher:

```powershell
cargo run -p multigalaxy -- dev-launch development.local.toml 'target\mg1-sync-mario' 0 --gecko-bridge-probe
cargo run -p multigalaxy -- dev-launch development.local.toml 'target\mg1-sync-luigi' 0 --gecko-bridge-probe
```

In two more PowerShell windows, attach the network clients to the matching PIDs:

```powershell
cargo run -p multigalaxy -- sync-client development.local.toml <MARIO_DOLPHIN_PID> 127.0.0.1:39845 Mario
cargo run -p multigalaxy -- sync-client development.local.toml <LUIGI_DOLPHIN_PID> 127.0.0.1:39845 Luigi
```

Enter playable scenes in both Dolphins. Each sync-client console should print the other player's positions and update rate. Move/jump each Mario independently, pause one game, transition scenes, and close one client to verify the remote entry expires or disappears. This workflow has not yet been run by the project maintainer. Stop sync clients with Ctrl+C, stop the bridge consoles with `Q`, close both Dolphin windows, then stop the server with Ctrl+C.

The isolated profiles are separate, but both test users may import the same user-owned save as input. Nothing here copies a profile into the other or alters the regular Dolphin profile or original RVZ.

## Future scope

Progression authority, prediction/reconciliation, remote rendering, four-player identity allocation, and Russian/French/English cross-localization validation remain future work. The M4 relay is not evidence of multiplayer gameplay. Classic Controller mapping and Wario/Waluigi abilities remain on their existing roadmap.
