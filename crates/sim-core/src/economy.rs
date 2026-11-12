//! The economy's numbers: `data/economy.json` ([`Economy`]).
//!
//! One integer currency, the Crown, stored and sent as whole Crowns
//! (`i64` in the server and on the wire). Sources: the starting
//! purse, exploration finds and selling to other players. Sinks:
//! listing fees, sales tax, owl-order purchases, postage, and dyes and
//! potions used up.
//!
//! Every amount here is a whole number, and the arithmetic helpers
//! ([`ExchangeRules::sales_tax`] and friends) are checked: they return
//! `None` instead of wrapping, so money code never silently overflows.

use serde::{Deserialize, Serialize};

use protocol::EffectKind;

/// The economy schema version this crate reads.
pub const ECONOMY_VERSION: u32 = 1;

#[derive(Debug, thiserror::Error)]
pub enum EconomyError {
    #[error("economy JSON is invalid: {0}")]
    Json(#[from] serde_json::Error),
    #[error("economy is inconsistent: {0}")]
    Invalid(String),
}

/// The Owl Exchange (auction house: fixed buyout prices, no bidding).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ExchangeRules {
    /// Flat fee per listing, paid when listing, never refunded (Crowns).
    pub listing_fee: u32,
    /// Percent of the price removed from the economy on a sale (whole
    /// percent, rounded down to whole Crowns).
    pub sales_tax_pct: u8,
    /// How long a listing stays up (seconds; 5 real days). Unsold items
    /// return by owl mail.
    pub listing_secs: u32,
    /// Active listings per character.
    pub active_listings_max: u32,
    /// Lowest and highest listing price (Crowns, for the whole listing).
    pub price_min: u32,
    pub price_max: u32,
    /// Search requests per character: a burst, then one per this many seconds.
    pub search_burst: u32,
    pub search_refill_secs: u32,
}

impl ExchangeRules {
    /// The tax on a sale at `price`: `price * sales_tax_pct / 100`, rounded
    /// down. `None` for a negative price or on overflow.
    pub fn sales_tax(&self, price: i64) -> Option<i64> {
        if price < 0 {
            return None;
        }
        price
            .checked_mul(i64::from(self.sales_tax_pct))
            .map(|t| t / 100)
    }

    /// What the seller receives for a sale at `price` (price minus tax).
    pub fn proceeds(&self, price: i64) -> Option<i64> {
        price.checked_sub(self.sales_tax(price)?)
    }

    /// Whether `price` may be asked.
    pub fn price_ok(&self, price: i64) -> bool {
        (i64::from(self.price_min)..=i64::from(self.price_max)).contains(&price)
    }
}

/// Owl mail.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MailRules {
    /// Paid by the sender of a player letter (Crowns); system mail is free.
    pub postage: u32,
    pub subject_max_chars: u16,
    pub body_max_chars: u16,
    /// Item attachments per letter.
    pub attachments_max: u8,
    /// Most Crowns one letter may carry.
    pub crowns_max: u32,
    /// Letters a character may send: a burst, then one per this many seconds.
    pub send_burst: u32,
    pub send_refill_secs: u32,
    /// Days a mail is kept once nothing waits in it any more (collected, or
    /// sent with nothing to collect); then the cleanup job deletes it. Mail
    /// whose items or Crowns were never collected is kept. Added after
    #[serde(default = "default_retention_days")]
    pub retention_days: u16,
}

fn default_retention_days() -> u16 {
    30
}

impl MailRules {
    /// How long collected mail is kept ([`MailRules::retention_days`]).
    pub fn retention(&self) -> std::time::Duration {
        std::time::Duration::from_secs(u64::from(self.retention_days) * 86_400)
    }

    /// What sending a letter carrying `crowns` costs the sender
    /// (`crowns + postage`); `None` for a negative amount or on overflow.
    pub fn send_cost(&self, crowns: i64) -> Option<i64> {
        if crowns < 0 {
            return None;
        }
        crowns.checked_add(i64::from(self.postage))
    }
}

/// The trade window (realtime).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TradeRules {
    /// Both players must be in the same zone within this distance (metres)
    /// to request, and while trading; walking farther than
    /// `distance_m + leave_slack_m` cancels.
    pub distance_m: f32,
    pub leave_slack_m: f32,
    /// Most Crowns one side may offer.
    pub crowns_max: u32,
}

/// An effect that only lasts.
#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct Timed {
    pub duration_secs: u32,
}

#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct ChameleonRules {
    pub duration_secs: u32,
    /// Time per colour step (milliseconds), for the client's cycle.
    pub cycle_ms: u32,
}

#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct ScaleRules {
    pub duration_secs: u32,
    /// Drawn size in percent of normal (`VisibleEffect::Scale`).
    pub scale_pct: u8,
}

#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct MimicDraughtRules {
    pub duration_secs: u32,
    /// The friend must be in the same zone within this distance (metres).
    pub range_m: f32,
}

#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct FortunesDraughtRules {
    pub duration_secs: u32,
    /// Extra loot rolls per find while lucky.
    pub extra_rolls: u8,
    /// Weight multiplier (percent) of loot entries marked `rare`.
    pub rare_weight_pct: u16,
}

