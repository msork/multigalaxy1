# Networking

MultiGalaxy1 is online-only: each player runs their own official stock Dolphin instance with a full-screen local camera. Dolphin NetPlay and local split-screen are not used. The Rust networking design targets 1–4 stable player identities (Mario, Luigi, Wario, Waluigi), prediction for responsive local movement, and server authority over progression and contested shared interactions.

A launcher alone cannot read local player state or create/render remote actors. The Gecko counter/echo over Dolphin 2609a named MEM1 has user-observed live bidirectional operation near 60 Hz, but relies on an undocumented Windows mapping. Controlled gameplay overhead, lifecycle recovery, and Mario state extraction remain unverified. MemoryWatcher is unavailable in Windows Dolphin 2609a; GDB stays development-only. See [stock bridge research](stock-dolphin-bridge.md).

Do not begin actor synchronization until paired gameplay performance and live PAL Mario coordinate extraction are verified. If official Dolphin fails those gates, record the blocker; do not silently build a fork.

Localization is client-side by design: Russian, French, and English PAL assets must be audited to ensure gameplay data is identical before mixed-language interoperability can be claimed. This remains untested. Wario/Waluigi characters and Classic Controller support remain roadmap requirements; the mandatory AllStars patch stays external and validated separately from future game hooks.
