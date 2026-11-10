//! Reversible Gecko mailbox experiment for stock Dolphin.
//!
//! The C0 routine owns only its inline code/data mailbox. It walks the exact
//! PAL RMGP01 Rev 0 Mario pointer chain after checking every pointer and never
//! writes to game objects. The host's only write remains the ACK word.

pub const HANDLER_BASE: u32 = 0x8000_1800;
pub const HANDLER_END: u32 = 0x8000_3000;
/// Verified Dolphin `Sys/codehandler.bin` size used by the Gecko payload layout.
pub const CODEHANDLER_BYTES: usize = 2880;
const ALLSTARS_CAVE_START: u32 = 0x8000_2790;

// Stage 1 trampoline dry-run hook points (docs/references/draw-only-hook-contract.md).
pub const HOOK_ADDRESS: u32 = 0x802B_62E4;
pub const HOOK_RETURN_ADDRESS: u32 = 0x802B_62E8;
pub const ORIGINAL_HOOK_WORD: u32 = 0x4BFF_083D;
const DIRECT_DRAW: u32 = 0x802A_6B20;
const STUB_WORDS: usize = 8;
pub const STUB_WORDS_PUBLIC: usize = STUB_WORDS;
const MAILBOX_MAGIC: u32 = 0x4d47_3150; // "MG1P"
pub const MAILBOX_VERSION: u32 = 1;
const MAILBOX_WORDS: usize = 14;
const MAILBOX_BYTES: u32 = (MAILBOX_WORDS * 4) as u32;
const SNAPSHOT_BYTES: usize = 12 * 4;

// Mailbox words: counter, host ACK, game echo, marker, version, sequence,
// valid, actor address, and X/Y/Z IEEE-754 bits. Mutable fields are masked
// when matching a live handler snapshot.
const COUNTER: usize = 0;
const ACK: usize = 1;
const ECHO: usize = 2;
const MARKER: usize = 3;
const VERSION: usize = 4;
const SEQUENCE: usize = 5;
const VALID: usize = 6;
const ACTOR: usize = 7;
const X: usize = 8;
const Y: usize = 9;
const Z: usize = 10;
const SAVED_LR: usize = 12;

const GAME_SYSTEM_SINGLETON: u32 = 0x806a_1228;

#[derive(Clone, Copy)]
enum FixupKind {
    Branch,
    Conditional { bo: u8, bi: u8 },
}

#[derive(Clone, Copy)]
struct Fixup {
    at: usize,
    label: usize,
    kind: FixupKind,
}

#[derive(Default)]
struct Program {
    words: Vec<u32>,
    labels: Vec<Option<usize>>,
    fixups: Vec<Fixup>,
}

impl Program {
    fn label(&mut self) -> usize {
        let id = self.labels.len();
        self.labels.push(None);
        id
    }
    fn bind(&mut self, label: usize) {
        self.labels[label] = Some(self.words.len());
    }
    fn emit(&mut self, word: u32) {
        self.words.push(word);
    }
    fn b(&mut self, label: usize) {
        let at = self.words.len();
        self.emit(0);
        self.fixups.push(Fixup {
            at,
            label,
            kind: FixupKind::Branch,
        });
    }
    fn bc(&mut self, bo: u8, bi: u8, label: usize) {
        let at = self.words.len();
        self.emit(0);
        self.fixups.push(Fixup {
            at,
            label,
            kind: FixupKind::Conditional { bo, bi },
        });
    }
    fn resolve(mut self) -> Result<Vec<u32>, String> {
        for f in self.fixups {
            let target = self.labels[f.label].ok_or("unbound PPC code label")?;
            let delta = (target as i64 - f.at as i64) * 4;
            if delta & 3 != 0 || !(-0x0200_0000..0x0200_0000).contains(&delta) {
                return Err("PPC branch displacement is invalid".into());
            }
            self.words[f.at] = match f.kind {
                FixupKind::Branch => 0x4800_0000 | ((delta as u32) & 0x03ff_fffc),
                FixupKind::Conditional { bo, bi } => {
                    if !(-0x8000..0x8000).contains(&delta) {
                        return Err("PPC conditional branch displacement is out of range".into());
                    }
                    0x4000_0000
                        | (u32::from(bo) << 21)
                        | (u32::from(bi) << 16)
                        | ((delta as u32) & 0x0000_fffc)
                }
            };
        }
        Ok(self.words)
    }
}

