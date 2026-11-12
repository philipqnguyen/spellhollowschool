//! The secrets, the Wishing Room and the map under load
//! (docs/architecture.md "Secrets and the map").
//!
//! - **Every bot fetches `GET /api/map` once after its session** (as the
//!   browser does at sign-in): the summary reports the requests, errors and
//!   latency ([`SecretStats`]).
//! - **Explorers** (`scenarios.json` `explore`): walkers that tour the five
//!   hidden passages, toured like the grounds walkers ([`crate::grounds`]).
//!   Their stops are each passage's two public doorsteps in turn (computed
//!   from `data/world/secrets.json` and the layout: a point just outside
//!   each door surface on the public side), so the way from one doorstep
//!   to the other is the passage (each is a shortcut), and the way on to
//!   the next passage crosses the castle. The bots' navigation may use the
//!   passages (`sim_core::nav`: the trail never does). Each explorer finds
//!   each passage once per character (`Discovered`).
//! - **Ritual bots** (`scenarios.json` `ritual`): teleported to the
//!   seventh-floor east corridor, they pace between its two marks three
//!   times, walk to the blank wall's door when it opens for them
//!   (`WishingRoomDoor`), enter one of the two rooms (`EnterWishingRoom`,
//!   alternating), stay a while and come out, and start over after a rest.
//!   The corridor is one flat rectangle, so their straight walks are always
//!   valid moves.

use std::collections::BTreeMap;

use protocol::{Anim, ClientMsg, ServerMsg, WishingRoomKind, Zone};
use rand::Rng;
use serde::{Deserialize, Serialize};
use sim_core::key_places::PlaceTarget;
use sim_core::{Layout, SecretsFile, World};
use tokio::time::{Duration, Instant};

use crate::grounds::GroundsSpec;
use crate::http::Target;

/// The role the explorers report under.
pub const EXPLORE_ROLE: &str = "explorer";
/// The role the ritual bots report under.
pub const RITUAL_ROLE: &str = "ritual";

const SECRETS: &str = include_str!("../../../data/world/secrets.json");

/// The corridor's two marks and the door (`data/world/layout.json`: the
/// `pace_mark` areas `wishing_room_pace_west`, `wishing_room_pace_east` and the
/// `wishing_room_door` at [-3, 63, 8.5], reach 2 m).
const WEST: [f32; 3] = [-11.0, 63.0, 4.0];
const EAST: [f32; 3] = [5.0, 63.0, 4.0];
const DOOR: [f32; 3] = [-3.0, 63.0, 7.3];
/// Walking pace on the corridor (m/s), under the walk speed.
const PACE_SPEED: f32 = 3.6;
/// How long a ritual bot waits at the far mark for the door before it
/// starts over.
const DOOR_WAIT: Duration = Duration::from_secs(3);
/// How long it waits for the room after asking to enter.
const ENTER_WAIT: Duration = Duration::from_secs(5);

/// The `explore` entry of a scenario in `scenarios.json`.
#[derive(Debug, Clone, PartialEq, Deserialize, Serialize)]
pub struct ExploreSpec {
    /// How many of the bots tour the passages (all of them if fewer).
    pub walkers: usize,
    /// A doorstep is any point within this many metres of it.
    pub jitter: f32,
    /// Share of trips to a random point of the castle instead.
    pub stray: f64,
}

/// The `ritual` entry of a scenario in `scenarios.json`.
#[derive(Debug, Clone, PartialEq, Deserialize, Serialize)]
pub struct RitualSpec {
    /// How many of the bots pace for the Wishing Room.
    pub bots: usize,
    /// Seconds spent inside a room, and resting before pacing again
    /// (each drawn from [min, max]).
    pub stay_secs: [f64; 2],
    pub rest_secs: [f64; 2],
}

impl ExploreSpec {
    /// The tour: each passage's two public doorsteps in turn, over the
    /// whole castle.
    pub fn tour(&self, world: &World, layout: &Layout) -> Result<GroundsSpec, String> {
        Ok(GroundsSpec {
            walkers: self.walkers,
            cells: vec!["*".into()],
            stops: doorsteps(world, layout)?
                .into_iter()
                .map(PlaceTarget::Pos)
                .collect(),
            jitter: self.jitter,
            stray: self.stray,
        })
    }
}

