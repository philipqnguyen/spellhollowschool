//! SpellHollow School game server library: everything `shs-server` runs,
//! split out so integration tests (and the realtime world) can build on it.
//!
//! - [`config`]: environment configuration (docs/architecture.md).
//! - [`db`]: Postgres pool and embedded migrations (`db/migrations`).
//! - [`http`]: router and the `{ error, message }` error type.
//! - [`auth`]: accounts, verification, sessions, [`validate_session`].
//! - [`characters`]: one character per account, plus world persistence helpers.
//! - [`catalog`], [`inventory`], [`outfits`], [`friends`]: content data,
//!   items and equipment, saved outfits, friends and blocks (HTTP).
//! - [`dev`]: dev-only endpoints (`SHS_DEV=1`).
//! - [`realtime`]: the WebSocket gateway (`GET /ws`), the castle world and
//!   instances; [`dorm`]: dorm persistence and placement rules.
//! - [`email`], [`rate_limit`], [`metrics`], [`monitoring`] (error tracking
//!   and performance): supporting services.
//! - [`ledger`] (the append-only item and Crown
//!   ledgers) and [`items`] (stack, move and consume helpers that record
//!   them). Economy: [`wallet`], [`mail`] (owl mail), [`shop`] (the
//!   owl-order catalogue), [`trade`], [`exchange`] (the Owl Exchange).
//!   Crafting: [`crafting`], [`skills`], [`potions`], [`finds`], [`effects`].
//! - [`clock`] (the game clock), [`progression`] (levels, XP,
//!   learned spells; its grant is shared), [`house_points`] (shared),
//!   [`classes`], [`spells`], [`npc`] and [`dueling`].
//! - [`house_cup`] (the weekly House Cup: standings, the close,
//!   the reward) on top of [`house_points`] (now also `find` and `craft`).
//!
//! Who owns what (docs/architecture.md): the data side owns
//! `characters`, `catalog`, `inventory`, `outfits` and `friends` and their
//! routes; the realtime side owns `realtime` and `dorm`; the economy side
//! owns `wallet`, `ledger`, `mail`, `shop`, `trade`, `exchange`, and the
//! crafting side `crafting`, `skills`, `potions`, `finds`, `effects` and
//! `inventory/dye.rs`. The HTTP side tells the world
//! about changes only through [`realtime::world::WorldHandle`] (reached with
//! [`AppState::world`]); world-thread feature code works through
//! [`realtime::ext::WorldCtx`].

pub mod auth;
pub mod catalog;
pub mod characters;
pub mod classes;
pub mod clock;
pub mod clubs;
pub mod config;
pub mod crafting;
pub mod db;
pub mod dev;
pub mod dorm;
pub mod dueling;
pub mod effects;
pub mod email;
pub mod exchange;
pub mod finds;
pub mod friends;
pub mod house_cup;
pub mod house_points;
pub mod http;
pub mod inventory;
pub mod items;
pub mod ledger;
pub mod mail;
/// The map's reveals and the secrets found (`GET /api/map`).
pub mod map;
pub mod metrics;
pub mod monitoring;
pub mod npc;
pub mod outfits;
/// The players list and the player card.
pub mod players;
pub mod potions;
pub mod progression;
pub mod rate_limit;
pub mod realtime;
pub mod residents;
pub mod shop;
pub mod skills;
pub mod spells;
pub mod static_files;
pub mod trade;
pub mod wallet;

use std::sync::Arc;

use sqlx::PgPool;

pub use auth::{SessionInfo, validate_session};
pub use characters::Character;
pub use config::Config;
pub use metrics::Metrics;

/// Shared state for every handler. Cheap to clone (pointers only).
#[derive(Clone)]
pub struct AppState {
    pub pool: PgPool,
    pub config: Arc<Config>,
    pub metrics: Arc<Metrics>,
    pub mailer: Arc<dyn email::Mailer>,
    pub auth_limiter: Arc<rate_limit::RateLimiter>,
    /// Per-account limits on emails sent and failed passwords.
    pub account_limits: Arc<rate_limit::AccountLimits>,
    /// The gateway and castle world, once started ([`realtime::Realtime::start`]).
    /// `GET /ws` answers 503 without it (as in the HTTP-only tests).
    pub realtime: Option<Arc<realtime::Realtime>>,
}

impl AppState {
    pub fn new(pool: PgPool, config: Config, mailer: Arc<dyn email::Mailer>) -> Self {
        let auth_limiter = Arc::new(rate_limit::RateLimiter::new(config.auth_rate_limit));
        let account_limits = Arc::new(rate_limit::AccountLimits::new(
            config.email_limit,
            config.login_failure_limit,
        ));
        Self {
            pool,
            config: Arc::new(config),
            metrics: Arc::new(Metrics::new()),
            mailer,
            auth_limiter,
            account_limits,
            realtime: None,
        }
    }

    /// Starts the castle world for this state and attaches it.
    pub fn start_realtime(&mut self, world: sim_core::World, filter: realtime::chat::WordFilter) {
        self.realtime = Some(realtime::Realtime::start(self, world, filter));
    }

    /// The world, for HTTP handlers that must tell it about a change
    /// (appearance, friends, wallet, mail) or ask it (stations). `None` when
    /// the realtime side is not running, as in the HTTP-only tests: then
    /// there is no one to tell.
    pub fn world(&self) -> Option<&realtime::world::WorldHandle> {
        self.realtime.as_deref().map(|rt| &rt.world)
    }
}
