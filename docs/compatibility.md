# Compatibility

The target is PAL SMG1 only. RMGP01 Rev 0 is identified by the user's disc ID/revision and the exact Petari-matched `main.dol` SHA-256. No production game-side hooks are registered yet. All unknown hashes and revisions fail closed.

The preferred controller patch is the third-party Dolphin per-frame **AllStars** patch with both L/ZL and R/ZR swaps, statically checked against this exact Rev 0 DOL. The launcher can enable it only in a fresh isolated Dolphin profile after verifying the three pinned upstream files and DOL hash. It is not bundled. Dolphin 2609a runtime controls and exact movement/gameplay remain unverified. See `docs/references/classic-controller-patch.md`.

Each PAL revision needs a separate DOL hash and hook validation even when the game ID is the same. Every patch write must land in verified executable text or the reviewed low-memory trampoline range and must not overlap any registered MultiGalaxy1 hook. Localization builds that alter the DOL need their own fingerprint and patch-site validation.