/// Every passage's two doorsteps, in `secrets.json` order: a point 1.2 m
/// outside each door surface, on the side whose ground is public.
pub fn doorsteps(world: &World, layout: &Layout) -> Result<Vec<[f32; 3]>, String> {
    let file: SecretsFile =
        serde_json::from_str(SECRETS).map_err(|e| format!("secrets.json: {e}"))?;
    let mut out = Vec::new();
    for s in &file.secrets {
        for d in &s.doors {
            let door = layout
                .surfaces
                .iter()
                .find(|x| x.id == *d)
                .ok_or_else(|| format!("secrets.json: no door surface {d}"))?;
            let (cx, cz) = (
                (door.min[0] + door.max[0]) / 2.0,
                (door.min[1] + door.max[1]) / 2.0,
            );
            let out_by = 1.2;
            let sides = [
                (door.min[0] - out_by, cz),
                (door.max[0] + out_by, cz),
                (cx, door.min[1] - out_by),
                (cx, door.max[1] + out_by),
            ];
            let step = sides.iter().find_map(|&(x, z)| {
                let g = world.ground_at(x, z, door.y)?;
                (!world.surface(g.surface).secret).then_some([x, g.y, z])
            });
            out.push(step.ok_or_else(|| format!("door {d}: no public side"))?);
        }
    }
    Ok(out)
}

/// What the bots did about the map, the secrets and the Wishing
/// Room (summed over bots).
#[derive(Debug, Clone, Default, Serialize)]
pub struct SecretStats {
    /// `GET /api/map` at sign-in: answered, failed, latency (ms) of the answered.
    pub map_fetches: u64,
    pub map_errors: u64,
    pub map_error_reasons: BTreeMap<String, u64>,
    #[serde(skip)]
    pub map_ms: Vec<f64>,
    /// Revealed surfaces the map listed (summed).
    pub map_surfaces: u64,
    /// Secrets found (`Discovered`).
    pub discoveries: u64,
    /// Ritual bots: passes walked, doors that opened, entries by room
    /// (`WishingRoomKind` order), refusals (`DormError`), rituals that
    /// did not open the door or reach the room.
    pub passes: u64,
    pub doors: u64,
    pub entries: [u64; 2],
    pub refusals: u64,
    pub failed: u64,
}

impl SecretStats {
    pub fn add(&mut self, o: &SecretStats) {
        self.map_fetches += o.map_fetches;
        self.map_errors += o.map_errors;
        for (k, v) in &o.map_error_reasons {
            *self.map_error_reasons.entry(k.clone()).or_default() += v;
        }
        self.map_ms.extend_from_slice(&o.map_ms);
        self.map_surfaces += o.map_surfaces;
        self.discoveries += o.discoveries;
        self.passes += o.passes;
        self.doors += o.doors;
        self.entries[0] += o.entries[0];
        self.entries[1] += o.entries[1];
        self.refusals += o.refusals;
        self.failed += o.failed;
    }
}

/// `GET /api/map` once, into `stats`.
pub async fn fetch_map(target: &Target, token: &str, stats: &mut SecretStats) {
    let sent = Instant::now();
    let res = target.get_as(token, "/api/map").await;
    let failed = match res {
        Ok(r) if r.is_success() => {
            stats.map_fetches += 1;
            stats.map_ms.push(sent.elapsed().as_secs_f64() * 1000.0);
            let surfaces = serde_json::from_slice::<serde_json::Value>(&r.body)
                .ok()
                .and_then(|v| v["surfaces"].as_array().map(Vec::len))
                .unwrap_or(0);
            stats.map_surfaces += surfaces as u64;
            None
        }
        Ok(r) => Some(format!("status {}", r.status)),
        Err(e) => Some(e),
    };
    if let Some(reason) = failed {
        stats.map_errors += 1;
        *stats.map_error_reasons.entry(reason).or_default() += 1;
    }
}

/// The ritual bots of one run.
pub struct Ritual {
    pub spec: RitualSpec,
    members: Vec<bool>,
}

impl Ritual {
    /// `spec.bots` of the bots marked in `walking`, evenly spread.
    pub fn new(spec: RitualSpec, walking: &[bool]) -> Ritual {
        Ritual {
            members: crate::grounds::members(walking, spec.bots),
            spec,
        }
    }

