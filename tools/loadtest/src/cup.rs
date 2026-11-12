//! The House Cup under load. With
//! `--cup-poll-secs S` every bot asks `GET /api/house-cup` every S seconds
//! (from a random moment of its first interval, so the requests spread), as
//! the browser's standings panel, hourglasses and banners do; the summary
//! reports the requests, errors and latency in the measured window. With
//! `--cup-close`, one bot of its own (`<prefix>cup`) closes the running cup
//! week with `POST /api/dev/house-cup/close` in the middle of the window, so
//! the summary can show the tick time of that interval next to the rest
//! (the close and its reward mails run off the world thread).

use std::collections::BTreeMap;
use std::time::Duration;

use serde::Serialize;
use tokio::sync::watch;
use tokio::time::{Instant, sleep_until};

use crate::bot::stopped;
use crate::http::Target;

/// One bot's cup requests.
#[derive(Debug, Default, Clone)]
pub struct CupPolls {
    pub sent: u64,
    pub errors: u64,
    /// By status or error text.
    pub error_reasons: BTreeMap<String, u64>,
    /// Successful requests: (seconds into the run when sent, latency ms).
    pub samples: Vec<(f64, f64)>,
}

impl CupPolls {
    pub fn add(&mut self, o: &CupPolls) {
        self.sent += o.sent;
        self.errors += o.errors;
        for (k, v) in &o.error_reasons {
            *self.error_reasons.entry(k.clone()).or_default() += v;
        }
        self.samples.extend_from_slice(&o.samples);
    }
}

/// Asks for the cup every `every` (the first time after `delay`) until the
/// run stops.
pub async fn poll(
    target: Target,
    token: String,
    every: Duration,
    delay: Duration,
    epoch: Instant,
    mut stop: watch::Receiver<bool>,
) -> CupPolls {
    let mut out = CupPolls::default();
    let mut next = Instant::now() + delay;
    loop {
        tokio::select! {
            _ = sleep_until(next) => {}
            _ = stopped(&mut stop) => break,
        }
        next += every;
        let sent = Instant::now();
        let res = target.get_as(&token, "/api/house-cup").await;
        let ms = sent.elapsed().as_secs_f64() * 1000.0;
        out.sent += 1;
        let failed = match res {
            Ok(r) if r.is_success() => {
                out.samples
                    .push((sent.duration_since(epoch).as_secs_f64(), ms));
                None
            }
            Ok(r) => Some(format!("status {}", r.status)),
            Err(e) => Some(e),
        };
        if let Some(reason) = failed {
            out.errors += 1;
            *out.error_reasons.entry(reason).or_default() += 1;
        }
    }
    out
}

/// The one dev close of a run.
#[derive(Debug, Clone, Default, Serialize)]
pub struct CupClose {
    /// Seconds into the run when it was sent.
    pub at_secs: f64,
    pub ms: f64,
    pub status: u16,
    /// The answer: `closed`, the winners and how many were mailed.
    pub closed: Option<bool>,
    pub winners: Vec<String>,
    pub mailed: Option<u64>,
    pub error: Option<String>,
}

/// Closes the running cup week at `at` through a session of the bot
/// `<prefix>cup` (made before waiting, so only the close itself is timed).
/// `None` if the run stopped first.
pub async fn close_at(
    target: Target,
    prefix: String,
    at: Instant,
    epoch: Instant,
    mut stop: watch::Receiver<bool>,
) -> Option<CupClose> {
    let session = target
        .post_json(
            "/api/dev/bot-session",
            &serde_json::json!({ "name": format!("{prefix}cup") }),
        )
        .await;
    let token = match session {
        Ok(r) if r.is_success() => serde_json::from_slice::<serde_json::Value>(&r.body)
            .ok()
            .and_then(|v| v["token"].as_str().map(str::to_owned)),
        _ => None,
    };
    tokio::select! {
        _ = sleep_until(at) => {}
        _ = stopped(&mut stop) => return None,
    }
    let at_secs = Instant::now().duration_since(epoch).as_secs_f64();
    let Some(token) = token else {
        return Some(CupClose {
            at_secs,
            error: Some("no session for the cup bot".into()),
            ..CupClose::default()
        });
    };
    let sent = Instant::now();
    let res = target
        .post_json_as(&token, "/api/dev/house-cup/close", &serde_json::json!({}))
        .await;
    let ms = sent.elapsed().as_secs_f64() * 1000.0;
    Some(match res {
        Ok(r) => {
            let body: serde_json::Value = serde_json::from_slice(&r.body).unwrap_or_default();
            CupClose {
                at_secs,
                ms,
                status: r.status,
                closed: body["closed"].as_bool(),
                winners: body["winners"]
                    .as_array()
                    .map(|w| {
                        w.iter()
                            .filter_map(|h| h.as_str().map(str::to_owned))
                            .collect()
                    })
                    .unwrap_or_default(),
                mailed: body["mailed"].as_u64(),
                error: (!r.is_success()).then(|| String::from_utf8_lossy(&r.body).into_owned()),
            }
        }
        Err(e) => CupClose {
            at_secs,
            ms,
            error: Some(e),
            ..CupClose::default()
        },
    })
}
