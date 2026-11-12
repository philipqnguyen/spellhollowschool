//! Server-side measurements: the Prometheus text from `GET /metrics` and
//! `docker stats` for the containers under test.

use std::collections::HashMap;

use serde::Serialize;

/// One scrape of `/metrics`: every sample keyed by its series exactly as
/// printed, e.g. `shs_world_players` or `shs_world_tick_seconds_bucket{le="0.015"}`.
#[derive(Debug, Clone, Default)]
pub struct Metrics {
    pub series: HashMap<String, f64>,
}

impl Metrics {
    /// Parses the Prometheus text format (comments and malformed lines are skipped).
    pub fn parse(text: &str) -> Metrics {
        let series = text
            .lines()
            .filter(|l| !l.starts_with('#') && !l.trim().is_empty())
            .filter_map(|l| {
                let (key, value) = l.rsplit_once(' ')?;
                Some((key.trim().to_owned(), value.trim().parse().ok()?))
            })
            .collect();
        Metrics { series }
    }

    pub fn get(&self, series: &str) -> f64 {
        self.series.get(series).copied().unwrap_or(0.0)
    }

    /// Sum over every series of the metric `name`, whatever its labels.
    pub fn sum(&self, name: &str) -> f64 {
        self.series
            .iter()
            .filter(|(k, _)| *k == name || k.strip_prefix(name).is_some_and(|r| r.starts_with('{')))
            .map(|(_, v)| v)
            .sum()
    }

    /// Value of `name{label="value"}` for each value of `label`.
    pub fn by_label(&self, name: &str, label: &str) -> HashMap<String, f64> {
        let prefix = format!("{name}{{{label}=\"");
        self.series
            .iter()
            .filter_map(|(k, v)| {
                let rest = k.strip_prefix(&prefix)?;
                Some((rest.split('"').next()?.to_owned(), *v))
            })
            .collect()
    }

    /// Cumulative buckets `(le, count)` of the histogram `name`, ascending,
    /// ending with `+Inf`.
    pub fn buckets(&self, name: &str) -> Vec<(f64, f64)> {
        let mut b: Vec<(f64, f64)> = self
            .by_label(&format!("{name}_bucket"), "le")
            .into_iter()
            .filter_map(|(le, v)| {
                let le = if le == "+Inf" {
                    f64::INFINITY
                } else {
                    le.parse().ok()?
                };
                Some((le, v))
            })
            .collect();
        b.sort_by(|x, y| x.0.total_cmp(&y.0));
        b
    }
}

/// The observations of histogram `name` made between two scrapes.
#[derive(Debug, Clone, Default)]
pub struct HistogramDelta {
    /// Cumulative `(le, count)`, ascending.
    pub buckets: Vec<(f64, f64)>,
    pub sum: f64,
    pub count: f64,
}

impl HistogramDelta {
    pub fn between(start: &Metrics, end: &Metrics, name: &str) -> HistogramDelta {
        let before: HashMap<u64, f64> = start
            .buckets(name)
            .into_iter()
            .map(|(le, v)| (le.to_bits(), v))
            .collect();
        let buckets = end
            .buckets(name)
            .into_iter()
            .map(|(le, v)| (le, v - before.get(&le.to_bits()).copied().unwrap_or(0.0)))
            .collect();
        HistogramDelta {
            buckets,
            sum: end.get(&format!("{name}_sum")) - start.get(&format!("{name}_sum")),
            count: end.get(&format!("{name}_count")) - start.get(&format!("{name}_count")),
        }
    }

    pub fn mean(&self) -> f64 {
        if self.count > 0.0 {
            self.sum / self.count
        } else {
            0.0
        }
    }

    /// The `q` quantile, interpolated linearly inside its bucket as Prometheus'
    /// `histogram_quantile` does. Observations in the `+Inf` bucket report the
    /// largest finite bound.
    pub fn quantile(&self, q: f64) -> f64 {
        let Some(&(_, total)) = self.buckets.last() else {
            return 0.0;
        };
        if total <= 0.0 {
            return 0.0;
        }
        let rank = q * total;
        let mut prev = (0.0, 0.0);
        for &(le, cum) in &self.buckets {
            if cum >= rank {
                if le.is_infinite() {
                    return prev.0;
                }
                let in_bucket = cum - prev.1;
                let frac = if in_bucket > 0.0 {
                    (rank - prev.1) / in_bucket
                } else {
                    1.0
                };
                return prev.0 + (le - prev.0) * frac;
            }
            prev = (le, cum);
        }
        prev.0
    }

    /// Observations above the bucket bound `le` (which must be one of the bounds).
    pub fn count_above(&self, le: f64) -> f64 {
        let total = self.buckets.last().map_or(0.0, |b| b.1);
        let below = self
            .buckets
            .iter()
            .find(|b| (b.0 - le).abs() < 1e-12)
            .map_or(total, |b| b.1);
        total - below
    }

    /// Upper bound of the bucket holding the largest observation.
    pub fn max_bound(&self) -> f64 {
        let mut prev_cum = 0.0;
        let mut bound = 0.0;
        for &(le, cum) in &self.buckets {
            if cum > prev_cum {
                bound = le;
            }
            prev_cum = cum;
        }
        bound
    }
}

/// One `docker stats` reading of one container.
#[derive(Debug, Clone, Serialize)]
pub struct ContainerSample {
    pub name: String,
    /// Percent of one CPU (160 = 1.6 CPUs busy).
    pub cpu_pct: f64,
    pub mem_mib: f64,
    pub mem_limit_mib: f64,
}