    pub fn is_member(&self, index: usize) -> bool {
        self.members.get(index).copied().unwrap_or(false)
    }

    pub fn bots(&self) -> usize {
        self.members.iter().filter(|&&m| m).count()
    }

    /// Bot `index`'s act, starting now.
    pub fn act(&self, index: usize) -> RitualAct {
        RitualAct {
            phase: Phase::Start,
            pos: WEST,
            yaw: 0.0,
            anim: Anim::Idle,
            target: EAST,
            passes: 0,
            room: if index.is_multiple_of(2) {
                WishingRoomKind::LostProperty
            } else {
                WishingRoomKind::Gathering
            },
            since: Instant::now(),
            until: Instant::now(),
            door_open: false,
            arrived: None,
            from_mark: false,
        }
    }

    /// Where a ritual bot is teleported at sign-in.
    pub fn start() -> [f32; 3] {
        WEST
    }
}

#[derive(Debug, Clone, Copy, PartialEq)]
enum Phase {
    /// Waiting for the teleport to the west mark.
    Start,
    /// Walking between the marks.
    Pacing,
    /// Walking to the door.
    ToDoor,
    /// Asked to enter; waiting for the room.
    Entering,
    /// In the room until `until`.
    Inside,
    /// Asked to leave; waiting for the corridor.
    Leaving,
    /// Back on the corridor, resting until `until`.
    Resting,
}

/// One ritual bot.
#[derive(Debug, Clone)]
pub struct RitualAct {
    phase: Phase,
    pub pos: [f32; 3],
    yaw: f32,
    anim: Anim,
    target: [f32; 3],
    passes: u8,
    room: WishingRoomKind,
    since: Instant,
    until: Instant,
    door_open: bool,
    /// When the bot reached the mark it stands at (`None` while walking).
    arrived: Option<Instant>,
    /// The walk under way started at the other mark (so it is a pass).
    from_mark: bool,
}

impl RitualAct {
    /// Placed (teleported to the west mark, or wherever it stands).
    pub fn placed(&mut self, pos: [f32; 3], now: Instant) {
        self.pos = pos;
        self.start_pacing(now);
    }

    fn start_pacing(&mut self, now: Instant) {
        self.phase = Phase::Pacing;
        self.passes = 0;
        self.door_open = false;
        self.arrived = None;
        // From the west mark to the east one, else to the west mark first
        // (that walk is no pass), then back and forth.
        let at_west = (self.pos[0] - WEST[0]).abs() < 0.5 && (self.pos[2] - WEST[2]).abs() < 4.0;
        self.target = if at_west { EAST } else { WEST };
        self.from_mark = at_west;
        self.since = now;
    }

    /// One frame: what to send, and where the bot stands.
    pub fn tick(
        &mut self,
        now: Instant,
        dt: f32,
        zone: &Zone,
        rng: &mut impl Rng,
        stats: &mut SecretStats,
    ) -> (Option<ClientMsg>, [f32; 3], f32, Anim) {
        let mut msg = None;
        match self.phase {
            Phase::Start => {}
            Phase::Pacing => {
                if self.door_open {
                    self.phase = Phase::ToDoor;
                    self.target = DOOR;
                } else if self.walk(dt) {
                    // At a mark: a pass ended. After the third, wait a moment
                    // for the door; without one, pace on (the server counts
                    // the last three passes within its minute).
                    let at = match self.arrived {
                        Some(at) => at,
                        None => {
                            self.arrived = Some(now);
                            if self.from_mark {
                                stats.passes += 1;
                                self.passes = self.passes.saturating_add(1);
                            }
                            now
                        }
                    };
                    if self.passes < 3 || now.duration_since(at) > DOOR_WAIT {
                        if self.passes >= 6 {
                            stats.failed += 1;
                            self.passes = 0;
                        }
                        self.target = if self.target == EAST { WEST } else { EAST };
                        self.arrived = None;
                        self.from_mark = true;
                    }
                }
            }
            Phase::ToDoor => {
                if self.walk(dt) {
                    self.phase = Phase::Entering;
                    self.since = now;
                    msg = Some(ClientMsg::EnterWishingRoom { room: self.room });
                }
            }
            Phase::Entering => {
                if now.duration_since(self.since) > ENTER_WAIT {
                    stats.failed += 1;
                    self.start_pacing(now);
                    self.target = WEST;
                }
            }
            Phase::Inside => {
                if now >= self.until {
                    self.phase = Phase::Leaving;
                    self.since = now;
                    msg = Some(ClientMsg::LeaveInstance);
                }
            }
            Phase::Leaving => {
                if now.duration_since(self.since) > ENTER_WAIT {
                    self.since = now;
                    msg = Some(ClientMsg::LeaveInstance);
                }
            }
            Phase::Resting => {
                if now >= self.until {
                    self.start_pacing(now);
                    self.target = WEST;
                }
            }
        }
        let _ = (zone, rng);
        (msg, self.pos, self.yaw, self.anim)
    }

