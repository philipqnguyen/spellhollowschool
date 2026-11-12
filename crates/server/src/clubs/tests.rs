use protocol::{ClubErrorCode as E, ClubRole as R, House};

use super::*;

fn filter() -> WordFilter {
    WordFilter::from_list("badword")
}

fn m(id: i64, role: R) -> Member {
    Member {
        character_id: id,
        name: format!("M{id}"),
        house: House::Burrowsby,
        level: 5,
        role,
    }
}

/// Owner 1, officers 2 and 3, members 4 and 5, in that joining order.
fn club() -> Club {
    Club {
        id: 9,
        name: "Chess Club".into(),
        tag: "CHS".into(),
        motd: String::new(),
        version: 1,
        members: vec![
            m(1, R::Owner),
            m(2, R::Officer),
            m(3, R::Officer),
            m(4, R::Member),
            m(5, R::Member),
        ],
    }
}

#[test]
fn shipped_rules_load() {
    let r = rules();
    assert_eq!(r.found_level, 4);
    assert_eq!(r.found_crowns, 150);
    assert_eq!(r.members_max, 100);
    assert_eq!(r.secretary, "clerk_clubs");
    let roster = &crate::catalog::shipped().npcs.roster;
    let secretary = roster
        .get(&r.secretary)
        .expect("the secretary is on the roster");
    assert_eq!(secretary.kind, sim_core::npcs::NpcKind::Clerk);
}

