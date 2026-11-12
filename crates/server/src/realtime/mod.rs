//! The realtime game: the WebSocket gateway (`GET /ws`), the castle world and
//! the dorm instances.
//!
//! - [`gateway`]: one task per connection: the `Hello` handshake, the login
//!   queue, the heartbeat, and shuttling frames between socket and world.
//! - [`admission`]: the player cap (`SHS_PLAYER_CAP`) and the login queue.
//! - [`world`]: the 20 Hz simulation on its own thread; [`instances`]: zones,
//!   dorm rooms, doors, guests and furniture; [`social`]: friends as priority
//!   players and their online events.
//! - [`entities`], [`grid`], [`visibility`], [`snapshot`]: the world's data
//!   layout, neighbour queries, who-sees-whom and snapshot (delta) encoding.
//! - [`chat`]: chat text rules and the word filter; [`limits`]: rate limits.
//! - [`jobs`]: database work run off the world thread.
//! - [`persist`]: saves positions off the tick thread.
//! - [`ext`], the seams the trade, potion and find handlers
//!   (outside `realtime/`) work through, and potion effects as core state;
//!   [`stations`], station grants for crafting and the Owl Exchange.
//! - Feature instances (class sections, arenas) and NPCs in
//!   [`instances`] and [`ext`], their layouts in [`layouts`]; levels,
//!   Lucerna and zone rules in [`world`].
//! - [`explore`], the map's reveals, the secrets found and the
//!   pacing for the Wishing Room; [`wishing_room`], its door and its
//!   rooms (on the dorm instance code).
//! - [`presence`], the online characters by name for the
//!   players list.
//!
//! Refused chat lines are answered with `ServerMsg::ChatRejected`, failed
//! dorm and door actions with `ServerMsg::DormError`, kicks carry a `KickCode`.

pub mod admission;
pub mod chat;
pub mod club;
pub mod entities;
pub mod explore;
pub mod ext;
pub mod flying;
pub mod gateway;
pub mod grid;
pub mod instances;
pub mod jobs;
pub mod layouts;
pub mod limits;
pub mod metrics;
pub mod party;
pub mod persist;
pub mod presence;
pub mod snapshot;
pub mod social;
pub mod stairs;
pub mod stations;
pub mod visibility;
pub mod wishing_room;
pub mod world;

use std::collections::HashMap;
use std::path::Path;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};

use protocol::KickCode;
use sqlx::PgPool;

use crate::auth::token::TokenHash;
use tokio::sync::Notify;
use tokio::task::JoinHandle;

use self::admission::{Admission, Permit};
use self::chat::WordFilter;
use self::metrics::RealtimeMetrics;
use self::world::{WorldHandle, WorldSim};
use crate::AppState;
use crate::metrics::Registry;

// Reasons sent in `ServerMsg::Kick`, shown to the player. The client decides
// what to do from the `KickCode`, never from this text.
pub const KICK_REPLACED: &str = "You signed in somewhere else.";
pub const KICK_SLOW: &str = "Your connection is too slow to keep up.";
pub const KICK_RESTART: &str = "The server is restarting. Please come back in a minute.";
pub const KICK_TIMEOUT: &str = "The connection timed out.";
pub const KICK_FLOOD: &str = "Too many messages.";
pub const KICK_PROTOCOL: &str = "The game sent something unexpected. Please reload the page.";
pub const KICK_VERSION: &str = "This game version is out of date. Please reload the page.";
pub const KICK_SESSION: &str = "Your session has expired. Please sign in again.";
pub const KICK_NO_CHARACTER: &str = "Choose a character first.";
pub const KICK_FULL: &str = "The castle is full. Please try again shortly.";
pub const KICK_BANNED: &str = "This account has been suspended.";