fn addi(rt: u8, ra: u8, imm: u16) -> u32 {
    0x3800_0000 | (u32::from(rt) << 21) | (u32::from(ra) << 16) | u32::from(imm)
}
fn lwz(rt: u8, ra: u8, offset: u16) -> u32 {
    0x8000_0000 | (u32::from(rt) << 21) | (u32::from(ra) << 16) | u32::from(offset)
}
fn stw(rs: u8, ra: u8, offset: u16) -> u32 {
    0x9000_0000 | (u32::from(rs) << 21) | (u32::from(ra) << 16) | u32::from(offset)
}
fn lis(rt: u8, imm: u16) -> u32 {
    0x3c00_0000 | (u32::from(rt) << 21) | u32::from(imm)
}
fn ori(ra: u8, rs: u8, imm: u16) -> u32 {
    0x6000_0000 | (u32::from(rs) << 21) | (u32::from(ra) << 16) | u32::from(imm)
}
fn oris(ra: u8, rs: u8, imm: u16) -> u32 {
    0x6400_0000 | (u32::from(rs) << 21) | (u32::from(ra) << 16) | u32::from(imm)
}
fn cmpwi(ra: u8, imm: u16) -> u32 {
    0x2c00_0000 | (u32::from(ra) << 16) | u32::from(imm)
}
fn andi_dot(ra: u8, rs: u8, imm: u16) -> u32 {
    0x7000_0000 | (u32::from(rs) << 21) | (u32::from(ra) << 16) | u32::from(imm)
}
fn cmplw(ra: u8, rb: u8) -> u32 {
    0x7c00_0040 | (u32::from(ra) << 16) | (u32::from(rb) << 11)
}
fn subf(rd: u8, ra: u8, rb: u8) -> u32 {
    0x7c00_0000 | (u32::from(rd) << 21) | (u32::from(ra) << 16) | (u32::from(rb) << 11) | (40 << 1)
}
fn rlwinm(ra: u8, rs: u8, sh: u8, mb: u8, me: u8) -> u32 {
    0x5400_0000
        | (u32::from(rs) << 21)
        | (u32::from(ra) << 16)
        | (u32::from(sh) << 11)
        | (u32::from(mb) << 6)
        | (u32::from(me) << 1)
}
fn mtctr(rs: u8) -> u32 {
    0x7c00_03a6 | (u32::from(rs) << 21)
}
const BCTRL: u32 = 0x4e80_0421;
const BCTR: u32 = 0x4e80_0420;

fn emit_pointer_check(p: &mut Program, reg: u8, field_end: u32, unavailable: usize) {
    let check_mem2 = p.label();
    let valid = p.label();
    p.emit(cmpwi(reg, 0));
    p.bc(12, 2, unavailable); // beq unavailable
    p.emit(andi_dot(0, reg, 3));
    p.bc(4, 2, unavailable); // bne unavailable (unaligned)

    // MEM1: [0x80000000, 0x81800000); field_end keeps the full field in RAM.
    p.emit(lis(0, 0x8000));
    p.emit(cmplw(reg, 0));
    p.bc(12, 0, check_mem2); // blt
    let mem1_limit = 0x8180_0000u32 - field_end;
    p.emit(lis(0, (mem1_limit >> 16) as u16));
    p.emit(ori(0, 0, mem1_limit as u16));
    p.emit(cmplw(reg, 0));
    p.bc(12, 0, valid); // blt

    p.bind(check_mem2);
    p.emit(lis(0, 0x9000));
    p.emit(cmplw(reg, 0));
    p.bc(12, 0, unavailable); // blt
    let mem2_limit = 0x9400_0000u32 - field_end;
    p.emit(lis(0, (mem2_limit >> 16) as u16));
    p.emit(ori(0, 0, mem2_limit as u16));
    p.emit(cmplw(reg, 0));
    p.bc(4, 0, unavailable); // bge
    p.bind(valid);
}

/// Constant 8-word trampoline stub executed when the hook branch is taken:
/// call the original `directDraw` via CTR, then jump back to the callsite's
/// next instruction. Fully position-independent (absolute addresses only).
fn stub_template() -> [u32; STUB_WORDS] {
    stub_template_public()
}

