//! Potions end to end: drinking over the WebSocket uses the
//! potion up and starts its effect for everyone to see, effects survive a
//! relog, Mimic Draught copies a consenting friend's look, Chatterbrew scrambles
//! chat, and two drinks of one last potion never both succeed.

#[path = "effects_harness.rs"]
mod harness;

use harness::{Client, TestServer};
use protocol::{
    Appearance, ChatChannel, ClientMsg, EffectKind, PotionErrorCode, ServerMsg, VisibleEffect,
};
use server::catalog::shipped;

fn potion(kind: EffectKind) -> u16 {
    shipped()
        .catalog
        .items
        .iter()
        .find(|i| i.potion.is_some_and(|p| p.effect == kind))
        .unwrap()
        .id
}

async fn drank(c: &mut Client) -> Result<EffectKind, PotionErrorCode> {
    c.expect(|m| match m {
        ServerMsg::Drank { effect, .. } => Some(Ok(*effect)),
        ServerMsg::PotionError { code, .. } => Some(Err(*code)),
        _ => None,
    })
    .await
}

async fn own_effects(c: &mut Client) -> (Vec<EffectKind>, u16) {
    c.expect(|m| match m {
        ServerMsg::OwnEffects {
            effects, speed_pct, ..
        } => Some((effects.iter().map(|e| e.kind).collect(), *speed_pct)),
        _ => None,
    })
    .await
}

#[tokio::test]
async fn drinking_uses_the_potion_shows_the_effect_and_survives_a_relog() {
    let server = TestServer::start().await;
    let ada = server.bot().await;
    let glow = server
        .give(ada.id, potion(EffectKind::Glow), 1, Some([34, 0, 0]))
        .await;
    let pick_me_up_tonic = server
        .give(ada.id, potion(EffectKind::PickMeUpTonic), 1, None)
        .await;
    let mimic_draught = server
        .give(ada.id, potion(EffectKind::MimicDraught), 1, None)
        .await;
    let mut a = server.join(&ada).await;
    assert_eq!(own_effects(&mut a).await, (vec![], 100));

    a.send(&ClientMsg::Drink { item_id: glow }).await;
    assert_eq!(drank(&mut a).await, Ok(EffectKind::Glow));
    assert_eq!(own_effects(&mut a).await, (vec![EffectKind::Glow], 100));
    assert_eq!(server.units(ada.id, potion(EffectKind::Glow)).await, 0);
    let consumed: i64 = sqlx::query_scalar(
        "SELECT count(*) FROM item_ledger WHERE item_id = $1 AND op = 'consume' AND reason = 'drink'",
    )
    .bind(glow)
    .fetch_one(server.pool())
    .await
    .unwrap();
    assert_eq!(consumed, 1);
    // Gone now.
    a.send(&ClientMsg::Drink { item_id: glow }).await;
    assert_eq!(drank(&mut a).await, Err(PotionErrorCode::ItemNotFound));
    // Mimic Draught needs a friend.
    a.send(&ClientMsg::Drink {
        item_id: mimic_draught,
    })
    .await;
    assert_eq!(drank(&mut a).await, Err(PotionErrorCode::NeedsTarget));
    assert_eq!(
        server.units(ada.id, potion(EffectKind::MimicDraught)).await,
        1,
        "kept"
    );
    // Pick-Me-Up Tonic: the speed the server validates with.
    a.send(&ClientMsg::Drink {
        item_id: pick_me_up_tonic,
    })
    .await;
    assert_eq!(drank(&mut a).await, Ok(EffectKind::PickMeUpTonic));
    let speed = shipped().economy.effects.pick_me_up_tonic.speed_pct;
    assert_eq!(
        own_effects(&mut a).await,
        (vec![EffectKind::Glow, EffectKind::PickMeUpTonic], speed)
    );

    // A relog brings both back, with their timers.
    a.close().await;
    let mut a = server.join(&ada).await;
    let (kinds, pct) = own_effects(&mut a).await;
    assert_eq!(kinds, vec![EffectKind::Glow, EffectKind::PickMeUpTonic]);
    assert_eq!(pct, speed);
    let effects = server.get(&ada, "/api/effects").await;
    let listed: Vec<&str> = effects["effects"]
        .as_array()
        .unwrap()
        .iter()
        .map(|e| e["kind"].as_str().unwrap())
        .collect();
    assert_eq!(listed.len(), 2);
    assert!(listed.contains(&"glow") && listed.contains(&"pick_me_up_tonic"));
    let active = server::effects::load_active(server.pool(), ada.id)
        .await
        .unwrap();
    assert!(
        active
            .iter()
            .any(|e| e.visible == Some(VisibleEffect::Glow { colour: 34 }))
    );
}

