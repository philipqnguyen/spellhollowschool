//! A serde serializer that writes any protocol value in the shape the
//! client's TypeScript types describe (messages; the hot movement, snapshot
//! and economy messages keep their hand-written converters):
//!
//! - struct fields become camelCase properties (`until_unix_ms` →
//!   `untilUnixMs`); `Option::None` is `null`;
//! - the outermost `ServerMsg` variant becomes `{ type: "<camelCase>", ...fields }`;
//! - enums listed in [`UNIONS`] (tagged unions with data) become
//!   `{ kind: "<camelCase>", ...fields }`, also their unit variants
//!   (`{ kind: "fight" }`); a newtype variant puts its value in `value`
//!   (`DuelRole::Duelist(A)` → `{ kind: "duelist", value: "a" }`);
//! - every other enum (codes) is a snake_case string (`"not_in_practice"`).
//!
//! Integers become JS numbers (ids stay below 2^53).

use serde::ser::{self, Serialize};
use serde_json::{Map, Number, Value};

/// Enums written as `{ kind, ... }` objects (all their variants).
pub const UNIONS: &[&str] = &[
    "DuelEvent",
    "SpellTarget",
    "NpcEvent",
    "DuelRole",
    "LessonPhase",
    "BeatPrompt",
    // Protocol v12.
    "NpcReply",
    "NpcGift",
    // Protocol v14.
    "PartyPlace",
    "PartyNotice",
    // Protocol v15.
    "ClubNotice",
];

/// `value` as a JSON value in the client's shape.
pub fn to_shape<T: Serialize + ?Sized>(value: &T) -> Result<Value, Error> {
    value.serialize(Shape { top: true })
}

#[derive(Debug)]
pub struct Error(String);

impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for Error {}

impl ser::Error for Error {
    fn custom<T: std::fmt::Display>(msg: T) -> Self {
        Error(msg.to_string())
    }
}

/// `snake_case` or `PascalCase` to `camelCase`.
pub fn camel(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    let mut upper = false;
    for (k, c) in s.chars().enumerate() {
        if c == '_' {
            upper = true;
        } else if k == 0 {
            out.extend(c.to_lowercase());
        } else if upper {
            out.extend(c.to_uppercase());
            upper = false;
        } else {
            out.push(c);
        }
    }
    out
}

/// `PascalCase` (or already `snake_case`) to `snake_case`.
pub fn snake(s: &str) -> String {
    let mut out = String::with_capacity(s.len() + 4);
    for (k, c) in s.chars().enumerate() {
        if c.is_uppercase() {
            if k > 0 {
                out.push('_');
            }
            out.extend(c.to_lowercase());
        } else {
            out.push(c);
        }
    }
    out
}

#[derive(Clone, Copy)]
struct Shape {
    /// The outermost value: its enum variant is tagged `type`.
    top: bool,
}

const INNER: Shape = Shape { top: false };

fn tag(top: bool) -> &'static str {
    if top { "type" } else { "kind" }
}

fn num(n: impl Into<Number>) -> Value {
    Value::Number(n.into())
}

impl ser::Serializer for Shape {
    type Ok = Value;
    type Error = Error;
    type SerializeSeq = Seq;
    type SerializeTuple = Seq;
    type SerializeTupleStruct = Seq;
    type SerializeTupleVariant = TupleVariant;
    type SerializeMap = MapS;
    type SerializeStruct = StructS;
    type SerializeStructVariant = StructS;

