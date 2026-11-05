# Networking

MultiGalaxy1 is online-only: each player runs their own official stock Dolphin instance with a full-screen local camera. Dolphin NetPlay and local split-screen are not used. The Rust networking design targets 1–4 stable player identities (Mario, Luigi, Wario, Waluigi), prediction for responsive local movement, and server authority over progression and contested shared interactions.

A launcher alone cannot read local player state or create/render remote actors. No stock-Dolphin-compatible game-state bridge has been proven. Gecko/DOL hooks can run guest PPC code but do not inherently expose host IPC. GDB is a development diagnostic; MemoryWatcher is unavailable in Windows Dolphin 2609a and is one-way on Unix; external process reads are unsupported/racy. See [stock bridge research](stock-dolphin-bridge.md).

Do not begin actor synchronization implementation until a stock-only prototype proves safe bidirectional exchange near 60 Hz with no noticeable gameplay slowdown. If official Dolphin cannot provide a suitable channel, record the blocker; do not silently build a fork.

Localization is client-side by design: Russian, French, and English PAL assets must be audited to ensure gameplay data is identical before mixed-language interoperability can be claimed. This remains untested. Wario/Waluigi characters and Classic Controller support remain roadmap requirements; the mandatory AllStars patch stays external and validated separately from future game hooks.
