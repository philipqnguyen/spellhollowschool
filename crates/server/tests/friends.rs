//! Friends, friend requests and blocks over HTTP, plus one end-to-end test
//! with a running world that checks the pushed `Friend` and
//! `AppearanceChanged` messages and the online status in the friend list.

mod common;

use std::net::SocketAddr;
use std::sync::Arc;
use std::time::Duration;

use axum::http::StatusCode;
use common::{TestApp, TestPlayer, unique_name};
use futures_util::{SinkExt, StreamExt};
use protocol::{
    ClientMsg, FRIENDS_MAX, FriendEvent, PROTOCOL_VERSION, ServerMsg, decode_server, encode_client,
};
use serde_json::{Value, json};
use server::realtime::chat::WordFilter;
use server::{AppState, Config, db, email, http};
use tokio::net::TcpStream;
use tokio_tungstenite::tungstenite::Message;
use tokio_tungstenite::{MaybeTlsStream, WebSocketStream};

async fn request(app: &TestApp, from: &TestPlayer, to_name: &str) -> common::Response {
    app.post(
        "/api/friends/requests",
        json!({ "name": to_name }),
        from.token(),
    )
    .await
}

/// (request id, other party) of a player's `incoming` / `outgoing` requests;
/// `""` leaves the direction to its default.
async fn requests(app: &TestApp, p: &TestPlayer, direction: &str) -> Vec<(i64, i64)> {
    let path = match direction {
        "" => "/api/friends/requests".to_owned(),
        d => format!("/api/friends/requests?direction={d}"),
    };
    let res = app.get(&path, p.token()).await;
    assert_eq!(res.status, StatusCode::OK, "{:?}", res.body);
    res.body["items"]
        .as_array()
        .unwrap()
        .iter()
        .map(|r| {
            (
                r["id"].as_i64().unwrap(),
                r["character_id"].as_i64().unwrap(),
            )
        })
        .collect()
}

async fn friends(app: &TestApp, p: &TestPlayer) -> Vec<Value> {
    let res = app.get("/api/friends?limit=100", p.token()).await;
    assert_eq!(res.status, StatusCode::OK, "{:?}", res.body);
    res.body["items"].as_array().unwrap().clone()
}

async fn friend_ids(app: &TestApp, p: &TestPlayer) -> Vec<i64> {
    friends(app, p)
        .await
        .iter()
        .map(|f| f["character_id"].as_i64().unwrap())
        .collect()
}

/// Makes `a` and `b` friends through the API.
async fn befriend(app: &TestApp, a: &TestPlayer, b: &TestPlayer) {
    let res = request(app, a, &b.name).await;
    assert_eq!(res.status, StatusCode::CREATED, "{:?}", res.body);
    let id = res.body["request"]["id"].as_i64().unwrap();
    let res = app
        .post(
            &format!("/api/friends/requests/{id}/accept"),
            json!({}),
            b.token(),
        )
        .await;
    assert_eq!(res.status, StatusCode::OK, "{:?}", res.body);
}

#[tokio::test]
async fn friend_endpoints_need_a_session() {
    let app = TestApp::new().await;
    for path in ["/api/friends", "/api/friends/requests", "/api/blocks"] {
        assert_eq!(
            app.get(path, None).await.status,
            StatusCode::UNAUTHORIZED,
            "{path}"
        );
    }
    let res = app
        .post("/api/friends/requests", json!({ "name": "Someone" }), None)
        .await;
    assert_eq!(res.status, StatusCode::UNAUTHORIZED);
    assert_eq!(
        app.delete("/api/friends/1", None).await.status,
        StatusCode::UNAUTHORIZED
    );
    let res = app
        .post("/api/blocks", json!({ "name": "Someone" }), Some("nope"))
        .await;
    assert_eq!(res.status, StatusCode::UNAUTHORIZED);
}

