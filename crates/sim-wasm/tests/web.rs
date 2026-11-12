//! Tests that need a JS engine: run with `scripts/build-wasm.sh --test` (Node).
#![cfg(target_arch = "wasm32")]

use js_sys::{Array, Reflect};
use protocol::{
    Anim, Appearance, CharacterInfo, ChatChannel, ChatRejectReason, ClientMsg, DormErrorCode,
    EntityDelta, EntityEntry, EntityInfo, EntityState, FriendEvent, FurnitureEdit, House, KickCode,
    Location, Outfit, PlacedFurniture, QPos, ServerMsg, Worn, Zone, decode_client, encode_server,
};
use protocol::{Finish, VisibleEffect};
use sim_wasm::{
    ServerDecoder, SimWorld, can_wear, encode_dorm_place, encode_emote, furniture_top_areas,
    move_params, placement_fits, protocol_constants,
};
use wasm_bindgen::JsValue;
use wasm_bindgen_test::wasm_bindgen_test;

fn get(v: &JsValue, key: &str) -> JsValue {
    Reflect::get(v, &key.into()).unwrap()
}

fn num(v: &JsValue, key: &str) -> f64 {
    get(v, key)
        .as_f64()
        .unwrap_or_else(|| panic!("{key} is not a number"))
}

fn string(v: &JsValue, key: &str) -> String {
    get(v, key)
        .as_string()
        .unwrap_or_else(|| panic!("{key} is not a string"))
}

fn decode(d: &mut ServerDecoder, msg: &ServerMsg) -> JsValue {
    d.decode(&encode_server(msg)).unwrap()
}

fn vec3(v: &JsValue) -> Vec<f64> {
    Array::from(v).iter().map(|x| x.as_f64().unwrap()).collect()
}

fn dressed() -> Appearance {
    Appearance {
        body_type: 1,
        hair_style: 18,
        eye_colour: 10,
        outfit: Outfit {
            body: Some(Worn {
                item_def: 203,
                dyes: [0, 32, 38],
                finish: Finish::Shimmer,
            }),
            ..Outfit::default()
        },
        ..Appearance::default()
    }
}

fn info(id: u32, name: &str) -> EntityEntry {
    EntityEntry::Full(EntityInfo {
        id,
        character: CharacterInfo {
            name: name.into(),
            house: House::Roarwing,
        },
        appearance_version: 2,
        appearance: dressed(),
        effects_version: 1,
        effects: vec![VisibleEffect::Glow { colour: 21 }],
        level: 4,
        npc: None,
    })
}

fn state(id: u32, pos: [f32; 3], anim: Anim) -> EntityState {
    EntityState {
        id,
        pos: QPos::from_m(pos),
        yaw: 64,
        anim,
    }
}

fn snapshot(
    entered: Vec<EntityEntry>,
    full: Vec<EntityState>,
    deltas: Vec<EntityDelta>,
    left: Vec<u32>,
) -> ServerMsg {
    ServerMsg::Snapshot {
        tick: 42,
        entered,
        full,
        deltas,
        left,
        looks: Vec::new(),
    }
}

/// Protocol v10: look changes come through as `{ id, target }` (`target`
/// null: nobody), and `encodeLookAt` encodes `ClientMsg::LookAt`.
#[wasm_bindgen_test]
fn decodes_look_changes_and_encodes_look_at() {
    let mut d = ServerDecoder::new();
    let msg = ServerMsg::Snapshot {
        tick: 1,
        entered: vec![info(3, "Ada"), info(9, "Bea")],
        full: vec![
            state(3, [0.0, 0.0, 0.0], Anim::Idle),
            state(9, [1.0, 0.0, 0.0], Anim::Idle),
        ],
        deltas: vec![],
        left: vec![],
        looks: vec![
            protocol::LookChange {
                id: 3,
                target: Some(9),
            },
            protocol::LookChange {
                id: 9,
                target: None,
            },
        ],
    };
    let js = decode(&mut d, &msg);
    let looks = Array::from(&get(&js, "looks"));
    assert_eq!(looks.length(), 2);
    assert_eq!(num(&looks.get(0), "id"), 3.0);
    assert_eq!(num(&looks.get(0), "target"), 9.0);
    assert!(get(&looks.get(1), "target").is_null());
    // A snapshot without changes has an empty list.
    let js = decode(&mut d, &snapshot(vec![], vec![], vec![], vec![9]));
    assert_eq!(Array::from(&get(&js, "looks")).length(), 0);

    assert_eq!(
        decode_client(&sim_wasm::encode_look_at(Some(9))).unwrap(),
        ClientMsg::LookAt { target: Some(9) }
    );
    assert_eq!(
        decode_client(&sim_wasm::encode_look_at(None)).unwrap(),
        ClientMsg::LookAt { target: None }
    );
    let c = protocol_constants();
    assert_eq!(num(&c, "lookRangeM"), 20.0);
    assert_eq!(num(&c, "lookSendsPerSec"), 4.0);
}

#[wasm_bindgen_test]
fn decodes_snapshots_in_metres_and_applies_deltas() {
    let mut d = ServerDecoder::new();
    let js = decode(
        &mut d,
        &snapshot(
            vec![info(3, "Ada"), info(9, "Bea")],
            vec![
                state(3, [12.34, 4.0, -100.25], Anim::Walk),
                state(9, [0.0, 0.0, 0.0], Anim::Idle),
            ],
            vec![],
            vec![],
        ),
    );
    assert_eq!(string(&js, "type"), "snapshot");
    assert_eq!(num(&js, "tick"), 42.0);
    assert_eq!(d.visible_count(), 2);

    let entered = Array::from(&get(&js, "entered"));
    let e = entered.get(0);
    assert_eq!(num(&e, "id"), 3.0);
    assert_eq!(num(&e, "appearanceVersion"), 2.0);
    assert_eq!(string(&get(&e, "character"), "house"), "roarwing");
    let appearance = get(&e, "appearance");
    assert_eq!(num(&appearance, "bodyType"), 1.0);
    assert_eq!(num(&appearance, "hairStyle"), 18.0);
    assert_eq!(num(&appearance, "eyeColour"), 10.0);
    assert!(get(&appearance, "build").is_undefined());
    let body = get(&get(&appearance, "outfit"), "body");
    assert_eq!(num(&body, "itemDef"), 203.0);
    assert_eq!(vec3(&get(&body, "dyes")), [0.0, 32.0, 38.0]);
    assert!(get(&get(&appearance, "outfit"), "hat").is_null());

    let s = Array::from(&get(&js, "states")).get(0);
    assert_eq!(num(&s, "x"), 12.34);
    assert_eq!(num(&s, "y"), 4.0);
    assert_eq!(num(&s, "z"), -100.25);
    assert!((num(&s, "yaw") - std::f64::consts::FRAC_PI_2).abs() < 1e-12);
    assert_eq!(string(&s, "anim"), "walk");

    // A delta for index 0 (id 3) arrives as an absolute state.
    let delta = EntityDelta {
        index: 0,
        dx: 40,
        dy: 0,
        dz: -10,
        yaw: 0,
        anim: Anim::Run,
    };
    let js = decode(&mut d, &snapshot(vec![], vec![], vec![delta], vec![]));
    let s = Array::from(&get(&js, "states")).get(0);
    assert_eq!(num(&s, "id"), 3.0);
    assert_eq!(num(&s, "x"), 12.74);
    assert_eq!(num(&s, "z"), -100.35);
    assert_eq!(string(&s, "anim"), "run");

    // 3 leaves; id 9 is now index 0.
    let js = decode(
        &mut d,
        &snapshot(
            vec![],
            vec![],
            vec![EntityDelta { index: 0, ..delta }],
            vec![3],
        ),
    );
    let s = Array::from(&get(&js, "states")).get(0);
    assert_eq!(num(&s, "id"), 9.0);
    assert_eq!(num(&s, "x"), 0.4);
    assert_eq!(vec3(&get(&js, "left")), [3.0]);

    // A delta outside the set is a desync and throws; a zone change resets.
    let bad = EntityDelta { index: 5, ..delta };
    assert!(
        d.decode(&encode_server(&snapshot(vec![], vec![], vec![bad], vec![])))
            .is_err()
    );
    decode(
        &mut d,
        &ServerMsg::ZoneChanged {
            zone: Zone::Castle,
            spawn: QPos::from_m([1.0, 0.0, 1.0]),
            yaw: 0,
        },
    );
    assert_eq!(d.visible_count(), 0);
}

