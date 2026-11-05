# MultiGalaxy1

Rust-first, online-only multiplayer modification research for PAL Super Mario Galaxy 1, with Dolphin as the local emulation backend. Each participant runs a separate Dolphin instance and sees a full-screen local camera. MultiGalaxy1 is developing a launcher, a game-side bridge prototype, and an online session layer; it does not use Dolphin NetPlay.

## Current status

- M0 research and PAL DOL analysis tooling are in progress.
- The Rust CLI parses and compares DOLs; multiplayer runtime behavior is not implemented.
- PAL game-side state extraction remains unverified; the stock Dolphin Gecko mailbox has user-observed live bidirectional operation at approximately 60 Hz.
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

MultiGalaxy1 uses an unmodified official Dolphin installation. The retired Dolphin fork is not part of the project. A Windows Dolphin 2609a Gecko counter/echo bridge has user-observed live bidirectional operation at about 60 Hz through Dolphin's undocumented named MEM1 mapping. A controlled playable bridge-on/off performance comparison, Mario coordinate extraction, and online actor synchronization remain pending. AllStars stays mandatory on launcher paths. See [bridge evidence and interactive test instructions](docs/stock-dolphin-bridge.md).