#[tokio::test]
async fn request_and_accept() {
    let app = TestApp::new().await;
    let ada = app.player("Roarwing").await;
    let bea = app.player("Serpentyne").await;

    // By name, ignoring case and surrounding spaces.
    let res = request(&app, &ada, &format!("  {} ", bea.name.to_uppercase())).await;
    assert_eq!(res.status, StatusCode::CREATED, "{:?}", res.body);
    let req = &res.body["request"];
    assert_eq!(req["character_id"], bea.id);
    assert_eq!(req["name"], bea.name);
    assert_eq!(req["house"], "Serpentyne");
    let id = req["id"].as_i64().unwrap();
    assert_eq!(requests(&app, &ada, "outgoing").await, vec![(id, bea.id)]);
    assert_eq!(requests(&app, &bea, "incoming").await, vec![(id, ada.id)]);
    assert_eq!(
        requests(&app, &bea, "").await.len(),
        1,
        "incoming is the default"
    );
    assert!(requests(&app, &ada, "incoming").await.is_empty());

    // Asking twice, asking yourself, asking no one.
    let res = request(&app, &ada, &bea.name).await;
    assert_eq!(
        (res.status, res.body["error"].clone()),
        (StatusCode::CONFLICT, json!("request_pending"))
    );
    let res = request(&app, &ada, &ada.name.to_lowercase()).await;
    assert_eq!(
        (res.status, res.body["error"].clone()),
        (StatusCode::BAD_REQUEST, json!("self"))
    );
    let res = request(&app, &ada, &unique_name()).await;
    assert_eq!(
        (res.status, res.body["error"].clone()),
        (StatusCode::NOT_FOUND, json!("character_not_found"))
    );

    // Only the recipient accepts.
    let res = app
        .post(
            &format!("/api/friends/requests/{id}/accept"),
            json!({}),
            ada.token(),
        )
        .await;
    assert_eq!(
        (res.status, res.body["error"].clone()),
        (StatusCode::NOT_FOUND, json!("request_not_found"))
    );
    let res = app
        .post(
            &format!("/api/friends/requests/{id}/accept"),
            json!({}),
            bea.token(),
        )
        .await;
    assert_eq!(res.status, StatusCode::OK, "{:?}", res.body);
    let friend = &res.body["friend"];
    assert_eq!(friend["character_id"], ada.id);
    assert_eq!(friend["name"], ada.name);
    assert_eq!(friend["house"], "Roarwing");
    assert_eq!(friend["online"], false);
    assert_eq!(friend["location"], Value::Null);
    assert!(friend["since"].is_string());

    assert_eq!(friend_ids(&app, &ada).await, vec![bea.id]);
    assert_eq!(friend_ids(&app, &bea).await, vec![ada.id]);
    assert!(requests(&app, &ada, "outgoing").await.is_empty());
    assert!(requests(&app, &bea, "incoming").await.is_empty());
    let res = app
        .post(
            &format!("/api/friends/requests/{id}/accept"),
            json!({}),
            bea.token(),
        )
        .await;
    assert_eq!(res.body["error"], "request_not_found");
    let res = request(&app, &bea, &ada.name).await;
    assert_eq!(
        (res.status, res.body["error"].clone()),
        (StatusCode::CONFLICT, json!("already_friends"))
    );

    // The world-facing helper agrees.
    let ids = server::friends::friend_ids(&app.state.pool, ada.id)
        .await
        .unwrap();
    assert_eq!(ids, vec![bea.id]);
}

#[tokio::test]
async fn asking_back_accepts_at_once() {
    let app = TestApp::new().await;
    let ada = app.player("Burrowsby").await;
    let bea = app.player("Skytalon").await;
    assert_eq!(
        request(&app, &ada, &bea.name).await.status,
        StatusCode::CREATED
    );
    let res = request(&app, &bea, &ada.name).await;
    assert_eq!(res.status, StatusCode::OK, "{:?}", res.body);
    assert_eq!(res.body["friend"]["character_id"], ada.id);
    assert_eq!(friend_ids(&app, &ada).await, vec![bea.id]);
    assert!(requests(&app, &ada, "outgoing").await.is_empty());
}

