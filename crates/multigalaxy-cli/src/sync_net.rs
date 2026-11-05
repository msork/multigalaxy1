//! Versioned bounded binary protocol and localhost authoritative position relay.
use crate::gecko_bridge::PositionSnapshot;
use std::collections::HashMap;
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpListener, TcpStream};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{mpsc, Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

const MAGIC: &[u8; 4] = b"MG1N";
const VERSION: u16 = 1;
const HEADER_BYTES: usize = 26;
const MAX_PAYLOAD: usize = 32;
const MAX_AGE_MS: u64 = 2_000;
const PEER_TTL: Duration = Duration::from_secs(1);

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
#[repr(u8)]
pub enum PlayerId {
    Mario = 1,
    Luigi = 2,
}

impl PlayerId {
    pub fn parse(value: &str) -> Result<Self, String> {
        match value.to_ascii_lowercase().as_str() {
            "mario" | "1" => Ok(Self::Mario),
            "luigi" | "2" => Ok(Self::Luigi),
            _ => Err("M4 supports player IDs Mario/1 and Luigi/2".into()),
        }
    }
    fn decode(value: u8) -> Result<Self, String> {
        match value {
            1 => Ok(Self::Mario),
            2 => Ok(Self::Luigi),
            _ => Err(format!("unknown player ID {value}")),
        }
    }
    pub fn name(self) -> &'static str {
        match self {
            Self::Mario => "Mario",
            Self::Luigi => "Luigi",
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub enum Frame {
    Hello {
        player: PlayerId,
        game_id: [u8; 6],
        revision: u8,
        dolphin_pid: u32,
    },
    Position {
        player: PlayerId,
        sequence: u64,
        timestamp_ms: u64,
        counter: u32,
        x: f32,
        y: f32,
        z: f32,
    },
    PeerState {
        player: PlayerId,
        sequence: u64,
        timestamp_ms: u64,
        counter: u32,
        x: f32,
        y: f32,
        z: f32,
    },
    PeerGone {
        player: PlayerId,
    },
    Reject {
        player: PlayerId,
        code: u8,
    },
}

impl Frame {
    fn parts(&self) -> (u8, PlayerId, u64, u64, Vec<u8>) {
        match self {
            Self::Hello {
                player,
                game_id,
                revision,
                dolphin_pid,
            } => {
                let mut payload = game_id.to_vec();
                payload.push(*revision);
                payload.extend(dolphin_pid.to_be_bytes());
                (1, *player, 0, 0, payload)
            }
            Self::Position {
                player,
                sequence,
                timestamp_ms,
                counter,
                x,
                y,
                z,
            } => (
                2,
                *player,
                *sequence,
                *timestamp_ms,
                encode_position(*counter, *x, *y, *z),
            ),
            Self::PeerState {
                player,
                sequence,
                timestamp_ms,
                counter,
                x,
                y,
                z,
            } => (
                3,
                *player,
                *sequence,
                *timestamp_ms,
                encode_position(*counter, *x, *y, *z),
            ),
            Self::PeerGone { player } => (4, *player, 0, now_ms(), Vec::new()),
            Self::Reject { player, code } => (5, *player, 0, now_ms(), vec![*code]),
        }
    }
}

pub fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64
}

pub fn valid_position(x: f32, y: f32, z: f32) -> bool {
    [x, y, z]
        .into_iter()
        .all(|v| v.is_finite() && v.abs() < 10_000_000.0)
}

pub fn fresh_timestamp(timestamp_ms: u64, now: u64) -> bool {
    timestamp_ms <= now.saturating_add(MAX_AGE_MS) && now.saturating_sub(timestamp_ms) <= MAX_AGE_MS
}

fn encode_position(counter: u32, x: f32, y: f32, z: f32) -> Vec<u8> {
    let mut p = Vec::with_capacity(16);
    p.extend(counter.to_be_bytes());
    p.extend(x.to_bits().to_be_bytes());
    p.extend(y.to_bits().to_be_bytes());
    p.extend(z.to_bits().to_be_bytes());
    p
}

pub fn write_frame(mut writer: impl Write, frame: &Frame) -> Result<(), String> {
    let (kind, player, sequence, timestamp_ms, payload) = frame.parts();
    if payload.len() > MAX_PAYLOAD {
        return Err("outgoing protocol frame exceeds payload limit".into());
    }
    if matches!(frame, Frame::Position { x, y, z, .. } | Frame::PeerState { x, y, z, .. } if !valid_position(*x, *y, *z))
    {
        return Err("outgoing protocol position is nonfinite or out of range".into());
    }
    let mut h = Vec::with_capacity(HEADER_BYTES + payload.len());
    h.extend(MAGIC);
    h.extend(VERSION.to_be_bytes());
    h.push(kind);
    h.push(player as u8);
    h.extend(sequence.to_be_bytes());
    h.extend(timestamp_ms.to_be_bytes());
    h.extend((payload.len() as u16).to_be_bytes());
    h.extend(payload);
    writer
        .write_all(&h)
        .map_err(|e| format!("write TCP frame: {e}"))
}

pub fn read_frame(mut reader: impl Read) -> Result<Frame, String> {
    let mut h = [0u8; HEADER_BYTES];
    reader
        .read_exact(&mut h)
        .map_err(|e| format!("read TCP frame header: {e}"))?;
    if &h[0..4] != MAGIC {
        return Err("protocol magic mismatch".into());
    }
    let version = u16::from_be_bytes(h[4..6].try_into().unwrap());
    if version != VERSION {
        return Err(format!("unsupported protocol version {version}"));
    }
    let kind = h[6];
    let player = PlayerId::decode(h[7])?;
    let sequence = u64::from_be_bytes(h[8..16].try_into().unwrap());
    let timestamp_ms = u64::from_be_bytes(h[16..24].try_into().unwrap());
    let payload_len = u16::from_be_bytes(h[24..26].try_into().unwrap()) as usize;
    if payload_len > MAX_PAYLOAD {
        return Err("protocol payload exceeds 32-byte limit".into());
    }
    let mut p = vec![0u8; payload_len];
    reader
        .read_exact(&mut p)
        .map_err(|e| format!("read TCP frame payload: {e}"))?;
    match kind {
        1 if p.len() == 11 && sequence == 0 && timestamp_ms == 0 => Ok(Frame::Hello {
            player,
            game_id: p[..6].try_into().unwrap(),
            revision: p[6],
            dolphin_pid: u32::from_be_bytes(p[7..11].try_into().unwrap()),
        }),
        2 | 3 if p.len() == 16 && sequence != 0 => {
            let counter = u32::from_be_bytes(p[0..4].try_into().unwrap());
            let x = f32::from_bits(u32::from_be_bytes(p[4..8].try_into().unwrap()));
            let y = f32::from_bits(u32::from_be_bytes(p[8..12].try_into().unwrap()));
            let z = f32::from_bits(u32::from_be_bytes(p[12..16].try_into().unwrap()));
            if !valid_position(x, y, z) {
                return Err("position contains nonfinite or out-of-range coordinates".into());
            }
            if kind == 2 {
                Ok(Frame::Position {
                    player,
                    sequence,
                    timestamp_ms,
                    counter,
                    x,
                    y,
                    z,
                })
            } else {
                Ok(Frame::PeerState {
                    player,
                    sequence,
                    timestamp_ms,
                    counter,
                    x,
                    y,
                    z,
                })
            }
        }
        4 if p.is_empty() && sequence == 0 => Ok(Frame::PeerGone { player }),
        5 if p.len() == 1 && sequence == 0 => Ok(Frame::Reject { player, code: p[0] }),
        _ => Err(format!(
            "malformed protocol frame kind={kind} payload_len={payload_len}"
        )),
    }
}

pub fn validate_hello(frame: &Frame) -> Result<(PlayerId, u32), String> {
    match frame {
        Frame::Hello {
            player,
            game_id,
            revision,
            dolphin_pid,
        } => {
            if game_id != b"RMGP01" || *revision != 0 {
                return Err(format!(
                    "unsupported game identity {} revision {revision}",
                    String::from_utf8_lossy(game_id)
                ));
            }
            if *dolphin_pid == 0 {
                return Err("Hello contains an invalid zero Dolphin PID".into());
            }
            Ok((*player, *dolphin_pid))
        }
        _ => Err("first frame must be Hello".into()),
    }
}

pub fn validate_position(
    frame: &Frame,
    expected: PlayerId,
    now: u64,
    previous_sequence: u64,
) -> Result<(), String> {
    match frame {
        Frame::Position {
            player,
            sequence,
            timestamp_ms,
            x,
            y,
            z,
            ..
        } => {
            if *player != expected {
                return Err("position player ID differs from Hello identity".into());
            }
            if *sequence <= previous_sequence {
                return Err("position sequence is duplicate or out of order".into());
            }
            if !fresh_timestamp(*timestamp_ms, now) {
                return Err("position timestamp is stale or too far in the future".into());
            }
            if !valid_position(*x, *y, *z) {
                return Err("position coordinates are invalid".into());
            }
            Ok(())
        }
        _ => Err("client may send only position frames after Hello".into()),
    }
}

#[derive(Clone)]
struct PeerSlot {
    connection: u64,
    dolphin_pid: u32,
    tx: mpsc::SyncSender<Frame>,
    sequence: u64,
    last_seen: Instant,
    visible: bool,
    snapshot: Option<Frame>,
}

type Lobby = Arc<Mutex<HashMap<PlayerId, PeerSlot>>>;
static NEXT_CONNECTION: AtomicU64 = AtomicU64::new(1);

fn enqueue(tx: &mpsc::SyncSender<Frame>, frame: Frame) {
    // Snapshots are replaceable state. A slow reader may lose an intermediate
    // position, but bounded queues prevent memory growth and the receiver will
    // apply its own age/sequence checks.
    let _ = tx.try_send(frame);
}

pub fn run_server(address: SocketAddr) -> Result<(), String> {
    if !address.ip().is_loopback() {
        return Err("M4 TCP server must bind to localhost/loopback".into());
    }
    let listener =
        TcpListener::bind(address).map_err(|e| format!("bind sync server {address}: {e}"))?;
    println!(
        "sync_server=ready address={}",
        listener.local_addr().map_err(|e| e.to_string())?
    );
    serve(listener, Arc::new(AtomicBool::new(false)))
}

fn serve(listener: TcpListener, stopping: Arc<AtomicBool>) -> Result<(), String> {
    listener.set_nonblocking(true).map_err(|e| e.to_string())?;
    let lobby: Lobby = Arc::new(Mutex::new(HashMap::new()));
    while !stopping.load(Ordering::Relaxed) {
        match listener.accept() {
            Ok((stream, peer)) => {
                if let Err(error) = stream.set_nonblocking(false) {
                    eprintln!("sync_client_rejected peer={peer} reason=socket_mode_error:{error}");
                    continue;
                }
                stream.set_nodelay(true).ok();
                let state = lobby.clone();
                thread::spawn(move || {
                    if let Err(error) = handle_server_client(stream, state) {
                        eprintln!("sync_client_closed peer={peer} reason={error}");
                    }
                });
            }
            Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {}
            Err(e) => return Err(format!("accept sync client: {e}")),
        }
        expire_peers(&lobby);
        thread::sleep(Duration::from_millis(20));
    }
    Ok(())
}

fn expire_peers(lobby: &Lobby) {
    let mut state = lobby.lock().unwrap_or_else(|e| e.into_inner());
    let gone: Vec<PlayerId> = state
        .iter_mut()
        .filter_map(|(id, slot)| {
            if slot.visible && slot.last_seen.elapsed() > PEER_TTL {
                slot.visible = false;
                Some(*id)
            } else {
                None
            }
        })
        .collect();
    for id in gone {
        for (other, slot) in state.iter() {
            if *other != id {
                enqueue(&slot.tx, Frame::PeerGone { player: id });
            }
        }
    }
}

fn handle_server_client(mut stream: TcpStream, lobby: Lobby) -> Result<(), String> {
    stream
        .set_read_timeout(Some(Duration::from_secs(5)))
        .map_err(|e| e.to_string())?;
    let hello = read_frame(&mut stream)?;
    let (player, dolphin_pid) = validate_hello(&hello)?;
    let connection = NEXT_CONNECTION.fetch_add(1, Ordering::Relaxed);
    let (tx, rx) = mpsc::sync_channel::<Frame>(8);
    {
        let mut state = lobby.lock().unwrap_or_else(|e| e.into_inner());
        if state.len() >= 2
            || state.contains_key(&player)
            || state.values().any(|slot| slot.dolphin_pid == dolphin_pid)
        {
            let code = if state.contains_key(&player) {
                1
            } else if state.values().any(|slot| slot.dolphin_pid == dolphin_pid) {
                3
            } else {
                2
            };
            let _ = write_frame(&mut stream, &Frame::Reject { player, code });
            return Err(match code {
                1 => "player ID is already connected",
                2 => "two-player lobby is full",
                _ => "both clients attached to the same Dolphin PID",
            }
            .into());
        }
        for slot in state.values() {
            if slot.visible {
                if let Some(snapshot) = &slot.snapshot {
                    enqueue(&tx, snapshot.clone());
                }
            }
        }
        state.insert(
            player,
            PeerSlot {
                connection,
                dolphin_pid,
                tx: tx.clone(),
                sequence: 0,
                last_seen: Instant::now(),
                visible: false,
                snapshot: None,
            },
        );
    }
    let writer = stream.try_clone().map_err(|e| e.to_string())?;
    thread::spawn(move || {
        let mut writer = writer;
        while let Ok(frame) = rx.recv() {
            if write_frame(&mut writer, &frame).is_err() {
                break;
            }
        }
    });
    println!("sync_server=player_joined id={}", player.name());
    stream
        .set_read_timeout(Some(Duration::from_secs(30)))
        .map_err(|e| e.to_string())?;
    let result = loop {
        match read_frame(&mut stream) {
            Ok(frame) => {
                let previous = lobby
                    .lock()
                    .unwrap_or_else(|e| e.into_inner())
                    .get(&player)
                    .map_or(0, |p| p.sequence);
                if let Err(error) = validate_position(&frame, player, now_ms(), previous) {
                    break Err(error);
                }
                let snapshot = match frame {
                    Frame::Position {
                        player,
                        sequence,
                        timestamp_ms,
                        counter,
                        x,
                        y,
                        z,
                    } => Frame::PeerState {
                        player,
                        sequence,
                        timestamp_ms,
                        counter,
                        x,
                        y,
                        z,
                    },
                    _ => unreachable!(),
                };
                let mut state = lobby.lock().unwrap_or_else(|e| e.into_inner());
                if let Some(slot) = state.get_mut(&player) {
                    if let Frame::PeerState { sequence, .. } = snapshot {
                        slot.sequence = sequence;
                    }
                    slot.last_seen = Instant::now();
                    slot.visible = true;
                    slot.snapshot = Some(snapshot.clone());
                    for (other, recipient) in state.iter() {
                        if *other != player {
                            enqueue(&recipient.tx, snapshot.clone());
                        }
                    }
                }
            }
            Err(error) => break Err(error),
        }
    };
    {
        let mut state = lobby.lock().unwrap_or_else(|e| e.into_inner());
        if state
            .get(&player)
            .is_some_and(|slot| slot.connection == connection)
        {
            state.remove(&player);
            for slot in state.values() {
                enqueue(&slot.tx, Frame::PeerGone { player });
            }
        }
    }
    result
}

pub fn connect_local(address: SocketAddr) -> Result<TcpStream, String> {
    if !address.ip().is_loopback() {
        return Err("M4 clients may connect only to localhost/loopback".into());
    }
    let stream = TcpStream::connect_timeout(&address, Duration::from_secs(2))
        .map_err(|e| format!("connect sync server {address}: {e}"))?;
    stream.set_nodelay(true).map_err(|e| e.to_string())?;
    Ok(stream)
}

pub fn snapshot_frame(player: PlayerId, sequence: u64, sample: PositionSnapshot) -> Frame {
    Frame::Position {
        player,
        sequence,
        timestamp_ms: now_ms(),
        counter: sample.counter,
        x: sample.x,
        y: sample.y,
        z: sample.z,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Cursor;

    fn sample(player: PlayerId, sequence: u64) -> Frame {
        Frame::Position {
            player,
            sequence,
            timestamp_ms: now_ms(),
            counter: sequence as u32,
            x: 1.25,
            y: -2.5,
            z: 3.75,
        }
    }

    #[test]
    fn binary_frames_round_trip_with_exact_big_endian_identity_and_position() {
        for frame in [
            Frame::Hello {
                player: PlayerId::Mario,
                game_id: *b"RMGP01",
                revision: 0,
                dolphin_pid: 101,
            },
            sample(PlayerId::Luigi, 42),
            Frame::PeerGone {
                player: PlayerId::Mario,
            },
        ] {
            let mut bytes = Vec::new();
            write_frame(&mut bytes, &frame).unwrap();
            assert_eq!(read_frame(Cursor::new(bytes)).unwrap(), frame);
        }
    }

    #[test]
    fn rejects_bad_headers_lengths_versions_ids_and_nonfinite_positions() {
        let mut bytes = Vec::new();
        write_frame(&mut bytes, &sample(PlayerId::Mario, 1)).unwrap();
        let mut bad = bytes.clone();
        bad[0] ^= 0xff;
        assert!(read_frame(Cursor::new(bad)).unwrap_err().contains("magic"));
        let mut bad = bytes.clone();
        bad[5] = 2;
        assert!(read_frame(Cursor::new(bad))
            .unwrap_err()
            .contains("version"));
        let mut bad = bytes.clone();
        bad[7] = 9;
        assert!(read_frame(Cursor::new(bad))
            .unwrap_err()
            .contains("player ID"));
        let mut bad = bytes.clone();
        bad[25] = 33;
        assert!(read_frame(Cursor::new(bad))
            .unwrap_err()
            .contains("32-byte"));
        let mut bad = bytes;
        bad[30..34].copy_from_slice(&f32::NAN.to_bits().to_be_bytes());
        assert!(read_frame(Cursor::new(bad))
            .unwrap_err()
            .contains("nonfinite"));
    }

    #[test]
    fn rejects_wrong_game_bad_order_stale_future_and_out_of_range_positions() {
        let mut hello = Vec::new();
        write_frame(
            &mut hello,
            &Frame::Hello {
                player: PlayerId::Mario,
                game_id: *b"RMGE01",
                revision: 0,
                dolphin_pid: 101,
            },
        )
        .unwrap();
        assert!(validate_hello(&read_frame(Cursor::new(hello)).unwrap()).is_err());
        assert!(validate_hello(&Frame::Hello {
            player: PlayerId::Mario,
            game_id: *b"RMGP01",
            revision: 0,
            dolphin_pid: 0,
        })
        .unwrap_err()
        .contains("zero Dolphin PID"));
        let now = now_ms();
        let fresh = sample(PlayerId::Mario, 3);
        assert!(validate_position(&fresh, PlayerId::Mario, now, 2).is_ok());
        assert!(validate_position(&fresh, PlayerId::Luigi, now, 0).is_err());
        assert!(validate_position(&fresh, PlayerId::Mario, now, 3).is_err());
        assert!(!fresh_timestamp(now.saturating_sub(3_000), now));
        assert!(!fresh_timestamp(now + 3_000, now));
        assert!(!valid_position(f32::INFINITY, 0.0, 0.0));
        assert!(!valid_position(0.0, 10_000_000.0, 0.0));
    }

    #[test]
    fn loopback_two_client_relay_expires_and_removes_disconnected_peer() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let stop = Arc::new(AtomicBool::new(false));
        let server_stop = stop.clone();
        let server = thread::spawn(move || serve(listener, server_stop).unwrap());
        let mut mario = TcpStream::connect(address).unwrap();
        let mut luigi = TcpStream::connect(address).unwrap();
        for (stream, id) in [(&mut mario, PlayerId::Mario), (&mut luigi, PlayerId::Luigi)] {
            stream
                .set_read_timeout(Some(Duration::from_secs(2)))
                .unwrap();
            write_frame(
                stream,
                &Frame::Hello {
                    player: id,
                    game_id: *b"RMGP01",
                    revision: 0,
                    dolphin_pid: if id == PlayerId::Mario { 101 } else { 202 },
                },
            )
            .unwrap();
        }
        write_frame(&mut mario, &sample(PlayerId::Mario, 1)).unwrap();
        let received = read_frame(&mut luigi).unwrap();
        assert!(
            matches!(received, Frame::PeerState { player: PlayerId::Mario, x, .. } if x == 1.25)
        );
        write_frame(&mut luigi, &sample(PlayerId::Luigi, 1)).unwrap();
        let received = read_frame(&mut mario).unwrap();
        assert!(
            matches!(received, Frame::PeerState { player: PlayerId::Luigi, y, .. } if y == -2.5)
        );
        drop(mario);
        let gone = read_frame(&mut luigi).unwrap();
        assert!(matches!(
            gone,
            Frame::PeerGone {
                player: PlayerId::Mario
            }
        ));
        let mut mario_reconnected = TcpStream::connect(address).unwrap();
        mario_reconnected
            .set_read_timeout(Some(Duration::from_secs(2)))
            .unwrap();
        write_frame(
            &mut mario_reconnected,
            &Frame::Hello {
                player: PlayerId::Mario,
                game_id: *b"RMGP01",
                revision: 0,
                dolphin_pid: 303,
            },
        )
        .unwrap();
        write_frame(&mut mario_reconnected, &sample(PlayerId::Mario, 1)).unwrap();
        let rejoined = read_frame(&mut luigi).unwrap();
        assert!(matches!(
            rejoined,
            Frame::PeerState {
                player: PlayerId::Mario,
                sequence: 1,
                ..
            }
        ));
        drop(mario_reconnected);
        let _ = read_frame(&mut luigi).unwrap();
        stop.store(true, Ordering::Relaxed);
        server.join().unwrap();
    }

    #[test]
    fn server_rejects_two_player_ids_attached_to_the_same_dolphin_pid() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let stop = Arc::new(AtomicBool::new(false));
        let server_stop = stop.clone();
        let server = thread::spawn(move || serve(listener, server_stop).unwrap());
        let mut mario = TcpStream::connect(address).unwrap();
        write_frame(
            &mut mario,
            &Frame::Hello {
                player: PlayerId::Mario,
                game_id: *b"RMGP01",
                revision: 0,
                dolphin_pid: 77,
            },
        )
        .unwrap();
        let mut luigi = TcpStream::connect(address).unwrap();
        luigi
            .set_read_timeout(Some(Duration::from_secs(2)))
            .unwrap();
        write_frame(
            &mut luigi,
            &Frame::Hello {
                player: PlayerId::Luigi,
                game_id: *b"RMGP01",
                revision: 0,
                dolphin_pid: 77,
            },
        )
        .unwrap();
        assert!(matches!(
            read_frame(&mut luigi).unwrap(),
            Frame::Reject { code: 3, .. }
        ));
        drop(mario);
        stop.store(true, Ordering::Relaxed);
        server.join().unwrap();
    }

    #[test]
    fn stale_server_presence_emits_peer_gone_without_dropping_connection_slot() {
        let (tx_mario, _rx_mario) = mpsc::sync_channel(8);
        let (tx_luigi, rx_luigi) = mpsc::sync_channel(8);
        let lobby = Arc::new(Mutex::new(HashMap::from([
            (
                PlayerId::Mario,
                PeerSlot {
                    connection: 1,
                    dolphin_pid: 101,
                    tx: tx_mario,
                    sequence: 5,
                    last_seen: Instant::now() - Duration::from_secs(2),
                    visible: true,
                    snapshot: Some(sample(PlayerId::Mario, 5)),
                },
            ),
            (
                PlayerId::Luigi,
                PeerSlot {
                    connection: 2,
                    dolphin_pid: 202,
                    tx: tx_luigi,
                    sequence: 3,
                    last_seen: Instant::now(),
                    visible: true,
                    snapshot: Some(sample(PlayerId::Luigi, 3)),
                },
            ),
        ])));
        expire_peers(&lobby);
        assert!(matches!(
            rx_luigi.try_recv().unwrap(),
            Frame::PeerGone {
                player: PlayerId::Mario
            }
        ));
        let state = lobby.lock().unwrap();
        assert_eq!(state.len(), 2);
        assert!(!state.get(&PlayerId::Mario).unwrap().visible);
    }
}
