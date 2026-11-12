//! Who is online, by name, for `GET /api/players`
//! (`crate::players`).
//!
//! The gateway enters a character when the world has taken it in and leaves
//! it when the world has let it go ([`Presence::enter`] returns a guard that
//! leaves on drop), so the list covers every zone: castle, dorms, rooms and
//! classes. A login that replaces an older connection of the same account
//! enters before the old one leaves, hence the count per name.
//!
//! Kept off the world thread: a `BTreeMap` keyed by `characters::name_key`
//! behind a plain mutex. A page is one ordered scan of at most the player cap
//! (1,000 short strings), so the lock is held for microseconds and the tick
//! never waits on it.

use std::collections::BTreeMap;
use std::ops::Bound;
use std::sync::Mutex;

use protocol::House;

use crate::characters::name_key;

#[derive(Debug)]
struct Online {
    name: String,
    house: House,
    /// Connections of this character in the world (2 only while a new
    /// login takes over from an old one).
    count: u32,
}

/// One online character as the list shows it: name and house, never where.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize)]
pub struct Listed {
    pub name: String,
    pub house: House,
}

/// The online characters, by name key.
#[derive(Debug, Default)]
pub struct Presence {
    map: Mutex<BTreeMap<String, Online>>,
}

/// Leaves [`Presence`] on drop (see [`Presence::enter`]).
#[must_use]
pub struct Present<'a> {
    presence: &'a Presence,
    key: String,
}

impl Drop for Present<'_> {
    fn drop(&mut self) {
        self.presence.leave(&self.key);
    }
}

impl Presence {
    fn lock(&self) -> std::sync::MutexGuard<'_, BTreeMap<String, Online>> {
        self.map.lock().expect("presence lock poisoned")
    }

    /// Lists `name` until the returned guard drops.
    pub fn enter(&self, name: &str, house: House) -> Present<'_> {
        let key = name_key(name);
        self.lock()
            .entry(key.clone())
            .and_modify(|o| o.count += 1)
            .or_insert_with(|| Online {
                name: name.to_owned(),
                house,
                count: 1,
            });
        Present {
            presence: self,
            key,
        }
    }

    fn leave(&self, key: &str) {
        let mut map = self.lock();
        if let Some(o) = map.get_mut(key) {
            o.count -= 1;
            if o.count == 0 {
                map.remove(key);
            }
        }
    }

    /// How many characters are online.
    pub fn len(&self) -> usize {
        self.lock().len()
    }

    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }

    /// Up to `take` online characters whose name key contains `q` (already
    /// a name key; empty matches all), after the name key `after`, in name
    /// key order, each with its key (the page cursor).
    pub fn find(&self, q: &str, after: Option<&str>, take: usize) -> Vec<(String, Listed)> {
        let map = self.lock();
        let start = match after {
            Some(a) => Bound::Excluded(a),
            None => Bound::Unbounded,
        };
        map.range::<str, _>((start, Bound::Unbounded))
            .filter(|(key, _)| key.contains(q))
            .take(take)
            .map(|(key, o)| {
                (
                    key.clone(),
                    Listed {
                        name: o.name.clone(),
                        house: o.house,
                    },
                )
            })
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn names(rows: &[(String, Listed)]) -> Vec<&str> {
        rows.iter().map(|(_, l)| l.name.as_str()).collect()
    }

    #[test]
    fn enters_leaves_and_finds_by_substring_in_name_order() {
        let p = Presence::default();
        let ada = p.enter("Ada", House::Skytalon);
        let _bea = p.enter("Beatrice", House::Burrowsby);
        let _emile = p.enter("ÉMILE", House::Serpentyne);
        assert_eq!(names(&p.find("", None, 10)), ["Ada", "Beatrice", "ÉMILE"]);
        assert_eq!(names(&p.find("a", None, 10)), ["Ada", "Beatrice"]);
        assert_eq!(names(&p.find("émi", None, 10)), ["ÉMILE"]);
        // Paging: after a key, at most `take`.
        let first = p.find("", None, 1);
        assert_eq!(names(&first), ["Ada"]);
        assert_eq!(names(&p.find("", Some(&first[0].0), 1)), ["Beatrice"]);
        assert_eq!(p.find("", None, 10)[1].1.house, House::Burrowsby);
        drop(ada);
        assert_eq!(names(&p.find("a", None, 10)), ["Beatrice"]);
        assert_eq!(p.len(), 2);
    }

    #[test]
    fn a_replacing_login_keeps_the_name_listed() {
        let p = Presence::default();
        let old = p.enter("Ada", House::Skytalon);
        let new = p.enter("ada", House::Skytalon);
        drop(old);
        assert_eq!(names(&p.find("", None, 10)), ["Ada"]);
        drop(new);
        assert!(p.is_empty());
    }
}
