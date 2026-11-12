//! The duel rules as a pure step function: [`Duel::step`]
//! advances one duel by one world tick from both duelists' inputs and
//! reports what happened as `protocol::DuelEvent`s. The server runs it at
//! 20 Hz for every arena (`crate::dueling::DuelRules::tick_ms`); clients
//! never run it (they draw the events). No clock, no randomness: the same
//! inputs always give the same duel, which the balance simulation below
//! relies on.
//!
//! **The model** (slow and readable, so it works at browser latency):
//!
//! - Each duelist has Poise (`poise_max`); the first at 0 loses. When the
//!   time limit runs out, the higher Poise wins (equal Poise is a draw).
//!   Leaving (forfeit, disconnect) loses at once.
//! - A cast takes `cast_ms` (telegraphed to everyone by its gesture and
//!   colour, `CastStarted`), and its cooldown starts with the cast. One cast
//!   at a time; starting one drops a raised shield.
//! - **Attacks** (Torpeo, Titillara, Repulsa) release a projectile that
//!   flies straight toward where the opponent stood at release
//!   (`Launched`), so a duelist can sidestep it inside the ring. It hits if
//!   it passes within `duelist_radius_m` plus its own radius of the target.
//! - **Scutara** raises a shield for `shield_ms`. A projectile that reaches
//!   it is blocked (`Blocked`); one that arrives within the first
//!   `perfect_ms` is reflected back at its caster (`Reflected`; a reflected
//!   projectile cannot be reflected again).
//! - **Dimitto** is a projectile that breaks a shield instead of being
//!   blocked (`ShieldBroken`) and disarms its target (no casting for
//!   `disarm_ms`, any cast in progress lost: `Disarmed`); its long cast is
//!   `interruptible`: being hit while casting it breaks it (`Interrupted`).
//!
//! So counters work like rock-paper-scissors with timing: shields beat
//! attacks (and timed perfectly, turn them around), Dimitto beats
//! shields, attacks beat Dimitto's long cast. Numbers come from
//! `data/spells.json` and `data/dueling/rules.json` and never scale with
//! level. The balance test at the bottom plays scripted strategies against
//! each other: mixed play must beat both spamming attacks and turtling.
//!
//! **Spells to level 20** (protocol v11) add statuses and two new effects:
//!
//! - **Rigora** is a slow bolt that *roots* (no moving for
//!   `root.ms`; casting and shielding still work: `Rooted`).
//! - **Tardara** is a *wave* (`WaveShape`): a wall `2 * half_width_m`
//!   wide that no sidestep escapes; a hit *slows* (`Slowed`; the world
//!   applies the speed).
//! - **Adveni** is an interruptible bolt that, on a hit, takes any cast in
//!   progress (`Interrupted`), drops the shield, *pulls* the target toward
//!   its caster ([`pull_target`], `Pulled`) and *staggers* it (no casting,
//!   no moving: `Staggered`, [`CastRefusal::Staggered`]).
//! - **Dissolvo** ([`DuelEffect::Dispel`]) clears the caster's own
//!   root and slow at release (`Cleansed`), not a disarm or a stagger.
//! - **Custodio** ([`DuelEffect::Ward`]) raises a ward: the next
//!   projectile that would land on its duelist, from any direction and
//!   whatever it is, is stopped (`Warded`); unused, it fades (`WardFaded`).
//!
//! While rooted, staggered or pulled the duel holds the duelist where it
//! says ([`Duelist::held_pos`]) and ignores the input position.

use protocol::{
    ArenaPos, CastRefusal, DuelEndReason, DuelEvent, DuelSide, DuelSnapshot, DuelistState,
    ProjectileState, SpellId,
};

use crate::dueling::DuelRules;
use crate::spells::{DuelEffect, PullRider, RootRider, SlowRider, Spells, WaveShape};

/// One duelist's input for a tick.
#[derive(Debug, Clone, Copy, Default, PartialEq)]
pub struct DuelInput {
    /// A cast requested this tick (`ClientMsg::DuelCast`).
    pub cast: Option<SpellId>,
    /// Where the duelist stands (arena x and z, metres), from the world.
    pub pos: [f32; 2],
    /// The duelist left: forfeit (or left the arena) or disconnected.
    pub leave: Option<DuelEndReason>,
}

/// A raised shield.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Shield {
    pub spell: SpellId,
    pub until_ms: u32,
    pub perfect_until_ms: u32,
}

/// Adveni's slide: from `from` to `to` (arena metres), linearly over
/// `start_ms..end_ms` (duel clock).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct PullMotion {
    pub from: [f32; 2],
    pub to: [f32; 2],
    pub start_ms: u32,
    pub end_ms: u32,
}

/// A Guardian ward, up until `until_ms`.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Ward {
    pub spell: SpellId,
    pub until_ms: u32,
}

/// One duelist's state.
#[derive(Debug, Clone, PartialEq)]
pub struct Duelist {
    pub poise: u16,
    pub loadout: Vec<SpellId>,
    /// A cast in progress and when it releases.
    pub casting: Option<(SpellId, u32)>,
    /// When each spell cast so far can be cast again.
    pub cooldowns: Vec<(SpellId, u32)>,
    pub shield: Option<Shield>,
    pub disarmed_until: u32,
    pub pos: [f32; 2],
    /// Rigora: no moving before this.
    pub rooted_until: u32,
    /// Tardara: (until, pct).
    pub slowed: Option<(u32, u8)>,
    /// No casting and no moving before this (Adveni).
    pub staggered_until: u32,
    /// Adveni's slide in progress.
    pub pull: Option<PullMotion>,
    /// A Guardian ward.
    pub ward: Option<Ward>,
}

impl Duelist {
    /// Where the duel holds this duelist at `now_ms`: the pull's linear
    /// lerp (`from` -> `to` over `start_ms..end_ms`), else its current pos
    /// while rooted or staggered; `None` when it moves freely.
    pub fn held_pos(&self, now_ms: u32) -> Option<[f32; 2]> {
        if let Some(m) = self.pull {
            let span = m.end_ms.saturating_sub(m.start_ms).max(1) as f32;
            let t = (now_ms.saturating_sub(m.start_ms) as f32 / span).clamp(0.0, 1.0);
            return Some([
                m.from[0] + (m.to[0] - m.from[0]) * t,
                m.from[1] + (m.to[1] - m.from[1]) * t,
            ]);
        }
        (self.rooted(now_ms) || self.staggered(now_ms)).then_some(self.pos)
    }

    pub fn rooted(&self, now_ms: u32) -> bool {
        self.rooted_until > now_ms
    }

    pub fn staggered(&self, now_ms: u32) -> bool {
        self.staggered_until > now_ms
    }

    /// When `spell` is ready (0 if it never was cast).
    pub fn ready_at(&self, spell: SpellId) -> u32 {
        self.cooldowns
            .iter()
            .find(|c| c.0 == spell)
            .map_or(0, |c| c.1)
    }
}

/// A projectile in flight.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Projectile {
    pub id: u16,
    /// Whose it is: it can hit only the other side.
    pub side: DuelSide,
    pub spell: SpellId,
    pub pos: [f32; 2],
    /// Unit direction.
    pub dir: [f32; 2],
    pub speed_mps: f32,
    pub radius_m: f32,
    pub damage: u16,
    pub breaks_shield: bool,
    pub disarm_ms: u32,
    /// Metres flown since launch (or since reflected).
    pub travelled: f32,
    pub reflected: bool,
    /// Spells L20: the riders copied from its spell.
    pub root: Option<RootRider>,
    pub slow: Option<SlowRider>,
    pub pull: Option<PullRider>,
    pub wave: Option<WaveShape>,
}

/// Where a pull of `rider` takes a duelist at `from` toward `toward`:
/// along the line, at most `distance_m`, never closer than `gap_m` (no
/// movement if already within it). The ring is convex, so the point stays
/// inside it.
pub fn pull_target(from: [f32; 2], toward: [f32; 2], rider: &PullRider) -> [f32; 2] {
    let d = [toward[0] - from[0], toward[1] - from[1]];
    let len = (d[0] * d[0] + d[1] * d[1]).sqrt();
    if !len.is_finite() || len <= rider.gap_m || len < 1e-4 {
        return from;
    }
    let travel = rider.distance_m.min(len - rider.gap_m).max(0.0);
    [from[0] + d[0] / len * travel, from[1] + d[1] / len * travel]
}

/// One duel from countdown to result.
#[derive(Debug, Clone, PartialEq)]
pub struct Duel {
    now_ms: u32,
    fight_at_ms: u32,
    end_at_ms: u32,
    sides: [Duelist; 2],
    projectiles: Vec<Projectile>,
    next_id: u16,
    over: Option<(Option<DuelSide>, DuelEndReason)>,
}

