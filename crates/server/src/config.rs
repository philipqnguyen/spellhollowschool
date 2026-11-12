//! Server configuration, read from the environment exactly as
//! `docs/configuration.md` lists it. `RUST_LOG` is read by
//! the tracing subscriber in `main.rs`, not here.
//!
//! Settings that are not part of the deployment contract (rate limits, token
//! lifetimes) are plain fields with defaults so tests can tighten them.

use std::net::SocketAddr;
use std::path::PathBuf;
use std::time::Duration;

use crate::rate_limit::{FixedWindow, RateLimit};

#[derive(Debug, Clone)]
pub struct Config {
    /// `SHS_BIND`: HTTP + WebSocket listen address.
    pub bind: SocketAddr,
    /// `DATABASE_URL`: Postgres connection string.
    pub database_url: String,
    /// `SHS_LAYOUT`: world layout file (`sim_core::Layout` JSON).
    pub layout_path: PathBuf,
    /// `SHS_INSTANCES`: directory of instance layouts (`<id>.json`, e.g. the
    /// dorm shells `dorm_<house id>.json`).
    pub instances_dir: PathBuf,
    /// `SHS_PLAYER_CAP`: concurrent players in the world; beyond it logins queue.
    pub player_cap: u32,
    /// `SHS_DEV=1`: dev endpoints on, verification links logged and returned.
    pub dev: bool,
    /// `SHS_PUBLIC_URL`: base URL used in links sent to players (no trailing slash).
    pub public_url: String,
    /// `SHS_STAIRS_PERIOD_S`: the moving staircases switch every
    /// this many seconds instead of `data/world/stairs.json`'s period
    /// (development and load tests: honoured only with `SHS_DEV=1`).
    pub stairs_period_s: Option<u32>,
    /// `SHS_STATIC_DIR`: the built client (`client/dist`) to serve next to
    /// the API, with `index.html` for every other path. Unset: no client
    /// (development runs it on Vite).
    pub static_dir: Option<PathBuf>,
    /// `SHS_ASSETS_CDN_URL`: the public URL of the asset bucket
    /// (`https://assets.example.org`, no trailing slash), where the client
    /// loads the game assets by their content-addressed keys. The server names
    /// it in the `index.html` it serves. Unset: the client loads them from this
    /// server (docs/deploy.md, "Assets from a bucket").
    pub assets_cdn_url: Option<String>,
    /// `SHS_TRUST_PROXY=1`: the server sits behind a reverse proxy (kamal-proxy)
    /// that appends the client's address to `X-Forwarded-For`; rate limits key
    /// on that address instead of the proxy's. Never set it without a proxy in
    /// front: clients could then pick their own key.
    pub trust_proxy: bool,
    /// `SHS_CLIENT_IP_HEADER`: a header that carries the client's address, set
    /// by the edge in front of everything (`cf-connecting-ip` behind a
    /// Cloudflare Tunnel). Rate limits key on it, ahead of `SHS_TRUST_PROXY`.
    /// Only when the server is reachable through that edge alone: anyone who
    /// reaches it another way could pick their own key.
    pub client_ip_header: Option<axum::http::HeaderName>,
    /// `SHS_SMTP_URL`: the mail server, as `smtps://user:pass@host:465` or
    /// `smtp://user:pass@host:587?tls=required`. Unset: mail is only logged.
    pub smtp_url: Option<Redacted>,
    /// `SHS_MAIL_FROM`: the sender of every email, e.g.
    /// `SpellHollow School <no-reply@example.org>`. Required with `SHS_SMTP_URL`.
    pub mail_from: Option<String>,

