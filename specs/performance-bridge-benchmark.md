# Retired: Runtime Memory Bridge and GDB Benchmark

This benchmark plan described a custom Dolphin fork with a frame-end shared-memory publisher. That architecture is retired under the permanent official-stock-Dolphin-only decision. The prior synthetic Rust named-mapping measurement (approximately 61 writes/s between two Rust processes) only verified host IPC; it did not include Dolphin or emulated memory and is not evidence for a game bridge.

The GDB source investigation and remaining stock-only test matrix are documented in [performance.md](../docs/performance.md) and [stock-dolphin-bridge.md](../docs/stock-dolphin-bridge.md). No controlled numeric gameplay comparison is available. Do not use the retired `bridge-enabled`/`bridge-disabled` modes or report synthetic IPC throughput as Dolphin performance.