#[wasm_bindgen_test]
fn cached_entries_arrive_complete() {
    let mut d = ServerDecoder::new();
    decode(
        &mut d,
        &snapshot(
            vec![info(3, "Ada")],
            vec![state(3, [1.0, 0.0, 1.0], Anim::Idle)],
            vec![],
            vec![],
        ),
    );
    // Ada leaves, the client changes zone and back, and Ada enters again as
    // a cached entry: the decoder fills in who she is.
    decode(&mut d, &snapshot(vec![], vec![], vec![], vec![3]));
    let zone = ServerMsg::ZoneChanged {
        zone: Zone::Castle,
        spawn: QPos::from_m([1.0, 0.0, 1.0]),
        yaw: 0,
    };
    decode(&mut d, &zone);
    let cached = EntityEntry::Cached {
        id: 3,
        appearance_version: 2,
        effects_version: 1,
    };
    let js = decode(
        &mut d,
        &snapshot(
            vec![cached.clone()],
            vec![state(3, [2.0, 0.0, 1.0], Anim::Idle)],
            vec![],
            vec![],
        ),
    );
    let e = Array::from(&get(&js, "entered")).get(0);
    assert_eq!(string(&get(&e, "character"), "name"), "Ada");
    assert_eq!(num(&get(&e, "appearance"), "bodyType"), 1.0);

    // A new connection (`reset`, or its `Welcome`) forgets every look: the
    // same entry is now a desync.
    decode(&mut d, &zone);
    d.reset();
    let frame = encode_server(&snapshot(
        vec![cached],
        vec![state(3, [2.0, 0.0, 1.0], Anim::Idle)],
        vec![],
        vec![],
    ));
    assert!(d.decode(&frame).is_err());
}

#[wasm_bindgen_test]
fn decodes_every_other_message() {
    let mut d = ServerDecoder::new();
    let welcome = decode(
        &mut d,
        &ServerMsg::Welcome {
            protocol_version: 2,
            your_id: 5,
            character: CharacterInfo {
                name: "Mara".into(),
                house: House::Skytalon,
            },
            appearance_version: 3,
            appearance: dressed(),
            effects_version: 0,
            effects: vec![],
            zone: Zone::Dorm {
                owner_name: "Mara".into(),
                house: House::Skytalon,
                layout: "dorm_skytalon".into(),
            },
            spawn: QPos::from_m([1.5, 0.0, 2.25]),
            yaw: 128,
            tick_hz: 20,
            snapshot_hz: 10,
            unix_ms: 1_800_000_000_000,
        },
    );
    assert_eq!(num(&welcome, "unixMs"), 1_800_000_000_000.0);
    assert_eq!(string(&welcome, "type"), "welcome");
    assert_eq!(num(&welcome, "yourId"), 5.0);
    assert_eq!(string(&get(&welcome, "character"), "house"), "skytalon");
    assert_eq!(vec3(&get(&welcome, "spawn")), [1.5, 0.0, 2.25]);
    assert!((num(&welcome, "yaw") - std::f64::consts::PI).abs() < 1e-12);
    let zone = get(&welcome, "zone");
    assert_eq!(string(&zone, "kind"), "dorm");
    assert_eq!(string(&zone, "layout"), "dorm_skytalon");
    assert_eq!(string(&zone, "ownerName"), "Mara");

    let whisper = decode(
        &mut d,
        &ServerMsg::Chat {
            from: 4,
            from_name: "Tobin".into(),
            channel: ChatChannel::Whisper { to: "Mara".into() },
            text: "hi".into(),
        },
    );
    assert_eq!(string(&whisper, "channel"), "whisper");
    assert_eq!(string(&whisper, "to"), "Mara");

    let kick = decode(
        &mut d,
        &ServerMsg::Kick {
            code: KickCode::ServerRestart,
            reason: "bye".into(),
        },
    );
    assert_eq!(string(&kick, "code"), "server_restart");
    assert_eq!(string(&kick, "reason"), "bye");

    let rejected = decode(
        &mut d,
        &ServerMsg::ChatRejected {
            client_seq: 12,
            reason: ChatRejectReason::RateLimited,
        },
    );
    assert_eq!(string(&rejected, "type"), "chatRejected");
    assert_eq!(num(&rejected, "clientSeq"), 12.0);
    assert_eq!(string(&rejected, "reason"), "rate_limited");

    let changed = decode(
        &mut d,
        &ServerMsg::AppearanceChanged {
            id: 5,
            appearance_version: 4,
            appearance: Appearance::default(),
        },
    );
    assert_eq!(num(&changed, "appearanceVersion"), 4.0);
    assert_eq!(num(&get(&changed, "appearance"), "height"), 128.0);

    let placed = PlacedFurniture {
        item_id: 123_456_789_012,
        item_def: 1001,
        dyes: [10, 16, 21],
        pos: QPos::from_m([1.25, 0.0, -2.5]),
        yaw: 64,
    };
    let state = decode(
        &mut d,
        &ServerMsg::DormState {
            open_to_friends: true,
            cap: 150,
            items: vec![placed],
        },
    );
    assert_eq!(get(&state, "openToFriends"), JsValue::TRUE);
    let item = Array::from(&get(&state, "items")).get(0);
    assert_eq!(num(&item, "itemId"), 123_456_789_012.0);
    assert_eq!(vec3(&get(&item, "pos")), [1.25, 0.0, -2.5]);

    let edit = decode(
        &mut d,
        &ServerMsg::DormFurniture {
            edit: FurnitureEdit::Removed { item_id: 7 },
        },
    );
    let edit = get(&edit, "edit");
    assert_eq!(string(&edit, "kind"), "removed");
    assert_eq!(num(&edit, "itemId"), 7.0);

    let invite = decode(
        &mut d,
        &ServerMsg::DormInviteReceived {
            invite_id: 3,
            from_name: "Ada".into(),
            expires_in_secs: 120,
            wishing_room: Some(protocol::WishingRoomKind::Gathering),
        },
    );
    assert_eq!(string(&invite, "type"), "dormInviteReceived");
    assert_eq!(string(&invite, "wishingRoom"), "gathering");
    let err = decode(
        &mut d,
        &ServerMsg::DormError {
            code: DormErrorCode::CapReached,
            item_id: None,
        },
    );
    assert_eq!(string(&err, "code"), "cap_reached");
    assert!(get(&err, "itemId").is_undefined());

    let online = decode(
        &mut d,
        &ServerMsg::Friend {
            event: FriendEvent::Online {
                character_id: 77,
                location: Location::Castle {
                    place: "Great Hall".into(),
                },
                room_open: true,
            },
        },
    );
    let event = get(&online, "event");
    assert_eq!(string(&event, "kind"), "online");
    assert_eq!(num(&event, "characterId"), 77.0);
    assert_eq!(string(&get(&event, "location"), "place"), "Great Hall");
    assert_eq!(get(&event, "roomOpen").as_bool(), Some(true));

    let open = decode(
        &mut d,
        &ServerMsg::Friend {
            event: FriendEvent::RoomOpen {
                character_id: 77,
                open: false,
            },
        },
    );
    let event = get(&open, "event");
    assert_eq!(string(&event, "kind"), "roomOpen");
    assert_eq!(get(&event, "open").as_bool(), Some(false));

    let declined = decode(
        &mut d,
        &ServerMsg::DormInviteDeclined {
            by_name: "Bea".into(),
        },
    );
    assert_eq!(string(&declined, "type"), "dormInviteDeclined");
    assert_eq!(string(&declined, "byName"), "Bea");
    let full = decode(
        &mut d,
        &ServerMsg::DormError {
            code: DormErrorCode::Full,
            item_id: None,
        },
    );
    assert_eq!(string(&full, "code"), "full");
}

