//! The starter grant: every new character gets the `starter` items of the
//! catalog for their house (about 14 clothing items and 12 furniture pieces),
//! one row each, and wears the starter outfit chosen in the creator. Their
//! Four-Poster Bed comes placed in their dorm room, by the wall the room's
//! shell keeps for it (the layout's `starter_furniture`); it is ordinary
//! placed furniture from then on.
//! Characters made before starter wardrobes get the same on their first request
//! ([`ensure_granted`], called by `characters::Player`) or WebSocket `Hello`
//! ([`grant_once`], called by the gateway), whichever comes first.
//!
//! [`take_off_unwearable`], on the same two paths, takes off
//! any worn garment the character's body may not wear (a dev character
//! dressed before the wear rule, or a later data change). A guard, not a
//! migration: it costs nothing unless the stored look breaks the rule.

use protocol::{Appearance, GearSlot, House, Outfit, QPos, Worn, yaw_to_u8};
use sim_core::ItemDef;
use sqlx::{PgConnection, PgPool};

use super::equip::{
    body_wear, forbidden_for, lock_appearance, refresh_appearance, set_slot, wear_message,
};
use super::{LOCATION_DORM, LOCATION_EQUIPPED, LOCATION_INVENTORY};
use crate::AppState;
use crate::catalog::shipped;
use crate::characters::{CHARACTER_COLUMNS, Character, CharacterRow};
use crate::http::ApiError;
use crate::ledger::{self, ItemMove, ItemOp, Reason};
use crate::realtime::world::WorldHandle;

/// The creator's preselected outfit for `house`: its `default`-tagged starter
/// clothing (body, legs and shoes), in the colours a grant gives them.
pub fn default_outfit(house: House) -> Outfit {
    let content = shipped();
    let mut outfit = Outfit::default();
    for item in content.catalog.starter_items(house) {
        if let (Some(slot), true) = (item.slot, item.has_tag(sim_core::catalog::TAG_DEFAULT)) {
            outfit.set(
                slot,
                Some(Worn::new(
                    item.id,
                    item.granted_dyes(house, &content.palette),
                )),
            );
        }
    }
    outfit
}

/// The creator defaults (every slider at its default) wearing
/// [`default_outfit`]: what `POST /api/characters` uses without an appearance.
pub fn default_appearance(house: House) -> Appearance {
    Appearance {
        outfit: default_outfit(house),
        ..shipped().creator.default_appearance(0)
    }
}

/// A new character may only wear its own starter clothing: every worn item
/// must be a `starter` item of `house` for its slot, and one `appearance`'s
/// body may wear ("That is female only."), else `400
/// invalid_appearance`. Indices, slots and dyes are checked by
/// `Creator::check_appearance`.
pub fn check_starter_outfit(appearance: &Appearance, house: House) -> Result<(), ApiError> {
    let catalog = &shipped().catalog;
    let body = body_wear(appearance);
    for slot in GearSlot::ALL {
        let Some(worn) = appearance.outfit.get(slot) else {
            continue;
        };
        let item = catalog
            .clothing(worn.item_def, slot)
            .filter(|item| item.is_starter_for(house));
        let Some(item) = item else {
            return Err(ApiError::bad_request(
                "invalid_appearance",
                format!(
                    "Your starting {} must come from the starter wardrobe.",
                    slot.id()
                ),
            ));
        };
        if let Some(w) = forbidden_for(body, item) {
            return Err(ApiError::bad_request("invalid_appearance", wear_message(w)));
        }
    }
    Ok(())
}

/// Where a starter item goes ([`starter_locations`]).
struct Planned {
    item: &'static ItemDef,
    /// `items.location`.
    location: &'static str,
    /// Placed in the dorm room here (wire values).
    spot: Option<(QPos, u8)>,
}

