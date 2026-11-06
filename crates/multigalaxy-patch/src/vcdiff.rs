//! Minimal pure-Rust VCDIFF (RFC 3284) codec, restricted profile:
//! header magic, single window with a source segment, address cache size 0
//! (all COPY addresses in SELF mode, absolute in the combined source+target
//! address space), ADD/COPY instructions only, no checksums, no secondary
//! compressor. Round-trips through this crate's decoder; deterministic for
//! fixed inputs. Not a full xdelta3 CLI replacement.

pub const VCDIFF_MAGIC: [u8; 4] = [0xD6, 0xC3, 0xC4, 0x00];

const OP_RUN: u8 = 0;
const OP_ADD: u8 = 1;
const OP_COPY: u8 = 2;

fn write_varint(out: &mut Vec<u8>, mut value: u64) {
    loop {
        let mut byte = (value & 0x7F) as u8;
        value >>= 7;
        if value != 0 {
            byte |= 0x80;
        }
        out.push(byte);
        if value == 0 {
            break;
        }
    }
}

fn read_varint(data: &[u8], pos: &mut usize) -> Result<u64, String> {
    let mut value = 0u64;
    let mut shift = 0u32;
    loop {
        let byte = *data.get(*pos).ok_or("truncated VCDIFF varint")?;
        *pos += 1;
        value |= u64::from(byte & 0x7F) << shift;
        if byte & 0x80 == 0 {
            return Ok(value);
        }
        shift += 7;
        if shift > 63 {
            return Err("VCDIFF varint overflow".into());
        }
    }
}

/// Encode `target` as a VCDIFF delta against `source`. Deterministic: identical
/// runs are coalesced greedily; changed bytes become ADD.
pub fn encode(source: &[u8], target: &[u8]) -> Vec<u8> {
    let mut out = VCDIFF_MAGIC.to_vec();
    out.push(0x01); // Win_Indicator: source present
    write_varint(&mut out, source.len() as u64);
    write_varint(&mut out, 0); // source segment position

    let mut instr = Vec::new();
    let mut data = Vec::new();
    let mut i = 0usize;
    while i < target.len() {
        let mut best_len = 0usize;
        let mut best_addr = 0usize;
        if source.len() >= 4 && i + 4 <= target.len() {
            let needle = &target[i..i + 4];
            let mut from = 0usize;
            while from + 4 <= source.len() {
                if &source[from..from + 4] == needle {
                    let mut len = 4usize;
                    while i + len < target.len()
                        && from + len < source.len()
                        && target[i + len] == source[from + len]
                    {
                        len += 1;
                    }
                    if len > best_len {
                        best_len = len;
                        best_addr = from;
                    }
                }
                from += 1;
            }
        }
        if best_len >= 4 {
            instr.push(OP_COPY);
            write_varint(&mut instr, best_len as u64);
            instr.push(0); // mode 0 = SELF (cache size 0)
            write_varint(&mut instr, best_addr as u64);
            i += best_len;
        } else {
            let start = i;
            while i < target.len() {
                if i + 4 <= target.len() && source.len() >= 4 {
                    let needle = &target[i..i + 4];
                    if source.windows(4).any(|w| w == needle) {
                        break;
                    }
                    i += 4;
                } else {
                    i += 1;
                }
            }
            let run = &target[start..i];
            instr.push(OP_ADD);
            write_varint(&mut instr, run.len() as u64);
            data.extend_from_slice(run);
        }
    }

    let mut delta = Vec::new();
    write_varint(&mut delta, target.len() as u64);
    write_varint(&mut delta, instr.len() as u64);
    write_varint(&mut delta, data.len() as u64);
    write_varint(&mut delta, 0); // address cache size 0
    delta.extend_from_slice(&instr);
    delta.extend_from_slice(&data);
    write_varint(&mut out, delta.len() as u64);
    out.extend_from_slice(&delta);
    out
}

