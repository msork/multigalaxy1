# WIT-rebuilt ISO launch validation

Status: implemented; synthetic tests and actual local validation-only run pass. No Dolphin launch is performed by the validator.

## Objective

Allow `dev-launch` to launch a separate ISO produced by `rebuild-iso-dol` only after validating its adjacent WIT manifest and repeating the disc-aware identity checks. Preserve the normal configured-image launch path.

## Verified research

- WIT manifest schema v1 records source/output image SHA-256, source/target DOL SHA-256, deterministic VCDIFF SHA-256, DolphinTool/WIT binary SHA-256, WIT version, game ID, revision, and input format.
- Before this work, `--patched-image` used the legacy plaintext `mg1patch.txt` identity validator and could not accept a WIT manifest. It now accepts only the strict WIT replacement manifest with explicit source ISO and WIT inputs.
- Launcher already validates the configured PAL image with DolphinTool, prepares mandatory AllStars and the configured controller profile in an isolated Dolphin user directory, and enables the mailbox only when `--gecko-bridge-probe` is explicit.
- WIT can verify and extract the ISO DATA partition; DolphinTool 2609a can report the disc header. The rebuilt DOL must be verified by WIT re-extraction, not raw ISO scanning.

## Functional requirements

- Clean launches remain unchanged.
- Rebuilt-image launch requires an explicit output ISO, its original source ISO, and WIT executable. Reject RVZ, missing/duplicate/stale/unknown manifest fields, wrong game/revision, wrong tool hashes/version, mismatched source/output hashes, and any extracted DOL mismatch.
- Source DOL must match the pinned PAL RMGP01 Rev 0 executable. Candidate DOL identity is authorized by the manifest's exact target hash; it must not be forced to equal the source DOL hash.
- Independently re-extract both source and output with WIT. Confirm source DOL, candidate DOL, deterministic VCDIFF digest, and unchanged game DATA files. Recheck source/output hashes and DolphinTool identity before launch.
- Revalidate candidate changes are only in existing text and do not overlap mandatory AllStars writes.
- Preserve mandatory AllStars configuration and controller profile. Do not add/remove M4 bridge codes unless the corresponding explicit option is present.
- Perform all validation before writing the isolated GameSettings/profile or starting Dolphin.

## Technical design

Strict manifest parser with exact known fields and no duplicate/unknown keys. Hash full source/output images; verify tool identities and WIT version; run WIT partition verification and extraction to a unique temporary directory; run DolphinTool header validation; compare extracted DOL hashes and bytes; recompute VCDIFF from source/candidate; compare all extracted `files/**` hashes. Use RAII cleanup for temporary extraction. The explicit launch options are `--patched-image <output.iso> --source-iso <source.iso> --wit <wit.exe>`.

## Dependencies

Official DolphinTool beside the configured Dolphin executable, user-supplied WIT, pinned PAL source DOL and Petari map, external AllStars patch files, and sufficient temporary disk space for source/output extraction.

## Risks

Launch-time validation is I/O intensive. The manifest is an integrity record, not a cryptographic signature; trust remains anchored to the user's explicit source ISO, pinned DOL, verified tool binaries, and exact target hash. WIT output can be structurally valid without proving Dolphin bootability or PPC behavior.

## Acceptance criteria

- Synthetic tests cover valid changed target, malformed/duplicate/missing fields, wrong image/source/DOL/tool hashes, unsupported format/revision/patch ID, and unexpected target bytes.
- Launcher wiring proves clean launch does not require WIT options, patched launch requires all three paths, and bridge-probe default remains false.
- No Dolphin process is started by automated tests.
- Manual command for the disposable neutral candidate is documented. Runtime boot remains unverified until performed interactively.

## Testing strategy

Rust unit tests with synthetic DOLs/manifests and launch argument-set validation; `cargo fmt`, workspace tests/build, PAL map validation, and the `validate-rebuilt-iso` no-launch command. Any Dolphin boot is a separate manual test and must use the prepared isolated profile.
