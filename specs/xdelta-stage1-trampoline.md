# Stage 1 image pipeline audit: PAL RMGP01 Rev 0

Status (2026-11-09): partition-aware PAL ISO identity and changed-byte DOL replacement both passed offline WIT/DolphinTool verification. This proves the reconstruction pipeline for the tested inputs, not trampoline correctness or Dolphin boot. See [partition-aware rebuild evidence](../docs/references/wii-image-rebuild.md).

## Audit findings

### Hook window

The five 32-bit words begin at guest address `0x802B62D8` and occupy `[0x802B62D8, 0x802B62EC)`. Their word addresses are `0x802B62D8`, `0x802B62DC`, `0x802B62E0`, `0x802B62E4`, and `0x802B62E8`; the last word is inclusive at `0x802B62E8`, and the end address `0x802B62EC` is exclusive. Length is exactly 20 bytes. The expected PAL Rev 0 words remain checked by `image_patch::stage1_patch` and the DOL map/signature path.

### Input image and Wii disc layout

`image_patch::locate_dol_in_image` is a plaintext byte scan starting at file offset `0x400`, stepping by four, and comparing a 64-byte prefix followed by the full DOL SHA-256. It does not parse the Wii partition table, read the partition DOL offset, decrypt AES-encrypted partition clusters, validate H0/H1/H2/H3 cluster hashes, update partition hashes, update FST/partition metadata, or handle RVZ. Dolphin's [WIA/RVZ format documentation](https://github.com/dolphin-emu/dolphin/blob/master/docs/WiaAndRvz.md) describes Wii partition data as encrypted and hashed, and explains that re-encryption/recalculated hashes are required when producing a disc image. Dolphin's [DiscExtractor](https://github.com/dolphin-emu/dolphin/blob/master/Source/Core/DiscIO/DiscExtractor.cpp) gets the boot DOL through partition-aware volume reads rather than searching raw file bytes. On a normal raw Wii ISO the executable resides in the encrypted data partition, so this scan cannot serve as DOL extraction. The old “uncompressed ISO” error wording and prior claim that byte-patched ISO boots were unsupported conclusions and are withdrawn. An extracted/decrypted plaintext byte stream is not by itself a bootable or integrity-correct rebuilt Wii image.

The patch-image CLI validates the configured development game through DolphinTool, but its separate `source-image` argument is not independently sent through DolphinTool. It receives only the six-byte disc ID/revision check and plaintext DOL scan in `image_patch`. `dev-launch --patched-image` checks its output manifest and plaintext DOL but does not ask DolphinTool to validate that alternate image. This is insufficient evidence that either argument is a valid bootable PAL Wii image.

### Hash gates

- The `patch-image` CLI validates the supplied original `main.dol` against the exact PAL RMGP01 Rev 0 DOL/symbol map using `player_probe::verify_pal_inputs`; `locate_dol_in_image` also requires the plaintext DOL candidate bytes to hash exactly to that supplied DOL. `patch-image` does not currently parse AllStars writes for overlap, but its target DOL is unchanged, so this identity patch has no changed write sites. Any future non-identity patch must add and test an explicit AllStars overlap check.
- The source image SHA-256 is calculated and recorded. It is **not compared to a preapproved full-image digest**; this is intentional for client-local language variants, but means it is not a compatibility gate. The source-image path also is not checked by DolphinTool.
- The output image SHA-256 is verified against the manifest at `dev-launch --patched-image` time. The Stage 1 DOL target hash is now required to equal the pinned source DOL hash. Manifest metadata is checked for the exact schema, patch ID, PAL identity, and window.
- Because this identity patch must change no bytes, validation also requires manifest source/output image hashes to match. This ties its no-op result together but does not authenticate an unknown source image or replace an image-format/rebuild validator. The VCDIFF bytes are not persisted as a sidecar, so their manifest digest is format-checked but cannot be recomputed by `dev-launch`.

### What the identity patch proves

Stage 1 verifies the pinned window, emits a restricted-profile VCDIFF stream, decodes that stream, and uses the decoded result as the DOL splice input. Synthetic tests exercise this codec path and image-offset splice against an unencrypted fixture. For this identity target, the complete output image is byte-for-byte equal to the input; no DOL bytes change. It therefore tests DOL location, codec round-trip, and a no-op copy/manifest path. It does **not** prove a changed-byte VCDIFF application, Wii partition rebuilding, hash-cluster repair, Dolphin boot, rendering behavior, or a valid runtime hook.

The five-word hook window remains an identity replacement. It is not a trampoline installation.

### `dev-launch --patched-image`, AllStars, and M4

`dev-launch` prepares the isolated RMGP01 GameSettings before selecting the image path. Mandatory AllStars and both verified shoulder-swap Gecko patches are always configured. Selecting `--patched-image` only changes the disc path; the current identity output cannot modify AllStars hook sites or M4 code.

The M4 mailbox Gecko payload is opt-in through `--gecko-bridge-probe`; `--patched-image` alone does not enable it. The two options can be combined, and the profile builder prepends the mailbox payload while retaining the mandatory AllStars settings. Thus AllStars preservation is built into this launch path; M4 remains available only when explicitly enabled and still requires its separate runtime verification. No image boot or M4 test was performed here.

## Acceptance and disposition

Do not run live testing with the current patch-image output as evidence of a patched Wii image. Mark the image pipeline **NO-GO** for live testing. Keep the original RVZ untouched and keep stock Dolphin, AllStars, and M4 paths unchanged.

At the time of the original audit, a partition-aware rebuild was required before any live image test. That requirement is now met for ISO identity extraction/recomposition only; see the follow-up section. A non-identity synthetic/test delta and changed DOL target remain prerequisites before a live patched-image test. Preserve locale assets by changing only the intended DOL; do not require one fixed whole-image hash for all translated images, but always record and verify the actual source hash when applying a real delta.

## Validation evidence

At the original audit: static review and synthetic tests only; no real RVZ/ISO was read or written, no Dolphin process was launched, and no live hook/window was tested. `--patched-image` was **NO-GO** until the partition rebuild blocker was resolved. That historical evidence is supplemented by the offline follow-up below; `--patched-image` remains unintegrated with the new manifest.

## Follow-up: partition-aware ISO rebuild (2026-11-09)

The historical audit above correctly describes the legacy `patch-image` implementation, which remains a plaintext byte scanner and is not suitable for encrypted Wii images. A separate Rust `rebuild-iso` command now uses externally supplied Wiimms ISO Tool (WIT) to extract and recompose a fresh ISO. DolphinTool 2609a validates PAL `RMGP01`, revision 0 on both input and output; WIT verifies partitions before and after; the extracted `sys/main.dol` is pinned to the verified PAL DOL and symbol map. Output is re-extracted and checked, source file hashes are compared, and the original source image hash is rechecked before publishing.

This path has been run against the local PAL ISO and independently recomposed to the same deterministic output hash. The first run was identity-only; the changed-byte follow-up separately proves changed DOL delivery. The local RVZ-to-ISO conversion emitted a key warning and RVZ is not accepted by `rebuild-iso`. `dev-launch --patched-image` now consumes the WIT replacement manifest through `--source-iso`/`--wit`; the exact disposable image passed the no-launch `validate-rebuilt-iso` command. No Dolphin was launched. Detailed tool versions, hashes, evidence, and commands are in [`docs/references/wii-image-rebuild.md`](../docs/references/wii-image-rebuild.md).

Disposition at this phase: **GO** for offline PAL ISO identity rebuild with the tested tool versions; subsequent evidence updates changed DOL deployment status below. **NO-GO** for launcher consumption, RVZ input, and trampoline/live hook.

## Follow-up: hash-pinned DOL replacement and trampoline ABI (2026-11-09)

`rebuild-iso-dol` now accepts a caller-built replacement DOL and a declared target SHA-256, alongside the exact PAL source DOL/symbol map and required AllStars patch directory. It refuses changed size/header/section layout/BSS/entry point, requires every changed byte to map to existing DOL text, rejects overlap with any parsed AllStars word write, round-trips the generated VCDIFF, and verifies the output ISO's extracted DOL byte-for-byte. Synthetic changed-text, expected-byte, wrong-hash, data/header mutation, layout, and AllStars-overlap tests cover the validator. The identity command remains available separately.

The exact source instruction window is five words / 20 bytes: `0x802B62D8: 4BFF0289` (`bl 0x802A6560`), `0x802B62DC: 7FA3EB78` (`mr r3,r29`), `0x802B62E0: 38800000` (`li r4,0`), `0x802B62E4: 4BFF083D` (`bl 0x802A6B20`), `0x802B62E8: 38000000` (`li r0,0`); `0x802B62EC` is exclusive. The continuation at `0x802B62EC` stores the zero to `0x1B0(r29)`. Only the single `bl` at `0x802B62E4` needs interposition; the other four words are context and must remain unchanged. The original branch displacement sign-extends to `-0xF7C4`, returning to the verified `directDraw` entry at `0x802A6B20` with LK=1. At entry, `r3=r29` (J3DModelX), `r4=0`; the enclosing frame and nonvolatile `r29/r30/r31` remain owned by `drawMarioModel`.

Candidate behavior-preserving tail trampoline, expressed as instructions rather than an installed patch:

```text
at 0x802B62E4: b <verified_stub>     ; LK=0; no register or stack change
stub:          lis r12,0x802B
               ori r12,r12,0x62E8
               mtlr r12               ; restore the original call's LR value
               lis r12,0x802A
               ori r12,r12,0x6B20
               mtctr r12
               bctr                   ; tail-enter directDraw with r3/r4 intact
```

This preserves the original `directDraw` entry, argument registers, and return continuation/LR while leaving `r1`, `r0`, CR, and nonvolatile registers unchanged in the stub. It uses ABI-volatile `r12` and CTR. The callee returns through LR to the exact original continuation at `0x802B62E8`, where the original cleanup executes. It does not replace or omit either draw call. The five-word caller window must hash exactly apart from the one hook word. This is a static ABI design, not an installed or runtime-verified trampoline.

**Trampoline GO/NO-GO:** NO-GO. No verified unused executable address exists for the seven-word stub. Parsing the pinned PAL DOL header shows 2/7 text descriptors and 8/11 data descriptors occupied. Loaded text ranges are `0x80004000..0x800064E0` and `0x800070A0..0x8052D280`; data ranges include `0x8052D280..0x805F5A20` and `0x8069CCA0..0x806ADF60`. DOL BSS is `0x805F5A40..0x806ADF90`, overlapping that latter data range; this rules out treating the BSS bounding range or its apparent tail as free code space. The remaining descriptor slots are file-format capacity only. They do not prove that a proposed guest address avoids game heap, loader/runtime use, or AllStars' dynamically used Gecko cave, nor do they establish a valid RAM placement.

An appended text section is therefore only a candidate mechanism, not an established placement. A correct implementation would need an unused descriptor, aligned non-overflowing file/guest ranges, an entrypoint-independent branch within PPC relative `b` reach (signed 26-bit displacement, 4-byte alignment), plus proof the selected MEM1 region is reserved from BSS, all DOL sections, runtime heap(s), codehandler/AllStars, and other runtime allocations. The current DOL map and observed runtime heap identities do not prove such a region. Dolphin's DOL loader accepting a descriptor would prove loading, not that SMG1 will never allocate over it. No safe appended-section address can be named from current evidence.

The previous Gecko attempts ended with an invalid instruction caused by an `ori`/`oris` installer bug (fixed in source) and did not prove safe stub placement. No changed trampoline DOL was generated. The replacement command remains a transport primitive, not authorization to patch a guessed cave.

**Changed-image gate:** the changed-byte pipeline is now **GO offline** for the exact behavior-neutral candidate at `0x80078A5C` (see evidence doc), including two deterministic WIT rebuilds, independent re-extraction, AllStars non-overlap, and unchanged source. It is **not** an acceptance boot. The neutral image is a possible controlled stock-Dolphin boot fixture, but `dev-launch --patched-image` does not consume the WIT manifest, so the project’s isolated launcher acceptance is not ready. No Dolphin was launched. The trampoline remains NO-GO independently of this pipeline success.
