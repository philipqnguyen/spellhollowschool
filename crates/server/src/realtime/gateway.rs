//! `GET /ws`: one task per WebSocket connection.
//!
//! 1. **Hello.** The first frame must be `ClientMsg::Hello` within
//!    [`HELLO_TIMEOUT`], with our `PROTOCOL_VERSION` and a live session token
//!    that has picked a character (`sessions.character_id`): the connection
//!    plays that one. A `Hello` naming another character than the session's
//!    gets `Replaced` (another window picked another character meanwhile),
//!    and an older client's `Hello` `VersionMismatch`. Anything else gets
//!    `Kick` and a close.
//!    A character made before starter wardrobes gets its starter wardrobe here.
//! 2. **One connection per account.** The character's friends, blocks and
//!    dorm setting are loaded, then registering kicks any older connection of
//!    the same account and inherits its world slot (the new one takes over
//!    where the old one stands, in the same zone, when it is the same
//!    character; another character of the account starts where it resumes). From then on the session
//!    is watched: checked once right away and then every
//!    `Config::session_recheck`, the connection is kicked with
//!    `SessionExpired` once its session has expired or been deleted, and
//!    with `NoCharacter` once the session no longer plays its character
//!    (deleting a character also kicks the connection playing it at once). Signing
//!    out and password resets kick at once (`Realtime::kick_session`,
//!    `Realtime::kick_account`).
//! 3. **Queue.** Without a free slot the client waits in the login queue,
//!    receiving `Queued { position }` whenever its place changes.
//! 4. **Play.** The world sends `Welcome`, then snapshots, chat and rosters
//!    through a bounded outbound queue ([`OUTBOUND_QUEUE`] frames); the world
//!    disconnects a client whose queue fills up rather than buffering for it.
//!    Inbound frames are decoded here and forwarded to the world (moves,
//!    chat, emotes, doors, dorm actions); `Ping` is answered here.
//!
//! Throughout, a WebSocket ping goes out every [`HEARTBEAT`]; a connection that
//! sends nothing for [`IDLE_TIMEOUT`] is closed. Inbound messages are limited
//! to a burst of [`INBOUND_BURST`] and [`INBOUND_PER_SECOND`] after that.

use std::sync::Arc;
use std::time::{Duration, Instant};

use axum::extract::State;
use axum::extract::ws::{CloseFrame, Message, WebSocket, WebSocketUpgrade, close_code};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use bytes::Bytes;
use futures_util::SinkExt;
use protocol::{
    ClientMsg, EntityId, KickCode, PROTOCOL_VERSION, ServerMsg, decode_client, encode_server,
};
use tokio::sync::mpsc;
use tokio::time::{MissedTickBehavior, interval_at, timeout};

use super::admission::{Entry, Permit, QueueState, Ticket};
use super::jobs;
use super::metrics::RealtimeMetrics;
use super::world::{Input, Joining};
use super::*;
use crate::AppState;
use crate::characters::Character;
use crate::http::ApiError;

pub const HELLO_TIMEOUT: Duration = Duration::from_secs(10);
pub const HEARTBEAT: Duration = Duration::from_secs(15);
pub const IDLE_TIMEOUT: Duration = Duration::from_secs(45);
/// A single write that takes longer than this means the peer is gone.
pub const SEND_TIMEOUT: Duration = Duration::from_secs(10);
/// Outbound frames buffered per client: 6.4 s of snapshots.
pub const OUTBOUND_QUEUE: usize = 64;
/// Largest inbound message (a full chat line plus framing fits easily).
pub const MAX_INBOUND_BYTES: usize = 2048;
pub const INBOUND_BURST: f32 = 60.0;
pub const INBOUND_PER_SECOND: f32 = 30.0;
/// Frames written per flush when several are queued.
const MAX_BATCH: usize = 16;

/// `GET /ws`
pub async fn ws_handler(State(state): State<AppState>, ws: WebSocketUpgrade) -> Response {
    let Some(rt) = state.realtime.clone() else {
        return ApiError::new(
            StatusCode::SERVICE_UNAVAILABLE,
            "unavailable",
            "The game world is not running.",
        )
        .into_response();
    };
    ws.max_message_size(MAX_INBOUND_BYTES)
        .max_frame_size(MAX_INBOUND_BYTES)
        .on_upgrade(move |socket| connection(socket, rt))
}

