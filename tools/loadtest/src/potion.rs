//! Potion effects on bots (`--effects-pct`): a share of the bots gets one
//! potion from the dev grant (`POST /api/dev/grant`, so the server must run
//! with `SHS_DEV=1`, as for bot sessions) and drinks it once it is in the
//! world. Their looks then carry a visible effect (`EntityInfo.effects`,
//! `EffectsChanged`), and Pick-Me-Up Tonic drinkers walk on the boosted speed the
//! server validates, so a run measures what potions add to snapshots
//! and move checks.
//!
//! Every effect kind is used except Mimic Draught, which needs a consenting
//! friend; effects last 5-20 minutes, longer than a usual run.

use protocol::effects::EffectKind;
use rand::Rng;
use serde_json::{Value, json};
use sim_core::{Catalog, Palette};

use crate::http::Target;

/// The potion item definitions bots may drink.
pub struct Potions {
    defs: Vec<(u16, EffectKind)>,
}

impl Potions {
    pub fn shipped() -> Potions {
        let palette = Palette::from_json(include_str!("../../../data/items/palette.json"))
            .expect("the shipped palette is valid");
        let catalog =
            Catalog::from_json(include_str!("../../../data/items/catalog.json"), &palette)
                .expect("the shipped catalog is valid");
        let defs = catalog
            .items
            .iter()
            .filter(|d| !d.retired)
            .filter_map(|d| Some((d.id, d.potion.as_ref()?.effect)))
            .filter(|(_, effect)| *effect != EffectKind::MimicDraught)
            .collect();
        Potions { defs }
    }

    /// A random potion: its item definition and effect.
    pub fn pick(&self, rng: &mut impl Rng) -> (u16, EffectKind) {
        self.defs[rng.gen_range(0..self.defs.len())]
    }
}

/// Grants the bot whose session is `token` one `item_def` potion; returns
/// the inventory row to drink.
pub async fn grant(target: &Target, token: &str, item_def: u16) -> Result<i64, String> {
    let res = target
        .post_json_as(
            token,
            "/api/dev/grant",
            &json!({ "items": [{ "item_def": item_def, "quantity": 1 }] }),
        )
        .await?;
    if !res.is_success() {
        return Err(format!(
            "grant {}: {}",
            res.status,
            String::from_utf8_lossy(&res.body)
        ));
    }
    let v: Value = serde_json::from_slice(&res.body).map_err(|e| format!("grant body: {e}"))?;
    v["items"][0]
        .as_i64()
        .ok_or_else(|| "grant response has no item".to_owned())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bots_drink_every_effect_but_mimic_draught() {
        let p = Potions::shipped();
        let kinds: std::collections::HashSet<EffectKind> = p.defs.iter().map(|d| d.1).collect();
        assert!(!kinds.contains(&EffectKind::MimicDraught));
        assert!(kinds.contains(&EffectKind::PickMeUpTonic) && kinds.contains(&EffectKind::Glow));
        assert!(kinds.len() >= 8, "{kinds:?}");
    }
}
