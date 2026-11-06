# Plan/spec: Stage 1 trampoline via xdelta-patched image (pivot from Gecko)

Status: **plan only — no code, no patch generated or applied.** Stage 1 acceptance criteria are unchanged: installed state verified by read-only host checks (`verify-hook`), identical rendering hook on/off in Beach Bowl, stability across ≥1 scene transition, clean rollback. The Gecko thread landed and closed its bug (installer used `ori` instead of `oris`; see draw-only-hook-contract.md Addendum 4), so the second-writer question is settled: there is no second writer; the Gecko path remains the working carrier until the image route proves out.

## 1. Where the 8-word stub can live in PAL DOL text

Scanned both DOL text sections of the exact PAL DOL (`text0` `0x80004000`+0x24E0, `text1` `0x800070A0`+0x5261E0) for nop (`0x60000000`) / zero runs: **no padding run ≥ 8 words (32 bytes) exists anywhere**, and section tails are real code, not padding. So there is no free 32-byte cave in the DOL text.

Recommended Stage 1 placement: **inline at the callsite window**, not a cave. `0x802B62DC..0x802B62F0` is 5 words (`mr r3,r29; li r4,0; bl directDraw; li r0,0; stw r0,0x1B0(r29)`) — enough to hold the whole Stage 1 trampoline inline (original `bl 0x802A6B20` + flag clear + branch back), zero cave needed, zero displacement risk. Disjointness discipline unchanged: reject if any AllStars write (419 parsed), pinned observer signature, or mailbox range overlaps the window; the window is currently disjoint (verified: no AllStars write near `0x802B62E4`). Stage 2's larger duplicate-draw stub has no inline space — it would need an appended DOL text section (header/size/hash change, re-fingerprint) or a proven-dead function; both require separate evidence and are out of scope for Stage 1.

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