/// The player-facing text sent with a kick code.
pub fn kick_reason(code: KickCode) -> &'static str {
    match code {
        KickCode::ServerRestart => KICK_RESTART,
        KickCode::Timeout => KICK_TIMEOUT,
        KickCode::TooSlow => KICK_SLOW,
        KickCode::ServerFull => KICK_FULL,
        KickCode::Replaced => KICK_REPLACED,
        KickCode::Banned => KICK_BANNED,
        KickCode::ProtocolError => KICK_PROTOCOL,
        KickCode::VersionMismatch => KICK_VERSION,
        KickCode::SessionExpired => KICK_SESSION,
        KickCode::NoCharacter => KICK_NO_CHARACTER,
        KickCode::Flood => KICK_FLOOD,
    }
}

/// Lets the world or the gateway ask a connection to close with a reason.
/// The first code wins; the connection task sends it as `Kick` and closes.
#[derive(Debug, Default)]
pub struct ConnControl {
    reason: Mutex<Option<KickCode>>,
    notify: Notify,
}

impl ConnControl {
    pub fn kick(&self, code: KickCode) {
        let mut r = self.reason.lock().expect("kick lock poisoned");
        if r.is_none() {
            *r = Some(code);
            // Stores a permit if nobody is waiting yet, so the wake-up is never lost.
            self.notify.notify_one();
        }
    }

    pub fn reason(&self) -> Option<KickCode> {
        *self.reason.lock().expect("kick lock poisoned")
    }

    /// Resolves with the code once kicked.
    pub async fn kicked(&self) -> KickCode {
        loop {
            if let Some(r) = self.reason() {
                return r;
            }
            self.notify.notified().await;
        }
    }
}

/// One connection per account: a new login kicks the old one and takes over
/// its world slot, so reconnecting never queues behind other players. Each
/// entry remembers the session the connection signed in with, so ending
/// that session (sign-out, password reset) can end the connection.
#[derive(Debug, Default)]
struct Accounts {
    map: Mutex<HashMap<i64, AccountConn>>,
    next_conn: AtomicU64,
}

#[derive(Debug)]
struct AccountConn {
    conn: u64,
    /// The hash of the session token the connection's `Hello` carried.
    session: TokenHash,
    /// The character it plays.
    character: i64,
    control: Arc<ConnControl>,
    permit: Arc<Mutex<Option<Permit>>>,
}

/// A connection's entry in [`Accounts`]; dropping it unregisters the
/// connection and frees its world slot.
pub struct Registration<'a> {
    accounts: &'a Accounts,
    account: i64,
    conn: u64,
    /// The world slot, once admitted (or inherited from a replaced connection).
    pub permit: Arc<Mutex<Option<Permit>>>,
}

impl Accounts {
    fn register(
        &self,
        account: i64,
        session: TokenHash,
        character: i64,
        control: Arc<ConnControl>,
    ) -> Registration<'_> {
        let conn = self.next_conn.fetch_add(1, Ordering::Relaxed);
        let permit = Arc::new(Mutex::new(None));
        let old = self.lock().insert(
            account,
            AccountConn {
                conn,
                session,
                character,
                control,
                permit: permit.clone(),
            },
        );
        if let Some(old) = old {
            old.control.kick(KickCode::Replaced);
            let inherited = old.permit.lock().expect("permit lock poisoned").take();
            *permit.lock().expect("permit lock poisoned") = inherited;
        }
        Registration {
            accounts: self,
            account,
            conn,
            permit,
        }
    }

    /// Kicks the account's connection, if it has one (and, with `session`,
    /// only if it signed in with that session). Returns whether it kicked.
    fn kick(&self, account: i64, session: Option<&TokenHash>, code: KickCode) -> bool {
        let map = self.lock();
        let Some(c) = map.get(&account) else {
            return false;
        };
        if session.is_some_and(|s| *s != c.session) {
            return false;
        }
        c.control.kick(code);
        true
    }

    /// Kicks the account's connection if it plays `character`.
    fn kick_character(&self, account: i64, character: i64, code: KickCode) -> bool {
        let map = self.lock();
        match map.get(&account) {
            Some(c) if c.character == character => {
                c.control.kick(code);
                true
            }
            _ => false,
        }
    }

    fn kick_all(&self, code: KickCode) {
        for c in self.lock().values() {
            c.control.kick(code);
        }
    }

    fn lock(&self) -> std::sync::MutexGuard<'_, HashMap<i64, AccountConn>> {
        self.map.lock().expect("accounts lock poisoned")
    }
}

