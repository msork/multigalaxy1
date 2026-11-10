# Stage 1 image pipeline audit: PAL RMGP01 Rev 0

Status: **NO-GO for live testing.** The current Stage 1 result is an identity/no-op copy, and the image code is not a Wii partition-aware patcher. No source image was modified and Dolphin was not launched during this audit.

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

Before any future live image test, implement or adopt a verified, reversible path that accepts the user's actual PAL source (including RVZ if supported), validates the source image via DolphinTool, extracts/decrypts the data partition, locates DOL using partition metadata, applies a non-identity synthetic/test delta to extracted DOL bytes, rebuilds/re-encrypts the partition and all integrity hashes correctly, emits a distinct image, then validates its full hash and DolphinTool ID/revision. Only after offline rebuild tests pass should an interactive boot be attempted. Preserve locale assets by changing only the intended DOL; do not require one fixed whole-image hash for all translated images, but always record and verify the actual source hash when applying a real delta.

## Validation evidence

Static review and synthetic tests only. No real RVZ/ISO was read or written by this audit, no Dolphin process was launched, and no live hook/window was tested. `--patched-image` must remain **NO-GO for live testing** until the image format/rebuild blocker is resolved.
