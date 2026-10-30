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
cargo run -p multigalaxy -- <original.dol> <modified.dol>
cargo run -p multigalaxy -- identify disc-header.bin main.dol
```

Use only game files you are legally entitled to use. The launcher must not download or bundle proprietary game files.

## License

Original MultiGalaxy1 code is released under the [WTFPL Version 2](LICENSE). Third-party code, patches, documentation, and assets retain their own licenses and attribution; this license does not relicense them. In particular, the preferred Classic Controller patch is a third-party Dolphin cheat patch and is not bundled here.

## Dolphin debug bridge status

The CLI can launch Dolphin in a fresh isolated user directory and read guest memory through Dolphin 2609a's GDB stub. The test/debug-only `--mute` option silences that isolated run; without it, normal audio settings remain in effect. This is not a production mute setting. The supplied PAL RVZ identifies as `RMGP01` revision 0, and the current PAL `main.dol` matches every loaded Dolphin section. No player coordinate address or game hook is verified yet. See [runtime research](docs/research.md), [testing evidence](docs/testing.md), and [debug launch instructions](docs/building.md).
