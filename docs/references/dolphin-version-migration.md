# Dolphin capability compatibility and migration record

## Policy

MultiGalaxy1 has no Dolphin release/build allowlist and does not pin Dolphin.exe SHA-256. `discover-dolphin` checks for one non-empty platform Dolphin executable and reports whether sibling DolphinTool and `Sys/codehandler.bin` files are present. Those are static prerequisites, not proof that the emulator can run a game.

Launch validation checks PAL `RMGP01` revision 0 by invoking the configured installation's DolphinTool `header` operation. The launch path then uses the configured executable with an isolated user directory and mandatory AllStars setup. Failure to spawn or complete the requested GUI/debug handshake is reported for that launch attempt; it does not block non-debug launches because GDB is optional.

AllStars is mandatory. Before launcher setup, the external PAL patch inputs are checked against the pinned PAL DOL and the installation must contain a non-empty Gecko codehandler file. This establishes static prerequisites, not that Gecko activation has been visually observed on every Dolphin build. Runtime AllStars remains a per-build gameplay check.

## Optional capability gates

- **GDB:** no release lock. When requested, the launched process must accept the GDB RSP connection and pass the requested live identity/read checks. Unsupported GDB only prevents that debug request.
- **Read-only MEM1 inspection:** no release lock or handler fingerprint. It verifies the target PID's executable path/session, opens only the named Windows mapping, validates the MEM1 span, reads live `RMGP01` and PAL runtime signatures, and uses bounded, explicit read ranges. The mapping is an undocumented implementation detail, not a supported Dolphin API.
- **M4 Gecko mailbox:** separate fail-closed capability. It requires the known 2880-byte Gecko codehandler fingerprint plus the runtime-validated MEM1 mapping, expected PAL identity/signatures, a unique in-bounds mailbox, stable sequence snapshots, and bounded ACK writes. No Dolphin version is accepted merely because its name or version string matches. The live bridge result is user-observed on 2609a; other builds remain unverified and can use M4 only if every capability check succeeds. A changed/unknown handler or mapping blocks M4 without blocking ordinary gameplay.
- **Wii image validation/rebuild:** current DolphinTool must successfully report PAL RMGP01 revision 0 and pass the actual header operations. Current WIT must successfully pass its version command, partition verification, extraction, and comparison operations. No historical tool version or binary hash is required.

## Historical DolphinTool/WIT manifest hashes

Existing schema-v1 ISO manifests may retain `dolphintool_sha256`, `wit_sha256`, and `wit_version` as reconstruction provenance; these fields are optional. If hashes are present, they must be well-formed, but they are not compared with current binaries or the reported WIT version. Current validator tools are hashed at the start and end to detect replacement during one validation run. Source/output ISO hashes, source/target DOL hashes, recomputed VCDIFF, PAL identity, WIT partition integrity, AllStars non-overlap, and unchanged DATA files remain enforced.

A schema-v1 manifest is not signed. A valid-format edit to a provenance-only tool hash cannot be authenticated and does not affect image authorization. Tampering with an image/DOL/delta identity or changing extracted DATA is detected by the integrity checks. Historical manifests are not rewritten.

## Live boot evidence

The user confirmed that the disposable changed-byte PAL RMGP01 Rev 0 ISO (`aa5630857139c63f48ad9fa4e1941188ffc3bf367d73cd68dfde9aaefc35f765`) booted normally in Dolphin 2609a: title screen, save load, Mario movement, AllStars Classic Controller operation, animation, rendering, and camera all worked. This is a live changed-image regression report, not trampoline or multiplayer validation.

The local development config currently points to Dolphin 2609b and its isolated profile. The 2609b DolphinTool successfully identified the disposable output as PAL RMGP01 Rev 0. A 2609b game boot has not been performed. The separate 2609a local config/profile remain available for rollback.

## Commands

From an interactive PowerShell at the repository root, these commands validate the existing rebuilt ISO without launching Dolphin, then (only when run) launch it for the controlled gameplay check. Set `$DolphinTool` and `$Wit` to the local tool paths; no original ISO is modified.

```powershell
$DolphinTool = 'C:\path\to\Dolphin-2609b-x64\DolphinTool.exe'
$Wit = 'C:\path\to\wit.exe'
$AllStars = '<external AllStars PAL patch folder>'

cargo run --release -p multigalaxy -- validate-rebuilt-iso `
  $DolphinTool $Wit `
  'target\mg1-disc-pipeline\source.iso' `
  'target\mg1-disc-pipeline\changed-neutral-nop.iso' `
  'reference\original\main.dol' $AllStars

cargo run --release -p multigalaxy -- dev-launch development.local.toml `
  'target\mg1-dev-profile-2609b' 0 `
  --patched-image 'target\mg1-disc-pipeline\changed-neutral-nop.iso' `
  --source-iso 'target\mg1-disc-pipeline\source.iso' `
  --wit $Wit
```

`validate-rebuilt-iso` is disk intensive because it re-hashes and re-extracts both images. `dev-launch` visibly starts Dolphin and is intentionally left as a user-run command.
