//! The players list (`GET /api/players`: online only, name
//! and house only, search, paging, rate limit) and the player card (`GET
//! /api/players/{name}/card`) over a running world; and where new
//! characters and bots appear (the front steps, or a bot's `start`).

mod common;

use std::net::SocketAddr;
use std::path::Path;
use std::sync::Arc;
use std::time::Duration;

use axum::http::StatusCode;
use common::{TestApp, unique_name};
use futures_util::{SinkExt, StreamExt};
use protocol::{ClientMsg, PROTOCOL_VERSION, ServerMsg, Zone, decode_server, encode_client};
use serde_json::{Value, json};
use server::catalog::shipped;
use server::realtime::chat::WordFilter;
use server::{AppState, Config, db, email, http};
use sim_core::Subject;
use tokio::net::TcpStream;
use tokio_tungstenite::tungstenite::Message;
use tokio_tungstenite::{MaybeTlsStream, WebSocketStream};

const LAYOUT: &str = r#"{
    "version": 1,
    "surfaces": [
        { "id": "hall", "kind": "room", "name": "Hall", "level": 0, "cell": "c",
          "min": [0, 0], "max": [200, 200], "y": 0, "ceiling": 10 }
    ],
    "spawns": [{ "id": "middle", "pos": [100, 0, 100], "yaw": 0 }]
}"#;

const WAIT: Duration = Duration::from_secs(10);

type Socket = WebSocketStream<MaybeTlsStream<TcpStream>>;

