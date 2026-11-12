//! The Owl Order Catalogue is a place: a stand in each house common room of
//! the shipped castle. A real server over `data/world/layout.json` with the
//! world running: a bot orders at its own house's stand, and is refused
//! everywhere else (before using the stand, at a rival house's stand, after
//! walking away), by the world's authoritative position. The catalogue's own
//! rules (prices, retries, the ledger) are `shop.rs`'s.

#[path = "effects_harness.rs"]
mod harness;

use std::path::Path;

use axum::body::Body;
use axum::http::{Request, StatusCode, header};
use protocol::{ClientMsg, DormErrorCode, House, ServerMsg, StationKind};
use serde_json::{Value, json};
use server::characters::house_from_db;
use server::http;
use server::realtime::stations::OWL_ORDER_AWAY;
use tower::ServiceExt;

const WOOL: u16 = 3001;

fn stand(house: House) -> &'static str {
    match house {
        House::Roarwing => "owl_order_house_a",
        House::Burrowsby => "owl_order_house_b",
        House::Skytalon => "owl_order_house_c",
        House::Serpentyne => "owl_order_house_d",
    }
}

async fn shipped_server() -> harness::TestServer {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../data/world/layout.json");
    let castle = server::realtime::load_world(&path).expect("the shipped castle loads");
    harness::TestServer::start_with(castle).await
}

/// An HTTP GET as the bot: (status, JSON body).
async fn get(server: &harness::TestServer, bot: &harness::Bot, path: &str) -> (StatusCode, Value) {
    let req = Request::get(path)
        .header(header::AUTHORIZATION, format!("Bearer {}", bot.token))
        .body(Body::empty())
        .unwrap();
    let res = http::router(server.state.clone())
        .oneshot(req)
        .await
        .unwrap();
    let status = res.status();
    let bytes = axum::body::to_bytes(res.into_body(), usize::MAX)
        .await
        .unwrap();
    (status, serde_json::from_slice(&bytes).unwrap_or_default())
}

async fn teleport(server: &harness::TestServer, bot: &harness::Bot, pos: [f32; 3]) {
    let (status, body) = server
        .post(bot, "/api/dev/teleport", json!({ "pos": pos }))
        .await;
    assert!(
        status.is_success(),
        "teleport to {pos:?}: {status} {body:?}"
    );
    // Past the zone-change grace before the next interaction.
    tokio::time::sleep(std::time::Duration::from_millis(700)).await;
}

fn assert_away(status: StatusCode, body: &Value) {
    assert_eq!(status, StatusCode::FORBIDDEN, "{body:?}");
    assert_eq!(body["error"], "not_at_station");
    assert_eq!(body["message"], OWL_ORDER_AWAY);
}

#[tokio::test]
async fn the_catalogue_is_ordered_from_at_your_own_common_rooms_stand() {
    let server = shipped_server().await;
    let castle = harness_castle();
    let ada = server.bot().await;
    let house: i16 = sqlx::query_scalar("SELECT house FROM characters WHERE id = $1")
        .bind(ada.id)
        .fetch_one(server.pool())
        .await
        .unwrap();
    let house = house_from_db(house).unwrap();
    let mine = castle.interactable(stand(house)).unwrap().clone();
    assert_eq!(mine.house.as_deref(), Some(house.id()));
    let wool_before = server.units(ada.id, WOOL).await;
    let mut a = server.join(&ada).await;
    let order = json!({ "lines": [{ "item_def": WOOL, "quantity": 2 }] });

    // At the spawn (the Entrance Hall): neither browsing nor ordering.
    let (status, body) = get(&server, &ada, "/api/shop?limit=5").await;
    assert_away(status, &body);
    let (status, body) = server.post(&ada, "/api/shop/orders", order.clone()).await;
    assert_away(status, &body);
    // Using the stand from afar is refused.
    a.send(&ClientMsg::Interact {
        target: stand(house).into(),
    })
    .await;
    let code = a
        .expect(|m| match m {
            ServerMsg::DormError { code, .. } => Some(*code),
            _ => None,
        })
        .await;
    assert_eq!(code, DormErrorCode::TooFar);

    // A rival house's stand is not this character's (its room is not even
    // walkable for them: the interaction is refused by house).
    let rival = House::ALL.into_iter().find(|h| *h != house).unwrap();
    a.send(&ClientMsg::Interact {
        target: stand(rival).into(),
    })
    .await;
    let code = a
        .expect(|m| match m {
            ServerMsg::DormError { code, .. } => Some(*code),
            _ => None,
        })
        .await;
    assert_eq!(code, DormErrorCode::WrongHouse);

    // In their own common room, at the stand: opened, browsed, ordered.
    teleport(&server, &ada, mine.pos).await;
    a.send(&ClientMsg::Interact {
        target: stand(house).into(),
    })
    .await;
    let opened = a
        .expect(|m| match m {
            ServerMsg::StationOpened { kind, target, .. } => Some((*kind, target.clone())),
            _ => None,
        })
        .await;
    assert_eq!(opened, (StationKind::OwlOrder, stand(house).to_owned()));
    let (status, body) = get(&server, &ada, "/api/shop?limit=5").await;
    assert_eq!(status, StatusCode::OK, "{body:?}");
    assert_eq!(body["items"].as_array().unwrap().len(), 5);
    let (status, body) = server.post(&ada, "/api/shop/orders", order.clone()).await;
    assert_eq!(status, StatusCode::CREATED, "{body:?}");
    // The owl brings it: readable (and taken) anywhere, as before.
    assert!(body["order"]["mail_id"].as_i64().is_some());

    // Walking off (here: back to the Entrance Hall) closes the catalogue.
    teleport(&server, &ada, [16.0, 0.0, 30.0]).await;
    let (status, body) = server.post(&ada, "/api/shop/orders", order).await;
    assert_away(status, &body);
    let (status, body) = get(&server, &ada, "/api/shop?limit=5").await;
    assert_away(status, &body);
    assert_eq!(server.units(ada.id, WOOL).await, wool_before + 2);
}

fn harness_castle() -> sim_core::Layout {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../data/world/layout.json");
    sim_core::Layout::from_json(&std::fs::read_to_string(path).unwrap()).unwrap()
}

/// Every house has one stand, in its own common room, where the house's own
/// key place leads.
#[test]
fn each_common_room_has_its_house_stand() {
    let layout = harness_castle();
    let stands: Vec<_> = layout
        .interactables
        .iter()
        .filter(|i| i.kind == sim_core::layout::InteractableKind::OwlOrder)
        .collect();
    assert_eq!(stands.len(), 4);
    for house in House::ALL {
        let s = layout.interactable(stand(house)).unwrap();
        assert_eq!(s.kind.station(), Some(StationKind::OwlOrder));
        assert_eq!(s.house.as_deref(), Some(house.id()));
        assert_eq!(s.name.as_deref(), Some("Owl Order Catalogue"));
    }
}
