# MultiGalaxy1

An open source multiplayer modification research and development project for the original Super Mario Galaxy on Wii, intended for Dolphin Emulator. The project is early-stage: current work establishes binary-analysis tooling and evidence; no multiplayer runtime feature is implemented yet.

## Current status

- M0 research and feasibility is in progress.
- `multigalaxy-patch` and the `multigalaxy` CLI provide Rust DOL inspection and comparison.
- Local reference executables are analysis inputs only and must not be redistributed.
- See [docs/milestones.md](docs/milestones.md) for acceptance status and [docs/testing.md](docs/testing.md) for evidence levels.

## Development

Requires Rust and Cargo (see the workspace `rust-version`).

```powershell
cargo test --workspace
cargo run -p multigalaxy -- reference/original/main.dol reference/allreds/main.dol
```

Only use game files you are legally entitled to use. The patch workflow is intended to consume a user-supplied clean executable and never downloads proprietary files.

## Project documents

See `docs/` for research, feasibility, architecture, controls, networking, rendering, compatibility, and milestone specifications. Binary findings are recorded in `docs/references/`.