impl Duel {
    /// A duel between loadouts `loadouts` standing at `pos`; the countdown
    /// starts now (duel clock 0).
    pub fn new(rules: &DuelRules, loadouts: [Vec<SpellId>; 2], pos: [[f32; 2]; 2]) -> Duel {
        let [a, b] = loadouts;
        let duelist = |loadout, pos| Duelist {
            poise: rules.poise_max,
            loadout,
            casting: None,
            cooldowns: Vec::new(),
            shield: None,
            disarmed_until: 0,
            pos,
            rooted_until: 0,
            slowed: None,
            staggered_until: 0,
            pull: None,
            ward: None,
        };
        Duel {
            now_ms: 0,
            fight_at_ms: rules.countdown_ms,
            end_at_ms: rules.countdown_ms + rules.time_limit_ms,
            sides: [duelist(a, pos[0]), duelist(b, pos[1])],
            projectiles: Vec::new(),
            next_id: 1,
            over: None,
        }
    }

    /// The duel clock (ms since the countdown began).
    pub fn now_ms(&self) -> u32 {
        self.now_ms
    }

    pub fn fight_at_ms(&self) -> u32 {
        self.fight_at_ms
    }

    /// Fighting time left (ms).
    pub fn time_left_ms(&self) -> u32 {
        self.end_at_ms
            .saturating_sub(self.now_ms.max(self.fight_at_ms))
    }

    /// How long the fight has run (ms, 0 during the countdown).
    pub fn fought_ms(&self) -> u32 {
        self.now_ms.saturating_sub(self.fight_at_ms)
    }

    pub fn fighting(&self) -> bool {
        self.over.is_none() && self.now_ms >= self.fight_at_ms
    }

    pub fn duelist(&self, side: DuelSide) -> &Duelist {
        &self.sides[side.index()]
    }

    pub fn projectiles(&self) -> &[Projectile] {
        &self.projectiles
    }

    /// The result once over: the winner (`None` for a draw) and why.
    pub fn result(&self) -> Option<(Option<DuelSide>, DuelEndReason)> {
        self.over
    }

    pub fn is_over(&self) -> bool {
        self.over.is_some()
    }

    /// Where the duel stands, for spectators joining late.
    pub fn snapshot(&self) -> DuelSnapshot {
        let state = |d: &Duelist| DuelistState {
            poise: d.poise,
            casting: d.casting,
            shield_until: d.shield.map(|s| s.until_ms),
            disarmed_until: (d.disarmed_until > self.now_ms).then_some(d.disarmed_until),
            cooldowns: d
                .cooldowns
                .iter()
                .copied()
                .filter(|c| c.1 > self.now_ms)
                .collect(),
            rooted_until: d.rooted(self.now_ms).then_some(d.rooted_until),
            slowed: d.slowed.filter(|s| s.0 > self.now_ms),
            staggered_until: d.staggered(self.now_ms).then_some(d.staggered_until),
            ward_until: d.ward.map(|w| w.until_ms),
        };
        DuelSnapshot {
            at_ms: self.now_ms,
            fight_at_ms: self.fight_at_ms,
            sides: [state(&self.sides[0]), state(&self.sides[1])],
            projectiles: self
                .projectiles
                .iter()
                .map(|p| ProjectileState {
                    id: p.id,
                    side: p.side,
                    spell: p.spell,
                    pos: cm(p.pos),
                    to: cm(add(p.pos, scale(p.dir, 1_000.0))),
                    speed_cms: speed_cms(p.speed_mps),
                })
                .collect(),
        }
    }

