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

The CLI supports isolated Dolphin profiles and read-only memory inspection. GDB is a development-only diagnostic, not the planned production multiplayer bridge. Upstream source shows its active stub schedules frequent core callbacks while attached; actual 2609a gameplay slowdown still needs the interactive four-condition benchmark. A versioned atomic shared-memory snapshot and Windows mapping prototype is implemented with synthetic payloads; it is not yet connected to Dolphin or Mario state. Actual coordinates during controlled movement remain unverified. AllStars is mandatory, validated against PAL `RMGP01` Rev 0, and enabled in the per-game profile. PC-specific D3D12, Wii save, and controller profile inputs live only in ignored `development.local.toml`; production paths remain user-selected. See [building](docs/building.md) and [performance research](docs/performance.md).