/// Where each starter item of `house` goes, in catalog order: worn (an item
/// of `outfit`), placed in the dorm room (a piece of the room's
/// `starter_furniture`, each spot taken once: `Some((pos, yaw))`) or the
/// inventory.
fn starter_locations(house: House, outfit: &Outfit) -> Vec<Planned> {
    let content = shipped();
    let mut spots: Vec<_> = crate::dorm::shells::shipped()
        .starter_furniture(house)
        .iter()
        .map(Some)
        .collect();
    let worn = |def: u16| {
        GearSlot::ALL
            .into_iter()
            .any(|s| outfit.get(s).is_some_and(|w| w.item_def == def))
    };
    content
        .catalog
        .starter_items(house)
        .map(|item| {
            if worn(item.id) {
                return Planned {
                    item,
                    location: LOCATION_EQUIPPED,
                    spot: None,
                };
            }
            let spot = spots
                .iter_mut()
                .find(|s| s.is_some_and(|p| p.item == item.id))
                .and_then(Option::take);
            match spot {
                Some(p) => Planned {
                    item,
                    location: LOCATION_DORM,
                    spot: Some((QPos::from_m(p.pos), yaw_to_u8(p.yaw))),
                },
                None => Planned {
                    item,
                    location: LOCATION_INVENTORY,
                    spot: None,
                },
            }
        })
        .collect()
}

