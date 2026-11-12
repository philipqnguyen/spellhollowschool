//! WebAssembly bindings exposing `sim-core` rules and the `protocol` codec to the browser client.
//!
//! Build with `scripts/build-wasm.sh`, which writes `sim_wasm.js`,
//! `sim_wasm_bg.wasm` and `sim_wasm.d.ts` to `client/src/wasm/` (wasm-bindgen
//! `--target web`: call the default export `init()` once before anything else).
//!
//! Three parts:
//! - [`SimWorld`]: a layout (the castle, or an instance such as a dorm) with
//!   movement, ground queries and furniture placement, so the client predicts
//!   with exactly the code the server validates with.
//! - `encode*`: the client never hand-encodes postcard.
//! - [`ServerDecoder`]: decodes server frames into plain JS objects with a
//!   `type` discriminator and positions in metres (shapes in [`TS_TYPES`]).
//!   It is stateful: it keeps the visible set and each entity's baseline, so
//!   snapshot deltas (protocol v2) arrive as absolute states, and the looks
//!   sent on this connection, so cached entries (v3) arrive complete. Use one
//!   decoder per WebSocket connection.

use js_sys::{Array, Object, Reflect};
use protocol::{
    Anim, Appearance, CHAT_MAX_LEN, CLIENT_MOVE_HZ, ChameleonPart, CharacterInfo, ChatChannel,
    ChatRejectReason, ClientMsg, DORM_INVITE_TTL_SECS, DORM_ITEM_CAP, Desync, DormErrorCode,
    EMOTE_BOW_MS, EMOTE_WAVE_MS, Emote, EntityEntry, EntityInfo, EntityState, FRIENDS_MAX,
    FindOutcome, Finish, FriendEvent, FurnitureEdit, GuardianAnimal, House, KickCode, Location,
    LookCache, MAX_UPDATE_GAP_SNAPSHOTS, MIMIC_DRAUGHT_REQUEST_SECS, OfferItem, PROTOCOL_VERSION,
    PlacedFurniture, PotionErrorCode, QPos, SAVED_OUTFITS_MAX, SERVER_ENTITY_ID, SPEED_PCT_MAX,
    SPEED_PCT_NORMAL, STATION_GRANT_SECS, ServerMsg, SnapshotReceiver, SpellTarget,
    TRADE_ITEMS_MAX, TRADE_REQUEST_SECS, TracePoint, TradeErrorCode, TradeOffer, TradeOutcome,
    TradeView, VIEW_LIMIT_DEFAULT, VIEW_LIMIT_MAX, VisibleEffect, Worn, Zone, yaw_to_u8,
};
use sim_core::placement::{FurnitureShape, PlaceArea, PlaceSurface, check_placement};
use sim_core::{Gestures, MoveParams, Schedule, World};
use wasm_bindgen::prelude::*;

mod shape;

#[wasm_bindgen(typescript_custom_section)]
const TS_TYPES: &'static str = r#"
/** World position in metres: x = east, y = up, z = south. */
export type Vec3 = [number, number, number];
/**
 * `cast` (protocol v5): a wand gesture, set by the server; moving ends it.
 * `fly` / `hover` (protocol v8): on a broom, moving or still (`encodeMove` while mounted).
 */
export type Anim = "idle" | "walk" | "run" | "wave" | "bow" | "sit" | "cast" | "fly" | "hover";
export type Emote = "wave" | "bow" | "sit";
export type House = "roarwing" | "burrowsby" | "skytalon" | "serpentyne";
export type GearSlot = "hat" | "body" | "legs" | "shoes";
/** `party` (protocol v14): every member of the sender's party, wherever they are. */
/** `club` (protocol v15): every member of the sender's club, wherever they are. */
export type ChatChannelName = "say" | "house" | "whisper" | "party" | "club";
/** A body type's or a restricted garment's wear class (`creator.json`, `catalog.json`). */
export type Wear = "female" | "male";
/** `protocol::KickCode`. Retry (with backoff) only on the first four. */
export type KickCode =
  | "server_restart" | "timeout" | "too_slow" | "server_full"
  | "replaced" | "banned" | "protocol_error" | "version_mismatch"
  | "session_expired" | "no_character" | "flood";
/** `no_channel` (protocol v14): party chat outside a party. */
export type ChatRejectReason = "rate_limited" | "filtered" | "unknown_target" | "invalid" | "no_channel";
export type DormErrorCode =
  | "not_owner" | "not_in_dorm" | "item_not_found" | "not_furniture" | "item_unavailable"
  | "out_of_bounds" | "cap_reached" | "not_friend" | "not_online" | "closed"
  | "invite_expired" | "too_far" | "wrong_house" | "unknown_target" | "rate_limited" | "failed"
  | "full"
  /** Protocol v8: doors and stations refuse riders (land first). */
  | "mounted"
  /** Something stands on the piece's top or shelves: take it off before moving or putting the piece away. */
  | "not_empty"
  /** Solid furniture: the piece would stand on someone or the entry, or close the way out or to someone. */
  | "blocks_way";
export type PlaceSurface = "floor" | "wall" | "table";

export interface Spawn { id: string; pos: Vec3; yaw: number }
/** Solid furniture: a placed piece for `SimWorld.setFurniture` / `keepsWay` (the catalog's `furniture` block, where it stands, its yaw in radians). */
export interface FurniturePiece { id: number; furniture: object; pos: Vec3; yaw: number }
export interface Landmark { id: string; name: string; pos: Vec3 }
/** Where a station grant lets the HTTP side craft or trade. */
export type StationKind = "craft_charms" | "craft_transfiguration" | "cauldron" | "owl_exchange" | "owl_order";
/**
 * `wishing_room_door` is secret: shown only while `wishingRoomDoor` holds it open. `owl_order`
 * (a station) is the Owl Order Catalogue stand in each common room.
 */
export type InteractableKind =
  | "dorm_door" | "dorm_exit" | "find_spot" | StationKind | "class_door" | "instance_exit"
  | "notice_board" | "owl_mail" | "wishing_room_door";
/** `sim_core::layout::Interactable` as written in the layout file. */
export interface Interactable {
  id: string;
  kind: InteractableKind;
  house?: House;
  pos: Vec3;
  yaw: number;
  radius: number;
  name?: string;
}
/** `sim_core::placement::PlaceArea` as written in the layout file. */
export interface PlaceArea {
  id: string;
  surface: PlaceSurface;
  min: Vec3;
  max: Vec3;
  facing?: number;
  /** Floor and table areas: the tallest item that fits (a shelf's headroom, m). */
  clear?: number;
  /** Floor and table areas: `min` / `max` are in this frame (a placed piece's tops turn with it). */
  frame?: AreaFrame;
}
/** `world = origin + R(yaw) · local`, `R(yaw)` turning local +Z to `(sin yaw, 0, cos yaw)`. */
export interface AreaFrame { origin: Vec3; yaw: number }

/** sim-core movement constants (speeds in m/s, lengths in metres). */
export interface MoveParams {
  walkSpeed: number;
  runSpeed: number;
  stepHeight: number;
  radius: number;
  speedTolerance: number;
}

export interface ProtocolConstants {
  protocolVersion: number;
  clientMoveHz: number;
  viewLimitDefault: number;
  viewLimitMax: number;
  /** Maximum chat text length in UTF-8 bytes. */
  chatMaxLen: number;
  /** `Chat.from` of notices from the server itself (never a character). */
  serverEntityId: number;
  /** A moving entity is updated at least every this many snapshots (far tier: every other one). */
  maxUpdateGapSnapshots: number;
  emoteWaveMs: number;
  emoteBowMs: number;
  dormItemCap: number;
  dormInviteTtlSecs: number;
  savedOutfitsMax: number;
  friendsMax: number;
  tradeRequestSecs: number;
  tradeItemsMax: number;
  stationGrantSecs: number;
  mimicDraughtRequestSecs: number;
  /** `ownEffects.speedPct` without effects (100), the most an effect gives and (protocol v11) the least (0: bound). */
  speedPctNormal: number;
  speedPctMax: number;
  speedPctMin: number;
  castAnimMs: number;
  levelMax: number;
  loadoutMax: number;
  tracePointsMax: number;
  lessonBeatsMax: number;
  duelChallengeSecs: number;
  // Protocol v7.
  /** Most options a pick beat offers (`PickAnswer.choice` is below it). */
  pickOptionsMax: number;
  // Protocol v8.
  /** `ownEffects.broomPct` without effects (100) and the most an effect gives. */
  broomPctNormal: number;
  broomPctMax: number;
  // Protocol v10: the farthest a character looks at another (m), and `encodeLookAt`'s rate.
  lookRangeM: number;
  lookSendsPerSec: number;
  // Protocol v14: the most members of a party, and how long a party invitation lasts (s).
  partyMax: number;
  partyInviteTtlSecs: number;
  // Protocol v15: how long a club invitation lasts (s).
  clubInviteTtlSecs: number;
}

/** Why `encodeMount` was refused (`mounted.reason`). */
export type MountRefused = "no_skill" | "no_airspace" | "no_fly" | "busy" | "not_landed" | "rate_limited";
/** A broom's flight numbers (`SimWorld.flyParams`): speeds m/s, lengths metres. */
export interface FlyParams {
  /** Top horizontal speed (the tier's, times `broomPct`). */
  speed: number;
  /** Top climb and dive speed. */
  climbSpeed: number;
  /** How fast the broom gathers and sheds speed (m/s²; the controller's feel). */
  acceleration: number;
  /** The lowest a broom flies over the ground or the water. */
  clearance: number;
  /** How far the prediction keeps off the airspace's edges and the no-fly boxes. */
  margin: number;
  /** A rider may land when ground is at most this far below. */
  dismountHeight: number;
  /** The top of the grounds' and the lake's airspace (world y). */
  ceilingY: number;
}
/** One hoop of a ring course (the Flying class's layout); `ring` beats index them. */
export interface Ring { id: string; pos: Vec3; yaw: number; radius: number }

export interface CharacterInfo { name: string; house: House }

/** A brewed finish on a garment (`protocol::Finish`). */
export type Finish = "none" | "shimmer" | "colour_shift";
/**
 * An item as worn: catalog id, palette indices (primary, secondary, trim) and
 * finish. The decoder always sets `finish`; looks the client builds itself may
 * leave it out (none).
 */
export interface Worn { itemDef: number; dyes: [number, number, number]; finish?: Finish }
export interface Outfit { hat: Worn | null; body: Worn | null; legs: Worn | null; shoes: Worn | null }
/** `protocol::Appearance`; indices and ids refer to data/characters/creator.json and data/items/. */
export interface Appearance {
  bodyType: number;
  /** Height byte: 0 / 128 / 255 = the body's minimum / default / maximum (a uniform scale). */
  height: number;
  /** Jaw, cheek, nose, brow, eye shape (0-255, 128 neutral). */
  face: [number, number, number, number, number];
  skinTone: number;
  hairStyle: number;
  hairColour: number;
  /** Index into creator.json `eye_colours` (protocol 6). */
  eyeColour: number;
  outfit: Outfit;
}

/**
 * An entity entering the visible set, before its first state. Always complete:
 * the decoder fills in looks the server sent as cached (protocol v3).
 */
export interface EntityInfo {
  id: number;
  character: CharacterInfo;
  /**
   * Cache assembled characters by (id, appearanceVersion) within one
   * connection: after a reconnect (perhaps to a restarted server) an id may
   * be someone else.
   */
  appearanceVersion: number;
  appearance: Appearance;
  /** Bumped by the server whenever `effects` changes (from 0 at each sign-in). */
  effectsVersion: number;
  /** Active potion effects to draw (usually none), and `lucerna` while the wand is lit. */
  effects: VisibleEffect[];
  /** The player's level (1-20) for the nameplate's school year; 0 for NPCs. */
  level: number;
  /** The NPC's data id (`data/classes/<class>.json` `professor.id`), or null for players. */
  npc: string | null;
}

export type EffectKind =
  | "glow" | "chameleon" | "shrinking" | "swelling" | "babbling"
  | "mimic_draught" | "fortunes_draught" | "pick_me_up_tonic" | "invisibility"
  /** Protocol v7: a Divination fortune (not a potion; only its holder sees it, `fortune` says which). */
  | "fortune"
  /** Protocol v8: Featherlight Elixir, a faster broom (`ownEffects.broomPct`); nobody else sees it. */
  | "featherlight"
  /** Protocol v11: spell effects (cast, never drunk): Rigora (speed 0), Tardara, Tinctura, Custodio. */
  | "petrified" | "slowed" | "recoloured" | "guardian";
/** Protocol v11: the silver animal a character's guardian takes (stable per character). */
export type GuardianAnimal = "hare" | "fox" | "cat" | "owl" | "otter" | "horse";
/** Protocol v11: a `protocol::QPos` as the generic shape writes it: integer centimetres (divide by 100 for metres). */
export interface QPosCm { x: number; y: number; z: number }
/** Protocol v7: Lucky Find, Quick Hands, Keen Mind. */
export type FortuneKind = "luck" | "quick_hands" | "keen_mind";

/** `protocol::VisibleEffect`: what viewers draw for a potion effect. */
export type VisibleEffect =
  /** A soft aura in palette colour `colour`. */
  | { kind: "glow"; colour: number }
  /** `part` cycles through palette family `family` (index into palette.json `families`). */
  | { kind: "chameleon"; part: "hair" | "robe"; family: number }
  /** Drawn at `pct` percent of normal size (collision unchanged). */
  | { kind: "scale"; pct: number }
  | { kind: "babbling" }
  /** Mimic Draught: draw this look instead (the nameplate keeps the drinker's name). */
  | { kind: "disguise"; appearance: Appearance }
  | { kind: "lucky" }
  | { kind: "steam" }
  | { kind: "nameplateHidden" }
  /** Lucerna (protocol v5): a lit wand tip. Real lights for the nearest few, an emissive glow for the rest. */
  | { kind: "lucerna" }
  /** Protocol v11: Rigora: stiff as a board (the pose freezes). */
  | { kind: "bound" }
  /** Protocol v11: Tardara: moving in slow motion at `pct`% (animation rate). */
  | { kind: "slowed"; pct: number }
  /** Protocol v11: Tinctura: the body garment's primary dye shows palette colour `colour` (beats a Chameleon tint). */
  | { kind: "recoloured"; colour: number }
  /** Protocol v11: Custodio: the holder's silver animal follows (`guarding`: circles them, a duel ward). */
  | { kind: "guardian"; animal: GuardianAnimal; guarding: boolean };

export type TradeErrorCode =
  | "unknown_target" | "self_trade" | "too_far" | "busy" | "unknown_trade" | "expired"
  | "locked" | "not_locked" | "stale_revision" | "invalid_offer" | "item_unavailable"
  | "insufficient_crowns" | "rate_limited" | "failed";
export type TradeOutcome =
  | { kind: "completed" | "cancelled" | "declined" | "expired" | "walked_away" | "disconnected" }
  | { kind: "failed"; code: TradeErrorCode };
export interface OfferedItem {
  itemId: number; itemDef: number; dyes: [number, number, number]; finish: Finish;
  quantity: number; maker: string | null;
}
export interface TradeOffer { items: OfferedItem[]; crowns: number }
export interface TradeView {
  tradeId: number; partner: number; partnerName: string;
  /** Confirm this revision (`encodeTradeConfirm(tradeId, rev)`). */
  rev: number;
  mine: TradeOffer; theirs: TradeOffer;
  mineLocked: boolean; theirsLocked: boolean; mineConfirmed: boolean; theirsConfirmed: boolean;
}
export type PotionErrorCode =
  | "item_not_found" | "not_a_potion" | "needs_target" | "not_friend" | "too_far"
  | "not_online" | "expired" | "disabled" | "busy" | "rate_limited" | "failed";
export type FindOutcome =
  | { kind: "found"; items: { itemDef: number; quantity: number }[]; crowns: number }
  | { kind: "nothing" }
  | { kind: "cooldown"; readyInSecs: number }
  | { kind: "failed" };

/** Latest absolute state of a visible entity (deltas already applied). Position flattened to x/y/z (metres). */
/** Protocol v10: member `id` of the visible set now looks at `target` (null: nobody). */
export interface LookChange {
  id: number;
  target: number | null;
}

export interface EntityState {
  id: number;
  x: number;
  y: number;
  z: number;
  /** Radians, [0, 2π). */
  yaw: number;
  anim: Anim;
}

export type Zone =
  | { kind: "castle" }
  /** `layout`: data/world/instances/<layout>.json. */
  | { kind: "dorm"; ownerName: string; house: House; layout: string }
  /** A class section (`classJoined` follows). */
  | { kind: "class"; class: string; section: number; layout: string }
  /** A dueling arena (`duelStart` follows). */
  | { kind: "arena"; duelId: number; venue: string; layout: string }
  /** A Wishing Room, its opener's (`dormState` follows: no items, its open flag). */
  | { kind: "wishing_room"; ownerName: string; room: WishingRoomKind; layout: string };

/** `wishing_room`: in a Wishing Room (theirs or a friend's). */
export type Location =
  | { kind: "castle"; place: string }
  | { kind: "dorm"; ownerName: string }
  | { kind: "wishing_room"; ownerName: string };

/** The Wishing Room's two rooms (`protocol::WishingRoomKind`). */
export type WishingRoomKind = "lost_property" | "gathering";

/** A placed dorm item. `itemId` is an `items.id` (always below 2^53); `yaw` in radians. */
export interface PlacedFurniture { itemId: number; itemDef: number; dyes: [number, number, number]; pos: Vec3; yaw: number }

export type FurnitureEdit =
  | { kind: "placed"; item: PlacedFurniture }
  | { kind: "moved"; itemId: number; pos: Vec3; yaw: number }
  | { kind: "removed"; itemId: number };

export type FriendEvent =
  | { kind: "requestReceived"; requestId: number; characterId: number; name: string; house: House }
  | { kind: "added"; characterId: number; name: string; house: House; location: Location | null; roomOpen: boolean }
  | { kind: "requestClosed"; requestId: number }
  | { kind: "removed"; characterId: number }
  | { kind: "online"; characterId: number; location: Location; roomOpen: boolean }
  | { kind: "moved"; characterId: number; location: Location }
  | { kind: "offline"; characterId: number }
  /** An online friend opened or closed their room: Visit works only while it is open. */
  | { kind: "roomOpen"; characterId: number; open: boolean };

// ---- Protocol v5: levels, spells, NPCs, classes, dueling.
// Tagged unions with data are `{ kind, ... }` objects; codes are snake_case strings.

/** `explore`: finding a secret. */
export type XpSource =
  | "class_attend" | "class_bonus" | "craft" | "find" | "duel_win" | "duel_loss" | "duel_draw" | "admin"
  | "explore";
export interface Progress {
  level: number; xp: number;
  /** Total XP the current level began at. */
  levelXp: number;
  /** Total XP the next level needs; null at the cap. */
  nextLevelXp: number | null;
  /** School year (1-7) shown beside the level. */
  year: number;
}
export interface XpGain { source: XpSource; amount: number; capped: boolean }
export type Grade = "miss" | "good" | "great" | "perfect";
export type TraceReject =
  | "bad_sample_count" | "bad_duration" | "too_fast" | "too_perfect" | "too_small" | "out_of_range"
  | "replayed" | "bad_timing" | "unknown_beat" | "not_in_practice" | "rate_limited"
  /** Protocol v7: a trace for a pick beat or a pick for a trace beat; a choice that is not an option. */
  | "wrong_beat_kind" | "bad_choice";
/** The server's (or `GestureScorer.score`'s, `PickScorer.preview`'s) verdict on one beat. A wrong pick is score 0, miss, not rejected. */
export interface GestureResult { score: number; grade: Grade; rejected: TraceReject | null }
export type LessonPhase =
  | { kind: "gathering" } | { kind: "demonstration" } | { kind: "practice"; round: number }
  | { kind: "results" } | { kind: "ended" };
/**
 * Protocol v7: what a beat asks. `trace`: trace `gesture` (data/classes/gestures.json id).
 * `pick`: show item `prompt` of the class's prompt set and items `options` of its option set
 * (data/classes/picks.json; the class file's `picks` names both sets), in this order; answer with
 * `encodePickAnswer(round, beat, choice, offsetMs)`, `choice` the position in `options`.
 */
export type BeatPrompt =
  | { kind: "trace"; gesture: number }
  | { kind: "pick"; prompt: number; options: number[] }
  /**
   * Protocol v8 (the Flying class): fly through ring `ring` of the class layout's `rings`
   * (`SimWorld.rings()`) within the round's window of the beat. Nothing is sent: the server
   * scores the crossing from the checked flight and answers `beatScored`.
   */
  | { kind: "ring"; ring: number };
/** Beat `beat` of a round: trace or pick at `atUnixMs` (server clock). */
export interface LessonBeat { beat: number; prompt: BeatPrompt; atUnixMs: number }
export interface ClassRewards {
  xp: number;
  /** The daily class XP cap cut `xp` short. */
  xpCapped: boolean;
  /** XP in the class's subject skill. */
  skillXp: number;
  housePoints: number;
  spells: number[];
  // Protocol v7.
  crowns: number;
  items: { itemDef: number; quantity: number }[];
  /** Recipe ids newly known (data/crafting/recipes.json). */
  recipes: number[];
  /** Lore entry unlocked (data/classes/lore.json id). */
  lore: number | null;
  /** Where an unsearched find spot is (a place name). */
  hint: string | null;
  fortune: FortuneKind | null;
}
export type ClassErrorCode = "not_open" | "full" | "already_attended" | "rate_limited" | "failed";
export interface ClassInfo {
  class: string; session: number; section: number;
  startUnixMs: number; joinUntilUnixMs: number; endUnixMs: number;
  /** The professor's entity id (an NPC in the snapshot). */
  professor: number;
  phase: LessonPhase; phaseUntilUnixMs: number;
}
export type SpellTarget =
  | { kind: "none" } | { kind: "furniture"; itemId: number } | { kind: "entity"; id: number };
export type SpellErrorCode =
  | "not_learned" | "not_here" | "cooldown" | "bad_target" | "too_far" | "busy" | "rate_limited"
  /** Protocol v11: not a friend; no target (or yourself) for a spell that needs one; the target sits, is in class, trading, dueling, on a broom or being moved; Adveni has no safe way. */
  | "not_friend" | "needs_target" | "unavailable" | "no_path";
/** Protocol v11: what an Aperio spot is (a code). */
export type RevealKind = "passage" | "find_spot";
export type NpcEvent =
  | { kind: "say"; text: string }
  | { kind: "demonstrate"; gesture: number; spell: number | null; durationMs: number }
  | { kind: "emote"; emote: Emote };