#[tokio::test]
async fn crossing_requests_end_as_one_friendship() {
    let app = TestApp::new().await;
    let ada = app.player("Burrowsby").await;
    let bea = app.player("Skytalon").await;
    let (a, b) = tokio::join!(
        request(&app, &ada, &bea.name),
        request(&app, &bea, &ada.name)
    );
    let mut statuses = [a.status, b.status];
    statuses.sort();
    assert_eq!(statuses, [StatusCode::OK, StatusCode::CREATED]);
    assert_eq!(friend_ids(&app, &ada).await, vec![bea.id]);
    assert!(requests(&app, &ada, "incoming").await.is_empty());
    assert!(requests(&app, &bea, "incoming").await.is_empty());
}

#[tokio::test]
async fn decline_and_cancel() {
    let app = TestApp::new().await;
    let ada = app.player("Roarwing").await;
    let bea = app.player("Roarwing").await;
    let id = request(&app, &ada, &bea.name).await.body["request"]["id"]
        .as_i64()
        .unwrap();

    // The sender cannot decline, the recipient cannot cancel.
    let res = app
        .post(
            &format!("/api/friends/requests/{id}/decline"),
            json!({}),
            ada.token(),
        )
        .await;
    assert_eq!(res.body["error"], "request_not_found");
    let res = app
        .delete(&format!("/api/friends/requests/{id}"), bea.token())
        .await;
    assert_eq!(res.body["error"], "request_not_found");

    let res = app
        .post(
            &format!("/api/friends/requests/{id}/decline"),
            json!({}),
            bea.token(),
        )
        .await;
    assert_eq!(res.status, StatusCode::NO_CONTENT);
    assert!(requests(&app, &bea, "incoming").await.is_empty());
    assert!(friend_ids(&app, &ada).await.is_empty());

    // Asking again is allowed; this time the sender takes it back.
    let id = request(&app, &ada, &bea.name).await.body["request"]["id"]
        .as_i64()
        .unwrap();
    let res = app
        .delete(&format!("/api/friends/requests/{id}"), ada.token())
        .await;
    assert_eq!(res.status, StatusCode::NO_CONTENT);
    let res = app
        .delete(&format!("/api/friends/requests/{id}"), ada.token())
        .await;
    assert_eq!(
        (res.status, res.body["error"].clone()),
        (StatusCode::NOT_FOUND, json!("request_not_found"))
    );
    assert!(requests(&app, &bea, "incoming").await.is_empty());
    let res = app
        .post("/api/friends/requests/abc/accept", json!({}), bea.token())
        .await;
    assert_eq!(res.body["error"], "invalid_path");
}

#[tokio::test]
async fn removing_a_friend() {
    let app = TestApp::new().await;
    let ada = app.player("Serpentyne").await;
    let bea = app.player("Burrowsby").await;
    let cat = app.player("Burrowsby").await;
    befriend(&app, &ada, &bea).await;
    befriend(&app, &ada, &cat).await;

    let res = app
        .delete(&format!("/api/friends/{}", bea.id), ada.token())
        .await;
    assert_eq!(res.status, StatusCode::NO_CONTENT);
    assert_eq!(friend_ids(&app, &ada).await, vec![cat.id]);
    assert!(friend_ids(&app, &bea).await.is_empty(), "both sides");
    let res = app
        .delete(&format!("/api/friends/{}", bea.id), ada.token())
        .await;
    assert_eq!(
        (res.status, res.body["error"].clone()),
        (StatusCode::NOT_FOUND, json!("not_friends"))
    );
    // Removing someone else's friendship is not a thing.
    let res = app
        .delete(&format!("/api/friends/{}", ada.id), bea.token())
        .await;
    assert_eq!(res.body["error"], "not_friends");
    assert_eq!(friend_ids(&app, &cat).await, vec![ada.id]);
}

