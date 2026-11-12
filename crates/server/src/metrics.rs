//! A small Prometheus text-format registry (exposition format 0.0.4).
//!
//! Metrics are registered once at startup and return `Arc` handles; updating a
//! handle is a lock-free atomic operation, so the world tick can record freely.
//! Only registration and rendering (`GET /metrics`) take the registry lock.
//!
//! Every series of a family must be registered with the same kind (and, for
//! histograms, the same buckets). Labels are fixed at registration: register
//! one handle per label set you need (keep cardinality small and bounded).
//!
//! ```
//! # use server::metrics::Registry;
//! let reg = Registry::default();
//! let players = reg.gauge("shs_world_players", "Characters in the castle world.");
//! let tick = reg.histogram("shs_world_tick_seconds", "World tick duration.", &[0.001, 0.005, 0.015]);
//! players.set(42.0);
//! tick.observe(0.004);
//! assert!(reg.render().contains("shs_world_players 42"));
//! ```

use std::fmt::Write as _;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

/// A monotonically increasing count.
#[derive(Debug, Default)]
pub struct Counter(AtomicU64);

impl Counter {
    pub fn inc(&self) {
        self.add(1);
    }
    pub fn add(&self, n: u64) {
        self.0.fetch_add(n, Ordering::Relaxed);
    }
    pub fn get(&self) -> u64 {
        self.0.load(Ordering::Relaxed)
    }
}

/// A value that can go up and down (stored as `f64` bits).
#[derive(Debug, Default)]
pub struct Gauge(AtomicU64);

impl Gauge {
    pub fn set(&self, v: f64) {
        self.0.store(v.to_bits(), Ordering::Relaxed);
    }
    pub fn add(&self, delta: f64) {
        atomic_add_f64(&self.0, delta);
    }
    pub fn inc(&self) {
        self.add(1.0);
    }
    pub fn dec(&self) {
        self.add(-1.0);
    }
    pub fn get(&self) -> f64 {
        f64::from_bits(self.0.load(Ordering::Relaxed))
    }
}

/// Observations counted into fixed buckets, plus their sum.
#[derive(Debug)]
pub struct Histogram {
    /// Upper bounds, strictly increasing; `+Inf` is implicit.
    bounds: Vec<f64>,
    /// Non-cumulative count per bucket; the last slot is the `+Inf` bucket.
    counts: Vec<AtomicU64>,
    sum: AtomicU64,
}

impl Histogram {
    fn new(bounds: &[f64]) -> Self {
        assert!(
            bounds.windows(2).all(|w| w[0] < w[1]) && bounds.iter().all(|b| b.is_finite()),
            "histogram bounds must be finite and strictly increasing"
        );
        Self {
            bounds: bounds.to_vec(),
            counts: (0..=bounds.len()).map(|_| AtomicU64::new(0)).collect(),
            sum: AtomicU64::new(0f64.to_bits()),
        }
    }

    pub fn observe(&self, v: f64) {
        let i = self.bounds.partition_point(|&b| b < v);
        self.counts[i].fetch_add(1, Ordering::Relaxed);
        atomic_add_f64(&self.sum, v);
    }

    /// Records a duration in seconds (the Prometheus base unit).
    pub fn observe_duration(&self, d: Duration) {
        self.observe(d.as_secs_f64());
    }

    pub fn count(&self) -> u64 {
        self.counts.iter().map(|c| c.load(Ordering::Relaxed)).sum()
    }

    pub fn sum(&self) -> f64 {
        f64::from_bits(self.sum.load(Ordering::Relaxed))
    }
}

fn atomic_add_f64(cell: &AtomicU64, delta: f64) {
    let mut cur = cell.load(Ordering::Relaxed);
    loop {
        let next = (f64::from_bits(cur) + delta).to_bits();
        match cell.compare_exchange_weak(cur, next, Ordering::Relaxed, Ordering::Relaxed) {
            Ok(_) => return,
            Err(seen) => cur = seen,
        }
    }
}