/// Inserts the starter items of `house` for `character_id`, equips the
/// granted instances of `outfit`'s items with its dyes (which must be starter
/// items of that house, see [`check_starter_outfit`]) and places the room's
/// starter furniture (`dorms`, `dorm_placements`). Every item not worn gets
/// `ItemDef::granted_dyes`. Runs inside the caller's transaction.
pub(crate) async fn grant(
    conn: &mut PgConnection,
    character_id: i64,
    house: House,
    outfit: &Outfit,
) -> Result<(), sqlx::Error> {
    let content = shipped();
    let worn_slot = |def: u16| {
        GearSlot::ALL
            .into_iter()
            .find(|&s| outfit.get(s).is_some_and(|w| w.item_def == def))
    };
    let plan = starter_locations(house, outfit);
    let mut defs = Vec::new();
    let mut dyes: [Vec<i16>; 3] = Default::default();
    let mut locations = Vec::new();
    for &Planned { item, location, .. } in &plan {
        let d = match worn_slot(item.id).and_then(|s| outfit.get(s)) {
            Some(worn) => worn.dyes,
            None => item.granted_dyes(house, &content.palette),
        };
        defs.push(i32::from(item.id));
        for (column, dye) in dyes.iter_mut().zip(d) {
            column.push(i16::from(dye));
        }
        locations.push(location);
    }
    // Inserted in catalog order, so ids follow it and "newest first" lists
    // show the starter set in reverse catalog order.
    let granted: Vec<(i64, i32)> = sqlx::query_as(
        "INSERT INTO items (owner_id, item_def, dye_primary, dye_secondary, dye_trim, location)
         SELECT $1, d, p, s, t, l
         FROM unnest($2::int[], $3::smallint[], $4::smallint[], $5::smallint[], $6::text[])
              WITH ORDINALITY AS u(d, p, s, t, l, n)
         ORDER BY n
         RETURNING id, item_def",
    )
    .bind(character_id)
    .bind(&defs)
    .bind(&dyes[0])
    .bind(&dyes[1])
    .bind(&dyes[2])
    .bind(&locations)
    .fetch_all(&mut *conn)
    .await?;

    let (slots, item_ids): (Vec<&str>, Vec<i64>) = granted
        .iter()
        .filter_map(|&(id, def)| {
            let slot = worn_slot(u16::try_from(def).ok()?)?;
            Some((slot.id(), id))
        })
        .unzip();
    sqlx::query(
        "INSERT INTO equipment (character_id, slot, item_id)
         SELECT $1, s, i FROM unnest($2::text[], $3::bigint[]) AS u(s, i)",
    )
    .bind(character_id)
    .bind(&slots)
    .bind(&item_ids)
    .execute(&mut *conn)
    .await?;

    // Each starter item is granted once, so its def finds its plan.
    let planned = |def: i32| plan.iter().find(|p| i32::from(p.item.id) == def);
    // The starter furniture placed in the room.
    let placed: Vec<(i64, QPos, u8)> = granted
        .iter()
        .filter_map(|&(id, def)| planned(def)?.spot.map(|(pos, yaw)| (id, pos, yaw)))
        .collect();
    if !placed.is_empty() {
        sqlx::query("INSERT INTO dorms (owner_id) VALUES ($1) ON CONFLICT (owner_id) DO NOTHING")
            .bind(character_id)
            .execute(&mut *conn)
            .await?;
        let column = |f: fn(&(i64, QPos, u8)) -> i32| placed.iter().map(f).collect::<Vec<i32>>();
        let ids: Vec<i64> = placed.iter().map(|p| p.0).collect();
        sqlx::query(
            "INSERT INTO dorm_placements (item_id, owner_id, pos_x, pos_y, pos_z, yaw)
             SELECT i, $1, x, y, z, w
             FROM unnest($2::bigint[], $3::int[], $4::int[], $5::int[], $6::int[]) AS u(i, x, y, z, w)",
        )
        .bind(character_id)
        .bind(&ids)
        .bind(column(|p| p.1.x))
        .bind(column(|p| p.1.y))
        .bind(column(|p| p.1.z))
        .bind(column(|p| i32::from(p.2)))
        .execute(&mut *conn)
        .await?;
    }

    // Every granted item enters the ledger, and the starting
    // purse comes with the wardrobe (once: `purse_granted_at`).
    let moves: Vec<ItemMove<'_>> = granted
        .iter()
        .map(|&(id, def)| {
            let item_def = u16::try_from(def).unwrap_or(0);
            let location = planned(def).map_or(LOCATION_INVENTORY, |p| p.location);
            ItemMove::new(id, item_def, ItemOp::Grant, 1, Reason::Starter)
                .to(character_id, location)
        })
        .collect();
    ledger::record_item_moves(conn, &moves)
        .await
        .map_err(ledger_db)?;
    let purse = i64::from(content.economy.starting_purse);
    let balance: Option<i64> = sqlx::query_scalar(
        "UPDATE characters SET crowns = crowns + $2, purse_granted_at = now()
         WHERE id = $1 AND purse_granted_at IS NULL RETURNING crowns",
    )
    .bind(character_id)
    .bind(purse)
    .fetch_optional(&mut *conn)
    .await?;
    if let Some(balance) = balance {
        ledger::record_crowns(
            conn,
            character_id,
            purse,
            balance,
            Reason::StartingPurse,
            None,
        )
        .await
        .map_err(ledger_db)?;
    }
    Ok(())
}

/// The ledger's errors here can only be database errors (quantities are 1).
fn ledger_db(e: ledger::LedgerError) -> sqlx::Error {
    match e {
        ledger::LedgerError::Db(db) => db,
        other => sqlx::Error::Protocol(other.to_string()),
    }
}

/// Gives a character made before starter wardrobes (no `starter_granted_at`) the
/// starter grant, wearing the house's [`default_outfit`] over its stored (or
/// the creator's default) look, and bumps `appearance_version`. Safe to race:
/// the character row is locked and the flag re-checked.
pub(crate) async fn ensure_granted(
    state: &AppState,
    character: Character,
) -> Result<Character, ApiError> {
    grant_once(&state.pool, state.world(), character).await
}