impl Drop for Registration<'_> {
    fn drop(&mut self) {
        {
            let mut map = self.accounts.lock();
            if map.get(&self.account).is_some_and(|c| c.conn == self.conn) {
                map.remove(&self.account);
            }
        }
        let permit = self.permit.lock().expect("permit lock poisoned").take();
        drop(permit);
    }
}

#[derive(Debug, Clone)]
pub struct RealtimeConfig {
    /// `SHS_PLAYER_CAP`.
    pub player_cap: u32,
    /// Snapshot worker threads (defaults to the machine's cores).
    pub snapshot_threads: usize,
    /// The dorm room shells (`SHS_INSTANCES`).
    pub dorm_shells: crate::dorm::DormShells,
    /// Class rooms and arenas (`SHS_INSTANCES`).
    pub instance_layouts: layouts::InstanceLayouts,
    /// The moving staircases' period in seconds instead of
    /// `stairs.json`'s (`SHS_STAIRS_PERIOD_S`, development only).
    pub stairs_period_s: Option<u32>,
    /// How often a connection checks that its session is still live
    /// (`Config::session_recheck`).
    pub session_recheck: std::time::Duration,
}

impl RealtimeConfig {
    /// Built-in dorm shells; see [`RealtimeConfig::with_instances_dir`].
    pub fn new(player_cap: u32) -> Self {
        Self {
            player_cap,
            snapshot_threads: std::thread::available_parallelism().map_or(2, |n| n.get()),
            dorm_shells: crate::dorm::DormShells::embedded(),
            instance_layouts: layouts::InstanceLayouts::embedded(),
            stairs_period_s: None,
            session_recheck: crate::Config::default().session_recheck,
        }
    }

    /// The moving staircases switch every `period_s` seconds
    /// (development only: the caller passes `SHS_STAIRS_PERIOD_S` only with
    /// `SHS_DEV=1`).
    pub fn with_stairs_period(mut self, period_s: Option<u32>) -> Self {
        self.stairs_period_s = period_s;
        self
    }

    /// A connection checks its session every `every`.
    pub fn with_session_recheck(mut self, every: std::time::Duration) -> Self {
        self.session_recheck = every;
        self
    }

    /// Loads the dorm shells from `dir`; a directory that does not exist
    /// keeps the built-in ones (logged), a broken shell file is logged and
    /// also falls back.
    pub fn with_instances_dir(mut self, dir: &Path) -> Self {
        match crate::dorm::DormShells::load(dir) {
            Ok(shells) => self.dorm_shells = shells,
            Err(e) => {
                tracing::error!(error = %e, "dorm shells did not load; using the built-in ones")
            }
        }
        match layouts::InstanceLayouts::load(dir) {
            Ok(l) => self.instance_layouts = l,
            Err(e) => tracing::error!(
                error = %e,
                "class and arena layouts did not load; using the built-in ones"
            ),
        }
        self
    }
}

/// The running realtime side: world thread, saver task, queue and sessions.
pub struct Realtime {
    pub world: WorldHandle,
    /// The game clock (`Pong`, dev skew), shared with the world.
    pub clock: Arc<crate::clock::ServerClock>,
    pub admission: Arc<Admission>,
    pub metrics: Arc<RealtimeMetrics>,
    /// Who is online, by name (`GET /api/players`).
    pub presence: presence::Presence,
    pool: PgPool,
    accounts: Accounts,
    /// `RealtimeConfig::session_recheck`.
    session_recheck: std::time::Duration,
    closing: AtomicBool,
    world_thread: Mutex<Option<std::thread::JoinHandle<()>>>,
    saver: Mutex<Option<JoinHandle<()>>>,
    /// The duels still running when the world stopped.
    unfinished_duels: crate::dueling::world::Unfinished,
}

