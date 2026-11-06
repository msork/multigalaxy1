# Plan/spec: Stage 1 trampoline via xdelta-patched image (pivot from Gecko)

Status: **implemented (code + tests), live boot pending.** Stage 1 proves THE PIPELINE, not hook semantics: the 5-word window at `0x802B62D8..0x802B62EC` (`bl 0x802A6560; mr r3,r29; li r4,0; bl 0x802A6B20; li r0,0`) is replaced with byte-identical content (identity patch, pinned against the exact PAL DOL and verified against the real DOL). Acceptance means: patched image boots, hook window bytes verified read-only at runtime (`verify-hook` now reports `window=original` + words), rendering identical, rollback = launch the clean copy.

Explicit spec points (approved):

1. Pipeline proof only — identity window; Stage 2 hook semantics unchanged and parked.
2. Size-preserving in-place DOL patching at the located image offset; no FST/partition rebuild (that blocker stays documented for Stage 2+).
3. Wii disc integrity: Wii data clusters carry ECC/hash material, but Dolphin 2609a does not verify cluster hashes unless disc-integrity verification is explicitly enabled (off by default), so a byte-patched ISO boots. Recorded here as the source-level expectation; the live boot is the user's acceptance step and must be recorded when observed.
4. All identity gates fail closed: RMGP01 Rev 0 disc header, source image SHA-256, pinned DOL SHA-256 (`8200fb72…fbee`), Petari symbol hash, pinned window words, AllStars non-overlap (the window is disjoint from all 419 parsed AllStars writes). The original image is opened read-only and never modified; the patch applies only to a new user-owned output path (refuses to overwrite).
5. Pure-Rust VCDIFF (RFC 3284 restricted profile) in `multigalaxy-patch::vcdiff`; `patch-image <dev.toml> <source-image> <output-image>` CLI; `dev-launch --patched-image <path>`; `verify-hook` extended to check the window. Gecko stays for AllStars only.

The Gecko thread landed and closed its bug (installer used `ori` instead of `oris`; see draw-only-hook-contract.md Addendum 4), so the second-writer question is settled: there is no second writer; the Gecko path remains the working carrier until the image route proves out.

## 1. Where the stub lives in PAL DOL text

Scanned both DOL text sections of the exact PAL DOL (`text0` `0x80004000`+0x24E0, `text1` `0x800070A0`+0x5261E0) for nop (`0x60000000`) / zero runs: **no padding run ≥ 8 words (32 bytes) exists anywhere**, and section tails are real code, not padding. So there is no free 32-byte cave in the DOL text.

Implemented Stage 1 placement: **inline at the callsite window**, not a cave. `0x802B62D8..0x802B62EC` is the 5-word window (`bl 0x802A6560; mr r3,r29; li r4,0; bl 0x802A6B20; li r0,0`) — enough for the whole Stage 1 trampoline inline, zero cave, zero displacement risk. Disjointness: the window is verified disjoint from all 419 parsed AllStars writes and every pinned observer signature; `patch-image` additionally re-verifies the pinned window words against the exact PAL DOL before writing. Stage 2's larger duplicate-draw stub has no inline space — it would need an appended DOL text section (header/size/hash change, re-fingerprint) or a proven-dead function; both require separate evidence and are out of scope for Stage 1.

## 2. Patch mechanics

- Scope: extracted `main.dol` delta (preferred per `docs/references/xdelta-deployment-assessment.md`); never byte-patch the RVZ (compressed container). The image extract/rebuild (RVZ → decompressed → patch DOL → repack or overlay) is the documented implementation blocker: it must use a validated path (official DolphinTool or equivalent), write a **new user-owned temp image**, verify output hash, and never touch the original. Dolphin 2609a boots RVZ; the patched copy must be presented as a bootable image, not a bare DOL (SMG1 reads assets from disc).
- Identity: pre-patch, validate game ID `RMGP01` Rev 0, exact source-image SHA-256, source-DOL SHA-256 (`8200fb72…fbee`), Petari symbol hash, and every hook's expected original bytes; post-patch, verify target-DOL size/SHA-256 and all PAL signatures. Manifest per the assessment (schema, patch id/sha, xdelta version/options, source hashes, target hashes, AllStars/mailbox fingerprints, non-overlap). Fail closed everywhere; unknown build = no patch.
- AllStars + shoulder swaps stay Gecko (unchanged); one variable at a time.

## 3. Rust tooling

The workspace has **zero external dependencies** (dependency-free SHA-256 precedent). Plan: pure-Rust, xdelta3-compatible VCDIFF encode/decode in `multigalaxy-patch` (no external binary; manifest still records tool version + deterministic options). CLI surface in `multigalaxy-cli`: `patch-image <manifest> <source image> <output image>` (generate/apply with full identity gates) and a `dev-launch --patched-image <path>` flag that launches the patched temp copy in the isolated profile. Rollback = launch the original image (flag off); `verify-hook` unchanged for host-side read-only verification.

## 4. Sequencing

1. Pure-Rust VCDIFF codec + manifest validation + synthetic round-trip tests (multigalaxy-patch).
2. `patch-image` generating a DOL-scoped delta with the inline Stage 1 trampoline; AllStars disjointness asserted against the parsed 419 writes.
3. Image extract/rebuild pipeline feasibility proof (the blocker) — only then wire `dev-launch --patched-image`.
4. Live acceptance per the unchanged Stage 1 criteria.

## 5. Go/no-go

Image route is **NO-GO until** the extract/rebuild pipeline is proven bootable on stock Dolphin 2609a with AllStars enabled and identical rendering; Gecko route remains the fallback carrier and is currently bug-fixed (Addendum 4). Rendering remains **NO-GO**; Stage 2 parked.