#[wasm_bindgen_test]
fn malformed_frames_throw() {
    assert!(ServerDecoder::new().decode(&[0xff, 0xff, 0xff]).is_err());
}

#[wasm_bindgen_test]
fn encodes_dorm_and_emote_messages() {
    let place = decode_client(&encode_dorm_place(42.0, 1.234, 0.0, -2.0, 0.0).unwrap()).unwrap();
    assert_eq!(
        place,
        ClientMsg::DormPlace {
            item_id: 42,
            pos: QPos::from_m([1.23, 0.0, -2.0]),
            yaw: 0
        }
    );
    assert!(encode_dorm_place(1.5, 0.0, 0.0, 0.0, 0.0).is_err());
    assert!(encode_dorm_place(-1.0, 0.0, 0.0, 0.0, 0.0).is_err());
    let emote = decode_client(&encode_emote("sit").unwrap()).unwrap();
    assert_eq!(
        emote,
        ClientMsg::Emote {
            emote: protocol::Emote::Sit
        }
    );
    assert!(encode_emote("dance").is_err());
    assert_eq!(num(&protocol_constants(), "dormItemCap"), 150.0);
}

const LAYOUT: &str = r#"{
  "version": 1,
  "surfaces": [
    { "id": "room", "kind": "room", "name": "Room", "level": 0, "cell": "c",
      "min": [0, 0], "max": [10, 10], "y": 0, "ceiling": 4 },
    { "id": "common", "kind": "room", "name": "Common Room", "level": 0, "cell": "c",
      "min": [10, 0], "max": [20, 10], "y": 0, "ceiling": 4, "house": "burrowsby" }
  ],
  "spawns": [{ "id": "start", "pos": [5, 0, 5], "yaw": 0.5 }],
  "interactables": [
    { "id": "door", "kind": "dorm_door", "house": "burrowsby", "pos": [18, 0, 5], "radius": 1.5 }
  ],
  "place_areas": [
    { "id": "floor", "surface": "floor", "min": [0, 0, 0], "max": [10, 0, 10] }
  ]
}"#;

#[wasm_bindgen_test]
fn sim_world_moves_and_queries() {
    let world = SimWorld::new(LAYOUT, None).unwrap();
    assert_eq!(world.surface_count(), 2);
    let pos = world.move_and_slide(5.0, 0.0, 5.0, 2.0, 0.0);
    assert!(
        (pos[0] - 7.0).abs() < 0.01 && pos[1] == 0.0 && pos[2] == 5.0,
        "{pos:?}"
    );
    assert_eq!(world.ground_at(5.0, 5.0, 0.2), Some(0.0));
    assert_eq!(world.ground_at(50.0, 5.0, 0.0), None);
    assert_eq!(world.level_at(5.0, 5.0, 0.0), Some(0));
    assert_eq!(world.surface_id_at(5.0, 5.0, 0.0).as_deref(), Some("room"));

    let spawn = Array::from(&world.spawns().unwrap()).get(0);
    assert_eq!(string(&spawn, "id"), "start");
    assert_eq!(vec3(&get(&spawn, "pos")), [5.0, 0.0, 5.0]);
    assert_eq!(num(&spawn, "yaw"), 0.5);

    assert!(SimWorld::new("{}", None).is_err());
    assert_eq!(num(&move_params(None), "stepHeight"), 0.35);
}

#[wasm_bindgen_test]
fn house_worlds_and_placement() {
    // Another house finds the common room walled off; its own house walks in.
    let other = SimWorld::new(LAYOUT, Some("roarwing".into())).unwrap();
    assert_eq!(other.surface_count(), 1);
    let blocked = other.move_and_slide(9.0, 0.0, 5.0, 3.0, 0.0);
    assert!(blocked[0] < 10.0, "{blocked:?}");
    assert_eq!(Array::from(&other.interactables().unwrap()).length(), 0);
    let own = SimWorld::new(LAYOUT, Some("burrowsby".into())).unwrap();
    assert!(own.move_and_slide(9.0, 0.0, 5.0, 3.0, 0.0)[0] > 11.0);
    assert!(own.in_reach("door", 17.0, 0.0, 5.0));
    assert!(!own.in_reach("door", 15.0, 0.0, 5.0));
    assert!(SimWorld::new(LAYOUT, Some("nowhere".into())).is_err());

    // A 1 x 2 m bed fits in the middle of the floor, not over its edge; a
    // lamp fits on a desk's top (turned 22.5 degrees) listed in `tops`.
    let none = || JsValue::from(Array::new());
    assert!(
        own.can_place("floor", 1.0, 2.0, 1.0, 5.0, 0.0, 5.0, 0.0, none())
            .unwrap()
    );
    assert!(
        !own.can_place("floor", 1.0, 2.0, 1.0, 9.8, 0.0, 5.0, 0.0, none())
            .unwrap()
    );
    let desk = js_sys::JSON::parse(
        r#"{ "surface": "floor", "footprint": [1.2, 0.6], "height": 0.78, "top": 0.75 }"#,
    )
    .unwrap();
    let yaw = std::f32::consts::PI / 8.0;
    let tops = furniture_top_areas(desk, "top_1", 3.0, 0.0, 3.0, yaw).unwrap();
    assert!(
        own.can_place("table", 0.2, 0.2, 0.3, 3.0, 0.75, 3.0, 0.0, tops.clone())
            .unwrap()
    );
    assert!(placement_fits(tops, "table", 0.2, 0.2, 0.3, 3.0, 0.75, 3.0, 0.0).unwrap());
    assert!(
        own.can_place("shelf", 1.0, 1.0, 1.0, 0.0, 0.0, 0.0, 0.0, none())
            .is_err()
    );
}