/** Protocol v12: what an NPC gave (`NpcReply` gift). */
export type NpcGift =
  | { kind: "lore"; loreId: number; title: string }
  | { kind: "item"; itemDef: number; quantity: number }
  | { kind: "housePoint"; points: number }
  | { kind: "hint"; place: string };
export type NpcErrorCode = "too_far" | "unknown_npc" | "rate_limited" | "failed";
/** Protocol v12: the answer to `encodeNpcInteract`. */
export type NpcReply =
  | { kind: "talk"; givenToday: boolean }
  | { kind: "gift"; gift: NpcGift }
  | { kind: "refused"; code: NpcErrorCode };
/** NPCs (data/npcs/roster.json; `NpcBook`). */
export type NpcKind = "portrait" | "ghost" | "staff" | "shopkeeper" | "student" | "clerk";
export type LineWhen = "pass" | "talk" | "hint" | "gift" | "given" | "shop";
export type CupStanding = "yours_leads" | "other_leads" | "tied";
export type GiftKind = "lore" | "item" | "house_point" | "hint";
/** One roster NPC (`NpcBook.npc`, `npcs`). */
export interface NpcView {
  id: string; kind: NpcKind; name: string; title?: string; house?: House;
  personality: string;
  /** Shopkeepers: the station interactable they keep (interact sends `encodeInteract(station)`). */
  station?: string;
  /** Portraits: today's hint opens every conversation (`NpcBook.dailyHint`). */
  daily_hint?: boolean;
  /** Students: the shared lines key. */
  lines?: string;
}
/** The NPC rules (roster.json `rules`, snake_case as in the file). */
export interface NpcRulesView {
  talk_radius_m: number; talk_reach_m: number; pass_radius_m: number; pass_cooldown_s: number;
  pass_gap_s: number; pass_line_pct: number; alive_radius_m: number; look_radius_m: number;
  subtitle_ms: number; talk_lines_max: number; hint_pct: number; students_max: number;
  students_in_view_max: number; student_view_penalty_m: number; walk_speed_mps: number;
  ghost_speed_mps: number; ghost_float_m: number;
}
/** A talking portrait on its wall (`NpcBook.portraits`): the canvas centre on the wall face, facing `yaw`. */
export interface PortraitView { npc: string; pos: Vec3; yaw: number; size: [number, number]; backdrop: number; level: number }
/** What a line's conditions are checked against (`NpcBook.pick`). */
export interface LineContext {
  house: House; npcHouse?: House | null; period?: Period | null; cup?: CupStanding | null;
  /** Catalog tags of everything the player wears. */
  wornTags?: string[];
  /** Secret ids the player has found (`GET /api/map`). */
  foundSecrets?: string[];
  gift?: GiftKind | null;
}

export type DuelSide = "a" | "b";
export type DuelRole = { kind: "duelist"; value: DuelSide } | { kind: "spectator" };
export interface DuelistInfo { entity: number; name: string; house: House; level: number; loadout: number[] }
/** Arena positions are centimetres: [x, z] in the arena layout. */
export type ArenaPos = [number, number];
/** A projectile flies from `pos` toward `to` at `speedCms` centimetres per second. */
export interface ProjectileState {
  id: number; side: DuelSide; spell: number; pos: ArenaPos; to: ArenaPos; speedCms: number;
}
/** Times are the duel clock (ms since the arena opened). `casting` is [spell, releaseMs]; cooldowns [spell, readyMs]. */
export interface DuelistState {
  poise: number; casting: [number, number] | null; shieldUntil: number | null;
  disarmedUntil: number | null; cooldowns: [number, number][];
  /** Protocol v11: rooted until; slowed [untilMs, pct]; staggered until; a Guardian ward up until (each null when not now). */
  rootedUntil: number | null; slowed: [number, number] | null; staggeredUntil: number | null;
  wardUntil: number | null;
}
export interface DuelSnapshot {
  atMs: number; fightAtMs: number; sides: [DuelistState, DuelistState]; projectiles: ProjectileState[];
}
export type CastRefusal =
  | "not_in_loadout" | "cooldown" | "disarmed" | "busy" | "not_fighting"
  /** Protocol v11: staggered by Adveni. */
  | "staggered";
export type DuelEndReason = "poise" | "timeout" | "forfeit" | "disconnect";
export type DuelEvent =
  | { kind: "fight" }
  | { kind: "castStarted"; side: DuelSide; spell: number; releaseMs: number }
  /** To that duelist only. */
  | { kind: "refused"; side: DuelSide; spell: number; reason: CastRefusal }
  | { kind: "interrupted"; side: DuelSide; spell: number }
  | { kind: "shieldUp"; side: DuelSide; spell: number; untilMs: number; perfectUntilMs: number }
  | { kind: "launched"; id: number; side: DuelSide; spell: number; from: ArenaPos; to: ArenaPos; speedCms: number }
  | { kind: "blocked"; id: number; by: DuelSide }
  | { kind: "reflected"; id: number; by: DuelSide; from: ArenaPos; to: ArenaPos }
  | { kind: "shieldBroken"; side: DuelSide }
  | { kind: "hit"; id: number; target: DuelSide; damage: number; poise: number }
  | { kind: "disarmed"; side: DuelSide; untilMs: number }
  | { kind: "missed"; id: number }
  | { kind: "ended"; winner: DuelSide | null; reason: DuelEndReason }
  /** Protocol v11: Rigora: no moving until `untilMs` (casting and shielding still work). */
  | { kind: "rooted"; side: DuelSide; untilMs: number }
  /** Protocol v11: Tardara: moving at `pct`% until `untilMs`. */
  | { kind: "slowed"; side: DuelSide; untilMs: number; pct: number }
  /** Protocol v11: Adveni: a straight slide `from` -> `to`, arriving at `untilMs` (`staggered` follows). */
  | { kind: "pulled"; side: DuelSide; from: ArenaPos; to: ArenaPos; untilMs: number }
  /** Protocol v11: no casting and no moving until `untilMs`. */
  | { kind: "staggered"; side: DuelSide; untilMs: number }
  /** Protocol v11: Dissolvo released: root and slow gone (sent even with nothing to clear). */
  | { kind: "cleansed"; side: DuelSide; spell: number }
  /** Protocol v11: a Guardian ward blocks the next spell that would land, until `untilMs`. */
  | { kind: "wardUp"; side: DuelSide; spell: number; untilMs: number }
  /** Protocol v11: the ward of `by` stopped projectile `id` (and is gone). */
  | { kind: "warded"; id: number; by: DuelSide }
  /** Protocol v11: the ward faded unused. */
  | { kind: "wardFaded"; side: DuelSide };
export type ChallengeEnd =
  | "started" | "declined" | "cancelled" | "expired" | "walked_away" | "disconnected";
export interface DuelRewards { xp: number; capped: boolean; housePoints: number }
export interface DuelSummary {
  duelId: number; aName: string; aHouse: House; bName: string; bHouse: House;
  spectators: number; elapsedMs: number;
}
export type DuelErrorCode =
  | "unknown_target" | "self_duel" | "not_in_area" | "target_not_in_area" | "busy"
  | "unknown_challenge" | "expired" | "unknown_duel" | "full" | "no_spells" | "rate_limited" | "failed";

/** `GameClock` (sim_core::clock). Unix ms are the server clock. */
export type Period = "morning" | "afternoon" | "night";
export interface SessionTimes { session: number; startMs: number; joinUntilMs: number; endMs: number }
/** `GameClock.timetable()`: a slot of the day and the classes starting then. */
export interface TimetableSlot {
  slot: number;
  /** "08:00" (game time). */
  at: string;
  gameSeconds: number;
  period: Period;
  /** Class ids (schedule.json), in file order. */
  classes: string[];
}
export interface ClassTimes {
  /** The session under way, if any. */
  current: SessionTimes | null;
  /** Its door admits students now. */
  joinable: boolean;
  /** The next session to start. */
  next: SessionTimes;
}

/** A decoded `protocol::ServerMsg`. Field names are camelCase; positions are metres, yaws radians. */
export type ServerMessage =
  | { type: "welcome"; protocolVersion: number; yourId: number; character: CharacterInfo;
      appearanceVersion: number; appearance: Appearance; effectsVersion: number;
      effects: VisibleEffect[]; zone: Zone; spawn: Vec3; yaw: number;
      tickHz: number; snapshotHz: number;
      /** The server clock (Unix ms, UTC): correct the local clock by the difference. */
      unixMs: number }
  /** `states`: every entity updated by this snapshot, absolute (full states and applied deltas). `looks` (protocol v10): whom members of the visible set now look at (`target` null: nobody), only for changes; an entering entity looks at nobody unless listed. */
  | { type: "snapshot"; tick: number; entered: EntityInfo[]; states: EntityState[]; left: number[]; looks: LookChange[] }
  | { type: "roster"; online: number; nearby: number }
  /** `to` is set for whispers only. */
  | { type: "chat"; from: number; fromName: string; channel: ChatChannelName; to?: string; text: string }
  | { type: "correction"; seq: number; pos: Vec3 }
  | { type: "pong"; clientTimeMs: number; serverTimeMs: number }
  | { type: "queued"; position: number }
  | { type: "kick"; code: KickCode; reason: string }
  /** The line sent with `clientSeq` was not delivered: remove its local echo. */
  | { type: "chatRejected"; clientSeq: number; reason: ChatRejectReason }
  /** Swap scenes, drop every remote and reset prediction. */
  | { type: "zoneChanged"; zone: Zone; spawn: Vec3; yaw: number }
  | { type: "appearanceChanged"; id: number; appearanceVersion: number; appearance: Appearance }
  | { type: "dormState"; openToFriends: boolean; cap: number; items: PlacedFurniture[] }
  | { type: "dormFurniture"; edit: FurnitureEdit }
  | { type: "dormOpenChanged"; open: boolean }
  /** Protocol v9: `wishingRoom` set: an invitation into the inviter's Wishing Room. */
  | { type: "dormInviteReceived"; inviteId: number; fromName: string; expiresInSecs: number; wishingRoom: WishingRoomKind | null }
  | { type: "dormError"; code: DormErrorCode; itemId?: number }
  | { type: "friend"; event: FriendEvent }
  /** The friend this client invited declined. */
  | { type: "dormInviteDeclined"; byName: string }
  // ---- Protocol v4.
  /** The Crown balance (after welcome and on every change). */
  | { type: "wallet"; crowns: number }
  /** Unread owl mail (after welcome and whenever it changes). */
  | { type: "mailNotice"; unread: number }
  /** Open the station's UI; HTTP accepts its requests while the client stays in reach. */
  | { type: "stationOpened"; kind: StationKind; target: string; expiresInSecs: number }
  | { type: "findResult"; spot: string; outcome: FindOutcome }
  | { type: "tradeRequested"; tradeId: number; from: number; fromName: string; expiresInSecs: number }
  | { type: "tradeState"; view: TradeView }
  | { type: "tradeResult"; tradeId: number; outcome: TradeOutcome }
  | { type: "tradeError"; tradeId?: number; code: TradeErrorCode }
  /** A visible character's (or the client's own) effects changed. */
  | { type: "effectsChanged"; id: number; effectsVersion: number; effects: VisibleEffect[] }
  /**
   * The client's own effect timers, and the speed to predict with (`moveParams(speedPct)`);
   * protocol v8: the broom's (`SimWorld.flyParams(tier, broomPct)`).
   */
  | { type: "ownEffects"; effects: { kind: EffectKind; remainingSecs: number; fortune: FortuneKind | null }[]; speedPct: number; broomPct: number }
  | { type: "drank"; itemId: number; effect: EffectKind }
  | { type: "mimicDraughtRequest"; requestId: number; from: number; fromName: string; expiresInSecs: number }
  | { type: "mimicDraughtDeclined"; byName: string }
  | { type: "potionError"; code: PotionErrorCode; itemId?: number }
  // ---- Protocol v5.
  /** The client's level and XP (after welcome, and after every grant: `gained`). */
  | { type: "progress"; progress: Progress; gained: XpGain | null }
  /** A visible character's (or the client's own) level changed. */
  | { type: "levelChanged"; id: number; level: number; effectsVersion: number }
  /** Learned spells, the dueling loadout and its slots; `new`: just learned. */
  | { type: "spellbook"; learned: number[]; loadout: number[]; slots: number; new: number[] }
  /** Someone cast a spell outside a duel: play its gesture and colour. */
  | { type: "spellCast"; caster: number; spell: number; target: SpellTarget }
  /** A furniture piece of this room floats and bobs for `durationMs`. */
  | { type: "levitating"; itemId: number; by: number; durationMs: number }
  | { type: "spellError"; spell: number; code: SpellErrorCode }
  | { type: "npcEvent"; id: number; event: NpcEvent }
  | { type: "classJoined"; info: ClassInfo }
  | { type: "lessonPhaseChanged"; phase: LessonPhase; untilUnixMs: number }
  | { type: "lessonRound"; round: number; windowMs: number; beats: LessonBeat[] }
  /** Protocol v7 (was `gestureScored`): the verdict on a trace or a pick. */
  | { type: "beatScored"; round: number; beat: number; result: GestureResult }
  | { type: "classResult"; class: string; session: number; scorePct: number; grade: Grade; rewards: ClassRewards }
  | { type: "classError"; code: ClassErrorCode }
  /** A challenge to the client (or the echo of its own: `from` = yourId). */
  | { type: "duelChallenged"; challengeId: number; from: number; fromName: string; fromLevel: number; expiresInSecs: number }
  | { type: "duelChallengeEnded"; challengeId: number; reason: ChallengeEnd }
  | { type: "duelError"; challengeId: number | null; code: DuelErrorCode }
  | { type: "duelStart"; duelId: number; you: DuelRole; a: DuelistInfo; b: DuelistInfo; poiseMax: number; timeLimitMs: number; state: DuelSnapshot }
  | { type: "duelEvents"; duelId: number; atMs: number; events: DuelEvent[] }
  | { type: "duelResult"; duelId: number; winner: DuelSide | null; reason: DuelEndReason; poise: [number, number]; rewards: DuelRewards | null; returnInMs: number }
  | { type: "duelList"; duels: DuelSummary[] }
  // ---- Protocol v8.
  /**
   * The mount state: after every `encodeMount` (`reason` when refused) and whenever the server
   * changes it. `tier` (1.., 0 when not mounted) and `ownEffects.broomPct` give the flight
   * numbers (`SimWorld.flyParams`); on mounting, lift to `SimWorld.mount(x, y, z)`'s position.
   */
  | { type: "mounted"; on: boolean; tier: number; reason: MountRefused | null }
  // ---- Protocol v9.
  /**
   * The Wishing Room's door shows for this client on the blank wall until `openUntilMs`
   * (the server's Unix ms); 0 takes it away. Using it offers the room choice (`encodeEnterWishingRoom`).
   */
  | { type: "wishingRoomDoor"; openUntilMs: number }
  /** A secret found for the first time (`SimWorld.secrets()` id): toast it and draw it on the map. */
  | { type: "discovered"; secret: string; xp: number; housePoint: boolean; recipe: number | null }
  // ---- Protocol v11: spells to level 20.
  /**
   * The server moves this client's character (Adveni): slide from where it is to `to` over `moveMs`,
   * then stand still until `holdMs` (both from now). Ignore input meanwhile; moves are dropped, not corrected.
   */
  | { type: "forcedMove"; to: QPosCm; moveMs: number; holdMs: number; by: number }
  /** Aperio, to its caster only: these spots shine for `durationMs` (nearest first, at most 32). */
  | { type: "revealed"; spell: number; durationMs: number; spots: { kind: RevealKind; pos: QPosCm }[] }
  // ---- Protocol v12: NPCs in the open castle.
  /** The answer to `encodeNpcInteract(id)`, to this client only: open the talk card (the line is ours: `NpcBook.pick`). */
  | { type: "npcReply"; id: number; reply: NpcReply }
  // ---- Protocol v14: parties.
  /** An invitation into `fromName`'s party: answer with `encodePartyRespond(inviteId, accept)` within `expiresInSecs`. */
  | { type: "partyInvited"; inviteId: number; fromName: string; fromHouse: House; fromLevel: number; expiresInSecs: number }
  /** The client's party after any change, or null: in no party. */
  | { type: "partyState"; party: PartyView | null }
  | { type: "partyNotice"; notice: PartyNotice }
  | { type: "partyError"; code: PartyErrorCode }
  // ---- Protocol v15: clubs.
  /** An invitation into a club from one of its officers (or its owner): answer with `encodeClubRespond(inviteId, accept)` within `expiresInSecs`. */
  | { type: "clubInvited"; inviteId: number; clubName: string; clubTag: string; fromName: string; expiresInSecs: number }
  /** The client's club after a change of members, ranks, name, tag or message of the day, or null: in no club. */
  | { type: "clubState"; club: ClubView | null }
  /** A member came online, went offline or changed level (that row only). */
  | { type: "clubPresence"; characterId: number; online: boolean; level: number }
  | { type: "clubNotice"; notice: ClubNotice }
  | { type: "clubError"; code: ClubErrorCode };

// ---- Protocol v14: parties.

/** Where an online party member is (`kind` is the place's kind; `class` and `venue` are data ids). */
export type PartyPlace =
  | { kind: "castle"; place: string }
  | { kind: "dorm"; ownerName: string; house: House }
  | { kind: "wishingRoom"; ownerName: string }
  | { kind: "class"; class: string }
  | { kind: "arena"; venue: string };
/**
 * An online member: their entity (nameplate, snapshots), their zone (members with the same `zone`
 * are together and see each other in their snapshots whatever the distance; the castle is 0) and place.
 */
export interface PartyPresence { entity: number; zone: number; place: PartyPlace }
export interface PartyMember {
  characterId: number; name: string; house: House;
  /** The level (1-20), the last one known while offline. */
  level: number;
  /** Null while offline. */
  online: PartyPresence | null;
}
/** A party: the leader's character id, and every member (the client too), the leader first. */
export interface PartyView { leader: number; members: PartyMember[] }
/** What just happened, for the chat log; `name` is the client's own when it happened to them. */
export type PartyNotice =
  | { kind: "inviteSent"; name: string }
  | { kind: "declined"; name: string }
  | { kind: "joined"; name: string }
  | { kind: "left"; name: string }
  | { kind: "removed"; name: string }
  | { kind: "leader"; name: string }
  | { kind: "disbanded" };
export type PartyErrorCode =
  | "not_online" | "self_invite" | "in_party" | "full" | "not_in_party" | "not_leader"
  | "not_member" | "invite_expired" | "blocked" | "rate_limited";

// ---- Protocol v15: clubs.

/** A member's rank: the owner (the president), officers, members. */
export type ClubRole = "member" | "officer" | "owner";
export interface ClubMember {
  characterId: number; name: string; house: House;
  /** The level (1-20), the last one known while offline. */
  level: number;
  role: ClubRole;
  online: boolean;
}
/** A club: every member (the client too), the owner first, then officers, then members, each longest-serving first. */
export interface ClubView { id: number; name: string; tag: string; motd: string; members: ClubMember[] }
/** What just happened, for the chat log; `name` is the client's own when it happened to them. */
export type ClubNotice =
  | { kind: "founded"; club: string }
  | { kind: "inviteSent"; name: string }
  | { kind: "declined"; name: string }
  | { kind: "joined"; name: string }
  | { kind: "left"; name: string }
  | { kind: "removed"; name: string }
  | { kind: "promoted"; name: string }
  | { kind: "demoted"; name: string }
  | { kind: "owner"; name: string }
  | { kind: "renamed"; club: string; tag: string }
  | { kind: "motd"; name: string }
  | { kind: "disbanded"; club: string };
export type ClubErrorCode =
  | "not_online" | "self_invite" | "in_club" | "already_in_club" | "full" | "not_in_club"
  | "not_allowed" | "not_member" | "invite_expired" | "blocked" | "rate_limited"
  | "name_invalid" | "tag_invalid" | "filtered" | "name_taken" | "tag_taken" | "motd_invalid"
  | "level_too_low" | "not_enough_crowns" | "too_far" | "failed";

/** One hidden passage (`data/world/secrets.json`; `SimWorld.secrets()`). */
export interface SecretView {
  id: string;
  /** Our own name: show it only once found. */
  name: string;
  entrance: "tapestry" | "panel" | "painting" | "statue" | "wall";
  /** Its layout surfaces (all `secret`) and its two doors. */
  surfaces: string[];
  doors: string[];
  reward: { xp: number; recipe?: number };
}
/** A surface as the map draws it (`SimWorld.mapSurfaces()`). */
export interface MapSurface {
  id: string;
  name: string;
  kind: "room" | "corridor" | "door" | "stair" | "courtyard" | "grounds" | "bridge";
  /** The floor (the map's layer; 0 = ground floor). */
  level: number;
  cell: string;
  min: [number, number];
  max: [number, number];
  y: number;
  yTop?: number;
  /** Never draw it before its secret is found. */
  secret: boolean;
  /** A moving flight's position (draw the one walked now). */
  moving?: { flight: string; position: "a" | "b" };
}
/** The Wishing Room's rules (`SimWorld.wishingRoom()`). */
export interface WishingRoomView {
  corridor: string;
  marks: [string, string];
  passes: number;
  window_s: number;
  door: string;
  door_open_s: number;
  capacity: number;
  rooms: { room: WishingRoomKind; name: string; layout: string }[];
}
"#;

// ------------------------------------------------------------------ world

/// A layout with its spatial index, plus the movement and placement rules.
#[wasm_bindgen]
pub struct SimWorld {
    /// The world moves are predicted in: the current staircase state's.
    world: std::sync::Arc<World>,
    params: MoveParams,
    /// The world's open sky, and the broom rules once set.
    air: sim_core::Airspace,
    flight: Option<sim_core::FlightRules>,
    /// The layout with both positions of every moving flight (the
    /// house's view), its schedule and one world per staircase state once
    /// `setStairs` ran, and the state predicted in.
    full: sim_core::Layout,
    stairs: Option<(sim_core::Stairs, sim_core::Worlds)>,
    state: u8,
    /// The castle's secrets once `setSecrets` ran.
    secrets: Option<sim_core::Secrets>,
    /// Solid furniture: the world without placed pieces (the staircase
    /// state's), and the pieces `setFurniture` gave (a dorm's).
    bare: std::sync::Arc<World>,
    furniture: Vec<sim_core::Blocker>,
}

/// A placed piece for `SimWorld.setFurniture` / `keepsWay`: the catalog
/// entry's `furniture` block, where it stands and its yaw (radians).
#[derive(serde::Deserialize)]
struct FurniturePiece {
    id: f64,
    furniture: sim_core::catalog::FurnitureDef,
    pos: [f32; 3],
    yaw: f32,
}

