// The sound id catalogue (docs/architecture.md "Sound and music"): every sound
// the game plays, one typed list, grouped. Game code names sounds only by these
// ids (`audio.play("ui.click")`); each effect and ambience id has a
// definition (sfx/defs.ts), and each music and stinger id a score (music/). A typo in a hook is a type error.
//
// Each entry says which bus it plays on, whether it is placed in the world
// (3D, through a panner at a position) or heard flat (2D), whether it loops,
// what it is made from (`cc0`: a vetted CC0 recording, `synth`: synthesized
// in the repo, `score`: a written piece the music engine plays), the file bank
// it is loaded with (only `cc0` files are downloaded; see places.ts
// `banksFor`), its priority when the voice cap is reached (3 keeps playing
// longest) and a cooldown that drops repeats closer together than that.
//
// Adding an id: append it here, then its definition in sfx/defs.ts or its score in
// music/. The tests check the groups, the prefixes and that every spell,
// class and emitter named by the data has its id.

export type Bus = "music" | "effects" | "ambience" | "interface";

/** What a sound is made from. */
export type Source = "cc0" | "synth" | "score";

/** File banks: the CC0 files are fetched by bank as the player reaches an area (places.ts `banksFor`). */
export const BANKS = ["core", "castle", "grounds"] as const;
export type BankId = (typeof BANKS)[number];

export const GROUPS = [
  "ui",
  "footsteps",
  "doors",
  "stairs",
  "spells",
  "classes",
  "duels",
  "flight",
  "crafting",
  "potions",
  "mail",
  "cup",
  "secrets",
  "world",
  "emitters",
  "sprinkles",
  "ambience",
  "music",
  "stingers",
] as const;
export type SoundGroup = (typeof GROUPS)[number];

export interface SoundEntry {
  id: string;
  group: SoundGroup;
  bus: Bus;
  /** Placed in the world (a panner at `playAt`'s position); false: heard flat. */
  spatial: boolean;
  loop: boolean;
  source: Source;
  bank: BankId;
  /** Kept longest when the voice cap is reached: 0 (first dropped) .. 3. */
  priority: 0 | 1 | 2 | 3;
  /** A repeat of this id sooner than this (ms, per owner) is dropped. */
  cooldownMs: number;
  /** What it is, for whoever defines it. */
  what: string;
}

interface Extra {
  bus?: Bus;
  spatial?: boolean;
  loop?: boolean;
  bank?: BankId;
  priority?: 0 | 1 | 2 | 3;
  cooldownMs?: number;
}

/** Defaults per group: bus, 3D or not, priority. */
const GROUP_DEFAULTS: Record<SoundGroup, { bus: Bus; spatial: boolean; priority: 0 | 1 | 2 | 3 }> = {
  ui: { bus: "interface", spatial: false, priority: 3 },
  footsteps: { bus: "effects", spatial: true, priority: 0 },
  doors: { bus: "effects", spatial: true, priority: 2 },
  stairs: { bus: "effects", spatial: true, priority: 2 },
  spells: { bus: "effects", spatial: true, priority: 2 },
  classes: { bus: "interface", spatial: false, priority: 3 },
  duels: { bus: "effects", spatial: true, priority: 3 },
  flight: { bus: "effects", spatial: true, priority: 2 },
  crafting: { bus: "effects", spatial: false, priority: 2 },
  potions: { bus: "effects", spatial: true, priority: 2 },
  mail: { bus: "effects", spatial: false, priority: 2 },
  cup: { bus: "interface", spatial: false, priority: 2 },
  secrets: { bus: "effects", spatial: true, priority: 3 },
  world: { bus: "ambience", spatial: true, priority: 1 },
  emitters: { bus: "ambience", spatial: true, priority: 1 },
  sprinkles: { bus: "ambience", spatial: true, priority: 0 },
  ambience: { bus: "ambience", spatial: false, priority: 3 },
  music: { bus: "music", spatial: false, priority: 3 },
  stingers: { bus: "music", spatial: false, priority: 3 },
};

function e<const I extends string, const Gr extends SoundGroup>(id: I, group: Gr, source: Source, what: string, x: Extra = {}): SoundEntry & { id: I; group: Gr } {
  const d = GROUP_DEFAULTS[group];
  return {
    id,
    group,
    bus: x.bus ?? d.bus,
    spatial: x.spatial ?? d.spatial,
    loop: x.loop ?? (group === "ambience" || group === "music" || group === "emitters"),
    source,
    bank: x.bank ?? "core",
    priority: x.priority ?? d.priority,
    cooldownMs: x.cooldownMs ?? 0,
    what,
  };
}

