//! Read-only GDB Remote Serial Protocol access for Dolphin's optional PowerPC stub.
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::time::{Duration, Instant};

const MAX_READ: usize = 4096;

/// Return half-open runs of differing bytes, including changes at either edge.
pub fn diff_runs(expected: &[u8], observed: &[u8]) -> Vec<(usize, usize)> {
    let mut runs = Vec::new();
    let common_len = expected.len().min(observed.len());
    let mut i = 0;
    while i < common_len {
        if expected[i] == observed[i] {
            i += 1;
            continue;
        }
        let start = i;
        while i < common_len && expected[i] != observed[i] {
            i += 1;
        }
        runs.push((start, i - start));
    }
    if expected.len() != observed.len() {
        runs.push((common_len, expected.len().max(observed.len()) - common_len));
    }
    runs
}

pub struct DolphinGdb {
    stream: TcpStream,
    request_timeout: Duration,
}

impl DolphinGdb {
    pub fn connect(address: SocketAddr, timeout: Duration) -> Result<Self, String> {
        let stream = TcpStream::connect_timeout(&address, timeout)
            .map_err(|e| format!("cannot connect to Dolphin GDB stub at {address}: {e}"))?;
        stream
            .set_read_timeout(Some(timeout))
            .and_then(|_| stream.set_write_timeout(Some(timeout)))
            .map_err(|e| format!("cannot configure GDB socket timeouts: {e}"))?;
        Ok(Self {
            stream,
            request_timeout: timeout,
        })
    }

    /// Resume a CPU paused by Dolphin's GDB startup path. Dolphin acknowledges
    /// the `c` packet but intentionally sends no stop reply until a breakpoint.
    /// Keep this same socket open for subsequent memory reads while it runs.
    pub fn continue_execution(&mut self) -> Result<(), String> {
        self.stream
            .write_all(&encode_packet("c"))
            .map_err(|e| format!("cannot send GDB continue request: {e}"))?;
        let ack = read_byte_until(&mut self.stream, Instant::now() + self.request_timeout)?;
        if ack != b'+' {
            return Err(format!(
                "expected GDB acknowledgement for continue, received 0x{ack:02x}"
            ));
        }
        Ok(())
    }

    /// Read emulated guest RAM. This client intentionally exposes no write operation.
    pub fn read_memory(&mut self, address: u32, length: usize) -> Result<Vec<u8>, String> {
        if length == 0 || length > MAX_READ {
            return Err(format!(
                "read length must be between 1 and {MAX_READ} bytes"
            ));
        }
        if u64::from(address) + length as u64 > (u64::from(u32::MAX) + 1) {
            return Err("memory read crosses the 32-bit address-space boundary".into());
        }
        let response = self
            .request(&format!("m{address:x},{length:x}"))
            .map_err(|e| format!("guest read 0x{address:08x}+0x{length:x} failed: {e}"))?;
        // RSP error replies are the three-character form Ehh. A valid memory
        // response is hexadecimal data and can itself begin with byte 0xE*.
        if response.len() == 3
            && response.starts_with('E')
            && response.as_bytes()[1..].iter().all(u8::is_ascii_hexdigit)
        {
            return Err(format!("Dolphin rejected guest memory read: {response}"));
        }
        let bytes = decode_hex(&response)?;
        if bytes.len() != length {
            return Err(format!(
                "Dolphin returned {} bytes for a {length}-byte memory read",
                bytes.len()
            ));
        }
        Ok(bytes)
    }