async fn connection(socket: WebSocket, rt: Arc<Realtime>) {
    rt.metrics.connections.inc();
    let mut conn = Conn::new(socket, rt.metrics.clone(), rt.clock.clone());
    serve(&mut conn, &rt).await;
    rt.metrics.connections.dec();
}

/// What one inbound frame amounted to.
enum Recv {
    Msg(ClientMsg),
    /// Control frames (ping/pong).
    Nothing,
    Closed,
    Bad,
    /// A `Hello` of another protocol version that does not decode.
    Outdated,
    Flood,
}

struct Conn {
    socket: WebSocket,
    metrics: Arc<RealtimeMetrics>,
    /// The game clock (`Pong.server_time_ms`), shared with the world.
    clock: Arc<crate::clock::ServerClock>,
    last_inbound: Instant,
    tokens: f32,
    tokens_at: Instant,
}

impl Conn {
    fn new(
        socket: WebSocket,
        metrics: Arc<RealtimeMetrics>,
        clock: Arc<crate::clock::ServerClock>,
    ) -> Self {
        let now = Instant::now();
        Self {
            socket,
            metrics,
            clock,
            last_inbound: now,
            tokens: INBOUND_BURST,
            tokens_at: now,
        }
    }

    /// Writes frames and flushes once. False if the peer is gone or stalled.
    async fn send_all(&mut self, frames: impl IntoIterator<Item = Bytes>) -> bool {
        let socket = &mut self.socket;
        let metrics = &self.metrics;
        let write = async {
            for f in frames {
                metrics.ws_bytes_sent.add(f.len() as u64);
                metrics.ws_messages_sent.inc();
                socket.feed(Message::Binary(f)).await?;
            }
            socket.flush().await
        };
        matches!(timeout(SEND_TIMEOUT, write).await, Ok(Ok(())))
    }

    async fn send(&mut self, frame: Bytes) -> bool {
        self.send_all([frame]).await
    }

    async fn send_msg(&mut self, msg: &ServerMsg) -> bool {
        self.send(encode_server(msg).into()).await
    }

    /// Sends `Kick` and closes.
    async fn kick(&mut self, code: KickCode) {
        tracing::debug!(?code, "kicking connection");
        let kick = ServerMsg::Kick {
            code,
            reason: kick_reason(code).to_owned(),
        };
        if self.send_msg(&kick).await {
            let close = Message::Close(Some(CloseFrame {
                code: close_code::NORMAL,
                reason: "".into(),
            }));
            let _ = timeout(Duration::from_secs(1), self.socket.send(close)).await;
        }
    }

    fn on_frame(&mut self, item: Option<Result<Message, axum::Error>>) -> Recv {
        let msg = match item {
            Some(Ok(m)) => m,
            None | Some(Err(_)) => return Recv::Closed,
        };
        let now = Instant::now();
        self.last_inbound = now;
        // Every frame counts, control frames included: a WebSocket ping flood
        // costs a pong each.
        let earned = now.duration_since(self.tokens_at).as_secs_f32();
        self.tokens_at = now;
        self.tokens = (self.tokens + earned * INBOUND_PER_SECOND).min(INBOUND_BURST);
        if self.tokens < 1.0 {
            return Recv::Flood;
        }
        self.tokens -= 1.0;
        match msg {
            Message::Binary(b) => {
                self.metrics.ws_bytes_received.add(b.len() as u64);
                self.metrics.ws_messages_received.inc();
                match decode_client(&b) {
                    Ok(m) => Recv::Msg(m),
                    Err(_) => match protocol::hello_version(&b) {
                        Some(v) if v != PROTOCOL_VERSION => Recv::Outdated,
                        _ => Recv::Bad,
                    },
                }
            }
            Message::Close(_) => Recv::Closed,
            Message::Ping(_) | Message::Pong(_) => Recv::Nothing,
            Message::Text(_) => Recv::Bad,
        }
    }

    /// Waits for the next message, skipping control frames.
    async fn next_msg(&mut self) -> Recv {
        loop {
            let item = self.socket.recv().await;
            match self.on_frame(item) {
                Recv::Nothing => continue,
                other => return other,
            }
        }
    }

