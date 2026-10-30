# Client-local localization

## Required outcome

Russian, French, and English PAL players can share one custom online session, each seeing their chosen language. Each player runs their own full-screen Dolphin instance. This is an acceptance target, not a verified capability.

## Architecture

Gameplay code/data, physics, progression, actor state, and bridge protocol must be identical across peers. Text, font, voice, and presentation assets may remain client-local only after audit proves they do not affect simulation, timing, collision, branching, or progression. Locale package IDs/hashes are reported to the session server; only explicitly audited presentation packages are allowed.

The custom launcher selects local language/assets independently from server-authoritative gameplay. No Dolphin NetPlay is used. The server must reject mismatched gameplay builds, unknown revisions, and unapproved locale packages.

## Russian patch evidence

The requested Zone of Games page and `SuperMarioGalaxy_wii.rar` could not be retrieved or found locally. Archive contents, installation, PAL revisions, changed files, and license are unverified. No installer was executed. See [Russian patch audit](references/russian-patch.md).

## Acceptance test

Launch three separate Dolphin clients using the same supported PAL revision and gameplay build, with Russian, French, and English local presentation respectively. Verify lobby compatibility, game-side bridge operation, language persistence across transitions, gameplay-state hash agreement, star collection, boss progression, death/respawn, and no cross-locale text leakage. Repeat with a modified gameplay payload and unknown locale package; both must be rejected.

## Status

Not runtime tested. No multilingual interoperability claim is made.