    /// Chat word filter list (see the file for its format). Missing means no filter.
    pub blocked_words_path: PathBuf,
    /// Per-IP limit shared by the auth endpoints (register, verify, login,
    /// password reset request and confirm).
    pub auth_rate_limit: RateLimit,
    /// Per account: emails the auth endpoints send on request (password
    /// reset links, and the verification links signing in to an unconfirmed
    /// account sends again), one shared count; the email registering sends
    /// is not counted. Over it, no email and `429 too_many_emails`.
    pub email_limit: FixedWindow,
    /// Per account: wrong passwords at sign-in. Over it, `429
    /// too_many_attempts` even for the right password until the window
    /// ends; a successful sign-in clears the count.
    pub login_failure_limit: FixedWindow,
    /// How often a live game connection checks that its session still
    /// exists and has not expired (sign-outs and password resets also end
    /// it at once).
    pub session_recheck: Duration,
    /// How long a login session lasts.
    pub session_ttl: Duration,
    /// How long an email verification link stays valid.
    pub verification_ttl: Duration,
    /// How long a password reset link stays valid.
    pub password_reset_ttl: Duration,
    /// Tests only, never read from the environment: every character may use
    /// every station and the Owl Exchange from anywhere
    /// (`realtime::stations::require`), so HTTP-only tests can craft and trade.
    pub stations_anywhere: bool,
}

#[derive(Debug, thiserror::Error, PartialEq, Eq)]
pub enum ConfigError {
    #[error("{var} is not valid: {reason}")]
    Invalid { var: &'static str, reason: String },
}

impl Default for Config {
    /// The documented defaults (what an empty environment yields).
    fn default() -> Self {
        Self {
            bind: SocketAddr::from(([0, 0, 0, 0], 8080)),
            database_url: "postgres://localhost/shs".into(),
            layout_path: "data/world/layout.json".into(),
            instances_dir: "data/world/instances".into(),
            player_cap: 1000,
            dev: false,
            public_url: "http://localhost:8080".into(),
            stairs_period_s: None,
            static_dir: None,
            assets_cdn_url: None,
            trust_proxy: false,
            client_ip_header: None,
            smtp_url: None,
            mail_from: None,
            blocked_words_path: "data/moderation/blocked_words.txt".into(),
            // Ten attempts in a burst, then one every six seconds (10 a minute).
            auth_rate_limit: RateLimit {
                burst: 10,
                per: Duration::from_secs(6),
            },
            // Three emails per account per quarter hour.
            email_limit: FixedWindow {
                max: 3,
                window: Duration::from_secs(15 * 60),
            },
            // Ten wrong passwords per account per quarter hour.
            login_failure_limit: FixedWindow {
                max: 10,
                window: Duration::from_secs(15 * 60),
            },
            session_recheck: Duration::from_secs(5 * 60),
            session_ttl: Duration::from_secs(30 * 24 * 3600),
            verification_ttl: Duration::from_secs(48 * 3600),
            password_reset_ttl: Duration::from_secs(3600),
            stations_anywhere: false,
        }
    }
}

impl Config {
    /// Reads the process environment.
    pub fn from_env() -> Result<Self, ConfigError> {
        Self::from_lookup(|key| std::env::var(key).ok())
    }

