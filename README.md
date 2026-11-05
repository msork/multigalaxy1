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

## Stock Dolphin bridge status

MultiGalaxy1 uses an unmodified official Dolphin installation. The previously explored Dolphin source fork/shared-memory publisher is retired; its implementation notes are retained in [bridge feasibility research](docs/stock-dolphin-bridge.md). Stock Dolphin 2609a's GDB supports development memory inspection but may impose runtime cost and pauses at startup. MemoryWatcher is not built into the Windows release and is one-way. Gecko/DOL injection can run guest code but does not itself provide a host IPC channel. No low-overhead, bidirectional stock-Dolphin bridge has yet passed a runtime benchmark, so online actor synchronization remains blocked on that proof. AllStars remains mandatory on launcher paths.