#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct PickMeUpTonicRules {
    pub duration_secs: u32,
    /// Movement speed in percent of normal while active; the server
    /// validates moves with exactly this (`MoveParams::with_speed_pct`).
    pub speed_pct: u16,
}

/// Featherlight Elixir: a faster broom.
#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct FeatherlightRules {
    pub duration_secs: u32,
    /// Broom speed in percent of the tier's while active
    /// (`OwnEffects.broom_pct`); the server validates flight with exactly
    /// this (`flight::FlightRules::params`).
    pub broom_pct: u16,
}

/// The Divination fortunes (`protocol::FortuneKind`): one at a
/// time, `duration_secs` long, seen only by their holder.
#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct FortuneRules {
    pub duration_secs: u32,
    /// Lucky Find: extra loot rolls per find (Fortune's Draught gives `fortunes_draught.extra_rolls`)...
    pub luck_extra_rolls: u8,
    /// ...and the weight multiplier (percent) of `rare` loot entries.
    pub luck_rare_weight_pct: u16,
    /// Quick Hands: craft busy time is cut by this percentage.
    pub quick_hands_pct: u8,
    /// Keen Mind: a class's bonus XP grows by this percentage.
    pub keen_mind_pct: u8,
}

/// Potion effects: durations and numbers, including the Divination
/// fortunes and Featherlight Elixir (a faster broom).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EffectRules {
    pub glow: Timed,
    pub chameleon: ChameleonRules,
    pub shrinking: ScaleRules,
    pub swelling: ScaleRules,
    pub babbling: Timed,
    pub mimic_draught: MimicDraughtRules,
    pub fortunes_draught: FortunesDraughtRules,
    pub pick_me_up_tonic: PickMeUpTonicRules,
    pub invisibility: Timed,
    pub fortune: FortuneRules,
    pub featherlight: FeatherlightRules,
}

impl EffectRules {
    /// How long an effect of `kind` lasts (seconds). Spells L20: the spell
    /// effects ([`EffectKind::is_spell`]) last what their spell says
    /// (`data/spells.json`), so they have none here (0).
    pub fn duration_secs(&self, kind: EffectKind) -> u32 {
        match kind {
            EffectKind::Petrified
            | EffectKind::Slowed
            | EffectKind::Recoloured
            | EffectKind::Guardian => 0,
            EffectKind::Glow => self.glow.duration_secs,
            EffectKind::Chameleon => self.chameleon.duration_secs,
            EffectKind::Shrinking => self.shrinking.duration_secs,
            EffectKind::Swelling => self.swelling.duration_secs,
            EffectKind::Babbling => self.babbling.duration_secs,
            EffectKind::MimicDraught => self.mimic_draught.duration_secs,
            EffectKind::FortunesDraught => self.fortunes_draught.duration_secs,
            EffectKind::PickMeUpTonic => self.pick_me_up_tonic.duration_secs,
            EffectKind::Invisibility => self.invisibility.duration_secs,
            EffectKind::Fortune => self.fortune.duration_secs,
            EffectKind::Featherlight => self.featherlight.duration_secs,
        }
    }
}

/// `data/economy.json`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Economy {
    pub version: u32,
    /// Crowns a new character starts with.
    pub starting_purse: u32,
    pub exchange: ExchangeRules,
    pub mail: MailRules,
    pub trade: TradeRules,
    pub effects: EffectRules,
}

impl Economy {
    pub fn from_json(json: &str) -> Result<Economy, EconomyError> {
        let e: Economy = serde_json::from_str(json)?;
        e.validate()?;
        Ok(e)
    }