#[tokio::test]
async fn blocking() {
    let app = TestApp::new().await;
    let ada = app.player("Skytalon").await;
    let bea = app.player("Skytalon").await;
    let cat = app.player("Skytalon").await;
    befriend(&app, &ada, &bea).await;
    // Dan has asked ada to be friends.
    let dan = app.player("Roarwing").await;
    assert_eq!(
        request(&app, &dan, &ada.name).await.status,
        StatusCode::CREATED
    );

    // Blocking a friend ends the friendship on both sides.
    let res = app
        .post(
            "/api/blocks",
            json!({ "name": bea.name.to_lowercase() }),
            ada.token(),
        )
        .await;
    assert_eq!(res.status, StatusCode::CREATED, "{:?}", res.body);
    assert_eq!(res.body["character_id"], bea.id);
    assert_eq!(res.body["name"], bea.name);
    assert!(friend_ids(&app, &ada).await.is_empty());
    assert!(friend_ids(&app, &bea).await.is_empty());
    // Blocking again is fine and keeps the original time.
    let again = app
        .post("/api/blocks", json!({ "name": bea.name }), ada.token())
        .await;
    assert_eq!(again.status, StatusCode::OK);
    assert_eq!(again.body["created_at"], res.body["created_at"]);

    // Blocking someone with a pending request removes it.
    let res = app
        .post("/api/blocks", json!({ "name": dan.name }), ada.token())
        .await;
    assert_eq!(res.status, StatusCode::CREATED);
    assert!(requests(&app, &ada, "incoming").await.is_empty());
    assert!(requests(&app, &dan, "outgoing").await.is_empty());

    // The blocked cannot ask (and cannot tell they are blocked); the blocker
    // is told to unblock first.
    let res = request(&app, &bea, &ada.name).await;
    assert_eq!(
        (res.status, res.body["error"].clone()),
        (StatusCode::NOT_FOUND, json!("character_not_found"))
    );
    let res = request(&app, &ada, &bea.name).await;
    assert_eq!(
        (res.status, res.body["error"].clone()),
        (StatusCode::CONFLICT, json!("blocked"))
    );
    // Bystanders are unaffected.
    assert_eq!(
        request(&app, &cat, &ada.name).await.status,
        StatusCode::CREATED
    );

    let lists = server::friends::block_lists(&app.state.pool, ada.id)
        .await
        .unwrap();
    let mut blocked = lists.blocked.clone();
    blocked.sort_unstable();
    let mut expected = vec![bea.id, dan.id];
    expected.sort_unstable();
    assert_eq!(blocked, expected);
    let theirs = server::friends::block_lists(&app.state.pool, bea.id)
        .await
        .unwrap();
    assert_eq!(theirs.blocked_by, vec![ada.id]);

    // The block list, newest first.
    let res = app.get("/api/blocks", ada.token()).await;
    let names: Vec<i64> = res.body["items"]
        .as_array()
        .unwrap()
        .iter()
        .map(|b| b["character_id"].as_i64().unwrap())
        .collect();
    assert_eq!(names, vec![dan.id, bea.id]);

    // Self and unknown.
    let res = app
        .post("/api/blocks", json!({ "name": ada.name }), ada.token())
        .await;
    assert_eq!(
        (res.status, res.body["error"].clone()),
        (StatusCode::BAD_REQUEST, json!("self"))
    );
    let res = app
        .post("/api/blocks", json!({ "name": unique_name() }), ada.token())
        .await;
    assert_eq!(res.body["error"], "character_not_found");

    // Unblocking (by character id) restores nothing but allows asking again.
    let res = app
        .delete(&format!("/api/blocks/{}", bea.id), ada.token())
        .await;
    assert_eq!(res.status, StatusCode::NO_CONTENT);
    let res = app
        .delete(&format!("/api/blocks/{}", bea.id), ada.token())
        .await;
    assert_eq!(
        (res.status, res.body["error"].clone()),
        (StatusCode::NOT_FOUND, json!("not_blocked"))
    );
    let res = app
        .delete(&format!("/api/blocks/{}", ada.id), bea.token())
        .await;
    assert_eq!(
        res.body["error"], "not_blocked",
        "only the blocker unblocks"
    );
    assert!(friend_ids(&app, &ada).await.is_empty());
    assert_eq!(
        request(&app, &bea, &ada.name).await.status,
        StatusCode::CREATED
    );
}