const G = "grounds" as const;
const C = "castle" as const;

export const SOUNDS = [
  // ---------------------------------------------------------------- ui (interface bus, synthesized, flat)
  e("ui.click", "ui", "synth", "a button or menu item pressed", { cooldownMs: 40 }),
  e("ui.open", "ui", "synth", "a window opens (the windows stack)", { cooldownMs: 80 }),
  e("ui.close", "ui", "synth", "a window closes", { cooldownMs: 80 }),
  e("ui.tab", "ui", "synth", "a tab or page within a window", { cooldownMs: 60 }),
  e("ui.toggle", "ui", "synth", "a setting switched, auto run or walk toggled", { cooldownMs: 60 }),
  e("ui.slider", "ui", "synth", "a volume slider's preview tick (on its own bus)", { cooldownMs: 90 }),
  e("ui.error", "ui", "synth", "a refusal: an error line, a refused cast or mount", { cooldownMs: 250 }),
  e("ui.notify", "ui", "synth", "an important system line (not every chat line)", { cooldownMs: 500 }),
  e("ui.whisper", "ui", "synth", "an incoming whisper", { cooldownMs: 400 }),
  e("ui.toast", "ui", "synth", "a toast appears", { cooldownMs: 300 }),
  e("ui.invite", "ui", "synth", "an invitation: a trade, a duel challenge, a dorm invite", { cooldownMs: 500 }),
  e("ui.select", "ui", "synth", "a character selected in the world (the ring)", { cooldownMs: 60 }),
  e("ui.coins_in", "ui", "cc0", "crowns received (the purse grows)", { cooldownMs: 200 }),
  e("ui.coins_out", "ui", "cc0", "crowns spent (a purchase, postage, a fee)", { cooldownMs: 200 }),
  e("ui.equip", "ui", "cc0", "a garment worn or taken off (cloth)", { cooldownMs: 120 }),
  e("ui.dye", "ui", "synth", "a dye applied (a soft magical wash)", { cooldownMs: 200 }),
  e("ui.parchment_open", "ui", "cc0", "the map or the timetable unrolls", { cooldownMs: 200 }),
  e("ui.parchment_close", "ui", "cc0", "the map or the timetable rolls up", { cooldownMs: 200 }),
  e("ui.page", "ui", "cc0", "a page turns: the spellbook, the catalogue, lore", { cooldownMs: 120 }),
  e("ui.friend_online", "ui", "synth", "a friend comes online", { cooldownMs: 1000 }),

  // ---------------------------------------------------------------- footsteps (CC0, by surface; places.ts footSurface)
  e("foot.stone", "footsteps", "cc0", "a step on castle flagstones", { cooldownMs: 120 }),
  e("foot.wood", "footsteps", "cc0", "a step on boards: stages, the covered bridge, dorms, stands", { cooldownMs: 120 }),
  e("foot.carpet", "footsteps", "cc0", "a step on rugs: Roarwing and Skytalon common rooms, Divination", { cooldownMs: 120 }),
  e("foot.grass", "footsteps", "cc0", "a step on the grounds' turf", { bank: G, cooldownMs: 120 }),
  e("foot.gravel", "footsteps", "cc0", "a step on shingle and soil: the lake shore, the greenhouse", { bank: G, cooldownMs: 120 }),
  e("foot.land", "footsteps", "cc0", "both feet down: off a broom", { cooldownMs: 300 }),

  // ---------------------------------------------------------------- doors, entrances, the moving staircases
  e("door.heavy", "doors", "cc0", "a heavy castle door: into a class, the Wishing Room", { cooldownMs: 600 }),
  e("door.wood", "doors", "cc0", "a wooden door: into and out of a dorm", { cooldownMs: 600 }),
  e("entrance.portrait", "doors", "cc0", "the guardian portrait swings open (Roarwing)", { cooldownMs: 800 }),
  e("entrance.double", "doors", "cc0", "the knocker door's two leaves part (Skytalon)", { cooldownMs: 800 }),
  e("entrance.tapestry", "doors", "cc0", "a tapestry lifts aside (cloth swish)", { cooldownMs: 800 }),
  e("entrance.panel", "doors", "cc0", "a wooden panel slides along the wall", { cooldownMs: 800 }),
  e("entrance.painting", "doors", "cc0", "a painting swings out on its hinge", { cooldownMs: 800 }),
  e("entrance.statue", "doors", "synth", "a statue grinds aside", { cooldownMs: 800 }),
  e("entrance.wall", "doors", "synth", "a stretch of wall sinks into the floor (Serpentyne, passages)", { cooldownMs: 800 }),
  e("entrance.barrels", "doors", "cc0", "the barrel stack rolls open (Burrowsby)", { cooldownMs: 800 }),
  e("stairs.warning", "stairs", "synth", "a flight shudders before it swings (dust)", { cooldownMs: 1000 }),
  e("stairs.swing", "stairs", "synth", "stone grinding while a flight swings", { loop: true }),
  e("stairs.dock", "stairs", "synth", "a flight settles at its new landing", { cooldownMs: 1000 }),
  e("stairs.gate", "stairs", "synth", "a landing rail is conjured in or vanishes in a puff of smoke (a soft poof)", { cooldownMs: 600 }),

  // ---------------------------------------------------------------- spells (synthesized; `spell.<key>` for every spell in data/spells.json)
  e("spell.lucerna", "spells", "synth", "Lantern Charm: the wand tip lights (a bright chime)", { cooldownMs: 150 }),
  e("spell.lucerna_off", "spells", "synth", "the wand light goes out", { cooldownMs: 150 }),
  e("spell.elevara", "spells", "synth", "Levitation Charm: a rising shimmer", { cooldownMs: 150 }),
  e("spell.levitate_end", "spells", "synth", "a levitated thing settles back down", { cooldownMs: 300 }),
  e("spell.scutara", "spells", "synth", "Shield Charm: a shield blooms and hums", { cooldownMs: 100 }),
  e("spell.torpeo", "spells", "synth", "Stunning Spell: a red bolt's crack and zip", { cooldownMs: 100 }),
  e("spell.dimitto", "spells", "synth", "Disarming Charm: a whip-crack bolt", { cooldownMs: 100 }),
  e("spell.titillara", "spells", "synth", "Tickling Charm: a fizzing, tickling bolt", { cooldownMs: 100 }),
  e("spell.repulsa", "spells", "synth", "Knockback Jinx: a thumping push", { cooldownMs: 100 }),
  e("spell.trail", "spells", "synth", "a gesture drawn in the air (a cast or a professor's demonstration)", { cooldownMs: 200 }),
  e("spell.fizzle", "spells", "synth", "a spell that fails (spellError)", { cooldownMs: 250 }),
  e("spell.monstra_viam", "spells", "synth", "Wayfinder Charm: the trail of orbs appears", { spatial: false, cooldownMs: 400 }),
  // Spells to level 20 (protocol v11): the new casts and what they do (sfx/magic.ts SPELL_L20_DEFS).
  e("spell.tinctura", "spells", "synth", "Colour Charm: a bright colour-wash shimmer", { cooldownMs: 150 }),
  e("spell.rigora", "spells", "synth", "Binding Curse: a stiff, chalky crack", { cooldownMs: 100 }),
  e("spell.volucra", "spells", "synth", "Bird Conjuring: a pop and a burst of feathers", { cooldownMs: 150 }),
  e("spell.adveni", "spells", "synth", "Summoning Charm: a hooked, reeling zip", { cooldownMs: 100 }),
  e("spell.dissolvo", "spells", "synth", "Unweaving: a quick unravelling chime", { cooldownMs: 100 }),
  e("spell.aperio", "spells", "synth", "Revealing Charm: a sweeping, searching ring", { cooldownMs: 200 }),
  e("spell.tardara", "spells", "synth", "Slowing Jinx: a slow, heavy magenta wave", { cooldownMs: 100 }),
  e("spell.custodio", "spells", "synth", "Guardian Charm: a rising silver swell", { cooldownMs: 200 }),
  e("spell.bound", "spells", "synth", "someone bound stiff (a stiffening crackle)", { cooldownMs: 200 }),
  e("spell.slowed", "spells", "synth", "someone slowed (a slurred whoosh)", { cooldownMs: 200 }),
  e("spell.pulled", "spells", "synth", "someone yanked toward the caster", { cooldownMs: 200 }),
  e("spell.dispelled", "spells", "synth", "spells ended (an unravelling chime)", { cooldownMs: 200 }),
  e("spell.recoloured", "spells", "synth", "a robe changes colour (a bright shimmer)", { cooldownMs: 200 }),
  e("spell.reveal_glint", "spells", "synth", "a revealed spot shining", { cooldownMs: 80 }),
  e("volucra.flutter", "spells", "synth", "a flock's wings beating", { cooldownMs: 300 }),
  e("volucra.chirp", "spells", "synth", "a small bird's chirp", { cooldownMs: 120 }),
  e("guardian.summon", "spells", "synth", "a guardian bursts out of silver mist", { cooldownMs: 300 }),
  e("guardian.ambient", "spells", "synth", "a guardian's soft shimmer while it follows (the nearest one)", { loop: true }),
  e("guardian.fade", "spells", "synth", "a guardian dissolves", { cooldownMs: 300 }),

  // ---------------------------------------------------------------- classes (the beats are interface sounds, timed to the beat)
  e("class.bell", "classes", "synth", "the school bell: a class session begins (heard faintly anywhere in the castle)", { cooldownMs: 5000 }),
  e("class.join", "classes", "synth", "arriving in a class (a soft welcome chord)", { cooldownMs: 2000 }),
  e("class.beat_tick", "classes", "synth", "the count-in tick before a practice beat"),
  e("class.beat_open", "classes", "synth", "a beat's window opens: trace, pick or fly now"),
  e("class.grade_perfect", "classes", "synth", "a beat scored Perfect"),
  e("class.grade_great", "classes", "synth", "a beat scored Great"),
  e("class.grade_good", "classes", "synth", "a beat scored Good"),
  e("class.miss", "classes", "synth", "a beat missed"),
  e("class.reject", "classes", "synth", "a trace not counted"),
  e("class.pick", "classes", "synth", "an answer chosen on the pick tray", { cooldownMs: 80 }),
  e("class.ring_glow", "classes", "synth", "a Flying ring lights up", { bus: "effects", spatial: true }),
  e("class.ring_through", "classes", "synth", "flying through a ring"),
  // The class's own reaction to a passed trace or a right pick (classes/fx/<class>.ts).
  e("class.fx.charms", "classes", "synth", "Charms: a feather lifts, sparkles"),
  e("class.fx.potions", "classes", "synth", "Potions: the cauldron bubbles up and clears"),
  e("class.fx.transfiguration", "classes", "synth", "Transfiguration: a soft pop as a thing becomes another"),
  e("class.fx.defence", "classes", "synth", "Defence: a drill spell lands"),
  e("class.fx.history", "classes", "synth", "History of Magic: a quill scratch and a page"),
  e("class.fx.divination", "classes", "synth", "Divination: the crystal ball hums"),
  e("class.fx.astronomy", "classes", "synth", "Astronomy: a star twinkles into place"),
  e("class.fx.herbology", "classes", "synth", "Herbology: leaves rustle and a plant perks up"),
  e("class.fx.creatures", "classes", "synth", "Creature Keeping: a small creature's happy chirp"),

  // ---------------------------------------------------------------- duels (the arena; spells sound as `spell.<key>` on launch)
  e("duel.countdown", "duels", "synth", "the count to the fight", { spatial: false }),
  e("duel.fight", "duels", "synth", "the fight begins (a struck gong)", { spatial: false }),
  e("duel.charge", "duels", "synth", "a duelist begins a cast (a rising charge)"),
  e("duel.blocked", "duels", "synth", "a bolt stopped on a shield"),
  e("duel.reflected", "duels", "synth", "a bolt sent back by a perfect shield"),
  e("duel.shield_broken", "duels", "synth", "a shield shatters"),
  e("duel.hit", "duels", "synth", "a bolt strikes a duelist"),
  e("duel.disarmed", "duels", "synth", "a wand knocked away"),
  e("duel.interrupted", "duels", "synth", "a cast broken off"),
  e("duel.missed", "duels", "synth", "a bolt fizzles out past its mark"),
  // Spells to level 20: binds, slows, pulls, cleanses and Guardian wards in a duel.
  e("duel.rooted", "duels", "synth", "a duelist rooted to the spot"),
  e("duel.slowed", "duels", "synth", "a duelist caught in the slowing wave"),
  e("duel.pulled", "duels", "synth", "a duelist pulled across the stage"),
  e("duel.cleansed", "duels", "synth", "a duelist frees themselves (the Unweaving)"),
  e("duel.ward_up", "duels", "synth", "a guardian ward rises"),
  e("duel.warded", "duels", "synth", "a spell breaks on a guardian ward"),
  e("duel.ward_faded", "duels", "synth", "a guardian ward fades unused"),

  // ---------------------------------------------------------------- brooms and flying
  e("broom.mount", "flight", "synth", "lifting off on a broom (a whoosh up)", { cooldownMs: 500 }),
  e("broom.land", "flight", "synth", "landing a broom (a settling swish)", { cooldownMs: 500 }),
  e("broom.wind", "flight", "synth", "the rush of air while flying (ours: rate and tone follow the speed)", { spatial: false, loop: true }),
  e("broom.flyby", "flight", "synth", "another rider passes close", { cooldownMs: 1500 }),

  // ---------------------------------------------------------------- crafting stations, cauldrons, finds
  e("craft.station_open", "crafting", "cc0", "a crafting station opened (tools set down)", { cooldownMs: 500 }),
  e("craft.add", "crafting", "synth", "an ingredient dropped in (a plop)", { cooldownMs: 120 }),
  e("craft.stir", "crafting", "synth", "the cauldron stirred", { cooldownMs: 200 }),
  e("craft.success", "crafting", "synth", "a brew or a craft comes out right (a sparkle)", { cooldownMs: 500 }),
  e("craft.fail", "crafting", "synth", "a brew or a craft fails (a dull puff)", { cooldownMs: 500 }),
  e("find.search", "crafting", "cc0", "rummaging at a find spot", { spatial: true, cooldownMs: 500 }),
  e("find.found", "crafting", "synth", "something found", { cooldownMs: 500 }),
  e("find.nothing", "crafting", "synth", "nothing there this time", { cooldownMs: 500 }),

  // ---------------------------------------------------------------- potions and their effects (3D on whoever it is)
  e("potion.drink", "potions", "cc0", "a potion drunk (a gulp)", { cooldownMs: 400 }),
  e("potion.wear_off", "potions", "synth", "an effect wears off", { cooldownMs: 400 }),
  e("potion.glow", "potions", "synth", "a glow begins"),
  e("potion.chameleon", "potions", "synth", "colours shift"),
  e("potion.shrinking", "potions", "synth", "shrinking (a falling squeak)"),
  e("potion.swelling", "potions", "synth", "swelling (a rising stretch)"),
  e("potion.babbling", "potions", "synth", "babbling (garbled syllables, wordless)"),
  e("potion.mimic_draught", "potions", "synth", "Mimic Draught: a bubbling change of shape"),
  e("potion.fortunes_draught", "potions", "synth", "Fortune's Draught: a lucky golden shimmer"),
  e("potion.pick_me_up_tonic", "potions", "synth", "Pick-Me-Up Tonic: a puff of steam from the ears"),
  e("potion.invisibility", "potions", "synth", "fading out of sight"),
  e("potion.fortune", "potions", "synth", "a Divination fortune given (a soft crystal chime)", { spatial: false }),

  // ---------------------------------------------------------------- owl mail, trades
  e("mail.arrive", "mail", "synth", "post arrives: unread mail grew (a hoot and wingbeats)", { cooldownMs: 3000 }),
  e("mail.send", "mail", "cc0", "a letter or parcel sent (paper, then wings)", { cooldownMs: 500 }),
  e("mail.collect", "mail", "cc0", "a parcel collected (paper and string)", { cooldownMs: 500 }),
  e("owl.hoot", "mail", "synth", "an owl hoots (the Owl Loft, the owl mail)", { spatial: true, cooldownMs: 1500 }),
  e("owl.flutter", "mail", "cc0", "an owl's wings", { spatial: true, cooldownMs: 800 }),
  e("trade.complete", "mail", "synth", "a trade completed", { cooldownMs: 500 }),

  // ---------------------------------------------------------------- the House Cup
  e("cup.points", "cup", "synth", "points for our house (gems falling in the hourglass)", { cooldownMs: 1000 }),

  // ---------------------------------------------------------------- secrets and the Wishing Room
  e("secret.reveal", "secrets", "synth", "a hidden passage discovered (with stinger.discovery)", { cooldownMs: 2000 }),
  e("secret.pace", "secrets", "synth", "a pass before the blank wall counted", { spatial: false, cooldownMs: 500 }),
  e("secret.door_appear", "secrets", "synth", "the Wishing Room's door forms in the wall", { cooldownMs: 2000 }),
  e("secret.door_vanish", "secrets", "synth", "the door melts back into stone", { cooldownMs: 2000 }),

  // ---------------------------------------------------------------- the world: levels, the clock
  e("level.up", "world", "synth", "a level gained: golden glints round the character", { bus: "effects", priority: 3, cooldownMs: 1000 }),
  e("world.clock_bell", "world", "synth", "the clock tower tolls the game hour (from the Clock Tower Courtyard)", { cooldownMs: 10000 }),

  // ---------------------------------------------------------------- emitters: loops placed in the world (places.ts EMITTERS)
  e("loop.fountain", "emitters", "cc0", "the Entrance Courtyard's fountain", { bank: G }),
  e("loop.hearth", "emitters", "cc0", "a common room's fire crackling", { bank: C }),
  e("loop.cauldron", "emitters", "synth", "a cauldron simmering (the Potions classroom)"),
  e("loop.owls", "emitters", "synth", "the Owl Loft's owls rustling and cooing", { bank: C }),
  e("loop.lake", "emitters", "cc0", "lake water lapping at the shore (the layer under both lake beds)", { bank: G }),

  // ---------------------------------------------------------------- sprinkles: one-shots an ambience bed scatters round the listener
  e("sprinkle.bird", "sprinkles", "cc0", "a songbird", { bank: G }),
  e("sprinkle.crow", "sprinkles", "cc0", "a distant crow or rook", { bank: G }),
  e("sprinkle.owl_far", "sprinkles", "synth", "a far owl at night"),
  e("sprinkle.frog", "sprinkles", "synth", "a frog by the lake at night", { bank: G }),
  e("sprinkle.drip", "sprinkles", "synth", "a water drip in the dungeons and passages"),
  e("sprinkle.gust", "sprinkles", "synth", "a gust round a tower"),
  e("sprinkle.creak", "sprinkles", "cc0", "old timber creaks", { bank: C }),
  e("sprinkle.fire_pop", "sprinkles", "cc0", "a log pops in the hearth", { bank: C }),
  e("sprinkle.page", "sprinkles", "cc0", "a page turned somewhere in the library", { bank: C }),

  // ---------------------------------------------------------------- ambience beds (one per area and, outdoors, time; places.ts ambienceFor)
  e("amb.castle", "ambience", "synth", "the castle's halls: quiet (indoors: no noise bed, no wind)"),
  e("amb.great_hall", "ambience", "synth", "the Great Hall: quiet under its music (no noise bed)"),
  e("amb.library", "ambience", "synth", "the library: hushed, a clock, pages (sprinkles)"),
  e("amb.tower", "ambience", "synth", "the open towers (Owl Loft, Astronomy platform): wind through the arches", { bank: C }),
  e("amb.owl_loft", "ambience", "cc0", "the Owl Loft: open wind, straw", { bank: C }),
  e("amb.dungeon", "ambience", "synth", "the dungeons: cold and still, drips (sprinkles)"),
  e("amb.kitchens", "ambience", "cc0", "the kitchens: a big fire, pots", { bank: C }),
  e("amb.common_room", "ambience", "cc0", "a common room: the hearth, a cosy hush", { bank: C }),
  e("amb.common_room_tower", "ambience", "synth", "the tower common room (no hearth): a hush, pages, creaks", { bank: C }),
  e("amb.common_room_lake", "ambience", "cc0", "the common room under the lake: the hearth crackling, water muffled through the glass, a page turned", { bank: C }),
  e("amb.under_lake", "ambience", "synth", "the lake heard through thick glass: a slow, deep, muffled wash and a rare soft bubble (a layer of the beds under the lake)"),
  e("amb.passage", "ambience", "synth", "a hidden passage: very close and still, a rare drip"),
  e("amb.courtyard_day", "ambience", "cc0", "a courtyard by day: birds, light wind", { bank: G }),
  e("amb.courtyard_night", "ambience", "cc0", "a courtyard by night: crickets, a breeze", { bank: G }),
  e("amb.grounds_day", "ambience", "cc0", "the grounds by day: birds, wind in the grass and trees", { bank: G }),
  e("amb.grounds_night", "ambience", "cc0", "the grounds by night: crickets, wind, a far owl", { bank: G }),
  e("amb.lake_day", "ambience", "cc0", "the lake shore by day: water lapping, birds", { bank: G }),
  e("amb.lake_night", "ambience", "cc0", "the lake shore by night: lapping, frogs", { bank: G }),
  e("amb.greenhouse", "ambience", "cc0", "the greenhouse: warm, insects, glass ticking", { bank: G }),
  e("amb.classroom", "ambience", "synth", "a classroom: quiet (no noise bed)"),
  e("amb.dorm", "ambience", "synth", "a dormitory: a hush, a soft fire's rare pops"),
  e("amb.dorm_lake", "ambience", "cc0", "the dormitory under the lake: a small fire crackling, the water muffled through the glass, a rare page", { bank: C }),
  e("amb.arena", "ambience", "synth", "the dueling arena: a tense, low steady drone"),
  e("amb.wishing_room", "ambience", "synth", "the Wishing Room: a faint magical shimmer"),
  e("amb.sky", "ambience", "synth", "high in the open sky on a broom: a steady high wind"),

  // ---------------------------------------------------------------- music: our own written pieces (music/), each with a softer night variant
  e("music.castle", "music", "score", "the castle's halls: gentle orchestral"),
  e("music.great_hall", "music", "score", "the Great Hall: warm, grander orchestral"),
  e("music.dungeons", "music", "score", "the dungeons: low, mysterious orchestral with celesta touches"),
  e("music.grounds", "music", "score", "the grounds and courtyards: Celtic/folk (harp, fiddle, flute)"),
  e("music.lake", "music", "score", "the lake shore: harp-led folk, wide and calm"),
  e("music.common_room", "music", "score", "the common rooms and kitchens: cosy folk"),
  e("music.dorm", "music", "score", "the dorms: a music-box lullaby"),
  e("music.class", "music", "score", "indoor classes: whimsical celesta and music box, light"),
  e("music.class_grounds", "music", "score", "Herbology and Creatures: folk with celesta touches"),
  e("music.astronomy", "music", "score", "Astronomy: night sky, celesta and soft strings"),
  e("music.flying", "music", "score", "brooms and the Flying class: brisk folk-orchestral, a little energetic"),
  e("music.duel", "music", "score", "duels: driving strings, soft brass and frame drum, more energetic"),
  e("music.wishing_room", "music", "score", "the Wishing Room: celesta and choir-like pads, wondering"),

  // ---------------------------------------------------------------- stingers: short phrases on the music bus (the loop ducks under them)
  e("stinger.welcome", "stingers", "score", "entering the game: a short welcoming phrase"),
  e("stinger.level_up", "stingers", "score", "our level up"),
  e("stinger.discovery", "stingers", "score", "a secret discovered"),
  e("stinger.class_result", "stingers", "score", "the class results card"),
  e("stinger.duel_won", "stingers", "score", "a duel won"),
  e("stinger.duel_lost", "stingers", "score", "a duel lost or drawn"),
  e("stinger.cup_week", "stingers", "score", "the House Cup week has closed"),
] as const;

export type SoundId = (typeof SOUNDS)[number]["id"];

type IdsOf<G extends SoundGroup> = Extract<(typeof SOUNDS)[number], { group: G }>["id"];
export type AmbienceId = IdsOf<"ambience">;
export type MusicId = IdsOf<"music">;
export type StingerId = IdsOf<"stingers">;
export type EmitterSoundId = IdsOf<"emitters">;
export type SprinkleId = IdsOf<"sprinkles">;
export type FootId = IdsOf<"footsteps">;
/** The sounds defined in sfx/defs.ts: everything but the music and stingers. */
export type EffectId = Exclude<SoundId, MusicId | StingerId>;

const BY_ID: ReadonlyMap<string, SoundEntry> = new Map(SOUNDS.map((s) => [s.id, s]));

export function soundEntry(id: SoundId): SoundEntry {
  return BY_ID.get(id)!;
}

export function isSoundId(id: string): id is SoundId {
  return BY_ID.has(id);
}

export function soundsIn(group: SoundGroup): SoundEntry[] {
  return SOUNDS.filter((s) => s.group === group);
}
