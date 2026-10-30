# Networking

## M0 finding

Dolphin NetPlay is the first option to investigate. Wii NetPlay is officially described as experimental. It supports assigning Wii Remote slots among remote peers, including multiple slots on one host/client PC, and standard NetPlay assignment can accommodate up to four participating input slots. Its lockstep model depends on matching game region/revision, Dolphin version, and emulated environment. It is not an actor replication service. If every instance executes identical mod logic and receives synchronized input, each may simulate all four actors locally without sending actor transforms. This is an architectural inference, not a tested result.

Fair Input Delay is Dolphin's default and gives all players the same delay; Host Input Authority trades zero host latency for higher client latency. The mod should avoid peer-specific authoritative gameplay state. NetPlay can load/save host progress, but this does not overwrite client saves; it can synchronize Wii saves for a session. Saves and progression should therefore be deterministic/shared by construction or have an explicit host-authority/save-copy policy. A disconnect is a session failure/restart unless NetPlay itself handles the client departure without changing input topology; gameplay recovery is unverified.

Camera and viewport choices should be local presentation state. This supports each client viewing the full-screen camera for its own player while preserving local split-screen, provided camera updates and render choices do not alter simulation.

## Experiment plan

1. Confirm four virtual Wii Remote slots synchronize in a stock/local multiplayer test.
2. Add a deterministic synthetic actor driven by a distinct slot and compare state across peers.
3. Test input delay, desync detection, reset/reconnect behavior, save timing, transitions, random events, and mixed performance.
4. Only consider a custom network protocol if required semantics cannot be expressed through NetPlay.

Custom networking would need state authority, transport, rollback/interpolation, prediction, version negotiation, and save ownership, while Dolphin still emulates each console. This complexity is unjustified until NetPlay experiments fail.
