# Stock Dolphin bridge feasibility task

## Objective

Prove or disprove a safe, low-overhead, bidirectional game-state bridge using official unmodified Dolphin only. Preserve the online-only 1–4 player architecture and do not modify Dolphin binaries/source.

## Verified research

- Official 2609a supports Gecko/AR patches, but guest code execution alone does not provide host IPC.
- GDB RSP supports development memory operations, has one persistent client, pauses at startup, and has reported but unquantified overhead.
- Windows 2609a excludes MemoryWatcher at CMake configuration; the UNIX facility is one-way changed-value reporting.
- External host process memory has no supported guest mapping and may race emulation.
- Previously explored Dolphin fork/shared mapping is retired and must not be reactivated.

## Requirements

Investigate official launcher, debugger, OS IPC, loader, and game/IOS options. Any candidate must support verified PAL RMGP01 Rev 0, bidirectional snapshots/commands near 60 Hz, stale detection, bounds safety, and no noticeable gameplay slowdown. AllStars stays mandatory and isolated from multiplayer hook composition.

## Design and dependencies

First prototype candidates outside production game code and benchmark on a user-visible interactive desktop. Keep the GDB probe read-only. Do not add guest writes or guessed addresses. Dependencies include exact PAL disassembly/hooks, a stock channel, Dolphin 2609a runtime, and an interactive performance run.

## Risks

There may be no supported stock-Dolphin host IPC. Guest-only Gecko hooks cannot expose local host mappings. GDB may be too costly. Windows external process reads may be racy and version-fragile.

## Acceptance criteria

A live two-way test runs with stock Dolphin, updates near 60 Hz, passes safe address/identity checks, and shows no noticeable slowdown against a paired no-bridge baseline. Otherwise document the technical limitation and stop before production bridge implementation.

## Testing strategy

Unit-test protocol and candidate transport with synthetic fixtures. Separately validate official Dolphin executable provenance, PAL image identity, patch activation, game boot, bidirectional exchange, cadence, and repeated gameplay performance. Compilation or a synthetic IPC microbenchmark is not runtime acceptance.
