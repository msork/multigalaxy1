//! Bounded PAL identity validation before any bridge write is permitted.

use std::time::{Duration, Instant};

pub const PAL_GAME_ID: [u8; 6] = *b"RMGP01";
pub const STARTUP_TIMEOUT_OPTION: &str = "--bridge-startup-timeout-secs=";

pub fn parse_timeout_option(option: &str) -> Result<Option<Duration>, String> {
    let Some(value) = option.strip_prefix(STARTUP_TIMEOUT_OPTION) else {
        return Ok(None);
    };
    let seconds = value
        .parse::<u64>()
        .map_err(|_| "bridge startup timeout must be integer seconds".to_owned())?;
    if !(5..=300).contains(&seconds) {
        return Err("bridge startup timeout must be between 5 and 300 seconds".into());
    }
    Ok(Some(Duration::from_secs(seconds)))
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum IdentityStatus {
    Ready,
    Uninitialized,
    WrongGame,
    InvalidMapping,
}

pub fn classify_game_id(bytes: [u8; 6]) -> IdentityStatus {
    if bytes == PAL_GAME_ID {
        IdentityStatus::Ready
    } else if bytes.iter().all(|byte| *byte == 0)
        || bytes
            .iter()
            .position(|byte| *byte == 0)
            .is_some_and(|first_zero| {
                first_zero > 0
                    && bytes[..first_zero].iter().all(u8::is_ascii_graphic)
                    && bytes[first_zero..].iter().all(|byte| *byte == 0)
            })
    {
        // Dolphin's named mapping exists before the emulated boot process has
        // populated MEM1[0..6]. A partial prefix of the expected ID is treated
        // as transient too; it is bounded by the caller's startup deadline.
        IdentityStatus::Uninitialized
    } else if bytes.iter().all(u8::is_ascii_graphic) {
        IdentityStatus::WrongGame
    } else {
        IdentityStatus::InvalidMapping
    }
}

pub fn describe_game_id(bytes: [u8; 6]) -> String {
    let hex = bytes
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect::<String>();
    let ascii = String::from_utf8_lossy(&bytes);
    format!("hex={hex} ascii={ascii:?}")
}

/// Poll a successfully mapped MEM1 view until it contains the exact PAL ID.
/// Read failures are invalid mapping failures, wrong printable IDs fail
/// immediately, and zero/partial IDs are retried only until `timeout`.
pub fn wait_for_pal_identity<R, L>(
    timeout: Duration,
    interval: Duration,
    mut read_id: R,
    mut on_poll: L,
) -> Result<(), String>
where
    R: FnMut() -> Result<[u8; 6], String>,
    L: FnMut(Duration, [u8; 6], IdentityStatus),
{
    let started = Instant::now();
    loop {
        let elapsed = started.elapsed();
        let observed_id = read_id().map_err(|error| {
            format!(
                "invalid Dolphin MEM1 mapping while reading game ID after {} ms: {error}",
                elapsed.as_millis()
            )
        })?;
        let status = classify_game_id(observed_id);
        on_poll(elapsed, observed_id, status);
        match status {
            IdentityStatus::Ready => return Ok(()),
            IdentityStatus::WrongGame => {
                return Err(format!(
                    "wrong game in Dolphin MEM1 after {} ms ({}); expected PAL RMGP01",
                    elapsed.as_millis(),
                    describe_game_id(observed_id)
                ));
            }
            IdentityStatus::InvalidMapping => {
                return Err(format!(
                    "invalid Dolphin MEM1 mapping after {} ms: game ID bytes are neither uninitialized nor printable ({}); refusing all writes",
                    elapsed.as_millis(),
                    describe_game_id(observed_id)
                ));
            }
            IdentityStatus::Uninitialized if elapsed >= timeout => {
                return Err(format!(
                    "timed out waiting {timeout:?} for PAL game identity in Dolphin MEM1; last observed uninitialized ID ({}) after {} ms",
                    describe_game_id(observed_id),
                    elapsed.as_millis()
                ));
            }
            IdentityStatus::Uninitialized => std::thread::sleep(interval),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::Cell;

    #[test]
    fn accepts_delayed_pal_identity_after_uninitialized_reads() {
        let reads = Cell::new(0);
        let observed = Cell::new(0);
        wait_for_pal_identity(
            Duration::from_secs(1),
            Duration::ZERO,
            || {
                let count = reads.get() + 1;
                reads.set(count);
                Ok(if count < 4 { [0; 6] } else { PAL_GAME_ID })
            },
            |_, _, _| observed.set(observed.get() + 1),
        )
        .unwrap();
        assert_eq!(reads.get(), 4);
        assert_eq!(observed.get(), 4);
    }

    #[test]
    fn rejects_wrong_game_immediately_with_observed_identity() {
        let error = wait_for_pal_identity(
            Duration::from_secs(60),
            Duration::ZERO,
            || Ok(*b"RMGE01"),
            |_, _, _| {},
        )
        .unwrap_err();
        assert!(error.contains("wrong game"));
        assert!(error.contains("RMGE01"));
    }

    #[test]
    fn treats_partially_copied_printable_id_as_startup_in_progress() {
        assert_eq!(
            classify_game_id(*b"RMGE\0\0"),
            IdentityStatus::Uninitialized
        );
        assert_eq!(classify_game_id(*b"RMGE01"), IdentityStatus::WrongGame);
    }

    #[test]
    fn times_out_when_identity_remains_uninitialized() {
        let error =
            wait_for_pal_identity(Duration::ZERO, Duration::ZERO, || Ok([0; 6]), |_, _, _| {})
                .unwrap_err();
        assert!(error.contains("timed out"));
        assert!(error.contains("000000000000"));
    }

    #[test]
    fn distinguishes_invalid_mapping_bytes_and_read_failures() {
        let invalid_bytes = wait_for_pal_identity(
            Duration::from_secs(1),
            Duration::ZERO,
            || Ok([0, 0xff, 0, 0, 0, 0]),
            |_, _, _| {},
        )
        .unwrap_err();
        assert!(invalid_bytes.contains("invalid Dolphin MEM1 mapping"));

        let read_error = wait_for_pal_identity(
            Duration::from_secs(1),
            Duration::ZERO,
            || Err("view bounds rejected the read".into()),
            |_, _, _| {},
        )
        .unwrap_err();
        assert!(read_error.contains("invalid Dolphin MEM1 mapping"));
    }

    #[test]
    fn parses_only_bounded_bridge_startup_timeouts() {
        assert_eq!(
            parse_timeout_option("--bridge-startup-timeout-secs=90").unwrap(),
            Some(Duration::from_secs(90))
        );
        assert_eq!(parse_timeout_option("--gecko-bridge-probe").unwrap(), None);
        assert!(parse_timeout_option("--bridge-startup-timeout-secs=4").is_err());
        assert!(parse_timeout_option("--bridge-startup-timeout-secs=301").is_err());
        assert!(parse_timeout_option("--bridge-startup-timeout-secs=nope").is_err());
    }
}
