# Performance verification

The stock bridge is a PAL Gecko frame counter/echo plus a bounded Rust view of Dolphin 2609a's named MEM1 mapping. GDB is not used for this bridge because it pauses startup and its `M` command has source-verified instruction-cache/disassembly side effects.

## User-observed live result (not a controlled benchmark)

The user reported an interactive stock-Dolphin 2609a / PAL RMGP01 Rev 0 run with `state=live`, game update rate ~59–60.5 Hz, ACK rate ~59–60 Hz, mean ACK round-trip ~16.7 ms, and shared read/write operations ~2 μs each. They observed smooth gameplay at 100% emulation speed while the bridge was live. These values verify live bidirectional mailbox operation by user observation. They are not a paired bridge-off/bridge-on benchmark, and no FPS/VPS/CPU samples were supplied.

The user reported per-report-window maximum ACK latency around 30–47 ms. This is occasional jitter of roughly two to three 60 Hz frames. The available mean and max values do not define the distribution. The probe now retains completed ACK latencies for the session and prints mean, population standard deviation, p50, p95, p99, and max when stopped with `Q`.

## Paired interactive benchmark

Run only from the user's visible interactive PowerShell, with Dolphin closed between trials. Keep the same isolated profile, D3D12 backend, AllStars patch, controller, game scene, resolution, and repeatable 60-second movement/jump route. The only changed variable is enabling the test Gecko routine.

Baseline:

```powershell
cargo run -p multigalaxy -- dev-launch development.local.toml 'target\mg1-stock-bridge-test' 0
cargo run -p multigalaxy -- bench-dolphin <PID> 60 stock-bridge-off
```

Bridge enabled:

```powershell
cargo run -p multigalaxy -- dev-launch development.local.toml 'target\mg1-stock-bridge-test' 0 --gecko-bridge-probe
cargo run -p multigalaxy -- bench-dolphin <PID> 60 stock-bridge-on
```

Record Dolphin OSD Speed%, FPS, and VPS for each run. `bench-dolphin` samples process CPU once per second and prompts for OSD averages. The bridge console reports game counter update Hz, ACK Hz, read Hz, rolling mean/max ACK latency, and host read/write/validation scan cost. On `Q`, it prints session count, mean, standard deviation, p50, p95, p99, and maximum latency. Run one paired 60-second gameplay trial first; repeat if results vary. `gdb-unattached` is paused and never a running-game comparator.

## Evidence status

Live bridge handshake and cadence are user-observed as above. Synthetic regression tests do not establish performance. The controlled gameplay comparison (FPS/VPS/speed/CPU), title/menu, pause/resume, and scene-transition behavior remain pending interactive validation. Do not infer “no noticeable slowdown” from the smoothness observation alone.