    fn serialize_bool(self, v: bool) -> Result<Value, Error> {
        Ok(Value::Bool(v))
    }
    fn serialize_i8(self, v: i8) -> Result<Value, Error> {
        Ok(num(v))
    }
    fn serialize_i16(self, v: i16) -> Result<Value, Error> {
        Ok(num(v))
    }
    fn serialize_i32(self, v: i32) -> Result<Value, Error> {
        Ok(num(v))
    }
    fn serialize_i64(self, v: i64) -> Result<Value, Error> {
        Ok(num(v))
    }
    fn serialize_u8(self, v: u8) -> Result<Value, Error> {
        Ok(num(v))
    }
    fn serialize_u16(self, v: u16) -> Result<Value, Error> {
        Ok(num(v))
    }
    fn serialize_u32(self, v: u32) -> Result<Value, Error> {
        Ok(num(v))
    }
    fn serialize_u64(self, v: u64) -> Result<Value, Error> {
        Ok(num(v))
    }
    fn serialize_f32(self, v: f32) -> Result<Value, Error> {
        self.serialize_f64(f64::from(v))
    }
    fn serialize_f64(self, v: f64) -> Result<Value, Error> {
        Ok(Number::from_f64(v).map_or(Value::Null, Value::Number))
    }
    fn serialize_char(self, v: char) -> Result<Value, Error> {
        Ok(Value::String(v.to_string()))
    }
    fn serialize_str(self, v: &str) -> Result<Value, Error> {
        Ok(Value::String(v.to_owned()))
    }
    fn serialize_bytes(self, v: &[u8]) -> Result<Value, Error> {
        Ok(Value::Array(v.iter().map(|&b| num(b)).collect()))
    }
    fn serialize_none(self) -> Result<Value, Error> {
        Ok(Value::Null)
    }
    fn serialize_some<T: Serialize + ?Sized>(self, v: &T) -> Result<Value, Error> {
        v.serialize(INNER)
    }
    fn serialize_unit(self) -> Result<Value, Error> {
        Ok(Value::Null)
    }
    fn serialize_unit_struct(self, _: &'static str) -> Result<Value, Error> {
        Ok(Value::Null)
    }
    fn serialize_unit_variant(
        self,
        name: &'static str,
        _: u32,
        variant: &'static str,
    ) -> Result<Value, Error> {
        if self.top || UNIONS.contains(&name) {
            let mut m = Map::new();
            m.insert(tag(self.top).into(), Value::String(camel(variant)));
            Ok(Value::Object(m))
        } else {
            Ok(Value::String(snake(variant)))
        }
    }
    fn serialize_newtype_struct<T: Serialize + ?Sized>(
        self,
        _: &'static str,
        v: &T,
    ) -> Result<Value, Error> {
        v.serialize(self)
    }
    fn serialize_newtype_variant<T: Serialize + ?Sized>(
        self,
        _: &'static str,
        _: u32,
        variant: &'static str,
        v: &T,
    ) -> Result<Value, Error> {
        let mut m = Map::new();
        m.insert(tag(self.top).into(), Value::String(camel(variant)));
        m.insert("value".into(), v.serialize(INNER)?);
        Ok(Value::Object(m))
    }
    fn serialize_seq(self, len: Option<usize>) -> Result<Seq, Error> {
        Ok(Seq(Vec::with_capacity(len.unwrap_or(0))))
    }
    fn serialize_tuple(self, len: usize) -> Result<Seq, Error> {
        self.serialize_seq(Some(len))
    }
    fn serialize_tuple_struct(self, _: &'static str, len: usize) -> Result<Seq, Error> {
        self.serialize_seq(Some(len))
    }
    fn serialize_tuple_variant(
        self,
        _: &'static str,
        _: u32,
        variant: &'static str,
        len: usize,
    ) -> Result<TupleVariant, Error> {
        Ok(TupleVariant {
            tag: tag(self.top),
            variant,
            items: Vec::with_capacity(len),
        })
    }
    fn serialize_map(self, _: Option<usize>) -> Result<MapS, Error> {
        Ok(MapS {
            map: Map::new(),
            key: None,
        })
    }
    fn serialize_struct(self, _: &'static str, _: usize) -> Result<StructS, Error> {
        Ok(StructS(Map::new()))
    }
    fn serialize_struct_variant(
        self,
        _: &'static str,
        _: u32,
        variant: &'static str,
        _: usize,
    ) -> Result<StructS, Error> {
        let mut m = Map::new();
        m.insert(tag(self.top).into(), Value::String(camel(variant)));
        Ok(StructS(m))
    }
}

struct Seq(Vec<Value>);

impl ser::SerializeSeq for Seq {
    type Ok = Value;
    type Error = Error;
    fn serialize_element<T: Serialize + ?Sized>(&mut self, v: &T) -> Result<(), Error> {
        self.0.push(v.serialize(INNER)?);
        Ok(())
    }
    fn end(self) -> Result<Value, Error> {
        Ok(Value::Array(self.0))
    }
}

impl ser::SerializeTuple for Seq {
    type Ok = Value;
    type Error = Error;
    fn serialize_element<T: Serialize + ?Sized>(&mut self, v: &T) -> Result<(), Error> {
        ser::SerializeSeq::serialize_element(self, v)
    }
    fn end(self) -> Result<Value, Error> {
        ser::SerializeSeq::end(self)
    }
}

