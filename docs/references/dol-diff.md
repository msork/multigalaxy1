# DOL comparison

## Input verification

| Input | Size | SHA-256 | Result |
|---|---:|---|---|
| User-supplied PAL `reference/original/main.dol` | 6,283,264 | `8200fb72b3170069bb71c8b3dc21b49e429977ce8ae5159bb95ea6a19de0fbee` | Matches every loaded section in Dolphin 2609a (`RMGP01`, Rev 0) |
| Historical AllReds `reference/allreds/main.dol` | 6,294,444 | `2db4fe4a7eed6af7e69746be7acbc211ff81340d03b2554a2b7ce282c25478f2` | Historical USA comparison input; not compared to current PAL |

Hashes were independently checked with Windows `certutil`. SHA-1 for the current PAL DOL is `9bd1daa520bd1f573b6a4b82e03336e9c51b511f`. The earlier USA DOL (`9a71008ae1ee9010e267fa67d1f0b0d4f0e895dd`) was replaced and is no longer retained.

## Historical structural comparison (USA)

The comparison below was performed against the former USA `RMGE01` Rev 0 DOL, not the current PAL file. The USA input has been replaced and is no longer available at the old path. Therefore these figures are historical AllReds analysis and cannot be reproduced from the current PAL file without obtaining the matching USA input again.

Using a separate read-only PowerShell/.NET byte parser against both local files:

- Entry point: `0x8000403C` in both.
- Modified executable adds text section 2: file offset `0x5FE000`, memory address `0x806BDFA0`, size `0x2BAC` (11,180 bytes).
- Changed bytes in shared file range: 217, across 72 contiguous differing runs.
- The DOL header gains section 2's offset/address/size descriptors at header fields `0x08`, `0x50`, and `0x98`.

The section layout and change counts are reproduced by both a separate parser and the Rust CLI. Five Rust unit tests pass. The CLI decodes 32 changed relative branch instructions that target the added text section; see `allreds-analysis.md` for addresses. Other edits include replacements with PPC NOP (`0x60000000`) and non-hook data edits. These are hook candidates only; target routines and semantics still require fuller PPC disassembly and original function mapping.