/// Decode a VCDIFF stream produced by `encode` (restricted profile).
pub fn decode(source: &[u8], delta: &[u8]) -> Result<Vec<u8>, String> {
    if delta.len() < 4 || delta[0..4] != VCDIFF_MAGIC {
        return Err("not a VCDIFF stream (bad magic)".into());
    }
    let mut pos = 4usize;
    let win_indicator = *delta.get(pos).ok_or("truncated window header")?;
    pos += 1;
    if win_indicator & 0x01 == 0 {
        return Err("restricted profile requires a source segment".into());
    }
    let src_len = read_varint(delta, &mut pos)? as usize;
    let src_pos = read_varint(delta, &mut pos)? as usize;
    if src_pos != 0 || src_len != source.len() {
        return Err("source segment does not match the supplied source".into());
    }
    let delta_len = read_varint(delta, &mut pos)? as usize;
    let delta_end = pos.checked_add(delta_len).ok_or("delta length overflow")?;
    if delta_end > delta.len() {
        return Err("truncated delta body".into());
    }
    let target_len = read_varint(delta, &mut pos)? as usize;
    let instr_len = read_varint(delta, &mut pos)? as usize;
    let data_len = read_varint(delta, &mut pos)? as usize;
    let cache_size = read_varint(delta, &mut pos)?;
    if cache_size != 0 {
        return Err("restricted profile requires address cache size 0".into());
    }
    let instr_start = pos;
    let instr_end = instr_start.checked_add(instr_len).ok_or("instr overflow")?;
    let data_start = instr_end;
    let data_end = data_start.checked_add(data_len).ok_or("data overflow")?;
    if data_end > delta_end {
        return Err("instruction/data sections exceed the delta body".into());
    }
    let instr = &delta[instr_start..instr_end];
    let data = &delta[data_start..data_end];
    let mut out = Vec::with_capacity(target_len);
    let mut ipos = 0usize;
    let mut dpos = 0usize;
    while ipos < instr.len() {
        let op = instr[ipos];
        ipos += 1;
        match op {
            OP_ADD => {
                let size = read_varint(instr, &mut ipos)? as usize;
                if dpos + size > data.len() {
                    return Err("ADD exceeds data section".into());
                }
                out.extend_from_slice(&data[dpos..dpos + size]);
                dpos += size;
            }
            OP_COPY => {
                let size = read_varint(instr, &mut ipos)? as usize;
                let mode = *instr.get(ipos).ok_or("truncated COPY mode")?;
                ipos += 1;
                if mode != 0 {
                    return Err("restricted profile requires COPY mode 0 (SELF)".into());
                }
                let addr = read_varint(instr, &mut ipos)? as usize;
                if addr < source.len() {
                    if addr + size > source.len() {
                        return Err("COPY from source out of range".into());
                    }
                    out.extend_from_slice(&source[addr..addr + size]);
                } else {
                    let taddr = addr - source.len();
                    if taddr + size > out.len() {
                        return Err("COPY from target out of range".into());
                    }
                    let chunk: Vec<u8> = out[taddr..taddr + size].to_vec();
                    out.extend_from_slice(&chunk);
                }
            }
            OP_RUN => return Err("RUN not emitted by the restricted encoder".into()),
            other => return Err(format!("unknown VCDIFF opcode {other}")),
        }
    }
    if out.len() != target_len {
        return Err(format!(
            "decoded length {} != declared target length {target_len}",
            out.len()
        ));
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn round_trips_identical_and_changed_payloads() {
        let source = b"the quick brown fox jumps over the lazy dog".to_vec();
        assert_eq!(decode(&source, &encode(&source, &source)).unwrap(), source);
        let mut target = source.clone();
        target[10..15].copy_from_slice(b"RED  ");
        target.extend_from_slice(b" and more tail bytes");
        let delta = encode(&source, &target);
        assert_eq!(decode(&source, &delta).unwrap(), target);
    }

    #[test]
    fn known_answer_header_and_empty_target() {
        let delta = encode(b"abc", b"");
        assert_eq!(&delta[0..4], &VCDIFF_MAGIC);
        assert_eq!(decode(b"abc", &delta).unwrap(), Vec::<u8>::new());
    }

    #[test]
    fn rejects_bad_magic_truncation_and_wrong_source() {
        assert!(decode(b"x", b"nope").is_err());
        let delta = encode(b"hello world", b"hello there world");
        assert!(decode(b"hello world", &delta[..delta.len() - 1]).is_err());
        assert!(decode(b"different source", &delta).is_err());
    }

    #[test]
    fn encode_is_deterministic() {
        let a = vec![0u8; 100];
        let mut b = a.clone();
        b[50] = 1;
        assert_eq!(encode(&a, &b), encode(&a, &b));
    }
}
