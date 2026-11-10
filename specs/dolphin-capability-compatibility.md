# Dolphin capability-based compatibility

## Objective

Allow players to use older, current, and future official Dolphin builds when the capabilities they request are available. Avoid release-string, build-number, and Dolphin.exe hash allowlists. Optional capabilities fail independently; failure of GDB or the experimental M4 memory bridge must not reject ordinary play.

## Verified research and existing behavior

- Windows `Dolphin.exe` is selected from an explicit path or bounded directory discovery. Discovery reports a candidate and file presence only; it does not assert authenticity or runtime capability.
- PAL image identity is checked by running DolphinTool's actual `header` operation and requiring PAL `RMGP01`, revision 0. The current DolphinTool is not compared to a historical manifest hash or release string.
- WIT's current version, partition verification, extraction, and file comparison operations are executed. The current WIT executable hash is not compared to manifest provenance.
- The mandatory AllStars inputs are checked against the exact PAL DOL, and the Dolphin installation must include a non-empty Gecko handler file. Presence is a prerequisite, not proof of runtime activation.
- GDB is tested only when requested, using a real RSP connection and live game-memory checks.
- The M4 bridge relies on Dolphin's undocumented Windows named MEM1 mapping. Its static source reference and live mailbox evidence are from Dolphin 2609a. A fixed Gecko handler fingerprint is retained as a bridge-specific capability guard; it is not an emulator release or Dolphin.exe identity requirement. Runtime mapping extent, PID/executable/session, PAL game ID/signatures, mailbox uniqueness, bounds, sequence stability, and limited ACK writes are all checked before use.

## Functional requirements

1. Normal launch and ISO validation do not restrict Dolphin release/build strings or Dolphin.exe hashes.
2. Discovery labels static results as candidates and reports absent optional files without pretending they are runtime support.
3. PAL image checking invokes the current DolphinTool and validates the returned game ID, region, and revision.
4. AllStars stays mandatory and isolated under the selected Dolphin user directory.
5. `-u` user-directory launch setup is applied by the requested launcher flow. The fact that the argument was issued is not proof that every untested build honors it; user-visible runtime checks and file observations are the evidence for that build.
6. When GDB is requested, it must connect and pass live checks. Failure affects the debug request only.
7. Read-only process-memory inspection has no handler or release gate; it requires actual mapping extent, target process/session, live RMGP01, PAL DOL signatures, and bounded reads.
8. M4 reads/writes remain disabled unless the mailbox-specific handler gate and all runtime mapping/game/mailbox checks pass. No fallback scans, guessed addresses, or unchecked writes are allowed.
9. Existing ISO manifests retain source/target DOL, image, delta, DolphinTool and WIT hashes, and WIT version. Tool hashes/version are historical provenance. They are syntax-checked and retained but not identity-matched against later validation tools. Current tool file identities are sampled before/after one run to detect replacement mid-validation.

## Technical design

`dolphin_capabilities.rs` performs bounded executable discovery, checks that the configured platform executable is a non-empty regular file, reports sibling DolphinTool and Gecko handler presence, and contains the bridge-specific codehandler capability check. It has no supported-release enum or Dolphin.exe fingerprint.

The launcher invokes DolphinTool to verify PAL game identity. ISO validation independently runs DolphinTool headers on source/output, WIT `verify` on both partitions, extracts source/output DATA, compares source and target DOL hashes, recomputes VCDIFF, checks AllStars write overlap, compares unchanged game files, and re-hashes source/output images. Validation tools are checked for changes during the run, not compared to historical tool hashes.

The stock MEM1 bridge remains Windows-only because it uses an undocumented named mapping. Both read-only and writable open paths verify that the mapping can cover the minimum retail MEM1 range. Live callers then verify executable/PID association, session, identity/signatures, and mailbox structure. A passing size or filename check by itself is insufficient.

## Dependencies

- User-supplied stock Dolphin installation and DolphinTool.
- User-supplied PAL RMGP01 Rev 0 game and external AllStars patch inputs.
- WIT for partition-aware ISO verification/rebuild.
- Windows named-memory capability for M4; this capability is optional for normal play.

## Risks and limitations

- A regular-file check cannot prove that a user-supplied executable is an official Dolphin build. This code does not claim cryptographic authenticity; launch and requested operations are validated by actual behavior.
- Presence of a Gecko handler and generated GameSettings is not proof AllStars is active on every release. A controlled per-build game boot is required to claim that.
- Dolphin may change `-u`, DolphinTool operations, GDB, Gecko handling, or named-memory layout. Runtime checks catch requested operation failures; GUI boot remains user-run.
- The M4 handler fingerprint is intentionally conservative and may reject a future handler even if compatible. Revalidate the handler/mapping before expanding that feature gate; keep ordinary game launch available.
- A schema-v1 manifest is unsigned. Provenance-only hash edits cannot be authenticated. Integrity-critical image/DOL/delta fields and extracted contents remain checked against actual files.

## Acceptance and testing

- Synthetic tests: no release marker required; missing/ambiguous executable discovery; optional component reporting; updated DolphinTool/WIT hashes and version accepted while historical manifest values stay unchanged; tampered source/output ISO digest, DOL digest, delta, game/revision, AllStars overlap, and DATA mismatch rejected.
- Static/live integration: DolphinTool returns PAL RMGP01 Rev 0; WIT verifies/extracts both images; original image remains unchanged; launch uses isolated user directory and mandatory AllStars.
- GDB: test only when requested; connection failure is isolated to debug mode.
- M4: no writes until handler resource, mapping extent, live identity/signatures, and unique mailbox validate; run live verification for each previously untested build before claiming bridge support.
- No Dolphin launch is performed by unit or ISO validation commands.