impl ser::SerializeTupleStruct for Seq {
    type Ok = Value;
    type Error = Error;
    fn serialize_field<T: Serialize + ?Sized>(&mut self, v: &T) -> Result<(), Error> {
        ser::SerializeSeq::serialize_element(self, v)
    }
    fn end(self) -> Result<Value, Error> {
        ser::SerializeSeq::end(self)
    }
}

struct TupleVariant {
    tag: &'static str,
    variant: &'static str,
    items: Vec<Value>,
}

impl ser::SerializeTupleVariant for TupleVariant {
    type Ok = Value;
    type Error = Error;
    fn serialize_field<T: Serialize + ?Sized>(&mut self, v: &T) -> Result<(), Error> {
        self.items.push(v.serialize(INNER)?);
        Ok(())
    }
    fn end(self) -> Result<Value, Error> {
        let mut m = Map::new();
        m.insert(self.tag.into(), Value::String(camel(self.variant)));
        m.insert("values".into(), Value::Array(self.items));
        Ok(Value::Object(m))
    }
}

struct MapS {
    map: Map<String, Value>,
    key: Option<String>,
}

impl ser::SerializeMap for MapS {
    type Ok = Value;
    type Error = Error;
    fn serialize_key<T: Serialize + ?Sized>(&mut self, k: &T) -> Result<(), Error> {
        match k.serialize(INNER)? {
            Value::String(s) => self.key = Some(s),
            Value::Number(n) => self.key = Some(n.to_string()),
            _ => return Err(Error("map keys must be strings or numbers".into())),
        }
        Ok(())
    }
    fn serialize_value<T: Serialize + ?Sized>(&mut self, v: &T) -> Result<(), Error> {
        let k = self
            .key
            .take()
            .ok_or_else(|| Error("a value without a key".into()))?;
        self.map.insert(k, v.serialize(INNER)?);
        Ok(())
    }
    fn end(self) -> Result<Value, Error> {
        Ok(Value::Object(self.map))
    }
}

struct StructS(Map<String, Value>);

impl ser::SerializeStruct for StructS {
    type Ok = Value;
    type Error = Error;
    fn serialize_field<T: Serialize + ?Sized>(
        &mut self,
        key: &'static str,
        v: &T,
    ) -> Result<(), Error> {
        self.0.insert(camel(key), v.serialize(INNER)?);
        Ok(())
    }
    fn end(self) -> Result<Value, Error> {
        Ok(Value::Object(self.0))
    }
}

impl ser::SerializeStructVariant for StructS {
    type Ok = Value;
    type Error = Error;
    fn serialize_field<T: Serialize + ?Sized>(
        &mut self,
        key: &'static str,
        v: &T,
    ) -> Result<(), Error> {
        ser::SerializeStruct::serialize_field(self, key, v)
    }
    fn end(self) -> Result<Value, Error> {
        Ok(Value::Object(self.0))
    }
}

#[cfg(test)]
mod tests {
    use protocol::{
        BeatPrompt, ClassRewards, DuelEvent, DuelRole, DuelSide, FortuneKind, GestureResult, Grade,
        LessonBeat, LessonPhase, RewardItem, ServerMsg, SpellTarget, TraceReject,
    };
    use serde_json::json;

    use super::*;

    #[test]
    fn names_convert() {
        assert_eq!(camel("until_unix_ms"), "untilUnixMs");
        assert_eq!(camel("LessonPhaseChanged"), "lessonPhaseChanged");
        assert_eq!(snake("NotInPractice"), "not_in_practice");
        assert_eq!(snake("rate_limited"), "rate_limited");
    }