    /// Heartbeat tick: closes idle connections, pings live ones.
    async fn heartbeat(&mut self) -> bool {
        if self.last_inbound.elapsed() > IDLE_TIMEOUT {
            self.kick(KickCode::Timeout).await;
            return false;
        }
        let ping = timeout(SEND_TIMEOUT, self.socket.send(Message::Ping(Bytes::new())));
        matches!(ping.await, Ok(Ok(())))
    }

    async fn pong(&mut self, client_time_ms: u32) -> bool {
        let server_time_ms = u64::try_from(self.clock.now_unix_ms()).unwrap_or(0);
        self.send_msg(&ServerMsg::Pong {
            client_time_ms,
            server_time_ms,
        })
        .await
    }
}

async fn serve(conn: &mut Conn, rt: &Arc<Realtime>) {
    if rt.is_closing() {
        conn.kick(KickCode::ServerRestart).await;
        return;
    }
    let (token, expected) = match timeout(HELLO_TIMEOUT, conn.next_msg()).await {
        Ok(Recv::Msg(ClientMsg::Hello {
            protocol_version,
            token,
            character_id,
        })) => {
            if protocol_version != PROTOCOL_VERSION {
                conn.kick(KickCode::VersionMismatch).await;
                return;
            }
            (token, character_id)
        }
        Ok(Recv::Outdated) => {
            conn.kick(KickCode::VersionMismatch).await;
            return;
        }
        Ok(Recv::Closed) => return,
        Err(_) => {
            conn.kick(KickCode::Timeout).await;
            return;
        }
        Ok(Recv::Flood) => {
            conn.kick(KickCode::Flood).await;
            return;
        }
        Ok(_) => {
            conn.kick(KickCode::ProtocolError).await;
            return;
        }
    };
    let Some(session) = crate::validate_session(rt.pool(), &token).await else {
        conn.kick(KickCode::SessionExpired).await;
        return;
    };
    let Some(character) = session.character else {
        conn.kick(KickCode::NoCharacter).await;
        return;
    };
    // The client was built for another character than the session now
    // plays (another window picked one while this one was reconnecting).
    if expected.is_some_and(|id| id != character.id) {
        conn.kick(KickCode::Replaced).await;
        return;
    }
    // A character made before starter wardrobes gets its starter wardrobe now if
    // no HTTP request has granted it yet, so it never enters the castle
    // undressed (the grant is idempotent and safe to race with HTTP).
    let character = if session.starter_granted {
        character
    } else {
        match crate::inventory::starter::grant_once(rt.pool(), Some(&rt.world), character).await {
            Ok(c) => c,
            Err(_) => {
                // Logged by `ApiError`; temporary, so the client retries.
                conn.kick(KickCode::ServerRestart).await;
                return;
            }
        }
    };
    // Nothing worn the body may not wear (a no-op unless the
    // stored look breaks the rule).
    let character =
        match crate::inventory::starter::take_off_unwearable(rt.pool(), Some(&rt.world), character)
            .await
        {
            Ok(c) => c,
            Err(_) => {
                conn.kick(KickCode::ServerRestart).await;
                return;
            }
        };

    // Friends, blocks and the dorm's open flag, so they apply from the first
    // tick (chat blocking, priority players, visits). Loaded before
    // registering: registering kicks an older connection of this account,
    // and the join must reach the world before that connection's leave so
    // the new one takes over where the old one stands.
    let character_id = character.id;
    let Some(mut joining) = load_joining(conn, rt, character).await else {
        return;
    };

    let control = Arc::new(ConnControl::default());
    let session_hash = crate::auth::token::hash(&token);
    let registration = rt.register(
        session.account_id,
        session_hash,
        character_id,
        control.clone(),
    );
    // After registering: a session ended, or a character deleted, since
    // `Hello` (whose kick found no connection yet) is caught by the first
    // check; a deletion after it kicks this registration.
    let _watch = AbortOnDrop(tokio::spawn(watch_session(
        rt.pool().clone(),
        session_hash,
        character_id,
        control.clone(),
        rt.session_recheck(),
    )));
    let inherited = registration.permit.lock().expect("permit lock").is_some();
    if !inherited {
        let permit = match rt.admission.enter() {
            Entry::Admitted(p) => p,
            Entry::Queued(ticket) => match wait_in_queue(conn, &control, ticket).await {
                Some(p) => {
                    // The wait may have been long: load them again (friends,
                    // and the character, whose look may have changed over HTTP).
                    let character = match crate::characters::find(rt.pool(), character_id).await {
                        Ok(Some(c)) => c,
                        Ok(None) => {
                            conn.kick(KickCode::NoCharacter).await;
                            return;
                        }
                        Err(e) => {
                            tracing::error!(error = %e, "reloading a queued character failed");
                            crate::monitoring::capture_for_character(&e, character_id);
                            conn.kick(KickCode::ServerRestart).await;
                            return;
                        }
                    };
                    joining = match load_joining(conn, rt, character).await {
                        Some(j) => j,
                        None => return,
                    };
                    p
                }
                None => return,
            },
        };
        *registration.permit.lock().expect("permit lock") = Some(permit);
    }

    let (out_tx, mut out_rx) = mpsc::channel(OUTBOUND_QUEUE);
    let name = joining.character.name.clone();
    let house = joining.character.house;
    let Some(id) = rt.world.join(joining, out_tx, control.clone()).await else {
        let reason = if rt.is_closing() {
            KickCode::ServerRestart
        } else {
            KickCode::ServerFull
        };
        conn.kick(reason).await;
        return;
    };
    tracing::debug!(%name, id, "player joined");
    // Listed as online (`GET /api/players`) while in the world.
    let present = rt.presence.enter(&name, house);
    play(conn, rt, &control, id, &mut out_rx).await;
    rt.world.leave(id).await;
    drop(present);
    tracing::debug!(%name, id, "player left");
    // Dropping the registration afterwards frees the slot only once the world
    // has removed the character, so the cap is never exceeded.
    drop(registration);
}