#[wasm_bindgen_test]
fn decodes_the_economy_messages_and_effects() {
    use protocol::{
        EffectKind, FindOutcome, FoundItem, OfferedItem, OwnEffect, StationKind, TradeErrorCode,
        TradeOffer, TradeOutcome, TradeView,
    };
    let mut d = ServerDecoder::new();
    // Entries carry effects and finishes.
    let js = decode(
        &mut d,
        &snapshot(
            vec![info(3, "Ada")],
            vec![state(3, [1.0, 0.0, 1.0], Anim::Idle)],
            vec![],
            vec![],
        ),
    );
    let e = Array::from(&get(&js, "entered")).get(0);
    assert_eq!(num(&e, "effectsVersion"), 1.0);
    let glow = Array::from(&get(&e, "effects")).get(0);
    assert_eq!(string(&glow, "kind"), "glow");
    assert_eq!(num(&glow, "colour"), 21.0);
    let body = get(&get(&get(&e, "appearance"), "outfit"), "body");
    assert_eq!(string(&body, "finish"), "shimmer");

    let wallet = decode(&mut d, &ServerMsg::Wallet { crowns: 1_250 });
    assert_eq!(string(&wallet, "type"), "wallet");
    assert_eq!(num(&wallet, "crowns"), 1_250.0);
    let station = decode(
        &mut d,
        &ServerMsg::StationOpened {
            kind: StationKind::Cauldron,
            target: "item:77".into(),
            expires_in_secs: 900,
        },
    );
    assert_eq!(string(&station, "kind"), "cauldron");
    let found = decode(
        &mut d,
        &ServerMsg::FindResult {
            spot: "find_wt_top".into(),
            outcome: FindOutcome::Found {
                items: vec![FoundItem {
                    item_def: 3006,
                    quantity: 1,
                }],
                crowns: 4,
            },
        },
    );
    let outcome = get(&found, "outcome");
    assert_eq!(string(&outcome, "kind"), "found");
    assert_eq!(
        num(&Array::from(&get(&outcome, "items")).get(0), "itemDef"),
        3006.0
    );
    let offer = TradeOffer {
        items: vec![OfferedItem {
            item_id: 12,
            item_def: 110,
            dyes: [1, 2, 3],
            finish: Finish::ColourShift,
            quantity: 1,
            maker: Some("Mara".into()),
        }],
        crowns: 25,
    };
    let trade = decode(
        &mut d,
        &ServerMsg::TradeState {
            view: TradeView {
                trade_id: 3,
                partner: 9,
                partner_name: "Bea".into(),
                rev: 4,
                mine: offer.clone(),
                theirs: TradeOffer::default(),
                mine_locked: true,
                theirs_locked: false,
                mine_confirmed: false,
                theirs_confirmed: false,
            },
        },
    );
    let view = get(&trade, "view");
    assert_eq!(num(&view, "rev"), 4.0);
    let line = Array::from(&get(&get(&view, "mine"), "items")).get(0);
    assert_eq!(string(&line, "maker"), "Mara");
    assert_eq!(string(&line, "finish"), "colour_shift");
    assert!(get(&view, "mineLocked").as_bool().unwrap());
    let failed = decode(
        &mut d,
        &ServerMsg::TradeResult {
            trade_id: 3,
            outcome: TradeOutcome::Failed(TradeErrorCode::ItemUnavailable),
        },
    );
    let outcome = get(&failed, "outcome");
    assert_eq!(string(&outcome, "kind"), "failed");
    assert_eq!(string(&outcome, "code"), "item_unavailable");
    let own = decode(
        &mut d,
        &ServerMsg::OwnEffects {
            effects: vec![
                OwnEffect {
                    kind: EffectKind::PickMeUpTonic,
                    remaining_secs: 600,
                    fortune: None,
                },
                OwnEffect {
                    kind: EffectKind::Fortune,
                    remaining_secs: 3_600,
                    fortune: Some(protocol::FortuneKind::KeenMind),
                },
            ],
            speed_pct: 115,
            broom_pct: 100,
        },
    );
    assert_eq!(num(&own, "speedPct"), 115.0);
    assert_eq!(
        string(&Array::from(&get(&own, "effects")).get(0), "kind"),
        "pick_me_up_tonic"
    );
    assert!(get(&Array::from(&get(&own, "effects")).get(0), "fortune").is_null());
    assert_eq!(
        string(&Array::from(&get(&own, "effects")).get(1), "fortune"),
        "keen_mind"
    );
    let changed = decode(
        &mut d,
        &ServerMsg::EffectsChanged {
            id: 3,
            effects_version: 2,
            effects: vec![VisibleEffect::Disguise {
                appearance: dressed(),
            }],
        },
    );
    let disguise = Array::from(&get(&changed, "effects")).get(0);
    assert_eq!(string(&disguise, "kind"), "disguise");
    assert_eq!(num(&get(&disguise, "appearance"), "bodyType"), 1.0);
    // The boosted speed the client predicts with.
    let fast = move_params(Some(115));
    assert!((num(&fast, "runSpeed") - 6.5 * 1.15).abs() < 1e-5);
    assert_eq!(num(&protocol_constants(), "speedPctNormal"), 100.0);
}