#[tokio::test]
async fn mimic_draught_copies_a_consenting_friends_look_and_babbling_scrambles_chat() {
    let server = TestServer::start().await;
    let ada = server.bot().await;
    let bea = server.bot().await;
    server.befriend(ada.id, bea.id).await;
    let mimic_draught = server
        .give(ada.id, potion(EffectKind::MimicDraught), 2, None)
        .await;
    let babbling = server
        .give(ada.id, potion(EffectKind::Babbling), 1, None)
        .await;
    let mut a = server.join(&ada).await;
    let mut b = server.join(&bea).await;
    let bea_look = b.appearance;

    let ask = ClientMsg::MimicDraughtAsk {
        item_id: mimic_draught,
        target: b.id,
    };
    // Bea declines first: nothing is drunk.
    a.send(&ask).await;
    let request = b
        .expect(|m| match m {
            ServerMsg::MimicDraughtRequest {
                request_id, from, ..
            } => Some((*request_id, *from)),
            _ => None,
        })
        .await;
    assert_eq!(request.1, a.id);
    b.send(&ClientMsg::MimicDraughtAnswer {
        request_id: request.0,
        accept: false,
    })
    .await;
    let by = a
        .expect(|m| match m {
            ServerMsg::MimicDraughtDeclined { by_name } => Some(by_name.clone()),
            _ => None,
        })
        .await;
    assert_eq!(by, bea.name);
    assert_eq!(
        server.units(ada.id, potion(EffectKind::MimicDraught)).await,
        2
    );

    // Then she agrees: Ada wears Bea's look, which Bea sees on Ada.
    a.send(&ask).await;
    let request_id = b
        .expect(|m| match m {
            ServerMsg::MimicDraughtRequest { request_id, .. } => Some(*request_id),
            _ => None,
        })
        .await;
    b.send(&ClientMsg::MimicDraughtAnswer {
        request_id,
        accept: true,
    })
    .await;
    assert_eq!(drank(&mut a).await, Ok(EffectKind::MimicDraught));
    let a_id = a.id;
    let seen = b
        .expect(|m| match m {
            ServerMsg::EffectsChanged { id, effects, .. } if *id == a_id => Some(effects.clone()),
            _ => None,
        })
        .await;
    assert_eq!(
        seen,
        vec![VisibleEffect::Disguise {
            appearance: bea_look
        }]
    );
    assert_eq!(
        server.units(ada.id, potion(EffectKind::MimicDraught)).await,
        1
    );

    // Chatterbrew: Bea hears Ada's line scrambled, the same length.
    a.send(&ClientMsg::Drink { item_id: babbling }).await;
    assert_eq!(drank(&mut a).await, Ok(EffectKind::Babbling));
    let line = "Meet me by the greenhouses tonight";
    a.send(&ClientMsg::Chat {
        channel: ChatChannel::Say,
        text: line.into(),
        client_seq: 1,
    })
    .await;
    let heard = b
        .expect(|m| match m {
            ServerMsg::Chat { from, text, .. } if *from == a_id => Some(text.clone()),
            _ => None,
        })
        .await;
    assert_ne!(heard, line);
    assert_eq!(heard.len(), line.len());
    assert_eq!(heard.matches(' ').count(), line.matches(' ').count());
}

#[tokio::test]
async fn two_drinks_of_the_last_potion_drink_it_once() {
    let server = TestServer::start().await;
    let ada = server.bot().await;
    let pool = server.pool().clone();
    for round in 0..5 {
        let item = server
            .give(ada.id, potion(EffectKind::Swelling), 1, None)
            .await;
        let (one, two) = tokio::join!(
            server::potions::drink(&pool, ada.id, item, None),
            server::potions::drink(&pool, ada.id, item, None),
        );
        let oks = [&one, &two].iter().filter(|r| r.is_ok()).count();
        assert_eq!(oks, 1, "round {round}: {one:?} / {two:?}");
        let err = if one.is_ok() { two } else { one };
        assert_eq!(err.unwrap_err(), PotionErrorCode::ItemNotFound);
        assert_eq!(server.units(ada.id, potion(EffectKind::Swelling)).await, 0);
    }
    let drinks: i64 = sqlx::query_scalar(
        "SELECT count(*) FROM item_ledger
         WHERE to_owner IS NULL AND from_owner = $1 AND reason = 'drink'",
    )
    .bind(ada.id)
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(drinks, 5);
    // One row per kind: swelling, restarted each time.
    let rows: i64 =
        sqlx::query_scalar("SELECT count(*) FROM active_effects WHERE character_id = $1")
            .bind(ada.id)
            .fetch_one(&pool)
            .await
            .unwrap();
    assert_eq!(rows, 1);
    // Diminishing Draught replaces it, and Invisibility ends a Mimic Draught.
    let small = server
        .give(ada.id, potion(EffectKind::Shrinking), 1, None)
        .await;
    let drunk = server::potions::drink(&pool, ada.id, small, None)
        .await
        .unwrap();
    assert_eq!(drunk.ended, vec![EffectKind::Swelling]);
    let kinds: Vec<String> =
        sqlx::query_scalar("SELECT kind FROM active_effects WHERE character_id = $1")
            .bind(ada.id)
            .fetch_all(&pool)
            .await
            .unwrap();
    assert_eq!(kinds, vec!["shrinking".to_owned()]);
    let disguise = server
        .give(ada.id, potion(EffectKind::MimicDraught), 1, None)
        .await;
    server::potions::drink(&pool, ada.id, disguise, Some(Appearance::default()))
        .await
        .unwrap();
    let hide = server
        .give(ada.id, potion(EffectKind::Invisibility), 1, None)
        .await;
    let drunk = server::potions::drink(&pool, ada.id, hide, None)
        .await
        .unwrap();
    assert_eq!(drunk.ended, vec![EffectKind::MimicDraught]);
    let mimic_draught: i64 = sqlx::query_scalar(
        "SELECT count(*) FROM active_effects WHERE character_id = $1 AND kind = 'mimic_draught'",
    )
    .bind(ada.id)
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(mimic_draught, 0);
}