    /// A step toward `target` at the pacing speed; true on arrival.
    fn walk(&mut self, dt: f32) -> bool {
        let (dx, dz) = (self.target[0] - self.pos[0], self.target[2] - self.pos[2]);
        let d = (dx * dx + dz * dz).sqrt();
        if d < 0.01 {
            self.anim = Anim::Idle;
            return true;
        }
        let len = (PACE_SPEED * dt.clamp(0.0, 0.25)).min(d);
        self.pos = [
            self.pos[0] + dx / d * len,
            self.target[1],
            self.pos[2] + dz / d * len,
        ];
        self.yaw = dx.atan2(dz);
        self.anim = Anim::Walk;
        false
    }

    /// A message from the server.
    pub fn on_msg(
        &mut self,
        msg: &ServerMsg,
        now: Instant,
        spec: &RitualSpec,
        rng: &mut impl Rng,
        stats: &mut SecretStats,
    ) {
        match msg {
            ServerMsg::WishingRoomDoor { open_until_ms } if *open_until_ms > 0 => {
                stats.doors += 1;
                self.door_open = true;
            }
            ServerMsg::ZoneChanged { zone, spawn, .. } => {
                self.pos = spawn.to_m();
                self.anim = Anim::Idle;
                match zone {
                    Zone::WishingRoom { room, .. } => {
                        let k = match room {
                            WishingRoomKind::LostProperty => 0,
                            WishingRoomKind::Gathering => 1,
                        };
                        stats.entries[k] += 1;
                        self.phase = Phase::Inside;
                        self.until = now + secs(spec.stay_secs, rng);
                        // The other room next time.
                        self.room = match self.room {
                            WishingRoomKind::LostProperty => WishingRoomKind::Gathering,
                            WishingRoomKind::Gathering => WishingRoomKind::LostProperty,
                        };
                    }
                    Zone::Castle if self.phase != Phase::Start => {
                        self.phase = Phase::Resting;
                        self.until = now + secs(spec.rest_secs, rng);
                    }
                    _ => {}
                }
            }
            ServerMsg::DormError { .. }
                if matches!(self.phase, Phase::Entering | Phase::ToDoor) =>
            {
                stats.refusals += 1;
                stats.failed += 1;
                self.start_pacing(now);
            }
            ServerMsg::Correction { pos, .. } => {
                self.pos = pos.to_m();
            }
            _ => {}
        }
    }

    /// Stays in a room of the Wishing Room (the bot must not leave it
    /// on arrival).
    pub fn stays_in(zone: &Zone) -> bool {
        matches!(zone, Zone::WishingRoom { .. })
    }
}

