# Characters

See [launcher character selection specification](../specs/launcher-character-selection.md) for the persistent four-character choice, identity boundaries, PAL movement evidence, and test plan. The selection UI and persistence are not implemented yet.

Use data-driven Rust configurations for acceleration, top speed, traction, jump impulse, airtime, spin, and ability parameters. Engine-backed collision, animation, and interaction behavior is not implemented. All abilities must preserve mandatory campaign traversal, with a neutral fallback for progression-critical moves.

PAL RMGP01 Rev 0 DOL defaults statically show Luigi's walk speed (15 versus Mario's 13) and higher inertia values; jump-height entry 0 is 22 for both, while Luigi's jumping-gravity value is lower (about 1.6 versus 1.8). The source and constants support the design direction, but do not constitute in-game movement validation. Proposed compositions: Wario combines Luigi-style slippery handling with Mario-style slow/low-jump targets; Waluigi combines Mario-style easy handling with Luigi-style fast/high-jump targets. These are design rules, not runtime implementation.

Wario and Waluigi model/animation sourcing needs a separate rights review. Only original or permissively licensed assets may be distributed; never extract or ship Nintendo assets.