    fn request(&mut self, command: &str) -> Result<String, String> {
        let started = Instant::now();
        let deadline = started + self.request_timeout;
        let packet = encode_packet(command);
        self.stream
            .write_all(&packet)
            .map_err(|e| format!("cannot send GDB request: {e}"))?;
        let ack = read_byte_until(&mut self.stream, deadline)?;
        if ack != b'+' {
            return Err(format!(
                "expected GDB packet acknowledgement, received 0x{ack:02x}"
            ));
        }
        let mut start = read_byte_until(&mut self.stream, deadline)?;
        while start != b'$' {
            start = read_byte_until(&mut self.stream, deadline)?;
        }
        let mut payload = Vec::new();
        loop {
            let byte = read_byte_until(&mut self.stream, deadline)?;
            if byte == b'#' {
                break;
            }
            if payload.len() >= 16 * MAX_READ {
                return Err("GDB response exceeds the bounded packet size".into());
            }
            payload.push(byte);
        }
        let high = read_byte_until(&mut self.stream, deadline)?;
        let low = read_byte_until(&mut self.stream, deadline)?;
        let expected = (hex_nibble(high)? << 4) | hex_nibble(low)?;
        let actual = payload
            .iter()
            .fold(0u8, |sum, byte| sum.wrapping_add(*byte));
        if expected != actual {
            self.stream
                .write_all(b"-")
                .map_err(|e| format!("cannot reject invalid GDB packet: {e}"))?;
            return Err("Dolphin GDB response checksum mismatch".into());
        }
        self.stream
            .write_all(b"+")
            .map_err(|e| format!("cannot acknowledge GDB response: {e}"))?;
        String::from_utf8(payload).map_err(|e| format!("GDB response is not ASCII: {e}"))
    }
}

#[cfg(test)]
fn read_byte(stream: &mut TcpStream) -> Result<u8, String> {
    let mut byte = [0];
    stream
        .read_exact(&mut byte)
        .map_err(|e| format!("cannot read GDB packet: {e}"))?;
    Ok(byte[0])
}

fn read_byte_until(stream: &mut TcpStream, deadline: Instant) -> Result<u8, String> {
    let remaining = deadline.saturating_duration_since(Instant::now());
    if remaining.is_zero() {
        return Err("GDB request exceeded its total response timeout".into());
    }
    stream
        .set_read_timeout(Some(remaining))
        .map_err(|e| format!("cannot set GDB response timeout: {e}"))?;
    let mut byte = [0];
    stream.read_exact(&mut byte).map_err(|e| {
        if matches!(
            e.kind(),
            std::io::ErrorKind::TimedOut | std::io::ErrorKind::WouldBlock
        ) {
            format!(
                "GDB response timed out ({} ms remained): {e}",
                remaining.as_millis()
            )
        } else {
            format!("cannot read GDB response: {e}")
        }
    })?;
    Ok(byte[0])
}

fn encode_packet(payload: &str) -> Vec<u8> {
    let checksum = payload
        .as_bytes()
        .iter()
        .fold(0u8, |sum, byte| sum.wrapping_add(*byte));
    format!("${payload}#{checksum:02x}").into_bytes()
}

fn decode_hex(text: &str) -> Result<Vec<u8>, String> {
    if text.len() % 2 != 0 {
        return Err("Dolphin returned an odd-length hexadecimal memory response".into());
    }
    text.as_bytes()
        .chunks_exact(2)
        .map(|pair| Ok((hex_nibble(pair[0])? << 4) | hex_nibble(pair[1])?))
        .collect()
}

