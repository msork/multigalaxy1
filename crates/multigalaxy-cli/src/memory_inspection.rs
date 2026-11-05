//! Bounded, read-only access to explicitly requested canonical Wii MEM1 ranges.

pub const GUEST_MEM1_BASE: u32 = 0x8000_0000;
/// Dolphin guarantees retail Wii MEM1 has at least 24 MiB. Restrict reads to
/// this lower bound so the tool never crosses into Dolphin's following regions.
pub const GUEST_MEM1_MIN_SIZE: u32 = 0x0180_0000;
pub const GUEST_MEM1_LIMIT: u32 = GUEST_MEM1_BASE + GUEST_MEM1_MIN_SIZE;
pub const MAX_READ_BYTES: usize = 0x2000;
pub const WINDOWS_ALLOCATION_GRANULARITY: u64 = 0x1_0000;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ValueFormat {
    Bytes,
    U32,
    Pointer,
    F32,
}

impl ValueFormat {
    pub fn parse(value: &str) -> Result<Self, String> {
        match value {
            "bytes" => Ok(Self::Bytes),
            "u32" => Ok(Self::U32),
            "pointer" => Ok(Self::Pointer),
            "f32" => Ok(Self::F32),
            _ => Err("format must be bytes, u32, pointer, or f32".into()),
        }
    }
}