/// Public copy of the trampoline stub template for host-side verification.
pub fn stub_template_public() -> [u32; STUB_WORDS] {
    [
        lis(11, (DIRECT_DRAW >> 16) as u16),
        ori(11, 11, DIRECT_DRAW as u16),
        mtctr(11),
        BCTRL,
        lis(11, (HOOK_RETURN_ADDRESS >> 16) as u16),
        ori(11, 11, HOOK_RETURN_ADDRESS as u16),
        mtctr(11),
        BCTR,
    ]
}

/// Emit the per-frame installer that writes the `b <stub>` hook word at
/// `HOOK_ADDRESS`. Idempotent and fail-safe: it stores only if the current word
/// is the original `bl directDraw` or the already-installed branch; anything
/// else is left untouched. Uses only the documented scratch regs r0/r10/r11/r12;
/// at this point r12 still holds the PC-relative mailbox base.
fn emit_trampoline_installer(p: &mut Program, stub_delta: u16) {
    let do_write = p.label();
    let done = p.label();
    p.emit(addi(10, 12, stub_delta)); // r10 = runtime stub address
    p.emit(lis(11, (HOOK_ADDRESS >> 16) as u16));
    p.emit(ori(11, 11, HOOK_ADDRESS as u16)); // r11 = HOOK_ADDRESS
    p.emit(subf(0, 11, 10)); // r0 = stub - hook
    p.emit(rlwinm(0, 0, 0, 6, 29)); // mask to the 26-bit branch displacement
                                    // oris (not ori): the 0x48 opcode byte lives in the HIGH halfword.
                                    // Stage 1 bug: ori r0,r0,0x4800 only ORed the low halfword, so the stored
                                    // hook word was the bare displacement 0x03D4CB34 (observed live twice).
    p.emit(oris(0, 0, 0x4800)); // r0 = b <stub> hook word
    p.emit(lwz(12, 11, 0)); // current word at the hook
    p.emit(lis(10, (ORIGINAL_HOOK_WORD >> 16) as u16));
    p.emit(ori(10, 10, ORIGINAL_HOOK_WORD as u16));
    p.emit(cmplw(12, 10));
    p.bc(12, 2, do_write); // beq: original word present -> install
    p.emit(cmplw(12, 0));
    p.bc(12, 2, done); // beq: already installed
    p.b(done); // unknown content: never clobber
    p.bind(do_write);
    p.emit(stw(0, 11, 0));
    p.b(done);
    p.bind(done);
}

