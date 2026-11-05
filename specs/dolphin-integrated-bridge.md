# Retired: Dolphin-integrated shared-memory bridge

This proposal is retired by the permanent stock-Dolphin-only decision. Do not apply `integration-2609a.patch`, build a custom Dolphin, or distribute a fork. The former design targeted a Windows named mapping from Dolphin's frame-end callback and a Rust reader; it was not a verified production bridge and did not establish Mario-state exchange or full-speed gameplay.

Useful research and the remaining feasibility questions are preserved in [stock-dolphin-bridge.md](../docs/stock-dolphin-bridge.md). The C++/patch/build implementation has been removed from the active repository. The ignored local source/build tree was left untouched. Any future implementation must use official Dolphin and pass the stock bridge benchmark before being considered.