fn hex_nibble(byte: u8) -> Result<u8, String> {
    match byte {
        b'0'..=b'9' => Ok(byte - b'0'),
        b'a'..=b'f' => Ok(byte - b'a' + 10),
        b'A'..=b'F' => Ok(byte - b'A' + 10),
        _ => Err(format!(
            "invalid hexadecimal digit 0x{byte:02x} in GDB response"
        )),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::net::TcpListener;
    use std::thread;

    #[test]
    fn reads_memory_using_gdb_rsp_without_write_commands() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let server = thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut packet = Vec::new();
            loop {
                let byte = read_byte(&mut stream).unwrap();
                packet.push(byte);
                if byte == b'#' {
                    packet.push(read_byte(&mut stream).unwrap());
                    packet.push(read_byte(&mut stream).unwrap());
                    break;
                }
            }
            assert_eq!(packet, encode_packet("m80004000,4"));
            stream.write_all(b"+").unwrap();
            stream.write_all(&encode_packet("3f800000")).unwrap();
            assert_eq!(read_byte(&mut stream).unwrap(), b'+');
        });
        let mut client = DolphinGdb::connect(address, Duration::from_secs(2)).unwrap();
        assert_eq!(
            client.read_memory(0x8000_4000, 4).unwrap(),
            [0x3f, 0x80, 0, 0]
        );
        server.join().unwrap();
    }

    #[test]
    fn continue_ack_is_followed_by_memory_reads_on_same_session() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let server = thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut packet = Vec::new();
            loop {
                let byte = read_byte(&mut stream).unwrap();
                packet.push(byte);
                if byte == b'#' {
                    packet.push(read_byte(&mut stream).unwrap());
                    packet.push(read_byte(&mut stream).unwrap());
                    break;
                }
            }
            assert_eq!(packet, encode_packet("c"));
            stream.write_all(b"+").unwrap();

            packet.clear();
            loop {
                let byte = read_byte(&mut stream).unwrap();
                packet.push(byte);
                if byte == b'#' {
                    packet.push(read_byte(&mut stream).unwrap());
                    packet.push(read_byte(&mut stream).unwrap());
                    break;
                }
            }
            assert_eq!(packet, encode_packet("m80004000,4"));
            stream.write_all(b"+").unwrap();
            stream.write_all(&encode_packet("3f800000")).unwrap();
            assert_eq!(read_byte(&mut stream).unwrap(), b'+');
        });

        let mut client = DolphinGdb::connect(address, Duration::from_secs(2)).unwrap();
        client.continue_execution().unwrap();
        assert_eq!(
            client.read_memory(0x8000_4000, 4).unwrap(),
            [0x3f, 0x80, 0, 0]
        );
        server.join().unwrap();
    }

    #[test]
    fn accepts_memory_payload_whose_first_byte_is_e() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let server = thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut packet = Vec::new();
            loop {
                let byte = read_byte(&mut stream).unwrap();
                packet.push(byte);
                if byte == b'#' {
                    packet.push(read_byte(&mut stream).unwrap());
                    packet.push(read_byte(&mut stream).unwrap());
                    break;
                }
            }
            stream.write_all(b"+").unwrap();
            stream.write_all(&encode_packet("ec02002a")).unwrap();
            assert_eq!(read_byte(&mut stream).unwrap(), b'+');
        });
        let mut client = DolphinGdb::connect(address, Duration::from_secs(2)).unwrap();
        assert_eq!(
            client.read_memory(0x8000_0000, 4).unwrap(),
            [0xec, 0x02, 0, 0x2a]
        );
        server.join().unwrap();
    }

    #[test]
    fn rejects_invalid_or_unbounded_reads_before_network_io() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let mut client =
            DolphinGdb::connect(listener.local_addr().unwrap(), Duration::from_secs(1)).unwrap();
        assert!(client.read_memory(0, 0).is_err());
        assert!(client.read_memory(0, MAX_READ + 1).is_err());
        assert!(client.read_memory(u32::MAX, 2).is_err());
    }

    #[test]
    fn bounds_total_gdb_response_time() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let server = thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut byte = [0];
            while stream.read_exact(&mut byte).is_ok() {
                if byte[0] == b'#' {
                    let _ = stream.read_exact(&mut byte);
                    let _ = stream.read_exact(&mut byte);
                    break;
                }
            }
            stream.write_all(b"+").unwrap();
            thread::sleep(Duration::from_millis(150));
            let _ = stream.write_all(&encode_packet("01020304"));
        });
        let mut client = DolphinGdb::connect(address, Duration::from_millis(40)).unwrap();
        let error = client.read_memory(0x8000_0000, 4).unwrap_err();
        assert!(error.contains("timed out"), "{error}");
        server.join().unwrap();
    }

    #[test]
    fn diff_runs_reports_all_mismatch_spans_and_length_difference() {
        assert_eq!(
            diff_runs(&[1, 8, 3, 4, 9], &[1, 2, 3, 7, 8]),
            vec![(1, 1), (3, 2)]
        );
        assert_eq!(diff_runs(&[1, 2], &[1, 2, 3]), vec![(2, 1)]);
        assert!(diff_runs(&[1, 2], &[1, 2]).is_empty());
    }
}
