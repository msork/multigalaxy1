# Optional xdelta deployment assessment

Status: architecture decision only. No `.xdelta` has been generated or applied. PAL RMGP01 Rev 0 and the verified user-supplied image remain the only target. This option does not change stock Dolphin, the Rust launcher/network bridge, mandatory AllStars behavior, or M4.

## What xdelta would and would not solve

xdelta3 encodes file differences as VCDIFF and can verify source and target digests in its armored mode ([format and commands](https://github.com/jmacd/xdelta/blob/main/site/docs/commandline.md), [overview](https://github.com/jmacd/xdelta/blob/main/site/docs/index.md)). MultiGalaxy1 must still independently check a versioned manifest, expected source identity, and the reconstructed output hash. A delta is a transport for bytes; it does not establish that a PPC hook is correct or that a J3D draw is safe.

| Patch scope | Benefit | Main cost | Assessment |
|---|---|---|---|
| Full disc image | Can reconstruct a bootable target image in one step if the exact full source image matches | Tightly binds the patch to one container/image hash; localization variants become separate sources; output can be large; does not avoid image integrity/rebuild concerns | Not preferred |
| Extracted `main.dol` | Small, deterministic executable delta; leaves each client's language assets untouched; a static hook need not be reapplied each frame | The result is not itself a bootable game. A supported way to present the patched DOL to stock Dolphin or build a new user-owned image must be demonstrated. Every localized source DOL still needs its own exact fingerprint | Preferred *future* scope if static code patching is justified |
| Gecko/runtime code | Current launcher already has a reversible path and keeps the user's image unchanged | Runtime hook ABI, code/data placement, and rendering safety still require proof | Safest current experiment carrier |

Neither DOL nor full-image xdelta simplifies model loading, matrix allocation, packet ownership, cache maintenance, GX state, animation, or scene lifetime. Static PPC patching may simplify repeatable installation after a hook passes the rendering safety gates; it cannot clear those gates. Wario and Waluigi model/resource integration remains independent work.

## Identity and reproducibility contract

Any future patch tool must fail closed and produce a new output path. It must never modify the supplied source image or source DOL in place. Before decoding, validate:

- manifest schema version, patch identifier/version, patch SHA-256, and the exact xdelta implementation/version and deterministic generation settings;
- game ID `RMGP01`, disc revision 0, image/container kind, and exact source-image SHA-256 for an enumerated supported input;
- extracted source `main.dol` size and SHA-256, plus the PAL symbol-map/DOL fingerprint and every hook's expected original bytes;
- the expected AllStars compatibility fingerprint and explicit non-overlap with AllStars and mailbox/code ranges;
- locale/input profile, if accepted. A Russian/French/English label is not identity: each actual image and DOL hash must be enumerated. If a translation changes `main.dol`, it is a separate source variant and must be independently validated.

After decode, verify output DOL size/hash and all expected PAL instruction/context signatures. If producing an image, verify its exact target-image hash and bootable container/partition metadata. Write to a temporary file, flush/close, verify, then atomically publish to a distinct user-selected output. A failed check deletes only the temporary output and reports which identity/signature failed. Never weaken source checks or fall back to an unpatched launch.

The versioned manifest should include at minimum:

```text
schema_version
patch_id, patch_version, patch_sha256
patch_scope (main.dol or full image)
xdelta_version, deterministic_options
game_id, disc_revision, container_kind
supported_source_image_sha256[]
source_dol_size, source_dol_sha256
target_dol_size, target_dol_sha256
supported_locale_variant_ids[] (each bound to exact image and DOL hashes)
allstars_fingerprint, mailbox_fingerprint, hook_signature_set
expected_output_image_sha256 (when image output is produced)
```

SHA-256 values above are independent MultiGalaxy1 checks; xdelta3's VCDIFF checksums/optional armored digest are additional corruption/source checks, not a substitute for the project manifest. The patch must contain only original MultiGalaxy1 code/data, never Nintendo executable/assets or extracted game data. Third-party patches retain their own terms and attribution.

## PAL multilingual and AllStars constraints

A DOL-scoped patch is the only candidate that naturally preserves each player's client-local localization assets. It is not automatically multilingual-compatible: first establish that the Russian, French, and English PAL image variants all contain the exact accepted source DOL, or maintain separately reviewed source/target DOL variants. Then prove each resulting image boots and passes AllStars compatibility. AllStars remains mandatory; any overlap, unknown patch order, or mismatch is a hard failure. No language-specific full-image delta is accepted merely because the disc ID says RMGP01.

The current project has not demonstrated a stock-Dolphin image-overlay/rebuild flow that takes a patched DOL while preserving the user's original RVZ, save/profile isolation, AllStars, and localization. RVZ should not be byte-patched as though it were an uncompressed ISO. A future implementation must use a validated extraction/rebuild path or a supported overlay mechanism, produce a separate user-owned output, and verify the output hash before launch. This is an implementation blocker, not a reason to weaken the source-image identity check.

## Recommendation and go/no-go

**Current decision: permit the option in architecture; do not generate or apply a patch.** Continue the current Gecko proof path while callsite, model/matrix ownership, cache, GX restoration, and teardown remain NO-GO. If those rendering gates pass and a static executable patch is actually useful, prototype a `main.dol`-scoped delta first, paired with a separately validated stock-Dolphin image overlay/rebuild flow. Accept it only after exact PAL source/output hashes, AllStars composition, language-preserving output, reproducibility, failure cleanup, and real boot are demonstrated. Full-image deltas are a less portable fallback for one exact image hash.

Related rendering safety contract: [draw-only shared-model renderer](draw-only-render-contract.md). The xdelta format is not a rendering or gameplay compatibility claim.

## Stage 1 image-pipeline audit (2026-11-09)

The implementation is an **offline identity/no-op experiment**, not a Wii image patcher. It scans for plaintext PAL DOL bytes and does not parse/decrypt Wii partitions, process cluster hashes, update partition metadata, or support RVZ. The source-image SHA-256 is recorded, not checked against an allowlist; the output hash and exact DOL identity are checked. For Stage 1, source and output full-image hashes must agree because the VCDIFF target is identical to the source DOL. The VCDIFF decoder output is now used as the splice input, but the identity target means the output image remains byte-for-byte unchanged. Synthetic tests validate only the codec/no-op copy against a synthetic plaintext fixture.

`dev-launch --patched-image` still configures mandatory AllStars; the M4 mailbox payload is only included when `--gecko-bridge-probe` is separately enabled. No Dolphin boot or gameplay was tested. The image pipeline is **NO-GO for live testing** until a proper extraction/decryption/rebuild path maintains Wii partition integrity and DolphinTool validates the completed image. See [`specs/xdelta-stage1-trampoline.md`](../../specs/xdelta-stage1-trampoline.md) for the complete audit and criteria. Previous statements here and in the Stage 1 spec that a raw byte-patched ISO would boot because hash verification is disabled are withdrawn; they were not established by the implementation or a runtime test.

### Follow-up: WIT-based ISO identity rebuild (2026-11-09)

The prior paragraph records the state at the time of the audit and remains accurate for `patch-image`. It is superseded for the separate `rebuild-iso` command: a Rust pipeline now invokes WIT's partition-aware extraction and `COPY` composition and validates input/output using WIT plus DolphinTool. A real PAL RMGP01 Rev 0 ISO identity rebuild passed offline validation and deterministic repeat composition. No DOL bytes changed, no Dolphin boot was attempted, and the old `dev-launch --patched-image` manifest path is not compatible with the new WIT manifest. See [`wii-image-rebuild.md`](wii-image-rebuild.md) for exact tool and image evidence. Current disposition: offline identity ISO rebuild **GO**; changed-code image deployment and live use **NO-GO**.
