# Compatibility

Initial revision is not selected yet. The supplied original hash is the candidate baseline; verify game ID/revision from a legal disc image and reconcile with toolchain symbols before allowing patching. Patch generation must require an exact expected hash/revision, preserve its input, be deterministic, and reject overlapping hunks/hooks.

Classic Controller support is an integration target, not a present compatibility claim. See `docs/references/classic-controller-patch.md`. Regional variants require separate hashes and hook validation.
