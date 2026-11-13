//! Exploring the castle in the world (docs/architecture.md
//! "Secrets and the map"; `sim_core::secrets`, `sim_core::wishing_room`, `crate::map`).
//!
//! Each accepted castle walking move ([`WorldSim::explore_moved`]):
//!
//! - **Reveals** the surface under the character (`castle_all.ground_at`,
//!   the map's rule: the place stood on, whole). One bit per surface per
//!   character; a new one is marked dirty and goes to the saver with the
//!   next 30 s position save, or at logout ([`WorldSim::take_reveals`]).
//! - **Discovers** a secret: standing on a secret's surface the first time
//!   starts one job ([`crate::map::discover`]: the row and the reward in one
//!   transaction); `Discovered` and `Progress` follow its commit. One job per
//!   character and secret at a time; a failed job is retried after
//!   [`DISCOVERY_RETRY`] on the next move there.
//! - **Paces** for the Wishing Room: only on its corridor, one
//!   `sim_core::Pacing` per character there (dropped on stepping off). The
//!   third pass within the minute opens the door for that character for
//!   `door_open_s`: `WishingRoomDoor { open_until_ms }`.
//!
//! Riders, instances and staircase carries reveal nothing; the next walking
//! move in the castle does. The per-character state lives in
//! [`WorldSim::explore`] by character id, from the join (loaded with the
//! character, `crate::map::load`) to the logout; a replaced connection keeps
//! it.

use std::collections::HashMap;
use std::sync::Arc;
use std::time::{Duration, Instant};

use protocol::ServerMsg;
use sim_core::Pacing;

use super::ext::ExtDone;
use super::world::WorldSim;
use crate::map::{Discovery, Explored, Reveal};

/// A discovery whose job failed is tried again on a move after this long.
pub const DISCOVERY_RETRY: Duration = Duration::from_secs(10);

/// Friends hear a walker's new place at most this often (review fix).
pub const PLACE_NOTICE_EVERY: Duration = Duration::from_secs(2);

/// The castle's surfaces and secrets as the world looks them up.
#[derive(Debug)]
pub struct ExploreIndex {
    /// Every castle surface's id, by its index in `castle_all`.
    surface_ids: Vec<Arc<str>>,
    by_id: HashMap<Arc<str>, u32>,
    /// The secrets, when `data/world/secrets.json` fits the castle (a test
    /// layout without them still reveals).
    secrets: Option<SecretsIndex>,
}

#[derive(Debug)]
struct SecretsIndex {
    secrets: sim_core::Secrets,
    /// The secret (index into `secrets.secrets()`) of each surface.
    surface_secret: Vec<Option<u8>>,
    /// The Wishing Room's corridor surface.
    corridor: u32,
}

impl ExploreIndex {
    /// The index of the castle `castle_all` (both positions of every flight).
    pub fn new(castle_all: &sim_core::World) -> ExploreIndex {
        let surface_ids: Vec<Arc<str>> = castle_all
            .surfaces()
            .iter()
            .map(|s| Arc::from(s.id.as_str()))
            .collect();
        let by_id = surface_ids
            .iter()
            .enumerate()
            .map(|(i, id)| (id.clone(), i as u32))
            .collect();
        let secrets = sim_core::Secrets::new(
            crate::catalog::shipped().secrets.clone(),
            castle_all.layout(),
        )
        .ok()
        .and_then(|secrets| {
            let corridor = castle_all
                .surfaces()
                .iter()
                .position(|s| s.id == secrets.wishing_room().corridor)?
                as u32;
            let surface_secret = castle_all
                .surfaces()
                .iter()
                .map(|s| {
                    secrets.secret_of(&s.id).and_then(|sec| {
                        secrets
                            .secrets()
                            .iter()
                            .position(|x| x.id == sec.id)
                            .map(|k| k as u8)
                    })
                })
                .collect();
            Some(SecretsIndex {
                secrets,
                surface_secret,
                corridor,
            })
        });
        ExploreIndex {
            surface_ids,
            by_id,
            secrets,
        }
    }