/// [`ensure_granted`] for callers without an `AppState`: the WebSocket
/// gateway grants on `Hello`, so an early character entering the castle before
/// any HTTP request is dressed too. `world` hears about the new look.
pub async fn grant_once(
    pool: &PgPool,
    world: Option<&WorldHandle>,
    character: Character,
) -> Result<Character, ApiError> {
    let mut tx = pool.begin().await?;
    let granted: bool = sqlx::query_scalar(
        "SELECT starter_granted_at IS NOT NULL FROM characters WHERE id = $1 FOR NO KEY UPDATE",
    )
    .bind(character.id)
    .fetch_one(&mut *tx)
    .await?;
    if granted {
        // Another request granted it meanwhile; return what it stored.
        tx.commit().await?;
        return reload(pool, character.id).await;
    }
    let outfit = default_outfit(character.house);
    // `Character::appearance` is `Appearance::default()` for a NULL column;
    // the creator's defaults are the better base then.
    let base = if character.appearance == Appearance::default() {
        shipped().creator.default_appearance(0)
    } else {
        character.appearance
    };
    let appearance = Appearance { outfit, ..base };
    grant(&mut tx, character.id, character.house, &outfit).await?;
    let row: CharacterRow = sqlx::query_as(&format!(
        "UPDATE characters
         SET appearance = $2, appearance_version = appearance_version + 1,
             starter_granted_at = now(), updated_at = now()
         WHERE id = $1 RETURNING {CHARACTER_COLUMNS}"
    ))
    .bind(character.id)
    .bind(sqlx::types::Json(appearance))
    .fetch_one(&mut *tx)
    .await?;
    tx.commit().await?;
    let updated = row
        .into_character()
        .ok_or_else(|| ApiError::internal("character row has an unknown house"))?;
    tracing::info!(character_id = updated.id, "granted the starter wardrobe");
    if let Some(world) = world {
        world.appearance_changed(updated.id, updated.appearance, updated.appearance_version);
    }
    Ok(updated)
}

/// The slots of `appearance` holding a garment its body may not wear.
fn unwearable_slots(appearance: &Appearance) -> Vec<GearSlot> {
    let catalog = &shipped().catalog;
    let body = body_wear(appearance);
    GearSlot::ALL
        .into_iter()
        .filter(|&slot| {
            appearance.outfit.get(slot).is_some_and(|worn| {
                catalog
                    .get(worn.item_def)
                    .is_some_and(|item| forbidden_for(body, item).is_some())
            })
        })
        .collect()
}

/// Takes off whatever `character` wears that its body may
/// not wear (back to the inventory, ledger reason `equip`, through
/// [`set_slot`] and [`refresh_appearance`] in one transaction) and tells
/// `world`. Returns the character unchanged, without touching the database,
/// when its stored look keeps the rule (every login but a dev character's
/// first after the rule). Called where a character is loaded for play: the
/// HTTP [`Player`](crate::characters::Player) and the gateway's `Hello`.
pub async fn take_off_unwearable(
    pool: &PgPool,
    world: Option<&WorldHandle>,
    character: Character,
) -> Result<Character, ApiError> {
    if unwearable_slots(&character.appearance).is_empty() {
        return Ok(character);
    }
    let mut tx = pool.begin().await?;
    let before = lock_appearance(&mut tx, character.id).await?;
    let slots = unwearable_slots(&before.appearance);
    for &slot in &slots {
        set_slot(&mut tx, character.id, before.wear(), slot, None)
            .await
            .map_err(ApiError::from)?;
    }
    let look = refresh_appearance(&mut tx, character.id, before).await?;
    tx.commit().await?;
    if look.changed {
        tracing::info!(
            character_id = character.id,
            ?slots,
            "took off garments the body may not wear"
        );
        if let Some(world) = world {
            world.appearance_changed(character.id, look.appearance, look.version);
        }
    }
    Ok(Character {
        appearance: look.appearance,
        appearance_version: look.version,
        ..character
    })
}