/// Default buckets for request/operation latencies in seconds.
pub const LATENCY_BUCKETS: &[f64] = &[
    0.0005, 0.001, 0.0025, 0.005, 0.01, 0.015, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5,
];

#[derive(Debug, Clone)]
enum Metric {
    Counter(Arc<Counter>),
    Gauge(Arc<Gauge>),
    Histogram(Arc<Histogram>),
}

impl Metric {
    fn type_name(&self) -> &'static str {
        match self {
            Metric::Counter(_) => "counter",
            Metric::Gauge(_) => "gauge",
            Metric::Histogram(_) => "histogram",
        }
    }
}

#[derive(Debug)]
struct Family {
    name: String,
    help: String,
    /// Rendered label pairs (`a="1",b="2"`, possibly empty) and the series.
    series: Vec<(String, Metric)>,
}

#[derive(Debug, Default)]
pub struct Registry {
    families: Mutex<Vec<Family>>,
}

impl Registry {
    pub fn counter(&self, name: &str, help: &str) -> Arc<Counter> {
        self.counter_with(name, help, &[])
    }

    pub fn counter_with(&self, name: &str, help: &str, labels: &[(&str, &str)]) -> Arc<Counter> {
        let c = Arc::new(Counter::default());
        self.register(name, help, labels, Metric::Counter(c.clone()));
        c
    }

    pub fn gauge(&self, name: &str, help: &str) -> Arc<Gauge> {
        self.gauge_with(name, help, &[])
    }

    pub fn gauge_with(&self, name: &str, help: &str, labels: &[(&str, &str)]) -> Arc<Gauge> {
        let g = Arc::new(Gauge::default());
        self.register(name, help, labels, Metric::Gauge(g.clone()));
        g
    }

    pub fn histogram(&self, name: &str, help: &str, bounds: &[f64]) -> Arc<Histogram> {
        self.histogram_with(name, help, bounds, &[])
    }

    pub fn histogram_with(
        &self,
        name: &str,
        help: &str,
        bounds: &[f64],
        labels: &[(&str, &str)],
    ) -> Arc<Histogram> {
        let h = Arc::new(Histogram::new(bounds));
        self.register(name, help, labels, Metric::Histogram(h.clone()));
        h
    }

    /// Panics on programming errors (bad names, kind mismatch, duplicate series),
    /// which surface at startup rather than as silently missing metrics.
    fn register(&self, name: &str, help: &str, labels: &[(&str, &str)], metric: Metric) {
        assert!(valid_name(name), "invalid metric name {name:?}");
        for (k, _) in labels {
            assert!(valid_name(k) && *k != "le", "invalid label name {k:?}");
        }
        let rendered = render_labels(labels);
        let mut families = self.families.lock().expect("metrics lock poisoned");
        match families.iter_mut().find(|f| f.name == name) {
            Some(family) => {
                let kind = family.series[0].1.type_name();
                assert_eq!(
                    kind,
                    metric.type_name(),
                    "metric {name} registered as {kind}"
                );
                assert!(
                    family.series.iter().all(|(l, _)| *l != rendered),
                    "metric {name}{{{rendered}}} registered twice"
                );
                family.series.push((rendered, metric));
            }
            None => families.push(Family {
                name: name.to_owned(),
                help: help.to_owned(),
                series: vec![(rendered, metric)],
            }),
        }
    }

    /// The whole registry in Prometheus text exposition format.
    pub fn render(&self) -> String {
        let families = self.families.lock().expect("metrics lock poisoned");
        let mut out = String::with_capacity(families.len() * 128);
        for f in families.iter() {
            let kind = f.series[0].1.type_name();
            let _ = writeln!(out, "# HELP {} {}", f.name, escape_help(&f.help));
            let _ = writeln!(out, "# TYPE {} {kind}", f.name);
            for (labels, metric) in &f.series {
                match metric {
                    Metric::Counter(c) => sample(&mut out, &f.name, "", labels, c.get() as f64),
                    Metric::Gauge(g) => sample(&mut out, &f.name, "", labels, g.get()),
                    Metric::Histogram(h) => render_histogram(&mut out, &f.name, labels, h),
                }
            }
        }
        out
    }
}

