# MultiGalaxy1

Rust-first, online-only multiplayer modification research for PAL Super Mario Galaxy 1, with Dolphin as the local emulation backend. Each participant runs a separate Dolphin instance and sees a full-screen local camera. MultiGalaxy1 supplies the launcher, game-side bridge, and online session layer; it does not use Dolphin NetPlay.

## Current status

- M0 research and PAL DOL analysis tooling are in progress.
- The Rust CLI parses and compares DOLs; multiplayer runtime behavior is not implemented.
- Exact PAL revision maps and a Dolphin game-side bridge remain unverified.
- Russian, French, and English client-local localization is a required acceptance case, not a tested feature.
- See [milestones](docs/milestones.md), [testing evidence](docs/testing.md), and [online architecture](docs/networking.md).

## Development

```powershell
cargo test --workspace
cargo run -p multigalaxy -- reference/original/main.dol reference/allreds/main.dol
cargo run -p multigalaxy -- identify disc-header.bin main.dol
```

Use only game files you are legally entitled to use. The launcher must not download or bundle proprietary game files.
