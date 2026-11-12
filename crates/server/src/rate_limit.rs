//! In-memory rate limiting. Token buckets ([`RateLimiter`]): per IP address
//! for the auth endpoints ([`limit_auth`]), per account for writes
//! ([`AccountKey`], used by `characters::extract`). Fixed windows
//! ([`WindowLimiter`], [`AccountLimits`]): per account for the emails the
//! auth endpoints send and for failed passwords, where the count clears
//! completely once the window has passed. A limiter is generic over its
//! [`BucketKey`].
//!
//! One game server is one process, so an in-process map is enough; nothing
//! needs to be shared between machines. IPv6 clients are keyed by their /64
//! prefix, since a single host usually controls a whole /64.
//!
//! Client IPs come from the TCP peer address (`ConnectInfo`). Behind an edge
//! that names the client in a header of its own (`SHS_CLIENT_IP_HEADER`,
//! `cf-connecting-ip` behind a Cloudflare Tunnel) they come from that header;
//! behind a plain reverse proxy (`SHS_TRUST_PROXY=1`) from the last
//! `X-Forwarded-For` entry, the one the proxy itself appended (see `client_ip`).

use std::collections::HashMap;
use std::hash::Hash;
use std::net::{IpAddr, Ipv6Addr, SocketAddr};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use axum::extract::{ConnectInfo, Request, State};
use axum::http::{HeaderMap, HeaderName};
use axum::middleware::Next;
use axum::response::{IntoResponse, Response};

use crate::AppState;
use crate::http::ApiError;

const X_FORWARDED_FOR: HeaderName = HeaderName::from_static("x-forwarded-for");

/// A bucket holds up to `burst` tokens and regains one every `per`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct RateLimit {
    pub burst: u32,
    pub per: Duration,
}

#[derive(Debug, Clone, Copy)]
struct Bucket {
    tokens: f64,
    updated: Instant,
}

/// Prune idle buckets once the map grows past this many keys.
const PRUNE_THRESHOLD: usize = 10_000;

/// What a limiter keys its buckets by.
pub trait BucketKey: Copy + Eq + Hash {
    /// The bucket this key spends from: itself, unless keys share buckets
    /// (IPv6 addresses share one per /64).
    fn bucket(self) -> Self {
        self
    }
}

impl BucketKey for IpAddr {
    fn bucket(self) -> Self {
        bucket_key(self)
    }
}

/// One account (`accounts.id`): per-account write budgets.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct AccountKey(pub i64);

impl BucketKey for AccountKey {}

#[derive(Debug)]
pub struct RateLimiter<K = IpAddr> {
    limit: RateLimit,
    buckets: Mutex<HashMap<K, Bucket>>,
}

impl<K: BucketKey> RateLimiter<K> {
    pub fn new(limit: RateLimit) -> Self {
        assert!(
            limit.burst > 0 && !limit.per.is_zero(),
            "rate limit must allow requests"
        );
        Self {
            limit,
            buckets: Mutex::new(HashMap::new()),
        }
    }

    /// Takes one token for `key` at time `now`. `Err` carries how long until
    /// the next token is available.
    pub fn check_at(&self, key: K, now: Instant) -> Result<(), Duration> {
        let key = key.bucket();
        let burst = f64::from(self.limit.burst);
        let per = self.limit.per.as_secs_f64();
        let mut buckets = self.buckets.lock().expect("rate limiter lock poisoned");

        if buckets.len() > PRUNE_THRESHOLD {
            // A bucket idle long enough to be full again carries no information.
            let full_after = self.limit.per * self.limit.burst;
            buckets.retain(|_, b| now.duration_since(b.updated) < full_after);
        }

        let bucket = buckets.entry(key).or_insert(Bucket {
            tokens: burst,
            updated: now,
        });
        let elapsed = now.saturating_duration_since(bucket.updated).as_secs_f64();
        bucket.tokens = (bucket.tokens + elapsed / per).min(burst);
        bucket.updated = now;

        if bucket.tokens >= 1.0 {
            bucket.tokens -= 1.0;
            Ok(())
        } else {
            Err(Duration::from_secs_f64((1.0 - bucket.tokens) * per))
        }
    }

    pub fn check(&self, key: K) -> Result<(), Duration> {
        self.check_at(key, Instant::now())
    }
}

/// IPv4 addresses are keyed as-is; IPv6 by /64 (IPv4-mapped v6 as the v4 address).
fn bucket_key(ip: IpAddr) -> IpAddr {
    match ip {
        IpAddr::V4(_) => ip,
        IpAddr::V6(v6) => {
            if let Some(v4) = v6.to_ipv4_mapped() {
                return IpAddr::V4(v4);
            }
            let s = v6.segments();
            IpAddr::V6(Ipv6Addr::new(s[0], s[1], s[2], s[3], 0, 0, 0, 0))
        }
    }
}