/// Aborts the task when dropped (the connection ended).
struct AbortOnDrop(tokio::task::JoinHandle<()>);

impl Drop for AbortOnDrop {
    fn drop(&mut self) {
        self.0.abort();
    }
}

/// Checks the connection's session now and then every `every`: kicks with
/// `SessionExpired` once it has expired or been deleted, and with
/// `NoCharacter` once it no longer plays `character` (deleted, or another
/// picked). A database error keeps the connection (logged; the next check
/// tries again).
async fn watch_session(
    pool: sqlx::PgPool,
    session: crate::auth::token::TokenHash,
    character: i64,
    control: Arc<ConnControl>,
    every: Duration,
) {
    use crate::auth::session::{SessionNow, session_playing};
    let mut check = tokio::time::interval(every.max(Duration::from_millis(10)));
    check.set_missed_tick_behavior(MissedTickBehavior::Delay);
    loop {
        check.tick().await;
        match session_playing(&pool, &session).await {
            Some(SessionNow::Ended) => {
                control.kick(KickCode::SessionExpired);
                return;
            }
            Some(SessionNow::Plays(plays)) if plays != Some(character) => {
                control.kick(KickCode::NoCharacter);
                return;
            }
            _ => {}
        }
    }
}

/// A character with its friends, blocks and dorm flag; on a database error
/// the connection is kicked (retryable) and `None` returned.
async fn load_joining(conn: &mut Conn, rt: &Realtime, character: Character) -> Option<Joining> {
    let loaded = tokio::try_join!(
        jobs::load_social(rt.pool(), character.id),
        crate::dorm::store::open_to_friends(rt.pool(), character.id),
        crate::wallet::hud(rt.pool(), character.id),
        crate::effects::load_active(rt.pool(), character.id),
        crate::progression::load_school(rt.pool(), character.id),
        crate::progression::load_flying_xp(rt.pool(), character.id),
        crate::map::load(rt.pool(), character.id),
        crate::clubs::store::load_of(rt.pool(), character.id),
    );
    match loaded {
        Ok((social, dorm_open, hud, effects, school, flying_xp, explored, club)) => Some(Joining {
            character,
            social,
            dorm_open,
            crowns: hud.crowns,
            unread_mail: hud.unread_mail,
            effects,
            school,
            flying_xp,
            explored,
            club,
        }),
        Err(e) => {
            tracing::error!(error = %e, "loading friends for a join failed");
            crate::monitoring::capture_for_character(&e, character.id);
            conn.kick(KickCode::ServerRestart).await;
            None
        }
    }
}