fn render_histogram(out: &mut String, name: &str, labels: &str, h: &Histogram) {
    let sep = if labels.is_empty() { "" } else { "," };
    let mut cumulative = 0u64;
    for (i, count) in h.counts.iter().enumerate() {
        cumulative += count.load(Ordering::Relaxed);
        let le = match h.bounds.get(i) {
            Some(b) => format_value(*b),
            None => "+Inf".to_owned(),
        };
        let bucket_labels = format!("{labels}{sep}le=\"{le}\"");
        sample(out, name, "_bucket", &bucket_labels, cumulative as f64);
    }
    sample(out, name, "_sum", labels, h.sum());
    // The +Inf bucket equals the count; reuse it so the two always agree.
    sample(out, name, "_count", labels, cumulative as f64);
}

fn sample(out: &mut String, name: &str, suffix: &str, labels: &str, value: f64) {
    if labels.is_empty() {
        let _ = writeln!(out, "{name}{suffix} {}", format_value(value));
    } else {
        let _ = writeln!(out, "{name}{suffix}{{{labels}}} {}", format_value(value));
    }
}

fn format_value(v: f64) -> String {
    if v.is_nan() {
        "NaN".into()
    } else if v.is_infinite() {
        if v > 0.0 {
            "+Inf".into()
        } else {
            "-Inf".into()
        }
    } else {
        // `Display` for f64 prints integers without a fraction and never uses exponents.
        v.to_string()
    }
}

fn render_labels(labels: &[(&str, &str)]) -> String {
    let mut out = String::new();
    for (i, (k, v)) in labels.iter().enumerate() {
        if i > 0 {
            out.push(',');
        }
        let _ = write!(out, "{k}=\"");
        for ch in v.chars() {
            match ch {
                '\\' => out.push_str("\\\\"),
                '"' => out.push_str("\\\""),
                '\n' => out.push_str("\\n"),
                c => out.push(c),
            }
        }
        out.push('"');
    }
    out
}

fn escape_help(help: &str) -> String {
    help.replace('\\', "\\\\").replace('\n', "\\n")
}

fn valid_name(name: &str) -> bool {
    let mut chars = name.chars();
    matches!(chars.next(), Some(c) if c.is_ascii_alphabetic() || c == '_' || c == ':')
        && chars.all(|c| c.is_ascii_alphanumeric() || c == '_' || c == ':')
}

/// The server's own metrics, plus the registry other modules (the world, instances)
/// register theirs in.
#[derive(Debug)]
pub struct Metrics {
    pub registry: Registry,
    /// HTTP responses by status class, indexed by `status / 100 - 1` (1xx..5xx).
    pub http_responses: [Arc<Counter>; 5],
    pub http_duration: Arc<Histogram>,
    pub registrations: Arc<Counter>,
    pub logins_ok: Arc<Counter>,
    pub logins_failed: Arc<Counter>,
    pub rate_limited: Arc<Counter>,
}

impl Default for Metrics {
    fn default() -> Self {
        Self::new()
    }
}

impl Metrics {
    pub fn new() -> Self {
        let registry = Registry::default();
        let http_responses = ["1xx", "2xx", "3xx", "4xx", "5xx"].map(|class| {
            registry.counter_with(
                "shs_http_responses_total",
                "HTTP responses sent, by status class.",
                &[("class", class)],
            )
        });
        let http_duration = registry.histogram(
            "shs_http_request_duration_seconds",
            "Time to handle an HTTP request.",
            LATENCY_BUCKETS,
        );
        let registrations =
            registry.counter("shs_auth_registrations_total", "Accounts registered.");
        let logins_ok = registry.counter_with(
            "shs_auth_logins_total",
            "Login attempts, by result.",
            &[("result", "ok")],
        );
        let logins_failed = registry.counter_with(
            "shs_auth_logins_total",
            "Login attempts, by result.",
            &[("result", "failed")],
        );
        let rate_limited = registry.counter(
            "shs_auth_rate_limited_total",
            "Auth requests rejected by the per-IP rate limit.",
        );
        Self {
            registry,
            http_responses,
            http_duration,
            registrations,
            logins_ok,
            logins_failed,
            rate_limited,
        }
    }

