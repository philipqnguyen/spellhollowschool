//! Appearance caching for entering entities (protocol v3; v4 adds effects).
//!
//! In a crowd the visible set turns over constantly: people walk through
//! each other's nearest fifty. Each entry used to carry the character's name
//! and full appearance (about 50 bytes), although the client had usually seen
//! that same character a moment before. Now `Snapshot.entered` holds
//! [`EntityEntry`]s: `Full` the first time a connection is sent an entity at
//! a given [`LookVersion`], and a 5-6 byte `Cached` after that.
//!
//! A look is the appearance plus the world's half of it: the visible potion
//! effects (protocol v4, [`crate::effects`]) and, since v5, Lucerna and the
//! level. So it is versioned by both `appearance_version` (the character's
//! stored look, bumped by wardrobe changes) and `effects_version` (bumped by
//! the world whenever the visible effects or the level change; it starts at
//! 0 when the character enters the world).
//!
//! Both ends keep the same record of what was sent in full on this
//! connection, with the same bounded-size rule, so they always agree:
//!
//! - the server's [`SentLooks`]: `(id, LookVersion)` of the last `Full`
//!   entry sent for each id;
//! - the client's [`LookCache`]: the `EntityInfo` of that entry.
//!
//! Rules (both ends):
//! 1. Only `Full` entries change the record (`AppearanceChanged` and
//!    `EffectsChanged` do not; a later entry at the new version is simply
//!    `Full` again).
//! 2. A `Full` entry for an id the record does not hold, when it already
//!    holds [`LOOK_CACHE_MAX`] ids, first empties the record.
//! 3. `Cached { id, appearance_version, effects_version }` is sent only while
//!    the record holds `id` at exactly those versions.
//! 4. The record belongs to the connection: it starts empty, survives zone
//!    changes and is emptied by `Welcome` (a new connection starts afresh, so
//!    a reconnect, even to a restarted server whose ids mean other people
//!    now, can never use a stale entry).
//!
//! A `Cached` entry the client cannot resolve is a desync ([`Desync::UnknownLook`]):
//! the client drops the connection, as for any other snapshot that does not fit.

use std::collections::HashMap;

use serde::{Deserialize, Serialize};

use crate::{Desync, EntityId, EntityInfo};

/// Most entities a connection's record holds (rule 2). A packed hall of
/// 1,000 players fits; past that the record restarts.
pub const LOOK_CACHE_MAX: usize = 1024;

/// Which look of an entity an entry carries.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct LookVersion {
    pub appearance: u32,
    pub effects: u32,
}

impl EntityInfo {
    /// The version pair this entry carries.
    pub fn look_version(&self) -> LookVersion {
        LookVersion {
            appearance: self.appearance_version,
            effects: self.effects_version,
        }
    }
}

/// An entity entering the visible set, as `Snapshot.entered` sends it.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum EntityEntry {
    /// Everything about the entity (name, house, appearance, effects).
    Full(EntityInfo),
    /// The same entity at the same versions as the last `Full` entry this
    /// connection was sent for `id`.
    Cached {
        id: EntityId,
        appearance_version: u32,
        effects_version: u32,
    },
}

impl EntityEntry {
    pub fn id(&self) -> EntityId {
        match self {
            EntityEntry::Full(info) => info.id,
            EntityEntry::Cached { id, .. } => *id,
        }
    }

    /// A `Cached` entry for `id` at `version`.
    pub fn cached(id: EntityId, version: LookVersion) -> Self {
        EntityEntry::Cached {
            id,
            appearance_version: version.appearance,
            effects_version: version.effects,
        }
    }
}

/// The server's half: which `(id, LookVersion)` this connection was last
/// sent in full. Sorted by id, 12 bytes an entry.
#[derive(Debug, Default, Clone)]
pub struct SentLooks {
    sent: Vec<(EntityId, LookVersion)>,
}

impl SentLooks {
    pub fn new() -> Self {
        Self::default()
    }

    /// Whether an entry for `id` at `version` may be `Cached` (rule 3).
    pub fn has(&self, id: EntityId, version: LookVersion) -> bool {
        self.sent
            .binary_search_by_key(&id, |&(i, _)| i)
            .is_ok_and(|k| self.sent[k].1 == version)
    }

    /// Records a `Full` entry (rules 1 and 2).
    pub fn record(&mut self, id: EntityId, version: LookVersion) {
        match self.sent.binary_search_by_key(&id, |&(i, _)| i) {
            Ok(k) => self.sent[k].1 = version,
            Err(k) => {
                if self.sent.len() >= LOOK_CACHE_MAX {
                    self.sent.clear();
                    self.sent.push((id, version));
                } else {
                    self.sent.insert(k, (id, version));
                }
            }
        }
    }

    pub fn len(&self) -> usize {
        self.sent.len()
    }

    pub fn is_empty(&self) -> bool {
        self.sent.is_empty()
    }

    pub fn clear(&mut self) {
        self.sent.clear();
    }
}

/// The client's half: the `EntityInfo` of every entity sent in full on this
/// connection (as many ids as the server's [`SentLooks`]).
#[derive(Debug, Default, Clone)]
pub struct LookCache {
    infos: HashMap<EntityId, EntityInfo>,
}

impl LookCache {
    pub fn new() -> Self {
        Self::default()
    }

