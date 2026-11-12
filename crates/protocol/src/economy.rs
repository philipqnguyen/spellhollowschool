//! Realtime parts of the economy: stations and the Owl
//! Exchange counter, exploration finds, the trade window, and the wallet and
//! mail notices. Everything that is a list of records (mail, listings,
//! recipes) is HTTP; see docs/architecture.md.
//!
//! **Stations.** `ClientMsg::Interact` with a station interactable (or
//! `ClientMsg::UseFurniture` with a cauldron placed in a dorm) in reach
//! answers `ServerMsg::StationOpened` and gives the character a short-lived
//! *station grant*: the HTTP side may then craft there, or use the Exchange
//! at the counter, while the character stays in reach (the server checks).
//!
//! **Trades** are a two-step window between two players in the same zone
//! within `EconomyRules.trade.distance_m`. Both set offers (items with
//! quantities, and Crowns), both lock, both confirm the exact revision they
//! reviewed. Any change to an offer bumps `TradeView.rev` and clears both
//! locks and confirmations. Once both confirmed, one Postgres transaction
//! moves everything; `TradeResult` reports how the window ended.

use serde::{Deserialize, Serialize};

use crate::{EntityId, Finish};

/// What a station grant lets the HTTP side do.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum StationKind {
    /// Knitting and sewing corner (Charms): garments.
    CraftCharms,
    /// Transfiguration table: restyles.
    CraftTransfiguration,
    /// A cauldron (Potions classroom, or cauldron furniture in a dorm):
    /// dyes, finishes and potions.
    Cauldron,
    /// The Owl Exchange counter in the Entrance Hall.
    OwlExchange,
    /// The Owl Order Catalogue's stand in a house common room (that house's
    /// only): browsing and ordering from the owl-order catalogue.
    OwlOrder,
}

impl StationKind {
    pub const ALL: [StationKind; 5] = [
        StationKind::CraftCharms,
        StationKind::CraftTransfiguration,
        StationKind::Cauldron,
        StationKind::OwlExchange,
        StationKind::OwlOrder,
    ];

    /// The id used in data files (interactable kinds, recipe stations,
    /// furniture `station`) and JSON.
    pub fn id(self) -> &'static str {
        match self {
            StationKind::CraftCharms => "craft_charms",
            StationKind::CraftTransfiguration => "craft_transfiguration",
            StationKind::Cauldron => "cauldron",
            StationKind::OwlExchange => "owl_exchange",
            StationKind::OwlOrder => "owl_order",
        }
    }

    pub fn from_id(id: &str) -> Option<StationKind> {
        StationKind::ALL.into_iter().find(|k| k.id() == id)
    }
}

/// A station grant lasts this long after the `Interact` that opened it
/// (seconds), and only while the character stays within reach.
pub const STATION_GRANT_SECS: u16 = 900;

/// An item a find produced.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct FoundItem {
    pub item_def: u16,
    pub quantity: u32,
}

/// What searching a find spot gave (`ServerMsg::FindResult`).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum FindOutcome {
    /// Items (into the inventory) and Crowns (into the wallet).
    Found { items: Vec<FoundItem>, crowns: i64 },
    /// The roll came up empty ("only dust").
    Nothing,
    /// This character searched this spot recently; try again later.
    Cooldown { ready_in_secs: u32 },
    /// The server could not complete the search; nothing was used up.
    Failed,
}

/// One line of an offer as the client sends it.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct OfferItem {
    /// An item in the sender's inventory (`items.id`).
    pub item_id: i64,
    /// How many of a stack (1 for clothing and furniture).
    pub quantity: u32,
}

/// One line of an offer as both sides see it.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct OfferedItem {
    pub item_id: i64,
    pub item_def: u16,
    pub dyes: [u8; 3],
    pub finish: Finish,
    pub quantity: u32,
    /// Maker's mark (the crafter's name) of crafted items.
    pub maker: Option<String>,
}

/// One side's offer.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct TradeOffer {
    pub items: Vec<OfferedItem>,
    pub crowns: i64,
}

/// The whole trade window as one side sees it (`ServerMsg::TradeState`).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct TradeView {
    pub trade_id: u32,
    pub partner: EntityId,
    pub partner_name: String,
    /// Bumped by every change to either offer (and by unlocking); confirms
    /// name the revision they approve.
    pub rev: u32,
    pub mine: TradeOffer,
    pub theirs: TradeOffer,
    pub mine_locked: bool,
    pub theirs_locked: bool,
    pub mine_confirmed: bool,
    pub theirs_confirmed: bool,
}

/// Why a trade action was refused (`ServerMsg::TradeError`; the window, if
/// any, stays as it was) or why a trade failed (`TradeOutcome::Failed`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum TradeErrorCode {
    /// No such player here (or one who blocked the sender: blocks stay private).
    UnknownTarget,
    /// Trading with yourself.
    SelfTrade,
    /// Farther than the trade distance, or in another zone.
    TooFar,
    /// Either side is already in a trade or has a request pending.
    Busy,
    /// No such trade, or it is not this player's.
    UnknownTrade,
    /// The request expired.
    Expired,
    /// The offer is locked; unlock it to change it.
    Locked,
    /// Confirming needs both offers locked.
    NotLocked,
    /// The confirmation named an older revision than the current one.
    StaleRevision,
    /// Too many lines, a duplicate item, a zero quantity or a negative or
    /// too large Crown amount.
    InvalidOffer,
    /// An offered item is not in the offerer's inventory (gone, equipped,
    /// placed, mailed, listed) or has fewer units than offered.
    ItemUnavailable,
    /// Not enough Crowns for the offer.
    InsufficientCrowns,
    /// Too many trade actions; slow down.
    RateLimited,
    /// The server could not complete the trade (e.g. a database error);
    /// nothing moved.
    Failed,
}

/// How a trade window ended (`ServerMsg::TradeResult`, sent to both sides).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum TradeOutcome {
    /// Everything moved in one transaction.
    Completed,
    /// One side cancelled.
    Cancelled,
    /// The other player declined the request.
    Declined,
    /// The request was not answered in time.
    Expired,
    /// The two walked out of trade range, or one changed zone.
    WalkedAway,
    /// One side disconnected.
    Disconnected,
    /// The final transaction refused (an item or Crowns were no longer
    /// there) or failed; nothing moved.
    Failed(TradeErrorCode),
}

/// A trade request may be answered for this long (seconds).
pub const TRADE_REQUEST_SECS: u16 = 30;
/// Most item lines in one side's offer.
pub const TRADE_ITEMS_MAX: usize = 12;