impl Realtime {
    /// Starts the world for `state` (its pool, player cap and metrics
    /// registry). Must be called inside the Tokio runtime.
    pub fn start(state: &AppState, world: sim_core::World, filter: WordFilter) -> Arc<Self> {
        Self::start_with(
            state.pool.clone(),
            &state.metrics.registry,
            RealtimeConfig::new(state.config.player_cap)
                .with_instances_dir(&state.config.instances_dir)
                .with_stairs_period(state.config.stairs_period_s.filter(|_| state.config.dev))
                .with_session_recheck(state.config.session_recheck),
            world,
            filter,
        )
    }

    pub fn start_with(
        pool: PgPool,
        registry: &Registry,
        config: RealtimeConfig,
        world: sim_core::World,
        filter: WordFilter,
    ) -> Arc<Self> {
        let metrics = Arc::new(RealtimeMetrics::register(registry));
        let (saver_tx, saver) = persist::spawn(pool.clone(), metrics.clone());
        let mut sim = WorldSim::new(
            Arc::new(world),
            filter,
            Some(saver_tx),
            metrics.clone(),
            config.snapshot_threads,
        );
        sim.set_dorm_shells(config.dorm_shells);
        sim.set_instance_layouts(config.instance_layouts);
        if let Some(p) = config.stairs_period_s {
            match sim.set_stairs_period(p) {
                Ok(()) => tracing::info!(period_s = p, "moving staircases on a development period"),
                Err(e) => {
                    tracing::error!(error = %e, "SHS_STAIRS_PERIOD_S refused; the shipped period stays")
                }
            }
        }
        let unfinished_duels = crate::dueling::world::Unfinished::default();
        sim.duels.keep_unfinished_in(unfinished_duels.clone());
        let clock = sim.clock().clone();
        let (handle, thread) = WorldHandle::spawn(sim, Some(pool.clone()));
        Arc::new(Self {
            world: handle,
            clock,
            admission: Admission::new(config.player_cap, metrics.clone()),
            metrics,
            presence: presence::Presence::default(),
            pool,
            accounts: Accounts::default(),
            session_recheck: config.session_recheck,
            closing: AtomicBool::new(false),
            world_thread: Mutex::new(Some(thread)),
            saver: Mutex::new(Some(saver)),
            unfinished_duels,
        })
    }

    pub fn is_closing(&self) -> bool {
        self.closing.load(Ordering::Acquire)
    }

    /// Disconnects everyone (saving positions), stops the world and waits for
    /// the last positions to be written; then records the duels the stop cut
    /// short as no contest (`dueling::record_no_contests`), before
    /// the caller lets the pool go. Later calls return immediately.
    pub async fn shutdown(&self) {
        if self.closing.swap(true, Ordering::AcqRel) {
            return;
        }
        tracing::info!("stopping the world");
        self.world.shutdown().await;
        self.accounts.kick_all(KickCode::ServerRestart);
        let thread = self.world_thread.lock().expect("lock").take();
        if let Some(t) = thread {
            let _ = tokio::task::spawn_blocking(move || t.join()).await;
        }
        let saver = self.saver.lock().expect("lock").take();
        if let Some(s) = saver {
            let _ = s.await;
        }
        // The world thread has ended, so its duels were dropped and left here.
        let unfinished = self.unfinished_duels.take();
        if !unfinished.is_empty() {
            let written = crate::dueling::record_no_contests(&self.pool, &unfinished).await;
            tracing::info!(
                duels = unfinished.len(),
                written,
                "recorded the duels cut short as no contest"
            );
        }
        tracing::info!("world stopped, positions saved");
    }

    pub(crate) fn pool(&self) -> &PgPool {
        &self.pool
    }