/// Creates `n` characters directly in the database with the given names and
/// makes each a friend of `p`, returning their ids.
async fn add_friends_directly(app: &TestApp, p: &TestPlayer, names: &[String]) -> Vec<i64> {
    let mut ids = Vec::new();
    for name in names {
        let (account_id, _) = app.account().await;
        let c = server::characters::insert(
            &app.state.pool,
            account_id,
            name,
            protocol::House::Burrowsby,
            server::characters::SavedZone::Dorm,
        )
        .await
        .unwrap();
        sqlx::query("INSERT INTO friendships (character_id, friend_id) VALUES ($1, $2), ($2, $1)")
            .bind(p.id)
            .bind(c.id)
            .execute(&app.state.pool)
            .await
            .unwrap();
        ids.push(c.id);
    }
    ids
}

#[tokio::test]
async fn the_friend_list_pages_by_name_ignoring_case() {
    let app = TestApp::new().await;
    let p = app.player("Roarwing").await;
    let tag = &unique_name()[4..10];
    let names: Vec<String> = [
        "delta", "Alpha", "charlie", "Echo", "bravo", "Foxtrot", "golf",
    ]
    .iter()
    .map(|n| format!("{n}{tag}"))
    .collect();
    add_friends_directly(&app, &p, &names).await;
    let mut expected: Vec<String> = names.clone();
    expected.sort_by_key(|n| n.to_lowercase());

    let mut seen = Vec::new();
    let mut after = String::new();
    let mut pages = 0;
    loop {
        let res = app
            .get(&format!("/api/friends?limit=3&after={after}"), p.token())
            .await;
        assert_eq!(res.status, StatusCode::OK, "{:?}", res.body);
        let items = res.body["items"].as_array().unwrap();
        assert!(items.len() <= 3);
        seen.extend(items.iter().map(|f| f["name"].as_str().unwrap().to_owned()));
        pages += 1;
        match res.body["next"].as_str() {
            Some(c) => after = c.to_owned(),
            None => break,
        }
    }
    assert_eq!(seen, expected);
    assert_eq!(pages, 3);
    let res = app.get("/api/friends?after=garbage", p.token()).await;
    assert_eq!(res.body["error"], "invalid_cursor");
    // Exactly one page: no cursor.
    let res = app.get("/api/friends?limit=7", p.token()).await;
    assert_eq!(res.body["next"], Value::Null);
}

#[tokio::test]
async fn request_lists_page_newest_first() {
    let app = TestApp::new().await;
    let p = app.player("Serpentyne").await;
    let mut senders = Vec::new();
    for _ in 0..5 {
        let s = app.player("Serpentyne").await;
        assert_eq!(request(&app, &s, &p.name).await.status, StatusCode::CREATED);
        senders.push(s.id);
    }
    senders.reverse();
    let mut seen = Vec::new();
    let mut after = String::new();
    loop {
        let res = app
            .get(
                &format!("/api/friends/requests?limit=2&after={after}"),
                p.token(),
            )
            .await;
        seen.extend(
            res.body["items"]
                .as_array()
                .unwrap()
                .iter()
                .map(|r| r["character_id"].as_i64().unwrap()),
        );
        match res.body["next"].as_str() {
            Some(c) => after = c.to_owned(),
            None => break,
        }
    }
    assert_eq!(seen, senders);
    let res = app
        .get("/api/friends/requests?direction=sideways", p.token())
        .await;
    assert_eq!(res.body["error"], "invalid_query");
}

