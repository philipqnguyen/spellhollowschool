//! One bot: gets a dev session, dresses in a random outfit (see [`dress`]),
//! connects to `/ws`, says `Hello`, then walks the castle sending `Move` at
//! 10 Hz (once a second while standing still, like the browser client),
//! chats and emotes now and then and pings to measure round trips. It applies
//! every snapshot through `protocol::SnapshotReceiver`, exactly as the
//! browser's decoder does, so a delta that does not line up is caught (and
//! counted as a desync). It records everything it receives in a [`BotReport`].
//!
//! Each bot walks with its house's view of the castle (rival common rooms are
//! walls, as on the server), in the staircase state its clock gives
//! ([`crate::stairs`]). A connection that ends before the run does
//! counts as a disconnect; the bot then reconnects with the same session so
//! the load stays up, unless the server's kick code says not to.
//!
//! [`dress`]: crate::dress

use std::sync::Arc;
use std::sync::atomic::Ordering;
use std::time::Duration;

use futures_util::{SinkExt, StreamExt};
use protocol::{
    Anim, ChatChannel, ClientMsg, Emote, EntityEntry, House, LookCache, PROTOCOL_VERSION,
    ServerMsg, SnapshotReceiver, Zone, decode_server, encode_client,
};
use rand::rngs::StdRng;
use rand::{Rng, SeedableRng};
use tokio::net::TcpStream;
use tokio::sync::watch;
use tokio::time::{Instant, MissedTickBehavior, interval, sleep, sleep_until, timeout};
use tokio_tungstenite::tungstenite::Message;
use tokio_tungstenite::{MaybeTlsStream, WebSocketStream};

use crate::dress::{self, Dressed, Wardrobe};
use crate::exchange::{Exchange, ExchangeAct};
use crate::http::Target;
use crate::nav::Nav;
use crate::potion::{self, Potions};
use crate::school::{Act, Role, School};
use crate::stats::{BotReport, Live, ws_header_len};
use crate::walker::Walker;

/// Moves are sent this often while walking.
const MOVE_INTERVAL: Duration = Duration::from_millis(100);
/// And this often while standing still.
const IDLE_MOVE_INTERVAL: Duration = Duration::from_secs(1);
/// Longest wait for `Welcome`, the login queue included.
const WELCOME_TIMEOUT: Duration = Duration::from_secs(300);
/// Attempts to get a session or a connection before giving up on this bot.
const MAX_ATTEMPTS: u32 = 8;

/// Neutral small talk (no setting-specific names; those live in data files).
const LINES: &[&str] = &[
    "Hello!",
    "Has anyone seen my scarf?",
    "Which way to the library?",
    "Meet by the fountain after class?",
    "Lovely weather on the grounds today.",
    "Anyone selling a spare hat?",
    "See you at dinner.",
    "I think the stairs moved again.",
];

const EMOTES: [Emote; 3] = [Emote::Wave, Emote::Bow, Emote::Sit];

type Socket = WebSocketStream<MaybeTlsStream<TcpStream>>;

/// Settings every bot shares.
pub struct Shared {
    pub target: Target,
    /// Navigation per house (and staircase state).
    pub navs: crate::stairs::CastleNavs,
    /// The moving staircases' schedule (the castle's, on the
    /// period the server runs: `--stairs-period-s`).
    pub stairs: Option<Arc<crate::stairs::Staircase>>,
    pub live: Arc<Live>,
    /// Zero point of the `client_time_ms` sent in pings.
    pub epoch: Instant,
    /// Mean time between a bot's chat lines; `None` disables chat.
    pub chat_every: Option<Duration>,
    /// Mean time between a standing bot's emotes; `None` disables them.
    pub emote_every: Option<Duration>,
    /// Protocol v10: mean time between a bot's look-target changes (it
    /// looks at a random character it sees, or at nobody); `None`: never.
    pub look_every: Option<Duration>,
    /// NPCs (protocol v12): mean time between a bot's `NpcInteract` with a
    /// resident NPC it sees (most are out of reach: `TooFar`); `None`: never.
    pub npc_talk_every: Option<Duration>,
    pub ping_every: Duration,
    /// `SetViewLimit` sent after joining, if set.
    pub view_limit: Option<u8>,
    /// Dress each bot through the wardrobe API before it connects.
    pub wardrobe: Option<Arc<Wardrobe>>,
    /// Potions to hand out and the share of bots that get one (`--effects-pct`).
    pub potions: Option<(Arc<Potions>, f64)>,
    /// The class door and the Dueling Club, when some bots have a school role.
    pub school: Option<Arc<School>>,
    /// Castle kit M2: the Owl Exchange court (`exchange` scenario).
    pub exchange: Option<Arc<Exchange>>,
    /// How often each bot asks for the House Cup (`--cup-poll-secs`).
    pub cup_every: Option<Duration>,
    /// The percent of bots browsing the players list and
    /// how often (`None`: only at sign-in), `--players-pct` and
    /// `--players-every-secs`.
    pub players: Option<(f64, Option<Duration>)>,
    /// The grounds walkers (`grounds` scenario); the
    /// stair walkers; the passage explorers. A bot tours one.
    pub tours: Vec<Arc<crate::grounds::Grounds>>,
    /// The ritual bots (Wishing Room).
    pub ritual: Option<Arc<crate::secrets::Ritual>>,
    /// Flyers and pitch spectators (`pitch` scenario, `--fly-pct`).
    pub flight: Option<Arc<crate::flyer::Flight>>,
}