    /// Reads configuration through `get`, so tests need not touch the real environment.
    pub fn from_lookup(get: impl Fn(&str) -> Option<String>) -> Result<Self, ConfigError> {
        let mut cfg = Self::default();
        if let Some(v) = get("SHS_BIND") {
            cfg.bind = v.parse().map_err(|e| invalid("SHS_BIND", e))?;
        }
        if let Some(v) = get("DATABASE_URL") {
            cfg.database_url = v;
        }
        if let Some(v) = get("SHS_INSTANCES") {
            cfg.instances_dir = v.into();
        }
        if let Some(v) = get("SHS_LAYOUT") {
            cfg.layout_path = v.into();
        }
        if let Some(v) = get("SHS_PLAYER_CAP") {
            cfg.player_cap = v.parse().map_err(|e| invalid("SHS_PLAYER_CAP", e))?;
        }
        if let Some(v) = get("SHS_DEV") {
            cfg.dev = v == "1";
        }
        if let Some(v) = get("SHS_PUBLIC_URL") {
            if !(v.starts_with("http://") || v.starts_with("https://")) {
                return Err(invalid(
                    "SHS_PUBLIC_URL",
                    "must start with http:// or https://",
                ));
            }
            cfg.public_url = v.trim_end_matches('/').to_owned();
        }
        if let Some(v) = get("SHS_STAIRS_PERIOD_S") {
            let p: u32 = v.parse().map_err(|e| invalid("SHS_STAIRS_PERIOD_S", e))?;
            if p == 0 {
                return Err(invalid("SHS_STAIRS_PERIOD_S", "must be at least 1"));
            }
            cfg.stairs_period_s = Some(p);
        }
        if let Some(v) = get("SHS_STATIC_DIR").filter(|v| !v.is_empty()) {
            cfg.static_dir = Some(v.into());
        }
        if let Some(v) = get("SHS_ASSETS_CDN_URL").filter(|v| !v.trim().is_empty()) {
            let v = v.trim();
            if !(v.starts_with("http://") || v.starts_with("https://")) {
                return Err(invalid(
                    "SHS_ASSETS_CDN_URL",
                    "must start with http:// or https://",
                ));
            }
            if v.contains(['"', '<', '>', '\'', ' ', '?', '#']) {
                return Err(invalid(
                    "SHS_ASSETS_CDN_URL",
                    "must be a plain URL (no quotes, spaces, query or fragment)",
                ));
            }
            cfg.assets_cdn_url = Some(v.trim_end_matches('/').to_owned());
        }
        if let Some(v) = get("SHS_TRUST_PROXY") {
            cfg.trust_proxy = v == "1";
        }
        if let Some(v) = get("SHS_CLIENT_IP_HEADER").filter(|v| !v.is_empty()) {
            let name = axum::http::HeaderName::from_bytes(v.trim().as_bytes())
                .map_err(|e| invalid("SHS_CLIENT_IP_HEADER", e))?;
            cfg.client_ip_header = Some(name);
        }
        if let Some(v) = get("SHS_SMTP_URL").filter(|v| !v.is_empty()) {
            if !(v.starts_with("smtp://") || v.starts_with("smtps://")) {
                return Err(invalid(
                    "SHS_SMTP_URL",
                    "must start with smtp:// or smtps://",
                ));
            }
            cfg.smtp_url = Some(Redacted(v));
        }
        if let Some(v) = get("SHS_MAIL_FROM").filter(|v| !v.is_empty()) {
            cfg.mail_from = Some(v);
        }
        if cfg.smtp_url.is_some() && cfg.mail_from.is_none() {
            return Err(invalid("SHS_MAIL_FROM", "is required with SHS_SMTP_URL"));
        }
        Ok(cfg)
    }
}

/// A setting that holds a secret (a password in a URL): `Debug` never prints it.
#[derive(Clone, PartialEq, Eq)]
pub struct Redacted(pub String);

impl Redacted {
    pub fn expose(&self) -> &str {
        &self.0
    }
}

impl std::fmt::Debug for Redacted {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("\"<redacted>\"")
    }
}

