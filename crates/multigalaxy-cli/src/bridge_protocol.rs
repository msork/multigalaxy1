//! Application payload carried inside the versioned shared snapshot ABI.

use std::time::{Duration, Instant};

pub const PAYLOAD_SCHEMA: u32 = 1;
pub const PAYLOAD_SIZE: usize = 48;
pub const GAME_ID: [u8; 6] = *b"RMGP01";
pub const REVISION: u32 = 0;
pub const FLAG_IDENTITY_VALID: u32 = 1;
pub const FLAG_RAM_SAMPLE_VALID: u32 = 2;
pub const MEM1_SAMPLE_ADDRESS: u32 = 0x8000_0000;
pub const HEARTBEAT_TIMEOUT: Duration = Duration::from_millis(500);

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GameSnapshot {
    pub game_id: [u8; 6],
    pub revision: u32,
    pub flags: u32,
    pub frame: u64,
    pub heartbeat: u64,
    pub ram_sample_address: u32,
    pub ram_sample_value: u32,
}

impl GameSnapshot {
    pub fn decode(bytes: &[u8]) -> Result<Self, String> {
        if bytes.len() != PAYLOAD_SIZE {
            return Err(format!(
                "bridge payload has {} bytes; expected {PAYLOAD_SIZE}",
                bytes.len()
            ));
        }
        let schema = read_u32(bytes, 0);
        if schema != PAYLOAD_SCHEMA {
            return Err(format!(
                "unsupported game snapshot schema {schema}; expected {PAYLOAD_SCHEMA}"
            ));
        }
        let mut game_id = [0; 6];
        game_id.copy_from_slice(&bytes[8..14]);
        let snapshot = Self {
            game_id,
            revision: read_u32(bytes, 4),
            flags: read_u32(bytes, 16),
            frame: read_u64(bytes, 24),
            heartbeat: read_u64(bytes, 32),
            ram_sample_address: read_u32(bytes, 40),
            ram_sample_value: read_u32(bytes, 44),
        };
        if snapshot.game_id != GAME_ID || snapshot.revision != REVISION {
            return Err(format!(
                "bridge identity mismatch: game {:?} revision {}; expected RMGP01 revision 0",
                String::from_utf8_lossy(&snapshot.game_id),
                snapshot.revision
            ));
        }
        if snapshot.flags & FLAG_IDENTITY_VALID == 0 {
            return Err("bridge publisher did not validate game identity".into());
        }
        if snapshot.flags & !(FLAG_IDENTITY_VALID | FLAG_RAM_SAMPLE_VALID) != 0 {
            return Err("bridge snapshot contains unknown flags".into());
        }
        if snapshot.heartbeat == 0 || snapshot.frame != snapshot.heartbeat {
            return Err("bridge frame and heartbeat counters are invalid".into());
        }
        if snapshot.flags & FLAG_RAM_SAMPLE_VALID != 0
            && snapshot.ram_sample_address != MEM1_SAMPLE_ADDRESS
        {
            return Err(format!(
                "bridge RAM sample address {:#010x} is outside the approved test field",
                snapshot.ram_sample_address
            ));
        }
        Ok(snapshot)
    }

    pub fn is_stale(last_heartbeat_change: Instant, now: Instant) -> bool {
        now.saturating_duration_since(last_heartbeat_change) > HEARTBEAT_TIMEOUT
    }
}

fn read_u32(bytes: &[u8], offset: usize) -> u32 {
    u32::from_le_bytes(bytes[offset..offset + 4].try_into().unwrap())
}

fn read_u64(bytes: &[u8], offset: usize) -> u64 {
    u64::from_le_bytes(bytes[offset..offset + 8].try_into().unwrap())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture() -> Vec<u8> {
        let mut bytes = vec![0; PAYLOAD_SIZE];
        bytes[0..4].copy_from_slice(&PAYLOAD_SCHEMA.to_le_bytes());
        bytes[4..8].copy_from_slice(&REVISION.to_le_bytes());
        bytes[8..14].copy_from_slice(&GAME_ID);
        bytes[16..20].copy_from_slice(&(FLAG_IDENTITY_VALID | FLAG_RAM_SAMPLE_VALID).to_le_bytes());
        bytes[24..32].copy_from_slice(&7u64.to_le_bytes());
        bytes[32..40].copy_from_slice(&7u64.to_le_bytes());
        bytes[40..44].copy_from_slice(&MEM1_SAMPLE_ADDRESS.to_le_bytes());
        bytes[44..48].copy_from_slice(&0x1234_5678u32.to_le_bytes());
        bytes
    }

    #[test]
    fn decodes_only_expected_pal_identity_and_bounded_sample() {
        let decoded = GameSnapshot::decode(&fixture()).unwrap();
        assert_eq!(decoded.game_id, GAME_ID);
        assert_eq!(decoded.revision, 0);
        assert_eq!(decoded.frame, 7);
        assert!(decoded.flags & FLAG_RAM_SAMPLE_VALID != 0);
    }

    #[test]
    fn rejects_wrong_schema_identity_length_and_ram_address() {
        let mut bytes = fixture();
        bytes[0] = 2;
        assert!(GameSnapshot::decode(&bytes).unwrap_err().contains("schema"));
        bytes = fixture();
        bytes[8] = b'X';
        assert!(GameSnapshot::decode(&bytes)
            .unwrap_err()
            .contains("identity"));
        assert!(GameSnapshot::decode(&fixture()[..PAYLOAD_SIZE - 1]).is_err());
        bytes = fixture();
        bytes[40..44].copy_from_slice(&0x8123_4567u32.to_le_bytes());
        assert!(GameSnapshot::decode(&bytes)
            .unwrap_err()
            .contains("address"));
    }

    #[test]
    fn stale_heartbeat_uses_host_monotonic_time() {
        let mark = Instant::now();
        assert!(!GameSnapshot::is_stale(
            mark,
            mark + Duration::from_millis(100)
        ));
        assert!(GameSnapshot::is_stale(
            mark,
            mark + Duration::from_millis(501)
        ));
    }
}
