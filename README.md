# MultiGalaxy1

Rust-first, online-only multiplayer modification research for PAL Super Mario Galaxy 1, with Dolphin as the local emulation backend. Each participant runs a separate Dolphin instance and sees a full-screen local camera. MultiGalaxy1 is developing a launcher, a game-side bridge prototype, and an online session layer; it does not use Dolphin NetPlay.

## Current status

- M0 research and PAL DOL analysis tooling are in progress.
- The M4 Rust localhost position relay and PAL Mario coordinate mailbox are implemented; two-instance live synchronization is not yet verified.
- A read-only preflight and one bounded ACK write/echo/rollback passed on Dolphin 2609b. This is bridge feedback validation, not multiplayer validation.
- Russian, French, and English client-local localization is a required acceptance case, not a tested feature.
- The PAL AllStars controller patch with both shoulder swaps is mandatory on every launcher path; source fingerprints and PAL Rev 0 compatibility are checked before Dolphin starts.
- Dolphin compatibility is capability-based; the general launcher has no Dolphin release or executable-hash allowlist. The changed-byte ISO has user-confirmed basic boot and controllable Mario on 2609a and 2609b. M4 read-only validation and ACK feedback passed on 2609b; two-instance relay and AllStars runtime activation remain unverified there.
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

MultiGalaxy1 uses an unmodified official Dolphin installation. The retired Dolphin fork is not part of the project. A Windows Gecko counter/echo bridge has live evidence on Dolphin 2609a, and a bounded ACK write/echo/rollback passed on 2609b through Dolphin's undocumented named MEM1 mapping. Two-instance position relay, AllStars runtime activation on 2609b, and online actor synchronization remain unverified. AllStars stays mandatory on launcher paths. See [bridge evidence and interactive test instructions](docs/stock-dolphin-bridge.md).