#[wasm_bindgen_test]
fn decodes_the_school_and_dueling_messages() {
    use protocol::{
        DuelEvent, DuelSide, LessonPhase, NpcEvent, Progress, SpellTarget, XpGain, XpSource,
    };
    let mut d = ServerDecoder::new();
    let p = decode(
        &mut d,
        &ServerMsg::Progress {
            progress: Progress {
                level: 2,
                xp: 250,
                level_xp: 200,
                next_level_xp: Some(440),
            },
            gained: Some(XpGain {
                source: XpSource::ClassAttend,
                amount: 80,
                capped: false,
            }),
        },
    );
    assert_eq!(string(&p, "type"), "progress");
    assert_eq!(num(&get(&p, "progress"), "nextLevelXp"), 440.0);
    assert_eq!(string(&get(&p, "gained"), "source"), "class_attend");
    let phase = decode(
        &mut d,
        &ServerMsg::LessonPhaseChanged {
            phase: LessonPhase::Practice { round: 2 },
            until_unix_ms: 1_800_000_000_000,
        },
    );
    assert_eq!(string(&get(&phase, "phase"), "kind"), "practice");
    assert_eq!(num(&phase, "untilUnixMs"), 1_800_000_000_000.0);
    let npc = decode(
        &mut d,
        &ServerMsg::NpcEvent {
            id: 9,
            event: NpcEvent::Say { text: "Hi".into() },
        },
    );
    assert_eq!(string(&get(&npc, "event"), "kind"), "say");
    let cast = decode(
        &mut d,
        &ServerMsg::SpellCast {
            caster: 3,
            spell: 2,
            target: SpellTarget::Furniture { item_id: 77 },
        },
    );
    assert_eq!(num(&get(&cast, "target"), "itemId"), 77.0);
    let events = decode(
        &mut d,
        &ServerMsg::DuelEvents {
            duel_id: 1,
            at_ms: 50,
            events: vec![
                DuelEvent::Fight,
                DuelEvent::Launched {
                    id: 2,
                    side: DuelSide::A,
                    spell: 11,
                    from: [-550, 0],
                    to: [1850, 0],
                    speed_cms: 1400,
                },
            ],
        },
    );
    let list = Array::from(&get(&events, "events"));
    assert_eq!(string(&list.get(0), "kind"), "fight");
    assert_eq!(string(&list.get(1), "side"), "a");
    assert_eq!(vec3(&get(&list.get(1), "from")), vec![-550.0, 0.0]);
}

#[wasm_bindgen_test]
fn scores_gestures_and_tells_the_time() {
    let scorer =
        sim_wasm::GestureScorer::new(include_str!("../../../data/classes/gestures.json")).unwrap();
    // A tiny wiggle is no gesture.
    let tiny: Vec<i32> = (0..20).flat_map(|k| [30, k % 3, 0]).collect();
    let r = scorer.score(1, 600, 0, &tiny).unwrap();
    assert_eq!(string(&r, "rejected"), "too_small");
    assert_eq!(string(&r, "grade"), "miss");
    let clock =
        sim_wasm::GameClock::new(include_str!("../../../data/classes/schedule.json")).unwrap();
    let t = clock.class_times("charms", 1_800_000_000_000.0).unwrap();
    assert!(num(&get(&t, "next"), "startMs") > 1_800_000_000_000.0);
    assert!(
        ["morning", "afternoon", "night"].contains(&clock.period(1_800_000_000_000.0).as_str())
    );
    // The timetable by slot, and subject names.
    let slots = Array::from(&clock.timetable().unwrap());
    assert_eq!(slots.length(), 6);
    let eight = slots.iter().find(|s| string(s, "at") == "08:00").unwrap();
    let classes: Vec<String> = Array::from(&get(&eight, "classes"))
        .iter()
        .map(|c| c.as_string().unwrap())
        .collect();
    assert_eq!(classes, ["charms", "potions", "herbology"]);
    assert_eq!(string(&eight, "period"), "morning");
    assert_eq!(clock.subject_name("history"), "History of Magic");
}

#[wasm_bindgen_test]
fn previews_picks_and_encodes_them() {
    let picks =
        sim_wasm::PickScorer::new(include_str!("../../../data/classes/gestures.json")).unwrap();
    assert_eq!(num(&picks.preview(1_400, 0).unwrap(), "score"), 100.0);
    assert!(num(&picks.preview(1_400, 1_000).unwrap(), "score") < 100.0);
    assert_eq!(
        string(&picks.preview(1_400, 1_401).unwrap(), "rejected"),
        "bad_timing"
    );
    assert!(picks.in_window(1_400, -1_400) && !picks.in_window(1_400, 1_500));
    let bytes = sim_wasm::encode_pick_answer(2, 5, 3, -120);
    assert_eq!(
        protocol::decode_client(&bytes).unwrap(),
        protocol::ClientMsg::PickAnswer {
            round: 2,
            beat: 5,
            choice: 3,
            offset_ms: -120
        }
    );
}

/// The wayfinding trail's path search for one house.
#[wasm_bindgen_test]
fn wayfinder_offers_the_house_its_key_places_and_finds_the_way() {
    let layout = include_str!("../../../data/world/layout.json");
    let places = include_str!("../../../data/world/key_places.json");
    let w = sim_wasm::Wayfinder::new(layout, "skytalon", places, "[]").unwrap();
    let list = Array::from(&w.places().unwrap());
    let ids: Vec<String> = list.iter().map(|p| string(&p, "id")).collect();
    assert!(ids.contains(&"dorm_skytalon".to_string()));
    assert!(!ids.contains(&"dorm_roarwing".to_string()));
    let potions = list
        .iter()
        .find(|p| string(p, "id") == "class_potions")
        .unwrap();
    assert_eq!(string(&potions, "class"), "potions");
    assert_eq!(string(&potions, "group"), "class");
    let path = w.path_to("owl_exchange", 14.0, 0.0, 32.0).unwrap();
    assert!(path.len() >= 6 && path.len().is_multiple_of(3));
    assert_eq!(&path[..3], &[14.0, 0.0, 32.0]);
    let n = path.len();
    assert_eq!(&path[n - 3..], &[3.5, 0.0, 30.0]);
    assert!(w.path_to("dorm_roarwing", 14.0, 0.0, 32.0).is_none());
    assert!(w.path(14.0, 0.0, 32.0, 0.0, 500.0, 0.0).is_none());
    assert!(sim_wasm::Wayfinder::new(layout, "no_such_house", places, "[]").is_err());

    // The grounds' places, and the trail down the hill follows the ground.
    let hut = list
        .iter()
        .find(|p| string(p, "id") == "groundskeepers_cottage")
        .unwrap();
    assert_eq!(string(&hut, "group"), "grounds");
    let path = w.path_to("lake_shore", 14.0, 0.0, 32.0).unwrap();
    let n = path.len();
    assert!(
        path[n - 2] < -14.0,
        "the shore is near the water: {}",
        path[n - 2]
    );
    let world = SimWorld::new(layout, Some("skytalon".into())).unwrap();
    for p in path.chunks(3) {
        let g = world
            .ground_at(p[0], p[2], p[1])
            .expect("the trail is on the ground");
        assert!((g - p[1]).abs() < 0.01, "{p:?} against {g}");
    }
}

