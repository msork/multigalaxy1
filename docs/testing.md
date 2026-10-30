# Testing and evidence

Evidence labels: **research** (documented source), **static** (binary/source inspection), **unit** (synthetic fixtures), **build** (successful compile/link), **boot** (Dolphin starts the patched game), **local gameplay**, and **online gameplay**. Each claim must name the achieved level.

Five Rust unit tests cover synthetic DOL section parsing, malformed input, changed ranges, SHA-256 known vectors, and PowerPC branch target decoding. The CLI was run on both local reference executables and reproduced their exact hashes, entry points, added text section, and 217 changed bytes/72 runs. Runtime regression matrix to grow: 1/2/3/4 players, controller mixes, layouts, abilities, local/online play, stars, death/respawn, transitions, and campaign progression. No Dolphin runtime result is currently established.
