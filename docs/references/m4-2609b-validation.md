# M4 bridge compatibility on Dolphin 2609b

## Read-only preflight — user verified

The user reported a successful `validate-m4-read-only` result on Dolphin 2609b:

```text
m4_read_only_preflight=passed
pid=8024
session=1
game=RMGP01
revision=0
mapping=read_only
mapping_view_bytes=65536
handler=verified_gecko_signature
handler_region=0x80001800..0x80003000
mailbox=stable_unique@0x800025a0
elapsed_ms=100
ack_write=not_performed
network=not_started
```

This verifies the running process identity/session, compatible MEM1 mapping extent, live PAL signatures, expected Gecko handler resource, and unique stable mailbox. It does **not** verify writable mapping access, ACK echo, two-client relay, AllStars activation, or M5 rendering.

## Controlled ACK-only experiment

`test-m4-ack` is separate and is never run by `dev-launch` or the read-only preflight. It requires the explicit `--confirm-single-ack-write` flag and an already-running Dolphin instance with the mailbox code active. Stop any `sync-client` or other bridge process attached to that Dolphin before running it.

```powershell
Get-Process -Name Dolphin | Format-Table Id, Path, Responding
$DolphinPid = [uint32](Read-Host 'Enter the running Dolphin PID')
cargo run --release -p multigalaxy -- test-m4-ack development.local.toml $DolphinPid --confirm-single-ack-write
```

Before writing, the command repeats read-only process/session, PAL DOL signature, handler, mailbox-identity, heartbeat, and ACK/echo-quiescence checks. It writes one distinct token only to the validated mailbox ACK word, waits at most three seconds for the game echo, then restores the prior ACK only if the same mailbox remains valid and the ACK still equals its own token. It performs no network operation and starts no Dolphin process. If another writer changes ACK or the game/mapping disappears, it refuses to overwrite that state and reports that rollback could not be confirmed. That process-exit/concurrent-writer case is an explicit limitation; run only with no other bridge writers.

## Two-client movement regression after ACK passes

This has **not** been run on 2609b. Use two prepared isolated profiles and two independent Dolphin instances, both with PAL RMGP01 Rev 0 and the mailbox-enabled launch option. Start the localhost server, then attach one `sync-client` to each PID. Move Mario in one instance and confirm the other console receives current position updates; reverse direction and confirm again. Also record disconnect/peer removal. The existing M4 protocol is unchanged. Do not treat the read-only preflight or ACK-only test as multiplayer success.

## Next M5 step

Only after the two-client M4 test passes should remote position data be fed into a visual test. M5 remains NO-GO: independent J3D mutable state, GX state restoration, matrix/packet lifetime, and scene teardown are unresolved. No remote actor/render hook is authorized by this bridge validation.