    /// The validated secrets, if the castle has them.
    pub fn secrets(&self) -> Option<&sim_core::Secrets> {
        self.secrets.as_ref().map(|s| &s.secrets)
    }

    /// The Wishing Room's rules, if the castle has them.
    pub fn wishing_room(&self) -> Option<&sim_core::WishingRoomRules> {
        self.secrets().map(|s| s.wishing_room())
    }

    fn secret_index(&self, id: &str) -> Option<u8> {
        self.secrets()?
            .secrets()
            .iter()
            .position(|s| s.id == id)
            .map(|k| k as u8)
    }
}

/// One character's exploration state.
#[derive(Debug, Default)]
pub struct Explorer {
    /// One bit per castle surface (by index): revealed.
    revealed: Vec<u64>,
    /// Revealed since the last save.
    dirty: Vec<u32>,
    /// One bit per secret (by index): found.
    found: u32,
    /// Discovery jobs in flight.
    pending: u32,
    /// After a failed discovery job: not again before this.
    retry_at: Option<Instant>,
    /// Pacing on the Wishing Room's corridor (`None` elsewhere).
    pacing: Option<Pacing>,
    /// The door shows for this character until then (server Unix ms; 0: no door).
    pub door_until_ms: u64,
    /// Review fix: the castle surface whose place name friends were last
    /// told (`None` until the first move after the join), and when.
    place: Option<u32>,
    place_sent: Option<Instant>,
}

impl Explorer {
    /// True if `surface` was not revealed before (it is now).
    fn reveal(&mut self, surface: u32) -> bool {
        let (w, b) = ((surface / 64) as usize, surface % 64);
        if self.revealed.len() <= w {
            self.revealed.resize(w + 1, 0);
        }
        let new = self.revealed[w] & (1 << b) == 0;
        self.revealed[w] |= 1 << b;
        new
    }

    fn is_revealed(&self, surface: u32) -> bool {
        let (w, b) = ((surface / 64) as usize, surface % 64);
        self.revealed.get(w).is_some_and(|x| x & (1 << b) != 0)
    }

    /// Adds what was loaded from the database (already saved, so not dirty).
    fn load(&mut self, explored: &Explored, index: &ExploreIndex) {
        for id in &explored.reveals {
            if let Some(&s) = index.by_id.get(id.as_str()) {
                self.reveal(s);
            }
        }
        for id in &explored.secrets {
            if let Some(k) = index.secret_index(id) {
                self.found |= 1 << k;
            }
        }
    }

    /// The revealed surfaces' ids and the found secrets' ids.
    fn explored(&self, index: &ExploreIndex) -> Explored {
        let reveals = (0..index.surface_ids.len() as u32)
            .filter(|&s| self.is_revealed(s))
            .map(|s| index.surface_ids[s as usize].to_string())
            .collect();
        let secrets = index.secrets().map_or_else(Vec::new, |sx| {
            sx.secrets()
                .iter()
                .enumerate()
                .filter(|(k, _)| self.found & (1 << k) != 0)
                .map(|(_, s)| s.id.clone())
                .collect()
        });
        Explored { reveals, secrets }
    }
}

impl WorldSim {
    /// A character joins (or a replaced connection's character comes back):
    /// its state, with what was loaded from the database.
    pub(super) fn explore_joined(&mut self, character_id: i64, explored: &Explored) {
        let mut ex = self.explore.remove(&character_id).unwrap_or_default();
        ex.load(explored, &self.explore_index);
        self.explore.insert(character_id, ex);
    }

    /// A character left the world: its unsaved reveals (for the leaving
    /// save), and its state goes unless the same character is signing in
    /// again (`keep`).
    pub(super) fn explore_left(&mut self, character_id: i64, keep: bool) -> Vec<Reveal> {
        let reveals = self.take_reveals(character_id);
        if !keep {
            self.explore.remove(&character_id);
        }
        reveals
    }

