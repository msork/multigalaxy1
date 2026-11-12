# M4 bridge compatibility on Dolphin 2609b

## Read-only preflight â€” user verified

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

This verifies the running process identity/session, compatible MEM1 mapping extent, live PAL signatures, expected Gecko handler resource, and unique stable mailbox. At that point, it did **not** verify writable mapping access, ACK echo, two-client relay, AllStars activation, or M5 rendering. Later ACK and two-client results are recorded below.

## Writable ACK feedback passed

The user subsequently confirmed that the explicit ACK-only test passed on Dolphin 2609b, PID 4712, with the same PAL mailbox. It performed one test write, received the game-side echo, performed one rollback write, and restored the prior ACK:

```text
m4_ack_test=passed pid=4712 mailbox=0x800025a0 test_write=1 rollback_write=1 ack_restored=0x4dd6ed41 network=not_started
```

This is verified writable mailbox feedback and rollback for this process/build. At the time of the ACK-only test, no network session had been started. The later two-client relay checkpoint is recorded separately below; this ACK test alone does not validate AllStars runtime behavior, progression, or gameplay synchronization.

## Controlled ACK-only experiment

`test-m4-ack` is separate and is never run by `dev-launch` or the read-only preflight. It requires the explicit `--confirm-single-ack-write` flag and an already-running Dolphin instance with the mailbox code active. Stop the `dev-launch --gecko-bridge-probe` host writer and any other process that writes this mailbox before running it. The Rust `sync-client` opens the mapping read-only and is not itself an ACK writer.

```powershell
Get-Process -Name Dolphin | Format-Table Id, Path, Responding
$DolphinPid = [uint32](Read-Host 'Enter the running Dolphin PID')
cargo run --release -p multigalaxy -- test-m4-ack development.local.toml $DolphinPid --confirm-single-ack-write
```

Before writing, the command repeats read-only process/session, PAL DOL signature, handler, mailbox-identity, heartbeat, and ACK/echo-quiescence checks. It writes one distinct token only to the validated mailbox ACK word, waits at most three seconds for the game echo, then restores the prior ACK only if the same mailbox remains valid and the ACK still equals its own token. It performs no network operation and starts no Dolphin process. If another writer changes ACK or the game/mapping disappears, it refuses to overwrite that state and reports that rollback could not be confirmed. That process-exit/concurrent-writer case is an explicit limitation; run only with no other bridge writers.

### Pre-write rejection diagnosis

An initial Dolphin 2609b attempt failed with `M4 mailbox or ACK changed during final pre-write check`; no write occurred. The previous implementation collapsed three comparisons into one error: validated mailbox location, ACK word, and game echo word. It did not compare the frame counter, so the normal heartbeat advancing during the 100 ms handler revalidation could not itself cause this failure. The captured error did not identify which comparison differed; host ACK activity or mailbox relocation were the remaining possibilities. A `sync-client` cannot cause ACK mutations because its mapping is read-only.

The command now reports only those mailbox fields before and after final read-only validation: mailbox base, frame counter, ACK, echo, elapsed validation milliseconds, and the exact changed field names. The counter is diagnostic and allowed to advance; mailbox identity, ACK, and echo must remain unchanged. A failed mailbox/signature revalidation reports its error and does not inspect another address. It never retries the write, and every changed/unknown state still rejects before writing. Synthetic tests cover stable control words with an advancing counter, ACK changes, echo changes, and mailbox relocation.

For the next attempt, ensure no ACK-writing bridge process targets this Dolphin. A read-only preflight can be repeated without changing memory:

```powershell
cargo run --release -p multigalaxy -- validate-m4-read-only development.local.toml 26952
```

Only after that passes and all other writers are stopped, invoke the explicit ACK test once using the command above. Preserve its complete `before`, `after`, `changed_fields`, and `elapsed_ms` output. Do not loop/retry automatically. If it still rejects, the changed-field report will distinguish mailbox relocation from ACK/echo mutation without making another write.

### ACK and echo ownership; 2609b quiescence sample

The PAL Gecko payload's generated PPC instructions establish the word ownership:

- `counter` is incremented by the game-side OnFrame Gecko routine.
- `ack` is read by that routine and is the host-writable word.
- `echo` is written by the game-side routine as a copy of the ACK value it read on that OnFrame.
- Rust's ordinary M4 writer computes `ack = counter.rotate_left(7) ^ 0x4d473141` and writes it after observing the counter. Therefore ACK and echo can differ between the host store and the next game-side copy. An instantaneous equality check is not a protocol invariant during active ACK traffic; it is only a quiet-state gate for the destructive test.

