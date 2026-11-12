//! The players list and the player card under load. With
//! `--players-pct P` (default 20) that share of the bots, at sign-in and
//! then every `--players-every-secs S` (default 120; 0: once), searches the
//! online players list for two letters of its own name
//! (`GET /api/players?q=..`), reads the second page when there is one, and
//! opens one listed player's card (`GET /api/players/{name}/card`), as the
//! browser's players window and card do. The summary reports the requests,
//! errors and latency of both endpoints.

use std::collections::BTreeMap;
use std::time::Duration;

use rand::rngs::StdRng;
use rand::{Rng, SeedableRng};
use serde::Serialize;
use tokio::sync::watch;
use tokio::time::{Instant, sleep_until};

use crate::bot::stopped;
use crate::http::Target;

/// The default share of bots that browse the players list.
pub const DEFAULT_PCT: f64 = 20.0;
/// The default time between a browsing bot's looks.
pub const DEFAULT_EVERY_SECS: f64 = 120.0;

/// What the bots did with the players list and cards (summed over bots).
#[derive(Debug, Clone, Default, Serialize)]
pub struct PlayerStats {
    /// Browses started (one search, maybe a second page, maybe a card).
    pub browses: u64,
    /// List pages answered, players they listed, cards answered.
    pub pages: u64,
    pub listed: u64,
    pub cards: u64,
    /// Failed requests, by status or error text.
    pub errors: u64,
    pub error_reasons: BTreeMap<String, u64>,
    #[serde(skip)]
    pub list_ms: Vec<f64>,
    #[serde(skip)]
    pub card_ms: Vec<f64>,
}

impl PlayerStats {
    pub fn add(&mut self, o: &PlayerStats) {
        self.browses += o.browses;
        self.pages += o.pages;
        self.listed += o.listed;
        self.cards += o.cards;
        self.errors += o.errors;
        for (k, v) in &o.error_reasons {
            *self.error_reasons.entry(k.clone()).or_default() += v;
        }
        self.list_ms.extend_from_slice(&o.list_ms);
        self.card_ms.extend_from_slice(&o.card_ms);
    }

    fn fail(&mut self, reason: String) {
        self.errors += 1;
        *self.error_reasons.entry(reason).or_default() += 1;
    }
}

/// Whether bot `index` browses with `pct` percent of the bots browsing
/// (spread over the indices, so every role has some).
pub fn browses(index: usize, pct: f64) -> bool {
    ((index * 61) % 100) as f64 + 0.5 < pct
}

/// Two letters of `name` to search for: its letters run (the name prefix)
/// at a seeded offset, so the searches match many bots and page.
pub fn search_for(name: &str, rng: &mut impl Rng) -> String {
    let letters: Vec<char> = name
        .chars()
        .take_while(|c| c.is_ascii_alphabetic())
        .collect();
    if letters.len() < 2 {
        return name.chars().take(2).collect();
    }
    let i = rng.gen_range(0..letters.len() - 1);
    letters[i..i + 2].iter().collect()
}

/// One list page: (names listed, the next cursor).
async fn page(
    target: &Target,
    token: &str,
    path: &str,
    stats: &mut PlayerStats,
) -> Option<(Vec<String>, Option<String>)> {
    let sent = Instant::now();
    let res = target.get_as(token, path).await;
    let ms = sent.elapsed().as_secs_f64() * 1000.0;
    let r = match res {
        Ok(r) if r.is_success() => r,
        Ok(r) => {
            stats.fail(format!("list status {}", r.status));
            return None;
        }
        Err(e) => {
            stats.fail(e);
            return None;
        }
    };
    let Ok(v) = serde_json::from_slice::<serde_json::Value>(&r.body) else {
        stats.fail("list body".into());
        return None;
    };
    stats.pages += 1;
    stats.list_ms.push(ms);
    let names: Vec<String> = v["items"]
        .as_array()
        .map(|items| {
            items
                .iter()
                .filter_map(|i| i["name"].as_str().map(str::to_owned))
                .collect()
        })
        .unwrap_or_default();
    stats.listed += names.len() as u64;
    Some((names, v["next"].as_str().map(str::to_owned)))
}

/// One look: a search, its second page if any, one card.
pub async fn browse(
    target: &Target,
    token: &str,
    q: &str,
    rng: &mut StdRng,
    stats: &mut PlayerStats,
) {
    stats.browses += 1;
    let Some((mut names, next)) = page(target, token, &format!("/api/players?q={q}"), stats).await
    else {
        return;
    };
    if let Some(next) = next
        && let Some((more, _)) = page(
            target,
            token,
            &format!("/api/players?q={q}&after={next}"),
            stats,
        )
        .await
    {
        names.extend(more);
    }
    if names.is_empty() {
        return;
    }
    let name = &names[rng.gen_range(0..names.len())];
    let sent = Instant::now();
    match target
        .get_as(token, &format!("/api/players/{name}/card"))
        .await
    {
        Ok(r) if r.is_success() => {
            stats.cards += 1;
            stats.card_ms.push(sent.elapsed().as_secs_f64() * 1000.0);
        }
        Ok(r) => stats.fail(format!("card status {}", r.status)),
        Err(e) => stats.fail(e),
    }
}

/// Browses at once and then every `every` (`None`: once) until the run
/// stops.
pub async fn run(
    target: Target,
    token: String,
    name: String,
    index: usize,
    every: Option<Duration>,
    mut stop: watch::Receiver<bool>,
) -> PlayerStats {
    let mut out = PlayerStats::default();
    let mut rng = StdRng::seed_from_u64(0x9a7e_0000 + index as u64);
    let mut next = Instant::now();
    loop {
        tokio::select! {
            _ = sleep_until(next) => {}
            _ = stopped(&mut stop) => break,
        }
        let q = search_for(&name, &mut rng);
        browse(&target, &token, &q, &mut rng, &mut out).await;
        match every {
            Some(e) => next += e,
            None => break,
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_share_of_the_bots_browses() {
        let n = (0..1000).filter(|&i| browses(i, 20.0)).count();
        assert_eq!(n, 200);
        assert_eq!((0..1000).filter(|&i| browses(i, 0.0)).count(), 0);
        assert_eq!((0..1000).filter(|&i| browses(i, 100.0)).count(), 1000);
    }

    #[test]
    fn searches_are_two_letters_of_the_prefix() {
        let mut rng = StdRng::seed_from_u64(1);
        for _ in 0..20 {
            let q = search_for("lts0042", &mut rng);
            assert!(["lt", "ts"].contains(&q.as_str()), "{q}");
        }
        assert_eq!(search_for("a0042", &mut rng), "a0");
    }
}