async fn reload(pool: &PgPool, character_id: i64) -> Result<Character, ApiError> {
    let row: CharacterRow = sqlx::query_as(&format!(
        "SELECT {CHARACTER_COLUMNS} FROM characters WHERE id = $1"
    ))
    .bind(character_id)
    .fetch_one(pool)
    .await?;
    row.into_character()
        .ok_or_else(|| ApiError::internal("character row has an unknown house"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn default_outfits_are_starter_clothing_in_house_colours() {
        for house in House::ALL {
            let outfit = default_outfit(house);
            assert!(outfit.body.is_some() && outfit.legs.is_some() && outfit.shoes.is_some());
            let a = default_appearance(house);
            assert!(check_starter_outfit(&a, house).is_ok());
            for body_type in [0, 1] {
                let a = Appearance { body_type, ..a };
                assert!(check_starter_outfit(&a, house).is_ok(), "{body_type}");
            }
            let c = shipped();
            assert_eq!(
                c.creator.check_appearance(&a, &c.catalog, &c.palette),
                Ok(())
            );
            // The house robe carries the house's colours.
            let robe = outfit.body.unwrap();
            let colours = c.palette.house(house).unwrap();
            assert!(robe.dyes.contains(&colours.primary));
        }
    }

    /// The starter grant's plan: the default outfit worn, the Four-Poster
    /// Bed placed at the house's spot (the shell's `starter_furniture`), and
    /// everything else in the inventory.
    #[test]
    fn the_bed_is_planned_into_the_room() {
        for house in House::ALL {
            let outfit = default_outfit(house);
            let plan = starter_locations(house, &outfit);
            let spot = crate::dorm::shells::shipped().starter_furniture(house)[0];
            let placed: Vec<_> = plan
                .iter()
                .filter(|p| p.location == LOCATION_DORM)
                .collect();
            assert_eq!(placed.len(), 1, "{house:?}");
            let Planned {
                item: bed,
                spot: at,
                ..
            } = placed[0];
            assert_eq!(bed.name, "Four-Poster Bed");
            assert_eq!(
                *at,
                Some((QPos::from_m(spot.pos), yaw_to_u8(spot.yaw))),
                "{house:?}"
            );
            assert_eq!(
                plan.iter()
                    .filter(|p| p.location == LOCATION_EQUIPPED)
                    .count(),
                3
            );
            assert!(
                plan.iter()
                    .all(|p| (p.location == LOCATION_DORM) == p.spot.is_some())
            );
            assert_eq!(plan.len(), shipped().catalog.starter_items(house).count());
        }
    }

    #[test]
    fn other_houses_starter_items_are_refused() {
        let roarwing_robe = default_outfit(House::Roarwing).body;
        let outfit = Outfit {
            body: roarwing_robe,
            ..Outfit::default()
        };
        let look = |outfit| Appearance {
            outfit,
            ..default_appearance(House::Serpentyne)
        };
        let err = check_starter_outfit(&look(outfit), House::Serpentyne).unwrap_err();
        assert_eq!(err.code, "invalid_appearance");
        assert!(check_starter_outfit(&look(Outfit::default()), House::Serpentyne).is_ok());
    }

    /// The pleated skirt (a starter item, female only) is
    /// offered to body_b only; the creator's refusal names the class.
    #[test]
    fn the_starter_skirt_is_for_the_female_body_only() {
        let skirt = shipped().catalog.get(302).unwrap();
        assert!(skirt.is_starter_for(House::Burrowsby));
        let mut a = default_appearance(House::Burrowsby);
        a.outfit.legs = Some(Worn::new(302, [0; 3]));
        let male = Appearance { body_type: 0, ..a };
        let female = Appearance { body_type: 1, ..a };
        let err = check_starter_outfit(&male, House::Burrowsby).unwrap_err();
        assert_eq!(err.code, "invalid_appearance");
        assert_eq!(err.message, "That is female only.");
        assert!(check_starter_outfit(&female, House::Burrowsby).is_ok());
        assert_eq!(unwearable_slots(&male), vec![GearSlot::Legs]);
        assert!(unwearable_slots(&female).is_empty());
    }
}
