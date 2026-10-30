# Testing and evidence

Evidence labels: **research** (source documentation), **static** (binary/source inspection), **unit** (synthetic fixtures), **build** (successful compilation), **Dolphin boot**, **two-instance online gameplay**, and **four-player online gameplay**. Do not promote a claim to a higher level than tested.

Current Rust DOL tests cover section parsing, malformed inputs, changed ranges, SHA-256 vectors, and PPC branch target decoding. PAL identity/hook-catalog tests use synthetic fixtures and do not establish a production revision map. The CLI has been used to reproduce hashes, sections, entry points, and AllReds diff ranges from local analysis inputs.

Online acceptance matrix:

| Case | Status |
|---|---|
| Two Dolphin instances exchange bridge-derived state and visibly render each other | Not implemented/tested |
| Independent local camera and controller input | Not tested |
| Four players and all characters | Not tested |
| Authority, prediction, and reconciliation | Not implemented/tested |
| Russian/French/English PAL clients | Not tested; Russian archive unaudited |
| Character abilities, star collection, death/respawn, transitions | Not tested |
| Loss, latency, reconnect, duplicate event handling | Not tested |

No Dolphin boot or multiplayer gameplay result is established.