impl Shared {
    /// The navigation a bot of `house` walks in staircase state `state`:
    /// its tour's (grounds or stair walkers) or the scenario's.
    fn nav<'a>(
        &'a self,
        tour: Option<&'a crate::grounds::Grounds>,
        house: House,
        state: u8,
    ) -> &'a Nav {
        match tour {
            Some(g) => g.nav_in(house, state),
            None => self.navs.nav(house, state),
        }
    }
}

/// How a connection ended.
enum End {
    /// The run is over (or was interrupted).
    Stopped,
    /// The server or the network ended it early.
    Lost(String),
    /// The server ended it for good (a kick code that says not to retry).
    Final(String),
    /// Never got in.
    Failed(String),
}

/// What a bot carries from one connection to the next: the potion still to
/// drink and its school role.
struct Plan {
    drink: Option<i64>,
    act: Option<Act>,
    court: Option<ExchangeAct>,
    /// The tour it walks (grounds, stairs, passages), by index.
    tour: Option<usize>,
    /// Paces for the Wishing Room.
    ritual: bool,
    /// Flies or watches the pitch (`crate::flyer`): the bot's index.
    fly: Option<usize>,
}

/// Runs bot `index` (called `name`) from `start` until `stop` turns true.
pub async fn run(
    shared: Arc<Shared>,
    index: usize,
    name: String,
    role: Role,
    start: Instant,
    mut stop: watch::Receiver<bool>,
) -> BotReport {
    let mut report = BotReport::default();
    let act = match (&role, shared.school.as_deref()) {
        (Role::Walker, _) | (_, None) => None,
        (_, Some(school)) => {
            report.role = Some(role.name());
            Some(Act::new(role, school, index).with_epoch(shared.epoch))
        }
    };
    let court = shared.exchange.as_ref().map(|ex| ex.act(index));
    if let Some(c) = &court {
        report.role = Some(c.role_name());
    }
    let tour = shared.tours.iter().position(|g| g.is_member(index));
    if let Some(k) = tour {
        report.role = Some(shared.tours[k].role);
    }
    let ritual = shared.ritual.as_ref().is_some_and(|r| r.is_member(index));
    if ritual {
        report.role = Some(crate::secrets::RITUAL_ROLE);
    }
    let member = shared.flight.as_ref().and_then(|f| f.member(index));
    match member {
        Some(crate::flyer::Member::Flyer { .. }) => {
            report.role = Some(crate::flyer::FLYER);
            report.fly.flyers = 1;
        }
        Some(crate::flyer::Member::Spectator) => {
            report.role = Some(crate::flyer::SPECTATOR);
            report.fly.spectators = 1;
        }
        None => {}
    }
    let mut rng = StdRng::seed_from_u64(0x5eed_0000 + index as u64);
    tokio::select! {
        _ = sleep_until(start) => {}
        _ = stopped(&mut stop) => return report,
    }

    let mut token = None;
    for attempt in 1..=MAX_ATTEMPTS {
        // Dressed bots are also different people (`dress::Wardrobe::random_body`).
        let body = shared.wardrobe.as_ref().map(|w| w.random_body(&mut rng));
        // Bots start in the Entrance Hall (players outside).
        let at = crate::nav::BOT_STARTS[index % crate::nav::BOT_STARTS.len()];
        match session(&shared.target, &name, body, at).await {
            Ok(t) => {
                token = Some(t);
                break;
            }
            Err(e) => {
                report.connect_errors.push(e);
                if attempt < MAX_ATTEMPTS && !backoff(&mut stop, attempt).await {
                    return report;
                }
            }
        }
    }
    let Some(token) = token else {
        return report;
    };
    // The map, once at sign-in (as the browser does).
    crate::secrets::fetch_map(&shared.target, &token, &mut report.secrets).await;
    // The House Cup standings, polled beside the connection (an
    // HTTP request never holds up the bot's socket).
    let cup = shared.cup_every.map(|every| {
        let delay = every.mul_f64(rng.gen_range(0.0..1.0));
        tokio::spawn(crate::cup::poll(
            shared.target.clone(),
            token.clone(),
            every,
            delay,
            shared.epoch,
            stop.clone(),
        ))
    });
    if let Some(w) = &shared.wardrobe {
        match dress::dress(&shared.target, &token, w, &mut rng).await {
            Ok(Dressed::Already | Dressed::Now(_)) => report.dressed = true,
            Err(e) => report.dress_errors.push(e),
        }
    }
    // A share of the bots drinks a potion once in the world (`crate::potion`).
    // A share of the bots searches the players list and
    // opens a card, at sign-in and now and then, beside the connection.
    let players = shared
        .players
        .filter(|&(pct, _)| crate::players::browses(index, pct))
        .map(|(_, every)| {
            tokio::spawn(crate::players::run(
                shared.target.clone(),
                token.clone(),
                name.clone(),
                index,
                every,
                stop.clone(),
            ))
        });
    let mut drink = None;
    if let Some((potions, share)) = &shared.potions
        && rng.gen_bool(share.clamp(0.0, 1.0))
    {
        let (def, _) = potions.pick(&mut rng);
        match potion::grant(&shared.target, &token, def).await {
            Ok(id) => {
                report.potion = true;
                drink = Some(id);
            }
            Err(e) => {
                report.potion_errors += 1;
                report.dress_errors.push(e);
            }
        }
    }

    // A flyer takes the Flying skill (as after a Flying class) at
    // its broom tier.
    if let Some(crate::flyer::Member::Flyer { tier }) = member {
        let xp = crate::flyer::TIER_XP[usize::from(tier.clamp(1, 3) - 1)];
        let body = serde_json::json!({ "skills": { "flying": xp } });
        match shared
            .target
            .post_json_as(&token, "/api/dev/grant", &body)
            .await
        {
            Ok(r) if r.is_success() => report.fly.skill_grants += 1,
            _ => report.fly.grant_errors += 1,
        }
    }

    let mut plan = Plan {
        drink,
        act,
        court,
        tour,
        ritual,
        fly: member.map(|_| index),
    };
    let mut failures = 0;
    loop {
        match connection(
            &shared,
            &token,
            start,
            &mut report,
            &mut rng,
            &mut stop,
            &mut plan,
        )
        .await
        {
            End::Stopped => break,
            End::Lost(reason) => {
                failures = 0;
                shared.live.disconnects.fetch_add(1, Ordering::Relaxed);
                report.disconnects.push(reason);
            }
            End::Final(reason) => {
                shared.live.disconnects.fetch_add(1, Ordering::Relaxed);
                report.disconnects.push(reason);
                break;
            }
            End::Failed(reason) => {
                failures += 1;
                report.connect_errors.push(reason);
                if failures >= MAX_ATTEMPTS {
                    break;
                }
            }
        }
        if !backoff(&mut stop, failures.max(1)).await {
            break;
        }
    }
    if let Some(task) = cup
        && let Ok(polls) = task.await
    {
        report.cup = polls;
    }
    if let Some(task) = players
        && let Ok(stats) = task.await
    {
        report.players = stats;
    }
    report
}

