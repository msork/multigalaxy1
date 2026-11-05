# Architecture

## Product shape

MultiGalaxy1 is a Rust-first, online-only mod for PAL Super Mario Galaxy 1. Each participant runs one official, unmodified Dolphin instance and receives a full-screen local camera. There is no Dolphin NetPlay and no local split-screen.

```text
Rust launcher/client
  ├─ lobby, identity, game/revision/localization validation
  ├─ Rust transport, prediction, reconciliation, authority
  ├─ official Dolphin process lifecycle and isolated profiles
  └─ candidate stock-compatible guest/host bridge (not yet proven)
       └─ verified PAL PPC hooks and remote actor presentation, if feasible

Authoritative session server
  ├─ lobby/membership/character reservations
  ├─ protocol and tick negotiation
  ├─ progression and contested interaction arbitration
  └─ sequenced events, snapshots, reconnect policy
```

## Rust-first workspace

Rust owns the launcher, protocol, networking, lobby, portable character rules, patch tooling, validation, and tests. Minimal PPC assembly or C/C++ is permitted only for verified game ABI integration. Platform-specific code remains isolated. No stock Dolphin source modifications, custom builds, or Dolphin binary redistribution are allowed.

## State and authority

Player slots are stable identities with transform, velocity, facing, animation, gravity frame, ability, health, and life state. Camera and locale are per-client presentation state and excluded from authoritative simulation hashes. Clients can predict responsive local motion; the server arbitrates progression, stars, boss transitions, shared item ownership, death/respawn, stage changes, and contested interactions. This architecture is a target only: no live bridge or online gameplay is verified.

## Stock Dolphin boundary

A launcher alone cannot expose or render game actors. Stock Dolphin exposes GDB diagnostics, but GDB's runtime cost is not quantified and it pauses on startup. MemoryWatcher is not compiled into Windows 2609a and is one-way. Gecko/DOL patches can execute guest code, but do not inherently provide host IPC. External process memory is unsupported and may race the emulation thread. A low-overhead bidirectional bridge must pass a stock-only feasibility test before implementation. See [stock bridge research](stock-dolphin-bridge.md).

## PAL and localization

Bind support only to verified PAL game ID, revision, DOL hash, provenance, and instruction signatures. Unknown builds fail closed. Gameplay data must match across peers; Russian, French, and English presentation assets remain client-local only after proving they do not affect simulation. Required multilingual interoperability is not yet tested.

## Mandatory controller patch

The PAL AllStars Classic Controller patch remains mandatory on launcher gameplay paths, with both L/ZL and R/ZR swaps enabled. Third-party patch files remain external and separately licensed/attributed. Exact PAL Rev 0 fingerprints are checked before launch. The patch does not provide the online bridge.