    pub fn record_http(&self, status: u16, elapsed: Duration) {
        if let Some(c) = (status / 100)
            .checked_sub(1)
            .and_then(|i| self.http_responses.get(usize::from(i)))
        {
            c.inc();
        }
        self.http_duration.observe_duration(elapsed);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn renders_counters_and_gauges() {
        let reg = Registry::default();
        let c = reg.counter("jobs_total", "Jobs done.");
        let g = reg.gauge("queue_depth", "Queued jobs.");
        c.add(3);
        g.set(2.5);
        g.dec();
        let out = reg.render();
        assert!(
            out.contains("# HELP jobs_total Jobs done.\n# TYPE jobs_total counter\njobs_total 3\n")
        );
        assert!(out.contains("# TYPE queue_depth gauge\nqueue_depth 1.5\n"));
    }

    #[test]
    fn labelled_series_share_one_family_header() {
        let reg = Registry::default();
        let a = reg.counter_with("hits_total", "Hits.", &[("kind", "a")]);
        let b = reg.counter_with("hits_total", "Hits.", &[("kind", "b\"q")]);
        a.inc();
        b.add(2);
        let out = reg.render();
        assert_eq!(out.matches("# TYPE hits_total").count(), 1);
        assert!(out.contains("hits_total{kind=\"a\"} 1\n"));
        assert!(out.contains("hits_total{kind=\"b\\\"q\"} 2\n"));
    }

    #[test]
    fn histogram_buckets_are_cumulative() {
        let reg = Registry::default();
        let h = reg.histogram_with(
            "tick_seconds",
            "Tick.",
            &[0.01, 0.1],
            &[("world", "castle")],
        );
        for v in [0.005, 0.01, 0.05, 3.0] {
            h.observe(v);
        }
        let out = reg.render();
        assert!(
            out.contains("tick_seconds_bucket{world=\"castle\",le=\"0.01\"} 2\n"),
            "{out}"
        );
        assert!(out.contains("tick_seconds_bucket{world=\"castle\",le=\"0.1\"} 3\n"));
        assert!(out.contains("tick_seconds_bucket{world=\"castle\",le=\"+Inf\"} 4\n"));
        assert!(out.contains("tick_seconds_count{world=\"castle\"} 4\n"));
        assert!((h.sum() - 3.065).abs() < 1e-9);
    }

    #[test]
    #[should_panic(expected = "registered as counter")]
    fn kind_mismatch_panics() {
        let reg = Registry::default();
        reg.counter("x", "x");
        reg.gauge("x", "x");
    }

    #[test]
    #[should_panic(expected = "registered twice")]
    fn duplicate_series_panics() {
        let reg = Registry::default();
        reg.counter("x", "x");
        reg.counter("x", "x");
    }

    #[test]
    fn concurrent_updates_are_not_lost() {
        let reg = Registry::default();
        let g = reg.gauge("g", "g");
        let h = reg.histogram("h", "h", &[1.0]);
        std::thread::scope(|s| {
            for _ in 0..4 {
                s.spawn(|| {
                    for _ in 0..1000 {
                        g.inc();
                        h.observe(0.5);
                    }
                });
            }
        });
        assert_eq!(g.get(), 4000.0);
        assert_eq!(h.count(), 4000);
        assert_eq!(h.sum(), 2000.0);
    }

    #[test]
    fn server_metrics_render() {
        let m = Metrics::new();
        m.record_http(204, Duration::from_millis(3));
        m.record_http(429, Duration::from_millis(1));
        let out = m.registry.render();
        assert!(out.contains("shs_http_responses_total{class=\"2xx\"} 1\n"));
        assert!(out.contains("shs_http_responses_total{class=\"4xx\"} 1\n"));
        assert!(out.contains("shs_http_request_duration_seconds_count 2\n"));
    }
}