#[tokio::test]
async fn the_block_list_pages() {
    let app = TestApp::new().await;
    let p = app.player("Skytalon").await;
    let mut blocked = Vec::new();
    for _ in 0..5 {
        let other = app.player("Burrowsby").await;
        let res = app
            .post("/api/blocks", json!({ "name": other.name }), p.token())
            .await;
        assert_eq!(res.status, StatusCode::CREATED);
        blocked.push(other.id);
    }
    // Two blocks at the same instant still page apart (ties break by id).
    sqlx::query("UPDATE blocks SET created_at = '2026-01-01T00:00:00Z' WHERE blocker_id = $1")
        .bind(p.id)
        .execute(&app.state.pool)
        .await
        .unwrap();
    blocked.sort_unstable_by(|a, b| b.cmp(a));
    let mut seen = Vec::new();
    let mut after = String::new();
    loop {
        let res = app
            .get(&format!("/api/blocks?limit=2&after={after}"), p.token())
            .await;
        assert_eq!(res.status, StatusCode::OK, "{:?}", res.body);
        seen.extend(
            res.body["items"]
                .as_array()
                .unwrap()
                .iter()
                .map(|b| b["character_id"].as_i64().unwrap()),
        );
        match res.body["next"].as_str() {
            Some(c) => after = c.to_owned(),
            None => break,
        }
    }
    assert_eq!(seen, blocked);
}

#[tokio::test]
async fn the_friend_limit() {
    let app = TestApp::new().await;
    let full = app.player("Roarwing").await;
    // Fill `full` to the limit with directly inserted characters.
    sqlx::query(
        "WITH a AS (
             INSERT INTO accounts (email, password_hash, adult_attested_at, email_verified_at)
             SELECT 'limit-' || md5(random()::text) || '@example.org', '!', now(), now()
             FROM generate_series(1, $2) RETURNING id
         ), c AS (
             INSERT INTO characters (account_id, name, name_key, house)
             SELECT id, 'Lim' || substr(md5(random()::text), 1, 16),
                    'lim' || substr(md5(id::text || random()::text), 1, 16), 0
             FROM a RETURNING id
         )
         INSERT INTO friendships (character_id, friend_id)
         SELECT $1, id FROM c UNION ALL SELECT id, $1 FROM c",
    )
    .bind(full.id)
    .bind(i32::try_from(FRIENDS_MAX).unwrap())
    .execute(&app.state.pool)
    .await
    .unwrap();

    let other = app.player("Roarwing").await;
    // Sending while full.
    let res = request(&app, &full, &other.name).await;
    assert_eq!(
        (res.status, res.body["error"].clone()),
        (StatusCode::CONFLICT, json!("friend_limit"))
    );
    // Accepting while the other side is full.
    let id = request(&app, &other, &full.name).await.body["request"]["id"]
        .as_i64()
        .unwrap();
    let res = app
        .post(
            &format!("/api/friends/requests/{id}/accept"),
            json!({}),
            full.token(),
        )
        .await;
    assert_eq!(
        (res.status, res.body["error"].clone()),
        (StatusCode::CONFLICT, json!("friend_limit"))
    );
    assert_eq!(
        requests(&app, &full, "incoming").await.len(),
        1,
        "the request stays"
    );
    // Paging the full list works and stops.
    let mut count = 0;
    let mut after = String::new();
    loop {
        let res = app
            .get(
                &format!("/api/friends?limit=100&after={after}"),
                full.token(),
            )
            .await;
        count += res.body["items"].as_array().unwrap().len();
        match res.body["next"].as_str() {
            Some(c) => after = c.to_owned(),
            None => break,
        }
    }
    assert_eq!(count, FRIENDS_MAX as usize);
}

#[tokio::test]
async fn friend_writes_are_rate_limited() {
    let app = TestApp::new().await;
    let p = app.player("Burrowsby").await;
    let mut limited = false;
    for _ in 0..40 {
        let res = app.delete("/api/blocks/1", p.token()).await;
        if res.status == StatusCode::TOO_MANY_REQUESTS {
            assert_eq!(res.body["error"], "rate_limited");
            limited = true;
            break;
        }
        assert_eq!(res.body["error"], "not_blocked");
    }
    assert!(limited);
    // Wardrobe writes have their own budget.
    let res = app
        .post(
            "/api/equipment",
            json!({ "slot": "hat", "item_id": null }),
            p.token(),
        )
        .await;
    assert_eq!(res.status, StatusCode::OK);
}