/// Sleeps before a retry; false if the run stopped meanwhile.
async fn backoff(stop: &mut watch::Receiver<bool>, attempt: u32) -> bool {
    let wait = Duration::from_millis(500 * u64::from(attempt.min(10)));
    let slept = tokio::select! {
        _ = sleep(wait) => true,
        _ = stopped(stop) => false,
    };
    slept && !*stop.borrow()
}

/// Resolves once the run is told to stop. (Unlike `wait_for` itself, the
/// future holds no borrow guard, so it can sit in a `select!` across awaits.)
pub async fn stopped(stop: &mut watch::Receiver<bool>) {
    let _ = stop.wait_for(|s| *s).await;
}

/// `POST /api/dev/bot-session { name, body?, start }` → token. `start` is
/// a new bot's castle position (the server ignores it for an existing bot).
async fn session(
    target: &Target,
    name: &str,
    body: Option<protocol::Appearance>,
    start: [f32; 3],
) -> Result<String, String> {
    let res = target
        .post_json(
            "/api/dev/bot-session",
            &serde_json::json!({ "name": name, "body": body, "start": start }),
        )
        .await?;
    if !res.is_success() {
        return Err(format!(
            "bot-session {}: {}",
            res.status,
            String::from_utf8_lossy(&res.body)
        ));
    }
    let v: serde_json::Value =
        serde_json::from_slice(&res.body).map_err(|e| format!("bot-session body: {e}"))?;
    v["token"]
        .as_str()
        .map(str::to_owned)
        .ok_or_else(|| "bot-session response has no token".to_owned())
}

/// Decrements the in-world gauge when a bot leaves the world, however it leaves.
struct InWorld<'a>(&'a Live);

impl<'a> InWorld<'a> {
    fn enter(live: &'a Live) -> Self {
        live.in_world.fetch_add(1, Ordering::Relaxed);
        InWorld(live)
    }
}

impl Drop for InWorld<'_> {
    fn drop(&mut self) {
        self.0.in_world.fetch_sub(1, Ordering::Relaxed);
    }
}

async fn send(ws: &mut Socket, live: &Live, msg: &ClientMsg) -> Result<(), String> {
    let bytes = encode_client(msg);
    Live::add(&live.tx_bytes, bytes.len() as u64);
    ws.send(Message::Binary(bytes.into()))
        .await
        .map_err(|e| format!("send: {e}"))
}

/// A `Kick` as a connection end: retryable codes reconnect, the others stop.
fn kicked(code: protocol::KickCode, reason: &str) -> End {
    let text = format!("kick {code:?}: {reason}");
    if code.is_retryable() {
        End::Lost(text)
    } else {
        End::Final(text)
    }
}