impl FurniturePiece {
    fn blocker(&self) -> Option<sim_core::Blocker> {
        self.furniture
            .blocker(&format!("furniture/{}", self.id), self.pos, self.yaw)
    }
}

impl SimWorld {
    /// The world predicted in: the state's, with the placed pieces solid.
    fn refurnish(&mut self) {
        self.world = if self.furniture.is_empty() {
            self.bare.clone()
        } else {
            std::sync::Arc::new(self.bare.with_solids(self.furniture.iter().cloned()))
        };
    }
}

#[wasm_bindgen]
impl SimWorld {
    /// Solid furniture: the dorm's placed pieces (`FurniturePiece[]`: `{ id,
    /// furniture, pos, yaw }`, the catalog's `furniture` block of each) are
    /// solid from now on, as the server has them (`FurnitureDef::blocker`:
    /// floor pieces taller than a step). Call it whenever the room changes.
    #[wasm_bindgen(js_name = setFurniture)]
    pub fn set_furniture(
        &mut self,
        #[wasm_bindgen(unchecked_param_type = "FurniturePiece[]")] pieces: JsValue,
    ) -> Result<(), JsError> {
        let pieces: Vec<FurniturePiece> = from_js_via_json(&pieces)?;
        self.furniture = pieces.iter().filter_map(FurniturePiece::blocker).collect();
        self.refurnish();
        Ok(())
    }

    /// The server's solid furniture rule (`sim_core::keeps_way`) for placing
    /// `piece` in a room holding `room` (the piece being moved left out),
    /// with characters at `people`: not on anyone or the entry, and the way
    /// out and to everyone still open. True for a piece that is not solid.
    #[wasm_bindgen(js_name = keepsWay)]
    pub fn keeps_way(
        &self,
        #[wasm_bindgen(unchecked_param_type = "FurniturePiece[]")] room: JsValue,
        #[wasm_bindgen(unchecked_param_type = "FurniturePiece")] piece: JsValue,
        #[wasm_bindgen(unchecked_param_type = "Vec3[]")] people: JsValue,
    ) -> Result<bool, JsError> {
        let room: Vec<FurniturePiece> = from_js_via_json(&room)?;
        let piece: FurniturePiece = from_js_via_json(&piece)?;
        let people: Vec<[f32; 3]> = from_js_via_json(&people)?;
        let Some(piece) = piece.blocker() else {
            return Ok(true);
        };
        let room: Vec<sim_core::Blocker> =
            room.iter().filter_map(FurniturePiece::blocker).collect();
        Ok(sim_core::keeps_way(
            &self.bare,
            &self.params,
            &room,
            &piece,
            &people,
        ))
    }
    /// Parses and validates a layout file (`data/world/layout.json` or an
    /// instance layout). With `house`, the world is the one that house walks:
    /// surfaces gated to other houses are walls, as the server enforces. Pass
    /// the player's house for the castle. Throws on an invalid layout.
    #[wasm_bindgen(constructor)]
    pub fn new(
        #[wasm_bindgen(js_name = layoutJson)] layout_json: &str,
        #[wasm_bindgen(unchecked_optional_param_type = "House")] house: Option<String>,
    ) -> Result<SimWorld, JsError> {
        // The moving staircase flights at rest in position A until
        // `setStairs` and `setStairState` (a layout without them is unchanged).
        let mut full =
            sim_core::Layout::from_json(layout_json).map_err(|e| JsError::new(&e.to_string()))?;
        if let Some(h) = house {
            let house = House::from_id(&h).ok_or_else(|| JsError::new("unknown house"))?;
            full = full.for_house(house);
        }
        let world = World::new(full.for_stairs(0)).map_err(|e| JsError::new(&e.to_string()))?;
        let air = sim_core::Airspace::new(&world);
        let world = std::sync::Arc::new(world);
        Ok(SimWorld {
            world: world.clone(),
            bare: world,
            furniture: Vec::new(),
            params: MoveParams::default(),
            air,
            flight: None,
            full,
            stairs: None,
            state: 0,
            secrets: None,
        })
    }

    // ---- Secrets and the map (`sim_core::secrets`).

    /// Sets the castle's secrets (`data/world/secrets.json`), checked against
    /// this layout (the castle's, both positions of every flight). Throws on
    /// invalid data.
    #[wasm_bindgen(js_name = setSecrets)]
    pub fn set_secrets(
        &mut self,
        #[wasm_bindgen(js_name = secretsJson)] secrets_json: &str,
    ) -> Result<(), JsError> {
        // A house's view drops other houses' gated rooms only: every secret
        // surface (never gated) is in it.
        let secrets = sim_core::Secrets::from_json(secrets_json, &self.full)
            .map_err(|e| JsError::new(&e.to_string()))?;
        self.secrets = Some(secrets);
        Ok(())
    }

    /// The map's reveal rule: the id of the surface a character at `(x, y, z)`
    /// stands on in the state predicted now (reveal it whole), or `undefined`.
    #[wasm_bindgen(js_name = revealAt)]
    pub fn reveal_at(&self, x: f32, y: f32, z: f32) -> Option<String> {
        sim_core::reveal_at(&self.world, [x, y, z]).map(|s| s.id.clone())
    }

    /// The secret (id) surface `surfaceId` belongs to, or `undefined` (public
    /// ground, or before `setSecrets`).
    #[wasm_bindgen(js_name = secretOf)]
    pub fn secret_of(
        &self,
        #[wasm_bindgen(js_name = surfaceId)] surface_id: &str,
    ) -> Option<String> {
        self.secrets
            .as_ref()?
            .secret_of(surface_id)
            .map(|s| s.id.clone())
    }

    /// The secrets (`[]` before `setSecrets`). Never show an unfound one's name.
    #[wasm_bindgen(unchecked_return_type = "SecretView[]")]
    pub fn secrets(&self) -> Result<JsValue, JsError> {
        let list: &[sim_core::Secret] = self.secrets.as_ref().map_or(&[], |s| s.secrets());
        to_js_via_json(&list)
    }

    /// The Wishing Room's rules (the corridor, the marks, the door, the
    /// rooms), or `undefined` before `setSecrets`.
    #[wasm_bindgen(js_name = wishingRoom, unchecked_return_type = "WishingRoomView | undefined")]
    pub fn wishing_room(&self) -> Result<JsValue, JsError> {
        match &self.secrets {
            Some(s) => to_js_via_json(s.wishing_room()),
            None => Ok(JsValue::UNDEFINED),
        }
    }

    /// Every surface of this world's layout (both positions of every moving
    /// flight) as the map draws them, in file order.
    #[wasm_bindgen(js_name = mapSurfaces, unchecked_return_type = "MapSurface[]")]
    pub fn map_surfaces(&self) -> Result<JsValue, JsError> {
        let list: Vec<serde_json::Value> = self
            .full
            .surfaces
            .iter()
            .map(|s| {
                let mut v = serde_json::json!({
                    "id": s.id, "name": s.name, "kind": s.kind, "level": s.level,
                    "cell": s.cell, "min": s.min, "max": s.max, "y": s.y,
                    "secret": s.secret,
                });
                if let Some(t) = s.y_top {
                    v["yTop"] = t.into();
                }
                if let Some(m) = &s.moving {
                    v["moving"] = serde_json::json!({ "flight": m.flight, "position": m.position });
                }
                v
            })
            .collect();
        to_js_via_json(&list)
    }

    // ---- The moving staircases (`sim_core::stairs`), from the
    // server's clock (`welcome.unixMs` and `pong` corrections): no message.

    /// Sets the staircase schedule (`data/world/stairs.json`) and builds one
    /// world per staircase state (the castle only). Throws on invalid data or
    /// a layout without moving flights. `periodS` (development only, the same
    /// on every end: the server's `SHS_STAIRS_PERIOD_S`, the load test's
    /// `--stairs-period-s`) shortens the period.
    #[wasm_bindgen(js_name = setStairs)]
    pub fn set_stairs(
        &mut self,
        #[wasm_bindgen(js_name = stairsJson)] stairs_json: &str,
        #[wasm_bindgen(js_name = periodS)] period_s: Option<u32>,
    ) -> Result<(), JsError> {
        let mut stairs = sim_core::Stairs::from_json(stairs_json, &self.full)
            .map_err(|e| JsError::new(&e.to_string()))?;
        if let Some(p) = period_s {
            stairs = stairs
                .with_period(p)
                .map_err(|e| JsError::new(&e.to_string()))?;
        }
        let worlds = sim_core::Worlds::new(&self.full).map_err(|e| JsError::new(&e.to_string()))?;
        self.bare = worlds.get(self.state).clone();
        self.stairs = Some((stairs, worlds));
        self.refurnish();
        Ok(())
    }

    /// The staircase state walked at `unixMs` (bit i: flight i at B); 0
    /// before `setStairs`.
    #[wasm_bindgen(js_name = stairState)]
    pub fn stair_state(&self, #[wasm_bindgen(js_name = unixMs)] unix_ms: f64) -> u8 {
        self.stairs
            .as_ref()
            .map_or(0, |(s, _)| s.state(unix_ms as i64))
    }

    /// The states a move sent at `unixMs` is accepted against (the grace
    /// window either side of a switch): the current one first.
    #[wasm_bindgen(js_name = statesNear)]
    pub fn states_near(&self, #[wasm_bindgen(js_name = unixMs)] unix_ms: f64) -> Vec<u8> {
        self.stairs
            .as_ref()
            .map_or(vec![0], |(s, _)| s.states_near(unix_ms as i64))
    }

    /// Predicts moves (`moveAndSlide`, `groundAt`, ...) in staircase state
    /// `state` from now on: call it when `stairState` changes, after carrying
    /// the player with `carry`.
    #[wasm_bindgen(js_name = setStairState)]
    pub fn set_stair_state(&mut self, state: u8) {
        self.state = state;
        if let Some((_, worlds)) = &self.stairs {
            self.bare = worlds.get(state).clone();
            self.refurnish();
        }
    }

    /// The staircase state predicted in now.
    #[wasm_bindgen(js_name = currentStairState, getter)]
    pub fn current_stair_state(&self) -> u8 {
        self.state
    }

    /// The moving flights (`stairs.json` `flights`: ids, surfaces, pivots,
    /// angles, gates), in bit order; `[]` before `setStairs`.
    #[wasm_bindgen(js_name = stairFlights, unchecked_return_type = "StairFlightDef[]")]
    pub fn stair_flights(&self) -> Result<JsValue, JsError> {
        let defs: &[sim_core::stairs::FlightDef] =
            self.stairs.as_ref().map_or(&[], |(s, _)| s.flights());
        to_js_via_json(&defs)
    }

    /// Every moving flight at `unixMs`: the position walked, whether it
    /// rests (and warns) or swings, the turn of its A model (degrees) and
    /// how far each of its gates is open.
    #[wasm_bindgen(js_name = flightStates, unchecked_return_type = "FlightView[]")]
    pub fn flight_states(
        &self,
        #[wasm_bindgen(js_name = unixMs)] unix_ms: f64,
    ) -> Result<JsValue, JsError> {
        use sim_core::stairs::FlightPhase;
        let Some((s, _)) = &self.stairs else {
            return to_js_via_json(&Vec::<serde_json::Value>::new());
        };
        let t = unix_ms as i64;
        let pos = |p: sim_core::StairPosition| match p {
            sim_core::StairPosition::A => "a",
            sim_core::StairPosition::B => "b",
        };
        let list: Vec<serde_json::Value> = s
            .flights()
            .iter()
            .enumerate()
            .map(|(i, f)| {
                let st = s.flight_state(i, t);
                let (phase, warning, progress) = match st.phase {
                    FlightPhase::Rest { warning, .. } => ("rest", warning, 0.0),
                    FlightPhase::Swing { progress, .. } => ("swing", false, progress),
                };
                let gates: Vec<serde_json::Value> = f
                    .gates
                    .iter()
                    .map(|g| serde_json::json!({ "id": g.id, "open": s.gate_open(i, g.position, t) }))
                    .collect();
                serde_json::json!({
                    "id": f.id,
                    "walk": pos(st.walk),
                    "phase": phase,
                    "warning": warning,
                    "progress": progress,
                    "turnDeg": s.turn_deg(i, t),
                    "lastSwitchMs": st.last_switch_ms as f64,
                    "nextSwitchMs": st.next_switch_ms as f64,
                    "gates": gates,
                })
            })
            .collect();
        to_js_via_json(&list)
    }

    /// Where a rider at `(x, y, z)` on flight `flight`'s position `from`
    /// stands after its switch to `to` (`"a"` / `"b"`), or `undefined` when
    /// not on it: its ground in the predicted world (call it before
    /// `setStairState`) is the flight's surface (`Stairs::carry_rider`, the
    /// rule the server and the bots carry riders with at each switch).
    #[allow(clippy::too_many_arguments)]
    pub fn carry(
        &self,
        x: f32,
        y: f32,
        z: f32,
        flight: u32,
        #[wasm_bindgen(unchecked_param_type = "\"a\" | \"b\"")] from: &str,
        #[wasm_bindgen(unchecked_param_type = "\"a\" | \"b\"")] to: &str,
    ) -> Option<Vec<f32>> {
        let p = |v: &str| match v {
            "a" => Some(sim_core::StairPosition::A),
            "b" => Some(sim_core::StairPosition::B),
            _ => None,
        };
        let (s, _) = self.stairs.as_ref()?;
        s.carry_rider(
            &self.full,
            &self.world,
            [x, y, z],
            flight as usize,
            p(from)?,
            p(to)?,
        )
        .map(|q| q.to_vec())
    }

    // ---- Flying (`sim_core::flight`), predicted with the server's code.

    /// Sets the broom rules (`data/flying.json`); the flight methods below
    /// need them. Throws on invalid rules.
    #[wasm_bindgen(js_name = setFlightRules)]
    pub fn set_flight_rules(
        &mut self,
        #[wasm_bindgen(js_name = flyingJson)] flying_json: &str,
    ) -> Result<(), JsError> {
        let rules = sim_core::FlightRules::from_json(flying_json)
            .map_err(|e| JsError::new(&e.to_string()))?;
        self.flight = Some(rules);
        Ok(())
    }

    fn fly(&self, tier: u8, broom_pct: u16) -> Result<sim_core::FlyParams, JsError> {
        let rules = self
            .flight
            .as_ref()
            .ok_or_else(|| JsError::new("setFlightRules first"))?;
        Ok(rules.params(tier, broom_pct))
    }

    /// Whether this world has open sky at all (a broom may be mounted
    /// somewhere): the castle's grounds and the Flying class's field.
    #[wasm_bindgen(js_name = hasAirspace, getter)]
    pub fn has_airspace(&self) -> bool {
        !self.air.is_empty()
    }

    /// The flight numbers of speed tier `tier` (`mounted.tier`) at
    /// `broomPct` (`ownEffects.broomPct`), as the server checks them.
    #[wasm_bindgen(js_name = flyParams, unchecked_return_type = "FlyParams")]
    pub fn fly_params(
        &self,
        tier: u8,
        #[wasm_bindgen(js_name = broomPct)] broom_pct: u16,
    ) -> Result<JsValue, JsError> {
        let p = self.fly(tier, broom_pct)?;
        let r = self.flight.as_ref().expect("checked");
        let o = Object::new();
        set(&o, "speed", short_f64(p.speed));
        set(&o, "climbSpeed", short_f64(p.climb_speed));
        set(&o, "acceleration", short_f64(r.acceleration));
        set(&o, "clearance", short_f64(p.clearance));
        set(&o, "margin", short_f64(p.margin));
        set(&o, "dismountHeight", short_f64(r.dismount_height));
        set(&o, "ceilingY", short_f64(r.ceiling_y));
        Ok(o.into())
    }

    /// Moves a flyer at `(x, y, z)` by `(dx, dy, dz)` metres within the
    /// airspace (sliding along its edges and the no-fly boxes, never under
    /// the floor); returns the new `[x, y, z]`. Call it every frame while
    /// mounted with the broom's velocity times the frame time.
    #[wasm_bindgen(js_name = flyAndSlide)]
    #[allow(clippy::too_many_arguments)]
    pub fn fly_and_slide(
        &self,
        x: f32,
        y: f32,
        z: f32,
        dx: f32,
        dy: f32,
        dz: f32,
        tier: u8,
        #[wasm_bindgen(js_name = broomPct)] broom_pct: u16,
    ) -> Result<Vec<f32>, JsError> {
        let p = self.fly(tier, broom_pct)?;
        Ok(sim_core::fly_and_slide(&self.world, &self.air, &p, [x, y, z], [dx, dy, dz]).to_vec())
    }

    /// The server's verdict on a flight move from `(px, py, pz)` to
    /// `(x, y, z)` over `dt` seconds: `undefined` when accepted, else
    /// `"out_of_airspace"`, `"no_fly"`, `"too_fast"` or `"below_ground"`
    /// (debugging and tests; an honest client never sees one).
    #[wasm_bindgen(js_name = checkFlight)]
    #[allow(clippy::too_many_arguments)]
    pub fn check_flight(
        &self,
        px: f32,
        py: f32,
        pz: f32,
        x: f32,
        y: f32,
        z: f32,
        dt: f32,
        tier: u8,
        #[wasm_bindgen(js_name = broomPct)] broom_pct: u16,
    ) -> Result<Option<String>, JsError> {
        use sim_core::{FlightCheck, FlightReject};
        let p = self.fly(tier, broom_pct)?;
        let check = sim_core::check_flight(&self.world, &self.air, &p, [px, py, pz], [x, y, z], dt);
        Ok(match check {
            FlightCheck::Accept { .. } => None,
            FlightCheck::Reject(r) => Some(
                match r {
                    FlightReject::OutOfAirspace => "out_of_airspace",
                    FlightReject::NoFly => "no_fly",
                    FlightReject::TooFast => "too_fast",
                    FlightReject::BelowGround => "below_ground",
                }
                .to_string(),
            ),
        })
    }

    /// Mounting where the character stands at `(x, y, z)`: `{ pos }` (where
    /// the broom lifts it) or `{ refused }` (the reason the server would
    /// give, before its own skill and busy checks).
    #[wasm_bindgen(unchecked_return_type = "{ pos: Vec3 } | { refused: MountRefused }")]
    pub fn mount(&self, x: f32, y: f32, z: f32) -> Result<JsValue, JsError> {
        let p = self.fly(1, protocol::BROOM_PCT_NORMAL)?;
        let o = Object::new();
        match sim_core::flight::mount(&self.world, &self.air, &p, [x, y, z]) {
            Ok(pos) => set(&o, "pos", f32_array(&pos)),
            Err(r) => set(&o, "refused", r.id()),
        }
        Ok(o.into())
    }

    /// Landing a flyer at `(x, y, z)`: where it stands (`[x, y, z]`), or
    /// `undefined` when no walkable ground is within the dismount height
    /// below (land first; never over the water).
    pub fn dismount(&self, x: f32, y: f32, z: f32) -> Result<Option<Vec<f32>>, JsError> {
        let rules = self
            .flight
            .as_ref()
            .ok_or_else(|| JsError::new("setFlightRules first"))?;
        Ok(sim_core::flight::dismount(&self.world, rules, [x, y, z])
            .ok()
            .map(|p| p.to_vec()))
    }

    /// The floor under `(x, z)` for a flyer at height `y` (the ground, or
    /// the water's floor), for the HUD's height and the flight camera;
    /// `undefined` outside the airspace.
    #[wasm_bindgen(js_name = floorAt)]
    pub fn floor_at(&self, x: f32, z: f32, y: f32) -> Option<f32> {
        self.air.floor_at(&self.world, x, z, y)
    }

    /// The layout's ring course (the Flying class), in order: `ring` beats
    /// index it.
    #[wasm_bindgen(unchecked_return_type = "Ring[]")]
    pub fn rings(&self) -> Result<JsValue, JsError> {
        to_js_via_json(&self.world.layout().rings)
    }

    /// Whether the move from `(ax, ay, az)` to `(bx, by, bz)` passes ring
    /// `ring` (the server's test, `sim_core::ring::crossing`), for the ring
    /// course's own feedback. False for an unknown ring.
    #[wasm_bindgen(js_name = passesRing)]
    #[allow(clippy::too_many_arguments)]
    pub fn passes_ring(
        &self,
        ring: u32,
        ax: f32,
        ay: f32,
        az: f32,
        bx: f32,
        by: f32,
        bz: f32,
    ) -> bool {
        self.world
            .layout()
            .rings
            .get(ring as usize)
            .is_some_and(|r| sim_core::ring::crossing(r, [ax, ay, az], [bx, by, bz]).is_some())
    }

    /// Moves a character at `(x, y, z)` by `(dx, dz)` metres with collision and
    /// wall sliding; returns the new `[x, y, z]` on the ground. Call it every
    /// frame with `speed * dt` along the input direction.
    #[wasm_bindgen(js_name = moveAndSlide)]
    pub fn move_and_slide(&self, x: f32, y: f32, z: f32, dx: f32, dz: f32) -> Vec<f32> {
        sim_core::move_and_slide(&self.world, &self.params, [x, y, z], [dx, dz]).to_vec()
    }

    /// Ground height at `(x, z)` on the floor nearest `nearY` (within 1 m), or
    /// `undefined` off the walkable area.
    #[wasm_bindgen(js_name = groundAt)]
    pub fn ground_at(
        &self,
        x: f32,
        z: f32,
        #[wasm_bindgen(js_name = nearY)] near_y: f32,
    ) -> Option<f32> {
        self.world.ground_at(x, z, near_y).map(|g| g.y)
    }

    /// Floor level (0 = ground floor) at `(x, z)` for height `nearY`.
    #[wasm_bindgen(js_name = levelAt)]
    pub fn level_at(
        &self,
        x: f32,
        z: f32,
        #[wasm_bindgen(js_name = nearY)] near_y: f32,
    ) -> Option<i32> {
        self.world.level_at(x, z, near_y)
    }

    /// Id of the surface under `(x, z)` for height `nearY`, for debug overlays.
    #[wasm_bindgen(js_name = surfaceIdAt)]
    pub fn surface_id_at(
        &self,
        x: f32,
        z: f32,
        #[wasm_bindgen(js_name = nearY)] near_y: f32,
    ) -> Option<String> {
        let g = self.world.ground_at(x, z, near_y)?;
        Some(self.world.surface(g.surface).id.clone())
    }