#[tokio::test]
async fn friend_list_reads_are_rate_limited() {
    let app = TestApp::new().await;
    let p = app.player("Skytalon").await;
    // Each page asks the world where everyone is: a loop of them is cut off.
    let mut limited = None;
    for n in 0..60 {
        let res = app.get("/api/friends", p.token()).await;
        if res.status == StatusCode::TOO_MANY_REQUESTS {
            assert_eq!(res.body["error"], "rate_limited");
            assert!(res.headers.contains_key("retry-after"));
            limited = Some(n);
            break;
        }
        assert_eq!(res.status, StatusCode::OK);
    }
    assert!(limited.is_some_and(|n| n >= 20), "{limited:?}");
    // Other reads and writes keep their own budgets.
    let res = app.get("/api/friends/requests", p.token()).await;
    assert_eq!(res.status, StatusCode::OK);
    let res = app.get("/api/blocks", p.token()).await;
    assert_eq!(res.status, StatusCode::OK);
}

// ------------------------------------------------------------------ live

/// A synthetic hall, as in the realtime tests.
const LAYOUT: &str = r#"{
    "version": 1,
    "surfaces": [
        { "id": "hall", "kind": "room", "name": "Hall", "level": 0, "cell": "c",
          "min": [0, 0], "max": [200, 200], "y": 0, "ceiling": 10 }
    ],
    "spawns": [ { "id": "south", "pos": [100, 0, 100], "yaw": 0 } ]
}"#;

const WAIT: Duration = Duration::from_secs(5);

/// A real server with the world running, on a random local port.
async fn live_server() -> (TestApp, SocketAddr) {
    let config = Config {
        database_url: std::env::var("DATABASE_URL")
            .unwrap_or_else(|_| "postgres://localhost/shs_test".into()),
        dev: true,
        ..Config::default()
    };
    let pool = db::connect_with(&config.database_url, 4).await.unwrap();
    let mut state = AppState::new(pool, config, Arc::new(email::DevMailer));
    state.start_realtime(
        sim_core::World::from_json(LAYOUT).unwrap(),
        WordFilter::from_list(""),
    );
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    let router = http::router(state.clone());
    tokio::spawn(async move {
        axum::serve(
            listener,
            router.into_make_service_with_connect_info::<SocketAddr>(),
        )
        .await
        .unwrap();
    });
    (TestApp::from_state(state), addr)
}

type Socket = WebSocketStream<MaybeTlsStream<TcpStream>>;

async fn connect(addr: SocketAddr, token: &str) -> Socket {
    let (mut ws, _) = tokio_tungstenite::connect_async(format!("ws://{addr}/ws"))
        .await
        .unwrap();
    let hello = ClientMsg::Hello {
        protocol_version: PROTOCOL_VERSION,
        token: token.to_owned(),
        character_id: None,
    };
    ws.send(Message::Binary(encode_client(&hello).into()))
        .await
        .unwrap();
    expect(&mut ws, |m| {
        matches!(m, ServerMsg::Welcome { .. }).then_some(())
    })
    .await;
    ws
}

/// Reads until `pick` returns something (snapshots and the like are skipped).
async fn expect<T>(ws: &mut Socket, mut pick: impl FnMut(&ServerMsg) -> Option<T>) -> T {
    tokio::time::timeout(WAIT, async {
        loop {
            let msg = ws.next().await.expect("socket open").expect("frame");
            if let Message::Binary(bytes) = msg {
                let msg = decode_server(&bytes).expect("valid frame");
                if let Some(t) = pick(&msg) {
                    return t;
                }
            }
        }
    })
    .await
    .expect("the expected message arrives")
}

