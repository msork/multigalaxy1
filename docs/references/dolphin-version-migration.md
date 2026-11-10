# Dolphin 2609a → 2609b development migration

## Supported binaries and validation

The development launcher accepts official stock Dolphin 2609a and 2609b for ordinary launches. `discover-dolphin <install directory>` searches a bounded depth for exactly one `Dolphin.exe` and validates it without starting the emulator. Validation reads the executable and sibling `DolphinTool` build markers, requires the two to report the same supported release, and checks the known 2880-byte `Sys/codehandler.bin` hash. A mixed, incomplete, ambiguous, or unknown install fails closed. Configure the exact executable path reported by discovery.

Observed installation check on 2026-11-09:

| Installation | Result | Dolphin.exe SHA-256 | DolphinTool SHA-256 |
|---|---|---|---|
| Official stable 2609b | Valid; both binaries report 2609b, matching codehandler present | `7d686bbe88539a0373ec2f0c29d6a810263ecd23a06ace89df9ed02101f3a2a2` | `e8df1901e6202a724bdfe71d95c7eed18c9e5f5c7e9e7861a8e8998a93a2cecd` |
| Official 2609a rollback | Valid; both binaries report 2609a, matching codehandler present | `17b01a8629d865378a45e58d5fcebf9dcc3f7919c719130a4ffdd1ca2b27b635` | `63dd9bad68996f4c75302fde1a2374ebecfa767df6780619d658f41684f97e8a` |

Version identity came from embedded binary build markers and matched sibling tool markers; no emulator process was needed for validation. The 2609b installation was not launched as part of this migration. The official release channel lists 2609b as the current stable release: [Dolphin downloads](https://dolphin-emu.org/download/).

## 2609a live baseline

The user confirmed that the disposable changed-byte PAL RMGP01 Rev 0 ISO (`aa5630857139c63f48ad9fa4e1941188ffc3bf367d73cd68dfde9aaefc35f765`) booted and behaved normally in official Dolphin 2609a: title screen, save load, Mario movement, AllStars Classic Controller, animations, rendering, and camera were all observed working. This is a user-reported live regression result, not a controlled benchmark and not Stage 1 trampoline validation. The changed word is a behavior-neutral `ori` identity variant; no remote-player hook was installed.

## Feature gates and rollback

- Ordinary GUI launch, PAL image validation, mandatory external AllStars setup, and `--patched-image` accept validated 2609a or 2609b.
- Dolphin MEM1 inspection, heap/actor/model/scheduler observers, Gecko mailbox and trampoline diagnostics, and the current M4 mailbox `sync-client` remain restricted to 2609a. Their Dolphin mapping and handler behavior have not been revalidated on 2609b.
- AllStars files, game image, Rust protocol, and M4 implementation were not changed for this migration.
- The prior local 2609a config/profile are retained separately from the new 2609b local config/profile. Local configs and profiles are ignored by Git; no personal paths or save/controller data are committed.
- A 2609b launch is not yet live-verified. The user should run the controlled command below manually and report title/save/gameplay/controller results. Do not interpret a successful boot as trampoline or multiplayer validation.

Read-only `validate-pal-game` using the 2609b DolphinTool accepted the disposable changed-byte ISO as PAL `RMGP01` revision 0. This validates DolphinTool/image identity handling only; it is not an emulator boot.

## Manual 2609b changed-byte ISO regression

From an interactive PowerShell at the repository root, after the launcher config and isolated 2609b profile are prepared, set `$Wit` to the locally installed Wiimms ISO Tool executable and run:

```powershell
cargo run --release -p multigalaxy -- dev-launch development.local.toml `
  'target\mg1-dev-profile-2609b' 0 `
  --patched-image 'target\mg1-disc-pipeline\changed-neutral-nop.iso' `
  --source-iso 'target\mg1-disc-pipeline\source.iso' `
  --wit $Wit
```

This is an interactive launch command and must be run by the user. It uses the isolated profile, leaves M4 mailbox probing off, and retains required AllStars setup. The launcher rechecks the rebuild manifest and hashes before launch.