    /// Positive durations and limits, a tax below 100 %, a sensible price
    /// range, finite distances, and effect numbers within what the protocol
    /// carries.
    pub fn validate(&self) -> Result<(), EconomyError> {
        let fail = |m: &str| Err(EconomyError::Invalid(m.into()));
        if self.version != ECONOMY_VERSION {
            return fail("unsupported economy version");
        }
        let x = &self.exchange;
        if x.sales_tax_pct >= 100
            || x.listing_secs == 0
            || x.active_listings_max == 0
            || x.price_min == 0
            || x.price_min > x.price_max
            || x.search_burst == 0
            || x.search_refill_secs == 0
        {
            return fail("exchange rules out of range");
        }
        let m = &self.mail;
        if m.subject_max_chars == 0
            || m.body_max_chars == 0
            || m.attachments_max == 0
            || m.send_burst == 0
            || m.send_refill_secs == 0
            || m.retention_days == 0
        {
            return fail("mail rules out of range");
        }
        let t = &self.trade;
        if !(t.distance_m.is_finite() && t.distance_m > 0.0)
            || !(t.leave_slack_m.is_finite() && t.leave_slack_m >= 0.0)
        {
            return fail("trade distances must be positive");
        }
        let fx = &self.effects;
        if EffectKind::ALL
            .iter()
            .any(|&k| !k.is_spell() && fx.duration_secs(k) == 0)
        {
            return fail("every effect needs a duration");
        }
        if !(1..100).contains(&fx.shrinking.scale_pct) || fx.swelling.scale_pct <= 100 {
            return fail("shrinking scales below 100 %, swelling above");
        }
        if !(protocol::SPEED_PCT_NORMAL + 1..=protocol::SPEED_PCT_MAX)
            .contains(&fx.pick_me_up_tonic.speed_pct)
        {
            return fail("pick_me_up_tonic speed must be above 100 % and at most SPEED_PCT_MAX");
        }
        if !(protocol::BROOM_PCT_NORMAL + 1..=protocol::BROOM_PCT_MAX)
            .contains(&fx.featherlight.broom_pct)
        {
            return fail("featherlight must speed the broom up, to at most BROOM_PCT_MAX");
        }
        if !(fx.mimic_draught.range_m.is_finite() && fx.mimic_draught.range_m > 0.0)
            || fx.chameleon.cycle_ms == 0
        {
            return fail("mimic_draught range and chameleon cycle must be positive");
        }
        let f = &fx.fortune;
        if f.quick_hands_pct >= 100
            || f.keen_mind_pct > 100
            || f.luck_rare_weight_pct < 100
            || f.luck_rare_weight_pct > fx.fortunes_draught.rare_weight_pct
            || f.luck_extra_rolls > fx.fortunes_draught.extra_rolls
        {
            return fail(
                "fortunes are small: below Fortune's Draught, under 100 % cuts and boosts",
            );
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const ECONOMY: &str = include_str!("../../../data/economy.json");

    #[test]
    fn the_shipped_economy_is_valid() {
        let e = Economy::from_json(ECONOMY).unwrap();
        assert!(e.starting_purse > 0);
        assert_eq!(e.exchange.listing_secs, 5 * 24 * 3600, "five real days");
        assert_eq!(e.exchange.active_listings_max, 20);
        // Divination's fortunes last a real hour and are half of Fortune's Draught.
        assert_eq!(e.effects.duration_secs(EffectKind::Fortune), 3_600);
        assert_eq!(e.effects.fortune.quick_hands_pct, 25);
        assert_eq!(e.effects.fortune.keen_mind_pct, 10);
    }

    #[test]
    fn tax_and_proceeds_round_down_and_never_overflow() {
        let mut x = Economy::from_json(ECONOMY).unwrap().exchange;
        x.sales_tax_pct = 5;
        assert_eq!(x.sales_tax(100), Some(5));
        assert_eq!(x.sales_tax(19), Some(0));
        assert_eq!(x.sales_tax(39), Some(1));
        assert_eq!(x.proceeds(39), Some(38));
        assert_eq!(x.sales_tax(-1), None);
        assert_eq!(x.sales_tax(i64::MAX), None);
        // Tax plus proceeds is always the price.
        for p in [1_i64, 7, 99, 100, 101, 12_345, 1_000_000] {
            assert_eq!(x.sales_tax(p).unwrap() + x.proceeds(p).unwrap(), p);
        }
        let m = Economy::from_json(ECONOMY).unwrap().mail;
        assert_eq!(m.send_cost(10), Some(10 + i64::from(m.postage)));
        assert_eq!(m.send_cost(-1), None);
        assert_eq!(m.send_cost(i64::MAX), None);
    }

    #[test]
    fn rejects_broken_numbers() {
        let mut e = Economy::from_json(ECONOMY).unwrap();
        e.exchange.sales_tax_pct = 100;
        assert!(e.validate().is_err());
        let mut e = Economy::from_json(ECONOMY).unwrap();
        e.effects.pick_me_up_tonic.speed_pct = 100;
        assert!(e.validate().is_err());
        let mut e = Economy::from_json(ECONOMY).unwrap();
        e.effects.shrinking.scale_pct = 120;
        assert!(e.validate().is_err());
        let mut e = Economy::from_json(ECONOMY).unwrap();
        e.effects.fortunes_draught.duration_secs = 0;
        assert!(e.validate().is_err());
        let mut e = Economy::from_json(ECONOMY).unwrap();
        e.mail.retention_days = 0;
        assert!(e.validate().is_err());
        let mut e = Economy::from_json(ECONOMY).unwrap();
        e.effects.fortune.luck_rare_weight_pct = e.effects.fortunes_draught.rare_weight_pct + 1;
        assert!(e.validate().is_err());
        let mut e = Economy::from_json(ECONOMY).unwrap();
        e.effects.fortune.duration_secs = 0;
        assert!(e.validate().is_err());
    }

    #[test]
    fn mail_retention_defaults_to_thirty_days() {
        let e = Economy::from_json(ECONOMY).unwrap();
        assert_eq!(
            e.mail.retention().as_secs(),
            u64::from(e.mail.retention_days) * 86_400
        );
        // A file from before the field keeps mail for 30 days.
        let mut v: serde_json::Value = serde_json::from_str(ECONOMY).unwrap();
        v["mail"].as_object_mut().unwrap().remove("retention_days");
        let old = Economy::from_json(&v.to_string()).unwrap();
        assert_eq!(old.mail.retention_days, 30);
    }
}