/// One WebSocket connection, from connecting to its end.
async fn connection(
    shared: &Shared,
    token: &str,
    started: Instant,
    report: &mut BotReport,
    rng: &mut StdRng,
    stop: &mut watch::Receiver<bool>,
    plan: &mut Plan,
) -> End {
    let Plan {
        drink,
        act,
        court,
        tour: tour_index,
        ritual: ritual_member,
        fly: fly_index,
    } = plan;
    let live = &*shared.live;
    let connect = timeout(
        Duration::from_secs(30),
        tokio_tungstenite::connect_async(shared.target.ws_url()),
    );
    let mut ws = match connect.await {
        Ok(Ok((ws, _))) => ws,
        Ok(Err(e)) => return End::Failed(format!("connect: {e}")),
        Err(_) => return End::Failed("connect: timed out".into()),
    };
    if let Err(e) = send(
        &mut ws,
        live,
        &ClientMsg::Hello {
            protocol_version: PROTOCOL_VERSION,
            token: token.to_owned(),
            character_id: None,
        },
    )
    .await
    {
        return End::Failed(e);
    }

    // Hello → (Queued ...) → Welcome.
    let deadline = Instant::now() + WELCOME_TIMEOUT;
    let (spawn, house, zone, unix_ms) = loop {
        let frame = tokio::select! {
            f = timeout_at_frame(&mut ws, deadline) => f,
            _ = stopped(stop) => {
                let _ = ws.close(None).await;
                return End::Stopped;
            }
        };
        let bytes = match frame {
            Ok(b) => b,
            Err(e) => return End::Failed(format!("before welcome: {e}")),
        };
        count_rx(live, report, bytes.len());
        match decode_server(&bytes) {
            Ok(ServerMsg::Welcome {
                spawn,
                character,
                zone,
                unix_ms,
                ..
            }) => break (spawn.to_m(), character.house, zone, unix_ms),
            Ok(ServerMsg::Queued { .. }) => report.queued += 1,
            Ok(ServerMsg::Kick { code, reason }) => {
                return match kicked(code, &reason) {
                    End::Final(r) => End::Final(r),
                    End::Lost(r) | End::Failed(r) => End::Failed(r),
                    End::Stopped => End::Stopped,
                };
            }
            Ok(_) => {}
            Err(_) => return End::Failed("undecodable frame".into()),
        }
    };
    if !report.welcomed {
        report.welcomed = true;
        report.welcomed_at_secs = Some(shared.epoch.elapsed().as_secs_f64());
        report.join_ms = Some(started.elapsed().as_secs_f32() * 1000.0);
        live.welcomed.fetch_add(1, Ordering::Relaxed);
    }
    let _in_world = InWorld::enter(live);
    if let Some(n) = shared.view_limit
        && let Err(e) = send(&mut ws, live, &ClientMsg::SetViewLimit { n }).await
    {
        return End::Lost(e);
    }
    // A grounds walker routes through its own cells (a
    // stair walker too). In the staircase state of the server's
    // clock, as this bot reckons it.
    let tour = tour_index.map(|k| &*shared.tours[k]);
    let mut rider = shared
        .stairs
        .as_deref()
        .map(|st| crate::stairs::Rider::new(st, unix_ms, Instant::now()));
    let mut nav = shared.nav(tour, house, rider.as_ref().map_or(0, |r| r.state));
    // Bots walk the castle; one that resumes in a dorm (a run interrupted
    // by hand) leaves it first and starts walking once it is out.
    let mut in_castle = zone == Zone::Castle;
    // A ritual bot stays in its Wishing Room.
    let ritual_bot = *ritual_member && shared.ritual.is_some();
    let stays = |act: &Option<Act>, zone: &Zone| {
        act.as_ref().is_some_and(|a| a.stays_in(zone))
            || (ritual_bot && crate::secrets::RitualAct::stays_in(zone))
    };
    if !in_castle
        && !stays(act, &zone)
        && let Err(e) = send(&mut ws, live, &ClientMsg::LeaveInstance).await
    {
        return End::Lost(e);
    }
    // The potion, once: effects survive a reconnect on the server.
    if let Some(item_id) = drink.take()
        && let Err(e) = send(&mut ws, live, &ClientMsg::Drink { item_id }).await
    {
        return End::Lost(e);
    }
    // A school role starts at the class door or in the club.
    let mut zone_now = zone.clone();
    // The dev teleport moves the bot with a `Correction`: that one is expected.
    let mut teleported = false;
    if in_castle
        && let (Some(a), Some(school)) = (act.as_mut(), shared.school.as_deref())
        && let Some(pos) = a.on_welcome(unix_ms, school)
    {
        let res = shared
            .target
            .post_json_as(
                token,
                "/api/dev/teleport",
                &serde_json::json!({ "pos": pos }),
            )
            .await;
        if res.is_ok_and(|r| r.is_success()) {
            teleported = true;
        } else {
            report.teleport_errors += 1;
        }
    }

    // The Exchange court: standers go to their spot.
    if in_castle
        && let Some(c) = court.as_mut()
        && let Some(pos) = c.on_welcome(spawn, &mut report.exchange)
    {
        let res = shared
            .target
            .post_json_as(
                token,
                "/api/dev/teleport",
                &serde_json::json!({ "pos": pos }),
            )
            .await;
        if res.is_ok_and(|r| r.is_success()) {
            teleported = true;
        } else {
            report.teleport_errors += 1;
        }
    }

    // A ritual bot to the corridor's west mark (without the
    // teleport it walks like any other bot this connection).
    let mut ritual = None;
    if ritual_bot && in_castle {
        let res = shared
            .target
            .post_json_as(
                token,
                "/api/dev/teleport",
                &serde_json::json!({ "pos": crate::secrets::Ritual::start() }),
            )
            .await;
        if res.is_ok_and(|r| r.is_success()) {
            teleported = true;
            let mut r = shared.ritual.as_ref().expect("ritual bots").act(0);
            r.placed(crate::secrets::Ritual::start(), Instant::now());
            ritual = Some(r);
        } else {
            report.teleport_errors += 1;
        }
    }
    let mut zone_ritual = zone.clone();

    // Flyers to a launch spot, spectators to the stands.
    let flight = shared.flight.as_deref();
    let mut fly = match (flight, *fly_index) {
        (Some(fl), Some(i)) => fl.act(i, Instant::now()),
        _ => None,
    };
    if let (Some(f), Some(fl)) = (fly.as_mut(), flight) {
        let spot = in_castle.then(|| f.on_welcome(fl, rng));
        let mut placed = None;
        if let Some(spot) = spot {
            let res = shared
                .target
                .post_json_as(
                    token,
                    "/api/dev/teleport",
                    &serde_json::json!({ "pos": spot.0 }),
                )
                .await;
            if res.is_ok_and(|r| r.is_success()) {
                teleported = true;
                placed = Some(spot);
            } else {
                report.fly.teleport_errors += 1;
            }
        }
        f.teleported(placed, spawn, Instant::now(), rng);
    }

    // Earlier connections' travels, so a reconnect adds to them.
    let walked_before = report.walk;
    let mut walker = Walker::new(spawn, 0.0);
    if let Some(g) = tour {
        walker = walker.with_tour(g.tour(rng));
    }
    let mut snapshots = SnapshotReceiver::new();
    // The looks sent on this connection, as the browser's decoder keeps them.
    let mut looks = LookCache::new();
    let mut infos = Vec::new();
    let mut states = Vec::new();
    let mut seq = 0u32;
    let mut chat_seq = 0u32;
    let mut last_sent: Option<([f32; 3], u8, Anim)> = None;
    let mut last_sent_at = Instant::now();
    let mut ping_pending: Option<(u32, Instant)> = None;

    let mut ticks = interval(MOVE_INTERVAL);
    ticks.set_missed_tick_behavior(MissedTickBehavior::Delay);
    let mut last_tick = Instant::now();
    // Spread pings, chat and emotes over time so bots don't act in lockstep.
    let mut next_ping = Instant::now() + shared.ping_every.mul_f64(rng.gen_range(0.1..1.0));
    let poisson =
        |every: Duration, rng: &mut StdRng| every.mul_f64(-(1.0 - rng.gen_range(0.0..1.0f64)).ln());
    let mut next_chat = shared
        .chat_every
        .map(|every| Instant::now() + every.mul_f64(rng.gen_range(0.0..2.0)));
    let mut next_emote = shared
        .emote_every
        .map(|every| Instant::now() + poisson(every, rng));
    let mut next_look = shared
        .look_every
        .map(|every| Instant::now() + poisson(every, rng));
    let mut look_target: Option<protocol::EntityId> = None;
    let mut next_npc_talk = shared
        .npc_talk_every
        .map(|every| Instant::now() + poisson(every, rng));
    // NPCs (protocol v12): the resident NPCs in view (`EntityInfo.npc`).
    let mut npcs_in_view: std::collections::BTreeSet<protocol::EntityId> = Default::default();
    let far_future = Instant::now() + Duration::from_secs(86_400);

    loop {
        tokio::select! {
            _ = stopped(stop) => {
                let _ = timeout(Duration::from_secs(2), ws.close(None)).await;
                return End::Stopped;
            }
            _ = ticks.tick() => {
                let now = Instant::now();
                let dt = now.duration_since(last_tick).as_secs_f32();
                last_tick = now;
                if let (Some(a), Some(school)) = (act.as_mut(), shared.school.as_deref()) {
                    for msg in a.tick(now, &zone_now, school, rng, &mut report.school) {
                        if let Err(e) = send(&mut ws, live, &msg).await {
                            return End::Lost(e);
                        }
                    }
                }
                // Walk the castle, stand at the door or in the club, walk
                // to a desk and sit in class, or side-step on the stage;
                // nothing to send otherwise.
                let parked = act.as_ref().is_some_and(Act::parked);
                // A new staircase state: carried on a flight that
                // switched, the new state's navigation, the route again.
                if in_castle
                    && fly.is_none()
                    && let (Some(r), Some(st)) = (rider.as_mut(), shared.stairs.as_deref())
                    && r.follow(st, nav.world(), &mut walker, now, &mut report.stairs).is_some()
                {
                    nav = shared.nav(tour, house, r.state);
                    if walker.replan(nav) {
                        report.stairs.replans += 1;
                    }
                }
                let mut then_send = None;
                let (pos, yaw, anim) = if let Some(r) = ritual.as_mut() {
                    // Pacing, at the door, in the room.
                    let (msg, pos, yaw, anim) = r.tick(now, dt, &zone_ritual, rng, &mut report.secrets);
                    if let Some(msg) = msg
                        && let Err(e) = send(&mut ws, live, &msg).await
                    {
                        return End::Lost(e);
                    }
                    (pos, yaw, anim)
                } else if in_castle
                    && let (Some(f), Some(fl)) = (fly.as_mut(), flight)
                {
                    // Flying, or watching from the stands.
                    let (msg, pos, yaw, anim) = f.tick(now, dt, fl, rng, &mut report.fly);
                    if let Some(msg) = msg
                        && let Err(e) = send(&mut ws, live, &msg).await
                    {
                        return End::Lost(e);
                    }
                    (pos, yaw, anim)
                } else if in_castle
                    && let (Some(c), Some(ex)) = (court.as_mut(), shared.exchange.as_deref())
                {
                    // The Exchange court: errands only (stand, use the counter, walk to the ring).
                    if let Some(msg) = c.tick(now, ex, &mut walker, nav, rng, &mut report.exchange)
                        && let Err(e) = send(&mut ws, live, &msg).await
                    {
                        return End::Lost(e);
                    }
                    if walker.on_errand() {
                        walker.step(nav, rng, dt);
                    } else {
                        walker.stand();
                    }
                    (walker.pos, walker.yaw, walker.anim)
                } else if in_castle && !parked {
                    // On a swinging flight, hold still.
                    let held = match (rider.as_ref(), shared.stairs.as_deref()) {
                        (Some(r), Some(st)) => r.held(st, nav.world(), walker.pos, now),
                        _ => false,
                    };
                    if held {
                        report.stairs.held_frames += 1;
                        walker.stand();
                    } else {
                        walker.step(nav, rng, dt);
                    }
                    (walker.pos, walker.yaw, walker.anim)
                } else if in_castle {
                    (walker.pos, walker.yaw, Anim::Idle)
                } else if let (Some(a), Some(school)) = (act.as_mut(), shared.school.as_deref())
                    && let Some((pos, yaw, anim, msg)) =
                        a.class_move(school, dt, &mut report.school)
                {
                    // The sit goes after the move that stops at the desk.
                    then_send = msg;
                    (pos, yaw, anim)
                } else if let Some(m) = act.as_ref().and_then(|a| a.arena_move(now)) {
                    m
                } else {
                    continue;
                };
                let state = (pos, protocol::yaw_to_u8(yaw), anim);
                // A hovering broom is as cheap as a standing walker.
                let moving = last_sent.is_none_or(|s| s != state)
                    || !matches!(anim, Anim::Idle | Anim::Hover);
                if moving || now.duration_since(last_sent_at) >= IDLE_MOVE_INTERVAL {
                    seq += 1;
                    let msg = ClientMsg::Move { seq, pos, yaw, anim };
                    if let Err(e) = send(&mut ws, live, &msg).await {
                        return End::Lost(e);
                    }
                    report.moves_sent += 1;
                    last_sent = Some(state);
                    last_sent_at = now;
                }
                if let Some(msg) = then_send
                    && let Err(e) = send(&mut ws, live, &msg).await
                {
                    return End::Lost(e);
                }
            }
            _ = sleep_until(next_ping) => {
                let client_time_ms = shared.epoch.elapsed().as_millis() as u32;
                ping_pending = Some((client_time_ms, Instant::now()));
                next_ping += shared.ping_every;
                if let Err(e) = send(&mut ws, live, &ClientMsg::Ping { client_time_ms }).await {
                    return End::Lost(e);
                }
                report.pings_sent += 1;
            }
            _ = sleep_until(next_chat.unwrap_or(far_future)), if next_chat.is_some() => {
                let every = shared.chat_every.expect("chat enabled");
                // Exponential gaps: chat arrives as a Poisson process.
                next_chat = Some(Instant::now() + poisson(every, rng));
                let text = LINES[rng.gen_range(0..LINES.len())].to_owned();
                chat_seq += 1;
                let msg = ClientMsg::Chat { channel: ChatChannel::Say, text, client_seq: chat_seq };
                if let Err(e) = send(&mut ws, live, &msg).await {
                    return End::Lost(e);
                }
                report.chats_sent += 1;
            }
            _ = sleep_until(next_emote.unwrap_or(far_future)), if next_emote.is_some() => {
                let every = shared.emote_every.expect("emotes enabled");
                // Riders never emote.
                let rider = fly
                    .as_ref()
                    .is_some_and(|f| f.member != crate::flyer::Member::Spectator);
                if walker.anim == Anim::Idle && in_castle && !rider {
                    next_emote = Some(Instant::now() + poisson(every, rng));
                    let emote = EMOTES[rng.gen_range(0..EMOTES.len())];
                    if let Err(e) = send(&mut ws, live, &ClientMsg::Emote { emote }).await {
                        return End::Lost(e);
                    }
                    report.emotes_sent += 1;
                } else {
                    // Emotes are for standing still: try again shortly.
                    next_emote = Some(Instant::now() + Duration::from_secs(1));
                }
            }
            _ = sleep_until(next_look.unwrap_or(far_future)), if next_look.is_some() => {
                let every = shared.look_every.expect("looks enabled");
                next_look = Some(Instant::now() + poisson(every, rng));
                // Protocol v10: half the time stop looking, else look at
                // someone in view (the server keeps it only within range).
                let seen = snapshots.visible();
                let target = if look_target.is_some() && rng.gen_bool(0.5) || seen.is_empty() {
                    None
                } else {
                    Some(seen[rng.gen_range(0..seen.len())])
                };
                if target != look_target {
                    look_target = target;
                    if let Err(e) = send(&mut ws, live, &ClientMsg::LookAt { target }).await {
                        return End::Lost(e);
                    }
                    report.looks_sent += 1;
                }
            }
            _ = sleep_until(next_npc_talk.unwrap_or(far_future)), if next_npc_talk.is_some() => {
                let every = shared.npc_talk_every.expect("npc talk enabled");
                next_npc_talk = Some(Instant::now() + poisson(every, rng));
                let seen: Vec<protocol::EntityId> = npcs_in_view.iter().copied().collect();
                if !seen.is_empty() {
                    let id = seen[rng.gen_range(0..seen.len())];
                    if let Err(e) = send(&mut ws, live, &ClientMsg::NpcInteract { id }).await {
                        return End::Lost(e);
                    }
                    report.npc_talks_sent += 1;
                }
            }
            frame = ws.next() => {
                let bytes = match frame {
                    Some(Ok(Message::Binary(b))) => b,
                    Some(Ok(Message::Close(c))) => {
                        return End::Lost(format!("closed by server: {c:?}"));
                    }
                    // Pings are answered by tungstenite on the next write.
                    Some(Ok(_)) => continue,
                    Some(Err(e)) => return End::Lost(format!("receive: {e}")),
                    None => return End::Lost("connection ended".into()),
                };
                count_rx(live, report, bytes.len());
                let decoded = decode_server(&bytes);
                if let (Ok(msg), Some(a), Some(school)) = (&decoded, act.as_mut(), shared.school.as_deref()) {
                    for reply in a.on_msg(msg, Instant::now(), school, rng, &mut report.school) {
                        if let Err(e) = send(&mut ws, live, &reply).await {
                            return End::Lost(e);
                        }
                    }
                }
                if let (Ok(msg), Some(f), Some(fl)) = (&decoded, fly.as_mut(), flight) {
                    f.on_msg(msg, Instant::now(), fl, rng, &mut report.fly);
                }
                // The ritual's door and rooms; secrets found.
                if let (Ok(msg), Some(r), Some(rt)) = (&decoded, ritual.as_mut(), shared.ritual.as_deref()) {
                    r.on_msg(msg, Instant::now(), &rt.spec, rng, &mut report.secrets);
                }
                if let Ok(ServerMsg::Discovered { .. }) = &decoded {
                    report.secrets.discoveries += 1;
                }
                match decoded {
                    Ok(ServerMsg::Snapshot { entered, full, deltas, left, looks: look_changes, .. }) => {
                        report.snapshot_bytes.push(bytes.len() as u32);
                        report.look_changes += look_changes.len() as u64;
                        report.states_full += full.len() as u64;
                        report.states_delta += deltas.len() as u64;
                        let cached = entered.iter().filter(|e| matches!(e, EntityEntry::Cached { .. })).count() as u64;
                        report.entries_cached += cached;
                        report.entries_full += entered.len() as u64 - cached;
                        states.clear();
                        infos.clear();
                        let applied = looks
                            .resolve(&entered, &mut infos)
                            .and_then(|()| snapshots.apply(entered.iter().map(EntityEntry::id), &full, &deltas, &left, &mut states));
                        if let Err(e) = applied {
                            report.desyncs += 1;
                            return End::Lost(format!("desync: {e}"));
                        }
                        report.visible_sum += snapshots.visible().len() as u64;
                        let mut npc_bytes = 0usize;
                        for id in &left {
                            if npcs_in_view.remove(id) {
                                npc_bytes += postcard_len(id);
                            }
                        }
                        npcs_in_view.extend(infos.iter().filter(|i| i.npc.as_deref().is_some_and(is_resident)).map(|i| i.id));
                        if !npcs_in_view.is_empty() {
                            let visible = snapshots.visible();
                            npc_bytes += entered.iter().filter(|e| npcs_in_view.contains(&e.id())).map(postcard_len).sum::<usize>()
                                + full.iter().filter(|s| npcs_in_view.contains(&s.id)).map(postcard_len).sum::<usize>()
                                + deltas
                                    .iter()
                                    .filter(|d| visible.get(usize::from(d.index)).is_some_and(|id| npcs_in_view.contains(id)))
                                    .map(postcard_len)
                                    .sum::<usize>()
                                + look_changes.iter().filter(|l| npcs_in_view.contains(&l.id)).map(postcard_len).sum::<usize>();
                        }
                        report.npc_rx_bytes += npc_bytes as u64;
                        report.npc_visible_sum += npcs_in_view.len() as u64;
                    }
                    Ok(ServerMsg::NpcReply { reply, .. }) => {
                        report.npc_rx_bytes += bytes.len() as u64;
                        match reply {
                            protocol::NpcReply::Talk { .. } => report.npc_talk_replies += 1,
                            protocol::NpcReply::Gift { .. } => report.npc_gifts += 1,
                            protocol::NpcReply::Refused { code: protocol::NpcErrorCode::TooFar } => {
                                report.npc_too_far += 1
                            }
                            protocol::NpcReply::Refused { .. } => report.npc_refused += 1,
                        }
                    }
                    Ok(ServerMsg::ZoneChanged { zone, spawn, .. }) => {
                        snapshots.reset();
                        npcs_in_view.clear();
                        // The server forgets the look target on a zone change.
                        look_target = None;
                        walker.corrected(spawn.to_m());
                        // A flyer that resumed in a dorm stands where it comes out.
                        if let Some(f) = fly.as_mut() {
                            f.teleported(None, spawn.to_m(), Instant::now(), rng);
                        }
                        in_castle = zone == Zone::Castle;
                        if !in_castle && !stays(act, &zone) && let Err(e) = send(&mut ws, live, &ClientMsg::LeaveInstance).await {
                            return End::Lost(e);
                        }
                        zone_ritual = zone.clone();
                        zone_now = zone;
                    }
                    Ok(ServerMsg::Correction { pos, .. }) => {
                        if !std::mem::take(&mut teleported) {
                            report.corrections += 1;
                            live.corrections.fetch_add(1, Ordering::Relaxed);
                        }
                        walker.corrected(pos.to_m());
                        if let (Some(c), Some(ex)) = (court.as_mut(), shared.exchange.as_deref()) {
                            c.moved(Instant::now(), ex, rng);
                        }
                    }
                    Ok(ref m @ ServerMsg::StationOpened { .. }) => {
                        if let (Some(c), Some(ex)) = (court.as_mut(), shared.exchange.as_deref()) {
                            c.on_msg(m, ex, &mut report.exchange);
                        }
                    }
                    Ok(ServerMsg::Pong { client_time_ms, server_time_ms }) => {
                        if let Some((_, at)) = ping_pending.take_if(|p| p.0 == client_time_ms) {
                            let rtt = at.elapsed().as_secs_f32() * 1000.0;
                            report.rtt_ms.push(rtt);
                            // The staircase clock, refined.
                            if let Some(r) = rider.as_mut() {
                                r.pong(server_time_ms, rtt, Instant::now());
                            }
                        }
                    }
                    Ok(ServerMsg::Chat { .. }) => report.chats_received += 1,
                    Ok(ServerMsg::ChatRejected { .. }) => report.chat_rejected += 1,
                    Ok(ServerMsg::Drank { .. }) => report.drank += 1,
                    Ok(ServerMsg::PotionError { .. }) => report.potion_errors += 1,
                    Ok(ServerMsg::EffectsChanged { .. }) => report.effects_changed += 1,
                    Ok(ServerMsg::Kick { code, reason }) => return kicked(code, &reason),
                    Ok(_) => {}
                    Err(_) => return End::Lost("undecodable frame".into()),
                }
            }
        }
        report.walk = walked_before.plus(walker.stats);
    }
}