    /// A character's reveals since the last save, now handed over.
    pub(super) fn take_reveals(&mut self, character_id: i64) -> Vec<Reveal> {
        let Some(ex) = self.explore.get_mut(&character_id) else {
            return Vec::new();
        };
        ex.dirty
            .drain(..)
            .map(|s| Reveal {
                character_id,
                surface: self.explore_index.surface_ids[s as usize].clone(),
            })
            .collect()
    }

    /// Everyone's reveals since the last save (the 30 s save).
    pub(super) fn take_all_reveals(&mut self) -> Vec<Reveal> {
        let ids = &self.explore_index.surface_ids;
        let mut out = Vec::new();
        for (&character_id, ex) in &mut self.explore {
            out.extend(ex.dirty.drain(..).map(|s| Reveal {
                character_id,
                surface: ids[s as usize].clone(),
            }));
        }
        out
    }

    /// What an online character has explored (`GET /api/map`).
    pub fn explored_of(&self, character_id: i64) -> Option<Explored> {
        self.explore
            .get(&character_id)
            .map(|ex| ex.explored(&self.explore_index))
    }

    /// After character `i`'s accepted castle walking move at `at`: reveal,
    /// discover and pace (see the module docs).
    pub(super) fn explore_moved(&mut self, i: usize, at: Instant) {
        let pos = self.ents.pos[i];
        let Some(g) = self.castle_all.ground_at(pos[0], pos[2], pos[1]) else {
            return;
        };
        let surface = g.surface.0;
        let cid = self.ents.info[i].character_id;
        let index = self.explore_index.clone();
        let Some(ex) = self.explore.get_mut(&cid) else {
            return;
        };
        if ex.reveal(surface) {
            ex.dirty.push(surface);
        }
        // Review fix: friends hear a new place as we walk into it (it came
        // only with a zone change, so the friend list and the map's pins went
        // stale), at most once per `PLACE_NOTICE_EVERY`; a change held back
        // goes with a later move.
        let names = self.castle_all.surfaces();
        let notify = match ex.place {
            None => {
                // Just joined: friends were told the place with the join.
                ex.place = Some(surface);
                false
            }
            Some(p) => {
                names[p as usize].name != names[surface as usize].name
                    && ex
                        .place_sent
                        .is_none_or(|t| at.saturating_duration_since(t) >= PLACE_NOTICE_EVERY)
            }
        };
        if notify {
            ex.place = Some(surface);
            ex.place_sent = Some(at);
            self.metrics.friend_place_notices.inc();
            self.notify_friends_moved(i);
            self.party_moved(i);
        }
        let Some(ex) = self.explore.get_mut(&cid) else {
            return;
        };
        let Some(sx) = index.secrets.as_ref() else {
            return;
        };
        // The first step onto a secret: one job finds it.
        if let Some(k) = sx.surface_secret[surface as usize] {
            let bit = 1u32 << k;
            if ex.found & bit == 0 && ex.pending & bit == 0 && ex.retry_at.is_none_or(|t| at >= t) {
                ex.pending |= bit;
                ex.retry_at = None;
                let secret = sx.secrets.secrets()[usize::from(k)].clone();
                self.ext_jobs.push(Box::new(move |pool| {
                    Box::pin(async move {
                        let result = crate::map::discover(&pool, cid, &secret)
                            .await
                            .map_err(|e| {
                                crate::monitoring::capture_for_character(&e, cid);
                                e.to_string()
                            });
                        ExtDone::Run(Box::new(move |ctx| {
                            let now = ctx.now();
                            ctx.sim.discovery_done(cid, k, result, now);
                        }))
                    })
                }));
            }
        }
        // Pacing, on the corridor only.
        let ex = self.explore.get_mut(&cid).expect("looked up above");
        if surface != sx.corridor {
            ex.pacing = None;
            return;
        }
        let rules = sx.secrets.wishing_room();
        let mark = rules.mark_at(self.castle.layout(), pos);
        let now_ms = u64::try_from(self.clock.unix_ms_at(at)).unwrap_or(0);
        if !ex.pacing.get_or_insert_default().step(rules, mark, now_ms) {
            return;
        }
        let open_until_ms = now_ms + rules.door_open_ms();
        ex.door_until_ms = open_until_ms;
        self.metrics.wishing_room_doors.inc();
        self.send_msg(i, &ServerMsg::WishingRoomDoor { open_until_ms });
    }