    /// Whether the ground under `(x, z)` for height `nearY` is open
    /// to the sky (a surface with no ceiling: the grounds, the courtyard).
    /// False off the walkable area. The client draws the far silhouette
    /// (HLOD) and the sun's shadow on the terrain only from outdoors.
    #[wasm_bindgen(js_name = outdoorsAt)]
    pub fn outdoors_at(
        &self,
        x: f32,
        z: f32,
        #[wasm_bindgen(js_name = nearY)] near_y: f32,
    ) -> bool {
        self.world
            .ground_at(x, z, near_y)
            .is_some_and(|g| self.world.surface(g.surface).ceiling.is_none())
    }

    /// The layout cell of the surface under `(x, z)` for height
    /// `nearY` (`grounds_r2_c3`, `courtyard`), or `undefined` off the
    /// walkable area.
    #[wasm_bindgen(js_name = cellAt)]
    pub fn cell_at(
        &self,
        x: f32,
        z: f32,
        #[wasm_bindgen(js_name = nearY)] near_y: f32,
    ) -> Option<String> {
        let g = self.world.ground_at(x, z, near_y)?;
        Some(self.world.surface(g.surface).cell.clone())
    }

    #[wasm_bindgen(js_name = surfaceCount, getter)]
    pub fn surface_count(&self) -> u32 {
        self.world.surfaces().len() as u32
    }

    #[wasm_bindgen(unchecked_return_type = "Spawn[]")]
    pub fn spawns(&self) -> Result<JsValue, JsError> {
        to_js_via_json(&self.world.spawns())
    }

    #[wasm_bindgen(unchecked_return_type = "Landmark[]")]
    pub fn landmarks(&self) -> Result<JsValue, JsError> {
        to_js_via_json(&self.world.layout().landmarks)
    }

    /// The interactables this world's characters may use.
    #[wasm_bindgen(unchecked_return_type = "Interactable[]")]
    pub fn interactables(&self) -> Result<JsValue, JsError> {
        to_js_via_json(&self.world.interactables())
    }

    #[wasm_bindgen(js_name = placeAreas, unchecked_return_type = "PlaceArea[]")]
    pub fn place_areas(&self) -> Result<JsValue, JsError> {
        to_js_via_json(&self.world.place_areas())
    }

    /// Whether interactable `id` is within reach of a character at `(x, y, z)`
    /// (the server's rule for `Interact`). False for unknown ids.
    #[wasm_bindgen(js_name = inReach)]
    pub fn in_reach(&self, id: &str, x: f32, y: f32, z: f32) -> bool {
        self.world
            .interactable(id)
            .is_some_and(|i| i.in_reach([x, y, z]))
    }

    /// The server's furniture placement rule (`sim_core::placement`): may an
    /// item with this catalog `surface`, `footprint` (`width` along X, `depth`
    /// along Z) and `height` stand at `(x, y, z)` turned by `yaw` radians?
    /// `tops` are the table areas of the placed pieces (`furnitureTopAreas`
    /// of each), on top of the room shell's own areas.
    #[wasm_bindgen(js_name = canPlace)]
    #[allow(clippy::too_many_arguments)]
    pub fn can_place(
        &self,
        #[wasm_bindgen(unchecked_param_type = "PlaceSurface")] surface: &str,
        width: f32,
        depth: f32,
        height: f32,
        x: f32,
        y: f32,
        z: f32,
        yaw: f32,
        #[wasm_bindgen(unchecked_param_type = "PlaceArea[]")] tops: JsValue,
    ) -> Result<bool, JsError> {
        let mut areas: Vec<PlaceArea> = self.world.place_areas().to_vec();
        areas.extend(from_js_via_json::<Vec<PlaceArea>>(&tops)?);
        placement_fits_in(&areas, surface, [width, depth, height], [x, y, z], yaw)
    }
}

/// The table areas a furniture piece offers where it stands (its top and
/// shelves, turning with it): `sim_core::catalog::FurnitureDef::top_areas`.
/// `furniture` is the catalog entry's `furniture` block; ids are `id` and
/// `id.shelf<n>`.
#[wasm_bindgen(js_name = furnitureTopAreas, unchecked_return_type = "PlaceArea[]")]
pub fn furniture_top_areas(
    #[wasm_bindgen(unchecked_param_type = "object")] furniture: JsValue,
    id: &str,
    x: f32,
    y: f32,
    z: f32,
    yaw: f32,
) -> Result<JsValue, JsError> {
    let def: sim_core::catalog::FurnitureDef = from_js_via_json(&furniture)?;
    to_js_via_json(&def.top_areas(id, [x, y, z], yaw))
}

/// The placement rule against `areas` only: whether a table item placed at
/// `(x, y, z)`, `yaw` rests on one of them (the server's "something stands
/// on it" check, `server::dorm::rules::holds_items`).
#[wasm_bindgen(js_name = placementFits)]
#[allow(clippy::too_many_arguments)]
pub fn placement_fits(
    #[wasm_bindgen(unchecked_param_type = "PlaceArea[]")] areas: JsValue,
    #[wasm_bindgen(unchecked_param_type = "PlaceSurface")] surface: &str,
    width: f32,
    depth: f32,
    height: f32,
    x: f32,
    y: f32,
    z: f32,
    yaw: f32,
) -> Result<bool, JsError> {
    let areas: Vec<PlaceArea> = from_js_via_json(&areas)?;
    placement_fits_in(&areas, surface, [width, depth, height], [x, y, z], yaw)
}

fn placement_fits_in(
    areas: &[PlaceArea],
    surface: &str,
    [width, depth, height]: [f32; 3],
    pos: [f32; 3],
    yaw: f32,
) -> Result<bool, JsError> {
    let surface = match surface {
        "floor" => PlaceSurface::Floor,
        "wall" => PlaceSurface::Wall,
        "table" => PlaceSurface::Table,
        other => return Err(JsError::new(&format!("unknown surface {other:?}"))),
    };
    let shape = FurnitureShape {
        surface,
        footprint: [width, depth],
        height,
    };
    Ok(check_placement(areas, &shape, pos, yaw).is_ok())
}

/// A JS value read through `JSON.stringify` (small, rarely called).
fn from_js_via_json<T: serde::de::DeserializeOwned>(v: &JsValue) -> Result<T, JsError> {
    let json = js_sys::JSON::stringify(v).map_err(|_| JsError::new("JSON.stringify failed"))?;
    let json = json.as_string().unwrap_or_default();
    serde_json::from_str(&json).map_err(|e| JsError::new(&e.to_string()))
}

fn f32_array(v: &[f32]) -> JsValue {
    let a = Array::new_with_length(v.len() as u32);
    for (i, &x) in v.iter().enumerate() {
        a.set(i as u32, JsValue::from_f64(short_f64(x)));
    }
    a.into()
}

/// Spawns and landmarks are read once, so a JSON round trip is fine and keeps
/// numbers as written in the layout file (no f32 → f64 noise).
fn to_js_via_json<T: serde::Serialize>(value: &T) -> Result<JsValue, JsError> {
    let json = serde_json::to_string(value).map_err(|e| JsError::new(&e.to_string()))?;
    js_sys::JSON::parse(&json).map_err(|_| JsError::new("JSON.parse failed"))
}

/// The wear rule (`sim_core::catalog::can_wear`), the one the
/// server enforces. `body` is the body type's `wear` in `creator.json`
/// (`"female"` / `"male"`), `item` the garment's `wear` (absent: anyone).
/// An unknown class never wears a restricted garment.
#[wasm_bindgen(js_name = canWear)]
pub fn can_wear(
    #[wasm_bindgen(unchecked_param_type = "Wear")] body: &str,
    #[wasm_bindgen(unchecked_optional_param_type = "Wear")] item: Option<String>,
) -> bool {
    use sim_core::catalog::Wear;
    match (Wear::from_id(body), item.as_deref().map(Wear::from_id)) {
        (_, None) => true,
        (Some(b), Some(Some(i))) => sim_core::catalog::can_wear(b, Some(i)),
        _ => false,
    }
}

/// The movement constants the server validates with; with `speedPct` (from
/// `ownEffects.speedPct`, e.g. 115 under the Pick-Me-Up Tonic; protocol v11: down to 0
/// while bound by Rigora, which stops walking), scaled exactly as the
/// server scales them (`MoveParams::with_speed_pct`).
#[wasm_bindgen(js_name = moveParams, unchecked_return_type = "MoveParams")]
pub fn move_params(#[wasm_bindgen(js_name = speedPct)] speed_pct: Option<u16>) -> JsValue {
    let p = MoveParams::default().with_speed_pct(speed_pct.unwrap_or(SPEED_PCT_NORMAL));
    let o = Object::new();
    set(&o, "walkSpeed", short_f64(p.walk_speed));
    set(&o, "runSpeed", short_f64(p.run_speed));
    set(&o, "stepHeight", short_f64(p.step_height));
    set(&o, "radius", short_f64(p.radius));
    set(&o, "speedTolerance", short_f64(p.speed_tolerance));
    o.into()
}

#[wasm_bindgen(js_name = protocolConstants, unchecked_return_type = "ProtocolConstants")]
pub fn protocol_constants() -> JsValue {
    let o = Object::new();
    set(&o, "protocolVersion", PROTOCOL_VERSION);
    set(&o, "clientMoveHz", CLIENT_MOVE_HZ);
    set(&o, "viewLimitDefault", VIEW_LIMIT_DEFAULT);
    set(&o, "viewLimitMax", VIEW_LIMIT_MAX);
    set(&o, "chatMaxLen", CHAT_MAX_LEN as u32);
    set(&o, "serverEntityId", SERVER_ENTITY_ID);
    set(&o, "maxUpdateGapSnapshots", MAX_UPDATE_GAP_SNAPSHOTS);
    set(&o, "emoteWaveMs", EMOTE_WAVE_MS);
    set(&o, "emoteBowMs", EMOTE_BOW_MS);
    set(&o, "dormItemCap", DORM_ITEM_CAP);
    set(&o, "dormInviteTtlSecs", DORM_INVITE_TTL_SECS);
    set(&o, "savedOutfitsMax", SAVED_OUTFITS_MAX);
    set(&o, "friendsMax", FRIENDS_MAX);
    set(&o, "tradeRequestSecs", TRADE_REQUEST_SECS);
    set(&o, "tradeItemsMax", TRADE_ITEMS_MAX as u32);
    set(&o, "stationGrantSecs", STATION_GRANT_SECS);
    set(&o, "mimicDraughtRequestSecs", MIMIC_DRAUGHT_REQUEST_SECS);
    set(&o, "speedPctNormal", SPEED_PCT_NORMAL);
    set(&o, "speedPctMin", protocol::SPEED_PCT_MIN);
    set(&o, "speedPctMax", SPEED_PCT_MAX);
    set(&o, "castAnimMs", protocol::CAST_ANIM_MS);
    set(&o, "levelMax", protocol::LEVEL_MAX);
    set(&o, "loadoutMax", protocol::LOADOUT_MAX as u32);
    set(&o, "tracePointsMax", protocol::TRACE_POINTS_MAX as u32);
    set(&o, "lessonBeatsMax", protocol::LESSON_BEATS_MAX as u32);
    set(&o, "duelChallengeSecs", protocol::DUEL_CHALLENGE_SECS);
    set(&o, "pickOptionsMax", protocol::PICK_OPTIONS_MAX as u32);
    set(&o, "broomPctNormal", protocol::BROOM_PCT_NORMAL);
    set(&o, "broomPctMax", protocol::BROOM_PCT_MAX);
    set(&o, "lookRangeM", short_f64(protocol::LOOK_RANGE_M));
    set(&o, "lookSendsPerSec", protocol::LOOK_SENDS_PER_SEC);
    set(&o, "partyMax", protocol::PARTY_MAX);
    set(&o, "partyInviteTtlSecs", protocol::PARTY_INVITE_TTL_SECS);
    set(&o, "clubInviteTtlSecs", protocol::CLUB_INVITE_TTL_SECS);
    o.into()
}

fn set(o: &Object, key: &str, value: impl Into<JsValue>) {
    // Defining a property on a fresh plain object cannot fail.
    let _ = Reflect::set(o, &key.into(), &value.into());
}

/// The f64 with the same shortest decimal form as `v`, so `0.35f32` reads as
/// `0.35` in JS rather than `0.3499999940395355`.
fn short_f64(v: f32) -> f64 {
    v.to_string().parse().unwrap_or(f64::from(v))
}

// ------------------------------------------------------------------ encoding

fn encode(msg: ClientMsg) -> Vec<u8> {
    protocol::encode_client(&msg)
}

fn js_err(e: String) -> JsError {
    JsError::new(&e)
}

/// `ClientMsg::Hello` with this build's protocol version, for the
/// character the client was started for (`characterId`; the server kicks
/// with `replaced` when the session now plays another).
#[wasm_bindgen(js_name = encodeHello)]
pub fn encode_hello(
    token: &str,
    #[wasm_bindgen(js_name = characterId)] character_id: f64,
) -> Vec<u8> {
    encode(ClientMsg::Hello {
        protocol_version: PROTOCOL_VERSION,
        token: token.to_string(),
        character_id: Some(character_id as i64),
    })
}

/// `ClientMsg::Mount` (protocol v8): mount a broom where the character
/// stands (`on`) or land and dismount; answered by `mounted`.
#[wasm_bindgen(js_name = encodeMount)]
pub fn encode_mount(on: bool) -> Vec<u8> {
    encode(ClientMsg::Mount { on })
}

/// `ClientMsg::EnterWishingRoom` (protocol v9): enter the Wishing Room
/// through the door the server opened for this client, choosing `room`.
#[wasm_bindgen(js_name = encodeEnterWishingRoom)]
pub fn encode_enter_wishing_room(
    #[wasm_bindgen(unchecked_param_type = "WishingRoomKind")] room: &str,
) -> Result<Vec<u8>, JsError> {
    let room = protocol::WishingRoomKind::from_id(room)
        .ok_or_else(|| JsError::new("unknown Wishing Room choice"))?;
    Ok(encode(ClientMsg::EnterWishingRoom { room }))
}

/// `ClientMsg::NpcInteract` (protocol v12): talk to NPC entity `id` (a
/// ghost, staff, a student) within `NpcRulesView.talk_radius_m`; answered by
/// `npcReply`. Shopkeepers are talked to with `encodeInteract(station)`.
#[wasm_bindgen(js_name = encodeNpcInteract)]
pub fn encode_npc_interact(id: u32) -> Vec<u8> {
    encode(ClientMsg::NpcInteract { id })
}

/// `ClientMsg::LookAt` (protocol v10): the character our own looks at now
/// (an entity id), or nobody (`null`/`undefined`). Send only on a change,
/// at most `protocolConstants().lookSendsPerSec` a second.
#[wasm_bindgen(js_name = encodeLookAt)]
pub fn encode_look_at(target: Option<u32>) -> Vec<u8> {
    encode(ClientMsg::LookAt { target })
}

/// `ClientMsg::Move`: the predicted position in metres and yaw in radians.
/// `anim` is `"idle"`, `"walk"` or `"run"`, and while mounted (protocol v8)
/// `"fly"` or `"hover"` (emotes go through `encodeEmote`).
#[wasm_bindgen(js_name = encodeMove)]
pub fn encode_move(
    seq: u32,
    x: f32,
    y: f32,
    z: f32,
    yaw: f32,
    #[wasm_bindgen(unchecked_param_type = "Anim")] anim: &str,
) -> Result<Vec<u8>, JsError> {
    let anim = parse_anim(anim).map_err(js_err)?;
    Ok(encode(ClientMsg::Move {
        seq,
        pos: [x, y, z],
        yaw,
        anim,
    }))
}

/// `ClientMsg::Chat`. `to` is the recipient's character name, required for
/// (and only used by) `"whisper"`. `clientSeq` identifies the line in a
/// `chatRejected` reply; use an increasing counter. Throws if `text` exceeds
/// `chatMaxLen` bytes.
#[wasm_bindgen(js_name = encodeChat)]
pub fn encode_chat(
    #[wasm_bindgen(unchecked_param_type = "ChatChannelName")] channel: &str,
    to: Option<String>,
    text: &str,
    #[wasm_bindgen(js_name = clientSeq)] client_seq: u32,
) -> Result<Vec<u8>, JsError> {
    chat_msg(channel, to, text, client_seq)
        .map(encode)
        .map_err(js_err)
}

/// `ClientMsg::SetViewLimit`: how many characters to draw (20, 50 or 100).
#[wasm_bindgen(js_name = encodeSetViewLimit)]
pub fn encode_set_view_limit(n: u8) -> Vec<u8> {
    encode(ClientMsg::SetViewLimit { n })
}

/// `ClientMsg::Ping`; the `Pong` echoes `clientTimeMs`.
#[wasm_bindgen(js_name = encodePing)]
pub fn encode_ping(#[wasm_bindgen(js_name = clientTimeMs)] client_time_ms: u32) -> Vec<u8> {
    encode(ClientMsg::Ping { client_time_ms })
}

/// `ClientMsg::Emote`.
#[wasm_bindgen(js_name = encodeEmote)]
pub fn encode_emote(
    #[wasm_bindgen(unchecked_param_type = "Emote")] emote: &str,
) -> Result<Vec<u8>, JsError> {
    let emote = match emote {
        "wave" => Emote::Wave,
        "bow" => Emote::Bow,
        "sit" => Emote::Sit,
        other => return Err(JsError::new(&format!("unknown emote {other:?}"))),
    };
    Ok(encode(ClientMsg::Emote { emote }))
}

/// `ClientMsg::Interact` with an interactable id of the current layout.
#[wasm_bindgen(js_name = encodeInteract)]
pub fn encode_interact(target: &str) -> Vec<u8> {
    encode(ClientMsg::Interact {
        target: target.to_string(),
    })
}

/// `ClientMsg::LeaveInstance`.
#[wasm_bindgen(js_name = encodeLeaveInstance)]
pub fn encode_leave_instance() -> Vec<u8> {
    encode(ClientMsg::LeaveInstance)
}

/// An `items.id` from JS: a positive integer below 2^53.
fn item_id(v: f64) -> Result<i64, JsError> {
    const MAX_SAFE: f64 = 9_007_199_254_740_991.0;
    if v.fract() == 0.0 && v > 0.0 && v <= MAX_SAFE {
        Ok(v as i64)
    } else {
        Err(JsError::new("item ids are positive integers"))
    }
}

/// `ClientMsg::DormPlace`: item `itemId` at `(x, y, z)` metres, turned by
/// `yaw` radians (both quantized as on the wire: centimetres, 1/256 turn).
#[wasm_bindgen(js_name = encodeDormPlace)]
pub fn encode_dorm_place(
    #[wasm_bindgen(js_name = itemId)] id: f64,
    x: f32,
    y: f32,
    z: f32,
    yaw: f32,
) -> Result<Vec<u8>, JsError> {
    Ok(encode(ClientMsg::DormPlace {
        item_id: item_id(id)?,
        pos: QPos::from_m([x, y, z]),
        yaw: yaw_to_u8(yaw),
    }))
}

/// `ClientMsg::DormMove`, quantized like `encodeDormPlace`.
#[wasm_bindgen(js_name = encodeDormMove)]
pub fn encode_dorm_move(
    #[wasm_bindgen(js_name = itemId)] id: f64,
    x: f32,
    y: f32,
    z: f32,
    yaw: f32,
) -> Result<Vec<u8>, JsError> {
    Ok(encode(ClientMsg::DormMove {
        item_id: item_id(id)?,
        pos: QPos::from_m([x, y, z]),
        yaw: yaw_to_u8(yaw),
    }))
}

/// `ClientMsg::DormRemove`.
#[wasm_bindgen(js_name = encodeDormRemove)]
pub fn encode_dorm_remove(#[wasm_bindgen(js_name = itemId)] id: f64) -> Result<Vec<u8>, JsError> {
    Ok(encode(ClientMsg::DormRemove {
        item_id: item_id(id)?,
    }))
}

/// `ClientMsg::DormSetOpen`.
#[wasm_bindgen(js_name = encodeDormSetOpen)]
pub fn encode_dorm_set_open(open: bool) -> Vec<u8> {
    encode(ClientMsg::DormSetOpen { open })
}

/// `ClientMsg::DormInvite` for a friend, by character name.
#[wasm_bindgen(js_name = encodeDormInvite)]
pub fn encode_dorm_invite(#[wasm_bindgen(js_name = characterName)] name: &str) -> Vec<u8> {
    encode(ClientMsg::DormInvite {
        character_name: name.to_string(),
    })
}

/// `ClientMsg::DormAcceptInvite` (`dormInviteReceived.inviteId`).
#[wasm_bindgen(js_name = encodeDormAcceptInvite)]
pub fn encode_dorm_accept_invite(#[wasm_bindgen(js_name = inviteId)] invite_id: u32) -> Vec<u8> {
    encode(ClientMsg::DormAcceptInvite { invite_id })
}

/// `ClientMsg::DormVisit`: walk into a friend's room that is open to friends.
#[wasm_bindgen(js_name = encodeDormVisit)]
pub fn encode_dorm_visit(#[wasm_bindgen(js_name = ownerName)] owner_name: &str) -> Vec<u8> {
    encode(ClientMsg::DormVisit {
        owner_name: owner_name.to_string(),
    })
}

/// `ClientMsg::DormKick`: send a guest (entity id) out of your room.
#[wasm_bindgen(js_name = encodeDormKick)]
pub fn encode_dorm_kick(entity: u32) -> Vec<u8> {
    encode(ClientMsg::DormKick { entity })
}

/// `ClientMsg::DormDeclineInvite` (`dormInviteReceived.inviteId`): the
/// inviter is told.
#[wasm_bindgen(js_name = encodeDormDeclineInvite)]
pub fn encode_dorm_decline_invite(#[wasm_bindgen(js_name = inviteId)] invite_id: u32) -> Vec<u8> {
    encode(ClientMsg::DormDeclineInvite { invite_id })
}

// ---- Protocol v14: parties.

/// A character id from JS (a positive integer below 2^53).
fn character_id_of(v: f64) -> Result<i64, JsError> {
    item_id(v).map_err(|_| JsError::new("character ids are positive integers"))
}

/// `ClientMsg::PartyInvite`: invite an online character (by name, any
/// house) into your party, or into a new one you lead. Answered by
/// `partyNotice` (`inviteSent`) or `partyError`.
#[wasm_bindgen(js_name = encodePartyInvite)]
pub fn encode_party_invite(name: &str) -> Vec<u8> {
    encode(ClientMsg::PartyInvite {
        name: name.to_string(),
    })
}