fn secs(range: [f64; 2], rng: &mut impl Rng) -> Duration {
    let (lo, hi) = (range[0].min(range[1]), range[0].max(range[1]));
    Duration::from_secs_f64(if hi > lo { rng.gen_range(lo..hi) } else { lo })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::nav::tests::castle;

    /// Each passage has a public doorstep outside each of its doors, and
    /// the corridor's walking points lie on it.
    #[test]
    fn the_passages_have_doorsteps_and_the_corridor_its_marks() {
        let world = castle();
        let steps = doorsteps(&world, world.layout()).unwrap();
        assert_eq!(steps.len(), 10);
        for p in &steps {
            let g = world.ground_at(p[0], p[2], p[1]).unwrap();
            assert!(!world.surface(g.surface).secret, "{p:?}");
        }
        for p in [WEST, EAST, DOOR] {
            let g = world.ground_at(p[0], p[2], p[1]).unwrap();
            assert_eq!(world.surface(g.surface).id, "l7_corridor_east");
        }
        let door = world.layout().interactable("wishing_room_door").unwrap();
        assert!(door.in_reach(DOOR));
    }

    /// The explorers' tour routes each passage's doorsteps through the
    /// passage (every house; its navigation may use secrets).
    #[test]
    fn explorers_walk_through_the_passages() {
        let world = castle();
        let layout = sim_core::Layout::from_json(crate::nav::tests::CASTLE).unwrap();
        let spec = ExploreSpec {
            walkers: 1,
            jitter: 0.5,
            stray: 0.1,
        };
        let scenario = crate::nav::Scenario::named("secrets").unwrap();
        let tour = spec.tour(&world, &layout).unwrap();
        let g = crate::grounds::Grounds::with_spec(
            &world,
            &layout,
            &scenario,
            tour,
            EXPLORE_ROLE,
            &[true],
        )
        .unwrap();
        assert!(g.is_member(0));
        let file: SecretsFile = serde_json::from_str(SECRETS).unwrap();
        for house in protocol::House::ALL {
            let nav = g.nav_in(house, 0);
            let w = nav.world();
            for (k, s) in file.secrets.iter().enumerate() {
                let (a, b) = (g.stops[2 * k], g.stops[2 * k + 1]);
                let sa = w.ground_at(a[0], a[2], a[1]).unwrap().surface.0;
                let sb = w.ground_at(b[0], b[2], b[1]).unwrap().surface.0;
                let route = nav
                    .route([a[0], a[2]], sa, [b[0], b[2]], sb)
                    .unwrap_or_else(|| panic!("{house:?}: no way through {}", s.id));
                let inside = route.iter().any(|p| {
                    s.surfaces.iter().any(|id| {
                        let x = layout.surfaces.iter().find(|x| x.id == *id).unwrap();
                        x.contains(p[0], p[1])
                    })
                });
                assert!(inside, "{house:?}: {} not walked through: {route:?}", s.id);
            }
        }
    }

    /// A ritual bot paces three passes, walks to the door once it opened,
    /// asks for its room, stays, leaves and rests.
    #[test]
    fn a_ritual_runs_its_course() {
        use rand::SeedableRng;
        let spec = RitualSpec {
            bots: 1,
            stay_secs: [1.0, 1.0],
            rest_secs: [1.0, 1.0],
        };
        let ritual = Ritual::new(spec.clone(), &[true]);
        let mut act = ritual.act(0);
        let mut rng = rand::rngs::StdRng::seed_from_u64(1);
        let mut stats = SecretStats::default();
        let mut now = Instant::now();
        act.placed(WEST, now);
        let mut sent = Vec::new();
        for k in 0..400 {
            now += Duration::from_millis(100);
            if stats.passes == 3 && !act.door_open {
                act.on_msg(
                    &ServerMsg::WishingRoomDoor { open_until_ms: 1 },
                    now,
                    &spec,
                    &mut rng,
                    &mut stats,
                );
            }
            let (msg, pos, _, _) = act.tick(now, 0.1, &Zone::Castle, &mut rng, &mut stats);
            // On the corridor between the marks, or at the room's entry.
            let corridor = (pos[1] - 63.0).abs() < 1e-3 && pos[0] >= -11.01 && pos[0] <= 5.01;
            assert!(corridor || pos == [0.0, 0.0, 8.0], "{k}: {pos:?}");
            if let Some(m) = msg {
                if let ClientMsg::EnterWishingRoom { room } = m {
                    act.on_msg(
                        &ServerMsg::ZoneChanged {
                            zone: Zone::WishingRoom {
                                owner_name: "Bot".into(),
                                room,
                                layout: "lost_property_vault".into(),
                            },
                            spawn: protocol::QPos::from_m([0.0, 0.0, 8.0]),
                            yaw: 0,
                        },
                        now,
                        &spec,
                        &mut rng,
                        &mut stats,
                    );
                }
                sent.push(m);
            }
        }
        assert_eq!(stats.doors, 1);
        assert_eq!(stats.entries, [1, 0]);
        assert!(sent.contains(&ClientMsg::LeaveInstance));
        assert_eq!(stats.failed, 0);
    }
}