    /// A discovery job finished (on the world thread).
    pub(super) fn discovery_done(
        &mut self,
        character_id: i64,
        k: u8,
        result: Result<Option<Discovery>, String>,
        now: Instant,
    ) {
        let Some(ex) = self.explore.get_mut(&character_id) else {
            return;
        };
        let bit = 1u32 << k;
        ex.pending &= !bit;
        let found = match result {
            Ok(Some(d)) => d,
            // Found before (another connection, or a retried job).
            Ok(None) => {
                ex.found |= bit;
                return;
            }
            Err(error) => {
                tracing::error!(character = character_id, %error, "a discovery failed");
                ex.retry_at = Some(now + DISCOVERY_RETRY);
                return;
            }
        };
        ex.found |= bit;
        self.metrics.discoveries.inc();
        let Some(i) = self.index_of_character(character_id) else {
            return;
        };
        let msg = ServerMsg::Discovered {
            secret: found.secret,
            xp: found.xp.granted,
            house_point: found.house_point,
            recipe: found.recipe,
        };
        self.send_msg(i, &msg);
        self.set_progress(i, found.xp.xp, Some(found.xp.gain()));
    }

    /// Whether character `cid`'s Wishing Room door is open
    /// at server time `now_ms`.
    pub(super) fn wishing_room_door_open(&self, cid: i64, now_ms: u64) -> bool {
        self.explore
            .get(&cid)
            .is_some_and(|e| e.door_until_ms != 0 && now_ms <= e.door_until_ms)
    }

    /// Takes character `cid`'s door away (entered) and starts its pacing over.
    pub(super) fn close_wishing_room_door(&mut self, cid: i64) {
        if let Some(e) = self.explore.get_mut(&cid) {
            e.door_until_ms = 0;
            e.pacing = None;
        }
    }

    /// The passes counted so far on the corridor (tests).
    pub fn pacing_passes(&self, character_id: i64) -> Option<usize> {
        self.explore
            .get(&character_id)?
            .pacing
            .as_ref()
            .map(Pacing::passes)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reveals_are_bits_and_new_ones_are_dirty_once() {
        let mut ex = Explorer::default();
        assert!(ex.reveal(3));
        assert!(!ex.reveal(3));
        assert!(ex.reveal(200));
        assert!(ex.is_revealed(200) && ex.is_revealed(3) && !ex.is_revealed(4));
    }

    #[test]
    fn the_shipped_castle_indexes_its_secrets() {
        let world =
            sim_core::World::from_json(include_str!("../../../../data/world/layout.json")).unwrap();
        let index = ExploreIndex::new(&world);
        let sx = index.secrets.as_ref().expect("the castle has its secrets");
        assert_eq!(sx.secrets.secrets().len(), 5);
        let secret_surfaces = sx.surface_secret.iter().filter(|s| s.is_some()).count();
        let listed: usize = sx.secrets.secrets().iter().map(|s| s.surfaces.len()).sum();
        assert_eq!(secret_surfaces, listed);
        assert_eq!(
            &*index.surface_ids[sx.corridor as usize],
            sx.secrets.wishing_room().corridor
        );
        let mut ex = Explorer::default();
        ex.load(
            &Explored {
                reveals: vec!["l7_corridor_east".into(), "no_such_surface".into()],
                secrets: vec!["cellarers_way".into()],
            },
            &index,
        );
        let back = ex.explored(&index);
        assert_eq!(back.reveals, vec!["l7_corridor_east".to_owned()]);
        assert_eq!(back.secrets, vec!["cellarers_way".to_owned()]);
        assert!(ex.dirty.is_empty(), "loaded reveals are saved already");
    }
}
