# Architecture

## Product shape

MultiGalaxy1 is a Rust-first, online-only mod for PAL Super Mario Galaxy 1. Each participant runs one official, unmodified Dolphin instance and receives a full-screen local camera. There is no Dolphin NetPlay and no local split-screen.

```text
Rust launcher/client
  ├─ lobby, identity, game/revision/localization validation
  ├─ Rust transport, prediction, reconciliation, authority
  ├─ official Dolphin process lifecycle and isolated profiles
  └─ experimental PAL Gecko + capability-gated named-MEM1 bridge (static/live reference: 2609a) (user-observed live ACK/counter; perf gate pending)
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

A launcher alone cannot expose or render game actors. A reversible Gecko mailbox using Dolphin's named Windows MEM1 mapping has user-observed live counter/ACK operation at ~60 Hz on Dolphin 2609a. A user-confirmed read-only mapping/handler/mailbox preflight passed on Dolphin 2609b; ACK feedback has not yet been tested there. No Dolphin release is locked for ordinary play. The optional mailbox uses a known handler capability and runtime mapping/game checks; untested builds remain unverified. Paired performance, pause/scene recovery, and player-state payloads remain unverified. GDB remains development-only; MemoryWatcher is not compiled into Windows 2609a. Actor synchronization cannot begin until performance and Mario-state gates pass. See [stock bridge research](stock-dolphin-bridge.md) and [M4 2609b validation](references/m4-2609b-validation.md).

## PAL and localization

Bind support only to verified PAL game ID, revision, DOL hash, provenance, and instruction signatures. Unknown builds fail closed. Gameplay data must match across peers; Russian, French, and English presentation assets remain client-local only after proving they do not affect simulation. Required multilingual interoperability is not yet tested.

## Mandatory controller patch

The PAL AllStars Classic Controller patch remains mandatory on launcher gameplay paths, with both L/ZL and R/ZR swaps enabled. Third-party patch files remain external and separately licensed/attributed. Exact PAL Rev 0 fingerprints are checked before launch. The patch does not provide the online bridge.
