//! Browsing the Owl Exchange: the query for one page of active listings
//! with every filter and sort, shaped so each path uses one of the partial
//! indexes of `db/migrations/0007_economy.sql` (`WHERE state = 'active'`):
//!
//! | Filters | Newest (`id DESC`) | Price (`price, id`, either way) |
//! | --- | --- | --- |
//! | none | `exchange_active_newest_idx` | `exchange_active_price_idx` |
//! | category | `exchange_cat_newest_idx` | `exchange_cat_price_idx` |
//! | category + subcategory | `exchange_sub_newest_idx` | `exchange_sub_price_idx` |
//! | name (`q` → catalog ids) | `exchange_def_newest_idx` | `exchange_def_price_idx` |
//!
//! Keyset pagination on (sort key, id): the next page continues after the
//! last row's `(price, id)` (or `id`), an index condition, so page 100 costs
//! what page 1 does. Level, colour and house filters are checked on the
//! index's rows. The SQL is built with only the filters present (each shape
//! its own prepared statement), never `($1 IS NULL OR ...)`, which would
//! hide the index choice from a generic plan.

use protocol::House;
use serde::{Deserialize, Serialize};
use sqlx::{Postgres, QueryBuilder};

use super::LISTING_FIELDS;
use crate::catalog::shipped;
use crate::http::ApiError;

/// `?sort=`.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Sort {
    /// Newest first (the default).
    #[default]
    Newest,
    /// Cheapest first.
    Price,
    /// Dearest first.
    PriceDesc,
}

/// The page cursor: the sort it belongs to and the last row's key.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct Cursor {
    pub sort: Sort,
    pub price: i64,
    pub id: i64,
}

/// `GET /api/exchange/listings` query string (pagination apart).
#[derive(Debug, Clone, Default, Deserialize)]
pub struct BrowseQuery {
    pub category: Option<String>,
    pub subcategory: Option<String>,
    pub q: Option<String>,
    pub min_level: Option<u8>,
    pub max_level: Option<u8>,
    pub colour: Option<u8>,
    pub house: Option<String>,
    #[serde(default)]
    pub sort: Sort,
}

/// A checked search: what [`build`] turns into SQL.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Search {
    pub category: Option<String>,
    pub subcategory: Option<String>,
    /// Catalog ids whose names match `q` (`Some(empty)` matches nothing).
    pub item_defs: Option<Vec<i32>>,
    pub min_level: Option<i16>,
    pub max_level: Option<i16>,
    pub colour: Option<i16>,
    /// A lower-case house id.
    pub house: Option<String>,
    pub sort: Sort,
    pub after: Option<Cursor>,
    /// Rows to fetch (the page size plus one).
    pub fetch: i64,
}

/// Longest name search accepted.
pub const QUERY_MAX_CHARS: usize = 64;

fn invalid(message: &'static str) -> ApiError {
    ApiError::bad_request("invalid_query", message)
}