/// The heightfield grounds through the wasm world: heights between
/// samples, outdoors or not, the cell underfoot.
#[wasm_bindgen_test]
fn sim_world_walks_the_hilly_grounds() {
    let layout = include_str!("../../../data/world/layout.json");
    let world = SimWorld::new(layout, Some("roarwing".into())).unwrap();
    // The gate apron is level with the courtyard; the hill falls toward the lake.
    assert_eq!(world.ground_at(16.0, 96.0, 0.0), Some(0.0));
    assert!(world.outdoors_at(16.0, 96.0, 0.0));
    assert_eq!(
        world.cell_at(16.0, 96.0, 0.0).as_deref(),
        Some("grounds_r0_c3")
    );
    // North of the fountain: since the solid props (d5712ed) the fountain's
    // round basin at (16, 61.2) holds no ground, so it is not outdoors either.
    assert!(world.outdoors_at(16.0, 50.0, 0.0), "the courtyard");
    assert_eq!(world.cell_at(16.0, 50.0, 0.0).as_deref(), Some("courtyard"));
    assert_eq!(world.ground_at(16.0, 61.2, 0.0), None, "the fountain");
    assert!(!world.outdoors_at(16.0, 30.0, 0.0), "the Entrance Hall");
    assert_eq!(
        world.cell_at(16.0, 30.0, 0.0).as_deref(),
        Some("entrance_hall")
    );
    assert!(!world.outdoors_at(16.0, 600.0, 0.0), "the lake");
    // Walking downhill keeps the feet on the ground.
    let mut pos = vec![16.0f32, 0.0, 100.0];
    for _ in 0..600 {
        pos = world.move_and_slide(pos[0], pos[1], pos[2], 0.0, 0.1);
    }
    let g = world.ground_at(pos[0], pos[2], pos[1]).unwrap();
    assert!(
        pos[2] > 150.0 && pos[1] < -3.0 && (g - pos[1]).abs() < 1e-4,
        "{pos:?}"
    );
}

/// Flying through the wasm world with the server's code: mount on
/// the pitch, climb and fly, stop at the castle's no-fly box, land; the ring
/// course of the Flying class; the mount state and the ring beat decoded.
#[wasm_bindgen_test]
fn sim_world_flies_the_grounds_and_the_ring_course() {
    let layout = include_str!("../../../data/world/layout.json");
    let flying = include_str!("../../../data/flying.json");
    let mut world = SimWorld::new(layout, Some("burrowsby".into())).unwrap();
    assert!(world.has_airspace());
    assert!(
        world
            .fly_and_slide(10.0, -9.2, 200.0, 0.0, 0.1, 0.0, 1, 100)
            .is_err(),
        "rules first"
    );
    world.set_flight_rules(flying).unwrap();
    let p = world.fly_params(3, 115).unwrap();
    assert!((num(&p, "speed") - 11.5).abs() < 1e-6);
    assert_eq!(num(&p, "clearance"), 0.3);
    // Mount on the field: lifted to the clearance; refused in the Entrance Hall.
    let m = world.mount(10.0, -9.5, 200.0).unwrap();
    let mut pos: Vec<f32> = vec3(&get(&m, "pos")).iter().map(|&v| v as f32).collect();
    assert_eq!(pos, vec![10.0, -9.2, 200.0]);
    let hall = world.mount(16.0, 0.0, 30.0).unwrap();
    assert_eq!(string(&hall, "refused"), "no_fly");
    // Climb, then north at the castle: stopped at its margin, never corrected.
    let mut accepted = pos.clone();
    for frame in 1..=1_200 {
        let (dy, dz) = if frame < 300 {
            (0.06, 0.0)
        } else {
            (0.0, -0.15)
        };
        pos = world
            .fly_and_slide(pos[0], pos[1], pos[2], 0.0, dy, dz, 3, 100)
            .unwrap();
        if frame % 6 == 0 {
            let verdict = world
                .check_flight(
                    accepted[0],
                    accepted[1],
                    accepted[2],
                    pos[0],
                    pos[1],
                    pos[2],
                    0.1,
                    3,
                    100,
                )
                .unwrap();
            assert_eq!(verdict, None, "{pos:?}");
            accepted = pos.clone();
        }
    }
    assert!(pos[2] > 100.0 && pos[2] < 100.5 && pos[1] > 5.0, "{pos:?}");
    assert_eq!(
        world
            .check_flight(pos[0], pos[1], pos[2], pos[0], pos[1], 99.0, 1.0, 3, 100)
            .unwrap()
            .as_deref(),
        Some("no_fly")
    );
    let floor = world.floor_at(pos[0], pos[2], pos[1]).unwrap();
    assert!(floor < pos[1] - 4.0);
    // Down to the ground and land.
    for _ in 0..600 {
        pos = world
            .fly_and_slide(pos[0], pos[1], pos[2], 0.0, -0.06, 0.0, 3, 100)
            .unwrap();
    }
    let landed = world
        .dismount(pos[0], pos[1], pos[2])
        .unwrap()
        .expect("low enough to land");
    assert!(
        (pos[1] - landed[1] - 0.3).abs() < 1e-3,
        "{pos:?} {landed:?}"
    );
    assert!(
        world.dismount(10.0, 20.0, 200.0).unwrap().is_none(),
        "too high"
    );

    // The Flying class's ring course.
    let class = SimWorld::new(
        include_str!("../../../data/world/instances/class_pitch.json"),
        None,
    )
    .unwrap();
    let rings = Array::from(&class.rings().unwrap());
    assert_eq!(rings.length(), 8);
    let first = rings.get(0);
    let at = vec3(&get(&first, "pos"));
    assert_eq!(string(&first, "id"), "ring_0");
    let (x, y, z) = (at[0] as f32, at[1] as f32, at[2] as f32);
    assert!(class.passes_ring(0, x - 1.0, y, z, x + 1.0, y, z));
    assert!(!class.passes_ring(0, x - 1.0, y + 4.0, z, x + 1.0, y + 4.0, z));
    assert!(!class.passes_ring(99, x - 1.0, y, z, x + 1.0, y, z));

    // The mount state and a ring beat, decoded.
    let mut d = ServerDecoder::new();
    let msg = decode(
        &mut d,
        &ServerMsg::Mounted {
            on: false,
            tier: 0,
            reason: Some(protocol::MountRefused::NotLanded),
        },
    );
    assert_eq!(string(&msg, "type"), "mounted");
    assert_eq!(string(&msg, "reason"), "not_landed");
    let round = decode(
        &mut d,
        &ServerMsg::LessonRound {
            round: 1,
            window_ms: 2_000,
            beats: vec![protocol::LessonBeat {
                beat: 0,
                prompt: protocol::BeatPrompt::Ring { ring: 3 },
                at_unix_ms: 1_790_000_000_000,
            }],
        },
    );
    let beat = Array::from(&get(&round, "beats")).get(0);
    let prompt = get(&beat, "prompt");
    assert_eq!(string(&prompt, "kind"), "ring");
    assert_eq!(num(&prompt, "ring"), 3.0);
    let own = decode(
        &mut d,
        &ServerMsg::OwnEffects {
            effects: Vec::new(),
            speed_pct: 100,
            broom_pct: 115,
        },
    );
    assert_eq!(num(&own, "broomPct"), 115.0);
    // A flying entity's delta counts 2 cm units.
    let base = EntityState {
        id: 70_001,
        pos: QPos::from_m([10.0, 5.0, 200.0]),
        yaw: 0,
        anim: Anim::Fly,
    };
    let snap =
        |full: Vec<EntityState>, deltas: Vec<EntityDelta>, entered: bool| ServerMsg::Snapshot {
            tick: 1,
            entered: if entered {
                vec![EntityEntry::Full(EntityInfo {
                    id: 70_001,
                    character: CharacterInfo {
                        name: "Ada".into(),
                        house: House::Skytalon,
                    },
                    appearance_version: 1,
                    appearance: Appearance::default(),
                    effects_version: 0,
                    effects: Vec::new(),
                    level: 1,
                    npc: None,
                })]
            } else {
                Vec::new()
            },
            full,
            deltas,
            left: Vec::new(),
            looks: Vec::new(),
        };
    decode(&mut d, &snap(vec![base], Vec::new(), true));
    let next = EntityState {
        pos: QPos::from_m([12.3, 5.0, 200.0]),
        ..base
    };
    let delta = EntityDelta::between(0, &base, &next).unwrap();
    let out = decode(&mut d, &snap(Vec::new(), vec![delta], false));
    let s = Array::from(&get(&out, "states")).get(0);
    assert_eq!(num(&s, "x"), 12.3);
    assert_eq!(string(&s, "anim"), "fly");
}