/// Axum middleware applying `AppState::auth_limiter` to the routes it wraps.
pub async fn limit_auth(
    State(state): State<AppState>,
    ConnectInfo(peer): ConnectInfo<SocketAddr>,
    req: Request,
    next: Next,
) -> Response {
    let ip = client_ip(
        peer,
        req.headers(),
        state.config.client_ip_header.as_ref(),
        state.config.trust_proxy,
    );
    match state.auth_limiter.check(ip) {
        Ok(()) => next.run(req).await,
        Err(wait) => {
            state.metrics.rate_limited.inc();
            ApiError::too_many(
                "rate_limited",
                "Too many attempts. Please wait a moment and try again.",
                wait,
            )
            .into_response()
        }
    }
}

/// The client address used as the rate-limit key: the edge's client header
/// when one is configured, else behind a trusted proxy the last
/// `X-Forwarded-For` entry, else the TCP peer. Only the last entry: the proxy
/// appends the address it saw to whatever the client sent, so earlier entries
/// are the client's own claims.
fn client_ip(
    peer: SocketAddr,
    headers: &HeaderMap,
    client_header: Option<&HeaderName>,
    trust_proxy: bool,
) -> IpAddr {
    let from_edge = client_header
        .and_then(|name| headers.get(name))
        .and_then(|v| v.to_str().ok())
        .and_then(|v| v.split(',').next())
        .and_then(|ip| ip.trim().parse().ok());
    if let Some(ip) = from_edge {
        return ip;
    }
    if trust_proxy {
        let forwarded = headers
            .get_all(X_FORWARDED_FOR)
            .iter()
            .filter_map(|v| v.to_str().ok())
            .flat_map(|v| v.split(','))
            .last()
            .and_then(|ip| ip.trim().parse().ok());
        if let Some(ip) = forwarded {
            return ip;
        }
    }
    peer.ip()
}

/// Shared handle type stored in `AppState`.
pub type SharedLimiter = Arc<RateLimiter>;

/// At most `max` counted events per `window`, the window opening with the
/// first event. Once it has passed, the count starts again from zero: unlike
/// a token bucket nothing trickles back early, and after the wait the whole
/// allowance is back.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct FixedWindow {
    pub max: u32,
    pub window: Duration,
}

#[derive(Debug, Clone, Copy)]
struct Window {
    count: u32,
    opened: Instant,
}

/// A fixed-window counter per key ([`FixedWindow`]). Besides counting
/// ([`take`](Self::take)) it can look without counting
/// ([`check`](Self::check)) and forget a key ([`clear`](Self::clear): a
/// successful sign-in clears its attempts).
#[derive(Debug)]
pub struct WindowLimiter<K = AccountKey> {
    limit: FixedWindow,
    windows: Mutex<HashMap<K, Window>>,
    /// Milliseconds the limiter's clock runs ahead of `Instant::now()`
    /// ([`WindowLimiter::advance`], tests only).
    ahead_ms: AtomicU64,
}

impl<K: BucketKey> WindowLimiter<K> {
    pub fn new(limit: FixedWindow) -> Self {
        assert!(
            limit.max > 0 && !limit.window.is_zero(),
            "window limit must allow events"
        );
        Self {
            limit,
            windows: Mutex::new(HashMap::new()),
            ahead_ms: AtomicU64::new(0),
        }
    }

    /// The limiter's clock: `Instant::now()`, plus any [`WindowLimiter::advance`].
    fn now(&self) -> Instant {
        Instant::now() + Duration::from_millis(self.ahead_ms.load(Ordering::Relaxed))
    }

    /// Moves the clock of [`check`](Self::check) and [`take`](Self::take)
    /// forward by `by`, so tests can let a window
    /// pass without waiting for it.
    pub fn advance(&self, by: Duration) {
        let ms = u64::try_from(by.as_millis()).unwrap_or(u64::MAX);
        self.ahead_ms.fetch_add(ms, Ordering::Relaxed);
    }

