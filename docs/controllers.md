# Controllers

Each online participant uses one Dolphin instance and one local controller. The portable input abstraction covers analog movement, jump, spin, crouch, pointer position and click, menu controls, motion actions, and character abilities. Wii Remote + Nunchuk remains native.

The preferred Classic Controller solution is the Dolphin Gecko patch described in [the patch research](references/classic-controller-patch.md), originally authored by SuperHackio and converted to Dolphin format by Starlightbotanist. Xbox and PlayStation controllers can be mapped in Dolphin to the emulated Wii Remote/Classic Controller inputs expected by the patch. Axes and buttons must be explicitly assigned; pointer and shake/spin need deliberate stick/button mappings because conventional pads have no IR pointer or accelerometer.

This is per-client input support, not evidence of four local controller slots in one game instance. Do not stack the patch with MultiGalaxy1 hooks until all writes and branch targets are checked against the exact PAL DOL. A localization that changes the DOL or code at patch sites needs its own verified fingerprint and mapping.
