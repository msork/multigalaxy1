# MultiGalaxy1

Rust-first, online-only multiplayer modification research for PAL Super Mario Galaxy 1, with Dolphin as the local emulation backend. Each participant runs a separate Dolphin instance and sees a full-screen local camera. MultiGalaxy1 supplies the launcher, game-side bridge, and online session layer; it does not use Dolphin NetPlay.

## Current status

- M0 research and PAL DOL analysis tooling are in progress.
- The Rust CLI parses and compares DOLs; multiplayer runtime behavior is not implemented.
- Exact PAL revision maps and a Dolphin game-side bridge remain unverified.
- Russian, French, and English client-local localization is a required acceptance case, not a tested feature.
- The PAL AllStars controller patch with both shoulder swaps is mandatory on every launcher path; source fingerprints and PAL Rev 0 compatibility are checked before Dolphin starts.
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

GDB is development-only. The preferred production path is now an opt-in Dolphin 2609a source patch that publishes versioned shared-memory snapshots from `Core::OnFrameEnd`; the patch and Rust watcher are implemented but have not been applied or built because a Dolphin source checkout is unavailable. Only title identity, callback counters, and one guarded read-only MEM1 test word are included. Mario coordinates, 60 Hz cadence, and full-speed gameplay are unverified. See [Dolphin patch instructions](tools/dolphin-bridge/README.md), [performance research](docs/performance.md), and [runtime tests](docs/testing.md). AllStars remains mandatory on launcher paths.