/// `ClientMsg::PartyRespond` to `partyInvited.inviteId`.
#[wasm_bindgen(js_name = encodePartyRespond)]
pub fn encode_party_respond(
    #[wasm_bindgen(js_name = inviteId)] invite_id: u32,
    accept: bool,
) -> Vec<u8> {
    encode(ClientMsg::PartyRespond { invite_id, accept })
}

/// `ClientMsg::PartyLeave`.
#[wasm_bindgen(js_name = encodePartyLeave)]
pub fn encode_party_leave() -> Vec<u8> {
    encode(ClientMsg::PartyLeave)
}

/// `ClientMsg::PartyKick` (leader only): remove member `characterId`.
#[wasm_bindgen(js_name = encodePartyKick)]
pub fn encode_party_kick(
    #[wasm_bindgen(js_name = characterId)] character_id: f64,
) -> Result<Vec<u8>, JsError> {
    Ok(encode(ClientMsg::PartyKick {
        character_id: character_id_of(character_id)?,
    }))
}

/// `ClientMsg::PartyPromote` (leader only): member `characterId` leads.
#[wasm_bindgen(js_name = encodePartyPromote)]
pub fn encode_party_promote(
    #[wasm_bindgen(js_name = characterId)] character_id: f64,
) -> Result<Vec<u8>, JsError> {
    Ok(encode(ClientMsg::PartyPromote {
        character_id: character_id_of(character_id)?,
    }))
}

// ---- Protocol v15: clubs.

/// `ClientMsg::ClubFound` at the clubs secretary's desk (`secretary`: her
/// entity id). Answered by `clubNotice` (`founded`) and `clubState`, or
/// `clubError`.
#[wasm_bindgen(js_name = encodeClubFound)]
pub fn encode_club_found(secretary: u32, name: &str, tag: &str) -> Vec<u8> {
    encode(ClientMsg::ClubFound {
        secretary,
        name: name.to_string(),
        tag: tag.to_string(),
    })
}

/// `ClientMsg::ClubInvite` (officers and the owner): invite an online
/// character by name.
#[wasm_bindgen(js_name = encodeClubInvite)]
pub fn encode_club_invite(name: &str) -> Vec<u8> {
    encode(ClientMsg::ClubInvite {
        name: name.to_string(),
    })
}

/// `ClientMsg::ClubRespond` to `clubInvited.inviteId`.
#[wasm_bindgen(js_name = encodeClubRespond)]
pub fn encode_club_respond(
    #[wasm_bindgen(js_name = inviteId)] invite_id: u32,
    accept: bool,
) -> Vec<u8> {
    encode(ClientMsg::ClubRespond { invite_id, accept })
}

/// `ClientMsg::ClubLeave`.
#[wasm_bindgen(js_name = encodeClubLeave)]
pub fn encode_club_leave() -> Vec<u8> {
    encode(ClientMsg::ClubLeave)
}

/// `ClientMsg::ClubKick`: officers remove members, the owner anyone else.
#[wasm_bindgen(js_name = encodeClubKick)]
pub fn encode_club_kick(
    #[wasm_bindgen(js_name = characterId)] character_id: f64,
) -> Result<Vec<u8>, JsError> {
    Ok(encode(ClientMsg::ClubKick {
        character_id: character_id_of(character_id)?,
    }))
}

/// `ClientMsg::ClubSetRole` (the owner): `"officer"` appoints, `"member"`
/// dismisses.
#[wasm_bindgen(js_name = encodeClubSetRole)]
pub fn encode_club_set_role(
    #[wasm_bindgen(js_name = characterId)] character_id: f64,
    #[wasm_bindgen(unchecked_param_type = "ClubRole")] role: &str,
) -> Result<Vec<u8>, JsError> {
    let role = protocol::ClubRole::from_id(role)
        .ok_or_else(|| JsError::new("a club role is member, officer or owner"))?;
    Ok(encode(ClientMsg::ClubSetRole {
        character_id: character_id_of(character_id)?,
        role,
    }))
}

/// `ClientMsg::ClubTransfer` (the owner): hand the club to a member.
#[wasm_bindgen(js_name = encodeClubTransfer)]
pub fn encode_club_transfer(
    #[wasm_bindgen(js_name = characterId)] character_id: f64,
) -> Result<Vec<u8>, JsError> {
    Ok(encode(ClientMsg::ClubTransfer {
        character_id: character_id_of(character_id)?,
    }))
}

/// `ClientMsg::ClubRename` (the owner).
#[wasm_bindgen(js_name = encodeClubRename)]
pub fn encode_club_rename(name: &str, tag: &str) -> Vec<u8> {
    encode(ClientMsg::ClubRename {
        name: name.to_string(),
        tag: tag.to_string(),
    })
}

/// `ClientMsg::ClubSetMotd` (officers and the owner; empty clears it).
#[wasm_bindgen(js_name = encodeClubSetMotd)]
pub fn encode_club_set_motd(motd: &str) -> Vec<u8> {
    encode(ClientMsg::ClubSetMotd {
        motd: motd.to_string(),
    })
}

/// `ClientMsg::ClubDisband` (the owner).
#[wasm_bindgen(js_name = encodeClubDisband)]
pub fn encode_club_disband() -> Vec<u8> {
    encode(ClientMsg::ClubDisband)
}

// ---- Protocol v4.

/// `ClientMsg::UseFurniture`: a station (cauldron) placed in the dorm the
/// client is in.
#[wasm_bindgen(js_name = encodeUseFurniture)]
pub fn encode_use_furniture(#[wasm_bindgen(js_name = itemId)] id: f64) -> Result<Vec<u8>, JsError> {
    Ok(encode(ClientMsg::UseFurniture {
        item_id: item_id(id)?,
    }))
}

/// `ClientMsg::TradeRequest` to a visible player (entity id).
#[wasm_bindgen(js_name = encodeTradeRequest)]
pub fn encode_trade_request(target: u32) -> Vec<u8> {
    encode(ClientMsg::TradeRequest { target })
}

/// `ClientMsg::TradeRespond` to `tradeRequested`.
#[wasm_bindgen(js_name = encodeTradeRespond)]
pub fn encode_trade_respond(
    #[wasm_bindgen(js_name = tradeId)] trade_id: u32,
    accept: bool,
) -> Vec<u8> {
    encode(ClientMsg::TradeRespond { trade_id, accept })
}

/// `ClientMsg::TradeSetOffer`: the client's whole offer. `itemIds[k]` with
/// `quantities[k]` units each (the arrays are parallel), plus `crowns`
/// (a whole number, at least 0). Throws on mismatched arrays or bad numbers.
#[wasm_bindgen(js_name = encodeTradeSetOffer)]
pub fn encode_trade_set_offer(
    #[wasm_bindgen(js_name = tradeId)] trade_id: u32,
    #[wasm_bindgen(js_name = itemIds)] item_ids: &[f64],
    quantities: &[u32],
    crowns: f64,
) -> Result<Vec<u8>, JsError> {
    let msg = trade_offer(trade_id, item_ids, quantities, crowns).map_err(js_err)?;
    Ok(encode(msg))
}

fn trade_offer(
    trade_id: u32,
    item_ids: &[f64],
    quantities: &[u32],
    crowns: f64,
) -> Result<ClientMsg, String> {
    const MAX_SAFE: f64 = 9_007_199_254_740_991.0;
    if item_ids.len() != quantities.len() {
        return Err("itemIds and quantities must be the same length".into());
    }
    if !(crowns.fract() == 0.0 && (0.0..=MAX_SAFE).contains(&crowns)) {
        return Err("crowns must be a whole number of at least 0".into());
    }
    let items = item_ids
        .iter()
        .zip(quantities)
        .map(|(&id, &quantity)| {
            if id.fract() == 0.0 && id > 0.0 && id <= MAX_SAFE {
                Ok(OfferItem {
                    item_id: id as i64,
                    quantity,
                })
            } else {
                Err("item ids are positive integers".to_string())
            }
        })
        .collect::<Result<Vec<_>, _>>()?;
    Ok(ClientMsg::TradeSetOffer {
        trade_id,
        items,
        crowns: crowns as i64,
    })
}

/// `ClientMsg::TradeLock`: lock (or unlock) the client's offer.
#[wasm_bindgen(js_name = encodeTradeLock)]
pub fn encode_trade_lock(
    #[wasm_bindgen(js_name = tradeId)] trade_id: u32,
    locked: bool,
) -> Vec<u8> {
    encode(ClientMsg::TradeLock { trade_id, locked })
}

/// `ClientMsg::TradeConfirm` of revision `rev` (`tradeState.view.rev`).
#[wasm_bindgen(js_name = encodeTradeConfirm)]
pub fn encode_trade_confirm(#[wasm_bindgen(js_name = tradeId)] trade_id: u32, rev: u32) -> Vec<u8> {
    encode(ClientMsg::TradeConfirm { trade_id, rev })
}

/// `ClientMsg::TradeCancel`.
#[wasm_bindgen(js_name = encodeTradeCancel)]
pub fn encode_trade_cancel(#[wasm_bindgen(js_name = tradeId)] trade_id: u32) -> Vec<u8> {
    encode(ClientMsg::TradeCancel { trade_id })
}

/// `ClientMsg::Drink` a potion from the inventory.
#[wasm_bindgen(js_name = encodeDrink)]
pub fn encode_drink(#[wasm_bindgen(js_name = itemId)] id: f64) -> Result<Vec<u8>, JsError> {
    Ok(encode(ClientMsg::Drink {
        item_id: item_id(id)?,
    }))
}

/// `ClientMsg::MimicDraughtAsk`: ask friend `target` (entity id) to be copied.
#[wasm_bindgen(js_name = encodeMimicDraughtAsk)]
pub fn encode_mimic_draught_ask(
    #[wasm_bindgen(js_name = itemId)] id: f64,
    target: u32,
) -> Result<Vec<u8>, JsError> {
    Ok(encode(ClientMsg::MimicDraughtAsk {
        item_id: item_id(id)?,
        target,
    }))
}

/// `ClientMsg::MimicDraughtAnswer` to `mimicDraughtRequest`.
#[wasm_bindgen(js_name = encodeMimicDraughtAnswer)]
pub fn encode_mimic_draught_answer(
    #[wasm_bindgen(js_name = requestId)] request_id: u32,
    accept: bool,
) -> Vec<u8> {
    encode(ClientMsg::MimicDraughtAnswer { request_id, accept })
}

// ---- Protocol v5.

/// Trace samples from JS: flat `[dtMs, x, y, dtMs, x, y, ...]` (dt 0-255,
/// x and y -100..=100 on the square pad, y up).
fn trace_points(flat: &[i32]) -> Result<Vec<TracePoint>, String> {
    if !flat.len().is_multiple_of(3) {
        return Err("points are [dtMs, x, y] triples".into());
    }
    if flat.len() / 3 > protocol::TRACE_POINTS_MAX {
        return Err(format!("at most {} points", protocol::TRACE_POINTS_MAX));
    }
    flat.chunks(3)
        .map(|c| {
            let (dt, x, y) = (c[0], c[1], c[2]);
            if !(0..=255).contains(&dt) || !(-128..=127).contains(&x) || !(-128..=127).contains(&y)
            {
                return Err("dtMs 0-255, x and y -128..127".to_string());
            }
            Ok(TracePoint {
                dt_ms: dt as u8,
                x: x as i8,
                y: y as i8,
            })
        })
        .collect()
}

/// `ClientMsg::TraceGesture` for beat `beat` of round `round`: the trace
/// started `startOffsetMs` after the beat's `atUnixMs` (negative: early, on
/// the corrected clock); `points` as for [`trace_points`].
#[wasm_bindgen(js_name = encodeTraceGesture)]
pub fn encode_trace_gesture(
    round: u8,
    beat: u8,
    #[wasm_bindgen(js_name = startOffsetMs)] start_offset_ms: i16,
    points: &[i32],
) -> Result<Vec<u8>, JsError> {
    let points = trace_points(points).map_err(js_err)?;
    Ok(encode(ClientMsg::TraceGesture {
        round,
        beat,
        start_offset_ms,
        points,
    }))
}

/// `ClientMsg::PickAnswer` (protocol v7): the pick for beat `beat` of round
/// `round`, `choice` the position in its `options`, made `offsetMs` from the
/// beat's `atUnixMs` (negative: early, on the corrected clock).
#[wasm_bindgen(js_name = encodePickAnswer)]
pub fn encode_pick_answer(
    round: u8,
    beat: u8,
    choice: u8,
    #[wasm_bindgen(js_name = offsetMs)] offset_ms: i16,
) -> Vec<u8> {
    encode(ClientMsg::PickAnswer {
        round,
        beat,
        choice,
        offset_ms,
    })
}

fn spell_target(kind: &str, id: Option<f64>) -> Result<SpellTarget, String> {
    match (kind, id) {
        ("none", _) => Ok(SpellTarget::None),
        ("furniture", Some(v)) if v.fract() == 0.0 && v > 0.0 && v <= 9_007_199_254_740_991.0 => {
            Ok(SpellTarget::Furniture { item_id: v as i64 })
        }
        ("entity", Some(v)) if v.fract() == 0.0 && (0.0..=f64::from(u32::MAX)).contains(&v) => {
            Ok(SpellTarget::Entity { id: v as u32 })
        }
        (k, _) => Err(format!("spell target {k:?} needs a valid id")),
    }
}

/// `ClientMsg::CastSpell`: `targetKind` is `"none"`, `"furniture"` (with
/// `targetId` an `items.id`) or `"entity"` (with an entity id).
#[wasm_bindgen(js_name = encodeCastSpell)]
pub fn encode_cast_spell(
    spell: u16,
    #[wasm_bindgen(js_name = targetKind, unchecked_param_type = "\"none\" | \"furniture\" | \"entity\"")]
    target_kind: &str,
    #[wasm_bindgen(js_name = targetId)] target_id: Option<f64>,
) -> Result<Vec<u8>, JsError> {
    let target = spell_target(target_kind, target_id).map_err(js_err)?;
    Ok(encode(ClientMsg::CastSpell { spell, target }))
}

/// `ClientMsg::EndSpell` (Lucerna off).
#[wasm_bindgen(js_name = encodeEndSpell)]
pub fn encode_end_spell(spell: u16) -> Vec<u8> {
    encode(ClientMsg::EndSpell { spell })
}

/// `ClientMsg::DuelChallenge` to a visible player (entity id).
#[wasm_bindgen(js_name = encodeDuelChallenge)]
pub fn encode_duel_challenge(target: u32) -> Vec<u8> {
    encode(ClientMsg::DuelChallenge { target })
}

/// `ClientMsg::DuelChallengeName` (`/duel name`).
#[wasm_bindgen(js_name = encodeDuelChallengeName)]
pub fn encode_duel_challenge_name(name: &str) -> Vec<u8> {
    encode(ClientMsg::DuelChallengeName {
        name: name.to_string(),
    })
}

/// `ClientMsg::DuelRespond` to `duelChallenged`.
#[wasm_bindgen(js_name = encodeDuelRespond)]
pub fn encode_duel_respond(
    #[wasm_bindgen(js_name = challengeId)] challenge_id: u32,
    accept: bool,
) -> Vec<u8> {
    encode(ClientMsg::DuelRespond {
        challenge_id,
        accept,
    })
}

/// `ClientMsg::DuelCancel` of the client's own challenge.
#[wasm_bindgen(js_name = encodeDuelCancel)]
pub fn encode_duel_cancel(#[wasm_bindgen(js_name = challengeId)] challenge_id: u32) -> Vec<u8> {
    encode(ClientMsg::DuelCancel { challenge_id })
}

/// `ClientMsg::DuelCast`: a spell of the loadout at the opponent.
#[wasm_bindgen(js_name = encodeDuelCast)]
pub fn encode_duel_cast(spell: u16) -> Vec<u8> {
    encode(ClientMsg::DuelCast { spell })
}

/// `ClientMsg::DuelForfeit`.
#[wasm_bindgen(js_name = encodeDuelForfeit)]
pub fn encode_duel_forfeit() -> Vec<u8> {
    encode(ClientMsg::DuelForfeit)
}

/// `ClientMsg::DuelListRequest`: the duels the client may watch (`duelList`).
#[wasm_bindgen(js_name = encodeDuelListRequest)]
pub fn encode_duel_list_request() -> Vec<u8> {
    encode(ClientMsg::DuelListRequest)
}

/// `ClientMsg::DuelSpectate`.
#[wasm_bindgen(js_name = encodeDuelSpectate)]
pub fn encode_duel_spectate(#[wasm_bindgen(js_name = duelId)] duel_id: u32) -> Vec<u8> {
    encode(ClientMsg::DuelSpectate { duel_id })
}

/// The trace scorer the server scores class gestures with
/// (`sim_core::gesture`), for the client's instant preview: the same input
/// gives the same `GestureResult`.
#[wasm_bindgen]
pub struct GestureScorer {
    gestures: Gestures,
}

#[wasm_bindgen]
impl GestureScorer {
    /// From `data/classes/gestures.json` (as imported).
    #[wasm_bindgen(constructor)]
    pub fn new(
        #[wasm_bindgen(js_name = gesturesJson)] gestures_json: &str,
    ) -> Result<GestureScorer, JsError> {
        let gestures =
            Gestures::from_json(gestures_json).map_err(|e| JsError::new(&e.to_string()))?;
        Ok(GestureScorer { gestures })
    }

    /// Scores a trace of gesture `gesture` (id) that started `offsetMs`
    /// from its beat, in a round with window `windowMs`; `points` are flat
    /// `[dtMs, x, y, ...]` triples. Throws for an unknown gesture.
    #[wasm_bindgen(unchecked_return_type = "GestureResult")]
    pub fn score(
        &self,
        gesture: u8,
        #[wasm_bindgen(js_name = windowMs)] window_ms: u16,
        #[wasm_bindgen(js_name = offsetMs)] offset_ms: i32,
        points: &[i32],
    ) -> Result<JsValue, JsError> {
        let g = self
            .gestures
            .get(gesture)
            .ok_or_else(|| JsError::new("unknown gesture"))?;
        let result = match trace_points(points) {
            Ok(points) => self.gestures.score(g, window_ms, offset_ms, &points),
            Err(_) => sim_core::gesture::rejected(protocol::TraceReject::BadSampleCount),
        };
        shape_to_js(&result)
    }
}

/// The pick scorer's timing (`sim_core::pick`) for the client's instant
/// preview: what a pick scores if it is right. The client never
/// knows the answer; the server's `beatScored` says whether it was.
#[wasm_bindgen]
pub struct PickScorer {
    gestures: Gestures,
}

#[wasm_bindgen]
impl PickScorer {
    /// From `data/classes/gestures.json` (picks use its scoring numbers).
    #[wasm_bindgen(constructor)]
    pub fn new(
        #[wasm_bindgen(js_name = gesturesJson)] gestures_json: &str,
    ) -> Result<PickScorer, JsError> {
        let gestures =
            Gestures::from_json(gestures_json).map_err(|e| JsError::new(&e.to_string()))?;
        Ok(PickScorer { gestures })
    }

    /// The result of a right pick made `offsetMs` from its beat in a round
    /// with window `windowMs` (a pick outside the window is `bad_timing`).
    #[wasm_bindgen(unchecked_return_type = "GestureResult")]
    pub fn preview(
        &self,
        #[wasm_bindgen(js_name = windowMs)] window_ms: u16,
        #[wasm_bindgen(js_name = offsetMs)] offset_ms: i32,
    ) -> Result<JsValue, JsError> {
        let result = if offset_ms.unsigned_abs() > u32::from(window_ms) {
            sim_core::gesture::rejected(protocol::TraceReject::BadTiming)
        } else {
            sim_core::pick::preview(&self.gestures, window_ms, offset_ms)
        };
        shape_to_js(&result)
    }

    /// Whether a pick made `offsetMs` from its beat still counts.
    #[wasm_bindgen(js_name = inWindow)]
    pub fn in_window(
        &self,
        #[wasm_bindgen(js_name = windowMs)] window_ms: u16,
        #[wasm_bindgen(js_name = offsetMs)] offset_ms: i32,
    ) -> bool {
        offset_ms.unsigned_abs() <= u32::from(window_ms)
    }
}

// ---- The wayfinding bindings (a `Wayfinder`
// over `sim_core::nav`: build it for the player's house from the castle
// layout, `path(from, to)` and the key places of
// `data/world/key_places.json`).

#[wasm_bindgen(typescript_custom_section)]
const TS_STAIRS: &'static str = r#"
/** A landing gate (`data/world/stairs.json` `flights[].gates[]`). */
export interface StairGateDef {
  id: string;
  /** The flight position whose arrival opens it. */
  position: "a" | "b";
  /** The surface whose edge it stands on. */
  landing: string;
  /** Its two hinges [x, z]: a leaf from each, meeting in the middle. */
  line: [[number, number], [number, number]];
  y: number;
  /** The unit direction [dx, dz] the leaves open to (onto the landing). */
  opens: [number, number];
}
/** A moving flight (`stairs.json` `flights[]`), in bit order. */
export interface StairFlightDef {
  id: string;
  name: string;
  /** The surface ids of positions A and B. */
  a: string;
  b: string;
  /** The vertical axis it turns about, [x, z]. */
  pivot: [number, number];
  /** A to B, degrees about +y in the layout's right-handed frame. */
  angle_deg: number;
  phase: number;
  gates: StairGateDef[];
}
/** A moving flight at an instant (`SimWorld.flightStates`). */
export interface FlightView {
  id: string;
  /** The position walked now (it changes at the switch, the end of a swing). */
  walk: "a" | "b";
  phase: "rest" | "swing";
  /** Resting with the swing less than the warning away (shudder, dust, the chat line). */
  warning: boolean;
  /** 0..1 through a swing (0 at rest). */
  progress: number;
  /** The turn of the flight's A model about its pivot (degrees: 0 at A, angle_deg at B). */
  turnDeg: number;
  lastSwitchMs: number;
  nextSwitchMs: number;
  /** How far each gate is open, 0..1. */
  gates: { id: string; open: number }[];
}
"#;

#[wasm_bindgen(typescript_custom_section)]
const TS_NAV: &'static str = r#"
/** `sim_core::key_places::PlaceGroup`: what a "Find my way" destination is. */
export type PlaceGroup =
  | "dorm" | "common_room" | "owl_order" | "owl_exchange" | "great_hall" | "entrance_hall" | "dueling_club" | "class"
  | "grounds" | "castle";
/** A key place offered to the wayfinder's house, with where its trail ends. */
export interface KeyPlaceView {
  id: string;
  group: PlaceGroup;
  /** Display name ("Potions Classroom"). */
  name: string;
  /** The scheduled class whose door it is (group "class"). */
  class?: string;
  pos: Vec3;
}
"#;