/// A real server with `world` running, on a random local port.
async fn live_server(world: sim_core::World) -> (TestApp, SocketAddr) {
    let config = Config {
        database_url: std::env::var("DATABASE_URL")
            .unwrap_or_else(|_| "postgres://localhost/shs_test".into()),
        dev: true,
        ..Config::default()
    };
    let pool = db::connect_with(&config.database_url, 8).await.unwrap();
    let mut state = AppState::new(pool, config, Arc::new(email::DevMailer));
    state.start_realtime(world, WordFilter::from_list(""));
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

/// Connects and returns the socket with its `Welcome`'s zone and spawn (m).
async fn connect(addr: SocketAddr, token: &str) -> (Socket, Zone, [f32; 3]) {
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
    let welcome = tokio::time::timeout(WAIT, async {
        loop {
            let msg = ws.next().await.expect("socket open").expect("frame");
            if let Message::Binary(bytes) = msg
                && let ServerMsg::Welcome { zone, spawn, .. } = decode_server(&bytes).unwrap()
            {
                return (zone, spawn.to_m());
            }
        }
    })
    .await
    .expect("welcome");
    (ws, welcome.0, welcome.1)
}

async fn bot_session(app: &TestApp, name: &str, start: Option<[f32; 3]>) -> String {
    let res = app
        .post(
            "/api/dev/bot-session",
            json!({ "name": name, "start": start }),
            None,
        )
        .await;
    assert_eq!(res.status, StatusCode::OK, "{:?}", res.body);
    res.body["token"].as_str().unwrap().to_owned()
}

fn random_letters(n: usize) -> String {
    (0..n)
        .map(|_| (b'a' + rand::random::<u8>() % 26) as char)
        .collect()
}

fn names(page: &Value) -> Vec<String> {
    page["items"]
        .as_array()
        .unwrap()
        .iter()
        .map(|i| i["name"].as_str().unwrap().to_owned())
        .collect()
}

/// Every name the search `q` lists, page by page of `limit`; and the pages.
async fn all_pages(app: &TestApp, token: &str, q: &str, limit: u32) -> (Vec<String>, usize) {
    let mut out = Vec::new();
    let mut after = String::new();
    let mut pages = 0;
    loop {
        let res = app
            .get(
                &format!("/api/players?q={q}&limit={limit}&after={after}"),
                Some(token),
            )
            .await;
        assert_eq!(res.status, StatusCode::OK, "{:?}", res.body);
        pages += 1;
        let items = names(&res.body);
        assert!(items.len() <= limit as usize);
        out.extend(items);
        match res.body["next"].as_str() {
            Some(n) => after = n.to_owned(),
            None => return (out, pages),
        }
    }
}

#[tokio::test]
async fn the_list_finds_online_players_by_name_and_pages() {
    let (app, addr) = live_server(sim_core::World::from_json(LAYOUT).unwrap()).await;
    let tag = random_letters(6);
    let mut tokens = Vec::new();
    let mut sockets = Vec::new();
    for i in 0..120 {
        let token = bot_session(&app, &format!("pl{tag}{i:03}"), None).await;
        sockets.push(connect(addr, &token).await.0);
        tokens.push(token);
    }
    // Offline characters are not listed.
    let offline = app.player("Roarwing").await;
    let me = &tokens[0];

    // Everyone online whose name holds the tag, in name order, over pages.
    let mut expected: Vec<String> = (0..120).map(|i| format!("pl{tag}{i:03}")).collect();
    expected.sort();
    let (listed, pages) = all_pages(&app, me, &tag, 50).await;
    assert_eq!(listed, expected, "the caller is listed too");
    assert_eq!(pages, 3);
    // Case-insensitive, a substring anywhere in the name.
    let (upper, _) = all_pages(&app, me, &tag.to_uppercase(), 100).await;
    assert_eq!(upper, expected);
    let (eleven, _) = all_pages(&app, me, &format!("{}11", &tag[3..]), 100).await;
    assert_eq!(eleven.len(), 10, "{eleven:?}");
    let res = app
        .get(
            &format!("/api/players?q={}", offline.name.to_lowercase()),
            Some(me),
        )
        .await;
    assert!(names(&res.body).is_empty(), "offline");

    // Name and house only; the default page is 25.
    let res = app.get(&format!("/api/players?q={tag}"), Some(me)).await;
    let items = res.body["items"].as_array().unwrap();
    assert_eq!(items.len(), 25);
    for item in items {
        let mut keys: Vec<&String> = item.as_object().unwrap().keys().collect();
        keys.sort();
        assert_eq!(keys, ["house", "name"]);
        assert!(
            ["Roarwing", "Burrowsby", "Skytalon", "Serpentyne"]
                .contains(&item["house"].as_str().unwrap())
        );
    }

    // Refusals.
    let long = "a".repeat(25);
    let res = app.get(&format!("/api/players?q={long}"), Some(me)).await;
    assert_eq!(res.status, StatusCode::BAD_REQUEST);
    assert_eq!(res.body["error"], "invalid_query");
    let res = app.get("/api/players?limit=0", Some(me)).await;
    assert_eq!(res.body["error"], "invalid_query");
    let res = app.get("/api/players?after=garbage!", Some(me)).await;
    assert_eq!(res.body["error"], "invalid_cursor");
    assert_eq!(
        app.get("/api/players", None).await.status,
        StatusCode::UNAUTHORIZED
    );

    // Leaving the world leaves the list.
    let gone = format!("pl{tag}119");
    drop(sockets.pop());
    tokio::time::timeout(WAIT, async {
        loop {
            let res = app
                .get(&format!("/api/players?q={gone}"), Some(&tokens[1]))
                .await;
            if names(&res.body).is_empty() {
                return;
            }
            tokio::time::sleep(Duration::from_millis(50)).await;
        }
    })
    .await
    .expect("the leaver drops off the list");
}

#[tokio::test]
async fn the_card_shows_house_level_skills_and_outfit() {
    let (app, _addr) = live_server(sim_core::World::from_json(LAYOUT).unwrap()).await;
    let viewer = app.player("Skytalon").await;
    let seen = app.player("Burrowsby").await;
    // Skill and player XP to show (as the dev grant gives them).
    let subject = Subject::ALL[1];
    let level_2 = shipped().recipes.skill_levels[0];
    let res = app
        .post(
            "/api/dev/grant",
            json!({ "skills": { subject.id(): level_2 } }),
            seen.token(),
        )
        .await;
    assert_eq!(res.status, StatusCode::OK, "{:?}", res.body);

    // Offline, by name in any case.
    let res = app
        .get(
            &format!("/api/players/{}/card", seen.name.to_uppercase()),
            viewer.token(),
        )
        .await;
    assert_eq!(res.status, StatusCode::OK, "{:?}", res.body);
    let card = &res.body;
    let mut keys: Vec<&String> = card.as_object().unwrap().keys().collect();
    keys.sort();
    assert_eq!(
        keys,
        ["appearance", "club", "house", "level", "name", "skills"]
    );
    // In no club.
    assert!(card["club"].is_null());
    assert_eq!(card["name"], seen.name);
    assert_eq!(card["house"], "Burrowsby");
    let progression = app.get("/api/progression", seen.token()).await.body;
    assert_eq!(card["level"], progression["level"]);
    let skills = card["skills"].as_array().unwrap();
    assert_eq!(skills.len(), Subject::ALL.len());
    for (s, want) in skills.iter().zip(Subject::ALL) {
        assert_eq!(s["discipline"], want.id());
        let level = if want == subject { 2 } else { 1 };
        assert_eq!(s["level"], level, "{}", want.id());
        assert_eq!(s.as_object().unwrap().len(), 2, "no XP shown");
    }
    let me = app.get("/api/characters/me", seen.token()).await.body;
    assert_eq!(card["appearance"], me["appearance"], "the worn outfit");

    let res = app
        .get(
            &format!("/api/players/{}/card", unique_name()),
            viewer.token(),
        )
        .await;
    assert_eq!(res.status, StatusCode::NOT_FOUND);
    assert_eq!(res.body["error"], "player_not_found");
}

#[tokio::test]
async fn the_list_and_cards_share_a_read_budget() {
    let (app, addr) = live_server(sim_core::World::from_json(LAYOUT).unwrap()).await;
    let name = format!("rl{}", random_letters(8));
    let token = bot_session(&app, &name, None).await;
    let _ws = connect(addr, &token).await;
    for i in 0..20 {
        let path = if i % 2 == 0 {
            "/api/players".to_owned()
        } else {
            format!("/api/players/{name}/card")
        };
        let res = app.get(&path, Some(&token)).await;
        assert_eq!(res.status, StatusCode::OK, "request {i}: {:?}", res.body);
    }
    let res = app.get("/api/players", Some(&token)).await;
    assert_eq!(res.status, StatusCode::TOO_MANY_REQUESTS);
    assert_eq!(res.body["error"], "rate_limited");
    assert!(res.headers.contains_key("retry-after"));
    let res = app
        .get(&format!("/api/players/{name}/card"), Some(&token))
        .await;
    assert_eq!(res.status, StatusCode::TOO_MANY_REQUESTS);
}

#[tokio::test]
async fn new_characters_appear_on_the_front_steps_and_bots_where_asked() {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../data/world/layout.json");
    let world = server::realtime::load_world(&path).unwrap();
    let steps: Vec<[f32; 3]> = world
        .spawns()
        .iter()
        .filter(|s| s.id.starts_with("front_steps"))
        .map(|s| s.pos)
        .collect();
    assert_eq!(steps.len(), 4);
    assert_eq!(steps.len(), world.spawns().len(), "every spawn is outside");
    let courtyard = |p: [f32; 3]| {
        world
            .ground_at(p[0], p[2], p[1])
            .map(|g| world.surface(g.surface).cell.clone())
    };
    let (app, addr) = live_server(server::realtime::load_world(&path).unwrap()).await;
    let near_steps = |p: [f32; 3]| steps.iter().any(|s| (s[0] - p[0]).hypot(s[2] - p[2]) < 3.0);

    // A new player: the castle, by the front doors, outside.
    let p = app.player("Serpentyne").await;
    let (_ws, zone, at) = connect(addr, &p.token).await;
    assert_eq!(zone, Zone::Castle);
    assert!(near_steps(at), "{at:?}");
    assert_eq!(courtyard(at).as_deref(), Some("courtyard"), "{at:?}");

    // A bot without a start does the same; one with a start begins there.
    let plain = bot_session(&app, &format!("fs{}", random_letters(8)), None).await;
    let (_ws2, _, at) = connect(addr, &plain).await;
    assert!(near_steps(at), "{at:?}");
    let hall = [14.0, 0.0, 32.0];
    let started = bot_session(&app, &format!("fs{}", random_letters(8)), Some(hall)).await;
    let (_ws3, zone, at) = connect(addr, &started).await;
    assert_eq!(zone, Zone::Castle);
    assert!(
        (at[0] - hall[0]).abs() < 0.01 && (at[2] - hall[2]).abs() < 0.01,
        "{at:?}"
    );
    assert_eq!(courtyard(at).as_deref(), Some("entrance_hall"));
}