    /// Resolves a snapshot's entries into complete `EntityInfo`s (appended to
    /// `out` in order), recording the `Full` ones.
    pub fn resolve(
        &mut self,
        entries: &[EntityEntry],
        out: &mut Vec<EntityInfo>,
    ) -> Result<(), Desync> {
        for e in entries {
            match e {
                EntityEntry::Full(info) => {
                    if !self.infos.contains_key(&info.id) && self.infos.len() >= LOOK_CACHE_MAX {
                        self.infos.clear();
                    }
                    self.infos.insert(info.id, info.clone());
                    out.push(info.clone());
                }
                EntityEntry::Cached {
                    id,
                    appearance_version,
                    effects_version,
                } => match self.infos.get(id) {
                    Some(info)
                        if info.appearance_version == *appearance_version
                            && info.effects_version == *effects_version =>
                    {
                        out.push(info.clone())
                    }
                    _ => return Err(Desync::UnknownLook(*id)),
                },
            }
        }
        Ok(())
    }

    pub fn len(&self) -> usize {
        self.infos.len()
    }

    pub fn is_empty(&self) -> bool {
        self.infos.is_empty()
    }

    /// Forgets everything (`Welcome`).
    pub fn clear(&mut self) {
        self.infos.clear();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{Appearance, CharacterInfo, House, VisibleEffect};

    fn v(appearance: u32, effects: u32) -> LookVersion {
        LookVersion {
            appearance,
            effects,
        }
    }

    fn info(id: EntityId, version: LookVersion) -> EntityInfo {
        EntityInfo {
            id,
            character: CharacterInfo {
                name: format!("p{id}"),
                house: House::Burrowsby,
            },
            appearance_version: version.appearance,
            appearance: Appearance::default(),
            effects_version: version.effects,
            effects: if version.effects % 2 == 1 {
                vec![VisibleEffect::Steam]
            } else {
                Vec::new()
            },
            level: 1,
            npc: None,
        }
    }

    /// What a server following the rules sends for `(id, version)`.
    fn entry(sent: &mut SentLooks, id: EntityId, version: LookVersion) -> EntityEntry {
        if sent.has(id, version) {
            EntityEntry::cached(id, version)
        } else {
            sent.record(id, version);
            EntityEntry::Full(info(id, version))
        }
    }

    #[test]
    fn cached_entries_resolve_to_the_last_full_one() {
        let (mut sent, mut cache) = (SentLooks::new(), LookCache::new());
        let mut out = Vec::new();
        let first = [
            entry(&mut sent, 70_001, v(3, 0)),
            entry(&mut sent, 70_002, v(1, 0)),
            entry(&mut sent, 70_003, v(1, 0)),
        ];
        assert!(matches!(first[0], EntityEntry::Full(_)));
        cache.resolve(&first, &mut out).unwrap();
        // The same three again: one with a new look, one with new effects.
        out.clear();
        let again = [
            entry(&mut sent, 70_001, v(3, 0)),
            entry(&mut sent, 70_002, v(2, 0)),
            entry(&mut sent, 70_003, v(1, 1)),
        ];
        assert_eq!(again[0], EntityEntry::cached(70_001, v(3, 0)));
        assert!(matches!(again[1], EntityEntry::Full(_)));
        assert!(matches!(again[2], EntityEntry::Full(_)));
        cache.resolve(&again, &mut out).unwrap();
        assert_eq!(
            out,
            vec![
                info(70_001, v(3, 0)),
                info(70_002, v(2, 0)),
                info(70_003, v(1, 1))
            ]
        );
        assert_eq!(out[2].effects, vec![VisibleEffect::Steam]);
        // A cached entry is a handful of bytes.
        assert!(postcard::to_allocvec(&again[0]).unwrap().len() <= 6);
    }

    #[test]
    fn an_entry_the_client_never_had_is_a_desync() {
        let mut cache = LookCache::new();
        let mut out = Vec::new();
        let stale = EntityEntry::cached(9, v(1, 0));
        assert_eq!(
            cache.resolve(std::slice::from_ref(&stale), &mut out),
            Err(Desync::UnknownLook(9))
        );
        cache
            .resolve(&[EntityEntry::Full(info(9, v(2, 0)))], &mut out)
            .unwrap();
        assert_eq!(
            cache.resolve(&[stale], &mut out),
            Err(Desync::UnknownLook(9)),
            "wrong appearance version"
        );
        assert_eq!(
            cache.resolve(&[EntityEntry::cached(9, v(2, 1))], &mut out),
            Err(Desync::UnknownLook(9)),
            "wrong effects version"
        );
    }

    #[test]
    fn both_ends_restart_together_when_full() {
        let (mut sent, mut cache) = (SentLooks::new(), LookCache::new());
        let mut out = Vec::new();
        let ids: Vec<EntityId> = (0..LOOK_CACHE_MAX as u32 + 300)
            .map(|n| n * 7 + 1)
            .collect();
        // Walk through more people than the record holds, coming back to
        // earlier ones now and then; every entry must resolve.
        for (k, &id) in ids.iter().enumerate() {
            let mut batch = vec![entry(&mut sent, id, v(1, 0))];
            if k % 5 == 0 {
                batch.push(entry(&mut sent, ids[k / 2], v(1, 0)));
            }
            out.clear();
            cache.resolve(&batch, &mut out).expect("in step");
            assert_eq!(sent.len(), cache.len());
            assert!(sent.len() <= LOOK_CACHE_MAX);
        }
        sent.clear();
        cache.clear();
        assert!(sent.is_empty() && cache.is_empty());
    }
}