The user supplied this read-only sample on Dolphin 2609b, PID 4712, mailbox `0x800025A0`:

```text
counter=0x00001044 ack=0x4d4f1341 echo=0x4d4f10c1
```

The ACK token decodes exactly to counter candidate `0x1044`; the echo token decodes to `0x1043`, exactly one frame behind. This is the protocol's expected direction and is consistent with the ordinary interval after a host writes the ACK for the current frame and before the next Gecko OnFrame copies it to echo. It is not evidence of stale/corrupt data by itself. The single sample cannot establish which host process wrote ACK or whether the game-side copy caught up on a subsequent frame. It does not prove a competing writer or a Dolphin 2609b memory fault.

The new diagnostic was then run read-only against PID 4712. Across six samples at 50 ms intervals, counter advanced from `0xA8FE` to `0xA90D`; ACK changed in every interval and encoded the current or adjacent frame; echo also advanced and usually encoded the preceding frame. Equality was observed in two samples. Summary: `counter_advanced=true ack_changed=true echo_changed=true ack_echo_equal_in_any_sample=true ack_echo_transition_observed=false writes=0`. This establishes active external ACK-writer activity while the game-side echo was tracking it. The values are consistent with the ordinary bridge writer and the observed one-frame lag; they do not show whether one or multiple host processes were writing. The one-shot test correctly did not write while this writer was active.

The ACK test remains fail-closed unless its quiet-state checks pass. A new `diagnose-m4-ack` command opens only a read-only MEM1 view, validates the same process/game/handler/mailbox prerequisites, and samples only counter/ACK/echo six times at 50 ms intervals, with a validated mailbox check before and after. It reports token-derived counter candidates, sample times, whether the counter/ACK/echo changed, and whether an ACK-to-echo transition was observed. Because this diagnostic itself performs no writes, ACK changes during its sample window prove that some external host-side writer is active; the tool cannot identify that process from the mailbox. Such activity is unsafe to overlap with the one-shot ACK test, even if the echo follows normally. A stable ACK followed by the game echo catching up is consistent with the ordinary one-frame protocol transition. Stable divergence while the counter advances means the echo did not catch up in the sample window; a stalled counter makes the sample inconclusive about game-side echo behavior. No ACK test write or network action occurs.

Run this read-only diagnostic against the already-running instance before any further ACK test:

```powershell
cargo run --release -p multigalaxy -- diagnose-m4-ack development.local.toml 4712
```

If ACK changes in the window, stop every ACK-writing bridge process and repeat the diagnostic. If the counter advances but echo remains divergent and stable, do not run the write test; retain the bounded output for protocol/runtime investigation. Only an observed stable ACK/echo state with no other ACK writer justifies a later, separately confirmed one-shot ACK test.

The interactive `dev-launch --gecko-bridge-probe` host process itself writes ACKs. In the PowerShell window running that probe, type `Q` to stop/unmap the host writer; the launcher drops its child handle without killing Dolphin. Then run `diagnose-m4-ack` again. Proceed to the explicitly confirmed ACK test only if the diagnostic shows no ACK changes and the echo has caught up, with the counter still advancing. If another ACK writer remains, do not run the ACK test.

## M4 two-client position relay passed

The user confirmed a live run on Dolphin 2609b, PAL RMGP01 Rev 0:

- Mario Dolphin PID `9312`; Luigi Dolphin PID `13420`.
- Each used a separate isolated Dolphin profile and its own Gecko ACK writer.
- Both read-only sync clients connected to `127.0.0.1:39845`.
- Mario→Luigi and Luigi→Mario position reception were observed.
- Luigi movement produced changing XYZ coordinates in Mario's client, generally about 58 Hz with reported receive ages of 0–16 ms.

This verifies the M4 position relay between two live Dolphin instances. It does not verify remote visual rendering, in-game character detection, physics/input/progression synchronization, pause or scene-transition recovery, disconnect cleanup, AllStars activation on 2609b, or broader gameplay performance. See the repeat procedure in [networking.md](../networking.md#two-instance-interactive-workflow).

## Next M5 step

M4 bidirectional position reception is now confirmed. M5 remains NO-GO: independent J3D mutable state, GX state restoration, matrix/packet lifetime, the prior failed hook dry-run, and scene teardown are unresolved. Follow the [M5 staged implementation plan](../../specs/M5-remote-rendering-implementation-plan.md). No remote actor/render hook is authorized by this bridge validation.