pub fn validate_typed_request(
    address: u32,
    length: usize,
    format: ValueFormat,
) -> Result<(), String> {
    plan_read(address, length)?;
    if format != ValueFormat::Bytes && length != 4 {
        return Err("typed u32, pointer, and f32 requests must be exactly four bytes".into());
    }
    if format != ValueFormat::Bytes && address & 3 != 0 {
        return Err("typed u32, pointer, and f32 requests require 4-byte alignment".into());
    }
    Ok(())
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ReadPlan {
    pub guest_address: u32,
    pub length: usize,
    pub mapping_offset: u64,
    pub mapped_length: usize,
    pub data_offset: usize,
}

pub fn plan_read(guest_address: u32, length: usize) -> Result<ReadPlan, String> {
    if length == 0 {
        return Err("read length must be nonzero".into());
    }
    if length > MAX_READ_BYTES {
        return Err(format!(
            "read length {length} exceeds maximum {MAX_READ_BYTES}"
        ));
    }
    let start = u64::from(guest_address);
    let end = start
        .checked_add(u64::try_from(length).map_err(|_| "read length overflow")?)
        .ok_or("guest address range overflow")?;
    if start < u64::from(GUEST_MEM1_BASE) || end > u64::from(GUEST_MEM1_LIMIT) {
        return Err(format!(
            "guest range 0x{guest_address:08x}+0x{length:x} is outside canonical retail MEM1"
        ));
    }
    let shared_offset = start - u64::from(GUEST_MEM1_BASE);
    let mapping_offset = shared_offset & !(WINDOWS_ALLOCATION_GRANULARITY - 1);
    let data_offset = usize::try_from(shared_offset - mapping_offset)
        .map_err(|_| "mapped view offset overflow")?;
    let mapped_length = data_offset
        .checked_add(length)
        .ok_or("mapped view length overflow")?;
    Ok(ReadPlan {
        guest_address,
        length,
        mapping_offset,
        mapped_length,
        data_offset,
    })
}

pub trait ReadOnlyMappedMemory {
    /// Read only the requested subrange from a bounded, allocation-aligned view.
    fn read_mapped_window(
        &self,
        mapping_offset: u64,
        mapped_length: usize,
        data_offset: usize,
        length: usize,
    ) -> Result<Vec<u8>, String>;
}

pub fn read_explicit_range(
    memory: &impl ReadOnlyMappedMemory,
    guest_address: u32,
    length: usize,
) -> Result<Vec<u8>, String> {
    let plan = plan_read(guest_address, length)?;
    let bytes = memory.read_mapped_window(
        plan.mapping_offset,
        plan.mapped_length,
        plan.data_offset,
        plan.length,
    )?;
    if bytes.len() != plan.length {
        return Err(format!(
            "mapping returned {} bytes for requested {} byte range",
            bytes.len(),
            plan.length
        ));
    }
    Ok(bytes)
}

pub fn decode_be_u32(address: u32, bytes: &[u8]) -> Result<u32, String> {
    if address & 3 != 0 {
        return Err("u32 reads require a 4-byte-aligned guest address".into());
    }
    let word: [u8; 4] = bytes
        .try_into()
        .map_err(|_| "u32 reads require exactly four bytes")?;
    Ok(u32::from_be_bytes(word))
}

pub fn decode_be_pointer(address: u32, bytes: &[u8]) -> Result<Option<u32>, String> {
    let pointer = decode_be_u32(address, bytes)?;
    if pointer == 0 {
        return Ok(None);
    }
    if pointer & 3 != 0 || pointer < GUEST_MEM1_BASE || pointer >= GUEST_MEM1_LIMIT {
        return Err(format!("invalid canonical MEM1 pointer 0x{pointer:08x}"));
    }
    Ok(Some(pointer))
}

pub fn decode_be_f32(address: u32, bytes: &[u8]) -> Result<f32, String> {
    let value = f32::from_bits(decode_be_u32(address, bytes)?);
    if !value.is_finite() {
        return Err(format!("f32 at 0x{address:08x} is not finite"));
    }
    Ok(value)
}

#[cfg(test)]
mod tests {
    use super::*;

    struct Fixture {
        bytes: Vec<u8>,
        fail_mapping: bool,
    }

    impl ReadOnlyMappedMemory for Fixture {
        fn read_mapped_window(
            &self,
            mapping_offset: u64,
            mapped_length: usize,
            data_offset: usize,
            length: usize,
        ) -> Result<Vec<u8>, String> {
            if self.fail_mapping {
                return Err("synthetic MapViewOfFile failure".into());
            }
            let start = usize::try_from(mapping_offset).map_err(|_| "offset overflow")?;
            let requested_start = start.checked_add(data_offset).ok_or("offset overflow")?;
            let requested_end = requested_start
                .checked_add(length)
                .ok_or("length overflow")?;
            let mapped_end = start.checked_add(mapped_length).ok_or("length overflow")?;
            if requested_end > mapped_end || mapped_end > self.bytes.len() {
                return Err("synthetic mapping does not cover requested range".into());
            }
            Ok(self.bytes[requested_start..requested_end].to_vec())
        }
    }

    #[test]
    fn translates_guest_addresses_to_allocation_aligned_shared_offsets() {
        let plan = plan_read(0x806A_1228, 4).unwrap();
        assert_eq!(plan.mapping_offset, 0x006A_0000);
        assert_eq!(plan.data_offset, 0x1228);
        assert_eq!(plan.mapped_length, 0x122C);
        assert_eq!(plan.mapping_offset % WINDOWS_ALLOCATION_GRANULARITY, 0);
    }

    #[test]
    fn rejects_out_of_range_zero_oversized_and_overflowing_requests() {
        assert!(plan_read(GUEST_MEM1_BASE - 1, 1).is_err());
        assert!(plan_read(GUEST_MEM1_LIMIT - 1, 2).is_err());
        assert!(plan_read(GUEST_MEM1_BASE, 0).is_err());
        assert!(plan_read(GUEST_MEM1_BASE, MAX_READ_BYTES + 1).is_err());
        assert!(plan_read(u32::MAX - 1, 8).is_err());
    }

    #[test]
    fn reads_only_requested_bytes_from_a_synthetic_mapping() {
        let mut backing = vec![0; 0x20_000];
        backing[0x1_0010..0x1_0014].copy_from_slice(&0x1234_5678u32.to_be_bytes());
        let fixture = Fixture {
            bytes: backing,
            fail_mapping: false,
        };
        assert_eq!(
            read_explicit_range(&fixture, GUEST_MEM1_BASE + 0x1_0010, 4).unwrap(),
            0x1234_5678u32.to_be_bytes()
        );
    }

    #[test]
    fn mapping_failure_is_reported_without_fallback_or_scan() {
        let fixture = Fixture {
            bytes: vec![0; 0x20_000],
            fail_mapping: true,
        };
        assert!(read_explicit_range(&fixture, GUEST_MEM1_BASE + 0x1_0000, 4)
            .unwrap_err()
            .contains("MapViewOfFile failure"));
    }

    #[test]
    fn typed_reads_decode_big_endian_words_floats_and_pointers() {
        let address = GUEST_MEM1_BASE + 0x100;
        let word = 0x806A_1228u32.to_be_bytes();
        assert_eq!(decode_be_u32(address, &word).unwrap(), 0x806A_1228);
        assert_eq!(
            decode_be_pointer(address, &word).unwrap(),
            Some(0x806A_1228)
        );
        assert_eq!(
            decode_be_pointer(address, &0u32.to_be_bytes()).unwrap(),
            None
        );
        assert_eq!(
            decode_be_f32(address, &12.5f32.to_bits().to_be_bytes()).unwrap(),
            12.5
        );
    }

    #[test]
    fn rejects_misaligned_invalid_pointer_and_nonfinite_typed_values() {
        assert!(decode_be_u32(GUEST_MEM1_BASE + 1, &[0; 4]).is_err());
        assert!(decode_be_pointer(GUEST_MEM1_BASE, &0x8000_0002u32.to_be_bytes()).is_err());
        assert!(decode_be_pointer(GUEST_MEM1_BASE, &0x9000_0000u32.to_be_bytes()).is_err());
        assert!(decode_be_f32(GUEST_MEM1_BASE, &f32::NAN.to_bits().to_be_bytes()).is_err());
    }

    #[test]
    fn typed_requests_require_exact_size_and_alignment() {
        assert!(validate_typed_request(GUEST_MEM1_BASE, 4, ValueFormat::U32).is_ok());
        assert!(validate_typed_request(GUEST_MEM1_BASE, 3, ValueFormat::U32).is_err());
        assert!(validate_typed_request(GUEST_MEM1_BASE + 1, 4, ValueFormat::Pointer).is_err());
        assert!(validate_typed_request(GUEST_MEM1_BASE, 4, ValueFormat::Pointer).is_ok());
    }
}