/// Waits for a world slot; `None` if the connection ends first.
async fn wait_in_queue(
    conn: &mut Conn,
    control: &ConnControl,
    mut ticket: Ticket,
) -> Option<Permit> {
    if let Some(position) = ticket.position()
        && !conn.send_msg(&ServerMsg::Queued { position }).await
    {
        return None;
    }
    let mut heartbeat = interval_at(tokio::time::Instant::now() + HEARTBEAT, HEARTBEAT);
    heartbeat.set_missed_tick_behavior(MissedTickBehavior::Delay);
    loop {
        tokio::select! {
            biased;
            reason = control.kicked() => {
                conn.kick(reason).await;
                return None;
            }
            changed = ticket.state.changed() => {
                let state = *ticket.state.borrow_and_update();
                match state {
                    QueueState::Admitted => return ticket.into_permit(),
                    QueueState::Waiting(position) => {
                        if changed.is_err() || !conn.send_msg(&ServerMsg::Queued { position }).await {
                            return None;
                        }
                    }
                }
            }
            item = conn.socket.recv() => match conn.on_frame(item) {
                Recv::Closed => return None,
                Recv::Flood => {
                    conn.kick(KickCode::Flood).await;
                    return None;
                }
                Recv::Msg(ClientMsg::Ping { client_time_ms }) => {
                    if !conn.pong(client_time_ms).await {
                        return None;
                    }
                }
                // Moves or chat sent while queued mean nothing yet.
                Recv::Msg(_) | Recv::Nothing => {}
                Recv::Bad | Recv::Outdated => {
                    conn.kick(KickCode::ProtocolError).await;
                    return None;
                }
            },
            _ = heartbeat.tick() => {
                if !conn.heartbeat().await {
                    return None;
                }
            }
        }
    }
}

async fn play(
    conn: &mut Conn,
    rt: &Realtime,
    control: &ConnControl,
    id: EntityId,
    out_rx: &mut mpsc::Receiver<Bytes>,
) {
    let mut heartbeat = interval_at(tokio::time::Instant::now() + HEARTBEAT, HEARTBEAT);
    heartbeat.set_missed_tick_behavior(MissedTickBehavior::Delay);
    let mut batch: Vec<Bytes> = Vec::with_capacity(MAX_BATCH);
    loop {
        tokio::select! {
            biased;
            reason = control.kicked() => {
                conn.kick(reason).await;
                return;
            }
            frame = out_rx.recv() => {
                let Some(frame) = frame else {
                    // The world dropped us (shutdown); the kick reason, if any,
                    // is handled above on the next turn.
                    if let Some(reason) = control.reason() {
                        conn.kick(reason).await;
                    }
                    return;
                };
                batch.push(frame);
                while batch.len() < MAX_BATCH {
                    match out_rx.try_recv() {
                        Ok(f) => batch.push(f),
                        Err(_) => break,
                    }
                }
                if !conn.send_all(batch.drain(..)).await {
                    return;
                }
            }
            item = conn.socket.recv() => match conn.on_frame(item) {
                Recv::Closed => return,
                Recv::Flood => {
                    conn.kick(KickCode::Flood).await;
                    return;
                }
                Recv::Bad | Recv::Outdated => {
                    conn.kick(KickCode::ProtocolError).await;
                    return;
                }
                Recv::Nothing => {}
                Recv::Msg(msg) => {
                    if let ClientMsg::Ping { client_time_ms } = msg {
                        if !conn.pong(client_time_ms).await {
                            return;
                        }
                        continue;
                    }
                    // Everything else is the world's (a second Hello is ignored).
                    let Some(input) = Input::from_client(msg) else {
                        continue;
                    };
                    if !rt.world.input(id, input) {
                        conn.kick(KickCode::ServerRestart).await;
                        return;
                    }
                }
            },
            _ = heartbeat.tick() => {
                if !conn.heartbeat().await {
                    return;
                }
            }
        }
    }
}
