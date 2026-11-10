# M4 specification: two-player position relay

## Objective

Exchange fresh PAL Mario position snapshots between two independent Rust clients, each attached read-only to its own official Dolphin process when the feature-specific mapping and mailbox checks pass. Use a localhost TCP server as the authoritative admission, freshness, sequence, and presence relay. Dolphin 2609b has passed live preflight and one ACK write/echo/rollback; two-instance movement relay remains unverified. This milestone does not create remote in-game actors.

## Verified research and starting evidence

- The stock-Dolphin Gecko mailbox provides a validated counter/ACK and versioned PAL position snapshot. The user reports live Mario X/Y/Z updates near 60 Hz. Its pointer path is statically bound to the RMGP01 Rev 0 DOL and Petari symbols.
- The named Windows MEM1 map is an undocumented Dolphin implementation detail, not a stable API. Static source review was against 2609a; runtime mapping, PAL identity, handler, and mailbox checks are required on every process. Dolphin 2609b has passed these checks and one controlled ACK write/echo/rollback.
- The previously validated AllStars patch and per-instance isolated profiles remain required. Each participant owns a separate Dolphin process and profile.
- Counter/ACK bridge performance was user-reported at 100% and 59.94 FPS/VPS both off and on; the expanded position publisher/network path has not been benchmarked.

## Functional requirements

1. Two independent local clients identify as Mario or Luigi and bind to separate Dolphin PIDs.
2. Each client validates live RMGP01 identity, the known Gecko handler capability fingerprint, unique mailbox signature/version, seqlock, freshness counter, actor range, and finite coordinates before sending a position.
3. The TCP server admits at most one client per player ID and two total clients. It validates protocol version, bounded frame length, identity/revision, timestamps, finite/range-bounded position, and strictly increasing per-session sequence numbers.
4. The server relays snapshots as server-approved peer state and sends explicit peer-presence removal on disconnect or stale data. No progression, event, health, input, or actor state is exchanged.
5. Clients reject malformed, stale, future, duplicate, or out-of-order peer snapshots, expire remote state after a bounded timeout, and print remote ID, XYZ, age, and approximate update rate.
6. TCP disconnects retry with bounded backoff. Server-side duplicate IDs and full lobbies fail clearly; reconnect is accepted after the previous socket closes.
7. The existing `dev-launch --gecko-bridge-probe` flow and single-instance probe behavior remain available.

## Protocol and technical design

Use Rust `std::net::TcpListener/TcpStream` for this local milestone. Frames have a fixed 26-byte big-endian header: magic `MG1N`, protocol version `u16`, message kind `u8`, player ID `u8`, sequence `u64`, Unix timestamp milliseconds `u64`, payload length `u16`. Maximum payload is 32 bytes. Hello carries six-byte game ID, revision, and local Dolphin PID; the server rejects both player identities attaching to the same PID. Position carries frame counter and three IEEE-754 `f32` values; PeerGone has no payload. Version 1 uses IDs 1=Mario, 2=Luigi. Each TCP connection is bound to its Hello ID; a client cannot change identity in later frames. PID uniqueness is a local-milestone check, not a cross-host identity guarantee.

The server validates and relays snapshots but does not simulate gameplay. Per-connection workers feed a synchronized two-slot lobby. Each client uses a read-only MEM1 mapping, samples at about 60 Hz, and has an independent TCP reader so incoming peers do not block local sampling. Local mailbox unavailability sends no coordinates; server/client freshness timers expire the remote view. No data from this protocol is written into Dolphin.

## Dependencies

No new external crates. Standard-library TCP, threads, channels, mutexes, monotonic timers, and existing `StockMem1`/Gecko validation are sufficient. Client attachment uses a Windows stock Dolphin named-memory mapping and PAL RMGP01 Rev 0; 2609b has read-only and ACK feedback evidence, but the live network relay remains to be tested.

## Risks and failure handling

- TCP is a reliable localhost milestone transport, not an internet deployment design. It has no authentication or encryption; bind to loopback by default.
- Wall clocks can differ even locally. Reject timestamps more than 2 seconds in the future or older than 2 seconds on ingress; use `Instant` for local peer expiry and display age.
- Never trust remote IDs from a position frame over the Hello-bound identity. Sequence numbers reset only on a new TCP session.
- An unavailable/paused/transitioning local mailbox yields no snapshot. The server removes the peer after 1 second without fresh positions; clients independently expire after 1 second.
- A malformed frame or identity mismatch closes that connection; do not crash the server or affect the other player.
- Memory mapping access for network clients is read-only. Existing bridge host ACK remains owned by the separate probe process.
- If multiple processes cannot coexist with isolated profiles or AllStars Gecko settings, stop and report; do not share a profile or weaken patch validation.

## Acceptance criteria

- Synthetic protocol tests cover round trips, exact lengths, unsupported versions/kinds/IDs, maximum frame bounds, nonfinite/out-of-range coordinates, stale/future timestamps, and out-of-order sequences.
- Synthetic two-client integration test starts a localhost server, connects Mario and Luigi, sends sequenced positions, verifies each peer receives the other's presence/XYZ, then verifies peer removal after disconnect.
- A manual two-instance test uses two distinct isolated profiles and Dolphin PIDs, validates both mailboxes, and observes both clients print the other player's changing position and update rate. No stale player remains after disconnect, pause, or scene transition.
- Automated format, workspace tests, workspace build, and exact PAL map validation pass.
- User-visible live relay is reported separately from synthetic tests. No rendering, gameplay writes, NetPlay, progression sync, or custom Dolphin build is introduced.

## Test strategy

Unit-test frame codec and validators with synthetic bytes. Run a loopback integration test in-process using two clients and a temporary localhost port. Then run the two user-side Dolphin instances with separate isolated profiles, same PAL game, and mandatory AllStars patch. Record both PIDs, game identity, mailbox status, positions/update rate, disconnect handling, and any FPS/VPS impact. Until the interactive run is observed, live synchronization remains unverified.
