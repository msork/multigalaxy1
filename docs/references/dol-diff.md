# DOL comparison

## Input verification

| Input | Size | SHA-256 | Result |
|---|---:|---|---|
| Local original `reference/original/main.dol` | 6,283,264 | `2c680585a8f58e1cc9c5521b579057f12b124ff0ef409e470a57606c50a93c09` | Matches expected |
| Local AllReds `reference/allreds/main.dol` | 6,294,444 | `2db4fe4a7eed6af7e69746be7acbc211ff81340d03b2554a2b7ce282c25478f2` | Matches expected |

Hashes were independently checked with Windows `certutil`.

## Reproduced structural comparison

Using a separate read-only PowerShell/.NET byte parser against both local files:

- Entry point: `0x8000403C` in both.
- Modified executable adds text section 2: file offset `0x5FE000`, memory address `0x806BDFA0`, size `0x2BAC` (11,180 bytes).
- Changed bytes in shared file range: 217, across 72 contiguous differing runs.
- The DOL header gains section 2's offset/address/size descriptors at header fields `0x08`, `0x50`, and `0x98`.

The section layout and change counts are reproduced by both a separate parser and the Rust CLI. Five Rust unit tests pass. The CLI decodes 32 changed relative branch instructions that target the added text section; see `allreds-analysis.md` for addresses. Other edits include replacements with PPC NOP (`0x60000000`) and non-hook data edits. These are hook candidates only; target routines and semantics still require fuller PPC disassembly and original function mapping.
