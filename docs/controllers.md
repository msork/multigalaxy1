# Controllers

Each online participant uses one Dolphin instance and one local controller. The portable input abstraction covers analog movement, jump, spin, crouch, pointer position and click, menu controls, motion actions, and character abilities. Wii Remote + Nunchuk remains native.

The preferred Classic Controller setup is SMG1 PAL AllStars with both L/ZL and R/ZR swap fragments composed into the base patch; Nvidia is not used. See [the patch research](references/classic-controller-patch.md) for button functions, pinned hashes, and verification status. Xbox and PlayStation pads map through Dolphin's emulated Wii Remote configuration with the Classic Controller extension selected. Configure the analog axes and buttons explicitly; pointer and motion actions need stick/button emulation because conventional pads lack Wii IR and accelerometer signals.

The launcher installs the patch only when the explicit `--allstars` option is supplied, and only into a fresh isolated Dolphin profile. This is per-client input support, not four local controller slots in one game process. Future MultiGalaxy1 hooks must pass patch overlap validation before combining.
