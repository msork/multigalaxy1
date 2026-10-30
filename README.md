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

## Dolphin debug bridge status

The CLI can launch Dolphin in a fresh isolated user directory and read guest memory through Dolphin 2609a's GDB stub. The test/debug-only `--mute` option silences that isolated run; without it, normal audio settings remain in effect. This is not a production mute setting. The supplied PAL RVZ identifies as `RMGP01` revision 0, but the currently available reference DOL differs from Dolphin's loaded executable at `0x800041a3`. No player coordinate address or game hook is verified yet. See [runtime research](docs/research.md), [testing evidence](docs/testing.md), and [debug launch instructions](docs/building.md).
