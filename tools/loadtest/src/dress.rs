//! Dresses a bot in a random outfit through the HTTP API, as a player would
//! in the wardrobe: `GET /api/characters/me` (which hands a fresh bot its
//! starter wardrobe), `GET /api/inventory`, then one `POST /api/equipment` per
//! slot and a random dye for each worn item (from the free basic palette:
//! other colours use up brewed dyes). Snapshots then carry realistic,
//! fully dressed appearances (about 40 bytes each instead of 17 naked).
//!
//! Body type, sliders, skin and hair are chosen at random too (`random_body`),
//! sent with the dev bot session (the API sets them only before the starter
//! grant), so every bot is a different person, as players will be.
//!
//! A bot puts on only what its body may wear
//! (`Creator::can_wear`, as the browser's wardrobe greys out the rest).

use protocol::{Appearance, GearSlot};
use rand::Rng;
use serde_json::{Value, json};
use sim_core::{Catalog, Creator, Palette};

use crate::http::Target;

/// Bots whose appearance is at least this version were dressed by an earlier
/// run (a grant plus a slot and a dye per worn item), so a rerun skips them.
const DRESSED_VERSION: u64 = 5;
/// Chance of going without a hat.
const BAREHEADED: f64 = 0.3;

/// The catalog and palette the server ships (compiled in, like the server).
pub struct Wardrobe {
    catalog: Catalog,
    creator: Creator,
    /// The free basic palette (`palette.json` `basic`): any other colour uses up a brewed dye, which bots don't carry.
    basic: Vec<u8>,
}

impl Wardrobe {
    pub fn shipped() -> Wardrobe {
        let palette = Palette::from_json(include_str!("../../../data/items/palette.json"))
            .expect("the shipped palette is valid");
        let catalog =
            Catalog::from_json(include_str!("../../../data/items/catalog.json"), &palette)
                .expect("the shipped catalog is valid");
        let creator = Creator::from_json(include_str!("../../../data/characters/creator.json"))
            .expect("the shipped creator data is valid");
        Wardrobe {
            catalog,
            creator,
            basic: palette.basic.clone(),
        }
    }

    /// The slot of `item_def` if a body of `body_type` may wear it.
    fn wearable_slot(&self, body_type: u8, item_def: u16) -> Option<GearSlot> {
        let def = self.catalog.get(item_def)?;
        def.slot.filter(|_| self.creator.can_wear(body_type, def))
    }

    /// A random body: body type, height and face sliders, skin tone, a
    /// hair style of that body, hair and eye colour, with no outfit
    /// ([`dress`] picks that).
    pub fn random_body(&self, rng: &mut impl Rng) -> Appearance {
        let c = &self.creator;
        let mut byte = || rng.gen_range(0..=u8::MAX);
        let (height, face) = (byte(), [0; 5].map(|_| byte()));
        let body_type = rng.gen_range(0..c.body_types.len()) as u8;
        let styles: Vec<u16> = c.styles_of(body_type).map(|h| h.id).collect();
        Appearance {
            body_type,
            height,
            face,
            skin_tone: rng.gen_range(0..c.skin_tones.len()) as u8,
            hair_style: styles[rng.gen_range(0..styles.len())],
            hair_colour: rng.gen_range(0..c.hair_colours.len()) as u8,
            eye_colour: rng.gen_range(0..c.eye_colours.len()) as u8,
            ..Appearance::default()
        }
    }
}

/// What dressing did.
#[derive(Debug, Default, Clone, Copy, PartialEq, Eq)]
pub enum Dressed {
    /// Already dressed by an earlier run.
    #[default]
    Already,
    /// Changed this many slots.
    Now(u32),
}

fn json_of(res: crate::http::Response, what: &str) -> Result<Value, String> {
    if !res.is_success() {
        return Err(format!(
            "{what}: {} {}",
            res.status,
            String::from_utf8_lossy(&res.body)
        ));
    }
    serde_json::from_slice(&res.body).map_err(|e| format!("{what}: {e}"))
}

