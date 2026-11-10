# Partition-aware Wii ISO rebuild

Status: **verified offline for an identity DOL target, PAL RMGP01 Rev 0**. This establishes extraction/recomposition and integrity checks, not a changed-code deployment or successful Dolphin boot.

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

This is a successful partition-aware **identity rebuild**. It does not prove a changed DOL patch, hook correctness, AllStars hook compatibility, or Dolphin bootability. No Dolphin instance was launched. The changed-DOL path is implemented and has synthetic changed-byte, exact-target-hash, DOL-layout, non-text-write, AllStars-overlap, and byte-verification tests; it has not yet been exercised through WIT against a changed target. The output ISO and extraction artifacts are local ignored files under `target/` and are not committed.

## Current gates

- **Partition-aware ISO extraction/rebuild:** GO for the tested WIT/DolphinTool versions and PAL RMGP01 Rev 0 ISO path.
- **RVZ ingestion:** not built into `rebuild-iso`. A separate DolphinTool conversion was observed for the supplied RVZ; its warning means general RVZ conversion support remains unclaimed.
- **Changed DOL pipeline:** implementation and synthetic validation are present; no changed target has yet passed a real WIT rebuild. Not ready for controlled Dolphin boot.
- **Trampoline:** NO-GO. The original callsite ABI is understood, but a safe executable location for the out-of-line stub is not established.
- **`dev-launch --patched-image` for this WIT manifest:** not integrated. The legacy option still expects its prior plaintext-scan manifest and must not be used for this rebuilt ISO.
- **Dolphin boot / visible harmless trampoline:** unverified. No Dolphin was launched per instruction.
- **Remote Mario rendering:** NO-GO. The exact callsite `0x802B62E4` keeps its original instruction in this image.

The next deployment task is to define a versioned changed-DOL manifest and a PAL-verified patch target, then extend the partition-aware command and launcher validation to those manifest semantics. Only after static signatures, AllStars overlap checks, and output partition verification pass should the user run the harmless one-call trampoline test.
