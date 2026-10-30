# Dolphin Classic Controller patch

Preferred patch: [SMG PAL AllStars](https://raw.githubusercontent.com/Starlightbotanist/SMG-SMG2ClassicControllerDolphinPatches/refs/heads/main/SMG/PAL/Individual%20Patches/SMG-AllStars-Pal.txt). **Use the AllStars variant only. Do not use Nvidia.** The upstream repository supplies separate SMG and SMG2 patches; this project requires the SMG1 PAL file. The collection says the patches were originally created as Riivolution files by **SuperHackio** and converted to Dolphin format by **Starlightbotanist**.

## Format and installation

The selected PAL file is a Dolphin per-frame memory patch list, with entries formatted as `0xADDRESS:dword:0xVALUE` (the repository calls these patches/codes). The upstream README says to map a Classic Controller, add the code under the game-specific User Config `[OnFrame]` section with a `$` name, then enable it under the game's Patches list. The Dolphin GameINI convention uses an `[OnFrame_Enabled]` section containing the enabled patch name; official Dolphin's own `RMG.ini` has an `[OnFrame]` section for per-frame patches. This is an emulator patch; it does not require MultiGalaxy1 to rewrite the user's RVZ or bundle a patched DOL. Dolphin 2609a's exact runtime compatibility with this code has not yet been tested.

The file has 419 lines of PPC guest-memory patches. Inspected sites include low-memory trampoline/code ranges around `0x800009B0–0x80000BC0`, `0x800014B0–0x800015D0`, `0x80002790–0x800029B0`, plus many hooks through `0x804A432C`. It overwrites game instructions and installs branches. The filename identifies PAL but does not state an RMGP01 revision or main.dol hash. Therefore the upstream label alone does not prove compatibility with RMGP01 Rev 0. Before enabling it alongside MultiGalaxy1, compare every patched address and branch target against the verified DOL, confirm expected original instructions, and reject any overlap with our hooks. The current MultiGalaxy1 revision has no game-side hook ranges registered, so future overlap compatibility is not yet testable.

## Mapping and conventional gamepads

The upstream README links a separate **SMG AllStars** mapping diagram. It offers variants Nvidia, AllStar, Swap L/ZL, and Swap R/ZL; only AllStar is selected here. The repository README does not give a textual button-by-button list, and this diagram has not been independently inspected in this work, so no exact mapping table is asserted yet. Link: [SMG AllStars mapping image](https://github.com/Starlightbotanist/SMG-SMG2ClassicControllerDolphinPatches/blob/main/SMG/Mapping-SMG-AllStars.png).

Map Xbox and PlayStation controllers in Dolphin to the emulated Wii Remote/Classic Controller controls expected by this patch. Assign analog movement and any pointer/spin inputs explicitly; conventional pads do not provide Wii IR or accelerometer signals by themselves. This is local input for each user's separate Dolphin instance, not four local players in one game process.

## Licensing, attribution, and localization

The inspected upstream tree exposes no license file or explicit redistribution grant. Treat its Gecko text and mapping image as third-party copyrighted material; do not copy or bundle them without permission. Preserve attribution to SuperHackio (original patch) and Starlightbotanist (Dolphin-format collection). MultiGalaxy1's WTFPL applies only to original project code.

Localization remains client-side. A translated installation can use this patch only if its game ID/revision and patch-site bytes match the verified build. Any translation that changes main.dol or a patched location needs its own DOL fingerprint, original-byte checks, and conflict validation. Different client-side text/voice assets do not by themselves conflict with the controller patch, but this has not been tested with the Russian/French/English installations.

## Verification status

Static inspection confirms the AllStars PAL code file and Gecko format. Exact RMGP01 Rev 0 compatibility, Dolphin 2609a runtime, AllStars button diagram, hook coexistence, Xbox/PlayStation bindings, and multilingual interaction remain unverified. No upstream patch asset is committed.
