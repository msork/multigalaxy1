//! Compact statistics for observed game-thread acknowledgement latency.

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct LatencySummary {
    pub count: usize,
    pub mean_ms: f64,
    pub stddev_ms: f64,
    pub p50_ms: f64,
    pub p95_ms: f64,
    pub p99_ms: f64,
    pub max_ms: f64,
}

pub fn summarize(samples_ms: &[f64]) -> Option<LatencySummary> {
    if samples_ms.is_empty() || samples_ms.iter().any(|sample| !sample.is_finite()) {
        return None;
    }
    let mut sorted = samples_ms.to_vec();
    sorted.sort_by(f64::total_cmp);
    let count = samples_ms.len();
    let mean_ms = samples_ms.iter().sum::<f64>() / count as f64;
    let variance = samples_ms
        .iter()
        .map(|sample| (sample - mean_ms).powi(2))
        .sum::<f64>()
        / count as f64;
    Some(LatencySummary {
        count,
        mean_ms,
        stddev_ms: variance.sqrt(),
        p50_ms: percentile(&sorted, 0.50),
        p95_ms: percentile(&sorted, 0.95),
        p99_ms: percentile(&sorted, 0.99),
        max_ms: *sorted.last().expect("nonempty input checked"),
    })
}

fn percentile(sorted: &[f64], quantile: f64) -> f64 {
    let index = quantile * (sorted.len() - 1) as f64;
    let lower = index.floor() as usize;
    let upper = index.ceil() as usize;
    let fraction = index - lower as f64;
    sorted[lower] + (sorted[upper] - sorted[lower]) * fraction
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reports_mean_jitter_percentiles_and_maximum() {
        let summary = summarize(&[10.0, 20.0, 30.0, 40.0, 50.0]).unwrap();
        assert_eq!(summary.count, 5);
        assert_eq!(summary.mean_ms, 30.0);
        assert!((summary.stddev_ms - 14.1421356237).abs() < 1e-8);
        assert_eq!(summary.p50_ms, 30.0);
        assert_eq!(summary.p95_ms, 48.0);
        assert!((summary.p99_ms - 49.6).abs() < 1e-10);
        assert_eq!(summary.max_ms, 50.0);
    }

    #[test]
    fn handles_single_sample_and_rejects_empty_or_nonfinite_data() {
        let summary = summarize(&[16.7]).unwrap();
        assert_eq!(summary.p50_ms, 16.7);
        assert_eq!(summary.stddev_ms, 0.0);
        assert!(summarize(&[]).is_none());
        assert!(summarize(&[1.0, f64::NAN]).is_none());
    }
}