/// The secretary's lines, filled with the shipped numbers, are the
/// approved words exactly.
#[test]
fn secretary_lines_fill_to_the_approved_words() {
    let r = rules();
    let v = |pairs: &[(&'static str, &str)]| -> Vec<(&'static str, String)> {
        pairs.iter().map(|(k, v)| (*k, v.to_string())).collect()
    };
    assert_eq!(
        r.line("greeting", &[]).unwrap(),
        "Clubs and Societies! Come to start one of your own, or just curious what's on offer?"
    );
    assert_eq!(r.line("option_found", &[]).unwrap(), "Found a club");
    assert_eq!(
        r.line("option_list", &[]).unwrap(),
        "Tell me about the clubs"
    );
    assert_eq!(r.line("option_goodbye", &[]).unwrap(), "Goodbye");
    assert_eq!(
        r.line("not_level", &v(&[("level", "2")])).unwrap(),
        "Oh, I do love the enthusiasm! But the school only grants charters from level 4, and you're level 2 now. A few more lessons and you'll be there. Do come back and see me then."
    );
    assert_eq!(
        r.line("not_crowns", &v(&[("crowns", "120"), ("short", "30")]))
            .unwrap(),
        "Your level is just right for a charter. It's the fee that's the trouble: founding a club costs 150 Crowns, and you have 120. That's 30 Crowns to go. Lessons pay well, and so does the Owl Exchange. I'll keep a blank charter ready for you."
    );
    assert_eq!(
        r.line("not_either", &v(&[("level", "3"), ("crowns", "40")]))
            .unwrap(),
        "Not just yet, I'm afraid. A charter needs two things: you must be level 4 (you're level 3), and the fee is 150 Crowns (you have 40). Keep attending your lessons. They'll take care of both, and I'll be right here when you're ready."
    );
    assert_eq!(
        r.line("in_club", &v(&[("club", "The Night Owls")]))
            .unwrap(),
        "You already belong to The Night Owls! A student may only be a member of one club. If you'd like to start your own, you'll need to leave that one first."
    );
    assert_eq!(
        r.line("eligible", &[]).unwrap(),
        "Splendid, everything is in order. The fee is 150 Crowns, paid when the charter is sealed. Now, what will your club be called, and what short tag should go beside your members' names?"
    );
    assert_eq!(
        r.line("sealed", &v(&[("club", "The Night Owls")])).unwrap(),
        "There we are: The Night Owls is officially on the school's register! Invite your friends, and may it flourish."
    );
    for line in r.secretary_lines.values() {
        assert!(
            !line.to_lowercase().contains("year"),
            "never school years: {line}"
        );
    }
}

#[test]
fn rules_refuse_broken_files() {
    let good: serde_json::Value = serde_json::from_str(CLUBS_JSON).unwrap();
    let with = |f: &dyn Fn(&mut serde_json::Value)| {
        let mut v = good.clone();
        f(&mut v);
        ClubRules::from_json(&v.to_string())
    };
    assert!(with(&|_| {}).is_ok());
    assert!(with(&|v| v["found_level"] = 0.into()).is_err());
    assert!(with(&|v| v["members_max"] = 1.into()).is_err());
    assert!(with(&|v| v["tag_chars"] = serde_json::json!([4, 2])).is_err());
    assert!(with(&|v| v["secretary_lines"]["sealed"] = "{nope}".into()).is_err());
    assert!(
        with(&|v| {
            v["secretary_lines"]
                .as_object_mut()
                .unwrap()
                .remove("eligible");
        })
        .is_err()
    );
}

#[test]
fn names_tags_and_messages() {
    let (r, f) = (rules(), filter());
    assert_eq!(
        clean_name("  The   Night Owls ", r, &f).unwrap(),
        "The Night Owls"
    );
    assert_eq!(
        clean_name("Knights & Dragons", r, &f).unwrap(),
        "Knights & Dragons"
    );
    assert_eq!(
        clean_name("O'Brien's 2nd-Floor", r, &f).unwrap(),
        "O'Brien's 2nd-Floor"
    );
    assert_eq!(clean_name("Café Crème", r, &f).unwrap(), "Café Crème");
    assert_eq!(clean_name("Ab", r, &f), Err(E::NameInvalid));
    assert_eq!(clean_name(&"a".repeat(25), r, &f), Err(E::NameInvalid));
    assert!(clean_name(&"a".repeat(24), r, &f).is_ok());
    assert_eq!(clean_name("-Owls", r, &f), Err(E::NameInvalid));
    assert_eq!(clean_name("Owls!", r, &f), Err(E::NameInvalid));
    assert_eq!(clean_name("Ow--ls", r, &f), Err(E::NameInvalid));
    assert_eq!(clean_name("Owls\u{200B}Club", r, &f), Err(E::NameInvalid));
    assert_eq!(clean_name("The Badword Club", r, &f), Err(E::Filtered));

    assert_eq!(clean_tag(" owl ", r, &f).unwrap(), "OWL");
    assert_eq!(clean_tag("ab12", r, &f).unwrap(), "AB12");
    assert_eq!(clean_tag("A", r, &f), Err(E::TagInvalid));
    assert_eq!(clean_tag("ABCDE", r, &f), Err(E::TagInvalid));
    assert_eq!(clean_tag("A B", r, &f), Err(E::TagInvalid));
    assert_eq!(clean_tag("ÉÉ", r, &f), Err(E::TagInvalid));
    let f4 = WordFilter::from_list("ugh");
    assert_eq!(clean_tag("UGH", r, &f4), Err(E::Filtered));

    assert_eq!(clean_motd("   ", r, &f).unwrap(), "");
    assert_eq!(
        clean_motd(" Meet\nat the lake ", r, &f).unwrap(),
        "Meet at the lake"
    );
    assert_eq!(clean_motd(&"a".repeat(201), r, &f), Err(E::MotdInvalid));
    assert_eq!(clean_motd("a badword here", r, &f), Err(E::Filtered));
    assert_eq!(name_key("Night OWLS"), "night owls");
}

/// Each action against each rank: owner, officer, member, and outsiders.
#[test]
fn ranks_and_what_they_may_do() {
    let c = club();
    // Inviting: officers and the owner.
    assert_eq!(c.check_invite(1, 100), Ok(()));
    assert_eq!(c.check_invite(2, 100), Ok(()));
    assert_eq!(c.check_invite(4, 100), Err(E::NotAllowed));
    assert_eq!(c.check_invite(99, 100), Err(E::NotInClub));
    assert_eq!(c.check_invite(1, 5), Err(E::Full));
    // Removing: officers remove members, the owner anyone else.
    assert_eq!(c.check_kick(2, 4), Ok(()));
    assert_eq!(
        c.check_kick(2, 3),
        Err(E::NotAllowed),
        "not another officer"
    );
    assert_eq!(c.check_kick(2, 1), Err(E::NotAllowed), "not the owner");
    assert_eq!(c.check_kick(1, 2), Ok(()));
    assert_eq!(c.check_kick(1, 5), Ok(()));
    assert_eq!(c.check_kick(4, 5), Err(E::NotAllowed));
    assert_eq!(c.check_kick(1, 1), Err(E::NotMember));
    assert_eq!(c.check_kick(1, 99), Err(E::NotMember));
    // Officers: the owner appoints and dismisses.
    assert_eq!(c.check_set_role(1, 4, R::Officer), Ok(()));
    assert_eq!(c.check_set_role(1, 2, R::Member), Ok(()));
    assert_eq!(c.check_set_role(2, 4, R::Officer), Err(E::NotAllowed));
    assert_eq!(c.check_set_role(1, 4, R::Owner), Err(E::NotAllowed));
    assert_eq!(c.check_set_role(1, 1, R::Member), Err(E::NotMember));
    // Handing over, renaming, disbanding: the owner.
    assert_eq!(c.check_transfer(1, 5), Ok(()));
    assert_eq!(c.check_transfer(2, 5), Err(E::NotAllowed));
    assert_eq!(c.check_transfer(1, 1), Err(E::NotMember));
    assert_eq!(c.check_owner(1), Ok(()));
    assert_eq!(c.check_owner(2), Err(E::NotAllowed));
    assert_eq!(c.check_owner(4), Err(E::NotAllowed));
    // The message of the day: officers and the owner.
    assert_eq!(c.check_motd(3), Ok(()));
    assert_eq!(c.check_motd(1), Ok(()));
    assert_eq!(c.check_motd(5), Err(E::NotAllowed));
}

#[test]
fn ownership_passes_to_the_longest_serving_officer_else_member() {
    let mut c = club();
    assert_eq!(c.successor(1), Some(2));
    assert_eq!(c.successor(2), None, "only the owner's leaving hands over");
    c.members.retain(|m| m.role != R::Officer);
    assert_eq!(c.successor(1), Some(4));
    c.members.retain(|m| m.character_id == 1);
    assert_eq!(c.successor(1), None, "nobody left: the club disbands");
    let c = club();
    let order: Vec<i64> = c.ordered().map(|m| m.character_id).collect();
    assert_eq!(order, [1, 2, 3, 4, 5]);
}
