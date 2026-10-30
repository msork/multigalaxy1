# Controllers

The target abstraction is four independent logical slots. Inputs include analog move, jump, spin, crouch, pointer position, pointer click/shoot, menu controls, motion actions, and character ability. Wii Remote + Nunchuk stays native; Classic Controller mapping is integrated at source level where possible. Dolphin-mapped Xbox and PlayStation devices can use Dolphin's emulated Wii Remote mapping but motion/pointer actions need explicit bindings.

The Galaxy Patch project describes synthesizing Classic Controller state into the KPAD sample ring buffer and redirecting movement/pointer/spin consumers. That patch supports SMG1 regional releases according to its README, but compatibility with MultiGalaxy1 hooks and four simultaneous players is not established. Do not stack DOL patches without overlap detection and revision matching. See `docs/references/classic-controller-patch.md`.