impl Search {
    /// Checks a query string: known category and subcategory (a
    /// subcategory needs its category), no empty values, levels within
    /// `1..=LEVEL_MAX` in order, a palette colour, a house id; `q` resolved
    /// to catalog ids. The cursor must come from the same sort
    /// (`400 invalid_cursor` otherwise).
    pub fn from_query(
        q: &BrowseQuery,
        after: Option<Cursor>,
        fetch: i64,
    ) -> Result<Search, ApiError> {
        let empty = |v: &Option<String>| v.as_deref().is_some_and(|s| s.trim().is_empty());
        if empty(&q.category) || empty(&q.subcategory) || empty(&q.q) || empty(&q.house) {
            return Err(invalid("Filters may not be empty."));
        }
        match (q.category.as_deref(), q.subcategory.as_deref()) {
            (None, Some(_)) => return Err(invalid("A subcategory needs its category.")),
            (Some(c), s) if !sim_core::exchange::is_valid(c, s) => {
                return Err(invalid("Unknown category or subcategory."));
            }
            _ => {}
        }
        let level = |l: Option<u8>| match l {
            Some(l) if !(1..=sim_core::catalog::LEVEL_MAX).contains(&l) => {
                Err(invalid("Levels go from 1 to 20."))
            }
            l => Ok(l.map(i16::from)),
        };
        let (min_level, max_level) = (level(q.min_level)?, level(q.max_level)?);
        if let (Some(lo), Some(hi)) = (min_level, max_level)
            && lo > hi
        {
            return Err(invalid("min_level is above max_level."));
        }
        if q.colour
            .is_some_and(|c| usize::from(c) >= shipped().palette.colours.len())
        {
            return Err(invalid("No such colour."));
        }
        let house = match q.house.as_deref() {
            None => None,
            Some(h) => Some(
                House::from_id(&h.trim().to_lowercase())
                    .ok_or_else(|| invalid("No such house."))?
                    .id()
                    .to_owned(),
            ),
        };
        let item_defs = match q.q.as_deref() {
            None => None,
            Some(text) if text.chars().count() > QUERY_MAX_CHARS => {
                return Err(invalid("That search is too long."));
            }
            Some(text) => Some(
                shipped()
                    .catalog
                    .search_names(text)
                    .into_iter()
                    .map(i32::from)
                    .collect(),
            ),
        };
        if after.is_some_and(|c| c.sort != q.sort) {
            return Err(ApiError::bad_request(
                "invalid_cursor",
                "That page cursor is not valid. Start again from the first page.",
            ));
        }
        Ok(Search {
            category: q.category.clone(),
            subcategory: q.subcategory.clone(),
            item_defs,
            min_level,
            max_level,
            colour: q.colour.map(i16::from),
            house,
            sort: q.sort,
            after,
            fetch,
        })
    }

    /// Whether the search can match anything (a name search that matched
    /// no catalog item cannot; it needs no query).
    pub fn can_match(&self) -> bool {
        self.item_defs.as_ref().is_none_or(|d| !d.is_empty())
    }

    /// The cursor after a row with this price and id.
    pub fn cursor(&self, price: i64, id: i64) -> Cursor {
        Cursor {
            sort: self.sort,
            price,
            id,
        }
    }
}

/// The page query for `search`, after `prefix` (`""`, or an `EXPLAIN` for
/// the plan tests). Selects a `ListingRow`'s columns.
///
/// The page of listings is chosen in a subquery of `exchange_listings`
/// alone, and only its rows are joined to their sellers' names: with the
/// join inside, the planner may prefer a merge join through the seller index
/// and sort every match, instead of walking the sort index and stopping
/// after a page.
pub fn build<'a>(search: &'a Search, prefix: &str) -> QueryBuilder<'a, Postgres> {
    let order = match search.sort {
        Sort::Newest => " ORDER BY x.id DESC",
        Sort::Price => " ORDER BY x.price, x.id",
        Sort::PriceDesc => " ORDER BY x.price DESC, x.id DESC",
    };
    let mut b = QueryBuilder::new(prefix);
    b.push("SELECT x.*, c.name AS seller_name FROM (SELECT ");
    b.push(LISTING_FIELDS);
    // The literal predicate matches the partial indexes.
    b.push(" FROM exchange_listings x WHERE x.state = 'active' AND x.expires_at > now()");
    if let Some(c) = &search.category {
        b.push(" AND x.category = ").push_bind(c.as_str());
    }
    if let Some(s) = &search.subcategory {
        b.push(" AND x.subcategory = ").push_bind(s.as_str());
    }
    if let Some(defs) = &search.item_defs {
        b.push(" AND x.item_def = ANY(")
            .push_bind(defs.as_slice())
            .push(")");
    }
    if let Some(l) = search.min_level {
        b.push(" AND x.required_level >= ").push_bind(l);
    }
    if let Some(l) = search.max_level {
        b.push(" AND x.required_level <= ").push_bind(l);
    }
    if let Some(c) = search.colour {
        b.push(" AND x.colour = ").push_bind(c);
    }
    if let Some(h) = &search.house {
        b.push(" AND x.house_theme = ").push_bind(h.as_str());
    }
    match (search.sort, search.after) {
        (Sort::Newest, Some(c)) => {
            b.push(" AND x.id < ").push_bind(c.id);
        }
        (Sort::Price, Some(c)) => {
            b.push(" AND (x.price, x.id) > (")
                .push_bind(c.price)
                .push(", ")
                .push_bind(c.id)
                .push(")");
        }
        (Sort::PriceDesc, Some(c)) => {
            b.push(" AND (x.price, x.id) < (")
                .push_bind(c.price)
                .push(", ")
                .push_bind(c.id)
                .push(")");
        }
        (_, None) => {}
    }
    b.push(order);
    b.push(" LIMIT ").push_bind(search.fetch);
    b.push(") x JOIN characters c ON c.id = x.seller_id");
    b.push(order);
    b
}

