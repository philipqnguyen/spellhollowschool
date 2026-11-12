//! Flying (protocol v8): brooms over the grounds, the pitch and the
//! lake. See docs/architecture.md.
//!
//! **Mounting.** `ClientMsg::Mount { on: true }` asks to mount where the
//! character stands; `on: false` to land and dismount. The server answers
//! every request with `ServerMsg::Mounted`: the state the character is now
//! in, the broom's speed tier, and why a request was refused. It also sends
//! `Mounted` on its own when it changes the state (entering or leaving a
//! Flying class, a zone change, a correction that lands the rider). The
//! broom is not an item: it appears when the character mounts.
//!
//! **Moving.** While mounted, `ClientMsg::Move` carries the flight position
//! predicted with `sim_core::flight::fly_and_slide` and `anim` `Fly` (moving)
//! or `Hover` (still); the server checks it with `sim_core::flight::check_flight`
//! (never trusting the client's anim to pick the mode: its own mount state
//! does). Viewers see `Anim::Fly` / `Anim::Hover` in snapshots, whose deltas
//! then count in 2 cm units ([`crate::snapshot`]).
//!
//! **Speed.** The broom's top speed is its tier's (`data/flying.json`
//! `tiers`, by the Flying skill level: 8, 9 and 10 m/s) times
//! `OwnEffects.broom_pct` (Featherlight Elixir: 115).

use serde::{Deserialize, Serialize};

/// `OwnEffects.broom_pct` with no effect (percent of the tier's speed).
pub const BROOM_PCT_NORMAL: u16 = 100;
/// The most an effect may give (percent); the server clamps to it.
pub const BROOM_PCT_MAX: u16 = 150;

/// Why `ClientMsg::Mount` was refused (`ServerMsg::Mounted.reason`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MountRefused {
    /// Mounting needs the Flying skill: take a Flying class first.
    NoSkill,
    /// Not in open sky here (indoors, an instance with no airspace, or
    /// outside the airspace's boxes).
    NoAirspace,
    /// Inside a no-fly zone (the castle, the Owl Loft, a building, the
    /// pitch's stands and towers).
    NoFly,
    /// Busy: trading, at a station, sitting, dueling or in a class that does
    /// not fly.
    Busy,
    /// Dismounting needs ground within `dismount_height` below (land first;
    /// never over the water).
    NotLanded,
    /// Too many requests; slow down.
    RateLimited,
}

impl MountRefused {
    pub const ALL: [MountRefused; 6] = [
        MountRefused::NoSkill,
        MountRefused::NoAirspace,
        MountRefused::NoFly,
        MountRefused::Busy,
        MountRefused::NotLanded,
        MountRefused::RateLimited,
    ];

    /// The id used in JSON and the client (snake_case).
    pub fn id(self) -> &'static str {
        match self {
            MountRefused::NoSkill => "no_skill",
            MountRefused::NoAirspace => "no_airspace",
            MountRefused::NoFly => "no_fly",
            MountRefused::Busy => "busy",
            MountRefused::NotLanded => "not_landed",
            MountRefused::RateLimited => "rate_limited",
        }
    }
}
