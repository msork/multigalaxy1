# Stock Dolphin read-only memory inspection (M5 Phase 1.6)

## Scope and status

This phase adds a Rust command for reading one caller-selected range from the Windows shared memory section used by official Dolphin 2609a. It is deliberately read-only, is not an actor scanner, and does not follow pointers. No actor addresses or structures were inspected while validating this reader. M4 networking, the existing Gecko mailbox, mandatory AllStars, and isolated profiles are unchanged.

Evidence is separated below:

- **Static source evidence:** verified against the local Dolphin 2609a source at upstream commit `409881c34357d0f105fd473167d15ab0bd9c1628`.
- **Synthetic evidence:** Rust unit tests exercise translation, bounds, mapping failures, typed decoding, and malformed pointers.
- **Live runtime evidence:** none for this new explicit-range command yet. Prior live mailbox use of the same named section does not establish that arbitrary-range inspection has been validated in a running game.

## Mapping findings (static source evidence)

The relevant files in the local checkout are `Source/Core/Common/MemArenaWin.cpp` and `Source/Core/Core/HW/Memmap.cpp` / `Memmap.h`. Their mapping-related contents match upstream 2609a; the local tree has unrelated changes, so it should not be treated as a pristine whole-source checkout.

- `MemArenaWin.cpp` creates a page-file section named `dolphin-emu.<process-id>` with `PAGE_READWRITE`; the section is then viewed at requested offsets with `MapViewOfFileEx`.
- `Memmap.h` declares Wii `MEM1_BASE_ADDR = 0x80000000`, `MEM1_SIZE_RETAIL = 0x01800000` (24 MiB), and `MEM1_SIZE_MAX = 0x04000000` (64 MiB).
- `Memmap.cpp` puts the MEM1 RAM physical region first in its shared section layout, at offset zero, before later regions. In normal Wii address translation, guest `0x80000000` refers to MEM1 offset zero.
- The minimum retail range `[0x80000000, 0x81800000)` is therefore a conservative window that does not cross the following shared-memory regions. This tool intentionally refuses addresses above that lower-bound window even if Dolphin allocated more RAM for a specific configuration.
- Windows view offsets must be aligned to the system allocation granularity (64 KiB on the supported Windows setup). The reader aligns the section offset down, adds the small prefix to the requested bytes, and maps only that bounded window.

This is an implementation detail of Dolphin 2609a, not a supported external-memory API. A future Dolphin release or alternate memory layout must fail validation until source and runtime compatibility are rechecked.

## Reader safety

`inspect-memory` requires the local development config and Dolphin PID. Before reading the requested address, it checks:

1. Development inputs validate against PAL RMGP01 Rev 0 using the configured DolphinTool/game inputs, DOL fingerprint, and Petari symbol file.
2. The configured executable is `Dolphin.exe`, the requested PID resolves to that exact canonical executable path, and it shares the current Windows session.
3. The adjacent codehandler has the expected 2609a size and SHA-256.
4. The prepared mandatory AllStars patch remains valid and has no writes overlapping the three known runtime code signatures.
5. The read-only named mapping opens and can map the final 64 KiB block of the minimum 24 MiB MEM1 region. This validates accessible mapping extent; it does not claim to discover or verify the section's full allocation size.
6. The live game ID is `RMGP01`; three known PAL Rev 0 code byte signatures match the configured DOL. Identity and signatures are checked again after the explicit read.

Each request must be nonempty, at most 8 KiB, and wholly within minimum retail MEM1. Arithmetic is checked, the Windows mapping offset is aligned, and an unexpected or short mapping fails closed. `u32`, `pointer`, and `f32` are 4-byte aligned, exactly four-byte, big-endian reads. Floats must be finite. A decoded non-null pointer must be aligned and point into the bounded MEM1 range; it is only reported and is never dereferenced. The `bytes` format returns only the exact requested bytes.

The new command opens the Dolphin mapping with read-only rights and uses temporary read-only views for explicit ranges outside the existing 64 KiB mailbox view. The old mailbox transport retains its own narrow ACK write path; this new command never calls it. It does not scan, enumerate candidate addresses, mutate memory, install hooks, or alter game/profile files.

## Synthetic validation

`memory_inspection` tests cover:

- guest-to-shared offset translation and 64 KiB alignment;
- lower/upper bounds, zero/oversized lengths, arithmetic overflow, and typed-read alignment/size;
- reading only the explicitly requested bytes from a synthetic mapping;
- a synthetic view-map failure with no fallback scan;
- big-endian integer, pointer, and finite float decoding;
- null, misaligned, out-of-range pointer, and non-finite float rejection.

These tests establish Rust-side policy and decoding, not Dolphin's live mapping behavior. The Windows mapping test for the existing mailbox still uses a separate synthetic page-file section.

## Interactive PowerShell usage

Use the user's normal interactive PowerShell and an already-running Dolphin launched through the isolated development profile. Do not use a regular Dolphin profile. Set the PID explicitly after checking it; do not select the first Dolphin process if more than one is open.

```powershell
Set-Location C:\Users\maxim\Projects\multigalaxy1
$DolphinPid = 12345 # replace with the PID from the isolated Dolphin window
cargo run -p multigalaxy -- inspect-memory .\development.local.toml $DolphinPid 0x80000000 6 bytes
```

Expected shape after the title/game has booted:

```text
memory_read=validated pid=12345 game=RMGP01 revision=0 backend=stock-read-only-mem1 address=0x80000000 length=6 bytes=524d47503031
```

`0x80000000` is used here only to read the six-byte game ID. For a typed decode demonstration without inspecting actor state, this four-byte read is also safe:

```powershell
cargo run -p multigalaxy -- inspect-memory .\development.local.toml $DolphinPid 0x80000000 4 u32
```

It reports `u32=0x524d4750` (the first four ASCII game-ID bytes interpreted as a big-endian word). A `pointer` read at the same address should fail pointer validation; it must not trigger dereferencing.

Stop a command with Ctrl+C if needed. The temporary view is unmapped by process cleanup, and closing the isolated Dolphin session releases the section. No rollback of game, Dolphin, save, or controller files is necessary because this command changes none of them. To remove local build output, use the project's normal Cargo cleanup only if desired; do not remove the ignored development profile while Dolphin is running.

## Validation result and next gate

The reader is ready for the first live *non-actor* read after the user launches the isolated PAL profile and supplies its PID. A successful game-ID read validates the command's basic path only. Before inspecting any actor fields, capture a live read of a known non-actor PAL code signature and verify it matches the DOL, then review each candidate address and structure separately. Never use the generic pointer format as permission to follow a pointer chain.

Rollback is simply to stop using `inspect-memory`; no hooks, injections, or persistent changes were introduced. If Dolphin changes the mapping name, MEM1 layout, minimum span, handler fingerprint, live game ID, or known signatures, the command must fail closed until revalidated.

## Source references

- [Dolphin 2609a Windows MemArena](https://github.com/dolphin-emu/dolphin/blob/2609a/Source/Core/Common/MemArenaWin.cpp)
- [Dolphin 2609a memory map](https://github.com/dolphin-emu/dolphin/blob/2609a/Source/Core/Core/HW/Memmap.cpp)
- [Dolphin 2609a memory map declarations](https://github.com/dolphin-emu/dolphin/blob/2609a/Source/Core/Core/HW/Memmap.h)