    #[test]
    fn messages_take_the_documented_shape() {
        let msg = ServerMsg::LessonPhaseChanged {
            phase: LessonPhase::Practice { round: 2 },
            until_unix_ms: 1_790_000_300_000,
        };
        assert_eq!(
            to_shape(&msg).unwrap(),
            json!({ "type": "lessonPhaseChanged",
                    "phase": { "kind": "practice", "round": 2 },
                    "untilUnixMs": 1_790_000_300_000u64 })
        );
        let msg = ServerMsg::BeatScored {
            round: 1,
            beat: 3,
            result: GestureResult {
                score: 0,
                grade: Grade::Miss,
                rejected: Some(TraceReject::NotInPractice),
            },
        };
        assert_eq!(
            to_shape(&msg).unwrap(),
            json!({ "type": "beatScored", "round": 1, "beat": 3,
                    "result": { "score": 0, "grade": "miss", "rejected": "not_in_practice" } })
        );
        let msg = ServerMsg::LessonRound {
            round: 2,
            window_ms: 1_400,
            beats: vec![
                LessonBeat {
                    beat: 0,
                    prompt: BeatPrompt::Trace { gesture: 3 },
                    at_unix_ms: 100,
                },
                LessonBeat {
                    beat: 1,
                    prompt: BeatPrompt::Pick {
                        prompt: 4,
                        options: vec![9, 4, 1],
                    },
                    at_unix_ms: 200,
                },
            ],
        };
        assert_eq!(
            to_shape(&msg).unwrap(),
            json!({ "type": "lessonRound", "round": 2, "windowMs": 1_400, "beats": [
                { "beat": 0, "prompt": { "kind": "trace", "gesture": 3 }, "atUnixMs": 100 },
                { "beat": 1, "prompt": { "kind": "pick", "prompt": 4, "options": [9, 4, 1] },
                  "atUnixMs": 200 }
            ] })
        );
        let rewards = ClassRewards {
            crowns: 12,
            items: vec![RewardItem {
                item_def: 3021,
                quantity: 2,
            }],
            fortune: Some(FortuneKind::QuickHands),
            ..ClassRewards::default()
        };
        assert_eq!(
            to_shape(&rewards).unwrap(),
            json!({ "xp": 0, "xpCapped": false, "skillXp": 0, "housePoints": 0, "spells": [],
                    "crowns": 12, "items": [{ "itemDef": 3021, "quantity": 2 }],
                    "recipes": [], "lore": null, "hint": null, "fortune": "quick_hands" })
        );
        let msg = ServerMsg::DuelEvents {
            duel_id: 9,
            at_ms: 100,
            events: vec![
                DuelEvent::Fight,
                DuelEvent::Hit {
                    id: 3,
                    target: DuelSide::B,
                    damage: 10,
                    poise: 90,
                },
            ],
        };
        assert_eq!(
            to_shape(&msg).unwrap(),
            json!({ "type": "duelEvents", "duelId": 9, "atMs": 100, "events": [
                { "kind": "fight" },
                { "kind": "hit", "id": 3, "target": "b", "damage": 10, "poise": 90 }
            ] })
        );
        assert_eq!(
            to_shape(&DuelRole::Duelist(DuelSide::A)).unwrap(),
            json!({ "type": "duelist", "value": "a" }),
            "only the outermost enum is tagged type"
        );
        assert_eq!(
            to_shape(&vec![
                SpellTarget::None,
                SpellTarget::Furniture { item_id: 7 }
            ])
            .unwrap(),
            json!([{ "kind": "none" }, { "kind": "furniture", "itemId": 7 }])
        );
    }

