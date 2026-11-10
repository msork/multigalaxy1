# Persistent rebuilt-ISO validation cache

Status: implemented; synthetic cache invalidation and local cold/warm no-launch measurements passed. Dolphin launch is not performed by cache validation.

## 1. Objective

Avoid repeating WIT partition verification and source/output DATA extraction on every `dev-launch --patched-image`, while preserving the existing full validation on first use, cache miss, cache invalidation, or explicit `--force-revalidate`.

## 2. Verified research

- The full validator checks source and output ISO SHA-256, current PAL RMGP01 Rev 0 DOL identity, manifest schema/identity, DolphinTool PAL headers, WIT partition integrity, extracted source/target DOL bytes, deterministic VCDIFF, AllStars overlap, unchanged DATA, and end-of-run image/tool stability.
- DolphinTool and WIT historical manifest hashes are provenance only. Dolphin release/tool updates alone must not invalidate the new cache.
- Whole-image hashes are expensive but required for strong detection of replaced/modified ISO contents. File size/timestamp alone cannot establish unchanged content.
- The manifest and local cache are unsigned. A local user able to rewrite both can fabricate cache state; this design is a trusted same-user performance cache, not a security boundary against a hostile local account.

## 3. Functional requirements

- Cache miss/corruption/incompatibility: run the complete current validator; only write a cache after it succeeds.
- Cache hit: hash full source/output ISOs, source DOL, current manifest, and the canonical prepared AllStars settings; validate PAL/manifest fields; perform the current DolphinTool header operation; confirm unchanged identities after checks. Skip WIT verify/extract/DATA comparison only when every cached identity and policy field matches.
- `--force-revalidate` skips any cache hit and performs the complete validator. It is valid only with `--patched-image`.
- Changes to source/output ISO bytes, source/candidate DOL identity, VCDIFF, manifest bytes/schema, AllStars settings fingerprint, cache schema, or validation-policy identifier force a full run.
- Dolphin executable, DolphinTool, and WIT version/hash changes alone do not invalidate the cache. Current DolphinTool still must pass its PAL header operation on a cache hit.
- A cache entry has a strict, bounded, duplicate-free schema, a canonical record digest for accidental corruption, exact hashes/identity/policy fields, and atomic best-effort replacement. Missing/malformed/inconsistent entries are never trusted; they cause full validation.
- Reuse relies on full content hashing each launch. It does not claim protection from a malicious same-user actor who can alter both inputs and cache coherently.
- Clean-image launch and standalone `validate-rebuilt-iso` remain unchanged; standalone validation always executes the full path.

## 4. Technical design

Use an adjacent `<rebuilt-image>.mg1validation-cache` record. Cache key inputs are full SHA-256 values for source ISO, output ISO, pinned source DOL, adjacent rebuild manifest, and canonical prepared AllStars settings, plus manifest target-DOL/VCDIFF hashes, PAL identity, cache schema, and a manually maintained validation-policy ID. Do not include emulator, DolphinTool, or WIT binary identity/version.

The cache is checked only after hashing current contents. A cache hit also asks current DolphinTool to identify the output image as PAL RMGP01 Rev 0 and rechecks image/manifest hashes. The unchanged full WIT check remains the source of the cache's trusted validation result. Force mode bypasses cache lookup and refreshes it only after complete success.

Cache writes use a same-directory temporary record and replacement; failure to persist a cache never downgrades or bypasses full validation. Corrupt cache records are treated as untrusted misses and replaced after a successful full validation.

## 5. Dependencies

- Existing rebuilt-ISO validator and exact PAL DOL.
- Prepared mandatory AllStars patch settings.
- Current compatible DolphinTool and WIT for full validation; current DolphinTool header operation for cached launch.
- Writable cache directory beside the user-supplied rebuilt ISO.

## 6. Risks

- Full ISO hashing still incurs sequential I/O on each launch; expected savings come from skipping WIT partition verification and extraction.
- Local cache authentication is not a defense against an attacker with write access to the same files. The integrity record detects accidental/truncated/tampered fields unless an attacker recomputes it.
- Validation-policy changes must bump the explicit policy identifier. Tests must make this invalidation visible.
- File replacement races are mitigated by rehashing relevant files around validation, but the cache does not provide an OS-level immutable snapshot of files.

## 7. Acceptance criteria

- Cold cache executes the complete validator and persists a cache entry only after success.
- Warm cache hashes current content, checks current DolphinTool PAL identity, skips WIT extraction/partition verification, and produces the same authorization result.
- Changed source/output ISO, DOL, manifest, AllStars fingerprint, cache schema, validation policy, malformed/corrupt cache, and `--force-revalidate` all prevent cache reuse.
- Dolphin/DolphinTool/WIT updates alone do not invalidate cache; DolphinTool must still pass the actual header operation.
- Clean launch behavior and mandatory AllStars remain unchanged.
- Measure and record cold/warm wall-clock validation for the local changed-neutral-nop ISO; distinguish actual measurements from test fixtures.

## 8. Testing strategy

Pure Rust tests cover strict parsing, record digest, source/output hash changes, stale manifest, DOL/VCDIFF/AllStars changes, policy/schema updates, tool-update independence, force mode, and malformed/corrupt entries. The local no-launch run measured 192,110 ms cold (complete WIT path) and 52,437 ms warm (hashes plus PAL headers, WIT extraction skipped). This gives a local 72.7% wall-time reduction; it is specific to this machine and image pair. No Dolphin process was spawned for timing.