    /// Advances the duel by one tick (`rules.tick_ms`) and appends what
    /// happened to `events`. Does nothing once the duel is over.
    pub fn step(
        &mut self,
        rules: &DuelRules,
        spells: &Spells,
        input: &[DuelInput; 2],
        events: &mut Vec<DuelEvent>,
    ) {
        if self.over.is_some() {
            return;
        }
        let before = self.now_ms;
        self.now_ms += rules.tick_ms;
        let now = self.now_ms;
        // Positions: a held duelist (pulled, rooted, staggered) stays where
        // the duel says; a finished pull lapses after its last position.
        for (d, i) in self.sides.iter_mut().zip(input) {
            if let Some(p) = d.held_pos(now) {
                d.pos = p;
            } else if i.pos.iter().all(|v| v.is_finite()) {
                d.pos = i.pos;
            }
            if d.pull.is_some_and(|m| m.end_ms <= now) {
                d.pull = None;
            }
        }

        // Leaving loses at once (both leaving at once is a draw).
        let leaving: Vec<(DuelSide, DuelEndReason)> = DuelSide::BOTH
            .into_iter()
            .filter_map(|s| input[s.index()].leave.map(|r| (s, r)))
            .collect();
        if let Some(&(side, reason)) = leaving.first() {
            let winner = (leaving.len() == 1).then(|| side.other());
            self.finish(winner, reason, events);
            return;
        }

        if before < self.fight_at_ms && now >= self.fight_at_ms {
            events.push(DuelEvent::Fight);
        }
        let fighting = now >= self.fight_at_ms;
        for (side, d) in DuelSide::BOTH.into_iter().zip(&mut self.sides) {
            if d.shield.is_some_and(|s| s.until_ms <= now) {
                d.shield = None;
            }
            if d.ward.is_some_and(|w| w.until_ms <= now) {
                d.ward = None;
                events.push(DuelEvent::WardFaded { side });
            }
            // Roots, slows and staggers simply lapse (the snapshot and the
            // events' `until_ms` tell clients).
            if d.slowed.is_some_and(|s| s.0 <= now) {
                d.slowed = None;
            }
        }

        // New casts.
        for side in DuelSide::BOTH {
            let Some(spell) = input[side.index()].cast else {
                continue;
            };
            let d = &mut self.sides[side.index()];
            let duel = spells.duel(spell).filter(|_| d.loadout.contains(&spell));
            let refusal = if !fighting {
                Some(CastRefusal::NotFighting)
            } else if duel.is_none() {
                Some(CastRefusal::NotInLoadout)
            } else if d.disarmed_until > now {
                Some(CastRefusal::Disarmed)
            } else if d.staggered(now) {
                Some(CastRefusal::Staggered)
            } else if d.casting.is_some() {
                Some(CastRefusal::Busy)
            } else if d.ready_at(spell) > now {
                Some(CastRefusal::Cooldown)
            } else {
                None
            };
            if let Some(reason) = refusal {
                events.push(DuelEvent::Refused {
                    side,
                    spell,
                    reason,
                });
                continue;
            }
            let duel = duel.expect("checked");
            let release_ms = now + duel.cast_ms;
            d.casting = Some((spell, release_ms));
            d.cooldowns.retain(|c| c.0 != spell);
            d.cooldowns.push((spell, now + duel.cooldown_ms));
            d.shield = None;
            events.push(DuelEvent::CastStarted {
                side,
                spell,
                release_ms,
            });
        }

        // Casts that release now.
        for side in DuelSide::BOTH {
            let d = &self.sides[side.index()];
            let Some((spell, release)) = d.casting else {
                continue;
            };
            if release > now {
                continue;
            }
            let from = d.pos;
            let target = self.sides[side.other().index()].pos;
            self.sides[side.index()].casting = None;
            let Some(duel) = spells.duel(spell) else {
                continue;
            };
            match duel.effect {
                DuelEffect::Shield {
                    shield_ms,
                    perfect_ms,
                } => {
                    let shield = Shield {
                        spell,
                        until_ms: now + shield_ms,
                        perfect_until_ms: now + perfect_ms,
                    };
                    self.sides[side.index()].shield = Some(shield);
                    events.push(DuelEvent::ShieldUp {
                        side,
                        spell,
                        until_ms: shield.until_ms,
                        perfect_until_ms: shield.perfect_until_ms,
                    });
                }
                DuelEffect::Dispel => {
                    let d = &mut self.sides[side.index()];
                    d.rooted_until = d.rooted_until.min(now);
                    d.slowed = None;
                    events.push(DuelEvent::Cleansed { side, spell });
                }
                DuelEffect::Ward { ward_ms } => {
                    let until_ms = now + ward_ms;
                    self.sides[side.index()].ward = Some(Ward { spell, until_ms });
                    events.push(DuelEvent::WardUp {
                        side,
                        spell,
                        until_ms,
                    });
                }
                DuelEffect::Projectile {
                    speed_mps,
                    radius_m,
                    poise_damage,
                    breaks_shield,
                    disarm_ms,
                    root,
                    slow,
                    pull,
                    wave,
                } => {
                    let dir = direction(from, target, side);
                    let id = self.next_id;
                    self.next_id = self.next_id.wrapping_add(1).max(1);
                    self.projectiles.push(Projectile {
                        id,
                        side,
                        spell,
                        pos: from,
                        dir,
                        speed_mps,
                        radius_m,
                        damage: poise_damage,
                        breaks_shield,
                        disarm_ms,
                        travelled: 0.0,
                        reflected: false,
                        root,
                        slow,
                        pull,
                        wave,
                    });
                    events.push(DuelEvent::Launched {
                        id,
                        side,
                        spell,
                        from: cm(from),
                        to: cm(add(from, scale(dir, rules.max_range_m))),
                        speed_cms: speed_cms(speed_mps),
                    });
                }
            }
        }

        // Projectiles fly.
        let dt = rules.tick_ms as f32 / 1_000.0;
        let mut k = 0;
        while k < self.projectiles.len() {
            let p = self.projectiles[k];
            let target = p.side.other();
            let step = p.speed_mps * dt;
            let end = add(p.pos, scale(p.dir, step));
            let t = &self.sides[target.index()];
            let reach = rules.duelist_radius_m + p.radius_m;
            let reaches = match p.wave {
                // A wave: a wall across its path, `2 * half_width_m` wide.
                Some(w) => {
                    let rel = [t.pos[0] - p.pos[0], t.pos[1] - p.pos[1]];
                    let a = rel[0] * p.dir[0] + rel[1] * p.dir[1];
                    let c = (p.dir[0] * rel[1] - p.dir[1] * rel[0]).abs();
                    (-reach..=step + reach).contains(&a)
                        && c <= w.half_width_m + rules.duelist_radius_m
                }
                None => segment_distance(p.pos, end, t.pos) <= reach,
            };
            if !reaches {
                let p = &mut self.projectiles[k];
                p.pos = end;
                p.travelled += step;
                if p.travelled > rules.max_range_m {
                    events.push(DuelEvent::Missed { id: p.id });
                    self.projectiles.swap_remove(k);
                } else {
                    k += 1;
                }
                continue;
            }
            // It reaches the target this tick.
            let t = &mut self.sides[target.index()];
            if let Some(shield) = t.shield {
                if p.breaks_shield {
                    t.shield = None;
                    events.push(DuelEvent::ShieldBroken { side: target });
                } else if now <= shield.perfect_until_ms && !p.reflected {
                    let from = t.pos;
                    let back = self.sides[p.side.index()].pos;
                    let dir = direction(from, back, target);
                    let r = &mut self.projectiles[k];
                    r.side = target;
                    r.pos = from;
                    r.dir = dir;
                    r.travelled = 0.0;
                    r.reflected = true;
                    events.push(DuelEvent::Reflected {
                        id: p.id,
                        by: target,
                        from: cm(from),
                        to: cm(add(from, scale(dir, rules.max_range_m))),
                    });
                    k += 1;
                    continue;
                } else {
                    events.push(DuelEvent::Blocked {
                        id: p.id,
                        by: target,
                    });
                    self.projectiles.swap_remove(k);
                    continue;
                }
            }
            // A ward stops whatever would land, and is gone.
            let t = &mut self.sides[target.index()];
            if t.ward.take().is_some() {
                events.push(DuelEvent::Warded {
                    id: p.id,
                    by: target,
                });
                self.projectiles.swap_remove(k);
                continue;
            }
            t.poise = t.poise.saturating_sub(p.damage);
            events.push(DuelEvent::Hit {
                id: p.id,
                target,
                damage: p.damage,
                poise: t.poise,
            });
            if let Some(root) = p.root {
                t.rooted_until = t.rooted_until.max(now + root.ms);
                events.push(DuelEvent::Rooted {
                    side: target,
                    until_ms: t.rooted_until,
                });
            }
            if let Some(slow) = p.slow {
                t.slowed = Some((now + slow.ms, slow.pct));
                events.push(DuelEvent::Slowed {
                    side: target,
                    until_ms: now + slow.ms,
                    pct: slow.pct,
                });
            }
            if let Some(rider) = p.pull {
                let toward = self.sides[p.side.index()].pos;
                let t = &mut self.sides[target.index()];
                if let Some((spell, _)) = t.casting.take() {
                    events.push(DuelEvent::Interrupted {
                        side: target,
                        spell,
                    });
                }
                t.shield = None;
                let to = pull_target(t.pos, toward, &rider);
                let motion = PullMotion {
                    from: t.pos,
                    to,
                    start_ms: now,
                    end_ms: now + rider.move_ms,
                };
                t.pull = Some(motion);
                t.staggered_until = t.staggered_until.max(now + rider.stagger_ms);
                events.push(DuelEvent::Pulled {
                    side: target,
                    from: cm(motion.from),
                    to: cm(motion.to),
                    until_ms: motion.end_ms,
                });
                events.push(DuelEvent::Staggered {
                    side: target,
                    until_ms: t.staggered_until,
                });
            } else if p.disarm_ms > 0 {
                t.disarmed_until = now + p.disarm_ms;
                t.shield = None;
                if let Some((spell, _)) = t.casting.take() {
                    events.push(DuelEvent::Interrupted {
                        side: target,
                        spell,
                    });
                }
                events.push(DuelEvent::Disarmed {
                    side: target,
                    until_ms: t.disarmed_until,
                });
            } else if let Some((spell, _)) = t.casting
                && spells.duel(spell).is_some_and(|d| d.interruptible)
            {
                t.casting = None;
                events.push(DuelEvent::Interrupted {
                    side: target,
                    spell,
                });
            }
            self.projectiles.swap_remove(k);
        }

        // The end.
        let [a, b] = [self.sides[0].poise, self.sides[1].poise];
        if a == 0 || b == 0 {
            let winner = match (a, b) {
                (0, 0) => None,
                (0, _) => Some(DuelSide::B),
                _ => Some(DuelSide::A),
            };
            self.finish(winner, DuelEndReason::Poise, events);
        } else if now >= self.end_at_ms {
            let winner = match a.cmp(&b) {
                std::cmp::Ordering::Greater => Some(DuelSide::A),
                std::cmp::Ordering::Less => Some(DuelSide::B),
                std::cmp::Ordering::Equal => None,
            };
            self.finish(winner, DuelEndReason::Timeout, events);
        }
    }

    fn finish(
        &mut self,
        winner: Option<DuelSide>,
        reason: DuelEndReason,
        events: &mut Vec<DuelEvent>,
    ) {
        self.over = Some((winner, reason));
        self.projectiles.clear();
        for d in &mut self.sides {
            d.casting = None;
            d.shield = None;
            d.rooted_until = 0;
            d.slowed = None;
            d.staggered_until = 0;
            d.pull = None;
            d.ward = None;
        }
        events.push(DuelEvent::Ended { winner, reason });
    }
}

fn add(a: [f32; 2], b: [f32; 2]) -> [f32; 2] {
    [a[0] + b[0], a[1] + b[1]]
}

fn scale(a: [f32; 2], k: f32) -> [f32; 2] {
    [a[0] * k, a[1] * k]
}

/// The unit direction from `from` to `to`; straight down the lane (+x for
/// A, -x for B) when they coincide.
fn direction(from: [f32; 2], to: [f32; 2], side: DuelSide) -> [f32; 2] {
    let d = [to[0] - from[0], to[1] - from[1]];
    let len = (d[0] * d[0] + d[1] * d[1]).sqrt();
    if len < 1e-4 {
        return match side {
            DuelSide::A => [1.0, 0.0],
            DuelSide::B => [-1.0, 0.0],
        };
    }
    [d[0] / len, d[1] / len]
}

/// Distance from point `p` to the segment `a`-`b`.
fn segment_distance(a: [f32; 2], b: [f32; 2], p: [f32; 2]) -> f32 {
    let ab = [b[0] - a[0], b[1] - a[1]];
    let ap = [p[0] - a[0], p[1] - a[1]];
    let len2 = ab[0] * ab[0] + ab[1] * ab[1];
    let t = if len2 > 0.0 {
        ((ap[0] * ab[0] + ap[1] * ab[1]) / len2).clamp(0.0, 1.0)
    } else {
        0.0
    };
    let c = [a[0] + t * ab[0] - p[0], a[1] + t * ab[1] - p[1]];
    (c[0] * c[0] + c[1] * c[1]).sqrt()
}

/// Metres to the wire's centimetres.
fn cm(p: [f32; 2]) -> ArenaPos {
    let q = |v: f32| {
        (v * 100.0)
            .round()
            .clamp(f32::from(i16::MIN), f32::from(i16::MAX)) as i16
    };
    [q(p[0]), q(p[1])]
}

