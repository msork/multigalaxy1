# Dolphin Classic Controller patch

## Selected PAL SMG1 variant

Use the SMG PAL **AllStars** patch from [Starlightbotanist's Dolphin patch collection](https://github.com/Starlightbotanist/SMG-SMG2ClassicControllerDolphinPatches). Do not use Nvidia. Original patch attribution belongs to **SuperHackio**; Starlightbotanist converted the Riivolution patch to Dolphin's per-frame patch format.

The AllStars mapping diagram assigns movement and Star Ball/Ray Surfing to the left stick; Star Pointer, Star Bits, Pull Stars, and aiming to the right stick; ZL to crouch/dive; L to camera reset; D-pad to camera/options; Select to pause; Home to HOME; ZR to shoot Star Bits/cancel; the face controls cover jump and spin/context actions. Use the upstream [SMG AllStars diagram](https://github.com/Starlightbotanist/SMG-SMG2ClassicControllerDolphinPatches/blob/main/SMG/Mapping-SMG-AllStars.png) for the button artwork. The controller image labels indicate functions but do not provide machine-readable button names for every face-button/context action.

The user requested both shoulder swaps too. The PAL `SMG-SwapLZL-Pal.txt` replaces AllStars writes at `0x80000B1C` and `0x80000B2C`; `SMG-SwapRZR-Pal.txt` replaces writes at `0x80000AEC` and `0x80000B0C`. These addresses overlap the base AllStars program by design. Do not enable the three files as competing per-frame codes. MultiGalaxy1 merges the two exact swap fragments into the AllStars write set, so each address has one deterministic final value.

## Static inspection and revision compatibility

The user's local files under `SMG/PAL/Individual Patches` were inspected as text only; nothing was executed or copied into the repository. SHA-256 fingerprints:

| File | SHA-256 |
|---|---|
| `SMG-AllStars-Pal.txt` | `c3b00b9e7471f980b887390d3b8bff11fd102be76260d60a42f218103617d5a9` |
| `SMG-SwapLZL-Pal.txt` | `5003d243aebbac6308702586d333ada04038d9dbe0aaf5be55c6ad907a71427b` |
| `SMG-SwapRZR-Pal.txt` | `f1444b6e45cd8349333155c750c160ad364888cdc204e0acd4b83cf0ecc362d5` |

The AllStars base has 419 unique writes. Comparing every address with the exact Petari-matched PAL RMGP01 Rev 0 `main.dol` shows 76 writes land in DOL text and 71 of those replace a different original word. The remaining 343 writes are in the patch's low-memory trampoline area (`0x80000000–0x80003fff`). All writes were checked to fall in one of these ranges. The code replaces instructions and branches across text sites up to `0x804a432c`; the patch filename itself does not declare a revision or DOL hash. Thus address compatibility with this exact DOL is statically established, but original call-site semantic correctness and runtime behavior are not yet proven.

Current MultiGalaxy1 has no game-side hooks registered, so there are no current project hooks to collide with. The launcher rejects any overlap with future registered hook sites. The exact patch files are hash-pinned; different bytes fail closed.

## Dolphin installation and launcher behavior

The source file uses Dolphin `[OnFrame]` entries in `0xADDRESS:dword:0xVALUE` form. Dolphin's official SMG `RMG.ini` has an `[OnFrame]` section for these frame patches; enabled patch names are listed in `[OnFrame_Enabled]`, and cheats are gated by `Core.EnableCheats`. Every launcher invocation requires the patch folder and PAL `main.dol`. It verifies the exact PAL Rev 0 DOL and the three patch hashes, merges the swaps, writes `GameSettings/RMGP01.ini` plus the cheat enable setting inside an isolated Dolphin profile, and fails closed when validation fails. It never edits the RVZ or regular Dolphin profile.

No patch assets are committed. The upstream repository has no license file or explicit redistribution grant in the inspected tree, so retain its third-party status and attribution. WTFPL applies only to original MultiGalaxy1 code.

## Gamepad mapping and verification status

Map Xbox/PlayStation devices in Dolphin's emulated Wii Remote configuration with the Classic Controller extension selected. Bind the sticks and buttons to the patch's expected Classic Controller inputs; Dolphin provides the conventional device mapping, while pointer and shake actions require explicit stick/button emulation. This code is for each online player's single local Dolphin instance, not local split-screen.

The files have passed static hash, format, composition, DOL address-range, and Rev 0 fingerprint checks. Neither activation nor gameplay has yet been tested in Dolphin 2609a. Button behavior, conventional pad bindings, and multilingual interaction remain runtime test items.
