# Networking

MultiGalaxy1 is online-only. Each participant runs their own official, unmodified Dolphin instance, full-screen local camera, and independent controller. Dolphin NetPlay and local split-screen are excluded.

## M4 two-player position relay

The implemented M4 prototype has one Rust loopback server and two independent Rust clients. Each client attaches to an explicit Dolphin PID, verifies the configured executable path, PAL RMGP01 identity, known Gecko handler capability fingerprint, unique mailbox signature, and consistent coordinate snapshot. The network client opens the MEM1 view read-only; a separate `dev-launch --gecko-bridge-probe` process owns the bounded ACK write for its Dolphin. Each instance needs its own bridge-probe console, process, and isolated profile. A user-run two-instance session passed on Dolphin 2609b: both clients connected to `127.0.0.1:39845` and received each other's positions. See the [M4 live checkpoint](references/m4-two-client-2609b.md).

The server accepts one Mario ID and one Luigi ID. It authoritatively binds each TCP session to its Hello ID, validates PAL game ID/revision, protocol version, bounded frame shape, timestamp freshness, sequence order, and finite/range-bounded coordinates, then relays approved presence/position snapshots. It does not simulate the game or synchronize progression, health, input, interactions, or actors. The clients print remote identity, XYZ, sequence, receive age, and approximate update rate. No position is written into game memory and no remote actor is spawned.

Protocol version 1 is big-endian, framed, and bounded to a 26-byte header plus at most 32 payload bytes. Header fields are magic `MG1N`, version, message kind, player ID, sequence, Unix-millisecond timestamp, and payload length. Hello carries `RMGP01`, revision 0, and the local Dolphin PID; the server rejects both player IDs attached to one PID. Position carries mailbox frame counter and three `f32` values. The server binds only to loopback; there is no authentication/encryption or internet hosting claim. See [the M4 spec](../specs/M4-two-player-position-sync.md).

The server marks a peer absent after one second without a fresh position and emits PeerGone on disconnect. Each client also expires a peer after one second. TCP loss triggers client reconnect with a one-second retry delay; a duplicate player ID or full two-player lobby is rejected. During title screens, pause, missing MarioHolder, or scene transitions, no new position is sent; clients remove the remote state after the freshness timeout.

Automated tests cover codec round trips, malformed headers/lengths, unsupported versions/IDs, nonfinite/out-of-range values, wrong game/revision, stale/future timestamps, out-of-order sequence, synthetic two-client relay, reconnect, disconnect, and stale-presence removal. Separately, the user confirmed a live two-client Dolphin 2609b relay in both directions. The observed Luigi→Mario stream had changing coordinates, generally about 58 Hz and 0–16 ms reported age. This does not validate remote rendering or synchronized gameplay; multiplayer-load performance beyond this position relay was not measured.

## Two-instance interactive workflow

Prepare two different directories once so Dolphin configuration and saves are isolated:

```powershell
cargo run -p multigalaxy -- dev-setup development.local.toml 'target\mg1-sync-mario'
cargo run -p multigalaxy -- dev-setup development.local.toml 'target\mg1-sync-luigi'
```

Use five separate interactive PowerShell windows on the same Windows desktop. The existing `target\mg1-sync-mario` and `target\mg1-sync-luigi` development profiles are distinct and marked prepared. Stop any old bridge probe with `Q`, then close its Dolphin through the GUI before this run; do not reuse one Dolphin PID for both player identities. Start the local server in window 1:

```powershell
cargo run -p multigalaxy -- sync-server 127.0.0.1:39845
```

Both launchers should select the same validated, manifest-backed patched PAL ISO and source ISO. From the repository root, set the local WIT executable path in each launcher window:

```powershell
$Wit = (Resolve-Path (Read-Host 'Full path to installed wit.exe')).Path
$SourceIso = (Resolve-Path 'target\mg1-disc-pipeline\source.iso').Path
$PatchedIso = (Resolve-Path 'target\mg1-disc-pipeline\changed-neutral-nop.iso').Path
```

In windows 2 and 3, set those three variables separately in each PowerShell window, then run only the matching launch command. Each launcher validates the rebuilt image and mandatory AllStars inputs, starts a separate Dolphin, and owns the ACK writer only for that instance. Record the printed Dolphin PIDs:

```powershell
cargo run --release -p multigalaxy -- dev-launch development.local.toml 'target\mg1-sync-mario' 0 --gecko-bridge-probe --patched-image $PatchedIso --source-iso $SourceIso --wit $Wit
cargo run --release -p multigalaxy -- dev-launch development.local.toml 'target\mg1-sync-luigi' 0 --gecko-bridge-probe --patched-image $PatchedIso --source-iso $SourceIso --wit $Wit
```

In window 4, set Mario's PID, run its non-mutating preflight, then attach the read-only network client:

```powershell
$MarioPid = [uint32](Read-Host 'Mario Dolphin PID')
cargo run --release -p multigalaxy -- validate-m4-read-only development.local.toml $MarioPid
cargo run --release -p multigalaxy -- sync-client development.local.toml $MarioPid 127.0.0.1:39845 Mario
```

In window 5, set Luigi's PID, run its non-mutating preflight, then attach the read-only network client:

```powershell
$LuigiPid = [uint32](Read-Host 'Luigi Dolphin PID')
cargo run --release -p multigalaxy -- validate-m4-read-only development.local.toml $LuigiPid
cargo run --release -p multigalaxy -- sync-client development.local.toml $LuigiPid 127.0.0.1:39845 Luigi
```

The user-confirmed run used Dolphin PIDs 9312 (Mario) and 13420 (Luigi), separate isolated profiles and ACK writers, and clients connected to `127.0.0.1:39845`. Position reception passed in both directions; Luigi movement showed changing XYZ in Mario's client, generally ~58 Hz with reported ages 0–16 ms. For a repeat, enter the same playable scene in both games, verify both clients connect, move each local character in turn, and check the opposite client receives changing coordinates. Pause/resume, scene-transition recovery, explicit peer removal, emulation speed under test, remote rendering, and game-world gameplay synchronization were not included in the reported pass.

Cleanup order: Ctrl+C both sync-client consoles; type `Q` in each bridge-probe console (this stops/unmaps its ACK writer but leaves that Dolphin running); close each Dolphin using its GUI; then Ctrl+C the localhost server. Confirm both Dolphin processes have exited. Do not terminate the wrong process or share either isolated profile.

The isolated profiles are separate, but both test users may import the same user-owned save as input. Nothing here copies a profile into the other or alters the regular Dolphin profile or original RVZ.

## Future scope

Progression authority, prediction/reconciliation, remote rendering, four-player identity allocation, and Russian/French/English cross-localization validation remain future work. The M4 relay is not evidence of multiplayer gameplay. Classic Controller mapping and Wario/Waluigi abilities remain on their existing roadmap.
