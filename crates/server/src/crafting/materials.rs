//! Using up what a craft or a recolouring costs (Gamp's Law: nothing comes
//! from nothing): recipe inputs by item definition, and dyes by palette
//! family (any dye item of that family will do).
//!
//! The caller names *what* is needed ([`Needs`]), not which rows: the
//! server picks the character's inventory rows, oldest first. The protocol,
//! inside one transaction that already holds the character's row lock:
//!
//! 1. [`lock_for`] locks every candidate row (and the item being crafted
//!    on, if any) in one statement, by ascending id: the lock order every
//!    economy transaction follows;
//! 2. [`allocate`] (pure) decides how many units each row gives, or says
//!    what is missing;
//! 3. [`use_up`] consumes them through `crate::items::consume`, which
//!    records the item ledger.
//!
//! Only rows in the inventory count: worn, placed, mailed and listed items
//! are never used up.

use std::collections::{BTreeMap, HashMap};

use sim_core::ItemKind;
use sqlx::PgConnection;

use crate::catalog::shipped;
use crate::http::ApiError;
use crate::inventory::LOCATION_INVENTORY;
use crate::items::{self, ItemError, LockedItem};
use crate::ledger::Reason;

/// What an action uses up: item definitions and dye families, each with a
/// unit count.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub(crate) struct Needs {
    /// `(item_def, units)`.
    pub items: Vec<(u16, u32)>,
    /// `(palette family index, dye units)`, as `Palette::dyes_needed` gives.
    pub dyes: Vec<(u8, u32)>,
}

impl Needs {
    pub fn is_empty(&self) -> bool {
        self.items.is_empty() && self.dyes.is_empty()
    }

    /// Every item definition that can cover a need: the inputs, and the
    /// dye items of each family.
    fn candidate_defs(&self) -> Vec<i32> {
        let mut defs: Vec<i32> = self.items.iter().map(|&(d, _)| i32::from(d)).collect();
        for &(family, _) in &self.dyes {
            defs.extend(dye_defs(family).map(i32::from));
        }
        defs.sort_unstable();
        defs.dedup();
        defs
    }
}

/// The dye items (catalog ids) of palette family `family`.
pub(crate) fn dye_defs(family: u8) -> impl Iterator<Item = u16> {
    let content = shipped();
    content
        .catalog
        .items
        .iter()
        .filter(move |i| {
            i.kind == ItemKind::Dye
                && i.dye
                    .as_ref()
                    .and_then(|d| content.palette.family_index(&d.family))
                    == Some(family)
        })
        .map(|i| i.id)
}

/// The palette family a dye item colours with.
fn dye_family_of(item_def: u16) -> Option<u8> {
    let content = shipped();
    let dye = content.catalog.get(item_def)?.dye.as_ref()?;
    content.palette.family_index(&dye.family)
}

/// Locks the character's inventory rows that could cover `needs`, plus
/// `extra` (an item the action works on, wherever it is), in one statement
/// by ascending id. Returns `(the extra items found, the candidates)`.
/// The caller already holds the character's row lock, so the unlocked
/// candidate query cannot race another change of these rows.
pub(crate) async fn lock_for(
    conn: &mut PgConnection,
    owner: i64,
    needs: &Needs,
    extra: &[i64],
) -> Result<(Vec<LockedItem>, Vec<LockedItem>), sqlx::Error> {
    let mut ids: Vec<i64> = if needs.is_empty() {
        Vec::new()
    } else {
        sqlx::query_scalar(
            "SELECT id FROM items
             WHERE owner_id = $1 AND location = $2 AND item_def = ANY($3)
             ORDER BY id",
        )
        .bind(owner)
        .bind(LOCATION_INVENTORY)
        .bind(needs.candidate_defs())
        .fetch_all(&mut *conn)
        .await?
    };
    ids.extend_from_slice(extra);
    ids.sort_unstable();
    ids.dedup();
    let locked = items::lock(conn, owner, &ids).await?;
    let (extra_rows, candidates) = locked.into_iter().partition(|r| extra.contains(&r.id));
    Ok((extra_rows, candidates))
}

/// What is missing for an action: `(item_def, units)` and `(family, dyes)`.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub(crate) struct Shortfall {
    pub items: Vec<(u16, u32)>,
    pub dyes: Vec<(u8, u32)>,
}

impl Shortfall {
    /// `409 missing_inputs` (materials) or `409 missing_dyes` (only dyes
    /// short), naming what is missing.
    pub fn to_api(&self) -> ApiError {
        let content = shipped();
        let mut parts: Vec<String> = self
            .items
            .iter()
            .map(|&(def, n)| {
                let name = content
                    .catalog
                    .get(def)
                    .map_or("an item", |d| d.name.as_str());
                format!("{n} more {name}")
            })
            .collect();
        parts.extend(self.dyes.iter().map(|&(f, n)| {
            let name = content
                .palette
                .families
                .get(usize::from(f))
                .map_or("matching", |f| f.name.as_str());
            format!("{n} more {name} dye")
        }));
        let message = format!("You need {}.", parts.join(", "));
        if self.items.is_empty() {
            ApiError::conflict("missing_dyes", message)
        } else {
            ApiError::conflict("missing_inputs", message)
        }
    }
}