/// Solid furniture: a key place standing in a piece is reached at the nearest
/// free spot this far from it at most (metres: the courtyard fountain's rim
/// is 4 m from its middle).
const PLACE_SLACK: f32 = 6.0;

/// The wayfinding trail's path search (the Monstra Viam charm): the
/// castle's walkable-surface graph as one house walks it (other houses'
/// gated rooms are walls), walking round the furniture keep-outs, and the key
/// places offered to that house. Client only: the server never sees a trail.
#[wasm_bindgen]
pub struct Wayfinder {
    nav: sim_core::nav::Nav,
    places: Vec<(sim_core::KeyPlace, [f32; 3])>,
    /// The house's layout with both positions of every moving
    /// flight, the keep-outs, the graphs of the other staircase states built
    /// so far (`nav` is the current state's), and the state planned in.
    full: sim_core::Layout,
    keep_out: Vec<sim_core::nav::KeepOut>,
    navs: Vec<Option<sim_core::nav::Nav>>,
    state: u8,
}

#[wasm_bindgen]
impl Wayfinder {
    /// From the castle layout, the player's house, `data/world/key_places.json`
    /// and the furniture footprints to walk round (a JSON array of `{ id,
    /// min: [x, z], max: [x, z], cell? }`; `[]` for none). Throws on invalid
    /// data. Places whose target is not on the house's ground are left out.
    #[wasm_bindgen(constructor)]
    pub fn new(
        #[wasm_bindgen(js_name = layoutJson)] layout_json: &str,
        #[wasm_bindgen(unchecked_param_type = "House")] house: &str,
        #[wasm_bindgen(js_name = keyPlacesJson)] key_places_json: &str,
        #[wasm_bindgen(js_name = keepOutJson)] keep_out_json: &str,
    ) -> Result<Wayfinder, JsError> {
        let house = House::from_id(house).ok_or_else(|| JsError::new("unknown house"))?;
        let full = sim_core::Layout::from_json_for_house(layout_json, house)
            .map_err(|e| JsError::new(&e.to_string()))?;
        let layout = full.for_stairs(0);
        let keep_out: Vec<sim_core::nav::KeepOut> =
            serde_json::from_str(keep_out_json).map_err(|e| JsError::new(&e.to_string()))?;
        let key_places = sim_core::KeyPlaces::from_json(key_places_json)
            .map_err(|e| JsError::new(&e.to_string()))?;
        let world =
            std::sync::Arc::new(World::new(layout).map_err(|e| JsError::new(&e.to_string()))?);
        let places = key_places
            .offered_to(house)
            .filter_map(|p| {
                let mut pos = p.resolve(world.layout())?;
                // Solid furniture: a target standing in a piece (a landmark
                // at a table's middle) is reached beside it.
                if world.ground_at(pos[0], pos[2], pos[1]).is_none() {
                    pos = sim_core::stand_near(&world, &MoveParams::default(), pos, PLACE_SLACK)?;
                }
                world.ground_at(pos[0], pos[2], pos[1])?;
                Some((p.clone(), pos))
            })
            .collect();
        let nav = sim_core::nav::Nav::new(world, &keep_out, sim_core::nav::trail_allows);
        let states = 1usize << full.flights().len();
        Ok(Wayfinder {
            nav,
            places,
            full,
            keep_out,
            navs: (0..states).map(|_| None).collect(),
            state: 0,
        })
    }

    /// Plans trails in staircase state `state` from now on
    /// (`SimWorld.stairState`); each state's graph is built the first time.
    #[wasm_bindgen(js_name = setStairState)]
    pub fn set_stair_state(&mut self, state: u8) -> Result<(), JsError> {
        let state = (usize::from(state) % self.navs.len()) as u8;
        if state == self.state {
            return Ok(());
        }
        let nav = match self.navs[usize::from(state)].take() {
            Some(nav) => nav,
            None => {
                let world = World::new(self.full.for_stairs(state))
                    .map_err(|e| JsError::new(&e.to_string()))?;
                sim_core::nav::Nav::new(
                    std::sync::Arc::new(world),
                    &self.keep_out,
                    sim_core::nav::trail_allows,
                )
            }
        };
        let old = std::mem::replace(&mut self.nav, nav);
        self.navs[usize::from(self.state)] = Some(old);
        self.state = state;
        Ok(())
    }

    /// The staircase state trails are planned in.
    #[wasm_bindgen(js_name = stairState, getter)]
    pub fn stair_state(&self) -> u8 {
        self.state
    }

    /// The moving flights the trail to `place` from `(x, y, z)`
    /// crosses in the current state, as bits (bit i: flight i in the
    /// layout's order): re-plan when one of them starts to swing. 0 for an
    /// unknown place, no way, or a trail on fixed ground only.
    #[wasm_bindgen(js_name = pathFlights)]
    pub fn path_flights(&self, place: &str, x: f32, y: f32, z: f32) -> u8 {
        let Some((_, to)) = self.places.iter().find(|(p, _)| p.id == place) else {
            return 0;
        };
        let Some(route) = self.nav.path_surfaces([x, y, z], *to) else {
            return 0;
        };
        let flights = self.full.flights();
        let surfaces = self.nav.world().surfaces();
        route.iter().fold(0u8, |bits, &s| {
            let i = surfaces[s as usize]
                .moving
                .as_ref()
                .and_then(|m| flights.iter().position(|f| *f == m.flight));
            match i {
                Some(i) => bits | 1 << i,
                None => bits,
            }
        })
    }

    /// The key places this house is offered, in file order.
    #[wasm_bindgen(unchecked_return_type = "KeyPlaceView[]")]
    pub fn places(&self) -> Result<JsValue, JsError> {
        let list: Vec<serde_json::Value> = self
            .places
            .iter()
            .map(|(p, pos)| {
                let mut v = serde_json::json!({
                    "id": p.id,
                    "group": p.group,
                    "name": p.name,
                    "pos": pos,
                });
                if let Some(c) = &p.class {
                    v["class"] = c.clone().into();
                }
                v
            })
            .collect();
        to_js_via_json(&list)
    }

    /// The trail from `(x, y, z)` to `(tx, ty, tz)` as flat `[x, y, z, ...]`
    /// waypoints on the ground (the first is the start, the last the end), or
    /// `undefined` when either is off this house's ground or no way joins them.
    #[allow(clippy::too_many_arguments)]
    pub fn path(&self, x: f32, y: f32, z: f32, tx: f32, ty: f32, tz: f32) -> Option<Vec<f32>> {
        self.nav
            .path([x, y, z], [tx, ty, tz])
            .map(|p| p.into_iter().flatten().collect())
    }

    /// The trail from `(x, y, z)` to key place `place` (an id from
    /// [`Wayfinder::places`]); `undefined` for an unknown place or no way.
    #[wasm_bindgen(js_name = pathTo)]
    pub fn path_to(&self, place: &str, x: f32, y: f32, z: f32) -> Option<Vec<f32>> {
        let (_, to) = self.places.iter().find(|(p, _)| p.id == place)?;
        self.path(x, y, z, to[0], to[1], to[2])
    }
}
// ---- end of the wayfinding bindings.

/// The game clock and the class schedule (`sim_core::clock`), from
/// `data/classes/schedule.json`. All times are the server's Unix ms (correct
/// the local clock with `welcome.unixMs`).
#[wasm_bindgen]
pub struct GameClock {
    schedule: Schedule,
}

#[wasm_bindgen]
impl GameClock {
    #[wasm_bindgen(constructor)]
    pub fn new(
        #[wasm_bindgen(js_name = scheduleJson)] schedule_json: &str,
    ) -> Result<GameClock, JsError> {
        let schedule =
            Schedule::from_json(schedule_json).map_err(|e| JsError::new(&e.to_string()))?;
        Ok(GameClock { schedule })
    }

    /// Game seconds since game midnight (0-86,399).
    #[wasm_bindgen(js_name = gameSeconds)]
    pub fn game_seconds(&self, #[wasm_bindgen(js_name = unixMs)] unix_ms: f64) -> u32 {
        self.schedule.clock.game_time(unix_ms as i64).secs
    }

    /// The game day number since the epoch.
    #[wasm_bindgen(js_name = gameDay)]
    pub fn game_day(&self, #[wasm_bindgen(js_name = unixMs)] unix_ms: f64) -> f64 {
        self.schedule.clock.game_time(unix_ms as i64).day as f64
    }

    #[wasm_bindgen(unchecked_return_type = "Period")]
    pub fn period(&self, #[wasm_bindgen(js_name = unixMs)] unix_ms: f64) -> String {
        self.schedule.clock.period(unix_ms as i64).id().to_string()
    }

    /// 0 (night) to 1 (midday), for the lighting.
    pub fn daylight(&self, #[wasm_bindgen(js_name = unixMs)] unix_ms: f64) -> f32 {
        self.schedule.clock.daylight(unix_ms as i64)
    }

    /// When the next period starts (Unix ms).
    #[wasm_bindgen(js_name = nextPeriodAt)]
    pub fn next_period_at(&self, #[wasm_bindgen(js_name = unixMs)] unix_ms: f64) -> f64 {
        self.schedule.clock.next_period(unix_ms as i64).1 as f64
    }

    /// The timetable: every slot in order through the day, its
    /// start, period and the classes that start then.
    #[wasm_bindgen(unchecked_return_type = "TimetableSlot[]")]
    pub fn timetable(&self) -> Result<JsValue, JsError> {
        let s = &self.schedule;
        let slots: Vec<serde_json::Value> = s
            .slots
            .iter()
            .enumerate()
            .map(|(k, t)| {
                serde_json::json!({
                    "slot": k,
                    "at": t.to_string(),
                    "gameSeconds": t.secs(),
                    "period": s.clock.period_at_secs(t.secs()).id(),
                    "classes": s.in_slot(k as u8).map(|c| c.id.as_str()).collect::<Vec<_>>(),
                })
            })
            .collect();
        js_sys::JSON::parse(&serde_json::Value::from(slots).to_string())
            .map_err(|_| JsError::new("JSON.parse failed"))
    }

    /// A subject's display name (`schedule.json` `subjects`), or the id.
    #[wasm_bindgen(js_name = subjectName)]
    pub fn subject_name(&self, subject: &str) -> String {
        sim_core::Subject::from_id(subject).map_or_else(
            || subject.to_owned(),
            |s| self.schedule.subject_name(s).to_owned(),
        )
    }

    /// Class `class`'s session under way, whether its door is open, and the
    /// next session. Throws for an unknown class.
    #[wasm_bindgen(js_name = classTimes, unchecked_return_type = "ClassTimes")]
    pub fn class_times(
        &self,
        class: &str,
        #[wasm_bindgen(js_name = unixMs)] unix_ms: f64,
    ) -> Result<JsValue, JsError> {
        let c = self
            .schedule
            .class(class)
            .ok_or_else(|| JsError::new("unknown class"))?;
        let now = unix_ms as i64;
        let current = self.schedule.current(c, now);
        let next = self.schedule.next(c, now);
        let times = |s: sim_core::SessionTimes| {
            serde_json::json!({
                "session": s.key.id(),
                "startMs": s.start_ms,
                "joinUntilMs": s.join_until_ms,
                "endMs": s.end_ms,
            })
        };
        let value = serde_json::json!({
            "current": current.map(times),
            "joinable": current.is_some_and(|s| s.joinable(now)),
            "next": times(next),
        });
        js_sys::JSON::parse(&value.to_string()).map_err(|_| JsError::new("JSON.parse failed"))
    }
}

/// Any protocol value as the client's shape (`shape`).
fn shape_to_js<T: serde::Serialize>(v: &T) -> Result<JsValue, JsError> {
    let json = shape::to_shape(v)
        .map_err(|e| JsError::new(&e.to_string()))?
        .to_string();
    js_sys::JSON::parse(&json).map_err(|_| JsError::new("JSON.parse failed"))
}

fn parse_anim(s: &str) -> Result<Anim, String> {
    match s {
        "idle" => Ok(Anim::Idle),
        "walk" => Ok(Anim::Walk),
        "run" => Ok(Anim::Run),
        "fly" => Ok(Anim::Fly),
        "hover" => Ok(Anim::Hover),
        other => Err(format!("unknown move anim {other:?}")),
    }
}

fn chat_msg(
    channel: &str,
    to: Option<String>,
    text: &str,
    client_seq: u32,
) -> Result<ClientMsg, String> {
    if text.len() > CHAT_MAX_LEN {
        return Err(format!("chat text is longer than {CHAT_MAX_LEN} bytes"));
    }
    let channel = match (channel, to) {
        ("say", _) => ChatChannel::Say,
        ("house", _) => ChatChannel::House,
        ("whisper", Some(to)) if !to.is_empty() => ChatChannel::Whisper { to },
        ("whisper", _) => return Err("a whisper needs a recipient".into()),
        ("party", _) => ChatChannel::Party,
        ("club", _) => ChatChannel::Club,
        (other, _) => return Err(format!("unknown chat channel {other:?}")),
    };
    Ok(ClientMsg::Chat {
        channel,
        text: text.to_string(),
        client_seq,
    })
}

// ------------------------------------------------------------------ decoding

/// Generates `Keys`, the property names and string values decoded messages use,
/// created once as JS strings so a snapshot doesn't re-encode them per entity.
macro_rules! keys {
    ($($field:ident = $name:literal),* $(,)?) => {
        struct Keys { $($field: JsValue),* }
        impl Keys {
            fn new() -> Keys {
                Keys { $($field: JsValue::from_str($name)),* }
            }
        }
    };
}

keys! {
    // Property names.
    type_ = "type", kind = "kind", protocol_version = "protocolVersion", your_id = "yourId",
    character = "character", spawn = "spawn", tick_hz = "tickHz", snapshot_hz = "snapshotHz",
    tick = "tick", entered = "entered", states = "states", left = "left", id = "id",
    name = "name", house = "house", appearance_version = "appearanceVersion",
    appearance = "appearance", zone = "zone", owner_name = "ownerName", layout = "layout",
    x = "x", y = "y", z = "z", yaw = "yaw", anim = "anim", online = "online",
    nearby = "nearby", from = "from", from_name = "fromName", channel = "channel",
    to = "to", text = "text", seq = "seq", pos = "pos", client_time_ms = "clientTimeMs",
    server_time_ms = "serverTimeMs", position = "position", reason = "reason", code = "code",
    client_seq = "clientSeq", body_type = "bodyType", height = "height",
    face = "face", skin_tone = "skinTone", hair_style = "hairStyle", hair_colour = "hairColour",
    eye_colour = "eyeColour",
    outfit = "outfit", hat = "hat", body = "body", legs = "legs", shoes = "shoes",
    item_def = "itemDef", dyes = "dyes", item_id = "itemId", item = "item", edit = "edit",
    open = "open", open_to_friends = "openToFriends", cap = "cap", items = "items",
    invite_id = "inviteId", expires_in_secs = "expiresInSecs", event = "event",
    request_id = "requestId", character_id = "characterId", location = "location",
    place = "place", room_open = "roomOpen", by_name = "byName",
    effects_version = "effectsVersion", effects = "effects", finish = "finish",
    level = "level", npc = "npc", unix_ms = "unixMs", class = "class", section = "section",
    duel_id = "duelId", venue = "venue",
    colour = "colour", part = "part", family = "family", pct = "pct",
    looks = "looks", target = "target",
    // `type` values.
    welcome = "welcome", snapshot = "snapshot", roster = "roster", chat = "chat",
    correction = "correction", pong = "pong", queued = "queued", kick = "kick",
    chat_rejected = "chatRejected", zone_changed = "zoneChanged",
    appearance_changed = "appearanceChanged", dorm_state = "dormState",
    dorm_furniture = "dormFurniture", dorm_open_changed = "dormOpenChanged",
    dorm_invite_received = "dormInviteReceived", dorm_error = "dormError", friend = "friend",
    dorm_invite_declined = "dormInviteDeclined",
    // Enum values.
    idle = "idle", walk = "walk", run = "run", wave = "wave", bow = "bow", sit = "sit",
    say = "say", house_channel = "house", whisper = "whisper", roarwing = "roarwing",
    burrowsby = "burrowsby", skytalon = "skytalon", serpentyne = "serpentyne",
    castle = "castle", dorm = "dorm", placed = "placed", moved = "moved", removed = "removed",
    request_received = "requestReceived", added = "added", request_closed = "requestClosed",
    offline = "offline",
    // Finishes and visible effects (protocol v4).
    finish_none = "none", shimmer = "shimmer", colour_shift = "colour_shift",
    glow = "glow", chameleon = "chameleon", scale = "scale", babbling = "babbling",
    disguise = "disguise", lucky = "lucky", steam = "steam", nameplate_hidden = "nameplateHidden",
    hair = "hair", robe = "robe",
    // Protocol v5.
    cast = "cast", lucerna = "lucerna", class_zone = "class", arena = "arena",
    // Protocol v8.
    fly = "fly", hover = "hover",
    // Protocol v11: spell effects.
    bound = "bound", slowed = "slowed", recoloured = "recoloured", guardian = "guardian",
    animal = "animal", guarding = "guarding", hare = "hare", fox = "fox", cat = "cat",
    owl = "owl", otter = "otter", horse = "horse",
}

thread_local! {
    static KEYS: Keys = Keys::new();
}

/// Decodes the server frames of one connection. Keeps the visible set and the
/// per-entity baselines (protocol v2 snapshot rules), so `snapshot.states`
/// are always absolute, and the looks sent on this connection (protocol v3,
/// `protocol::looks`), so `snapshot.entered` is always complete. `welcome`
/// resets both, `zoneChanged` the visible set; make a new decoder for each
/// new connection.
#[wasm_bindgen]
pub struct ServerDecoder {
    rx: SnapshotReceiver,
    looks: LookCache,
    states: Vec<EntityState>,
    entered: Vec<EntityInfo>,
}

impl Default for ServerDecoder {
    fn default() -> Self {
        Self::new()
    }
}

#[wasm_bindgen]
impl ServerDecoder {
    #[wasm_bindgen(constructor)]
    pub fn new() -> ServerDecoder {
        ServerDecoder {
            rx: SnapshotReceiver::new(),
            looks: LookCache::new(),
            states: Vec::new(),
            entered: Vec::new(),
        }
    }

    /// Decodes one binary WebSocket frame into a `ServerMessage`. Throws on a
    /// malformed frame, and on a snapshot that does not fit the visible set
    /// (the client is out of sync: close the socket and reconnect).
    #[wasm_bindgen(unchecked_return_type = "ServerMessage")]
    pub fn decode(&mut self, bytes: &[u8]) -> Result<JsValue, JsError> {
        let msg =
            protocol::decode_server(bytes).map_err(|_| JsError::new("malformed server message"))?;
        match &msg {
            ServerMsg::Welcome { .. } => {
                self.rx.reset();
                self.looks.clear();
            }
            ServerMsg::ZoneChanged { .. } => self.rx.reset(),
            ServerMsg::Snapshot {
                entered,
                full,
                deltas,
                left,
                ..
            } => {
                self.states.clear();
                self.entered.clear();
                let desync = |e: Desync| JsError::new(&format!("snapshot out of sync: {e}"));
                self.looks
                    .resolve(entered, &mut self.entered)
                    .map_err(desync)?;
                self.rx
                    .apply(
                        entered.iter().map(EntityEntry::id),
                        full,
                        deltas,
                        left,
                        &mut self.states,
                    )
                    .map_err(desync)?;
            }
            _ => {}
        }
        Ok(KEYS.with(|k| server_msg_to_js(k, &msg, &self.entered, &self.states)))
    }

    /// Forgets the visible set and every look (e.g. before reusing the
    /// decoder on a new socket).
    pub fn reset(&mut self) {
        self.rx.reset();
        self.looks.clear();
    }

    /// Entities currently in the visible set.
    #[wasm_bindgen(js_name = visibleCount, getter)]
    pub fn visible_count(&self) -> u32 {
        self.rx.visible().len() as u32
    }
}

#[inline]
fn put(o: &Object, key: &JsValue, value: &JsValue) {
    // Defining a property on a fresh plain object cannot fail.
    let _ = Reflect::set(o, key, value);
}

#[inline]
fn num(o: &Object, key: &JsValue, value: f64) {
    put(o, key, &JsValue::from_f64(value));
}

fn typed(k: &Keys, ty: &JsValue) -> Object {
    let o = Object::new();
    put(&o, &k.type_, ty);
    o
}

fn kinded(k: &Keys, kind: &JsValue) -> Object {
    let o = Object::new();
    put(&o, &k.kind, kind);
    o
}

/// Centimetres to metres, in f64 so `1234` reads as `12.34` in JS.
#[inline]
fn metres(cm: i32) -> f64 {
    f64::from(cm) / 100.0
}

#[inline]
fn radians(yaw: u8) -> f64 {
    f64::from(yaw) / 256.0 * std::f64::consts::TAU
}

/// An i64 id as a JS number (ids stay below 2^53).
#[inline]
fn id_num(v: i64) -> JsValue {
    JsValue::from_f64(v as f64)
}

fn vec3(p: QPos) -> JsValue {
    let a = Array::new_with_length(3);
    a.set(0, JsValue::from_f64(metres(p.x)));
    a.set(1, JsValue::from_f64(metres(p.y)));
    a.set(2, JsValue::from_f64(metres(p.z)));
    a.into()
}

fn bytes(v: &[u8]) -> JsValue {
    let a = Array::new_with_length(v.len() as u32);
    for (i, &b) in v.iter().enumerate() {
        a.set(i as u32, JsValue::from_f64(f64::from(b)));
    }
    a.into()
}

fn anim_name(k: &Keys, anim: Anim) -> &JsValue {
    match anim {
        Anim::Idle => &k.idle,
        Anim::Walk => &k.walk,
        Anim::Run => &k.run,
        Anim::Wave => &k.wave,
        Anim::Bow => &k.bow,
        Anim::Sit => &k.sit,
        Anim::Cast => &k.cast,
        Anim::Fly => &k.fly,
        Anim::Hover => &k.hover,
    }
}

fn house_name(k: &Keys, house: House) -> &JsValue {
    match house {
        House::Roarwing => &k.roarwing,
        House::Burrowsby => &k.burrowsby,
        House::Skytalon => &k.skytalon,
        House::Serpentyne => &k.serpentyne,
    }
}

/// Codes are rare, so these are built on demand rather than cached in `Keys`.
fn kick_code_name(code: KickCode) -> &'static str {
    match code {
        KickCode::ServerRestart => "server_restart",
        KickCode::Timeout => "timeout",
        KickCode::TooSlow => "too_slow",
        KickCode::ServerFull => "server_full",
        KickCode::Replaced => "replaced",
        KickCode::Banned => "banned",
        KickCode::ProtocolError => "protocol_error",
        KickCode::VersionMismatch => "version_mismatch",
        KickCode::SessionExpired => "session_expired",
        KickCode::NoCharacter => "no_character",
        KickCode::Flood => "flood",
    }
}

