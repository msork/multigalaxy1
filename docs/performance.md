# Performance verification

The stock bridge uses the official Dolphin 2609a named MEM1 mapping and a PAL Gecko frame routine. GDB is not used by this bridge. The coordinate payload currently has only synthetic/static verification; the benchmark below is for the proven counter/ACK payload.

## Paired live Dolphin benchmark (user-reported measurements)

The user completed a paired playable run on stock Dolphin 2609a / PAL RMGP01 Rev 0 using the same isolated profile and AllStars configuration. These are user-reported measurements; raw process traces and captures were not supplied for independent recalculation.

| Condition | Speed | FPS | VPS | Dolphin CPU |
|---|---:|---:|---:|---:|
| Gecko bridge off | 100% | 59.94 | 59.94 | 3.289% |
| Gecko bridge on | 100% | 59.94 | 59.94 | 3.383% |

FPS/VPS and speed were unchanged. CPU increased by 0.094 percentage points. The relative increase of about 2.86% is calculated from the two reported CPU figures, not a separately measured metric. The user also reported ~60 Hz game updates and ACKs across 6,667 ACK samples: mean 16.626 ms, p95 30.422 ms, p99 31.237 ms, and max 141.461 ms. This passes the initial counter/ACK bridge performance gate for that session; it does not establish performance for the larger Mario-position payload.

The maximum is about 8.5 times the reported mean, or roughly 8.5 nominal 60 Hz frame intervals (derived comparisons). The aggregate values do not identify whether it came from host scheduling, an emulator frame delay, or delayed echo processing. No optimization is justified yet. The probe logs a timestamped diagnostic for ACKs over 50 ms with the ACK value and time since the last game-counter change, to help locate future outliers. The existing end-of-session p50/p95/p99/max summary remains the distribution report.

Earlier live observations of ~16.7 ms mean ACK round trip and ~2 microsecond shared-map calls are retained as user-observed behavior, separate from this paired benchmark.

## Coordinate payload validation

The new Gecko payload uses a versioned inline mailbox and an even/odd sequence counter. It marks position invalid before following the pointer chain, checks alignment and MEM1/MEM2 bounds before each dereference, and publishes `valid=1` only after all three coordinates are copied. Rust accepts a sample only when version, marker, stable even sequence, actor range, finite floats, and a matching live frame counter pass. A missing holder or stalled game is reported unavailable; previous coordinates are never replayed as live.

Payload generation, cave bounds, handler signature, and snapshot decoding have automated synthetic tests. **No runtime coordinate sample or performance measurement for this payload has been observed yet.** Follow the interactive test in `docs/testing.md` before describing X/Y/Z as working.
