# Classic Controller patch research

The identified project is [Wii-Patches/Galaxy-Patch](https://github.com/Wii-Patches/Galaxy-Patch), published under MIT. Its README states that it supports SMG1 USA (`RMGE01`), Europe (`RMGP01`), Japan (`RMGJ01`), and Korea (`RMGK01`) and describes synthesizing KPAD Classic Controller state, redirecting movement/pointer/spin handling, and polling GameCube SI ports because SMG lacks a PAD library. It has explicit mappings for movement, pointer, jump, spin, crouch/ground pound/long jump, star-bit shooting, camera adjustment, and menus.

The README and license have been inspected, but source hooks have not yet been audited at a pinned revision. Regional support and mappings are author-reported, not independently runtime-tested here. It does not establish simultaneous four-controller support. Plan on shared source-level integration or conflict-aware patch generation and test motion-dependent stages.