fn chat_reject_name(r: ChatRejectReason) -> &'static str {
    match r {
        ChatRejectReason::RateLimited => "rate_limited",
        ChatRejectReason::Filtered => "filtered",
        ChatRejectReason::UnknownTarget => "unknown_target",
        ChatRejectReason::Invalid => "invalid",
        ChatRejectReason::NoChannel => "no_channel",
    }
}

fn dorm_error_name(c: DormErrorCode) -> &'static str {
    use DormErrorCode::*;
    match c {
        NotOwner => "not_owner",
        NotInDorm => "not_in_dorm",
        ItemNotFound => "item_not_found",
        NotFurniture => "not_furniture",
        ItemUnavailable => "item_unavailable",
        OutOfBounds => "out_of_bounds",
        CapReached => "cap_reached",
        NotFriend => "not_friend",
        NotOnline => "not_online",
        Closed => "closed",
        InviteExpired => "invite_expired",
        TooFar => "too_far",
        WrongHouse => "wrong_house",
        UnknownTarget => "unknown_target",
        RateLimited => "rate_limited",
        Failed => "failed",
        Full => "full",
        Mounted => "mounted",
        NotEmpty => "not_empty",
        BlocksWay => "blocks_way",
    }
}

fn character_to_js(k: &Keys, c: &CharacterInfo) -> JsValue {
    let o = Object::new();
    put(&o, &k.name, &JsValue::from_str(&c.name));
    put(&o, &k.house, house_name(k, c.house));
    o.into()
}

fn finish_name(k: &Keys, f: Finish) -> &JsValue {
    match f {
        Finish::None => &k.finish_none,
        Finish::Shimmer => &k.shimmer,
        Finish::ColourShift => &k.colour_shift,
    }
}

fn worn_to_js(k: &Keys, w: Option<Worn>) -> JsValue {
    let Some(w) = w else {
        return JsValue::NULL;
    };
    let o = Object::new();
    num(&o, &k.item_def, f64::from(w.item_def));
    put(&o, &k.dyes, &bytes(&w.dyes));
    put(&o, &k.finish, finish_name(k, w.finish));
    o.into()
}

fn visible_effect_to_js(k: &Keys, e: &VisibleEffect) -> JsValue {
    let o = match e {
        VisibleEffect::Glow { colour } => {
            let o = kinded(k, &k.glow);
            num(&o, &k.colour, f64::from(*colour));
            o
        }
        VisibleEffect::Chameleon { part, family } => {
            let o = kinded(k, &k.chameleon);
            let part = match part {
                ChameleonPart::Hair => &k.hair,
                ChameleonPart::Robe => &k.robe,
            };
            put(&o, &k.part, part);
            num(&o, &k.family, f64::from(*family));
            o
        }
        VisibleEffect::Scale { pct } => {
            let o = kinded(k, &k.scale);
            num(&o, &k.pct, f64::from(*pct));
            o
        }
        VisibleEffect::Babbling => kinded(k, &k.babbling),
        VisibleEffect::Disguise { appearance } => {
            let o = kinded(k, &k.disguise);
            put(&o, &k.appearance, &appearance_to_js(k, appearance));
            o
        }
        VisibleEffect::Lucky => kinded(k, &k.lucky),
        VisibleEffect::Steam => kinded(k, &k.steam),
        VisibleEffect::NameplateHidden => kinded(k, &k.nameplate_hidden),
        VisibleEffect::Lucerna => kinded(k, &k.lucerna),
        // Protocol v11.
        VisibleEffect::Bound => kinded(k, &k.bound),
        VisibleEffect::Slowed { pct } => {
            let o = kinded(k, &k.slowed);
            num(&o, &k.pct, f64::from(*pct));
            o
        }
        VisibleEffect::Recoloured { colour } => {
            let o = kinded(k, &k.recoloured);
            num(&o, &k.colour, f64::from(*colour));
            o
        }
        VisibleEffect::Guardian { animal, guarding } => {
            let o = kinded(k, &k.guardian);
            let name = match animal {
                GuardianAnimal::Hare => &k.hare,
                GuardianAnimal::Fox => &k.fox,
                GuardianAnimal::Cat => &k.cat,
                GuardianAnimal::Owl => &k.owl,
                GuardianAnimal::Otter => &k.otter,
                GuardianAnimal::Horse => &k.horse,
            };
            put(&o, &k.animal, name);
            put(&o, &k.guarding, &JsValue::from_bool(*guarding));
            o
        }
    };
    o.into()
}

fn effects_to_js(k: &Keys, effects: &[VisibleEffect]) -> JsValue {
    array(effects, |e| visible_effect_to_js(k, e))
}

fn appearance_to_js(k: &Keys, a: &Appearance) -> JsValue {
    let o = Object::new();
    num(&o, &k.body_type, f64::from(a.body_type));
    num(&o, &k.height, f64::from(a.height));
    put(&o, &k.face, &bytes(&a.face));
    num(&o, &k.skin_tone, f64::from(a.skin_tone));
    num(&o, &k.hair_style, f64::from(a.hair_style));
    num(&o, &k.hair_colour, f64::from(a.hair_colour));
    num(&o, &k.eye_colour, f64::from(a.eye_colour));
    let outfit = Object::new();
    put(&outfit, &k.hat, &worn_to_js(k, a.outfit.hat));
    put(&outfit, &k.body, &worn_to_js(k, a.outfit.body));
    put(&outfit, &k.legs, &worn_to_js(k, a.outfit.legs));
    put(&outfit, &k.shoes, &worn_to_js(k, a.outfit.shoes));
    put(&o, &k.outfit, &outfit);
    o.into()
}

fn entity_info_to_js(k: &Keys, e: &EntityInfo) -> JsValue {
    let o = Object::new();
    num(&o, &k.id, f64::from(e.id));
    put(&o, &k.character, &character_to_js(k, &e.character));
    num(&o, &k.appearance_version, f64::from(e.appearance_version));
    put(&o, &k.appearance, &appearance_to_js(k, &e.appearance));
    num(&o, &k.effects_version, f64::from(e.effects_version));
    put(&o, &k.effects, &effects_to_js(k, &e.effects));
    num(&o, &k.level, f64::from(e.level));
    let npc = e.npc.as_deref().map_or(JsValue::NULL, JsValue::from_str);
    put(&o, &k.npc, &npc);
    o.into()
}

fn entity_state_to_js(k: &Keys, s: &EntityState) -> JsValue {
    let o = Object::new();
    num(&o, &k.id, f64::from(s.id));
    num(&o, &k.x, metres(s.pos.x));
    num(&o, &k.y, metres(s.pos.y));
    num(&o, &k.z, metres(s.pos.z));
    num(&o, &k.yaw, radians(s.yaw));
    put(&o, &k.anim, anim_name(k, s.anim));
    o.into()
}

fn zone_to_js(k: &Keys, z: &Zone) -> JsValue {
    match z {
        Zone::Castle => kinded(k, &k.castle).into(),
        Zone::Dorm {
            owner_name,
            house,
            layout,
        } => {
            let o = kinded(k, &k.dorm);
            put(&o, &k.owner_name, &JsValue::from_str(owner_name));
            put(&o, &k.house, house_name(k, *house));
            put(&o, &k.layout, &JsValue::from_str(layout));
            o.into()
        }
        Zone::Class {
            class,
            section,
            layout,
        } => {
            let o = kinded(k, &k.class_zone);
            put(&o, &k.class, &JsValue::from_str(class));
            num(&o, &k.section, f64::from(*section));
            put(&o, &k.layout, &JsValue::from_str(layout));
            o.into()
        }
        Zone::Arena {
            duel_id,
            venue,
            layout,
        } => {
            let o = kinded(k, &k.arena);
            num(&o, &k.duel_id, f64::from(*duel_id));
            put(&o, &k.venue, &JsValue::from_str(venue));
            put(&o, &k.layout, &JsValue::from_str(layout));
            o.into()
        }
        Zone::WishingRoom {
            owner_name,
            room,
            layout,
        } => {
            let o = kinded(k, &JsValue::from_str("wishing_room"));
            put(&o, &k.owner_name, &JsValue::from_str(owner_name));
            put(
                &o,
                &JsValue::from_str("room"),
                &JsValue::from_str(room.id()),
            );
            put(&o, &k.layout, &JsValue::from_str(layout));
            o.into()
        }
    }
}

fn location_to_js(k: &Keys, l: &Location) -> JsValue {
    match l {
        Location::Castle { place } => {
            let o = kinded(k, &k.castle);
            put(&o, &k.place, &JsValue::from_str(place));
            o.into()
        }
        Location::Dorm { owner_name } => {
            let o = kinded(k, &k.dorm);
            put(&o, &k.owner_name, &JsValue::from_str(owner_name));
            o.into()
        }
        Location::WishingRoom { owner_name } => {
            let o = kinded(k, &JsValue::from_str("wishing_room"));
            put(&o, &k.owner_name, &JsValue::from_str(owner_name));
            o.into()
        }
    }
}

fn placed_to_js(k: &Keys, p: &PlacedFurniture) -> JsValue {
    let o = Object::new();
    put(&o, &k.item_id, &id_num(p.item_id));
    num(&o, &k.item_def, f64::from(p.item_def));
    put(&o, &k.dyes, &bytes(&p.dyes));
    put(&o, &k.pos, &vec3(p.pos));
    num(&o, &k.yaw, radians(p.yaw));
    o.into()
}

fn furniture_edit_to_js(k: &Keys, e: &FurnitureEdit) -> JsValue {
    match e {
        FurnitureEdit::Placed(p) => {
            let o = kinded(k, &k.placed);
            put(&o, &k.item, &placed_to_js(k, p));
            o.into()
        }
        FurnitureEdit::Moved { item_id, pos, yaw } => {
            let o = kinded(k, &k.moved);
            put(&o, &k.item_id, &id_num(*item_id));
            put(&o, &k.pos, &vec3(*pos));
            num(&o, &k.yaw, radians(*yaw));
            o.into()
        }
        FurnitureEdit::Removed { item_id } => {
            let o = kinded(k, &k.removed);
            put(&o, &k.item_id, &id_num(*item_id));
            o.into()
        }
    }
}

fn friend_event_to_js(k: &Keys, e: &FriendEvent) -> JsValue {
    let with_character = |kind: &JsValue, character_id: i64| {
        let o = kinded(k, kind);
        put(&o, &k.character_id, &id_num(character_id));
        o
    };
    let o = match e {
        FriendEvent::RequestReceived {
            request_id,
            character_id,
            name,
            house,
        } => {
            let o = with_character(&k.request_received, *character_id);
            put(&o, &k.request_id, &id_num(*request_id));
            put(&o, &k.name, &JsValue::from_str(name));
            put(&o, &k.house, house_name(k, *house));
            o
        }
        FriendEvent::Added {
            character_id,
            name,
            house,
            location,
            room_open,
        } => {
            let o = with_character(&k.added, *character_id);
            put(&o, &k.name, &JsValue::from_str(name));
            put(&o, &k.house, house_name(k, *house));
            let loc = location
                .as_ref()
                .map_or(JsValue::NULL, |l| location_to_js(k, l));
            put(&o, &k.location, &loc);
            put(&o, &k.room_open, &JsValue::from_bool(*room_open));
            o
        }
        FriendEvent::RequestClosed { request_id } => {
            let o = kinded(k, &k.request_closed);
            put(&o, &k.request_id, &id_num(*request_id));
            o
        }
        FriendEvent::Removed { character_id } => with_character(&k.removed, *character_id),
        FriendEvent::Online {
            character_id,
            location,
            room_open,
        } => {
            let o = with_character(&k.online, *character_id);
            put(&o, &k.location, &location_to_js(k, location));
            put(&o, &k.room_open, &JsValue::from_bool(*room_open));
            o
        }
        FriendEvent::Moved {
            character_id,
            location,
        } => {
            let o = with_character(&k.moved, *character_id);
            put(&o, &k.location, &location_to_js(k, location));
            o
        }
        FriendEvent::Offline { character_id } => with_character(&k.offline, *character_id),
        FriendEvent::RoomOpen { character_id, open } => {
            let o = with_character(&k.room_open, *character_id);
            put(&o, &k.open, &JsValue::from_bool(*open));
            o
        }
    };
    o.into()
}

fn array<T>(items: &[T], f: impl Fn(&T) -> JsValue) -> JsValue {
    let a = Array::new_with_length(items.len() as u32);
    for (i, item) in items.iter().enumerate() {
        a.set(i as u32, f(item));
    }
    a.into()
}

/// `entered` and `states` are what the decoder resolved for a snapshot: the
/// complete entries and the absolute states.
fn server_msg_to_js(
    k: &Keys,
    msg: &ServerMsg,
    entered: &[EntityInfo],
    states: &[EntityState],
) -> JsValue {
    let o = match msg {
        ServerMsg::Welcome {
            protocol_version,
            your_id,
            character,
            appearance_version,
            appearance,
            effects_version,
            effects,
            zone,
            spawn,
            yaw,
            tick_hz,
            snapshot_hz,
            unix_ms,
        } => {
            let o = typed(k, &k.welcome);
            // Exact up to 2^53 ms.
            num(&o, &k.unix_ms, *unix_ms as f64);
            num(&o, &k.protocol_version, f64::from(*protocol_version));
            num(&o, &k.your_id, f64::from(*your_id));
            put(&o, &k.character, &character_to_js(k, character));
            num(&o, &k.appearance_version, f64::from(*appearance_version));
            put(&o, &k.appearance, &appearance_to_js(k, appearance));
            num(&o, &k.effects_version, f64::from(*effects_version));
            put(&o, &k.effects, &effects_to_js(k, effects));
            put(&o, &k.zone, &zone_to_js(k, zone));
            put(&o, &k.spawn, &vec3(*spawn));
            num(&o, &k.yaw, radians(*yaw));
            num(&o, &k.tick_hz, f64::from(*tick_hz));
            num(&o, &k.snapshot_hz, f64::from(*snapshot_hz));
            o
        }
        ServerMsg::Snapshot {
            tick, left, looks, ..
        } => {
            let o = typed(k, &k.snapshot);
            num(&o, &k.tick, f64::from(*tick));
            put(&o, &k.entered, &array(entered, |e| entity_info_to_js(k, e)));
            put(&o, &k.states, &array(states, |s| entity_state_to_js(k, s)));
            put(
                &o,
                &k.left,
                &array(left, |&id| JsValue::from_f64(f64::from(id))),
            );
            put(
                &o,
                &k.looks,
                &array(looks, |l| {
                    let c = Object::new();
                    num(&c, &k.id, f64::from(l.id));
                    match l.target {
                        Some(t) => num(&c, &k.target, f64::from(t)),
                        None => put(&c, &k.target, &JsValue::NULL),
                    }
                    c.into()
                }),
            );
            o
        }
        ServerMsg::Roster { online, nearby } => {
            let o = typed(k, &k.roster);
            num(&o, &k.online, f64::from(*online));
            num(&o, &k.nearby, f64::from(*nearby));
            o
        }
        ServerMsg::Chat {
            from,
            from_name,
            channel,
            text,
        } => {
            let o = typed(k, &k.chat);
            num(&o, &k.from, f64::from(*from));
            put(&o, &k.from_name, &JsValue::from_str(from_name));
            match channel {
                ChatChannel::Say => put(&o, &k.channel, &k.say),
                ChatChannel::House => put(&o, &k.channel, &k.house_channel),
                ChatChannel::Whisper { to } => {
                    put(&o, &k.channel, &k.whisper);
                    put(&o, &k.to, &JsValue::from_str(to));
                }
                ChatChannel::Party => put(&o, &k.channel, &JsValue::from_str("party")),
                ChatChannel::Club => put(&o, &k.channel, &JsValue::from_str("club")),
            }
            put(&o, &k.text, &JsValue::from_str(text));
            o
        }
        ServerMsg::Correction { seq, pos } => {
            let o = typed(k, &k.correction);
            num(&o, &k.seq, f64::from(*seq));
            put(&o, &k.pos, &vec3(*pos));
            o
        }
        ServerMsg::Pong {
            client_time_ms,
            server_time_ms,
        } => {
            let o = typed(k, &k.pong);
            num(&o, &k.client_time_ms, f64::from(*client_time_ms));
            // Exact up to 2^53 ms, some 285,000 years.
            num(&o, &k.server_time_ms, *server_time_ms as f64);
            o
        }
        ServerMsg::Queued { position } => {
            let o = typed(k, &k.queued);
            num(&o, &k.position, f64::from(*position));
            o
        }
        ServerMsg::Kick { code, reason } => {
            let o = typed(k, &k.kick);
            put(&o, &k.code, &JsValue::from_str(kick_code_name(*code)));
            put(&o, &k.reason, &JsValue::from_str(reason));
            o
        }
        ServerMsg::ChatRejected { client_seq, reason } => {
            let o = typed(k, &k.chat_rejected);
            num(&o, &k.client_seq, f64::from(*client_seq));
            put(&o, &k.reason, &JsValue::from_str(chat_reject_name(*reason)));
            o
        }
        ServerMsg::ZoneChanged { zone, spawn, yaw } => {
            let o = typed(k, &k.zone_changed);
            put(&o, &k.zone, &zone_to_js(k, zone));
            put(&o, &k.spawn, &vec3(*spawn));
            num(&o, &k.yaw, radians(*yaw));
            o
        }
        ServerMsg::AppearanceChanged {
            id,
            appearance_version,
            appearance,
        } => {
            let o = typed(k, &k.appearance_changed);
            num(&o, &k.id, f64::from(*id));
            num(&o, &k.appearance_version, f64::from(*appearance_version));
            put(&o, &k.appearance, &appearance_to_js(k, appearance));
            o
        }
        ServerMsg::DormState {
            open_to_friends,
            cap,
            items,
        } => {
            let o = typed(k, &k.dorm_state);
            put(
                &o,
                &k.open_to_friends,
                &JsValue::from_bool(*open_to_friends),
            );
            num(&o, &k.cap, f64::from(*cap));
            put(&o, &k.items, &array(items, |p| placed_to_js(k, p)));
            o
        }
        ServerMsg::DormFurniture { edit } => {
            let o = typed(k, &k.dorm_furniture);
            put(&o, &k.edit, &furniture_edit_to_js(k, edit));
            o
        }
        ServerMsg::DormOpenChanged { open } => {
            let o = typed(k, &k.dorm_open_changed);
            put(&o, &k.open, &JsValue::from_bool(*open));
            o
        }
        ServerMsg::DormInviteReceived {
            invite_id,
            from_name,
            expires_in_secs,
            wishing_room,
        } => {
            let o = typed(k, &k.dorm_invite_received);
            num(&o, &k.invite_id, f64::from(*invite_id));
            put(&o, &k.from_name, &JsValue::from_str(from_name));
            num(&o, &k.expires_in_secs, f64::from(*expires_in_secs));
            let room = wishing_room.map_or(JsValue::NULL, |r| JsValue::from_str(r.id()));
            put(&o, &JsValue::from_str("wishingRoom"), &room);
            o
        }
        ServerMsg::DormError { code, item_id } => {
            let o = typed(k, &k.dorm_error);
            put(&o, &k.code, &JsValue::from_str(dorm_error_name(*code)));
            if let Some(id) = item_id {
                put(&o, &k.item_id, &id_num(*id));
            }
            o
        }
        ServerMsg::Friend { event } => {
            let o = typed(k, &k.friend);
            put(&o, &k.event, &friend_event_to_js(k, event));
            o
        }
        ServerMsg::DormInviteDeclined { by_name } => {
            let o = typed(k, &k.dorm_invite_declined);
            put(&o, &k.by_name, &JsValue::from_str(by_name));
            o
        }
        ServerMsg::EffectsChanged {
            id,
            effects_version,
            effects,
        } => {
            let o = typed(k, &JsValue::from_str("effectsChanged"));
            num(&o, &k.id, f64::from(*id));
            num(&o, &k.effects_version, f64::from(*effects_version));
            put(&o, &k.effects, &effects_to_js(k, effects));
            o
        }
        other => match school_msg_to_js(other) {
            Some(v) => return v,
            None => economy_msg_to_js(other),
        },
    };
    o.into()
}

/// The messages, through the generic [`shape`] serializer (the
/// shapes `TS_TYPES` documents); `None` for every other message.
fn school_msg_to_js(msg: &ServerMsg) -> Option<JsValue> {
    use ServerMsg as M;
    let school = matches!(
        msg,
        M::Progress { .. }
            | M::LevelChanged { .. }
            | M::Spellbook { .. }
            | M::SpellCast { .. }
            | M::Levitating { .. }
            | M::SpellError { .. }
            | M::NpcEvent { .. }
            | M::ClassJoined { .. }
            | M::LessonPhaseChanged { .. }
            | M::LessonRound { .. }
            | M::BeatScored { .. }
            | M::ClassResult { .. }
            | M::ClassError { .. }
            | M::DuelChallenged { .. }
            | M::DuelChallengeEnded { .. }
            | M::DuelError { .. }
            | M::DuelStart { .. }
            | M::DuelEvents { .. }
            | M::DuelResult { .. }
            | M::DuelList { .. }
            | M::Mounted { .. }
            | M::WishingRoomDoor { .. }
            | M::Discovered { .. }
            | M::ForcedMove { .. }
            | M::Revealed { .. }
            | M::NpcReply { .. }
            | M::PartyInvited { .. }
            | M::PartyState { .. }
            | M::PartyNotice { .. }
            | M::PartyError { .. }
            | M::ClubInvited { .. }
            | M::ClubState { .. }
            | M::ClubPresence { .. }
            | M::ClubNotice { .. }
            | M::ClubError { .. }
    );
    if !school {
        return None;
    }
    // A shape is plain JSON (strings, numbers, arrays, objects), so the round
    // trip through JSON.parse cannot fail.
    let json = shape::to_shape(msg).ok()?.to_string();
    js_sys::JSON::parse(&json).ok()
}