    fn lock(&self, now: Instant) -> std::sync::MutexGuard<'_, HashMap<K, Window>> {
        let mut windows = self.windows.lock().expect("window limiter lock poisoned");
        if windows.len() > PRUNE_THRESHOLD {
            // A window that has passed carries no information.
            let window = self.limit.window;
            windows.retain(|_, w| now.saturating_duration_since(w.opened) < window);
        }
        windows
    }

    /// The live window for `key` at `now`, dropping one that has passed.
    fn live(&self, windows: &mut HashMap<K, Window>, key: K, now: Instant) -> Option<Window> {
        let w = *windows.get(&key)?;
        if now.saturating_duration_since(w.opened) >= self.limit.window {
            windows.remove(&key);
            return None;
        }
        Some(w)
    }

    fn wait(&self, w: Window, now: Instant) -> Duration {
        (w.opened + self.limit.window).saturating_duration_since(now)
    }

    /// `Err` with the time until the window ends when `key` has used up its
    /// allowance; counts nothing.
    pub fn check_at(&self, key: K, now: Instant) -> Result<(), Duration> {
        let key = key.bucket();
        let mut windows = self.lock(now);
        match self.live(&mut windows, key, now) {
            Some(w) if w.count >= self.limit.max => Err(self.wait(w, now)),
            _ => Ok(()),
        }
    }

    /// Counts one event for `key` unless the allowance is used up (then
    /// `Err` with the time until the window ends, and nothing is counted).
    pub fn take_at(&self, key: K, now: Instant) -> Result<(), Duration> {
        let key = key.bucket();
        let mut windows = self.lock(now);
        let w = self.live(&mut windows, key, now);
        if let Some(w) = w.filter(|w| w.count >= self.limit.max) {
            return Err(self.wait(w, now));
        }
        windows.insert(key, Self::counted(w, now));
        Ok(())
    }

    /// `w` with one more event, or a window opening at `now` with one.
    fn counted(w: Option<Window>, now: Instant) -> Window {
        match w {
            Some(w) => Window {
                count: w.count.saturating_add(1),
                ..w
            },
            None => Window {
                count: 1,
                opened: now,
            },
        }
    }

    /// Forgets `key`'s window (a successful sign-in clears its failures).
    pub fn clear(&self, key: K) {
        let key = key.bucket();
        self.windows
            .lock()
            .expect("window limiter lock poisoned")
            .remove(&key);
    }

    pub fn check(&self, key: K) -> Result<(), Duration> {
        self.check_at(key, self.now())
    }

    pub fn take(&self, key: K) -> Result<(), Duration> {
        self.take_at(key, self.now())
    }

    /// Keys with a window, live or passed but not yet pruned (tests).
    pub fn len(&self) -> usize {
        self.windows
            .lock()
            .expect("window limiter lock poisoned")
            .len()
    }

    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }
}

/// The per-account limits of the auth endpoints, kept in `AppState`:
/// emails sent on request (password reset links and re-sent verification
/// links, one shared count) and failed password attempts at sign-in. In memory, so a restart
/// clears them.
#[derive(Debug)]
pub struct AccountLimits {
    pub emails: WindowLimiter<AccountKey>,
    pub login_failures: WindowLimiter<AccountKey>,
}

impl AccountLimits {
    pub fn new(emails: FixedWindow, login_failures: FixedWindow) -> Self {
        Self {
            emails: WindowLimiter::new(emails),
            login_failures: WindowLimiter::new(login_failures),
        }
    }
}

/// Whole minutes until `wait` is over, rounded up (at least one).
pub fn minutes_left(wait: Duration) -> u64 {
    wait.as_secs().div_ceil(60).max(1)
}

/// "Try again in 7 minutes." / "Try again in 1 minute."
pub fn try_again_in(wait: Duration) -> String {
    match minutes_left(wait) {
        1 => "Try again in 1 minute.".to_owned(),
        n => format!("Try again in {n} minutes."),
    }
}

/// `429 too_many_emails`: the account has been sent its allowance of emails.
pub fn too_many_emails(wait: Duration) -> ApiError {
    ApiError::too_many(
        "too_many_emails",
        format!("Too many emails sent. {}", try_again_in(wait)),
        wait,
    )
}