/// Dresses the bot whose session is `token`.
pub async fn dress(
    target: &Target,
    token: &str,
    wardrobe: &Wardrobe,
    rng: &mut impl Rng,
) -> Result<Dressed, String> {
    let me = json_of(
        target.get_as(token, "/api/characters/me").await?,
        "characters/me",
    )?;
    if me["appearance_version"].as_u64().unwrap_or(0) >= DRESSED_VERSION {
        return Ok(Dressed::Already);
    }
    let body_type = me["appearance"]["body_type"]
        .as_u64()
        .and_then(|b| u8::try_from(b).ok())
        .unwrap_or(0);
    let inv = json_of(
        target
            .get_as(token, "/api/inventory?kind=clothing&limit=100")
            .await?,
        "inventory",
    )?;
    let items: Vec<(i64, GearSlot)> = inv["items"]
        .as_array()
        .map(|a| {
            a.iter()
                .filter_map(|it| {
                    let id = it["id"].as_i64()?;
                    let def = u16::try_from(it["item_def"].as_u64()?).ok()?;
                    Some((id, wardrobe.wearable_slot(body_type, def)?))
                })
                .collect()
        })
        .unwrap_or_default();
    let mut changed = 0;
    for slot in GearSlot::ALL {
        let choices: Vec<i64> = items
            .iter()
            .filter(|(_, s)| *s == slot)
            .map(|(id, _)| *id)
            .collect();
        let pick = if choices.is_empty() || (slot == GearSlot::Hat && rng.gen_bool(BAREHEADED)) {
            None
        } else {
            Some(choices[rng.gen_range(0..choices.len())])
        };
        json_of(
            target
                .post_json_as(
                    token,
                    "/api/equipment",
                    &json!({ "slot": slot.id(), "item_id": pick }),
                )
                .await?,
            "equipment",
        )?;
        changed += 1;
        if let Some(id) = pick {
            let mut colour = || wardrobe.basic[rng.gen_range(0..wardrobe.basic.len())];
            let dyes = [colour(), colour(), colour()];
            json_of(
                target
                    .post_json_as(
                        token,
                        &format!("/api/items/{id}/dye"),
                        &json!({ "dyes": dyes }),
                    )
                    .await?,
                "dye",
            )?;
        }
    }
    Ok(Dressed::Now(changed))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn random_bodies_are_valid_and_varied() {
        use rand::SeedableRng;
        let w = Wardrobe::shipped();
        let palette = Palette::from_json(include_str!("../../../data/items/palette.json")).unwrap();
        let mut rng = rand::rngs::StdRng::seed_from_u64(7);
        let bodies: Vec<Appearance> = (0..50).map(|_| w.random_body(&mut rng)).collect();
        for b in &bodies {
            assert_eq!(w.creator.check_appearance(b, &w.catalog, &palette), Ok(()));
        }
        let types: std::collections::HashSet<u8> = bodies.iter().map(|b| b.body_type).collect();
        let hair: std::collections::HashSet<u16> = bodies.iter().map(|b| b.hair_style).collect();
        assert_eq!(types.len(), w.creator.body_types.len());
        assert!(hair.len() > 3);
        assert!(bodies.windows(2).any(|p| p[0].face != p[1].face));
    }

    #[test]
    fn bots_put_on_only_what_their_body_may_wear() {
        let w = Wardrobe::shipped();
        // The pleated skirt (302) for body_b, the kilt (305) for body_a.
        assert_eq!(w.wearable_slot(1, 302), Some(GearSlot::Legs));
        assert_eq!(w.wearable_slot(0, 302), None);
        assert_eq!(w.wearable_slot(0, 305), Some(GearSlot::Legs));
        assert_eq!(w.wearable_slot(1, 305), None);
        assert_eq!(w.wearable_slot(0, 301), Some(GearSlot::Legs));
        assert_eq!(w.wearable_slot(1, 301), Some(GearSlot::Legs));
    }

    #[test]
    fn the_shipped_wardrobe_has_every_slot() {
        let w = Wardrobe::shipped();
        for slot in GearSlot::ALL {
            assert!(
                w.catalog.items.iter().any(|i| i.slot == Some(slot)),
                "{slot:?}"
            );
        }
        // Free colours to dye with, so a fresh bot's dye requests succeed.
        assert!(w.basic.len() >= 2);
    }
}