/// Whether `npc` (an `EntityInfo.npc` id) is one of the open castle's
/// residents in `data/npcs/roster.json`, not a professor or a club duelist:
/// only residents answer `NpcInteract`.
fn is_resident(npc: &str) -> bool {
    static IDS: std::sync::OnceLock<std::collections::BTreeSet<String>> =
        std::sync::OnceLock::new();
    IDS.get_or_init(|| {
        let roster: serde_json::Value =
            serde_json::from_str(include_str!("../../../data/npcs/roster.json"))
                .expect("roster.json parses");
        roster["npcs"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(|n| n["id"].as_str().map(str::to_owned))
            .collect()
    })
    .contains(npc)
}

/// The postcard size of one part of a message, for the NPCs' share of the
/// bytes received (`BotReport::npc_rx_bytes`).
fn postcard_len<T: serde::Serialize>(part: &T) -> usize {
    postcard::to_allocvec(part).map_or(0, |v| v.len())
}

fn count_rx(live: &Live, report: &mut BotReport, len: usize) {
    report.rx_bytes += len as u64;
    report.rx_wire_bytes += (len + ws_header_len(len)) as u64;
    report.rx_messages += 1;
    Live::add(&live.rx_bytes, len as u64);
    Live::add(&live.rx_wire_bytes, (len + ws_header_len(len)) as u64);
    Live::add(&live.rx_messages, 1);
}

/// The next binary frame, skipping control frames, before `deadline`.
async fn timeout_at_frame(ws: &mut Socket, deadline: Instant) -> Result<bytes::Bytes, String> {
    loop {
        let next = tokio::time::timeout_at(deadline, ws.next())
            .await
            .map_err(|_| "timed out".to_owned())?;
        match next {
            Some(Ok(Message::Binary(b))) => return Ok(b),
            Some(Ok(Message::Close(c))) => return Err(format!("closed: {c:?}")),
            Some(Ok(_)) => continue,
            Some(Err(e)) => return Err(e.to_string()),
            None => return Err("connection ended".into()),
        }
    }
}