    /// Protocol v11: the new messages, duel events and duelist fields in
    /// the shapes the d.ts documents (a `QPos` is `{ x, y, z }` centimetres).
    #[test]
    fn spells_to_level_20_take_the_documented_shape() {
        use protocol::{CastRefusal, DuelistState, QPos, RevealKind, RevealedSpot, SpellErrorCode};
        let msg = ServerMsg::ForcedMove {
            to: QPos::from_m([12.5, 3.0, -40.25]),
            move_ms: 350,
            hold_ms: 450,
            by: 7,
        };
        assert_eq!(
            to_shape(&msg).unwrap(),
            json!({ "type": "forcedMove", "to": { "x": 1250, "y": 300, "z": -4025 },
                    "moveMs": 350, "holdMs": 450, "by": 7 })
        );
        let msg = ServerMsg::Revealed {
            spell: 5,
            duration_ms: 10_000,
            spots: vec![
                RevealedSpot {
                    kind: RevealKind::Passage,
                    pos: QPos::from_m([1.0, 0.0, 2.0]),
                },
                RevealedSpot {
                    kind: RevealKind::FindSpot,
                    pos: QPos::from_m([-3.0, 4.5, 8.0]),
                },
            ],
        };
        assert_eq!(
            to_shape(&msg).unwrap(),
            json!({ "type": "revealed", "spell": 5, "durationMs": 10_000, "spots": [
                { "kind": "passage", "pos": { "x": 100, "y": 0, "z": 200 } },
                { "kind": "find_spot", "pos": { "x": -300, "y": 450, "z": 800 } }
            ] })
        );
        let msg = ServerMsg::SpellError {
            spell: 4,
            code: SpellErrorCode::NoPath,
        };
        assert_eq!(
            to_shape(&msg).unwrap(),
            json!({ "type": "spellError", "spell": 4, "code": "no_path" })
        );
        let events = vec![
            DuelEvent::Rooted {
                side: DuelSide::A,
                until_ms: 10,
            },
            DuelEvent::Slowed {
                side: DuelSide::B,
                until_ms: 20,
                pct: 50,
            },
            DuelEvent::Pulled {
                side: DuelSide::B,
                from: [550, -20],
                to: [250, -10],
                until_ms: 30,
            },
            DuelEvent::Staggered {
                side: DuelSide::B,
                until_ms: 40,
            },
            DuelEvent::Cleansed {
                side: DuelSide::A,
                spell: 16,
            },
            DuelEvent::WardUp {
                side: DuelSide::A,
                spell: 18,
                until_ms: 50,
            },
            DuelEvent::Warded {
                id: 3,
                by: DuelSide::A,
            },
            DuelEvent::WardFaded { side: DuelSide::B },
            DuelEvent::Refused {
                side: DuelSide::B,
                spell: 11,
                reason: CastRefusal::Staggered,
            },
        ];
        assert_eq!(
            to_shape(&events).unwrap(),
            json!([
                { "kind": "rooted", "side": "a", "untilMs": 10 },
                { "kind": "slowed", "side": "b", "untilMs": 20, "pct": 50 },
                { "kind": "pulled", "side": "b", "from": [550, -20], "to": [250, -10], "untilMs": 30 },
                { "kind": "staggered", "side": "b", "untilMs": 40 },
                { "kind": "cleansed", "side": "a", "spell": 16 },
                { "kind": "wardUp", "side": "a", "spell": 18, "untilMs": 50 },
                { "kind": "warded", "id": 3, "by": "a" },
                { "kind": "wardFaded", "side": "b" },
                { "kind": "refused", "side": "b", "spell": 11, "reason": "staggered" }
            ])
        );
        let state = DuelistState {
            poise: 90,
            casting: None,
            shield_until: None,
            disarmed_until: None,
            cooldowns: Vec::new(),
            rooted_until: Some(10),
            slowed: Some((20, 50)),
            staggered_until: None,
            ward_until: Some(50),
        };
        assert_eq!(
            to_shape(&state).unwrap(),
            json!({ "poise": 90, "casting": null, "shieldUntil": null, "disarmedUntil": null,
                    "cooldowns": [], "rootedUntil": 10, "slowed": [20, 50],
                    "staggeredUntil": null, "wardUntil": 50 })
        );
    }

    /// Protocol v15: the club messages take the shapes `TS_TYPES` documents.
    #[test]
    fn clubs_take_the_documented_shape() {
        use protocol::{ClubErrorCode, ClubMember, ClubNotice, ClubRole, ClubView, House};
        let msg = ServerMsg::ClubState {
            club: Some(ClubView {
                id: 4,
                name: "The Night Owls".into(),
                tag: "OWL".into(),
                motd: "Lake at dusk".into(),
                members: vec![ClubMember {
                    character_id: 7,
                    name: "Ada".into(),
                    house: House::Skytalon,
                    level: 5,
                    role: ClubRole::Owner,
                    online: true,
                }],
            }),
        };
        assert_eq!(
            to_shape(&msg).unwrap(),
            json!({ "type": "clubState", "club": { "id": 4, "name": "The Night Owls", "tag": "OWL",
                "motd": "Lake at dusk", "members": [
                    { "characterId": 7, "name": "Ada", "house": "skytalon", "level": 5,
                      "role": "owner", "online": true }
                ] } })
        );
        assert_eq!(
            to_shape(&ServerMsg::ClubNotice {
                notice: ClubNotice::Renamed {
                    club: "Owls".into(),
                    tag: "OW".into()
                }
            })
            .unwrap(),
            json!({ "type": "clubNotice", "notice": { "kind": "renamed", "club": "Owls", "tag": "OW" } })
        );
        assert_eq!(
            to_shape(&ServerMsg::ClubError {
                code: ClubErrorCode::NotEnoughCrowns
            })
            .unwrap(),
            json!({ "type": "clubError", "code": "not_enough_crowns" })
        );
        assert_eq!(
            to_shape(&ServerMsg::ClubPresence {
                character_id: 7,
                online: false,
                level: 6
            })
            .unwrap(),
            json!({ "type": "clubPresence", "characterId": 7, "online": false, "level": 6 })
        );
        assert_eq!(
            to_shape(&ServerMsg::ClubInvited {
                invite_id: 2,
                club_name: "Owls".into(),
                club_tag: "OW".into(),
                from_name: "Ada".into(),
                expires_in_secs: 120,
            })
            .unwrap(),
            json!({ "type": "clubInvited", "inviteId": 2, "clubName": "Owls", "clubTag": "OW",
                    "fromName": "Ada", "expiresInSecs": 120 })
        );
    }