/// The messages other than `EffectsChanged`. Rare, so they are
/// built with plain string keys rather than cached `Keys`.
fn economy_msg_to_js(msg: &ServerMsg) -> Object {
    let o = Object::new();
    let ty = |name: &str| set(&o, "type", name);
    match msg {
        ServerMsg::Wallet { crowns } => {
            ty("wallet");
            set(&o, "crowns", *crowns as f64);
        }
        ServerMsg::MailNotice { unread } => {
            ty("mailNotice");
            set(&o, "unread", *unread);
        }
        ServerMsg::StationOpened {
            kind,
            target,
            expires_in_secs,
        } => {
            ty("stationOpened");
            set(&o, "kind", kind.id());
            set(&o, "target", target.as_str());
            set(&o, "expiresInSecs", *expires_in_secs);
        }
        ServerMsg::FindResult { spot, outcome } => {
            ty("findResult");
            set(&o, "spot", spot.as_str());
            set(&o, "outcome", find_outcome_to_js(outcome));
        }
        ServerMsg::TradeRequested {
            trade_id,
            from,
            from_name,
            expires_in_secs,
        } => {
            ty("tradeRequested");
            set(&o, "tradeId", *trade_id);
            set(&o, "from", *from);
            set(&o, "fromName", from_name.as_str());
            set(&o, "expiresInSecs", *expires_in_secs);
        }
        ServerMsg::TradeState { view } => {
            ty("tradeState");
            set(&o, "view", trade_view_to_js(view));
        }
        ServerMsg::TradeResult { trade_id, outcome } => {
            ty("tradeResult");
            set(&o, "tradeId", *trade_id);
            set(&o, "outcome", trade_outcome_to_js(*outcome));
        }
        ServerMsg::TradeError { trade_id, code } => {
            ty("tradeError");
            if let Some(id) = trade_id {
                set(&o, "tradeId", *id);
            }
            set(&o, "code", trade_error_name(*code));
        }
        ServerMsg::OwnEffects {
            effects,
            speed_pct,
            broom_pct,
        } => {
            ty("ownEffects");
            let list = array(effects, |e| {
                let x = Object::new();
                set(&x, "kind", e.kind.id());
                set(&x, "remainingSecs", e.remaining_secs);
                let fortune = e
                    .fortune
                    .map_or(JsValue::NULL, |f| JsValue::from_str(f.id()));
                set(&x, "fortune", fortune);
                x.into()
            });
            set(&o, "effects", list);
            set(&o, "speedPct", *speed_pct);
            set(&o, "broomPct", *broom_pct);
        }
        ServerMsg::Drank { item_id, effect } => {
            ty("drank");
            set(&o, "itemId", id_num(*item_id));
            set(&o, "effect", effect.id());
        }
        ServerMsg::MimicDraughtRequest {
            request_id,
            from,
            from_name,
            expires_in_secs,
        } => {
            ty("mimicDraughtRequest");
            set(&o, "requestId", *request_id);
            set(&o, "from", *from);
            set(&o, "fromName", from_name.as_str());
            set(&o, "expiresInSecs", *expires_in_secs);
        }
        ServerMsg::MimicDraughtDeclined { by_name } => {
            ty("mimicDraughtDeclined");
            set(&o, "byName", by_name.as_str());
        }
        ServerMsg::PotionError { code, item_id } => {
            ty("potionError");
            set(&o, "code", potion_error_name(*code));
            if let Some(id) = item_id {
                set(&o, "itemId", id_num(*id));
            }
        }
        // Every other message is converted by `server_msg_to_js`.
        _ => ty("unknown"),
    }
    o
}

fn find_outcome_to_js(outcome: &FindOutcome) -> JsValue {
    let o = Object::new();
    match outcome {
        FindOutcome::Found { items, crowns } => {
            set(&o, "kind", "found");
            let list = array(items, |i| {
                let x = Object::new();
                set(&x, "itemDef", i.item_def);
                set(&x, "quantity", i.quantity);
                x.into()
            });
            set(&o, "items", list);
            set(&o, "crowns", *crowns as f64);
        }
        FindOutcome::Nothing => set(&o, "kind", "nothing"),
        FindOutcome::Cooldown { ready_in_secs } => {
            set(&o, "kind", "cooldown");
            set(&o, "readyInSecs", *ready_in_secs);
        }
        FindOutcome::Failed => set(&o, "kind", "failed"),
    }
    o.into()
}

fn offer_to_js(offer: &TradeOffer) -> JsValue {
    let o = Object::new();
    let items = array(&offer.items, |i| {
        let x = Object::new();
        set(&x, "itemId", id_num(i.item_id));
        set(&x, "itemDef", i.item_def);
        set(&x, "dyes", bytes(&i.dyes));
        set(&x, "finish", i.finish.id());
        set(&x, "quantity", i.quantity);
        let maker = i.maker.as_deref().map_or(JsValue::NULL, JsValue::from_str);
        set(&x, "maker", maker);
        x.into()
    });
    set(&o, "items", items);
    set(&o, "crowns", offer.crowns as f64);
    o.into()
}

fn trade_view_to_js(v: &TradeView) -> JsValue {
    let o = Object::new();
    set(&o, "tradeId", v.trade_id);
    set(&o, "partner", v.partner);
    set(&o, "partnerName", v.partner_name.as_str());
    set(&o, "rev", v.rev);
    set(&o, "mine", offer_to_js(&v.mine));
    set(&o, "theirs", offer_to_js(&v.theirs));
    set(&o, "mineLocked", v.mine_locked);
    set(&o, "theirsLocked", v.theirs_locked);
    set(&o, "mineConfirmed", v.mine_confirmed);
    set(&o, "theirsConfirmed", v.theirs_confirmed);
    o.into()
}

fn trade_outcome_to_js(outcome: TradeOutcome) -> JsValue {
    let o = Object::new();
    let kind = match outcome {
        TradeOutcome::Completed => "completed",
        TradeOutcome::Cancelled => "cancelled",
        TradeOutcome::Declined => "declined",
        TradeOutcome::Expired => "expired",
        TradeOutcome::WalkedAway => "walked_away",
        TradeOutcome::Disconnected => "disconnected",
        TradeOutcome::Failed(code) => {
            set(&o, "code", trade_error_name(code));
            "failed"
        }
    };
    set(&o, "kind", kind);
    o.into()
}

fn trade_error_name(c: TradeErrorCode) -> &'static str {
    use TradeErrorCode::*;
    match c {
        UnknownTarget => "unknown_target",
        SelfTrade => "self_trade",
        TooFar => "too_far",
        Busy => "busy",
        UnknownTrade => "unknown_trade",
        Expired => "expired",
        Locked => "locked",
        NotLocked => "not_locked",
        StaleRevision => "stale_revision",
        InvalidOffer => "invalid_offer",
        ItemUnavailable => "item_unavailable",
        InsufficientCrowns => "insufficient_crowns",
        RateLimited => "rate_limited",
        Failed => "failed",
    }
}

fn potion_error_name(c: PotionErrorCode) -> &'static str {
    use PotionErrorCode::*;
    match c {
        ItemNotFound => "item_not_found",
        NotAPotion => "not_a_potion",
        NeedsTarget => "needs_target",
        NotFriend => "not_friend",
        TooFar => "too_far",
        NotOnline => "not_online",
        Expired => "expired",
        Disabled => "disabled",
        Busy => "busy",
        RateLimited => "rate_limited",
        Failed => "failed",
    }
}

// ---------------------------------------------------------------- NPCs

/// NPCs in the open castle (data/npcs; docs/architecture.md "NPCs"): the
/// roster, every NPC's look, the talking portraits on their walls, and the
/// lines with their seeded picks. Everything an NPC says is picked and
/// shown on this client only.
#[wasm_bindgen]
pub struct NpcBook {
    roster: sim_core::npcs::Roster,
    looks: sim_core::npcs::Looks,
    portraits: Vec<sim_core::npcs::PlacedPortrait>,
    lines: sim_core::npcs::LinesFile,
}

fn npc_err(e: impl std::fmt::Display) -> JsError {
    JsError::new(&e.to_string())
}

#[wasm_bindgen]
impl NpcBook {
    /// From `data/npcs/roster.json`, `looks.json`, `portraits.json`,
    /// `lines.json` and the castle layout (the portraits' walls). Throws on
    /// invalid data (the server and the tests check the files fully).
    #[wasm_bindgen(constructor)]
    pub fn new(
        #[wasm_bindgen(js_name = rosterJson)] roster_json: &str,
        #[wasm_bindgen(js_name = looksJson)] looks_json: &str,
        #[wasm_bindgen(js_name = portraitsJson)] portraits_json: &str,
        #[wasm_bindgen(js_name = linesJson)] lines_json: &str,
        #[wasm_bindgen(js_name = layoutJson)] layout_json: &str,
    ) -> Result<NpcBook, JsError> {
        let roster = sim_core::npcs::Roster::from_json(roster_json).map_err(npc_err)?;
        let looks = sim_core::npcs::Looks::from_json(looks_json).map_err(npc_err)?;
        let layout = sim_core::Layout::from_json(layout_json).map_err(npc_err)?;
        let portraits = sim_core::npcs::PortraitsFile::from_json(portraits_json)
            .and_then(|p| p.resolve(&roster, &layout))
            .map_err(npc_err)?;
        let lines = sim_core::npcs::LinesFile::from_json(lines_json).map_err(npc_err)?;
        Ok(NpcBook {
            roster,
            looks,
            portraits,
            lines,
        })
    }

    /// The rules (anti-annoyance numbers, caps, speeds).
    #[wasm_bindgen(unchecked_return_type = "NpcRulesView")]
    pub fn rules(&self) -> Result<JsValue, JsError> {
        to_js_via_json(&self.roster.rules)
    }

    /// Every roster NPC, in file order.
    #[wasm_bindgen(unchecked_return_type = "NpcView[]")]
    pub fn npcs(&self) -> Result<JsValue, JsError> {
        to_js_via_json(&self.roster.npcs)
    }

    /// One NPC by its id (`EntityInfo.npc`), or `undefined` (a professor,
    /// an unknown id).
    #[wasm_bindgen(unchecked_return_type = "NpcView | undefined")]
    pub fn npc(&self, id: &str) -> Result<JsValue, JsError> {
        match self.roster.get(id) {
            Some(n) => to_js_via_json(n),
            None => Ok(JsValue::UNDEFINED),
        }
    }

    /// An NPC's look (a portrait's subject included), in the decoder's
    /// `Appearance` shape, or `undefined`.
    #[wasm_bindgen(unchecked_return_type = "Appearance | undefined")]
    pub fn look(&self, id: &str) -> JsValue {
        match self.looks.get(id) {
            Some(a) => KEYS.with(|k| appearance_to_js(k, a)),
            None => JsValue::UNDEFINED,
        }
    }

    /// The talking portraits on their walls.
    #[wasm_bindgen(unchecked_return_type = "PortraitView[]")]
    pub fn portraits(&self) -> Result<JsValue, JsError> {
        to_js_via_json(&self.portraits)
    }

    /// A seeded, weighted pick among NPC `npc`'s `when` lines whose
    /// conditions hold in `context`, leaving out the line indices in
    /// `exclude` (said already in this conversation): the line's index, or
    /// `undefined`. `seed`: any u32 (random for talk; fixed for repeatable
    /// tests).
    pub fn pick(
        &self,
        npc: &str,
        #[wasm_bindgen(unchecked_param_type = "LineWhen")] when: &str,
        #[wasm_bindgen(unchecked_param_type = "LineContext")] context: JsValue,
        seed: u32,
        exclude: Vec<u16>,
    ) -> Result<Option<u16>, JsError> {
        let when =
            sim_core::npcs::LineWhen::from_id(when).ok_or_else(|| JsError::new("unknown when"))?;
        let cx = line_context(context)?;
        let key = self.roster.get(npc).map_or(npc, |n| n.lines_key());
        Ok(self.lines.pick(key, when, &cx, u64::from(seed), &exclude))
    }

    /// Today's hint of a daily-hint portrait for this character (the same
    /// all UTC day), or `undefined` when no hint line holds (every secret it
    /// hints at found).
    #[wasm_bindgen(js_name = dailyHint)]
    pub fn daily_hint(
        &self,
        npc: &str,
        #[wasm_bindgen(unchecked_param_type = "LineContext")] context: JsValue,
        #[wasm_bindgen(js_name = unixMs)] unix_ms: f64,
        #[wasm_bindgen(js_name = characterId)] character_id: f64,
    ) -> Result<Option<u16>, JsError> {
        let cx = line_context(context)?;
        let key = self.roster.get(npc).map_or(npc, |n| n.lines_key());
        let day = sim_core::npcs::gift_day(unix_ms as i64);
        Ok(self.lines.daily_hint(key, &cx, day, character_id as i64))
    }

    /// The text of NPC `npc`'s line `index` (placeholders `{name}` etc. left
    /// for the caller), or `undefined`.
    pub fn text(&self, npc: &str, index: u16) -> Option<String> {
        let key = self.roster.get(npc).map_or(npc, |n| n.lines_key());
        self.lines.text(key, index).map(str::to_owned)
    }
}

fn line_context(v: JsValue) -> Result<sim_core::npcs::LineContext, JsError> {
    let json = js_sys::JSON::stringify(&v)
        .map_err(|_| JsError::new("LineContext is not JSON"))?
        .as_string()
        .unwrap_or_default();
    serde_json::from_str(&json).map_err(npc_err)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn encodes_client_messages() {
        let hello = protocol::decode_client(&encode_hello("tok", 42.0)).unwrap();
        assert_eq!(
            hello,
            ClientMsg::Hello {
                protocol_version: PROTOCOL_VERSION,
                token: "tok".into(),
                character_id: Some(42),
            }
        );
        let mv = protocol::decode_client(&encode_move(7, 1.5, 0.0, -2.0, 0.5, "run").unwrap());
        assert_eq!(
            mv.unwrap(),
            ClientMsg::Move {
                seq: 7,
                pos: [1.5, 0.0, -2.0],
                yaw: 0.5,
                anim: Anim::Run
            }
        );
        let decode = |b: Vec<u8>| protocol::decode_client(&b).unwrap();
        assert_eq!(
            decode(encode_set_view_limit(20)),
            ClientMsg::SetViewLimit { n: 20 }
        );
        assert_eq!(
            decode(encode_ping(99)),
            ClientMsg::Ping { client_time_ms: 99 }
        );
        assert_eq!(
            decode(encode_interact("dorm_exit")),
            ClientMsg::Interact {
                target: "dorm_exit".into()
            }
        );
        assert_eq!(decode(encode_leave_instance()), ClientMsg::LeaveInstance);
        assert_eq!(
            decode(encode_dorm_set_open(true)),
            ClientMsg::DormSetOpen { open: true }
        );
        assert_eq!(
            decode(encode_dorm_invite("Bea")),
            ClientMsg::DormInvite {
                character_name: "Bea".into()
            }
        );
        assert_eq!(
            decode(encode_dorm_accept_invite(4)),
            ClientMsg::DormAcceptInvite { invite_id: 4 }
        );
        assert_eq!(
            decode(encode_dorm_visit("Bea")),
            ClientMsg::DormVisit {
                owner_name: "Bea".into()
            }
        );
        assert_eq!(
            decode(encode_dorm_kick(70_001)),
            ClientMsg::DormKick { entity: 70_001 }
        );
        assert_eq!(
            decode(encode_dorm_decline_invite(4)),
            ClientMsg::DormDeclineInvite { invite_id: 4 }
        );
    }

    #[test]
    fn encodes_the_economy_messages() {
        let decode = |b: Vec<u8>| protocol::decode_client(&b).unwrap();
        assert_eq!(
            decode(encode_trade_request(70_001)),
            ClientMsg::TradeRequest { target: 70_001 }
        );
        assert_eq!(
            decode(encode_trade_respond(3, true)),
            ClientMsg::TradeRespond {
                trade_id: 3,
                accept: true
            }
        );
        assert_eq!(
            decode(encode_trade_lock(3, false)),
            ClientMsg::TradeLock {
                trade_id: 3,
                locked: false
            }
        );
        assert_eq!(
            decode(encode_trade_confirm(3, 9)),
            ClientMsg::TradeConfirm {
                trade_id: 3,
                rev: 9
            }
        );
        assert_eq!(
            decode(encode_trade_cancel(3)),
            ClientMsg::TradeCancel { trade_id: 3 }
        );
        assert_eq!(
            decode(encode_mimic_draught_answer(4, true)),
            ClientMsg::MimicDraughtAnswer {
                request_id: 4,
                accept: true
            }
        );
        let offer = trade_offer(3, &[12.0, 13.0], &[40, 1], 250.0).unwrap();
        assert_eq!(
            offer,
            ClientMsg::TradeSetOffer {
                trade_id: 3,
                items: vec![
                    OfferItem {
                        item_id: 12,
                        quantity: 40
                    },
                    OfferItem {
                        item_id: 13,
                        quantity: 1
                    },
                ],
                crowns: 250,
            }
        );
        assert!(trade_offer(3, &[12.0], &[], 0.0).is_err(), "lengths differ");
        assert!(trade_offer(3, &[1.5], &[1], 0.0).is_err(), "not an id");
        assert!(trade_offer(3, &[], &[], -1.0).is_err(), "negative Crowns");
        assert!(trade_offer(3, &[], &[], 0.5).is_err(), "half a Crown");
    }

    #[test]
    fn encodes_the_school_and_dueling_messages() {
        let decode = |b: Vec<u8>| protocol::decode_client(&b).unwrap();
        assert_eq!(
            decode(encode_trace_gesture(1, 2, -30, &[0, -80, 40, 16, -70, 20]).unwrap()),
            ClientMsg::TraceGesture {
                round: 1,
                beat: 2,
                start_offset_ms: -30,
                points: vec![
                    TracePoint {
                        dt_ms: 0,
                        x: -80,
                        y: 40
                    },
                    TracePoint {
                        dt_ms: 16,
                        x: -70,
                        y: 20
                    },
                ],
            }
        );
        assert!(trace_points(&[1, 2]).is_err(), "not triples");
        assert!(trace_points(&[300, 0, 0]).is_err(), "dt out of range");
        assert_eq!(
            decode(encode_cast_spell(2, "furniture", Some(77.0)).unwrap()),
            ClientMsg::CastSpell {
                spell: 2,
                target: SpellTarget::Furniture { item_id: 77 },
            }
        );
        assert!(spell_target("furniture", None).is_err());
        assert_eq!(spell_target("none", None), Ok(SpellTarget::None));
        assert_eq!(
            spell_target("entity", Some(70_001.0)),
            Ok(SpellTarget::Entity { id: 70_001 })
        );
        assert_eq!(
            decode(encode_end_spell(1)),
            ClientMsg::EndSpell { spell: 1 }
        );
        assert_eq!(
            decode(encode_duel_challenge(70_002)),
            ClientMsg::DuelChallenge { target: 70_002 }
        );
        assert_eq!(
            decode(encode_duel_challenge_name("Bea")),
            ClientMsg::DuelChallengeName { name: "Bea".into() }
        );
        assert_eq!(
            decode(encode_duel_respond(4, false)),
            ClientMsg::DuelRespond {
                challenge_id: 4,
                accept: false
            }
        );
        assert_eq!(
            decode(encode_duel_cancel(4)),
            ClientMsg::DuelCancel { challenge_id: 4 }
        );
        assert_eq!(
            decode(encode_duel_cast(11)),
            ClientMsg::DuelCast { spell: 11 }
        );
        assert_eq!(decode(encode_duel_forfeit()), ClientMsg::DuelForfeit);
        assert_eq!(
            decode(encode_duel_list_request()),
            ClientMsg::DuelListRequest
        );
        assert_eq!(
            decode(encode_duel_spectate(9)),
            ClientMsg::DuelSpectate { duel_id: 9 }
        );
    }

    #[test]
    fn validates_enums_and_chat() {
        assert_eq!(parse_anim("walk"), Ok(Anim::Walk));
        // Protocol v8: on a broom.
        assert_eq!(parse_anim("fly"), Ok(Anim::Fly));
        assert_eq!(parse_anim("hover"), Ok(Anim::Hover));
        assert!(parse_anim("swim").is_err());
        assert!(parse_anim("sit").is_err(), "emotes are not move anims");
        assert_eq!(
            protocol::decode_client(&encode_mount(true)),
            Ok(ClientMsg::Mount { on: true })
        );

        let whisper = chat_msg("whisper", Some("Tobin".into()), "hi", 3).unwrap();
        assert_eq!(
            whisper,
            ClientMsg::Chat {
                channel: ChatChannel::Whisper { to: "Tobin".into() },
                text: "hi".into(),
                client_seq: 3,
            }
        );
        // `to` is ignored outside whispers.
        let say = chat_msg("say", Some("x".into()), "hi", 0).unwrap();
        assert!(matches!(
            say,
            ClientMsg::Chat {
                channel: ChatChannel::Say,
                ..
            }
        ));
        assert!(chat_msg("house", None, "hi", 0).is_ok());
        assert!(chat_msg("whisper", None, "hi", 0).is_err());
        assert!(chat_msg("whisper", Some(String::new()), "hi", 0).is_err());
        assert!(chat_msg("shout", None, "hi", 0).is_err());
        assert!(matches!(
            chat_msg("party", None, "hi", 0).unwrap(),
            ClientMsg::Chat {
                channel: ChatChannel::Party,
                ..
            }
        ));
        assert_eq!(
            protocol::decode_client(&encode_party_respond(4, false)),
            Ok(ClientMsg::PartyRespond {
                invite_id: 4,
                accept: false
            })
        );
        assert_eq!(
            protocol::decode_client(&encode_party_invite("Anne Marie")),
            Ok(ClientMsg::PartyInvite {
                name: "Anne Marie".into()
            })
        );
        assert_eq!(
            protocol::decode_client(&encode_party_leave()),
            Ok(ClientMsg::PartyLeave)
        );
        assert!(matches!(
            chat_msg("club", None, "hi", 0).unwrap(),
            ClientMsg::Chat {
                channel: ChatChannel::Club,
                ..
            }
        ));
        assert_eq!(
            protocol::decode_client(&encode_club_found(9, "Owls", "OW")),
            Ok(ClientMsg::ClubFound {
                secretary: 9,
                name: "Owls".into(),
                tag: "OW".into()
            })
        );
        assert_eq!(
            protocol::decode_client(&encode_club_respond(3, true)),
            Ok(ClientMsg::ClubRespond {
                invite_id: 3,
                accept: true
            })
        );
        assert_eq!(
            protocol::decode_client(&encode_club_set_motd("x")),
            Ok(ClientMsg::ClubSetMotd { motd: "x".into() })
        );
        assert_eq!(
            protocol::decode_client(&encode_club_disband()),
            Ok(ClientMsg::ClubDisband)
        );
        assert!(chat_msg("say", None, &"a".repeat(CHAT_MAX_LEN), 0).is_ok());
        assert!(chat_msg("say", None, &"a".repeat(CHAT_MAX_LEN + 1), 0).is_err());
    }
}