    pub(crate) fn register(
        &self,
        account: i64,
        session: TokenHash,
        character: i64,
        control: Arc<ConnControl>,
    ) -> Registration<'_> {
        self.accounts.register(account, session, character, control)
    }

    pub(crate) fn session_recheck(&self) -> std::time::Duration {
        self.session_recheck
    }

    /// Ends the account's live connection, if any, with `code` (a password
    /// reset ended every session: `KickCode::SessionExpired`). Returns
    /// whether there was one.
    pub fn kick_account(&self, account: i64, code: KickCode) -> bool {
        self.accounts.kick(account, None, code)
    }

    /// Ends the account's live connection only if it signed in with the
    /// session `session` (a token hash): signing out on one device leaves
    /// another device's connection alone. Returns whether it kicked.
    pub fn kick_session(&self, account: i64, session: &TokenHash, code: KickCode) -> bool {
        self.accounts.kick(account, Some(session), code)
    }

    /// Ends the account's live connection only if it plays `character`
    /// (whether or not it has reached the world yet): the character was
    /// deleted. Returns whether it kicked.
    pub fn kick_character(&self, account: i64, character: i64, code: KickCode) -> bool {
        self.accounts.kick_character(account, character, code)
    }
}

/// Reads and validates the layout file (`SHS_LAYOUT`).
pub fn load_world(path: &Path) -> Result<sim_core::World, String> {
    let json = std::fs::read_to_string(path)
        .map_err(|e| format!("cannot read layout {}: {e}", path.display()))?;
    // The whole castle file, both positions of every moving
    // staircase flight (`WorldSim::new` walks them by the schedule).
    let world =
        sim_core::World::from_json(&json).map_err(|e| format!("layout {}: {e}", path.display()))?;
    // Every find spot of the layout has its loot (and back).
    let content = crate::catalog::shipped();
    // The secrets fit the castle (their surfaces, the Wishing
    // Room's corridor, marks and door), and its rooms are
    // the instance layouts the server loads.
    let secrets = sim_core::Secrets::new(content.secrets.clone(), world.layout())
        .map_err(|e| format!("layout {} and data/world/secrets.json: {e}", path.display()))?;
    let rooms: Vec<&str> = secrets
        .wishing_room()
        .rooms
        .iter()
        .map(|r| r.layout.as_str())
        .collect();
    if rooms != layouts::WISHING_ROOM_LAYOUTS {
        return Err(format!(
            "data/world/secrets.json: the rooms' layouts are {:?}",
            layouts::WISHING_ROOM_LAYOUTS
        ));
    }
    // The moving staircases fit the layout (data/world/stairs.json).
    if !world.layout().flights().is_empty() {
        sim_core::Stairs::with_layout(content.stairs.clone(), world.layout())
            .map_err(|e| format!("layout {} and data/world/stairs.json: {e}", path.display()))?;
    }
    // And the Lost Property Vault's find spot in its room.
    let rooms = layouts::InstanceLayouts::embedded();
    let hidden = rooms
        .get(layouts::LOST_PROPERTY_LAYOUT)
        .ok_or("no Lost Property Vault layout")?;
    content
        .finds
        .validate(&[world.layout(), hidden.layout()], &content.loot)
        .map_err(|e| format!("layout {} and data/world/finds.json: {e}", path.display()))?;
    // Every class door is a castle `class_door`, every venue's
    // dueling area a castle area of that kind.
    for class in &content.schedule.classes {
        let door = world.interactable(&class.door);
        if door.is_none_or(|d| d.kind != sim_core::InteractableKind::ClassDoor) {
            return Err(format!(
                "layout {}: class {} needs a class_door {:?}",
                path.display(),
                class.id,
                class.door
            ));
        }
    }
    content
        .venues
        .validate_with(world.layout(), |id| {
            layouts::InstanceLayouts::embedded()
                .get(id)
                .and_then(|w| sim_core::World::new(w.layout().clone()).ok())
        })
        .map_err(|e| {
            format!(
                "layout {} and data/dueling/venues.json: {e}",
                path.display()
            )
        })?;
    Ok(world)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn kicks_are_not_lost_and_the_first_reason_wins() {
        let c = ConnControl::default();
        c.kick(KickCode::Timeout);
        c.kick(KickCode::Flood);
        assert_eq!(c.kicked().await, KickCode::Timeout);
        assert_eq!(c.kicked().await, KickCode::Timeout, "stays kicked");
    }

    #[test]
    fn a_new_login_kicks_the_old_and_inherits_its_slot() {
        let reg = Registry::default();
        let metrics = Arc::new(RealtimeMetrics::register(&reg));
        let admission = Admission::new(1, metrics);
        let accounts = Accounts::default();

        let old_control = Arc::new(ConnControl::default());
        let old = accounts.register(7, [1; 32], 70, old_control.clone());
        let admission::Entry::Admitted(p) = admission.enter() else {
            panic!("slot free")
        };
        *old.permit.lock().unwrap() = Some(p);

        let new = accounts.register(7, [2; 32], 71, Arc::new(ConnControl::default()));
        assert_eq!(old_control.reason(), Some(KickCode::Replaced));
        assert!(new.permit.lock().unwrap().is_some(), "slot handed over");
        drop(old);
        assert_eq!(
            admission.active(),
            1,
            "the old connection ending frees nothing"
        );
        assert_eq!(accounts.lock().len(), 1);
        drop(new);
        assert_eq!(admission.active(), 0);
        assert!(accounts.lock().is_empty());
    }

    #[test]
    fn kicks_by_account_and_by_session() {
        let accounts = Accounts::default();
        let a = Arc::new(ConnControl::default());
        let _reg_a = accounts.register(1, [1; 32], 10, a.clone());
        let b = Arc::new(ConnControl::default());
        let _reg_b = accounts.register(2, [2; 32], 20, b.clone());

        // Another session of the account (another device signing out): no kick.
        assert!(!accounts.kick(1, Some(&[9; 32]), KickCode::SessionExpired));
        assert_eq!(a.reason(), None);
        // No connection for the account: nothing to do.
        assert!(!accounts.kick(3, None, KickCode::SessionExpired));
        // The connection's own session.
        assert!(accounts.kick(1, Some(&[1; 32]), KickCode::SessionExpired));
        assert_eq!(a.reason(), Some(KickCode::SessionExpired));
        // Every session of the account (a password reset).
        assert!(accounts.kick(2, None, KickCode::SessionExpired));
        assert_eq!(b.reason(), Some(KickCode::SessionExpired));
    }

    #[test]
    fn kicks_by_character() {
        let accounts = Accounts::default();
        let a = Arc::new(ConnControl::default());
        let _reg = accounts.register(1, [1; 32], 10, a.clone());
        // Another character of the account was deleted: this one plays on.
        assert!(!accounts.kick_character(1, 11, KickCode::NoCharacter));
        assert!(!accounts.kick_character(2, 10, KickCode::NoCharacter));
        assert_eq!(a.reason(), None);
        assert!(accounts.kick_character(1, 10, KickCode::NoCharacter));
        assert_eq!(a.reason(), Some(KickCode::NoCharacter));
    }

    /// The world loads every moving flight's two positions and the
    /// simulation walks the castle at rest in A, with the schedule at hand.
    #[test]
    fn the_staircases_load_with_the_castle() {
        let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../data/world/layout.json");
        let world = load_world(&path).unwrap();
        assert_eq!(world.layout().flights().len(), 3);
        let sim = WorldSim::new(
            std::sync::Arc::new(world),
            WordFilter::default(),
            None,
            Arc::new(RealtimeMetrics::register(&Registry::default())),
            1,
        );
        assert_eq!(sim.stairs().map(|s| s.flights().len()), Some(3));
        assert_eq!(sim.castle_all().layout().flights().len(), 3);
        let b = |w: &sim_core::World| {
            w.surfaces()
                .iter()
                .filter(|s| {
                    s.moving
                        .as_ref()
                        .is_some_and(|m| m.position == sim_core::StairPosition::B)
                })
                .count()
        };
        assert_eq!(b(sim.castle_all()), 3);
        assert_eq!(b(&sim.castle), 0);
    }

    #[test]
    fn the_shipped_layout_loads() {
        let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../data/world/layout.json");
        let world = load_world(&path).unwrap();
        // Every spawn is on the ground.
        for s in world.spawns() {
            assert!(
                world.ground_at(s.pos[0], s.pos[2], s.pos[1]).is_some(),
                "{}",
                s.id
            );
        }
    }
}