fn speed_cms(mps: f32) -> u16 {
    (mps * 100.0).round().clamp(0.0, f32::from(u16::MAX)) as u16
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::dueling::tests::rules;
    use crate::spells::tests::shipped as spells;

    const SCUTARA: SpellId = 10;
    const TORPEO: SpellId = 11;
    const DIMITTO: SpellId = 12;
    const TITILLARA: SpellId = 13;
    const REPULSA: SpellId = 14;
    const FULL: [SpellId; 5] = [SCUTARA, TORPEO, DIMITTO, TITILLARA, REPULSA];
    const ADVENI: SpellId = 4;
    const RIGORA: SpellId = 15;
    const DISSOLVO: SpellId = 16;
    const TARDARA: SpellId = 17;
    const GUARDIAN: SpellId = 18;
    /// Every dueling spell (spells to level 20).
    const ALL: [SpellId; 10] = [
        SCUTARA, TORPEO, DIMITTO, TITILLARA, REPULSA, RIGORA, ADVENI, DISSOLVO, TARDARA, GUARDIAN,
    ];

    fn duel(distance: f32) -> Duel {
        Duel::new(
            &rules(),
            [FULL.to_vec(), FULL.to_vec()],
            [[-distance / 2.0, 0.0], [distance / 2.0, 0.0]],
        )
    }

    fn duel_all(distance: f32) -> Duel {
        Duel::new(
            &rules(),
            [ALL.to_vec(), ALL.to_vec()],
            [[-distance / 2.0, 0.0], [distance / 2.0, 0.0]],
        )
    }

    fn input(d: &Duel, side: DuelSide, cast: Option<SpellId>) -> DuelInput {
        DuelInput {
            cast,
            pos: d.duelist(side).pos,
            leave: None,
        }
    }

    /// Steps with the given casts, returning the events.
    fn step(d: &mut Duel, a: Option<SpellId>, b: Option<SpellId>) -> Vec<DuelEvent> {
        let (r, s) = (rules(), spells());
        let i = [input(d, DuelSide::A, a), input(d, DuelSide::B, b)];
        let mut ev = Vec::new();
        d.step(&r, &s, &i, &mut ev);
        ev
    }

    fn until_fight(d: &mut Duel) {
        while !d.fighting() {
            step(d, None, None);
        }
    }

    fn run_ms(d: &mut Duel, ms: u32) -> Vec<DuelEvent> {
        let mut all = Vec::new();
        for _ in 0..ms / rules().tick_ms {
            all.extend(step(d, None, None));
        }
        all
    }

    #[test]
    fn casting_waits_for_the_countdown_and_cooldowns() {
        let mut d = duel(11.0);
        let ev = step(&mut d, Some(TORPEO), None);
        assert!(matches!(
            ev[..],
            [DuelEvent::Refused {
                reason: CastRefusal::NotFighting,
                ..
            }]
        ));
        until_fight(&mut d);
        let ev = step(&mut d, Some(TORPEO), Some(99));
        assert!(ev.iter().any(|e| matches!(
            e,
            DuelEvent::CastStarted {
                side: DuelSide::A,
                ..
            }
        )));
        assert!(ev.iter().any(|e| matches!(
            e,
            DuelEvent::Refused {
                side: DuelSide::B,
                reason: CastRefusal::NotInLoadout,
                ..
            }
        )));
        let ev = step(&mut d, Some(TITILLARA), None);
        assert!(matches!(
            ev[..],
            [DuelEvent::Refused {
                reason: CastRefusal::Busy,
                ..
            }]
        ));
        run_ms(&mut d, 700);
        let ev = step(&mut d, Some(TORPEO), None);
        assert!(matches!(
            ev[..],
            [DuelEvent::Refused {
                reason: CastRefusal::Cooldown,
                ..
            }]
        ));
    }

    #[test]
    fn a_projectile_hits_unless_sidestepped() {
        let mut d = duel(11.0);
        until_fight(&mut d);
        step(&mut d, Some(TORPEO), None);
        let ev = run_ms(&mut d, 2_000);
        let hit = ev.iter().find_map(|e| match e {
            DuelEvent::Hit { target, poise, .. } => Some((*target, *poise)),
            _ => None,
        });
        let Some(DuelEffect::Projectile { poise_damage, .. }) =
            spells().duel(TORPEO).map(|x| x.effect)
        else {
            panic!("torpeo is a projectile")
        };
        assert_eq!(hit, Some((DuelSide::B, rules().poise_max - poise_damage)));

        // Again, but B steps aside after the launch.
        let mut d = duel(11.0);
        until_fight(&mut d);
        step(&mut d, Some(TORPEO), None);
        let mut launched = false;
        let mut events = Vec::new();
        for _ in 0..60 {
            let mut b = input(&d, DuelSide::B, None);
            if launched {
                b.pos = [b.pos[0], 0.9];
            }
            let i = [input(&d, DuelSide::A, None), b];
            let mut ev = Vec::new();
            d.step(&rules(), &spells(), &i, &mut ev);
            launched |= ev.iter().any(|e| matches!(e, DuelEvent::Launched { .. }));
            events.extend(ev);
        }
        assert!(events.iter().any(|e| matches!(e, DuelEvent::Missed { .. })));
        assert_eq!(d.duelist(DuelSide::B).poise, 100);
    }

    /// Steps until A's next projectile launches; returns its arrival time at B.
    fn launch(d: &mut Duel, spell: SpellId) -> u32 {
        step(d, Some(spell), None);
        loop {
            let ev = step(d, None, None);
            if let Some(DuelEvent::Launched {
                speed_cms, from, ..
            }) = ev.iter().find(|e| matches!(e, DuelEvent::Launched { .. }))
            {
                let dist = (d.duelist(DuelSide::B).pos[0] - f32::from(from[0]) / 100.0).abs()
                    - rules().duelist_radius_m;
                return d.now_ms() + (dist / (f32::from(*speed_cms) / 100.0) * 1_000.0) as u32;
            }
        }
    }

    #[test]
    fn shields_block_and_perfect_timing_reflects() {
        let scutara = spells().duel(SCUTARA).copied().unwrap();
        let DuelEffect::Shield { perfect_ms, .. } = scutara.effect else {
            panic!("scutara is a shield")
        };
        // Early shield: blocked.
        let mut d = duel(11.0);
        until_fight(&mut d);
        let arrive = launch(&mut d, TORPEO);
        let shield_at = arrive - scutara.cast_ms - perfect_ms - 150;
        let wait = shield_at.saturating_sub(d.now_ms());
        run_ms(&mut d, wait);
        step(&mut d, None, Some(SCUTARA));
        let ev = run_ms(&mut d, 1_500);
        assert!(
            ev.iter().any(|e| matches!(
                e,
                DuelEvent::Blocked {
                    by: DuelSide::B,
                    ..
                }
            )),
            "{ev:?}"
        );
        assert_eq!(d.duelist(DuelSide::B).poise, 100);

        // Perfect: reflected back into A.
        let mut d = duel(11.0);
        until_fight(&mut d);
        let arrive = launch(&mut d, TORPEO);
        let shield_at = arrive - scutara.cast_ms - perfect_ms / 2;
        let wait = shield_at.saturating_sub(d.now_ms());
        run_ms(&mut d, wait);
        step(&mut d, None, Some(SCUTARA));
        let ev = run_ms(&mut d, 2_500);
        assert!(
            ev.iter().any(|e| matches!(
                e,
                DuelEvent::Reflected {
                    by: DuelSide::B,
                    ..
                }
            )),
            "{ev:?}"
        );
        assert!(ev.iter().any(|e| matches!(
            e,
            DuelEvent::Hit {
                target: DuelSide::A,
                ..
            }
        )));
        assert_eq!(d.duelist(DuelSide::B).poise, 100);
        assert!(d.duelist(DuelSide::A).poise < 100);
    }

    #[test]
    fn dimitto_breaks_shields_disarms_and_can_be_interrupted() {
        let mut d = duel(11.0);
        until_fight(&mut d);
        let arrive = launch(&mut d, DIMITTO);
        let wait = arrive.saturating_sub(d.now_ms() + 300);
        run_ms(&mut d, wait);
        step(&mut d, None, Some(SCUTARA));
        let ev = run_ms(&mut d, 1_000);
        assert!(
            ev.iter()
                .any(|e| matches!(e, DuelEvent::ShieldBroken { side: DuelSide::B }))
        );
        assert!(ev.iter().any(|e| matches!(
            e,
            DuelEvent::Disarmed {
                side: DuelSide::B,
                ..
            }
        )));
        let ev = step(&mut d, None, Some(TORPEO));
        assert!(matches!(
            ev[..],
            [DuelEvent::Refused {
                reason: CastRefusal::Disarmed,
                ..
            }]
        ));

        // A quick attack lands during Dimitto's long cast and breaks it.
        let mut d = duel(11.0);
        until_fight(&mut d);
        step(&mut d, None, Some(TITILLARA));
        run_ms(&mut d, 300);
        step(&mut d, Some(DIMITTO), None);
        let ev = run_ms(&mut d, 1_500);
        assert!(
            ev.iter().any(|e| matches!(
                e,
                DuelEvent::Interrupted {
                    side: DuelSide::A,
                    spell: DIMITTO
                }
            )),
            "{ev:?}"
        );
        assert!(!ev.iter().any(|e| matches!(
            e,
            DuelEvent::Launched {
                side: DuelSide::A,
                ..
            }
        )));
    }

    #[test]
    fn the_duel_ends_by_poise_timeout_or_leaving() {
        let r = rules();
        // Timeout with equal Poise is a draw.
        let mut d = duel(11.0);
        let ev = run_ms(&mut d, r.countdown_ms + r.time_limit_ms);
        assert_eq!(
            ev.last(),
            Some(&DuelEvent::Ended {
                winner: None,
                reason: DuelEndReason::Timeout
            })
        );
        assert!(d.is_over());
        assert!(step(&mut d, Some(TORPEO), None).is_empty(), "over is over");

        // Forfeit.
        let mut d = duel(11.0);
        let mut i = [input(&d, DuelSide::A, None), input(&d, DuelSide::B, None)];
        i[1].leave = Some(DuelEndReason::Forfeit);
        let mut ev = Vec::new();
        d.step(&r, &spells(), &i, &mut ev);
        assert_eq!(
            d.result(),
            Some((Some(DuelSide::A), DuelEndReason::Forfeit))
        );

        // Poise: Repulsa after Repulsa.
        let mut d = duel(11.0);
        until_fight(&mut d);
        while !d.is_over() {
            let ready = d.duelist(DuelSide::A).ready_at(REPULSA) <= d.now_ms();
            step(&mut d, ready.then_some(REPULSA), None);
        }
        assert_eq!(d.result(), Some((Some(DuelSide::A), DuelEndReason::Poise)));
        assert_eq!(d.duelist(DuelSide::B).poise, 0);
        // The snapshot is what spectators get.
        let snap = d.snapshot();
        assert_eq!(snap.sides[1].poise, 0);
        assert!(snap.projectiles.is_empty());
    }

    // ------------------------------------------------- spells to level 20

    /// Steps with casts and wanted positions (`None`: stand still).
    fn step_at(
        d: &mut Duel,
        casts: [Option<SpellId>; 2],
        pos: [Option<[f32; 2]>; 2],
    ) -> Vec<DuelEvent> {
        let (r, s) = (rules(), spells());
        let mut i = [
            input(d, DuelSide::A, casts[0]),
            input(d, DuelSide::B, casts[1]),
        ];
        for (k, p) in pos.into_iter().enumerate() {
            if let Some(p) = p {
                i[k].pos = p;
            }
        }
        let mut ev = Vec::new();
        d.step(&r, &s, &i, &mut ev);
        ev
    }

    /// Steps until `side`'s next projectile launches; returns its arrival
    /// time at the other duelist (straight down the lane).
    fn launch_by(d: &mut Duel, side: DuelSide, spell: SpellId) -> u32 {
        let mut casts = [None, None];
        casts[side.index()] = Some(spell);
        step_at(d, casts, [None, None]);
        loop {
            let ev = step(d, None, None);
            if let Some(DuelEvent::Launched {
                speed_cms, from, ..
            }) = ev.iter().find(|e| matches!(e, DuelEvent::Launched { .. }))
            {
                let other = d.duelist(side.other()).pos[0];
                let dist = (other - f32::from(from[0]) / 100.0).abs() - rules().duelist_radius_m;
                return d.now_ms() + (dist / (f32::from(*speed_cms) / 100.0) * 1_000.0) as u32;
            }
        }
    }

    /// Steps (no casts) until the duel clock reaches `at_ms`.
    fn run_until(d: &mut Duel, at_ms: u32) -> Vec<DuelEvent> {
        let mut all = Vec::new();
        while d.now_ms() + rules().tick_ms <= at_ms {
            all.extend(step(d, None, None));
        }
        all
    }

    fn has(ev: &[DuelEvent], f: impl Fn(&DuelEvent) -> bool) -> bool {
        ev.iter().any(f)
    }

    #[test]
    fn rigora_roots_but_allows_casting_and_misses_a_sidestep() {
        let mut d = duel_all(11.0);
        until_fight(&mut d);
        launch_by(&mut d, DuelSide::A, RIGORA);
        let mut ev = Vec::new();
        let start = d.now_ms();
        while !has(&ev, |e| matches!(e, DuelEvent::Rooted { .. })) {
            ev = step(&mut d, None, None);
            assert!(d.now_ms() < start + 3_000, "never rooted");
        }
        let until = d.duelist(DuelSide::B).rooted_until;
        assert!(has(&ev, |e| *e
            == DuelEvent::Rooted {
                side: DuelSide::B,
                until_ms: until
            }));
        assert!(has(&ev, |e| matches!(e, DuelEvent::Hit { damage: 8, .. })));
        assert_eq!(d.snapshot().sides[1].rooted_until, Some(until));
        // Rooted: the input position is ignored, casting and shields work.
        let held = d.duelist(DuelSide::B).pos;
        let ev = step_at(&mut d, [None, Some(SCUTARA)], [None, Some([3.0, 1.0])]);
        assert_eq!(d.duelist(DuelSide::B).pos, held);
        assert!(has(&ev, |e| matches!(
            e,
            DuelEvent::CastStarted {
                side: DuelSide::B,
                ..
            }
        )));
        let mut ev = step_at(&mut d, [None, None], [None, Some([3.0, 1.0])]);
        ev.extend(step_at(&mut d, [None, None], [None, Some([3.0, 1.0])]));
        assert!(has(&ev, |e| matches!(
            e,
            DuelEvent::ShieldUp {
                side: DuelSide::B,
                ..
            }
        )));
        while d.now_ms() < until {
            step_at(&mut d, [None, None], [None, Some([3.0, 1.0])]);
            if d.now_ms() < until {
                assert_eq!(d.duelist(DuelSide::B).pos, held);
            }
        }
        step_at(&mut d, [None, None], [None, Some([3.0, 1.0])]);
        assert_eq!(d.duelist(DuelSide::B).pos, [3.0, 1.0], "free again");
        assert_eq!(d.snapshot().sides[1].rooted_until, None);

        // A sidestep after the launch makes it miss.
        let mut d = duel_all(11.0);
        until_fight(&mut d);
        launch_by(&mut d, DuelSide::A, RIGORA);
        let aside = [5.5, 0.9];
        let mut all = Vec::new();
        for _ in 0..60 {
            all.extend(step_at(&mut d, [None, None], [None, Some(aside)]));
        }
        assert!(has(&all, |e| matches!(e, DuelEvent::Missed { .. })));
        assert!(!has(&all, |e| matches!(e, DuelEvent::Rooted { .. })));
    }

    #[test]
    fn tardaras_wave_cannot_be_sidestepped_but_can_be_shielded() {
        // B steps 1.0 m aside after the launch: the wave still catches it.
        let mut d = duel_all(11.0);
        until_fight(&mut d);
        launch_by(&mut d, DuelSide::A, TARDARA);
        let mut all = Vec::new();
        for _ in 0..60 {
            all.extend(step_at(&mut d, [None, None], [None, Some([5.5, 1.0])]));
        }
        assert!(has(&all, |e| matches!(
            e,
            DuelEvent::Hit {
                target: DuelSide::B,
                damage: 8,
                ..
            }
        )));
        let slowed = all.iter().find_map(|e| match *e {
            DuelEvent::Slowed {
                side: DuelSide::B,
                until_ms,
                pct,
            } => Some((until_ms, pct)),
            _ => None,
        });
        let (until, pct) = slowed.expect("slowed");
        assert_eq!(pct, 50);
        assert_eq!(d.duelist(DuelSide::B).poise, 92);
        assert!(d.duelist(DuelSide::B).slowed.is_none() || d.now_ms() < until);
        // Slowed duelists still move (the world applies the speed).
        let mut d2 = duel_all(11.0);
        d2.sides[1].slowed = Some((60_000, 50));
        step_at(&mut d2, [None, None], [None, Some([5.0, 0.5])]);
        assert_eq!(d2.duelist(DuelSide::B).pos, [5.0, 0.5]);
        assert_eq!(d2.snapshot().sides[1].slowed, Some((60_000, 50)));

        // Too far aside (beyond the wall's half width): a miss.
        let mut d = duel_all(11.0);
        until_fight(&mut d);
        launch_by(&mut d, DuelSide::A, TARDARA);
        let mut all = Vec::new();
        for _ in 0..100 {
            all.extend(step_at(&mut d, [None, None], [None, Some([5.5, 2.3])]));
        }
        assert!(!has(&all, |e| matches!(e, DuelEvent::Hit { .. })));
        assert!(has(&all, |e| matches!(e, DuelEvent::Missed { .. })));

        // Scutara blocks it; a perfect one reflects it into A.
        let scutara = spells().duel(SCUTARA).copied().unwrap();
        let DuelEffect::Shield { perfect_ms, .. } = scutara.effect else {
            unreachable!()
        };
        let mut d = duel_all(11.0);
        until_fight(&mut d);
        let arrive = launch_by(&mut d, DuelSide::A, TARDARA);
        run_until(&mut d, arrive - scutara.cast_ms - perfect_ms - 150);
        step(&mut d, None, Some(SCUTARA));
        let ev = run_ms(&mut d, 2_000);
        assert!(has(&ev, |e| matches!(
            e,
            DuelEvent::Blocked {
                by: DuelSide::B,
                ..
            }
        )));
        assert!(!has(&ev, |e| matches!(e, DuelEvent::Slowed { .. })));
        let mut d = duel_all(11.0);
        until_fight(&mut d);
        let arrive = launch_by(&mut d, DuelSide::A, TARDARA);
        run_until(&mut d, arrive - scutara.cast_ms - perfect_ms / 2);
        step(&mut d, None, Some(SCUTARA));
        let ev = run_ms(&mut d, 4_000);
        assert!(has(&ev, |e| matches!(
            e,
            DuelEvent::Reflected {
                by: DuelSide::B,
                ..
            }
        )));
        assert!(has(&ev, |e| matches!(
            e,
            DuelEvent::Slowed {
                side: DuelSide::A,
                pct: 50,
                ..
            }
        )));
    }

    #[test]
    fn adveni_interrupts_pulls_and_staggers() {
        let s = spells();
        let Some(DuelEffect::Projectile {
            pull: Some(rider), ..
        }) = s.duel(ADVENI).map(|x| x.effect)
        else {
            panic!("adveni pulls")
        };
        let mut d = duel_all(11.0);
        until_fight(&mut d);
        let arrive = launch_by(&mut d, DuelSide::A, ADVENI);
        // B starts Repulsa (not interruptible) so it is mid-cast on arrival.
        run_until(&mut d, arrive - 400);
        step(&mut d, None, Some(REPULSA));
        let mut ev = Vec::new();
        let start = d.now_ms();
        while !has(&ev, |e| matches!(e, DuelEvent::Pulled { .. })) {
            ev = step(&mut d, None, None);
            assert!(d.now_ms() < start + 3_000, "never pulled");
        }
        assert!(has(&ev, |e| *e
            == DuelEvent::Interrupted {
                side: DuelSide::B,
                spell: REPULSA
            }));
        let (from, to, until) = ev
            .iter()
            .find_map(|e| match *e {
                DuelEvent::Pulled {
                    side: DuelSide::B,
                    from,
                    to,
                    until_ms,
                } => Some((from, to, until_ms)),
                _ => None,
            })
            .unwrap();
        let hit_at = d.now_ms();
        assert_eq!(until, hit_at + rider.move_ms);
        let pulled = |p: ArenaPos| [f32::from(p[0]) / 100.0, f32::from(p[1]) / 100.0];
        let expect = pull_target(pulled(from), d.duelist(DuelSide::A).pos, &rider);
        assert_eq!(cm(expect), to);
        assert!((pulled(from)[0] - expect[0] - rider.distance_m).abs() < 0.02);
        // Staggered: Pulled then Staggered, casting refused.
        let order: Vec<_> = ev
            .iter()
            .filter(|e| matches!(e, DuelEvent::Pulled { .. } | DuelEvent::Staggered { .. }))
            .collect();
        assert!(matches!(
            order[..],
            [DuelEvent::Pulled { .. }, DuelEvent::Staggered { side: DuelSide::B, until_ms }]
                if *until_ms == hit_at + rider.stagger_ms
        ));
        // The slide lerps (input ignored), then holds until the stagger ends.
        let ev = step_at(&mut d, [None, Some(SCUTARA)], [None, Some([5.5, 2.0])]);
        assert!(has(&ev, |e| *e
            == DuelEvent::Refused {
                side: DuelSide::B,
                spell: SCUTARA,
                reason: CastRefusal::Staggered
            }));
        let t = (d.now_ms() - hit_at) as f32 / rider.move_ms as f32;
        let from_m = pulled(from);
        let mid = [
            from_m[0] + (expect[0] - from_m[0]) * t,
            from_m[1] + (expect[1] - from_m[1]) * t,
        ];
        let b = d.duelist(DuelSide::B).pos;
        assert!(
            (b[0] - mid[0]).abs() < 1e-3 && (b[1] - mid[1]).abs() < 1e-3,
            "{b:?} {mid:?}"
        );
        assert!(d.snapshot().sides[1].staggered_until.is_some());
        while d.now_ms() < hit_at + rider.stagger_ms {
            step_at(&mut d, [None, None], [None, Some([5.5, 2.0])]);
            if d.now_ms() < hit_at + rider.stagger_ms && d.now_ms() >= until {
                assert_eq!(d.duelist(DuelSide::B).pos, expect);
            }
        }
        assert!(d.duelist(DuelSide::B).pull.is_none());
        let gap = (expect[0] - d.duelist(DuelSide::A).pos[0]).hypot(expect[1]);
        assert!(gap >= rider.gap_m - 1e-3, "{gap}");
        step_at(&mut d, [None, None], [None, Some([3.0, 0.5])]);
        assert_eq!(d.duelist(DuelSide::B).pos, [3.0, 0.5], "free again");

        // Never closer than gap_m: at 2.5 m it moves only 0.5 m.
        let close = pull_target([2.5, 0.0], [0.0, 0.0], &rider);
        assert!((close[0] - rider.gap_m).abs() < 1e-4);
        assert_eq!(pull_target([1.0, 0.0], [0.0, 0.0], &rider), [1.0, 0.0]);

        // A shield blocks it.
        let scutara = s.duel(SCUTARA).copied().unwrap();
        let DuelEffect::Shield { perfect_ms, .. } = scutara.effect else {
            unreachable!()
        };
        let mut d = duel_all(11.0);
        until_fight(&mut d);
        let arrive = launch_by(&mut d, DuelSide::A, ADVENI);
        run_until(&mut d, arrive - scutara.cast_ms - perfect_ms - 150);
        step(&mut d, None, Some(SCUTARA));
        let ev = run_ms(&mut d, 1_500);
        assert!(has(&ev, |e| matches!(e, DuelEvent::Blocked { .. })));
        assert!(!has(&ev, |e| matches!(e, DuelEvent::Pulled { .. })));
        // A perfect shield reflects it: the reflector pulls Adveni's caster.
        let mut d = duel_all(11.0);
        until_fight(&mut d);
        let arrive = launch_by(&mut d, DuelSide::A, ADVENI);
        run_until(&mut d, arrive - scutara.cast_ms - perfect_ms / 2);
        step(&mut d, None, Some(SCUTARA));
        let ev = run_ms(&mut d, 2_000);
        assert!(has(&ev, |e| matches!(
            e,
            DuelEvent::Pulled {
                side: DuelSide::A,
                ..
            }
        )));
        assert!(
            d.duelist(DuelSide::A).pos[0] > -5.5 + 2.9,
            "A came toward B"
        );
    }

    #[test]
    fn dissolvo_clears_root_and_slow_only() {
        let mut d = duel_all(11.0);
        until_fight(&mut d);
        // Nothing to clear: still Cleansed.
        step(&mut d, Some(DISSOLVO), None);
        let ev = run_ms(&mut d, 300);
        assert!(has(&ev, |e| *e
            == DuelEvent::Cleansed {
                side: DuelSide::A,
                spell: DISSOLVO
            }));
        // Rooted, slowed, staggered and disarmed: only root and slow go.
        let mut d = duel_all(11.0);
        until_fight(&mut d);
        let now = d.now_ms();
        step(&mut d, Some(DISSOLVO), None);
        let a = &mut d.sides[0];
        a.rooted_until = now + 5_000;
        a.slowed = Some((now + 5_000, 50));
        a.disarmed_until = now + 5_000;
        a.staggered_until = now + 5_000;
        let ev = run_ms(&mut d, 300);
        assert!(has(&ev, |e| matches!(e, DuelEvent::Cleansed { .. })));
        let a = d.duelist(DuelSide::A);
        assert!(!a.rooted(d.now_ms()) && a.slowed.is_none());
        assert!(a.staggered(d.now_ms()) && a.disarmed_until > d.now_ms());
        let snap = d.snapshot();
        assert_eq!(snap.sides[0].rooted_until, None);
        assert_eq!(snap.sides[0].slowed, None);
        assert_eq!(snap.sides[0].staggered_until, Some(now + 5_000));
    }

    #[test]
    fn a_ward_blocks_one_spell_from_anywhere_then_fades() {
        let s = spells();
        let ward_ms = match s.duel(GUARDIAN).unwrap().effect {
            DuelEffect::Ward { ward_ms } => ward_ms,
            _ => unreachable!(),
        };
        // Cast for real: WardUp, then a bolt is warded, the next one hits.
        let mut d = duel_all(11.0);
        until_fight(&mut d);
        step(&mut d, Some(GUARDIAN), None);
        let ev = run_ms(&mut d, 1_600);
        let until = ev
            .iter()
            .find_map(|e| match *e {
                DuelEvent::WardUp {
                    side: DuelSide::A,
                    spell: GUARDIAN,
                    until_ms,
                } => Some(until_ms),
                _ => None,
            })
            .expect("ward up");
        assert_eq!(d.snapshot().sides[0].ward_until, Some(until));
        launch_by(&mut d, DuelSide::B, TORPEO);
        let ev = run_ms(&mut d, 1_500);
        assert!(has(&ev, |e| matches!(
            e,
            DuelEvent::Warded {
                by: DuelSide::A,
                ..
            }
        )));
        assert!(!has(&ev, |e| matches!(e, DuelEvent::Hit { .. })));
        assert_eq!(d.duelist(DuelSide::A).poise, 100);
        assert_eq!(d.snapshot().sides[0].ward_until, None);
        run_ms(&mut d, 1_000);
        launch_by(&mut d, DuelSide::B, TORPEO);
        let ev = run_ms(&mut d, 1_500);
        assert!(has(&ev, |e| matches!(
            e,
            DuelEvent::Hit {
                target: DuelSide::A,
                ..
            }
        )));

        // Unused, it fades after ward_ms.
        let mut d = duel_all(11.0);
        until_fight(&mut d);
        step(&mut d, Some(GUARDIAN), None);
        let ev = run_ms(&mut d, 1_500 + ward_ms + 100);
        assert!(has(&ev, |e| *e == DuelEvent::WardFaded { side: DuelSide::A }));
        assert!(d.duelist(DuelSide::A).ward.is_none());

        // Whatever it is: a wave, a pull, Dimitto after it broke a
        // shield, a bolt reflected back at the warded caster.
        let warded = |setup: &dyn Fn(&mut Duel) -> Vec<DuelEvent>| {
            let mut d = duel_all(11.0);
            until_fight(&mut d);
            d.sides[0].ward = Some(Ward {
                spell: GUARDIAN,
                until_ms: 60_000,
            });
            let ev = setup(&mut d);
            assert!(
                has(&ev, |e| matches!(
                    e,
                    DuelEvent::Warded {
                        by: DuelSide::A,
                        ..
                    }
                )),
                "{ev:?}"
            );
            assert!(!has(&ev, |e| matches!(
                e,
                DuelEvent::Hit {
                    target: DuelSide::A,
                    ..
                } | DuelEvent::Pulled { .. }
                    | DuelEvent::Slowed { .. }
                    | DuelEvent::Disarmed { .. }
            )));
            assert_eq!(d.duelist(DuelSide::A).poise, 100);
            ev
        };
        for spell in [TARDARA, ADVENI, RIGORA, REPULSA] {
            warded(&|d| {
                launch_by(d, DuelSide::B, spell);
                run_ms(d, 2_500)
            });
        }
        let ev = warded(&|d| {
            let arrive = launch_by(d, DuelSide::B, DIMITTO);
            run_until(d, arrive - 300);
            step(d, Some(SCUTARA), None);
            run_ms(d, 1_500)
        });
        assert!(has(&ev, |e| *e == DuelEvent::ShieldBroken { side: DuelSide::A }));
        let scutara = s.duel(SCUTARA).copied().unwrap();
        let DuelEffect::Shield { perfect_ms, .. } = scutara.effect else {
            unreachable!()
        };
        let ev = warded(&|d| {
            let arrive = launch_by(d, DuelSide::A, TORPEO);
            run_until(d, arrive - scutara.cast_ms - perfect_ms / 2);
            step(d, None, Some(SCUTARA));
            run_ms(d, 2_500)
        });
        assert!(has(&ev, |e| matches!(
            e,
            DuelEvent::Reflected {
                by: DuelSide::B,
                ..
            }
        )));
    }

    #[test]
    fn the_end_clears_every_status() {
        let mut d = duel_all(11.0);
        until_fight(&mut d);
        let a = &mut d.sides[0];
        a.rooted_until = 60_000;
        a.slowed = Some((60_000, 50));
        a.staggered_until = 60_000;
        a.ward = Some(Ward {
            spell: GUARDIAN,
            until_ms: 60_000,
        });
        a.pull = Some(PullMotion {
            from: [0.0, 0.0],
            to: [1.0, 0.0],
            start_ms: 0,
            end_ms: 60_000,
        });
        let mut i = [input(&d, DuelSide::A, None), input(&d, DuelSide::B, None)];
        i[1].leave = Some(DuelEndReason::Forfeit);
        d.step(&rules(), &spells(), &i, &mut Vec::new());
        let a = d.duelist(DuelSide::A);
        assert_eq!(
            (a.rooted_until, a.slowed, a.staggered_until, a.ward, a.pull),
            (0, None, 0, None, None)
        );
    }

    // ---------------------------------------------------------------- balance

    /// How a scripted duelist plays.
    #[derive(Debug, Clone, Copy, PartialEq, Eq)]
    enum Style {
        /// Always casting the heaviest attack that is ready.
        Spam,
        /// Shields whenever a projectile comes (early, never timed), attacks
        /// only while its shield cools down.
        Turtle,
        /// Reads the duel: perfect-timed shields to reflect, baits a shield
        /// and punishes its cooldown, interrupts long casts, Dimitto
        /// into a raised shield.
        Mixed,
        /// Spells L20: Spam over every attack-like spell (its loadout holds
        /// all ten dueling spells).
        SpamAll,
    }

    /// What is coming at `me` and when it lands (ms from now): projectiles in
    /// flight, and the opponent's attack being cast (it is telegraphed).
    /// Each is (spell, breaks a shield, already reflected, eta).
    fn incoming(d: &Duel, r: &DuelRules, me: DuelSide) -> Vec<(SpellId, bool, bool, u32)> {
        let pos = d.duelist(me).pos;
        let mut out: Vec<_> = d
            .projectiles()
            .iter()
            .filter(|p| p.side != me)
            .map(|p| {
                let along = (pos[0] - p.pos[0]) * p.dir[0] + (pos[1] - p.pos[1]) * p.dir[1];
                let dist = (along - r.duelist_radius_m - p.radius_m).max(0.0);
                (
                    p.spell,
                    p.breaks_shield,
                    p.reflected,
                    (dist / p.speed_mps * 1_000.0) as u32,
                )
            })
            .collect();
        let them = d.duelist(me.other());
        if let Some((spell, release)) = them.casting
            && let Some(DuelEffect::Projectile {
                speed_mps,
                radius_m,
                breaks_shield,
                ..
            }) = spells().duel(spell).map(|x| x.effect)
        {
            let gap = (pos[0] - them.pos[0]).hypot(pos[1] - them.pos[1]);
            let dist = (gap - r.duelist_radius_m - radius_m).max(0.0);
            let eta = release.saturating_sub(d.now_ms()) + (dist / speed_mps * 1_000.0) as u32;
            out.push((spell, breaks_shield, false, eta));
        }
        out
    }

    fn ready(d: &Duel, me: DuelSide, spell: SpellId) -> bool {
        let x = d.duelist(me);
        x.ready_at(spell) <= d.now_ms() && x.casting.is_none() && x.disarmed_until <= d.now_ms()
    }

    fn heaviest_ready_all(d: &Duel, me: DuelSide) -> Option<SpellId> {
        [REPULSA, TARDARA, TORPEO, RIGORA, ADVENI, TITILLARA]
            .into_iter()
            .find(|&s| ready(d, me, s))
    }

    fn heaviest_ready(d: &Duel, me: DuelSide) -> Option<SpellId> {
        [REPULSA, TORPEO, TITILLARA]
            .into_iter()
            .find(|&s| ready(d, me, s))
    }

    /// The scripted choice of `style` for `me` this tick. `slack` (ms)
    /// shifts the mixed player's timing so runs differ.
    fn choose(d: &Duel, r: &DuelRules, me: DuelSide, style: Style, slack: u32) -> Option<SpellId> {
        let them = d.duelist(me.other());
        let now = d.now_ms();
        let threats = incoming(d, r, me);
        match style {
            Style::Spam => heaviest_ready(d, me),
            Style::SpamAll => heaviest_ready_all(d, me),
            Style::Turtle => {
                // Anything landing within a shield.s length: shield now.
                let soon = threats.iter().any(|t| t.3 <= 700);
                if soon && ready(d, me, SCUTARA) {
                    return Some(SCUTARA);
                }
                let shield_cooling = d.duelist(me).ready_at(SCUTARA) > now;
                if shield_cooling && d.duelist(me).shield.is_none() && !soon {
                    return heaviest_ready(d, me);
                }
                None
            }
            Style::Mixed => {
                let s = spells();
                let scutara = s.duel(SCUTARA).copied().unwrap();
                let DuelEffect::Shield { perfect_ms, .. } = scutara.effect else {
                    unreachable!()
                };
                // A shield timed to catch the next blockable projectile in
                // its perfect window, when the shield will be ready for it.
                let aim = scutara.cast_ms + perfect_ms / 2 + slack;
                let shield_ready_at = d.duelist(me).ready_at(SCUTARA);
                let next = threats
                    .iter()
                    .filter(|&&(_, breaks, reflected, eta)| {
                        !breaks && !reflected && now + eta.saturating_sub(aim) >= shield_ready_at
                    })
                    .map(|t| t.3)
                    .min();
                if let Some(eta) = next
                    && eta <= aim
                    && ready(d, me, SCUTARA)
                {
                    return Some(SCUTARA);
                }
                // A raised shield about to catch something: casting would drop it.
                if let Some(shield) = d.duelist(me).shield
                    && threats
                        .iter()
                        .any(|t| !t.1 && now + t.3 <= shield.until_ms + r.tick_ms)
                {
                    return None;
                }
                // Time free to cast before that shield is due; with the
                // shield ready, also before the quickest attack the opponent
                // could start now would need it.
                let mut free = next.map_or(u32::MAX, |eta| eta.saturating_sub(aim + r.tick_ms));
                if shield_ready_at <= now + 300 && them.casting.is_none() {
                    let gap = (d.duelist(me).pos[0] - them.pos[0]).abs();
                    let quickest = [REPULSA, TORPEO, TITILLARA]
                        .into_iter()
                        .filter_map(|x| {
                            let duel = s.duel(x)?;
                            let DuelEffect::Projectile { speed_mps, .. } = duel.effect else {
                                return None;
                            };
                            let wait = them.ready_at(x).saturating_sub(now);
                            Some(wait + duel.cast_ms + (gap / speed_mps * 1_000.0) as u32)
                        })
                        .min();
                    if let Some(q) = quickest {
                        free = free.min(q.saturating_sub(aim + r.tick_ms));
                    }
                }
                let fits = |spell: SpellId| {
                    ready(d, me, spell) && s.duel(spell).is_some_and(|x| x.cast_ms <= free)
                };
                // Interrupt a long cast.
                if let Some((spell, release)) = them.casting
                    && s.duel(spell).is_some_and(|x| x.interruptible)
                    && release > now + 900
                    && fits(TITILLARA)
                {
                    return Some(TITILLARA);
                }
                // A raised shield, or an idle opponent with one ready: break
                // it, or bait it with something cheap.
                let their_shield_ready = them.ready_at(SCUTARA) <= now + 400;
                if them.shield.is_some() || (their_shield_ready && them.casting.is_none()) {
                    return [DIMITTO, TITILLARA].into_iter().find(|&x| fits(x));
                }
                // Their shield is down (or they are busy casting): hit hard.
                [REPULSA, TORPEO, TITILLARA].into_iter().find(|&x| fits(x))
            }
        }
    }

    /// Plays one duel to the end; returns the winner.
    fn play(a: Style, b: Style, distance: f32, slack: u32) -> (Option<DuelSide>, DuelEndReason) {
        play_counting(a, b, distance, slack, &mut Default::default())
    }

    /// What happened in a duel, per side: [hits taken, damage taken,
    /// blocks made, reflects made, casts started].
    type Tally = [[u32; 5]; 2];

    fn play_counting(
        a: Style,
        b: Style,
        distance: f32,
        slack: u32,
        tally: &mut Tally,
    ) -> (Option<DuelSide>, DuelEndReason) {
        let (r, s) = (rules(), spells());
        let loadout = |style| match style {
            Style::SpamAll => ALL.to_vec(),
            _ => FULL.to_vec(),
        };
        let mut d = Duel::new(
            &r,
            [loadout(a), loadout(b)],
            [[-distance / 2.0, 0.0], [distance / 2.0, 0.0]],
        );
        let mut events = Vec::new();
        while !d.is_over() {
            let ia = DuelInput {
                cast: choose(&d, &r, DuelSide::A, a, slack),
                ..input(&d, DuelSide::A, None)
            };
            let ib = DuelInput {
                cast: choose(&d, &r, DuelSide::B, b, slack),
                ..input(&d, DuelSide::B, None)
            };
            events.clear();
            d.step(&r, &s, &[ia, ib], &mut events);
            for e in &events {
                match *e {
                    DuelEvent::Hit { target, damage, .. } => {
                        tally[target.index()][0] += 1;
                        tally[target.index()][1] += u32::from(damage);
                    }
                    DuelEvent::Blocked { by, .. } => tally[by.index()][2] += 1,
                    DuelEvent::Reflected { by, .. } => tally[by.index()][3] += 1,
                    DuelEvent::CastStarted { side, .. } => tally[side.index()][4] += 1,
                    _ => {}
                }
            }
        }
        d.result().expect("over")
    }

    /// Prints the opening events of a duel, for tuning.
    #[test]
    #[ignore]
    fn duel_trace() {
        let (r, s) = (rules(), spells());
        let mut d = duel(11.0);
        let mut events = Vec::new();
        while !d.is_over() && d.now_ms() < 12_000 {
            let ca = choose(&d, &r, DuelSide::A, Style::Mixed, 20);
            let cb = choose(&d, &r, DuelSide::B, Style::Spam, 20);
            let ia = DuelInput {
                cast: ca,
                ..input(&d, DuelSide::A, None)
            };
            let ib = DuelInput {
                cast: cb,
                ..input(&d, DuelSide::B, None)
            };
            events.clear();
            d.step(&r, &s, &[ia, ib], &mut events);
            if !events.is_empty() {
                println!(
                    "{:>6} A threats {:?} | {:?}",
                    d.now_ms(),
                    incoming(&d, &r, DuelSide::A),
                    events
                );
            }
        }
    }

    /// Prints what happens in a few duels (`cargo test -p sim-core
    /// duel_tallies -- --nocapture --ignored`), for tuning.
    #[test]
    #[ignore]
    fn duel_tallies() {
        for (a, b) in [
            (Style::Mixed, Style::Spam),
            (Style::Mixed, Style::Turtle),
            (Style::Spam, Style::Turtle),
        ] {
            for distance in [9.0, 11.0, 13.0] {
                let mut t = Tally::default();
                let res = play_counting(a, b, distance, 20, &mut t);
                println!(
                    "{a:?} vs {b:?} at {distance} m: {res:?}; [hits, damage, blocks, reflects, casts] {t:?}"
                );
            }
        }
    }

    /// Mixed play's record against `other` over varied distances, timing
    /// and both starting sides: (wins, losses, draws, timeouts).
    fn record(other: Style) -> (u32, u32, u32, u32) {
        let (mut w, mut l, mut dr, mut to) = (0, 0, 0, 0);
        for distance in [9.0, 10.0, 11.0, 12.0, 13.0] {
            for slack in [0, 20, 40, 60] {
                for mixed_side in DuelSide::BOTH {
                    let (a, b) = match mixed_side {
                        DuelSide::A => (Style::Mixed, other),
                        DuelSide::B => (other, Style::Mixed),
                    };
                    let (winner, reason) = play(a, b, distance, slack);
                    to += u32::from(reason == DuelEndReason::Timeout);
                    match winner {
                        Some(s) if s == mixed_side => w += 1,
                        Some(_) => l += 1,
                        None => dr += 1,
                    }
                }
            }
        }
        (w, l, dr, to)
    }

    #[test]
    fn mixed_play_beats_both_extremes() {
        let spam = record(Style::Spam);
        let turtle = record(Style::Turtle);
        println!("mixed vs spam (w, l, d, timeouts): {spam:?}");
        println!("mixed vs turtle (w, l, d, timeouts): {turtle:?}");
        for (what, (w, l, dr, _)) in [("spam", spam), ("turtle", turtle)] {
            let games = w + l + dr;
            assert!(
                w * 10 >= games * 8,
                "mixed won {w} of {games} against {what}"
            );
        }
        // Duels are decided by play, not mostly by the clock.
        let (_, _, _, timeouts) = spam;
        assert!(timeouts * 2 < spam.0 + spam.1 + spam.2);
        // Neither extreme is a strict winner over the other: spam beats a
        // turtle that never times its shield, or at least is not crushed.
        let (sw, ..) = {
            let mut out = (0u32, 0u32);
            for distance in [9.0, 11.0, 13.0] {
                for side in DuelSide::BOTH {
                    let (a, b) = match side {
                        DuelSide::A => (Style::Spam, Style::Turtle),
                        DuelSide::B => (Style::Turtle, Style::Spam),
                    };
                    match play(a, b, distance, 0).0 {
                        Some(s) if s == side => out.0 += 1,
                        _ => out.1 += 1,
                    }
                }
            }
            out
        };
        println!("spam vs turtle: spam won {sw} of 6");
    }

    /// Spells L20: spamming the new attacks too still loses to mixed play
    /// (old loadout) in at least 70% of the usual grid.
    #[test]
    fn mixed_beats_spam_with_new_spells() {
        let (w, l, dr, to) = record(Style::SpamAll);
        println!(
            "mixed vs spam-all (w, l, d, timeouts): {:?}",
            (w, l, dr, to)
        );
        let games = w + l + dr;
        assert!(
            w * 10 >= games * 7,
            "mixed won {w} of {games} against spam-all"
        );
    }

    #[test]
    fn duels_are_deterministic_and_symmetric() {
        assert_eq!(play(Style::Spam, Style::Spam, 11.0, 0).0, None);
        assert_eq!(
            play(Style::Mixed, Style::Turtle, 11.0, 20),
            play(Style::Mixed, Style::Turtle, 11.0, 20)
        );
        let ab = play(Style::Mixed, Style::Spam, 10.0, 0).0;
        let ba = play(Style::Spam, Style::Mixed, 10.0, 0).0;
        assert_eq!(ab.map(DuelSide::other), ba);
    }
}
