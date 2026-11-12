//! Station grants: crafting at a station and using the Owl
//! Exchange are HTTP requests, but only for a character standing there.
//!
//! `ClientMsg::Interact` with a station interactable (`craft_charms`,
//! `craft_transfiguration`, `cauldron`, `owl_exchange`) in reach, or
//! `ClientMsg::UseFurniture` with a cauldron placed in the dorm the
//! character is in, records a grant (one per character and station kind,
//! the newest wins) and answers `ServerMsg::StationOpened`. The HTTP side
//! asks `WorldHandle::station_access` (through [`require`]); the world
//! answers from the grant *and the character's current position*: still in
//! the same zone, within the station's reach plus [`REACH_SLACK_M`], within
//! [`STATION_GRANT_SECS`] of opening it, and (for furniture) the item still
//! placed there. Walking away closes it.

use std::time::{Duration, Instant};

use protocol::{STATION_GRANT_SECS, StationKind};

use super::entities::ZoneId;
use crate::AppState;
use crate::http::ApiError;

/// How far beyond a station's reach a character may drift while using it (metres).
pub const REACH_SLACK_M: f32 = 1.0;
/// Reach of placed station furniture (cauldrons), from the item's origin (metres).
pub const FURNITURE_REACH_M: f32 = 1.5;

/// How long a grant lasts.
pub fn grant_ttl() -> Duration {
    Duration::from_secs(u64::from(STATION_GRANT_SECS))
}

/// What a grant was made at.
#[derive(Debug, Clone, PartialEq)]
pub enum StationTarget {
    /// A layout interactable (by id) of the castle or the instance `zone`.
    Interactable { id: String },
    /// Station furniture placed in the dorm instance the character was in.
    Furniture { item_id: i64 },
}

impl StationTarget {
    /// `StationOpened.target`: the interactable id, or `item:<items.id>`.
    pub fn wire(&self) -> String {
        match self {
            StationTarget::Interactable { id } => id.clone(),
            StationTarget::Furniture { item_id } => format!("item:{item_id}"),
        }
    }
}

/// One character's grant for one station kind.
#[derive(Debug, Clone, PartialEq)]
pub struct StationGrant {
    pub kind: StationKind,
    pub zone: ZoneId,
    pub target: StationTarget,
    pub until: Instant,
}

/// What `station_access` answers while a grant holds.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StationAccess {
    pub kind: StationKind,
    /// As in `StationOpened.target`.
    pub target: String,
    /// The owner of the dorm a cauldron stands in (furniture only).
    pub dorm_owner: Option<i64>,
}

/// The owl-order catalogue's refusal away from the stand.
pub const OWL_ORDER_AWAY: &str = "Visit the Owl Order Catalogue in your common room.";

/// `403 not_at_station`: the HTTP answer when there is no grant.
pub fn not_at_station(kind: StationKind) -> ApiError {
    let message = match kind {
        StationKind::CraftCharms => "Go to a knitting corner and use it first.",
        StationKind::CraftTransfiguration => "Go to a Transfiguration table and use it first.",
        StationKind::Cauldron => "Go to a cauldron and use it first.",
        StationKind::OwlExchange => "Go to the Owl Exchange counter and use it first.",
        StationKind::OwlOrder => OWL_ORDER_AWAY,
    };
    ApiError::new(axum::http::StatusCode::FORBIDDEN, "not_at_station", message)
}

/// For HTTP handlers: the character's access to a `kind` station, or
/// `403 not_at_station`. With `Config::stations_anywhere` (tests only) every
/// character has access everywhere; without a running world (HTTP-only
/// tests) nobody does.
pub async fn require(
    state: &AppState,
    character_id: i64,
    kind: StationKind,
) -> Result<StationAccess, ApiError> {
    if state.config.stations_anywhere {
        return Ok(StationAccess {
            kind,
            target: "anywhere".into(),
            dorm_owner: None,
        });
    }
    let Some(world) = state.world() else {
        return Err(not_at_station(kind));
    };
    world
        .station_access(character_id, kind)
        .await
        .ok_or_else(|| not_at_station(kind))
}