/// `429 too_many_attempts`: too many wrong passwords for the account.
pub fn too_many_attempts(wait: Duration) -> ApiError {
    ApiError::too_many(
        "too_many_attempts",
        format!("Too many sign-in attempts. {}", try_again_in(wait)),
        wait,
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::http::HeaderValue;

    const LIMIT: RateLimit = RateLimit {
        burst: 3,
        per: Duration::from_secs(10),
    };

    #[test]
    fn client_ip_trusts_only_the_proxys_entry() {
        let peer = SocketAddr::from(([10, 0, 0, 2], 4000));
        let mut headers = HeaderMap::new();
        headers.insert(
            X_FORWARDED_FOR,
            HeaderValue::from_static("6.6.6.6, 203.0.113.9"),
        );
        assert_eq!(client_ip(peer, &headers, None, true), ip("203.0.113.9"));
        // Without a proxy the header is the client's to forge: ignored.
        assert_eq!(client_ip(peer, &headers, None, false), ip("10.0.0.2"));
        // No or garbled header: the peer.
        assert_eq!(
            client_ip(peer, &HeaderMap::new(), None, true),
            ip("10.0.0.2")
        );
        headers.insert(X_FORWARDED_FOR, HeaderValue::from_static("nonsense"));
        assert_eq!(client_ip(peer, &headers, None, true), ip("10.0.0.2"));
    }

    #[test]
    fn client_ip_prefers_the_edges_header() {
        let peer = SocketAddr::from(([172, 18, 0, 3], 4000));
        let cf = HeaderName::from_static("cf-connecting-ip");
        let mut headers = HeaderMap::new();
        headers.insert(cf.clone(), HeaderValue::from_static("198.51.100.7"));
        // kamal-proxy appends cloudflared's address: not the client.
        headers.insert(
            X_FORWARDED_FOR,
            HeaderValue::from_static("198.51.100.7, 172.18.0.5"),
        );
        assert_eq!(
            client_ip(peer, &headers, Some(&cf), true),
            ip("198.51.100.7")
        );
        assert_eq!(client_ip(peer, &headers, None, true), ip("172.18.0.5"));
        // Header missing or garbled: the next rule.
        headers.insert(cf.clone(), HeaderValue::from_static("garbage"));
        assert_eq!(client_ip(peer, &headers, Some(&cf), true), ip("172.18.0.5"));
        assert_eq!(
            client_ip(peer, &HeaderMap::new(), Some(&cf), false),
            ip("172.18.0.3")
        );
        // IPv6 clients come through as well.
        headers.insert(cf.clone(), HeaderValue::from_static("2001:db8::1"));
        assert_eq!(
            client_ip(peer, &headers, Some(&cf), false),
            ip("2001:db8::1")
        );
    }

    fn ip(s: &str) -> IpAddr {
        s.parse().unwrap()
    }

    #[test]
    fn allows_burst_then_limits() {
        let rl = RateLimiter::new(LIMIT);
        let t = Instant::now();
        for _ in 0..3 {
            assert!(rl.check_at(ip("10.0.0.1"), t).is_ok());
        }
        let wait = rl.check_at(ip("10.0.0.1"), t).unwrap_err();
        assert!(wait <= Duration::from_secs(10) && wait > Duration::from_secs(9));
    }

    #[test]
    fn refills_over_time() {
        let rl = RateLimiter::new(LIMIT);
        let t = Instant::now();
        for _ in 0..3 {
            rl.check_at(ip("10.0.0.1"), t).unwrap();
        }
        assert!(
            rl.check_at(ip("10.0.0.1"), t + Duration::from_secs(5))
                .is_err()
        );
        assert!(
            rl.check_at(ip("10.0.0.1"), t + Duration::from_secs(11))
                .is_ok()
        );
        assert!(
            rl.check_at(ip("10.0.0.1"), t + Duration::from_secs(11))
                .is_err()
        );
    }

    #[test]
    fn buckets_are_per_ip() {
        let rl = RateLimiter::new(LIMIT);
        let t = Instant::now();
        for _ in 0..3 {
            rl.check_at(ip("10.0.0.1"), t).unwrap();
        }
        assert!(rl.check_at(ip("10.0.0.2"), t).is_ok());
    }

    #[test]
    fn ipv6_shares_a_bucket_per_64() {
        let rl = RateLimiter::new(LIMIT);
        let t = Instant::now();
        for i in 0..3 {
            rl.check_at(ip(&format!("2001:db8:1:2::{i}")), t).unwrap();
        }
        assert!(rl.check_at(ip("2001:db8:1:2:ffff::1"), t).is_err());
        assert!(rl.check_at(ip("2001:db8:1:3::1"), t).is_ok());
    }

    #[test]
    fn accounts_get_their_own_buckets() {
        let rl = RateLimiter::new(RateLimit {
            burst: 2,
            per: Duration::from_secs(60),
        });
        let t = Instant::now();
        for _ in 0..2 {
            rl.check_at(AccountKey(1), t).unwrap();
        }
        assert!(rl.check_at(AccountKey(1), t).is_err());
        assert!(rl.check_at(AccountKey(2), t).is_ok());
        // Every bit of the id counts (the old IPv6-prefix encoding kept 64).
        assert!(rl.check_at(AccountKey(1 + (1 << 40)), t).is_ok());
        assert!(rl.check_at(AccountKey(-1), t).is_ok());
    }

    const WINDOW: FixedWindow = FixedWindow {
        max: 3,
        window: Duration::from_secs(900),
    };

    #[test]
    fn a_window_allows_its_max_then_refuses_until_it_ends() {
        let wl = WindowLimiter::new(WINDOW);
        let t = Instant::now();
        let a = AccountKey(1);
        for i in 0..3 {
            assert!(wl.take_at(a, t + Duration::from_secs(i)).is_ok());
        }
        let wait = wl.take_at(a, t + Duration::from_secs(60)).unwrap_err();
        assert_eq!(
            wait,
            Duration::from_secs(840),
            "until the window opened at t ends"
        );
        assert_eq!(
            wl.check_at(a, t + Duration::from_secs(899)),
            Err(Duration::from_secs(1))
        );
        // Other accounts are untouched.
        assert!(wl.take_at(AccountKey(2), t).is_ok());
    }

    #[test]
    fn a_window_ending_clears_the_whole_count() {
        let wl = WindowLimiter::new(WINDOW);
        let t = Instant::now();
        let a = AccountKey(1);
        for _ in 0..3 {
            wl.take_at(a, t).unwrap();
        }
        // No early trickle, as a token bucket would give.
        assert!(wl.take_at(a, t + Duration::from_secs(600)).is_err());
        // After the window the full allowance is back, in a new window.
        let later = t + Duration::from_secs(900);
        for _ in 0..3 {
            assert!(wl.take_at(a, later).is_ok());
        }
        assert_eq!(
            wl.take_at(a, later + Duration::from_secs(1)),
            Err(Duration::from_secs(899))
        );
    }

    #[test]
    fn refusals_are_not_counted() {
        let wl = WindowLimiter::new(WINDOW);
        let t = Instant::now();
        let a = AccountKey(1);
        for _ in 0..3 {
            wl.take_at(a, t).unwrap();
        }
        for _ in 0..10 {
            assert!(wl.take_at(a, t).is_err());
        }
        assert!(wl.take_at(a, t + Duration::from_secs(900)).is_ok());
    }

    #[test]
    fn counted_attempts_block_and_clear() {
        let wl = WindowLimiter::new(FixedWindow {
            max: 10,
            window: Duration::from_secs(900),
        });
        let t = Instant::now();
        let a = AccountKey(5);
        for i in 0..9 {
            wl.take_at(a, t + Duration::from_secs(i)).unwrap();
            assert!(wl.check_at(a, t + Duration::from_secs(i)).is_ok());
        }
        wl.take_at(a, t + Duration::from_secs(9)).unwrap();
        assert_eq!(
            wl.check_at(a, t + Duration::from_secs(100)),
            Err(Duration::from_secs(800))
        );
        // The window's end clears it...
        assert!(wl.check_at(a, t + Duration::from_secs(900)).is_ok());
        // ...and so does a success.
        for _ in 0..10 {
            wl.take_at(a, t + Duration::from_secs(1000)).unwrap();
        }
        assert!(wl.check_at(a, t + Duration::from_secs(1000)).is_err());
        wl.clear(a);
        assert!(wl.check_at(a, t + Duration::from_secs(1000)).is_ok());
        assert!(wl.is_empty());
    }

    #[test]
    fn passed_windows_are_pruned() {
        let wl = WindowLimiter::new(WINDOW);
        let t = Instant::now();
        for i in 0..=PRUNE_THRESHOLD as i64 {
            wl.take_at(AccountKey(i), t).unwrap();
        }
        assert_eq!(wl.len(), PRUNE_THRESHOLD + 1);
        // The next call after the windows passed drops them all.
        wl.take_at(AccountKey(-1), t + Duration::from_secs(900))
            .unwrap();
        assert_eq!(wl.len(), 1);
    }

    #[test]
    fn minutes_round_up() {
        assert_eq!(
            try_again_in(Duration::from_secs(1)),
            "Try again in 1 minute."
        );
        assert_eq!(
            try_again_in(Duration::from_secs(60)),
            "Try again in 1 minute."
        );
        assert_eq!(
            try_again_in(Duration::from_secs(61)),
            "Try again in 2 minutes."
        );
        assert_eq!(
            try_again_in(Duration::from_secs(900)),
            "Try again in 15 minutes."
        );
        assert_eq!(minutes_left(Duration::ZERO), 1);
    }

    #[test]
    fn mapped_ipv4_is_the_v4_address() {
        assert_eq!(bucket_key(ip("::ffff:10.1.2.3")), ip("10.1.2.3"));
    }
}
