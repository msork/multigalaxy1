# Partition-aware Wii ISO rebuild

Status: **verified offline for identity and one changed-byte DOL replacement, PAL RMGP01 Rev 0**. This establishes partition-aware extraction/recomposition and DOL integrity, not a Dolphin boot or executable-hook correctness.

## Tool capability findings

The installed official DolphinTool 2609a help reports `convert`, `verify`, `header`, and `extract`. It has no file-replacement or image-composition command. Its partition-aware extractor is not the rewriter used here. A direct `DolphinTool extract --single=sys/main.dol` request returned “No file/folder was extracted”; the system DOL is not in the DATA FST listing exposed by that mode.

The locally available Wiimms ISO Tool reports `v3.02a r7679 cygwin` (binary SHA-256 is recorded in the generated manifest). Its documented commands include `EXTRACT` and `COPY`; the latter composes an extracted FST back into a disc image, encrypting the partition and updating signatures/hashes. The tool is GPL-2.0-or-later according to its upstream [repository](https://github.com/Wiimm/wiimms-iso-tools); MultiGalaxy1 invokes a user-provided executable and does not bundle it. The upstream [COPY documentation](https://wit.wiimm.de/wit/cmd-copy.html) and [composition guide](https://wit.wiimm.de/info/composing.html) describe this workflow.

WIT's Cygwin Windows build requires `/cygdrive/<drive>/...` paths. The Rust CLI converts canonical local drive paths accordingly and rejects UNC/relative WIT paths. Inputs, staging, and outputs are all separate paths; WIT is never invoked with `EDIT` or `CONVERT` against the source.

## Rust command

`rebuild-iso` accepts ISO input only and requires explicit paths to DolphinTool, WIT, the pinned PAL DOL, and its matching symbol map:

```powershell
cargo run --release -p multigalaxy -- rebuild-iso `
  '<official Dolphin install>\DolphinTool.exe' `
  '<Wiimms ISO Tools>\wit.exe' `
  '<user-owned PAL source.iso>' `
  '<new output path\MultiGalaxy1.iso>' `
  '<verified PAL main.dol>' `
  '<matching Petari PAL symbols.txt>'
```

The command fails closed unless the source and output use `.iso`, output and manifest do not exist, DolphinTool reports PAL `RMGP01` revision 0, WIT verifies the input partition, and WIT-extracted `sys/main.dol` matches the exact DOL/symbol fingerprint. It applies the existing five-word identity VCDIFF operation to the extracted DOL in a unique temporary FST, then uses `wit COPY` to create a new ISO. It verifies the rebuilt image with WIT and DolphinTool, extracts it again, checks the output DOL hash, compares every `files/**` path/size/SHA-256, and checks that the source-image SHA-256 stayed constant. Full-image hashes stream in bounded memory.

For an already-built candidate DOL, `rebuild-iso-dol` accepts a replacement path, its declared SHA-256, and the external AllStars patch folder:

```powershell
cargo run --release -p multigalaxy -- rebuild-iso-dol `
  '<official Dolphin install>\DolphinTool.exe' `
  '<Wiimms ISO Tools>\wit.exe' `
  '<user-owned PAL source.iso>' `
  '<new output path\MultiGalaxy1-candidate.iso>' `
  '<verified PAL source main.dol>' `
  '<matching Petari PAL symbols.txt>' `
  '<candidate replacement main.dol>' `
  '<candidate replacement SHA-256>' `
  '<AllStars patch folder>'
```

Before extraction, this mode requires the exact PAL source DOL/symbol identity, an exact target hash, unchanged DOL header and section layout, same DOL size, and changes confined to existing text bytes. It parses the required AllStars patch writes and rejects any changed guest byte overlapping an AllStars write. It encodes and decodes VCDIFF and checks byte equality; after WIT composition it re-extracts and compares the entire output DOL byte-for-byte against the candidate. This accepts a *candidate DOL*; it does not prove PPC behavior, an unused trampoline address, or runtime compatibility. The original source is still read-only.

The adjacent deterministic text manifest includes schema/patch ID, game/revision, source/output image hashes, source/target DOL hashes, VCDIFF hash, tool binary hashes, and WIT version. It excludes machine paths and timestamps. Intermediate files are held in a unique sibling staging directory and removed on normal completion/failure. The requested output path must be new; the source is never opened for writing.

The older `patch-image` path remains a legacy plaintext-fixture experiment. It scans the raw byte stream and is **not** the production/rebuild command. Do not use its output as a bootable image.

## Observed local run

The supplied PAL RVZ was converted to a separate ISO through official DolphinTool `convert` with an isolated temporary user directory. DolphinTool returned success but printed `Error creating key`; this warning is retained as a limitation of the RVZ-conversion observation. The resulting ISO passed DolphinTool header validation and WIT partition verification. The original RVZ was only an input.

For source ISO SHA-256 `ce0bf021b21767c19a5077506ec8bd7e7ef67c3b53e04726ce792e05b52689bb`, the Rust pipeline produced separate output SHA-256 `6c18a566cdb2c7f7467c4eb9371101474fb23e12fdf293eb5702b4e142256d3c`. The manifest recorded source and target DOL SHA-256 `8200fb72b3170069bb71c8b3dc21b49e429977ce8ae5159bb95ea6a19de0fbee`; these are equal because the current Stage 1 VCDIFF is an identity operation. WIT reported the rebuilt DATA partition as encrypted, fake-signed its ticket/TMD as needed, and verified the partition successfully. DolphinTool reported `RMGP01`, PAL, revision 0. Re-extracted game DATA files matched all source file paths, sizes, and SHA-256 values. An independent second WIT composition from the same extracted FST produced the same output-image SHA-256.

This is a successful partition-aware **identity rebuild**. A separate changed-byte reconstruction was subsequently completed; its evidence is recorded below. No Dolphin instance was launched.

## Changed-byte round trip (2026-11-09)

The disposable candidate was made from the hash-pinned PAL DOL in ignored `target/` output. It changes one existing text word only:

| Field | Value |
|---|---|
| Guest address / file offset | `0x80078A5C` / `0x73F9C` |
| Function context | `exeDemoWait__16SkeletalFishBossFv`, symbol `0x80078A2C`, size `0x74` |
| Original / candidate words | `0x60000000` (`ori r0,r0,0`) / `0x60E70000` (`ori r7,r7,0`) |
| Source DOL SHA-256 | `8200fb72b3170069bb71c8b3dc21b49e429977ce8ae5159bb95ea6a19de0fbee` |
| Candidate DOL SHA-256 | `f055603eaf19baf1a78029df3f0b81359aecee37fe02efe1616fe169ddf9026e` |
| Source ISO SHA-256 (before and after) | `ce0bf021b21767c19a5077506ec8bd7e7ef67c3b53e04726ce792e05b52689bb` |
| Rebuilt output SHA-256, run 1 and run 2 | `aa5630857139c63f48ad9fa4e1941188ffc3bf367d73cd68dfde9aaefc35f765` |
| VCDIFF SHA-256 | `03d994bcab7da5e4262b7c427d45f5abe3412f25af26a4c787c90c71820b7d66` |
| DolphinTool / WIT SHA-256 | `63dd9bad68996f4c75302fde1a2374ebecfa767df6780619d658f41684f97e8a` / `8538c86e743b4d35c37da64a4ca8ee79b13cab0ad2cb4b0ba16ab97b72ed7753` |

The original instruction is a canonical `ori` no-op. The candidate is also `ori` with identical source/destination register and zero immediate, so it leaves that register unchanged and does not update condition flags. At `0x80078A58`, a linked branch returns to `0x80078A5C`; thus the site is an instruction in the named routine, not presumed unused padding. This is a static semantic equivalence argument, not a runtime proof. The candidate is outside the protected draw hook/signature region. `rebuild-iso-dol` also parsed the required AllStars writes and passed its byte-level non-overlap check.

Both real WIT runs extracted the exact pinned source DOL, built a separate ISO using WIT `COPY`, verified the partition, passed DolphinTool PAL `RMGP01` revision 0 header validation, re-extracted the output, and compared all DOL bytes to the candidate. The required AllStars set parsed as 419 writes (76 DOL text writes / 71 distinct changed words); the candidate address did not overlap those writes. A second independent WIT extraction at `target/mg1-disc-pipeline/changed-neutral-nop-independent-extract` independently yielded the candidate DOL hash and changed word. The repeated complete rebuild produced the same output image and manifest hashes. Complete phase/tool logs are retained as `target/mg1-disc-pipeline/changed-neutral-nop-build-1.log`, `changed-neutral-nop-build-2.log`, and `changed-neutral-nop-independent-extract.log`; manifests, candidate DOL, images, and extraction are also retained under ignored `target/mg1-disc-pipeline/`. No generated artifacts or game data are committed. The source DOL and ISO were not modified.

The exact generated artifacts can be rolled back by deleting only `target/mg1-disc-pipeline/changed-neutral-nop*` and `test-neutral-nop-main.dol`; this does not touch the source ISO, source DOL, Dolphin profile, or any installed game. They are retained now as test evidence.

**Evidence level:** an actual changed-byte, partition-aware ISO reconstruction and byte-for-byte output-DOL verification passed twice offline. The user subsequently confirmed a live boot/gameplay regression of this exact disposable ISO on official Dolphin 2609a; the reported results are recorded in [Dolphin version migration](dolphin-version-migration.md). This does not validate the trampoline or general executable patching. The chosen edit is supported as behavior-neutral by PPC instruction semantics, but this experiment does not establish a generally safe executable code cave.

## Current gates

- **Partition-aware ISO extraction/rebuild:** GO for the tested WIT/DolphinTool versions and PAL RMGP01 Rev 0 ISO path.
- **RVZ ingestion:** not built into `rebuild-iso`. A separate DolphinTool conversion was observed for the supplied RVZ; its warning means general RVZ conversion support remains unclaimed.
- **Changed DOL pipeline:** GO for this exact PAL source/target and installed WIT/DolphinTool versions; two real rebuilds, independent extraction, deterministic hashes, source immutability, and AllStars non-overlap passed offline. This does not establish a generic executable patch or runtime compatibility.
- **Trampoline:** NO-GO. The original callsite ABI is understood, but a safe executable location for the out-of-line stub is not established.
- **`dev-launch --patched-image` for this WIT manifest:** implemented. It requires explicit `--source-iso` and `--wit`; the validation-only `validate-rebuilt-iso` command runs the same checks without launching Dolphin.
- **Dolphin boot:** user-confirmed on official 2609a for the exact neutral changed-byte PAL ISO: it booted to title, loaded a save, allowed Mario movement, retained AllStars Classic Controller operation, and showed normal animation, rendering, and camera behavior. This is a live changed-image boot regression only; it does not validate the Stage 1 trampoline. The user later confirmed the same ISO launches through `dev-launch` and Mario is controllable on Dolphin 2609b. AllStars, scene transitions, M4, and trampoline execution were not separately verified on 2609b.
- **Remote Mario rendering:** NO-GO. The exact callsite `0x802B62E4` keeps its original instruction in this image.

The release-build `validate-rebuilt-iso` command was run on the disposable output without launching Dolphin and reported `rebuilt_iso=valid`: source ISO `ce0bf021…89bb`, source DOL `8200fb72…fbee`, target DOL `f055603e…9026e`, output ISO `aa563085…f765`, WIT v3.02a r7679. Both DolphinTool PAL headers and WIT partition checks passed; source/output DATA files and source/target DOL constraints matched. The manual validation and interactive launch commands are in [building.md](../building.md). A WIT-rebuilt image can be launched only from an interactive PowerShell with a prepared isolated profile; validation is intentionally disk intensive because it re-hashes and re-extracts both ISOs. The user-confirmed 2609a boot baseline is recorded in Dolphin version migration. Do not infer trampoline correctness from the neutral candidate. The callsite trampoline remains NO-GO until a safe executable location and loader/heap collision analysis are proven.

## 2609b validation using historical tool provenance (2026-11-10)

The prior validator stopped in `hash-images-and-tools` because it treated `dolphintool_sha256` as a current-tool identity requirement. The unchanged schema-v1 manifest records build-time DolphinTool SHA-256 `63dd9bad68996f4c75302fde1a2374ebecfa767df6780619d658f41684f97e8a`; current Dolphin 2609b DolphinTool SHA-256 is `e8df1901e6202a724bdfe71d95c7eed18c9e5f5c7e9e7861a8e8998a93a2cecd`. The manifest was not rewritten.

After separating provenance from validation identity, the release validator completed with `rebuilt_iso=valid`. It verified source ISO `ce0bf021b21767c19a5077506ec8bd7e7ef67c3b53e04726ce792e05b52689bb`, output ISO `aa5630857139c63f48ad9fa4e1941188ffc3bf367d73cd68dfde9aaefc35f765`, source DOL `8200fb72b3170069bb71c8b3dc21b49e429977ce8ae5159bb95ea6a19de0fbee`, and target DOL `f055603eaf19baf1a78029df3f0b81359aecee37fe02efe1616fe169ddf9026e`. The current DolphinTool header operation and WIT partition verification/extraction completed; extracted DOL and unchanged DATA comparisons passed. Current tool file hashes were rechecked for replacement during the run, not compared with historical manifest tool hashes. The ISO, manifest, Dolphin installation, and original source image were not modified. This was offline validation only; Dolphin was not launched.

Synthetic regression coverage accepts the historical tool hashes alongside different current validation hashes/version metadata, rejects malformed manifest hash fields and tampered source/output ISO digests, and rejects incorrect source/target DOL or VCDIFF hashes. A valid-format change to a provenance-only hash is intentionally not authenticated because schema-v1 manifests are unsigned.