fn payload_with(trampoline: bool) -> Result<(Vec<u32>, usize), String> {
    let mut p = Program::default();
    let unavailable = p.label();
    let finalize = p.label();
    let ret = p.label();

    // Preserve LR and obtain a PC-relative pointer to the inline mailbox.
    p.emit(0x7c08_02a6); // mflr r0
    p.emit(0x4800_0005); // bl +4
    p.emit(0x7d88_02a6); // mflr r12
    let mailbox_addi = p.words.len();
    p.emit(0);
    p.emit(stw(0, 12, (SAVED_LR * 4) as u16)); // preserve LR across scratch use of r0

    // Begin a seqlock write, invalidate the previous position, and preserve
    // the existing counter/ACK/echo behavior.
    p.emit(lwz(11, 12, (SEQUENCE * 4) as u16));
    p.emit(addi(11, 11, 1));
    p.emit(ori(11, 11, 1));
    p.emit(stw(11, 12, (SEQUENCE * 4) as u16));
    p.emit(0x7c00_04ac); // sync: expose odd sequence before mailbox mutation
    p.emit(addi(0, 0, 0));
    p.emit(stw(0, 12, (VALID * 4) as u16));
    p.emit(lwz(11, 12, (COUNTER * 4) as u16));
    p.emit(addi(11, 11, 1));
    p.emit(stw(11, 12, (COUNTER * 4) as u16));
    p.emit(lwz(10, 12, (ACK * 4) as u16));
    p.emit(stw(10, 12, (ECHO * 4) as u16));

    // GameSystem singleton -> scene controller -> scene -> scene object
    // holder -> MarioHolder slot -> MarioActor. Every dereference follows a
    // nonzero, aligned MEM1/MEM2 range check that includes its field extent.
    p.emit(lis(10, (GAME_SYSTEM_SINGLETON >> 16) as u16));
    p.emit(lwz(10, 10, GAME_SYSTEM_SINGLETON as u16));
    emit_pointer_check(&mut p, 10, 0x24 + 4, unavailable);
    p.emit(lwz(10, 10, 0x24));
    emit_pointer_check(&mut p, 10, 0xac + 4, unavailable);
    p.emit(lwz(10, 10, 0xac));
    emit_pointer_check(&mut p, 10, 0x10 + 4, unavailable);
    p.emit(lwz(10, 10, 0x10));
    emit_pointer_check(&mut p, 10, 0x50 + 4, unavailable);
    p.emit(lwz(10, 10, 0x50));
    emit_pointer_check(&mut p, 10, 0x0c + 4, unavailable);
    p.emit(lwz(10, 10, 0x0c));
    // mPosition is inline at MarioActor+0x0c; require all 12 bytes to fit.
    emit_pointer_check(&mut p, 10, 0x0c + 12, unavailable);
    p.emit(stw(10, 12, (ACTOR * 4) as u16));
    p.emit(lwz(11, 10, 0x0c));
    p.emit(stw(11, 12, (X * 4) as u16));
    p.emit(lwz(11, 10, 0x10));
    p.emit(stw(11, 12, (Y * 4) as u16));
    p.emit(lwz(11, 10, 0x14));
    p.emit(stw(11, 12, (Z * 4) as u16));
    p.emit(addi(0, 0, 1));
    p.emit(stw(0, 12, (VALID * 4) as u16));
    p.b(finalize);

    p.bind(unavailable);
    p.bind(finalize);
    p.emit(0x7c00_04ac); // sync: publish mailbox fields before even sequence
    p.emit(lwz(11, 12, (SEQUENCE * 4) as u16));
    p.emit(addi(11, 11, 1));
    p.emit(stw(11, 12, (SEQUENCE * 4) as u16));
    p.emit(lwz(0, 12, (SAVED_LR * 4) as u16));
    p.emit(0x7c08_03a6); // mtlr r0
    p.b(ret);
    p.bind(ret);
    let installer_addi = if trampoline {
        let at = p.words.len();
        emit_trampoline_installer(&mut p, 0);
        Some(at)
    } else {
        None
    };
    p.emit(0x4e80_0020); // blr
    if p.words.len() & 1 != 0 {
        p.emit(0x6000_0000); // nop; keep Gecko's instruction-pair count aligned
    }
    let mailbox_offset = p.words.len() * 4;
    p.words[mailbox_addi] = addi(12, 12, (mailbox_offset - 8) as u16);
    p.emit(0); // counter
    p.emit(0); // host ACK
    p.emit(0); // game-observed ACK echo
    p.emit(MAILBOX_MAGIC); // marker is data skipped by the branch above
    p.emit(MAILBOX_VERSION);
    p.emit(0); // sequence
    p.emit(0); // valid
    p.emit(0); // actor
    p.emit(0); // x
    p.emit(0); // y
    p.emit(0); // z
    p.emit(0); // reserved
    p.emit(0); // saved LR
    p.emit(0); // reserved/pad
    if trampoline {
        let stub_offset = p.words.len() * 4;
        // r12 holds the mailbox base; the stub sits stub_delta bytes after it.
        let stub_delta = (stub_offset - mailbox_offset) as u16;
        p.words[installer_addi.expect("installer emitted")] = addi(10, 12, stub_delta);
        for word in stub_template() {
            p.emit(word);
        }
    }
    let words = p.resolve()?;
    Ok((words, mailbox_offset / 4))
}

fn payload() -> Result<(Vec<u32>, usize), String> {
    payload_with(false)
}

fn mailbox_marker_index() -> Result<usize, String> {
    let (words, _) = payload()?;
    words
        .iter()
        .position(|word| *word == MAILBOX_MAGIC)
        .ok_or_else(|| "internal PPC payload has no mailbox marker".into())
}

pub const CODE_NAME: &str = "MultiGalaxy1 Stock Dolphin Mailbox Probe";

#[cfg(test)]
pub(crate) fn payload_words() -> Vec<u32> {
    payload().expect("static PPC payload must assemble").0
}

#[cfg(test)]
pub(crate) fn payload_trampoline_word_count_for_test() -> usize {
    payload_with(true)
        .expect("static trampoline PPC payload must assemble")
        .0
        .len()
}

#[cfg(test)]
pub(crate) fn mailbox_data_word() -> usize {
    payload().expect("static PPC payload must assemble").1
}