/// Decides which rows cover `needs`: oldest row first within each need, a
/// row never counted twice, `exclude` (the item being worked on) never
/// counted. Pure, so the allocation rule is tested on its own.
pub(crate) fn allocate(
    needs: &Needs,
    candidates: &[LockedItem],
    exclude: Option<i64>,
) -> Result<Vec<(LockedItem, u32)>, Shortfall> {
    let mut left: HashMap<i64, u32> = candidates
        .iter()
        .filter(|r| Some(r.id) != exclude && r.location == LOCATION_INVENTORY)
        .map(|r| (r.id, r.quantity))
        .collect();
    // (row id → units taken), in id order for a stable ledger.
    let mut taken: BTreeMap<i64, u32> = BTreeMap::new();
    let mut short = Shortfall::default();
    let mut take = |matches: &dyn Fn(&LockedItem) -> bool, mut want: u32| -> u32 {
        for r in candidates.iter().filter(|r| matches(r)) {
            if want == 0 {
                break;
            }
            let Some(have) = left.get_mut(&r.id) else {
                continue;
            };
            let n = want.min(*have);
            if n > 0 {
                *have -= n;
                want -= n;
                *taken.entry(r.id).or_default() += n;
            }
        }
        want
    };
    for &(def, units) in &needs.items {
        let missing = take(&|r| r.item_def == def, units);
        if missing > 0 {
            short.items.push((def, missing));
        }
    }
    for &(family, units) in &needs.dyes {
        let missing = take(&|r| dye_family_of(r.item_def) == Some(family), units);
        if missing > 0 {
            short.dyes.push((family, missing));
        }
    }
    if short != Shortfall::default() {
        return Err(short);
    }
    Ok(taken
        .into_iter()
        .filter_map(|(id, n)| {
            candidates
                .iter()
                .find(|r| r.id == id)
                .map(|r| (r.clone(), n))
        })
        .collect())
}

/// Consumes each allocated row's units (`ItemOp::Consume`, `reason`, `ref_id`).
pub(crate) async fn use_up(
    conn: &mut PgConnection,
    allocation: &[(LockedItem, u32)],
    reason: Reason,
    ref_id: Option<i64>,
) -> Result<(), ItemError> {
    for (row, units) in allocation {
        items::consume(&mut *conn, row, *units, reason, ref_id).await?;
    }
    Ok(())
}

/// An item-helper failure as an API error (nothing it reports is the
/// player's fault once the checks above passed).
pub(crate) fn item_error(e: ItemError) -> ApiError {
    ApiError::internal(e)
}

#[cfg(test)]
mod tests {
    use protocol::Finish;

    use super::*;

    fn row(id: i64, item_def: u16, quantity: u32) -> LockedItem {
        LockedItem {
            id,
            owner_id: 1,
            item_def,
            dyes: [0; 3],
            finish: Finish::None,
            quantity,
            location: LOCATION_INVENTORY.into(),
            maker_id: None,
            maker_name: None,
        }
    }

    fn blues() -> u8 {
        shipped().palette.family_index("blues").unwrap()
    }

    fn blue_dye() -> u16 {
        dye_defs(blues()).next().unwrap()
    }

    #[test]
    fn oldest_rows_go_first_and_none_twice() {
        let rows = [row(5, 3001, 2), row(9, 3001, 5), row(12, blue_dye(), 3)];
        let needs = Needs {
            items: vec![(3001, 4)],
            dyes: vec![(blues(), 2)],
        };
        let got = allocate(&needs, &rows, None).unwrap();
        let units: Vec<(i64, u32)> = got.iter().map(|(r, n)| (r.id, *n)).collect();
        assert_eq!(units, vec![(5, 2), (9, 2), (12, 2)]);
    }

    #[test]
    fn shortfalls_name_what_is_missing() {
        let rows = [row(5, 3001, 2), row(7, blue_dye(), 1)];
        let needs = Needs {
            items: vec![(3001, 3), (3003, 1)],
            dyes: vec![(blues(), 2)],
        };
        let short = allocate(&needs, &rows, None).unwrap_err();
        assert_eq!(short.items, vec![(3001, 1), (3003, 1)]);
        assert_eq!(short.dyes, vec![(blues(), 1)]);
        assert_eq!(short.to_api().code, "missing_inputs");
        let dyes_only = Shortfall {
            items: vec![],
            dyes: vec![(blues(), 1)],
        };
        let e = dyes_only.to_api();
        assert_eq!(e.code, "missing_dyes");
        assert!(e.message.contains("Blues"), "{}", e.message);
    }

    #[test]
    fn the_worked_item_and_rows_elsewhere_never_count() {
        let mut mailed = row(3, 3001, 9);
        mailed.location = "mail".into();
        let rows = [mailed, row(4, 3001, 9)];
        let needs = Needs {
            items: vec![(3001, 1)],
            dyes: vec![],
        };
        assert!(allocate(&needs, &rows, Some(4)).is_err());
        assert_eq!(allocate(&needs, &rows, None).unwrap()[0].0.id, 4);
    }
}
