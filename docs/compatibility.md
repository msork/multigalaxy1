# Compatibility

Current target scope is PAL SMG1 only. No PAL revision is yet accepted for patching. The supplied original DOL hash is a candidate executable fingerprint, but it is not tied to a verified game ID/revision because the DOL does not contain the full disc identity. Read `RMGPxx` game ID and revision from the user's disc header, then match both and exact main.dol SHA-256 to a verified map. The Rust detector returns unknown for unmapped identities/hashes. A map requires expected original bytes and provenance for each hook; map validation rejects unverified sites, out-of-text addresses, signature mismatches, and overlaps.

Classic Controller support is an integration target, not a present compatibility claim. See `docs/references/classic-controller-patch.md`. Each PAL revision needs a separate DOL hash and hook validation even when its game ID is the same.