/// Reads `docker stats` once for `containers`. Takes about two seconds (Docker
/// measures CPU over an interval).
pub async fn docker_stats(containers: &[String]) -> Result<Vec<ContainerSample>, String> {
    let out = tokio::process::Command::new("docker")
        .args(["stats", "--no-stream", "--format", "{{json .}}"])
        .args(containers)
        .output()
        .await
        .map_err(|e| format!("docker stats: {e}"))?;
    if !out.status.success() {
        return Err(format!(
            "docker stats: {}",
            String::from_utf8_lossy(&out.stderr).trim()
        ));
    }
    String::from_utf8_lossy(&out.stdout)
        .lines()
        .filter(|l| !l.trim().is_empty())
        .map(parse_docker_line)
        .collect()
}

fn parse_docker_line(line: &str) -> Result<ContainerSample, String> {
    let v: serde_json::Value =
        serde_json::from_str(line).map_err(|e| format!("docker stats line {line:?}: {e}"))?;
    let field = |k: &str| v[k].as_str().unwrap_or_default().to_owned();
    let cpu_pct = field("CPUPerc")
        .trim_end_matches('%')
        .parse()
        .map_err(|_| format!("docker stats CPUPerc in {line:?}"))?;
    let mem = field("MemUsage");
    let (used, limit) = mem
        .split_once('/')
        .ok_or_else(|| format!("docker stats MemUsage {mem:?}"))?;
    Ok(ContainerSample {
        name: field("Name"),
        cpu_pct,
        mem_mib: parse_size_mib(used)?,
        mem_limit_mib: parse_size_mib(limit)?,
    })
}

/// "512MiB", "1.2GiB", "800kB" → MiB.
fn parse_size_mib(s: &str) -> Result<f64, String> {
    let s = s.trim();
    let split = s
        .find(|c: char| c.is_ascii_alphabetic())
        .ok_or_else(|| format!("size without unit: {s:?}"))?;
    let (num, unit) = s.split_at(split);
    let n: f64 = num.trim().parse().map_err(|_| format!("bad size {s:?}"))?;
    let bytes = match unit {
        "B" => 1.0,
        "KiB" => 1024.0,
        "MiB" => 1024.0 * 1024.0,
        "GiB" => 1024.0 * 1024.0 * 1024.0,
        "TiB" => 1024.0f64.powi(4),
        "kB" | "KB" => 1e3,
        "MB" => 1e6,
        "GB" => 1e9,
        "TB" => 1e12,
        _ => return Err(format!("unknown unit in {s:?}")),
    };
    Ok(n * bytes / (1024.0 * 1024.0))
}

#[cfg(test)]
mod tests {
    use super::*;

    const TEXT: &str = "\
# HELP shs_world_tick_seconds Castle world tick duration.
# TYPE shs_world_tick_seconds histogram
shs_world_tick_seconds_bucket{le=\"0.001\"} 10
shs_world_tick_seconds_bucket{le=\"0.002\"} 60
shs_world_tick_seconds_bucket{le=\"0.015\"} 99
shs_world_tick_seconds_bucket{le=\"+Inf\"} 100
shs_world_tick_seconds_sum 0.25
shs_world_tick_seconds_count 100
shs_world_players 1000
shs_world_corrections_total{reason=\"too_fast\"} 2
shs_world_corrections_total{reason=\"blocked_path\"} 1
";

    #[test]
    fn parses_series_and_labels() {
        let m = Metrics::parse(TEXT);
        assert_eq!(m.get("shs_world_players"), 1000.0);
        assert_eq!(m.sum("shs_world_corrections_total"), 3.0);
        assert_eq!(
            m.by_label("shs_world_corrections_total", "reason")["too_fast"],
            2.0
        );
        assert_eq!(m.buckets("shs_world_tick_seconds").len(), 4);
    }

    #[test]
    fn histogram_quantiles_interpolate() {
        let end = Metrics::parse(TEXT);
        let h = HistogramDelta::between(&Metrics::default(), &end, "shs_world_tick_seconds");
        assert_eq!(h.count, 100.0);
        assert!((h.mean() - 0.0025).abs() < 1e-12);
        // Rank 50 falls 40/50 of the way through the 1-2 ms bucket.
        assert!((h.quantile(0.5) - 0.0018).abs() < 1e-9);
        assert!((h.quantile(0.99) - 0.015).abs() < 1e-9);
        // The slowest tick is beyond the last finite bucket.
        assert_eq!(h.quantile(1.0), 0.015);
        assert_eq!(h.max_bound(), f64::INFINITY);
        assert_eq!(h.count_above(0.015), 1.0);
        assert_eq!(h.count_above(0.001), 90.0);

        // A delta against an earlier scrape only counts the new observations.
        let start = Metrics::parse(&TEXT.replace("} 60", "} 50").replace("} 99", "} 89"));
        let d = HistogramDelta::between(&start, &end, "shs_world_tick_seconds");
        assert_eq!(d.buckets[1].1, 10.0);
    }

    #[test]
    fn parses_docker_stats() {
        let s = parse_docker_line(
            r#"{"Name":"shs-loadtest-server-1","CPUPerc":"42.17%","MemUsage":"183.4MiB / 3GiB"}"#,
        )
        .unwrap();
        assert_eq!(s.name, "shs-loadtest-server-1");
        assert_eq!(s.cpu_pct, 42.17);
        assert!((s.mem_mib - 183.4).abs() < 1e-9);
        assert_eq!(s.mem_limit_mib, 3072.0);
        assert!((parse_size_mib("800kB").unwrap() - 0.762939453125).abs() < 1e-9);
    }
}