/// The moving staircases from the clock, no message: the state at
/// an instant, the flights' views, the world switching with the state, a
/// rider carried, and the trail re-planning in the new state.
#[wasm_bindgen_test]
fn staircases_follow_the_clock() {
    let layout = include_str!("../../../data/world/layout.json");
    let stairs = include_str!("../../../data/world/stairs.json");
    let mut w = SimWorld::new(layout, Some("roarwing".into())).unwrap();
    assert_eq!(w.stair_state(1000.0), 0, "no schedule yet");
    w.set_stairs(stairs, None).unwrap();
    // At the epoch flights 5-6 and 6-7 still rest at B (their last switches).
    assert_eq!(w.stair_state(1000.0), 0b110);
    assert_eq!(w.stair_state(121_000.0), 0);
    assert_eq!(w.states_near(180_500.0), vec![1, 0]);
    let flights = w.flight_states(177_000.0).unwrap();
    let f0 = Array::from(&flights).get(0);
    assert_eq!(string(&f0, "id"), "flight_3_4");
    assert_eq!(string(&f0, "phase"), "swing");
    assert!((num(&f0, "progress") - 0.5).abs() < 1e-6);
    assert!((num(&f0, "turnDeg") + 45.0).abs() < 1e-3);
    assert_eq!(Array::from(&get(&f0, "gates")).length(), 4);
    assert_eq!(Array::from(&w.stair_flights().unwrap()).length(), 3);
    // Flight 6-7 at B: its foot on the sixth-floor north gallery is walkable.
    let foot = [-39.75f32, 54.0, -11.8];
    assert!(
        w.move_and_slide(foot[0], foot[1], foot[2], 0.0, 0.6)[2] < -11.6,
        "state 0: the gallery ends"
    );
    w.set_stair_state(0b100);
    assert_eq!(w.current_stair_state(), 0b100);
    let up = w.move_and_slide(foot[0], foot[1], foot[2], 0.0, 0.6);
    assert!(up[2] > -11.3 && up[1] > 54.0, "{up:?}");
    // A rider on A is carried onto B, and back (each carry in the world
    // predicted before its switch: who rides is whose ground is the flight).
    let on_a = [-45.75f32, 54.0 + 9.0 * 8.0 / 16.0, -9.5];
    assert!(
        w.carry(on_a[0], on_a[1], on_a[2], 2, "a", "b").is_none(),
        "A is not walked in state 0b100"
    );
    w.set_stair_state(0);
    let b = w.carry(on_a[0], on_a[1], on_a[2], 2, "a", "b").unwrap();
    assert!((b[1] - on_a[1]).abs() < 1e-3, "{b:?}");
    w.set_stair_state(0b100);
    let a = w.carry(b[0], b[1], b[2], 2, "b", "a").unwrap();
    assert!(
        (a[0] - on_a[0]).abs() < 1e-3 && (a[2] - on_a[2]).abs() < 1e-3,
        "{a:?}"
    );
    assert!(w.carry(0.0, 0.0, 30.0, 2, "a", "b").is_none());
    // The trail to the Roarwing dorm crosses flight 6-7, in either state.
    let places = include_str!("../../../data/world/key_places.json");
    let mut wf = sim_wasm::Wayfinder::new(layout, "roarwing", places, "[]").unwrap();
    let flights_a = wf.path_flights("dorm_roarwing", 14.0, 0.0, 32.0);
    assert_ne!(flights_a & 0b100, 0, "{flights_a:b}");
    wf.set_stair_state(0b111).unwrap();
    assert_eq!(wf.stair_state(), 0b111);
    assert_ne!(wf.path_flights("dorm_roarwing", 14.0, 0.0, 32.0) & 0b100, 0);
    let path = wf.path_to("dorm_roarwing", 14.0, 0.0, 32.0).unwrap();
    assert!(path.len() > 6);
    // The development period.
    w.set_stairs(stairs, Some(20)).unwrap();
    assert_eq!(w.stair_state(20_500.0), 0b001);
}