fn invalid(var: &'static str, reason: impl ToString) -> ConfigError {
    ConfigError::Invalid {
        var,
        reason: reason.to_string(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    fn from(pairs: &[(&str, &str)]) -> Result<Config, ConfigError> {
        let map: HashMap<String, String> = pairs
            .iter()
            .map(|(k, v)| (k.to_string(), v.to_string()))
            .collect();
        Config::from_lookup(|k| map.get(k).cloned())
    }

    #[test]
    fn empty_environment_gives_documented_defaults() {
        let cfg = from(&[]).unwrap();
        assert_eq!(cfg.bind.to_string(), "0.0.0.0:8080");
        assert_eq!(cfg.database_url, "postgres://localhost/shs");
        assert_eq!(cfg.layout_path, PathBuf::from("data/world/layout.json"));
        assert_eq!(cfg.instances_dir, PathBuf::from("data/world/instances"));
        assert_eq!(cfg.player_cap, 1000);
        assert!(!cfg.dev);
        assert_eq!(cfg.public_url, "http://localhost:8080");
        assert_eq!(cfg.stairs_period_s, None);
        assert_eq!(cfg.static_dir, None);
        assert_eq!(cfg.assets_cdn_url, None);
        assert!(!cfg.trust_proxy);
        assert_eq!(cfg.client_ip_header, None);
        assert_eq!(cfg.smtp_url, None);
        assert_eq!(cfg.mail_from, None);
    }

    #[test]
    fn reads_the_deployment_variables() {
        let cfg = from(&[
            ("SHS_STATIC_DIR", "/app/public"),
            ("SHS_ASSETS_CDN_URL", "https://assets.example.org/"),
            ("SHS_TRUST_PROXY", "1"),
            ("SHS_CLIENT_IP_HEADER", "CF-Connecting-IP"),
            ("SHS_SMTP_URL", "smtps://u:secret@mail.example.org:465"),
            ("SHS_MAIL_FROM", "Game <no-reply@example.org>"),
        ])
        .unwrap();
        assert_eq!(cfg.static_dir, Some(PathBuf::from("/app/public")));
        assert_eq!(
            cfg.assets_cdn_url.as_deref(),
            Some("https://assets.example.org")
        );
        assert!(cfg.trust_proxy);
        assert_eq!(
            cfg.client_ip_header.as_ref().map(|h| h.as_str()),
            Some("cf-connecting-ip")
        );
        assert_eq!(
            cfg.smtp_url.as_ref().map(Redacted::expose),
            Some("smtps://u:secret@mail.example.org:465")
        );
        assert!(!format!("{cfg:?}").contains("secret"));
        // Empty means unset, as docker and kamal pass blank variables.
        let blank = from(&[
            ("SHS_STATIC_DIR", ""),
            ("SHS_SMTP_URL", ""),
            ("SHS_ASSETS_CDN_URL", ""),
        ])
        .unwrap();
        assert_eq!(blank.static_dir, None);
        assert_eq!(blank.assets_cdn_url, None);
        assert_eq!(blank.smtp_url, None);
    }

    #[test]
    fn smtp_needs_a_scheme_and_a_sender() {
        assert!(from(&[("SHS_SMTP_URL", "mail.example.org")]).is_err());
        assert!(from(&[("SHS_SMTP_URL", "smtp://mail.example.org:587")]).is_err());
        assert!(from(&[("SHS_TRUST_PROXY", "true")]).is_ok_and(|c| !c.trust_proxy));
        assert!(from(&[("SHS_CLIENT_IP_HEADER", "not a header")]).is_err());
    }

    #[test]
    fn reads_every_variable() {
        let cfg = from(&[
            ("SHS_BIND", "127.0.0.1:9000"),
            ("DATABASE_URL", "postgres://db/x"),
            ("SHS_LAYOUT", "/srv/layout.json"),
            ("SHS_INSTANCES", "/srv/instances"),
            ("SHS_PLAYER_CAP", "250"),
            ("SHS_DEV", "1"),
            ("SHS_PUBLIC_URL", "https://play.example.org/"),
            ("SHS_STAIRS_PERIOD_S", "20"),
        ])
        .unwrap();
        assert_eq!(cfg.bind.port(), 9000);
        assert_eq!(cfg.database_url, "postgres://db/x");
        assert_eq!(cfg.layout_path, PathBuf::from("/srv/layout.json"));
        assert_eq!(cfg.instances_dir, PathBuf::from("/srv/instances"));
        assert_eq!(cfg.player_cap, 250);
        assert!(cfg.dev);
        assert_eq!(cfg.public_url, "https://play.example.org");
        assert_eq!(cfg.stairs_period_s, Some(20));
    }

    #[test]
    fn dev_is_only_on_for_exactly_one() {
        assert!(!from(&[("SHS_DEV", "0")]).unwrap().dev);
        assert!(!from(&[("SHS_DEV", "true")]).unwrap().dev);
    }

    #[test]
    fn rejects_bad_values() {
        assert!(from(&[("SHS_BIND", "nope")]).is_err());
        assert!(from(&[("SHS_PLAYER_CAP", "-1")]).is_err());
        assert!(from(&[("SHS_PUBLIC_URL", "example.org")]).is_err());
        assert!(from(&[("SHS_STAIRS_PERIOD_S", "0")]).is_err());
        assert!(from(&[("SHS_STAIRS_PERIOD_S", "soon")]).is_err());
        assert!(from(&[("SHS_ASSETS_CDN_URL", "assets.example.org")]).is_err());
        assert!(from(&[("SHS_ASSETS_CDN_URL", "https://a.example.org/\"x")]).is_err());
        assert!(from(&[("SHS_ASSETS_CDN_URL", "https://a.example.org/?v=1")]).is_err());
    }
}
