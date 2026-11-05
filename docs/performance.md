# Performance verification

The stock bridge prototype is a PAL Gecko frame counter/echo plus a bounded Rust view of Dolphin 2609a's named MEM1 mapping. GDB is not used for this bridge because it pauses startup and its `M` command has source-verified instruction-cache/disassembly side effects. No Dolphin performance numbers are currently recorded.

## Paired interactive benchmark

Run only from the user's visible interactive PowerShell, with Dolphin closed between trials. Keep the same isolated profile, D3D12 backend, AllStars patch, controller, game scene, resolution, and 30-second idle interval. The only changed variable is enabling the test Gecko routine.

Baseline:

```powershell
cargo run -p multigalaxy -- dev-launch development.local.toml 'target\mg1-stock-bridge-test' 0
cargo run -p multigalaxy -- bench-dolphin <PID> 30 stock-bridge-off
```

Bridge enabled:

```powershell
cargo run -p multigalaxy -- dev-launch development.local.toml 'target\mg1-stock-bridge-test' 0 --gecko-bridge-probe
cargo run -p multigalaxy -- bench-dolphin <PID> 30 stock-bridge-on
```

The operator records the Dolphin OSD's Speed%, FPS, and VPS for each run. `bench-dolphin` samples process CPU once per second. The bridge console additionally reports game counter update Hz, acknowledgement Hz, polling read Hz, mean/max echo latency, and host read/write duration. Repeat each condition at least three times; compare medians and note scene-specific variance. `gdb-unattached` is paused and never a running-game comparator.

## Evidence status

Synthetic named-mapping unit tests pass. They do not test Dolphin or establish runtime cost. Live frame cadence, game-observed acknowledgement, rendered gameplay, FPS/VPS/speed, CPU, and latency await user-side execution. Do not state “60 Hz” or “no noticeable slowdown” until paired results are recorded.