/// Append the local-only test Gecko code after the mandatory AllStars OnFrame
/// section. The game image remains untouched. `trampoline` adds the Stage 1
/// installer/stub to the C0 block. The trampoline variant is not combinable
/// with the interactive mailbox probe in Stage 1, so `locate_mailbox` keeps
/// matching the plain payload.
pub fn append_code_with(settings: &mut String, trampoline: bool) {
    let Ok((words, _)) = payload_with(trampoline) else {
        return;
    };
    let count = words.len() / 2;
    settings.push_str(&format!(
        "\n[Gecko]\n${CODE_NAME} [MultiGalaxy1]\nC0000000 {count:08X}\n"
    ));
    for pair in words.chunks_exact(2) {
        settings.push_str(&format!("{:08X} {:08X}\n", pair[0], pair[1]));
    }
    settings.push_str(&format!("\n[Gecko_Enabled]\n${CODE_NAME}\n"));
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Mailbox {
    base: u32,
}

impl Mailbox {
    pub fn counter_address(self) -> u32 {
        self.base
    }
    pub fn acknowledgement_address(self) -> u32 {
        self.base + (ACK as u32 * 4)
    }
    pub fn echo_address(self) -> u32 {
        self.base + (ECHO as u32 * 4)
    }
    pub fn snapshot_address(self) -> u32 {
        self.base
    }

    pub(crate) fn is_inside_handler(self) -> bool {
        u64::from(self.base) >= u64::from(HANDLER_BASE)
            && u64::from(self.base) + u64::from(MAILBOX_BYTES) <= u64::from(HANDLER_END)
            && u64::from(self.base) + u64::from(MAILBOX_BYTES) <= u64::from(ALLSTARS_CAVE_START)
            && self.base & 3 == 0
    }
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct PositionSnapshot {
    pub counter: u32,
    pub actor: u32,
    pub x: f32,
    pub y: f32,
    pub z: f32,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SnapshotState {
    Unavailable,
    Inconsistent,
    Invalid,
}

/// Decode a 48-byte mailbox view only if both sequence reads are the same even
/// value and the embedded version/marker are recognized. `after` is a second
/// read of the sequence field after copying the whole block.
pub fn decode_snapshot(
    bytes: &[u8],
    after: u32,
) -> Result<Result<PositionSnapshot, SnapshotState>, String> {
    if bytes.len() != SNAPSHOT_BYTES {
        return Err("mailbox snapshot has an unexpected length".into());
    }
    let word = |i: usize| u32::from_be_bytes(bytes[i * 4..i * 4 + 4].try_into().unwrap());
    if word(MARKER) != MAILBOX_MAGIC || word(VERSION) != MAILBOX_VERSION {
        return Err("mailbox marker or version mismatch".into());
    }
    let before = word(SEQUENCE);
    if before != after || before & 1 != 0 {
        return Ok(Err(SnapshotState::Inconsistent));
    }
    if word(VALID) == 0 {
        return Ok(Err(SnapshotState::Unavailable));
    }
    if word(VALID) != 1 {
        return Ok(Err(SnapshotState::Invalid));
    }
    let actor = word(ACTOR);
    if !crate::player_probe::is_guest_ram_range(actor, 0x18) || actor & 3 != 0 {
        return Ok(Err(SnapshotState::Invalid));
    }
    let x = f32::from_bits(word(X));
    let y = f32::from_bits(word(Y));
    let z = f32::from_bits(word(Z));
    if ![x, y, z]
        .into_iter()
        .all(|v| v.is_finite() && v.abs() < 10_000_000.0)
    {
        return Ok(Err(SnapshotState::Invalid));
    }
    Ok(Ok(PositionSnapshot {
        counter: word(COUNTER),
        actor,
        x,
        y,
        z,
    }))
}

/// Find exactly one versioned payload in Dolphin's fixed Gecko handler region.
pub fn locate_mailbox(
    region_base: u32,
    handler_size: usize,
    bytes: &[u8],
) -> Result<Option<Mailbox>, String> {
    if region_base != HANDLER_BASE || bytes.len() != (HANDLER_END - HANDLER_BASE) as usize {
        return Err("memory view does not cover the exact Gecko code-handler region".into());
    }
    if handler_size != CODEHANDLER_BYTES || handler_size > bytes.len() {
        return Err(format!(
            "Dolphin codehandler.bin size is unsupported for this mailbox layout: expected {CODEHANDLER_BYTES}, got {handler_size}"
        ));
    }
    let (expected, _) = payload()?;
    let marker_index = mailbox_marker_index()?;
    let marker_offset = marker_index * 4;
    let mut found = None;
    for marker_at in
        (handler_size + 8..bytes.len().saturating_sub(MAILBOX_BYTES as usize - 4)).step_by(4)
    {
        if u32::from_be_bytes(bytes[marker_at..marker_at + 4].try_into().unwrap()) != MAILBOX_MAGIC
        {
            continue;
        }
        let Some(start) = marker_at.checked_sub(marker_offset) else {
            continue;
        };
        let end = start + expected.len() * 4;
        if end > bytes.len() {
            return Err("Gecko payload extends outside handler memory".into());
        }
        let actual: Vec<u32> = bytes[start..end]
            .chunks_exact(4)
            .map(|c| u32::from_be_bytes(c.try_into().unwrap()))
            .collect();
        let mailbox_word = marker_index - MARKER;
        let mutable: Vec<usize> = [
            COUNTER, ACK, ECHO, SEQUENCE, VALID, ACTOR, X, Y, Z, SAVED_LR,
        ]
        .into_iter()
        .map(|i| mailbox_word + i)
        .collect();
        if actual
            .iter()
            .enumerate()
            .any(|(i, w)| !mutable.contains(&i) && *w != expected[i])
        {
            return Err(
                "found Gecko mailbox marker, but PPC routine/version signature is invalid".into(),
            );
        }
        if found.is_some() {
            return Err("multiple Gecko mailbox markers found in Dolphin's handler region".into());
        }
        let mailbox_start = region_base + start as u32 + (marker_index - MARKER) as u32 * 4;
        let cave_start = region_base + start as u32;
        if u64::from(cave_start) + (expected.len() * 4) as u64 > u64::from(ALLSTARS_CAVE_START) {
            return Err("Gecko probe body overlaps the PAL AllStars code cave; refusing to use this code-list layout".into());
        }
        let mailbox = Mailbox {
            base: mailbox_start,
        };
        if !mailbox.is_inside_handler() {
            return Err("located mailbox is outside Dolphin's reserved Gecko region".into());
        }
        found = Some(mailbox);
    }
    Ok(found)
}

pub fn snapshot_matches_mailbox(
    region_base: u32,
    handler_size: usize,
    bytes: &[u8],
    expected: Mailbox,
) -> Result<bool, String> {
    Ok(locate_mailbox(region_base, handler_size, bytes)? == Some(expected))
}

/// Validate that two independently sampled, read-only handler snapshots expose
/// the same unique mailbox. Mutable counter/position words may change between
/// samples; the PPC routine signature and mailbox location may not.
pub fn validate_read_only_mailbox_samples(
    handler_size: usize,
    first: &[u8],
    second: &[u8],
) -> Result<Mailbox, String> {
    let first = locate_mailbox(HANDLER_BASE, handler_size, first)?
        .ok_or("validated Gecko mailbox is absent from the live handler region")?;
    let second = locate_mailbox(HANDLER_BASE, handler_size, second)?
        .ok_or("Gecko mailbox disappeared from the second live handler sample")?;
    if first != second {
        return Err("Gecko mailbox moved between read-only samples".into());
    }
    Ok(first)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture() -> Vec<u8> {
        let mut bytes = vec![0; (HANDLER_END - HANDLER_BASE) as usize];
        let (words, _) = payload().unwrap();
        let offset = 2880 + 16;
        for (index, word) in words.iter().enumerate() {
            bytes[offset + index * 4..offset + index * 4 + 4].copy_from_slice(&word.to_be_bytes());
        }
        bytes
    }

    #[test]
    fn generated_gecko_payload_preserves_ack_and_walks_the_verified_pal_chain() {
        let (words, _) = payload().unwrap();
        assert_eq!(words.len() % 2, 0);
        assert_eq!(words[0..3], [0x7c08_02a6, 0x4800_0005, 0x7d88_02a6]);
        assert!(words.contains(&lwz(10, 10, 0x24)));
        assert!(words.contains(&lwz(10, 10, 0xac)));
        assert!(words.contains(&lwz(10, 10, 0x50)));
        assert!(words.contains(&lwz(11, 10, 0x0c)));
        assert!(words.contains(&stw(10, 12, 8)));
        let mut settings = "[OnFrame_Enabled]\nAllStars\n".to_owned();
        append_code_with(&mut settings, false);
        assert!(settings.contains(&format!("C0000000 {:08X}", words.len() / 2)));
        assert!(settings.ends_with("[Gecko_Enabled]\n$MultiGalaxy1 Stock Dolphin Mailbox Probe\n"));
    }

    #[test]
    fn locates_unique_versioned_mailbox_and_rejects_signature_or_bounds_changes() {
        let bytes = fixture();
        let mailbox = locate_mailbox(HANDLER_BASE, CODEHANDLER_BYTES, &bytes)
            .unwrap()
            .unwrap();
        let mailbox_word = mailbox_data_word();
        assert_eq!(
            mailbox.counter_address(),
            HANDLER_BASE + 2880 + 16 + mailbox_word as u32 * 4
        );
        assert!(mailbox.is_inside_handler());
        assert!(locate_mailbox(HANDLER_BASE + 4, CODEHANDLER_BYTES, &bytes).is_err());
        assert!(locate_mailbox(HANDLER_BASE, CODEHANDLER_BYTES + 1, &bytes).is_err());
        assert_eq!(
            locate_mailbox(HANDLER_BASE, CODEHANDLER_BYTES, &vec![0; bytes.len()]).unwrap(),
            None
        );
        let mut corrupt = bytes;
        corrupt[2880 + 16] ^= 1;
        assert!(locate_mailbox(HANDLER_BASE, CODEHANDLER_BYTES, &corrupt).is_err());
    }

    #[test]
    fn read_only_preflight_requires_same_unique_mailbox_in_both_samples() {
        let first = fixture();
        let mut second = first.clone();
        // Counter and sampled position words are mutable and may advance.
        second[2880 + 16 + mailbox_data_word() * 4] ^= 1;
        assert_eq!(
            validate_read_only_mailbox_samples(CODEHANDLER_BYTES, &first, &second).unwrap(),
            locate_mailbox(HANDLER_BASE, CODEHANDLER_BYTES, &first)
                .unwrap()
                .unwrap()
        );
        assert!(validate_read_only_mailbox_samples(
            CODEHANDLER_BYTES,
            &first,
            &vec![0; first.len()]
        )
        .unwrap_err()
        .contains("disappeared"));
        let mut moved = vec![0; first.len()];
        let (words, _) = payload().unwrap();
        let alternate = CODEHANDLER_BYTES + 20;
        assert!(alternate + words.len() * 4 <= moved.len());
        for (index, word) in words.iter().enumerate() {
            moved[alternate + index * 4..alternate + index * 4 + 4]
                .copy_from_slice(&word.to_be_bytes());
        }
        // A valid payload at another location is reported as moved, never
        // accepted as the original mailbox location.
        assert!(
            validate_read_only_mailbox_samples(CODEHANDLER_BYTES, &first, &moved)
                .unwrap_err()
                .contains("moved between read-only samples")
        );
    }

    #[test]
    fn snapshot_requires_stable_even_sequence_version_valid_actor_and_finite_coordinates() {
        let mut bytes = vec![0; SNAPSHOT_BYTES];
        let put =
            |b: &mut [u8], i: usize, v: u32| b[i * 4..i * 4 + 4].copy_from_slice(&v.to_be_bytes());
        put(&mut bytes, MARKER, MAILBOX_MAGIC);
        put(&mut bytes, VERSION, MAILBOX_VERSION);
        put(&mut bytes, SEQUENCE, 2);
        put(&mut bytes, VALID, 1);
        put(&mut bytes, ACTOR, 0x8000_4000);
        put(&mut bytes, X, 12.5f32.to_bits());
        put(&mut bytes, Y, (-3.25f32).to_bits());
        put(&mut bytes, Z, 99.0f32.to_bits());
        let decoded = decode_snapshot(&bytes, 2).unwrap().unwrap();
        assert_eq!((decoded.x, decoded.y, decoded.z), (12.5, -3.25, 99.0));
        assert_eq!(
            decode_snapshot(&bytes, 4).unwrap(),
            Err(SnapshotState::Inconsistent)
        );
        put(&mut bytes, SEQUENCE, 3);
        assert_eq!(
            decode_snapshot(&bytes, 3).unwrap(),
            Err(SnapshotState::Inconsistent)
        );
        put(&mut bytes, SEQUENCE, 4);
        put(&mut bytes, VALID, 0);
        assert_eq!(
            decode_snapshot(&bytes, 4).unwrap(),
            Err(SnapshotState::Unavailable)
        );
        put(&mut bytes, VALID, 1);
        put(&mut bytes, VALID, 2);
        assert_eq!(
            decode_snapshot(&bytes, 4).unwrap(),
            Err(SnapshotState::Invalid)
        );
        put(&mut bytes, VALID, 1);
        put(&mut bytes, ACTOR, 0x1234);
        assert_eq!(
            decode_snapshot(&bytes, 4).unwrap(),
            Err(SnapshotState::Invalid)
        );
    }

    #[test]
    fn trampoline_payload_fits_before_allstars_cave_with_valid_branches() {
        let (words, _) = payload_with(true).unwrap();
        // Gecko C0 words must form instruction pairs or the trailing word is
        // silently dropped by the ini emitter.
        assert_eq!(words.len() % 2, 0);
        // The block must be emitted FIRST in the ini so it loads at the lowest
        // cave address (right after the 2880-byte codehandler) and stays below
        // the AllStars cave writes at 0x80002790.
        let start = HANDLER_BASE + 2880 + 16;
        assert!(start + words.len() as u32 * 4 <= ALLSTARS_CAVE_START);
        assert!(words.len() > payload().unwrap().0.len());
        assert_eq!(&words[words.len() - STUB_WORDS..], &stub_template_public());
        for (index, word) in words.iter().enumerate() {
            if word >> 26 == 18 || word >> 26 == 16 {
                let displacement = if word >> 26 == 18 {
                    ((*word << 6) as i32) >> 6
                } else {
                    ((*word << 16) as i32) >> 16
                };
                let target = index as i64 + displacement as i64 / 4;
                assert!(
                    (0..words.len() as i64).contains(&target),
                    "branch at word {index} targets {target}"
                );
            }
        }
    }

    #[test]
    fn trampoline_installer_composes_the_full_branch_word() {
        // Regression for the live corruption (hook word stored as 0x03D4CB34):
        // the high opcode byte 0x48 lives in the HIGH halfword, so the OR step
        // must be `oris`, not `ori`.
        let (words, _) = payload_with(true).unwrap();
        let pos = words
            .iter()
            .position(|w| *w == 0x3D60_802B)
            .expect("installer lis r11,0x802B present");
        assert_eq!(words[pos + 3], 0x5400_01BA, "rlwinm mask 0x03FFFFFC");
        assert_eq!(
            words[pos + 4],
            0x6400_4800,
            "must be oris r0,r0,0x4800 (high halfword), not ori"
        );
        // Semantic check: compose the stored value the way the emitted
        // instructions do and require the full branch word.
        let hook = HOOK_ADDRESS;
        let stub = HANDLER_BASE + 2880 + 16 + (words.len() - STUB_WORDS) as u32 * 4;
        let delta = stub.wrapping_sub(hook) & 0x03FF_FFFC;
        let stored = delta | 0x4800_0000;
        assert_eq!(stored >> 26, 18);
        assert_eq!(stored & 1, 0, "plain b, no link");
        let disp = (stored & 0x03FF_FFFC) as i32;
        let disp = if disp & 0x0200_0000 != 0 {
            disp - 0x0400_0000
        } else {
            disp
        };
        let target = hook.wrapping_add(disp as u32);
        assert_eq!(target, stub);
    }

    #[test]
    fn payload_fits_before_allstars_cave() {
        let (words, _) = payload().unwrap();
        let start = HANDLER_BASE + 2880 + 16;
        assert!(start + words.len() as u32 * 4 <= ALLSTARS_CAVE_START);
        for (index, word) in words.iter().enumerate() {
            let opcode = word >> 26;
            let target = match opcode {
                18 => {
                    let displacement = ((*word << 6) as i32) >> 6;
                    Some(index as i32 + displacement / 4)
                }
                16 => {
                    let displacement = ((*word << 16) as i32) >> 16;
                    Some(index as i32 + displacement / 4)
                }
                _ => None,
            };
            if let Some(target) = target {
                assert!(
                    target >= 0 && (target as usize) < words.len(),
                    "branch at word {index} targets {target}"
                );
            }
        }
    }
}