/// A dev bot session (a castle character); its first API request grants the
/// starter wardrobe.
async fn bot(app: &TestApp) -> TestPlayer {
    let name = format!("bot{}", &unique_name()[4..]);
    let res = app
        .post("/api/dev/bot-session", json!({ "name": name }), None)
        .await;
    let token = res.body["token"].as_str().unwrap().to_owned();
    let me = app.get("/api/characters/me", Some(&token)).await;
    assert_eq!(me.status, StatusCode::OK);
    let info = server::validate_session(&app.state.pool, &token)
        .await
        .unwrap();
    TestPlayer {
        account_id: info.account_id,
        id: me.body["id"].as_i64().unwrap(),
        name: me.body["name"].as_str().unwrap().to_owned(),
        house: me.body["house"].as_str().unwrap().to_owned(),
        token,
    }
}

#[tokio::test]
async fn online_players_get_pushed_friend_and_appearance_changes() {
    let (app, addr) = live_server().await;
    let ada = bot(&app).await;
    let bea = bot(&app).await;
    let mut ada_ws = connect(addr, &ada.token).await;
    let mut bea_ws = connect(addr, &bea.token).await;

    // A request reaches bea at once.
    let res = request(&app, &ada, &bea.name).await;
    assert_eq!(res.status, StatusCode::CREATED);
    let request_id = res.body["request"]["id"].as_i64().unwrap();
    let event = expect(&mut bea_ws, |m| match m {
        ServerMsg::Friend { event } => Some(event.clone()),
        _ => None,
    })
    .await;
    assert_eq!(
        event,
        FriendEvent::RequestReceived {
            request_id,
            character_id: ada.id,
            name: ada.name.clone(),
            house: serde_json::from_value(json!(ada.house)).unwrap(),
        }
    );

    // Accepting tells both, with where the other is.
    let res = app
        .post(
            &format!("/api/friends/requests/{request_id}/accept"),
            json!({}),
            bea.token(),
        )
        .await;
    assert_eq!(res.status, StatusCode::OK);
    assert_eq!(res.body["friend"]["online"], true);
    assert_eq!(
        res.body["friend"]["location"],
        json!({ "kind": "castle", "place": "Hall" })
    );
    for (ws, other) in [(&mut ada_ws, &bea), (&mut bea_ws, &ada)] {
        let (id, location) = expect(ws, |m| match m {
            ServerMsg::Friend {
                event:
                    FriendEvent::Added {
                        character_id,
                        location,
                        ..
                    },
            } => Some((*character_id, location.clone())),
            _ => None,
        })
        .await;
        assert_eq!(id, other.id);
        assert_eq!(
            location,
            Some(protocol::Location::Castle {
                place: "Hall".into()
            })
        );
    }
    let list = friends(&app, &ada).await;
    assert_eq!(list[0]["online"], true);
    assert_eq!(list[0]["location"]["kind"], "castle");

    // A wardrobe change reaches the wearer (and anyone who sees them).
    let res = app.get("/api/inventory?slot=hat", ada.token()).await;
    let hat = res.body["items"][0]["id"].as_i64().unwrap();
    let res = app
        .post(
            "/api/equipment",
            json!({ "slot": "hat", "item_id": hat }),
            ada.token(),
        )
        .await;
    let version = res.body["appearance_version"].as_u64().unwrap() as u32;
    let (got, outfit_hat) = expect(&mut ada_ws, |m| match m {
        ServerMsg::AppearanceChanged {
            appearance_version,
            appearance,
            ..
        } => Some((*appearance_version, appearance.outfit.hat)),
        _ => None,
    })
    .await;
    assert_eq!(got, version);
    assert!(outfit_hat.is_some());

    // Unfriending tells both.
    let res = app
        .delete(&format!("/api/friends/{}", ada.id), bea.token())
        .await;
    assert_eq!(res.status, StatusCode::NO_CONTENT);
    for (ws, other) in [(&mut ada_ws, &bea), (&mut bea_ws, &ada)] {
        let id = expect(ws, |m| match m {
            ServerMsg::Friend {
                event: FriendEvent::Removed { character_id },
            } => Some(*character_id),
            _ => None,
        })
        .await;
        assert_eq!(id, other.id);
    }
}