#[cfg(test)]
mod tests {
    use super::*;

    fn query() -> BrowseQuery {
        BrowseQuery::default()
    }

    #[test]
    fn queries_are_checked() {
        let ok = |q: &BrowseQuery| Search::from_query(q, None, 26);
        assert!(ok(&query()).is_ok());
        let code = |q: BrowseQuery| ok(&q).unwrap_err().code;
        assert_eq!(
            code(BrowseQuery {
                category: Some("wands".into()),
                ..query()
            }),
            "invalid_query"
        );
        assert_eq!(
            code(BrowseQuery {
                subcategory: Some("boots".into()),
                ..query()
            }),
            "invalid_query"
        );
        assert_eq!(
            code(BrowseQuery {
                category: Some("hats".into()),
                subcategory: Some("boots".into()),
                ..query()
            }),
            "invalid_query"
        );
        assert_eq!(
            code(BrowseQuery {
                min_level: Some(5),
                max_level: Some(2),
                ..query()
            }),
            "invalid_query"
        );
        assert_eq!(
            code(BrowseQuery {
                colour: Some(255),
                ..query()
            }),
            "invalid_query"
        );
        assert_eq!(
            code(BrowseQuery {
                q: Some(" ".into()),
                ..query()
            }),
            "invalid_query"
        );
        let house = ok(&BrowseQuery {
            house: Some("Skytalon".into()),
            ..query()
        })
        .unwrap();
        assert_eq!(house.house.as_deref(), Some("skytalon"));
        // A cursor of another sort is refused.
        let c = Cursor {
            sort: Sort::Price,
            price: 5,
            id: 9,
        };
        assert_eq!(
            Search::from_query(&query(), Some(c), 26).unwrap_err().code,
            "invalid_cursor"
        );
        // A name nobody sells matches nothing without asking the database.
        let none = ok(&BrowseQuery {
            q: Some("zzzzqqq".into()),
            ..query()
        })
        .unwrap();
        assert!(!none.can_match());
    }

    #[test]
    fn the_sql_has_only_the_filters_given_and_keyset_conditions() {
        let s = Search {
            category: Some("hats".into()),
            sort: Sort::Price,
            after: Some(Cursor {
                sort: Sort::Price,
                price: 10,
                id: 99,
            }),
            fetch: 26,
            ..Search::default()
        };
        let sql = build(&s, "").into_sql();
        assert!(sql.contains("x.state = 'active'"));
        assert!(sql.contains("x.category = $1"));
        assert!(sql.contains("(x.price, x.id) > ($2, $3)"));
        assert!(sql.contains("ORDER BY x.price, x.id LIMIT $4) x JOIN characters"));
        assert!(sql.ends_with("ORDER BY x.price, x.id"));
        assert!(!sql.contains("subcategory ="));
        let newest = build(
            &Search {
                fetch: 26,
                ..Search::default()
            },
            "EXPLAIN ",
        )
        .into_sql();
        assert!(newest.starts_with("EXPLAIN SELECT"));
        assert!(newest.contains("ORDER BY x.id DESC LIMIT $1)"));
    }
}