/// The secrets and the map: reveals, the secret a surface belongs
/// to, the map surfaces, the Wishing Room's rules, the new messages
/// both ways.
#[wasm_bindgen_test]
fn secrets_the_map_and_the_room() {
    let layout = include_str!("../../../data/world/layout.json");
    let secrets = include_str!("../../../data/world/secrets.json");
    let mut w = SimWorld::new(layout, Some("skytalon".into())).unwrap();
    assert!(
        w.secret_of("sp_tapestry_stair").is_none(),
        "before setSecrets"
    );
    w.set_secrets(secrets).unwrap();
    assert_eq!(
        w.reveal_at(-70.0, 45.0, 8.0).as_deref(),
        Some("sp_tapestry_door_l5")
    );
    assert_eq!(
        w.secret_of("sp_tapestry_door_l5").as_deref(),
        Some("weavers_stair")
    );
    assert!(w.secret_of("l5_corridor_west").is_none());
    assert_eq!(Array::from(&w.secrets().unwrap()).length(), 5);
    let surfaces = Array::from(&w.map_surfaces().unwrap());
    let secret = surfaces
        .iter()
        .filter(|s| get(s, "secret").as_bool() == Some(true))
        .count();
    assert_eq!(secret, 29);
    let req = w.wishing_room().unwrap();
    assert_eq!(string(&req, "door"), "wishing_room_door");
    // The Monstra Viam trail never routes over a secret (the library to the Charms door).
    let places = include_str!("../../../data/world/key_places.json");
    let wf = sim_wasm::Wayfinder::new(layout, "skytalon", places, "[]").unwrap();
    let path = wf.path(-103.0, 18.0, 6.0, -80.0, 27.0, -1.0).unwrap();
    for p in path.chunks(3) {
        let here = w.reveal_at(p[0], p[1], p[2]).unwrap_or_default();
        assert!(w.secret_of(&here).is_none(), "{here}");
    }
    // Encoded and decoded.
    let bytes = sim_wasm::encode_enter_wishing_room("gathering").unwrap();
    assert_eq!(
        decode_client(&bytes).unwrap(),
        ClientMsg::EnterWishingRoom {
            room: protocol::WishingRoomKind::Gathering
        }
    );
    assert!(sim_wasm::encode_enter_wishing_room("attic").is_err());
    let mut d = ServerDecoder::new();
    let door = decode(
        &mut d,
        &ServerMsg::WishingRoomDoor {
            open_until_ms: 5_000,
        },
    );
    assert_eq!(string(&door, "type"), "wishingRoomDoor");
    assert_eq!(num(&door, "openUntilMs"), 5_000.0);
    let found = decode(
        &mut d,
        &ServerMsg::Discovered {
            secret: "weavers_stair".into(),
            xp: 100,
            house_point: true,
            recipe: Some(20),
        },
    );
    assert_eq!(string(&found, "type"), "discovered");
    assert_eq!(string(&found, "secret"), "weavers_stair");
    assert_eq!(get(&found, "housePoint").as_bool(), Some(true));
    assert_eq!(num(&found, "recipe"), 20.0);
    let zone = decode(
        &mut d,
        &ServerMsg::ZoneChanged {
            zone: Zone::WishingRoom {
                owner_name: "Ada".into(),
                room: protocol::WishingRoomKind::LostProperty,
                layout: "lost_property_vault".into(),
            },
            spawn: QPos::from_m([0.0, 0.0, 8.0]),
            yaw: 128,
        },
    );
    let z = get(&zone, "zone");
    assert_eq!(string(&z, "kind"), "wishing_room");
    assert_eq!(string(&z, "room"), "lost_property");
    assert_eq!(string(&z, "ownerName"), "Ada");
}

/// The wear rule as the client calls it.
#[wasm_bindgen_test]
fn can_wear_is_the_servers_rule() {
    assert!(can_wear("male", None));
    assert!(can_wear("female", None));
    assert!(can_wear("female", Some("female".into())));
    assert!(!can_wear("male", Some("female".into())));
    assert!(!can_wear("female", Some("male".into())));
    assert!(!can_wear("other", Some("male".into())));
    assert!(!can_wear("male", Some("unknown".into())));
}

/// Protocol v12 and the NPC book: talking to an NPC, the reply, and the
/// client-side lines and portraits.
#[wasm_bindgen_test]
fn npcs_talk_reply_lines_and_portraits() {
    let bytes = sim_wasm::encode_npc_interact(0x0004_0021);
    assert_eq!(
        decode_client(&bytes).unwrap(),
        ClientMsg::NpcInteract { id: 0x0004_0021 }
    );
    let mut d = ServerDecoder::new();
    let gift = decode(
        &mut d,
        &ServerMsg::NpcReply {
            id: 9,
            reply: protocol::NpcReply::Gift {
                gift: protocol::NpcGift::HousePoint { points: 1 },
            },
        },
    );
    assert_eq!(string(&gift, "type"), "npcReply");
    assert_eq!(num(&gift, "id"), 9.0);
    let reply = get(&gift, "reply");
    assert_eq!(string(&reply, "kind"), "gift");
    let g = get(&reply, "gift");
    assert_eq!(string(&g, "kind"), "housePoint");
    assert_eq!(num(&g, "points"), 1.0);
    let refused = decode(
        &mut d,
        &ServerMsg::NpcReply {
            id: 9,
            reply: protocol::NpcReply::Refused {
                code: protocol::NpcErrorCode::TooFar,
            },
        },
    );
    let r = get(&refused, "reply");
    assert_eq!(string(&r, "kind"), "refused");
    assert_eq!(string(&r, "code"), "too_far");
    let talk = decode(
        &mut d,
        &ServerMsg::NpcReply {
            id: 9,
            reply: protocol::NpcReply::Talk { given_today: true },
        },
    );
    assert_eq!(
        get(&get(&talk, "reply"), "givenToday").as_bool(),
        Some(true)
    );

    let book = sim_wasm::NpcBook::new(
        include_str!("../../../data/npcs/roster.json"),
        include_str!("../../../data/npcs/looks.json"),
        include_str!("../../../data/npcs/portraits.json"),
        include_str!("../../../data/npcs/lines.json"),
        include_str!("../../../data/world/layout.json"),
    )
    .unwrap();
    let portraits = Array::from(&book.portraits().unwrap());
    assert!((15..=20).contains(&portraits.length()));
    let first = portraits.get(0);
    assert_eq!(Array::from(&get(&first, "pos")).length(), 3);
    let npc = string(&first, "npc");
    assert_eq!(string(&book.npc(&npc).unwrap(), "kind"), "portrait");
    assert!(book.npc("charms_professor").unwrap().is_undefined());
    assert!(num(&book.look(&npc), "bodyType") <= 1.0);
    assert!(num(&book.rules().unwrap(), "pass_cooldown_s") >= 180.0);
    let cx =
        js_sys::JSON::parse(r#"{ "house": "skytalon", "period": "morning", "foundSecrets": [] }"#)
            .unwrap();
    let i = book
        .pick(&npc, "talk", cx.clone(), 7, vec![])
        .unwrap()
        .unwrap();
    assert!(!book.text(&npc, i).unwrap().is_empty());
    assert_eq!(
        book.pick(&npc, "talk", cx.clone(), 7, vec![]).unwrap(),
        Some(i)
    );
    assert!(
        book.pick(&npc, "shop", cx.clone(), 7, vec![])
            .unwrap()
            .is_none()
    );
    assert!(book.pick(&npc, "nonsense", cx.clone(), 7, vec![]).is_err());
    // A student says the shared pool's lines.
    let student = Array::from(&book.npcs().unwrap())
        .iter()
        .find(|n| string(n, "kind") == "student")
        .unwrap();
    let id = string(&student, "id");
    assert!(
        book.pick(&id, "talk", cx.clone(), 1, vec![])
            .unwrap()
            .is_some()
    );
    // Today's hint, the same all day.
    let hinter = Array::from(&book.npcs().unwrap())
        .iter()
        .find(|n| get(n, "daily_hint").as_bool() == Some(true))
        .unwrap();
    let h = string(&hinter, "id");
    let a = book.daily_hint(&h, cx.clone(), 1.8e12, 42.0).unwrap();
    assert_eq!(
        a,
        book.daily_hint(&h, cx, 1.8e12 + 3_600_000.0, 42.0).unwrap()
    );
}