    /// Protocol v14: the party messages take the shapes `TS_TYPES` documents.
    #[test]
    fn parties_take_the_documented_shape() {
        use protocol::{
            House, PartyErrorCode, PartyMember, PartyNotice, PartyPlace, PartyPresence, PartyView,
        };
        let msg = ServerMsg::PartyState {
            party: Some(PartyView {
                leader: 7,
                members: vec![
                    PartyMember {
                        character_id: 7,
                        name: "Ada".into(),
                        house: House::Skytalon,
                        level: 4,
                        online: Some(PartyPresence {
                            entity: 65_538,
                            zone: 0,
                            place: PartyPlace::Castle {
                                place: "Great Hall".into(),
                            },
                        }),
                    },
                    PartyMember {
                        character_id: 9,
                        name: "Bea".into(),
                        house: House::Roarwing,
                        level: 2,
                        online: Some(PartyPresence {
                            entity: 3,
                            zone: 12,
                            place: PartyPlace::WishingRoom {
                                owner_name: "Ada".into(),
                            },
                        }),
                    },
                    PartyMember {
                        character_id: 11,
                        name: "Cy".into(),
                        house: House::Burrowsby,
                        level: 9,
                        online: None,
                    },
                ],
            }),
        };
        assert_eq!(
            to_shape(&msg).unwrap(),
            json!({ "type": "partyState", "party": { "leader": 7, "members": [
                { "characterId": 7, "name": "Ada", "house": "skytalon", "level": 4,
                  "online": { "entity": 65_538, "zone": 0,
                              "place": { "kind": "castle", "place": "Great Hall" } } },
                { "characterId": 9, "name": "Bea", "house": "roarwing", "level": 2,
                  "online": { "entity": 3, "zone": 12,
                              "place": { "kind": "wishingRoom", "ownerName": "Ada" } } },
                { "characterId": 11, "name": "Cy", "house": "burrowsby", "level": 9, "online": null }
            ] } })
        );
        assert_eq!(
            to_shape(&ServerMsg::PartyState { party: None }).unwrap(),
            json!({ "type": "partyState", "party": null })
        );
        assert_eq!(
            to_shape(&ServerMsg::PartyNotice {
                notice: PartyNotice::Disbanded
            })
            .unwrap(),
            json!({ "type": "partyNotice", "notice": { "kind": "disbanded" } })
        );
        assert_eq!(
            to_shape(&ServerMsg::PartyNotice {
                notice: PartyNotice::Leader { name: "Bea".into() }
            })
            .unwrap(),
            json!({ "type": "partyNotice", "notice": { "kind": "leader", "name": "Bea" } })
        );
        assert_eq!(
            to_shape(&ServerMsg::PartyError {
                code: PartyErrorCode::NotLeader
            })
            .unwrap(),
            json!({ "type": "partyError", "code": "not_leader" })
        );
        assert_eq!(
            to_shape(&ServerMsg::PartyInvited {
                invite_id: 3,
                from_name: "Ada".into(),
                from_house: House::Skytalon,
                from_level: 4,
                expires_in_secs: 120,
            })
            .unwrap(),
            json!({ "type": "partyInvited", "inviteId": 3, "fromName": "Ada",
                    "fromHouse": "skytalon", "fromLevel": 4, "expiresInSecs": 120 })
        );
    }
}
