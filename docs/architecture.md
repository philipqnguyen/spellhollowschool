# Architecture

How SpellHollow School is put together: the parts of the repository, how the
server, the shared simulation and the browser client fit together, the wire
protocol and HTTP API, the database, the content data and the asset
pipeline. It describes the code as it is; when you change a contract
described here (a message, an endpoint, a data file's schema), update this
file in the same change.

> Code comments cite this document's headings by name. When you rename a
> heading, update the citations too (`git grep` the old heading).

Related documents:

- [README](../README.md): what the game is and how to run it.
- [CONTRIBUTING](../CONTRIBUTING.md): workflow, checks, conventions.
- [Configuration](configuration.md): every server setting (environment
  variable).
- [Deploying and self-hosting](deploy.md).
- [Asset pipeline](../tools/blender/README.md) and the [art docs](art/)
  (style guide, castle kit, characters).
- [Load testing](../tools/loadtest/README.md), [audio tools](../tools/audio/README.md).
- [Repository hygiene](repo-hygiene.md): what is and is not committed.

## Contents

1. [Repository layout](#repository-layout)
2. [Toolchain and local development](#toolchain-and-local-development)
3. [How the parts fit together](#how-the-parts-fit-together)
4. [Coordinates and world data](#coordinates-and-world-data)
5. [Content data](#content-data)
6. [Network](#network)
7. [HTTP API](#http-api)
8. [Database](#database)
9. [Game systems](#game-systems)
10. [NPCs](#npcs)
11. [Client](#client)
12. [Sound and music](#sound-and-music)
13. [Assets](#assets)
14. [Testing and checks](#testing-and-checks)

## Repository layout

| Path | What | Language |
| --- | --- | --- |
| `crates/protocol` | Wire messages (`ClientMsg`, `ServerMsg`), postcard codec, snapshot delta rules, cached looks | Rust |
| `crates/sim-core` | Shared game rules: layout and movement, flying, moving staircases, furniture placement, content catalogs and their validation, clock and schedule, gesture scoring, duels, NPC routines. Pure: no I/O, no clocks, no randomness | Rust |
| `crates/sim-wasm` | `wasm-bindgen` exports of `sim-core` and `protocol` for the browser | Rust → wasm |
| `crates/server` | The game server binary `shs-server` (HTTP API, WebSocket, world simulation, Postgres) | Rust |
| `tools/loadtest` | Bot clients for load tests ([README](../tools/loadtest/README.md)) | Rust |
| `tools/blender` | Headless Blender scripts that build every 3D asset ([README](../tools/blender/README.md)) | Python |
| `tools/assets` | Publishing and checking asset releases | Shell |
| `tools/audio` | Fetching and converting the CC0 sound files ([README](../tools/audio/README.md)) | Shell, Python |
| `client` | Browser client: Vite + TypeScript + Babylon.js | TypeScript |
| `data/` | Game content: world layouts, items, classes, spells, NPCs, economy numbers, art palettes (see [Content data](#content-data)) | JSON |
| `db/migrations` | Postgres migrations, embedded in the server and applied at start | SQL |
| `art/` | Art sources (Blender scene recipes, textures, credits), CC BY-SA 4.0 ([art/README.md](../art/README.md)) | |
| `docker/` | `Dockerfile`, `compose.yml` (server + Postgres), load-test compose file | |
| `config/`, `.kamal/` | Kamal deployment ([docs/deploy.md](deploy.md)) | YAML |
| `scripts/` | `check.sh` (what CI runs), `build-wasm.sh`, `env.sh`, the committed-files check and git hooks | Shell, Node |
| `bin/dev` | One command for local development: wasm, assets, Postgres check, server and client | Shell |
| `docs/` | This file, configuration, deployment, art docs | Markdown |

Generated and never committed: `client/public/assets/` (Blender exports),
`client/src/wasm/` (wasm bindings), `client/dist/`, `target/`,
`node_modules/`, `vendor_assets/` (downloaded third-party models). See
[repo-hygiene.md](repo-hygiene.md).

## Toolchain and local development

- **Rust**: stable via rustup with the `wasm32-unknown-unknown` target
  (`rust-toolchain.toml` installs both). `scripts/env.sh` puts the rustup
  toolchain first on `PATH` (useful where a system or Homebrew Rust would
  shadow it).
- **Node** with npm workspaces (the root `package.json` and `client/`).
- **Postgres** (any recent version): a local install, or the `db` service of
  `docker/compose.yml`. The server applies migrations on start.
- **Blender** (only to build assets yourself): the version pinned in
  `tools/blender/BLENDER_VERSION`, plus MPFB for the characters. Most
  checkouts download a published asset release instead
  ([tools/blender/README.md](../tools/blender/README.md), "Asset releases").

`bin/dev` runs the whole loop: it builds the wasm bindings, makes
`client/public/assets/` match the checkout (download, else build, else
greybox stand-ins), and starts the server and the Vite dev server.
`client/scripts/prepare.mjs` does the wasm and asset part and runs before
the client's `dev`, `build` and `typecheck` scripts; `SHS_SKIP_ASSETS=1`
skips the assets (CI does).

Ports in development:

- `8080`: the game server (HTTP API under `/api`, `/ws`, `/metrics`).
- `5173`: Vite dev server, proxying `/api` and `/ws` to the server.
- `4173`: `vite preview`, proxying the same.

`client/vite.config.ts` reads `SHS_SERVER` (proxy target, default
`http://localhost:8080`), `SHS_VITE_PORT` and `SHS_PREVIEW_PORT`, so
several checkouts can run side by side (start each server with its own
`SHS_BIND` and `DATABASE_URL`). Server settings: [configuration.md](configuration.md).

## How the parts fit together

```
            data/*.json (content, layouts)
                 │ compiled in / imported at build time
     ┌───────────┼────────────────────────────┐
     ▼           ▼                            ▼
 sim-core ──► sim-wasm (wasm-bindgen) ──► client (Babylon.js)
     │           ▲                            │  HTTP /api (JSON)
     │        protocol ◄──────────────────────┤  WebSocket /ws (postcard)
     ▼           ▼                            ▼
          server (shs-server) ───────────► Postgres
```

- **One rule, two ends.** Movement, flying, staircase timing, furniture
  placement, gesture scoring and content validation live in `sim-core`. The
  server validates with it; the client predicts with the same code compiled
  to wasm (`SimWorld`, `GestureScorer`, `GameClock`, `NpcBook`, ...), so an
  honest client is never corrected.
- **One wire definition.** `protocol` defines every WebSocket message. The
  browser never hand-encodes: it calls `sim_wasm` `encode*` functions and
  decodes with one `ServerDecoder` per socket, which applies snapshot deltas
  and cached looks and hands the client plain objects with a camelCase
  `type` (`"zoneChanged"`) and snake_case enum codes (`"server_restart"`).
- **The server** (`crates/server`) is one process:
  - an axum HTTP router (`http.rs`): JSON API under `/api`, `/metrics`,
    `/ws`, and optionally the built client (`SHS_STATIC_DIR`);
  - the realtime side (`realtime/`): a gateway task per connection
    (handshake, login queue, heartbeat) and the **world**, one thread that
    simulates the castle, grounds and every instance at 20 Hz. Connections
    talk to it through a bounded command channel; it answers through each
    player's bounded outbound queue of pre-encoded frames. Database work
    goes out as jobs on the tokio runtime and comes back as commands, so the
    tick never waits on Postgres;
  - feature modules (`trade`, `potions`, `finds`, `classes`, `dueling`,
    `residents`, ...) that own their HTTP routes and, where they have
    realtime state, plug into the world through `realtime::ext::WorldCtx`;
  - background jobs (`main.rs`): an hourly janitor for expired sessions and
    tokens, and a once-a-minute job that expires Exchange listings, cleans up
    expired effects, find cooldowns, old mail and old daily caps.
- **The HTTP side tells the world about changes** (a new outfit, a friend
  request, a wallet change) through `realtime::world::WorldHandle`, reached
  with `AppState::world()`. None of its calls blocks on a busy world.
- **Content** (`data/`) is compiled into the server (`server::catalog::shipped()`)
  and imported by the client at build time (Vite JSON imports). Both sides
  validate it with the same `sim-core` code; tests load the shipped files.
- **Display names live in data.** Names shown to players (items, spells,
  places, NPCs) are in `data/` files, not in code identifiers or asset file
  names. Asset keys are neutral (`house_a`..`house_d`, `body_a`, ...).

## Coordinates and world data

- Metres, right-handed, **Y up** (as glTF). x = east, z = south. Yaw 0 faces
  +Z; forward is `(sin yaw, 0, cos yaw)`. The client sets
  `scene.useRightHandedSystem = true` so glTF, `sim-core` and the server agree
  without conversions.
- On the wire positions are centimetres (`QPos`) and yaw is a byte (1/256
  turn).
- Storeys are 9 m floor to floor (level 0 at y 0, level -2 at y -18, level 7
  at y 63).

### Layouts

`data/world/layout.json` (the castle and grounds) and
`data/world/instances/<id>.json` (dorm rooms, classrooms, the duel arena,
the Wishing Room's rooms) share one schema, `sim_core::layout::Layout`
(mirrored in `tools/blender/layout_lib.py`):

- **`surfaces`**: walkable rectangles `{ id, kind, name, level, cell, min,
  max, y, ... }`. `kind` is `Room`, `Corridor`, `Door`, `Stair`,
  `Courtyard`, `Grounds` or `Bridge`. Stairs have `rise` and `y_top`;
  grounds surfaces carry `heights` (a heightfield on a shared lattice);
  optional `ceiling`, `house` (only that house may walk there: common rooms
  and their doorways), `moving` (a moving staircase flight and its position)
  and `secret` (a hidden passage). Walls are the gaps between surfaces.
  `name` is the place name shown to players (and in friends' locations).
- **`spawns`**, **`landmarks`** (named points for the HUD and the trail).
- **`interactables`**: `{ id, kind, house?, pos, yaw, radius, name? }`.
  Kinds: `dorm_door`, `dorm_exit`, `find_spot`, `craft_charms`,
  `craft_transfiguration`, `cauldron`, `owl_exchange`, `owl_order`,
  `owl_mail`, `class_door`, `instance_exit`, `notice_board`,
  `wishing_room_door`. In reach means horizontal distance within `radius`
  and height within 1.5 m. A gated one is used only by its house.
- **`place_areas`** (instance layouts): where dorm furniture may stand
  (floor, wall and table areas).
- **`areas`**: named boxes with a purpose: `dueling_club` (where duels may
  be challenged and watched), `solid` (masonry standing on walkable ground),
  `airspace` and `no_fly` (where brooms may fly).
- **`solids`**: blockers `{ id, at, half, y, yaw?, round? }`, boxes or
  ellipses of solid furniture and props. Ground strictly inside one, within
  its height span, is not ground, so movement stops at its outline as at a
  wall, on the server and in prediction alike. They are generated from the
  castle kit's pieces by `tools/blender/world/solids.py`; placed dorm
  furniture adds its catalog `blocker`.
- **`rings`**: the Flying class's ring course.

`Layout::for_house(house)` drops surfaces gated to other houses: each
player's moves are validated against their own house's view (a rival common
room is a wall), and the client predicts with the same view.

Other world files:

| File | What |
| --- | --- |
| `data/world/terrain/grounds.json`, `grounds_r*_c*.json` | The grounds' heightfield (generated by `tools/blender/terrain_layout.py`; do not edit by hand) |
| `data/world/castle_kit.json` | Which cells are dressed with the castle kit, their openings, arches and lights, and the camera's per-cell zoom limit ([castle-kit.md](art/castle-kit.md)) |
| `data/world/props.json` | Props, lights and kit placements per cell |
| `data/world/exterior.json` | The castle's far silhouette (HLOD chunks) |
| `data/world/stairs.json` | The Grand Staircase's moving flights ([Moving staircases](#moving-staircases)) |
| `data/world/secrets.json` | Hidden passages and the Wishing Room ([Secrets and the map](#secrets-and-the-map)) |
| `data/world/key_places.json` | Destinations of the wayfinding trail and the map |
| `data/world/keep_out.json` | Furniture footprints that NPC walkers, bots and the trail keep out of |
| `data/world/finds.json`, `loot.json` | Find spots and their loot tables |

### Cells and the world manifest

Every surface belongs to a `cell`. The asset build writes one glTF per cell
to `client/public/assets/world/<cell>.glb` and lists them in
`client/public/assets/world/manifest.json` (`{ cells: [{ id, file, bounds,
level, ... }] }`). Instance layouts are built into the same directory and
listed in `world/instances.json` with their lights and ambient fill. Entries
may also carry the shared `castle_*` materials they use (`library`), a
camera collider, LOD meshes, a camera zoom limit, `sees` (the cells visible
from that cell, which the client uses to hide the rest), torches, `seats`
and `floor_seats` (where a sitting character is drawn), and fire lists.
Details: [castle-kit.md](art/castle-kit.md).

### Zones

A **zone** is where a character is: the castle (one seamless world per
server, castle and grounds together), a dorm room (one per owner), a class
section, a duel arena, or a Wishing Room. Each character is in exactly one;
visibility, snapshots, nearby chat and the roster's `nearby` count are per
zone. Instances run in the same process and thread as the castle.

### Houses

Four houses (`client/src/content/houses.json`, `protocol::House`):
Roarwing, Burrowsby, Skytalon and Serpentyne. Asset keys and art use
`house_a`..`house_d` in that order. Each has a gated common room in the
castle with its dorm door, crafting stations and owl-order stand.

## Content data

The contract between art (asset keys), server (ids, grants, validation) and
client (labels, assets). Every file has a `version`; most have a `_doc`
string describing their fields. Schemas and checks are in `sim-core` (named
below); the shipped files are tested on every `cargo test`.

**Ids are stable.** Item, recipe, spell, gesture, hair-style, NPC and
secret ids are never reused or renumbered; palette indices and dye families
are only appended. Asset keys match `[a-z0-9_/]`.

### Items, dyes and characters

- **`data/items/catalog.json`** (`sim_core::catalog`): every item:
  `{ id, kind, slot? (clothing), name, asset, dyes: { primary, secondary,
  trim }, house_dyes?, tags, furniture?, stackable, max_stack?,
  required_level, exchange: { category, subcategory }, price?, family?,
  dye?, finish?, potion?, pattern?, ... }`. Kinds: `clothing` (slots `hat`,
  `body`, `legs`, `shoes`), `furniture`, `material`, `dye`, `finish`,
  `potion`, `pattern`. Id ranges: hats 1xx, body 2xx, legs 3xx, shoes 4xx,
  furniture 1001+, materials 3001+, dyes 3101+, finishes 3201+, potions
  3301+, patterns 3401+.
  - Tags: `starter` (granted to new characters), `default` (preselected in
    the creator), `house:<id>` (only that house gets it), plus free tags
    for filters.
  - Furniture: `{ surface: floor | wall | table, footprint, height, top?,
    top_area?, shelves?, seats?, blocker?, station? }`.
  - Clothing may be limited to one body class (`wear`: `female` or
    `male`; absent: anyone) and may belong to a restyle `family`.
- **`data/items/palette.json`**: 48 named colours, the house colour pairs,
  ten dye families and the free `basic` colours. **The dye rule**
  (`Palette::dyes_needed`): recolouring a channel to a colour that is not
  its current one, not the item's default and not basic uses up one dye of
  that colour's family.
- **`data/items/garment_shapes.json`, `garment_designs.json`**: the
  parametric clothing builder's shapes and designs
  ([garment-shapes.md](art/garment-shapes.md)).
- **`data/characters/creator.json`**: body types (`body_a`, `body_b`),
  sliders (height and face shape), skin tones, hair styles and colours, eye
  colours. See [character-adoption.md](art/character-adoption.md) and
  [base-body-spec.md](art/base-body-spec.md).

### Economy and crafting

- **`data/economy.json`** (`sim_core::economy`): the starting purse, Owl
  Exchange rules (listing fee, sales tax, listing duration, limits, price
  range), mail rules (postage, sizes, attachments, retention), trade
  distance, and each potion effect's numbers and duration.
- **`data/clubs.json`** (`server::clubs::ClubRules`, the client's
  `ui/club-state.ts`): what founding a club needs (level and fee), the
  member cap, name, tag and message-of-the-day lengths, the clubs
  secretary's roster id and her lines ([Clubs](#clubs)).
- **`data/crafting/recipes.json`** (`sim_core::crafting`): skill level
  thresholds and recipes `{ id, name, discipline: charms | transfiguration
  | potions, station, kind: craft | restyle, inputs, output, skill, xp,
  craft_secs, pattern?, dyeable?, tint? }`. Checked against the
  catalog (stations match disciplines, restyles stay within a family,
  patterns and recipes name each other).
- **`data/world/finds.json`, `loot.json`** (`sim_core::finds`): find spots
  (each a `find_spot` interactable, once per real day per character) and
  weighted loot tables.
- **Owl Exchange categories** (`sim_core::exchange::CATEGORIES`): hats,
  body, legs, shoes, furniture, materials, dyes, potions, each with its
  subcategories.

### School

- **`data/classes/schedule.json`** (`sim_core::clock`): the game clock and
  the timetable. A game day lasts two real hours (`day_real_secs` 7200, so
  twelve game days per real day), with morning, afternoon and night periods.
  Game time is a pure function of UTC, so every end computes it alone. The
  timetable has six daily slots and ten subjects (Charms, Potions,
  Transfiguration, Defensive Magic, History of Magic, Divination, Astronomy,
  Herbology, Creature Keeping, Flying); each class names its sessions, its
  castle door, its instance layout and its join window and capacity.
- **`data/classes/<class>.json`** (`sim_core::classes`): the professor (an
  NPC with a look from the catalog), the lesson script (say, emote,
  demonstrate, practice rounds of beats, results) and rewards.
- **`data/classes/gestures.json`** (`sim_core::gesture`): the wand gestures
  (templates on a -100..100 pad) and the trace scorer's numbers.
- **`data/classes/picks.json`** and **`data/classes/answers/`**: pick beats'
  vocabularies and their answer keys. The answers are server-only; the
  client never imports them.
- **`data/classes/lore/lore.json`**: lore entries, served by the server only
  once unlocked.
- **`data/progression.json`** (`sim_core::progression`): the XP curve for
  levels 1-20, school years, XP per source and daily caps, loadout slots by
  level.
- **`data/spells.json`** (`sim_core::spells`): every spell `{ id, key, name,
  incantation, school, kind, unlock: { level, at }, colour, gesture, social,
  duel? }`. Duel numbers never scale with level.
- **`data/dueling/rules.json`, `venues.json`** (`sim_core::dueling`): Poise,
  time limit, combat constants, rewards; the Dueling Club venue, its arena
  layout, marks and spectator capacity.
- **`data/house_cup.json`** (`sim_core::house_cup`): the weekly House Cup:
  week start, per-source daily caps, ties, the winners' reward.
- **`data/flying.json`** (`sim_core::flight`): broom speed tiers by Flying
  level, acceleration, climb speed, clearances, the airspace ceiling.

### NPCs, art, audio, moderation

- **`data/npcs/*.json`**: see [NPCs](#npcs).
- **`data/art/lighting.json`, `toon_palette.json`, `castle_textures.json`**:
  the castle's light by game hour, the toon ramps and outline rules, the
  castle kit's materials and palette. Art tunes them; the client reads them.
- **`data/audio/sources.json`**: the source and licence of every committed
  sound file (CC0 only).
- **`data/moderation/blocked_words.txt`**: the chat word filter, one word
  per line, read by the server at start (from this path, relative to its
  working directory). The shipped file holds placeholders only; operators
  should supply their own list.

## Network

- One WebSocket per player at `GET /ws`. Binary frames only, each one
  postcard-encoded `protocol::ClientMsg` or `ServerMsg`.
  `PROTOCOL_VERSION` is **15**; it is bumped on any breaking change, and a
  client on another version is kicked with `VersionMismatch`.
- The first client frame is `Hello { protocol_version, token, character_id }`:
  the session token from `POST /api/auth/login` and, optionally, the
  character the client expects to play. The connection plays the character
  the session has selected (`sessions.character_id`); a session with none is
  kicked `NoCharacter`, and a `Hello` naming another character than the
  session's is kicked `Replaced` (another window picked another character
  meanwhile), so a reconnect never comes back as a different character. An
  older client's `Hello` gets `VersionMismatch`. The server answers
  `Welcome` (the player's entity id, character, look, zone and spawn, tick
  rates and the server clock), or `Queued { position }` while the server is
  at `SHS_PLAYER_CAP`.
- **One connection per account**: a new connection of the same account
  replaces the older one (`Replaced`); it takes over the old one's place
  when it plays the same character, while another character starts where it
  last left off. Every connection re-checks its session every 5 minutes and
  is kicked `SessionExpired` once the session has expired or been deleted,
  or `NoCharacter` once the session no longer plays its character. Deleting
  a character kicks the connection playing it at once.
- **Authority**: the server is authoritative; movement is client-predicted.
  The client sends `Move { seq, pos, yaw, anim }` at 10 Hz; the server checks
  each with `sim_core` (walkable for the player's house, speed against
  earned movement credit, the path) and answers a refused move with
  `Correction`.
- **Rates**: the world ticks at 20 Hz; each client gets snapshots at 10 Hz
  and a `Roster { online, nearby }` once a second.
- **Visibility**: each client receives only its visible set in its zone:
  the nearest N characters (N = its view limit, default 50, at most 100,
  `SetViewLimit`), preferring the same level, always including priority
  entities (online friends in the zone, recent whisper partners, a class's
  professor, a duel's duelists), with hysteresis so the set does not
  flicker. Party members in the zone rank ahead of every other priority
  entity, so they are always in view whatever the distance
  ([Parties](#parties)). NPC students rank behind players.

### Messages

The message families (see `crates/protocol/src/lib.rs` and its modules for
every field):

| Family | Client → server | Server → client |
| --- | --- | --- |
| Session | `Hello`, `Ping`, `SetViewLimit` | `Welcome`, `Queued`, `Pong` (server time), `Roster`, `Kick { code, reason }` |
| Movement and zones | `Move`, `Interact { target }`, `LeaveInstance`, `Mount` (broom), `LookAt` | `Snapshot`, `Correction`, `ZoneChanged`, `Mounted`, `ForcedMove` |
| Looks | | `AppearanceChanged`, `EffectsChanged`, `LevelChanged` |
| Chat and emotes | `Chat { channel, text, client_seq }`, `Emote` | `Chat`, `ChatRejected { client_seq, reason }` |
| Dorms | `DormPlace`, `DormMove`, `DormRemove`, `DormSetOpen`, `DormInvite`, `DormAcceptInvite`, `DormDeclineInvite`, `DormVisit`, `DormKick`, `UseFurniture` | `DormState`, `DormFurniture`, `DormOpenChanged`, `DormInviteReceived`, `DormInviteDeclined`, `DormError` |
| Friends | | `Friend { event }` (request received, added, removed, online, moved, offline, room open) |
| Parties | `PartyInvite { name }`, `PartyRespond`, `PartyLeave`, `PartyKick`, `PartyPromote` | `PartyInvited`, `PartyState { party }`, `PartyNotice`, `PartyError` |
| Clubs | `ClubFound`, `ClubInvite { name }`, `ClubRespond`, `ClubLeave`, `ClubKick`, `ClubSetRole`, `ClubTransfer`, `ClubRename`, `ClubSetMotd`, `ClubDisband` | `ClubInvited`, `ClubState { club }`, `ClubPresence`, `ClubNotice`, `ClubError` |
| Economy | `TradeRequest`, `TradeRespond`, `TradeSetOffer`, `TradeLock`, `TradeConfirm`, `TradeCancel` | `Wallet`, `MailNotice`, `StationOpened`, `FindResult`, `TradeRequested`, `TradeState`, `TradeResult`, `TradeError` |
| Potions | `Drink`, `MimicDraughtAsk`, `MimicDraughtAnswer` | `OwnEffects`, `Drank`, `MimicDraughtRequest`, `MimicDraughtDeclined`, `PotionError` |
| School | `TraceGesture`, `PickAnswer` | `Progress`, `ClassJoined`, `LessonPhaseChanged`, `LessonRound`, `BeatScored`, `ClassResult`, `ClassError`, `NpcEvent` |
| Spells | `CastSpell`, `EndSpell` | `Spellbook`, `SpellCast`, `Levitating`, `Revealed`, `SpellError` |
| Dueling | `DuelChallenge`, `DuelChallengeName`, `DuelRespond`, `DuelCancel`, `DuelCast`, `DuelForfeit`, `DuelListRequest`, `DuelSpectate` | `DuelChallenged`, `DuelChallengeEnded`, `DuelError`, `DuelStart`, `DuelEvents`, `DuelResult`, `DuelList` |
| Secrets | `EnterWishingRoom` | `WishingRoomDoor`, `Discovered` |
| NPCs | `NpcInteract { id }` | `NpcReply { id, reply }` |

Failures answer with a typed error code (`DormError`, `TradeError`,
`SpellError`, ...), never free text; the client maps codes to messages.

### Snapshots

`ServerMsg::Snapshot { tick, entered, full, deltas, left, looks }` updates
the client's visible set V, kept sorted by entity id on both ends. Apply in
order (`protocol::SnapshotReceiver::apply`):

1. `left`: these ids leave V (their baselines are forgotten).
2. `entered`: these entities join V, each as `Full(EntityInfo)` (name,
   house, appearance, visible effects, level, NPC id) or `Cached { id,
   appearance_version, effects_version }` when this connection already
   received that entity at those versions.
3. `full`: absolute states `{ id, pos (cm), yaw (byte), anim }`, each the
   entity's new baseline.
4. `deltas`: `{ index, dx, dy, dz, yaw, anim }` (6 bytes), `index` being the
   entity's position in V after steps 1 and 2; the position moves by
   (dx, dy, dz) cm (2 cm units for a flying entity) from the baseline.

The server sends a full state on enter, when a component would not fit a
delta, and at least every `RESYNC_SNAPSHOTS` (100) snapshots. Unchanged
entities get nothing; far entities may be updated at 5 Hz instead of 10
(the 12 best-ranked are always near), but every changed entity is updated
within `MAX_UPDATE_GAP_SNAPSHOTS` (2). `looks` carries head-turn targets
(`LookAt`). `Welcome` and `ZoneChanged` reset V and every baseline.

**Cached looks** (`protocol::looks`): both ends remember the last full entry
sent per entity on this connection (at most `LOOK_CACHE_MAX`, 1,024 ids;
the record empties on `Welcome`). A cached entry the client cannot resolve
is a desync: the client closes the socket and reconnects from an empty set.

**Client rule**: a missing update does not mean "stopped". An entity stops
when a state with a non-moving anim arrives; interpolation handles uneven
100/200 ms update intervals.

### Appearance

`protocol::Appearance { body_type, height, face: [u8; 5], skin_tone,
hair_style, hair_colour, eye_colour, outfit: { hat, body, legs, shoes } }`,
each worn slot `{ item_def, dyes: [u8; 3], finish }` (finish `none`,
`shimmer` or `colour_shift`). `height` scales the whole character uniformly
between the body's limits; the server's collision capsule is the same for
everyone. Stored as JSON in `characters.appearance`; versioned with
`appearance_version`. Changes reach viewers as `AppearanceChanged`, at most
once per tick per character and only into the spare room of each
recipient's outbound queue, so a burst of wardrobe changes never
disconnects anyone.

### Anims and emotes

`Anim`: `Idle`, `Walk`, `Run`, `Wave`, `Bow`, `Sit`, `Cast`, `Fly`, `Hover`.
`Move.anim` carries only `Idle`, `Walk` or `Run` (on a broom `Fly` or
`Hover`). `Emote { Wave | Bow | Sit }` while standing still sets the anim;
wave and bow end after a few seconds, sit lasts until the character moves.
A move that keeps the position keeps the emote (that is how a seated
character turns round on a bench). `Cast` is set by the server for a wand
gesture.

### Chat

- `Chat { channel, text, client_seq }`; the client shows its own line at
  once and skips the server's echo.
- Channels: say (the speaker's zone, within `SAY_RADIUS_M`, 30 m), house,
  whisper, party and club (across zones: party and club lines reach every
  online member of the sender's party or club, wherever they are). Lines
  from a player the recipient blocked are not delivered.
- A refused line is answered with `ChatRejected { client_seq, reason }` and
  delivered to no one: `RateLimited` (a burst of 5, then one a second,
  shared by every channel), `Filtered` (the word filter), `UnknownTarget`
  (whisper recipient offline or blocking the sender), `Invalid` (empty or
  over 256 characters), `NoChannel` (party chat outside a party, club chat
  outside a club).
- The client shows each channel in its own colour with a label: house lines
  tinted by the house colour and labelled with its name, whispers pink,
  party lines blue (`--party`, a bright azure) labelled "[Party]", club
  lines spring green (`--club`) labelled with the club's tag ("[OWL]").
  `/h`, `/w` (or `/tell`), `/r`, `/p` (or `/party`) and `/c` (or `/club`)
  pick the channel.

### Kicks

`Kick { code, reason }`. Clients retry with backoff only on
`ServerRestart`, `Timeout`, `TooSlow` and `ServerFull`
(`KickCode::is_retryable`); `Replaced`, `Banned`, `ProtocolError`,
`VersionMismatch`, `SessionExpired`, `NoCharacter` and `Flood` are final.
`reason` is display text only.

### Delivery and limits

Each connection has a bounded outbound queue. Notices other players cause
(friend events, invitations, trade and duel requests) go out while the queue
has room above a reserve, otherwise in order from a bounded backlog over the
next ticks, so a flood of requests cannot get anyone disconnected. A client
whose queue overflows is kicked (`TooSlow`). World memory is bounded:
instances close when empty, invitations and memos expire, and per-connection
records are capped.

## HTTP API

All JSON endpoints are under `/api`.

- **Errors**: `{ "error": "<code>", "message": "<text>" }` with a 4xx/5xx
  status. `error` is a stable code the client switches on; `message` is for
  people and may change. Malformed JSON and query strings get the same
  shape (`invalid_body`, `invalid_query`); unknown paths are a JSON 404.
  Every `429` also carries `retry_after` (whole seconds to wait, rounded up)
  in the body and as a `Retry-After` header. The 429 codes: `rate_limited`
  (the per-IP limit on the auth endpoints), `too_many_emails` and
  `too_many_attempts` (per account, below).
- **Authentication**: `Authorization: Bearer <token>`. Tokens are 32 random
  bytes, base64url; only their SHA-256 is stored. Sessions last 30 days.
  Passwords are hashed with Argon2id.
- **Pagination**: every list uses keyset pagination: `?limit=25&after=<cursor>`
  → `{ items: [...], next: <cursor | null> }`; `limit` defaults to 25, at
  most 100; cursors are opaque; a bad one is `400 invalid_cursor`.
- **Ids** are JSON numbers below 2^53. Houses are spelled as `protocol::House`
  serializes them (`"Skytalon"`); slots as `"hat" | "body" | "legs" | "shoes"`.
- **Rate limits** (`rate_limit.rs`, in memory): the unauthenticated auth
  endpoints share a per-IP limiter (see `SHS_TRUST_PROXY` and
  `SHS_CLIENT_IP_HEADER` in [configuration.md](configuration.md) for
  servers behind a proxy); writes are limited per account. Two per-account
  fixed windows guard the auth endpoints: **emails**, 3 per account per
  15 minutes, shared by password-reset links and re-sent confirmation links
  (the email sent on registering does not count), over which nothing is sent
  and the answer is `429 too_many_emails`; and **wrong passwords**, 10 per
  account per 15 minutes, over which sign-in answers `429
  too_many_attempts`, even for the right password, until the window ends (a
  successful sign-in clears the count). A window's count clears completely
  when it ends. The counts are kept in memory, so a restart clears them.
- **Idempotent writes**: purchases, letters, listings and crafts accept a
  `request_id`; a retried request answers the original result.
- **Station-gated endpoints**: crafting, the owl-order shop and Exchange
  listings and purchases need the character to be at the matching station
  in the world (a *station grant*, see [Economy](#economy-and-crafting));
  otherwise `403 not_at_station`.

### Accounts

| Method and path | What |
| --- | --- |
| `POST /api/auth/register` | `{ email, password, confirm_adult: true }` → `201`; emails a verification link. Players attest they are 18 or older; no date of birth is stored. `409 email_taken` if the address has an account |
| `POST /api/auth/verify` | `{ token }` → `200 { token, expires_at }`: verifies the email and signs in. Links are `<SHS_PUBLIC_URL>/?verify=<token>`, single-use, valid 48 hours |
| `GET /api/auth/verify?token=` | `303` to the client's verify page (for older links) |
| `POST /api/auth/login` | `{ email, password }` → `200 { token, expires_at }`; `401 invalid_credentials`; `403 unverified` (right password, unconfirmed account: a fresh link is emailed); `429 too_many_attempts` after 10 wrong passwords in 15 minutes, even for the right password, until the window ends (a successful sign-in clears the count); `429 too_many_emails` when an unconfirmed account's fresh link would go over the email limit (no link is sent; earlier links still work) |
| `POST /api/auth/logout` | Ends the session and the game connection opened with it (only that one) → `204` |
| `POST /api/auth/password-reset` | `{ email }` → emails `<SHS_PUBLIC_URL>/?reset=<token>`, valid one hour, single-use; a new request replaces the previous link. `404 no_account` for an unknown email; `429 too_many_emails` over the email limit (nothing is sent and the previous link stays valid) |
| `POST /api/auth/password-reset/confirm` | `{ token, password }`: sets the password, ends every session of the account and its live game connection (`SessionExpired`), and counts as confirming the email |

Signing out and resetting the password end the game connection at once.
Beyond that, every live game connection re-checks its session every 5
minutes and is kicked with `SessionExpired` once the session has expired or
been deleted, so no connection outlives its session.

Without SMTP configured (`SHS_SMTP_URL` unset) the server does not send
email: it logs each verification and reset link instead, so an operator can
hand them out. With `SHS_DEV=1` the responses also carry the links
(`dev_verify_url`, `dev_reset_url`); never enable that on a public server
([configuration.md](configuration.md)).

### Characters, wardrobe and social

An account has up to 5 characters (`characters::MAX_PER_ACCOUNT`, a
constant, not a setting) and plays one at a time. The session records which
one (`sessions.character_id`, set by selecting or creating a character), and
`/api/characters/me` and every other character endpoint act as that
character; with none selected they answer `404 no_character`. Each
character is sorted into its own house and earns its own daily rewards.
Names are unique across all characters and freed when a character is
deleted.

**Deleting a character** removes its own rows (items, mail, friendships,
listings, progress, its club membership); friends are told. A club it was in
goes on as if it had left ([Clubs](#clubs)): a club it owned passes to the
longest-serving officer, else the longest-serving member, and a club left
empty is disbanded. Its items and Crowns leave through
the ledgers with reason `character_deleted` (an `item_ledger` destroy, a
`crown_ledger` debit to 0); Crowns waiting in its uncollected mail are
ledgered as `mail_collect` and then `character_deleted`, both naming the
mail. Records it shares with other characters stay theirs with the deleted
side empty: a duel in the opponent's history (`GET /api/duels` then gives
`opponent.character_id: null` and the name "A former student"), a listing
someone bought, a trade, letters it sent.

| Method and path | What |
| --- | --- |
| `GET /api/creator`, `GET /api/catalog` | The shipped creator data and palette, the item catalog. Public and cacheable (`ETag`) |
| `GET /api/characters` | The account's characters for the picker, oldest first: `{ characters: [{ id, name, house, level, appearance, appearance_version }], max: 5 }`. Session only (no character needs to be selected) |
| `POST /api/characters` | `{ name, house, appearance? }`: creates a character, grants the starter wardrobe, furniture and purse, and selects it for the session. `409 character_limit` when the account already has 5 |
| `POST /api/characters/{id}/select` | The session plays this character from now on → `200` the character. `404 character_not_found` unless it is the account's. Counts against the account's write budget. If the session has a live game connection playing another character, it is kicked `Replaced` |
| `DELETE /api/characters/{id}` | `{ confirm_name }` (the character's name, case and surrounding spaces ignored) → `204`. Checked in this order: `404 character_not_found` unless it is the account's, `409 character_online` while it is in the world (on any device) or within a duel's disconnect grace, `400 name_mismatch`. Deletes it for good (below); sessions that played it play nothing until they select again |
| `GET /api/characters/me` | The session's selected character, or `404 no_character` ("Choose a character first.") |
| `GET`/`POST /api/characters/me/tour` | The first-day tour's progress |
| `GET /api/inventory` | Items, filterable by `kind`, `slot`, `location` |
| `GET`/`POST /api/equipment` | What is worn; equip `{ slot, item_id \| null }` (checks level and body) |
| `POST /api/items/{id}/dye` | `{ dyes: [p, s, t] }`, by the dye rule |
| `POST /api/items/{id}/finish` | Applies a finish item to a garment |
| `GET`/`POST /api/outfits`, `PATCH`/`DELETE /api/outfits/{id}`, `POST /api/outfits/{id}/wear` | Saved outfits (at most 10) |
| `GET /api/friends`, `DELETE /api/friends/{character_id}` | Friends (with online status and location from the world) |
| `GET`/`POST /api/friends/requests`, `DELETE /api/friends/requests/{id}`, `POST .../accept`, `POST .../decline` | Friend requests |
| `GET`/`POST /api/blocks`, `DELETE /api/blocks/{character_id}` | Blocks (blocking removes friendships and requests both ways) |
| `GET /api/players`, `GET /api/players/{name}/card` | Characters online now (name and house, searchable); a player's card (with `club: { name, tag }` or null) |
| `GET /api/clubs` | The school's register of clubs: `{ clubs: [{ name, tag, members }] }`, the largest first, at most 100 ([Clubs](#clubs)). Club actions themselves go over the game connection |

### Economy and crafting

| Method and path | What |
| --- | --- |
| `GET /api/wallet`, `GET /api/wallet/history` | Crowns and the character's Crown ledger |
| `GET /api/shop`, `POST /api/shop/orders` | The owl-order catalogue; an order (paid at once, delivered by mail). At a stand |
| `GET`/`POST /api/mail`, `GET`/`DELETE /api/mail/{id}`, `POST /api/mail/{id}/collect` | Owl mail: letters with items and Crowns, collection. Mail holding items or Crowns cannot be deleted |
| `GET /api/mail/sent`, `GET /api/mail/sent/{id}` | The Outbox |
| `GET /api/exchange/listings` | Browse the Owl Exchange (category, subcategory, name, level, colour, house, sort) |
| `POST /api/exchange/listings`, `POST /api/exchange/listings/{id}/buy` | List an item (fee paid), buy one (item and proceeds travel by mail). At the counter |
| `GET /api/exchange/mine`, `DELETE /api/exchange/listings/{id}` | The seller's listings; take one down |
| `GET /api/trades` | Completed trades |
| `GET /api/recipes`, `POST /api/recipes/learn`, `POST /api/craft` | Recipes, learning one from a pattern, crafting (at a station) |
| `GET /api/skills` | The three crafting disciplines' XP and levels |
| `GET /api/effects`, `GET /api/finds` | Active potion effects; find spots on cooldown |

### School, map and the House Cup

| Method and path | What |
| --- | --- |
| `GET /api/progression`, `GET /api/xp/history` | Level, XP, year, loadout slots; the XP ledger |
| `GET /api/classes/schedule`, `GET /api/classes/attendance` | The timetable as the server computes it; the character's class record |
| `GET /api/lore`, `GET /api/lore/{id}` | Lore the character has unlocked |
| `GET /api/spells`, `PUT /api/loadout` | Learned spells; the duel loadout |
| `GET /api/duels` | The character's duel history |
| `GET /api/house-points`, `GET /api/house-cup` | House point totals (public); this week's House Cup |
| `GET /api/map` | The places revealed on the character's map and the secrets found |

### Operations and development

| Method and path | What |
| --- | --- |
| `GET /api/health` | `200 { status: "ok" }` when the database answers, `503` otherwise |
| `GET /metrics` | Prometheus text format (`shs_*` metrics: HTTP responses and latency, auth, world players, tick time, instances) |
| `POST /api/dev/bot-session`, `/api/dev/grant`, `/api/dev/teleport`, `/api/dev/clock`, `/api/dev/house-cup/close` | Only with `SHS_DEV=1`: throwaway bot accounts (the bot session selects the bot's character), granting items/Crowns/XP/spells, teleporting, shifting the game clock, closing the House Cup week. For playtests and load tests |

`shs-server audit [NAME ...]` replays the item, Crown and XP ledgers against
current holdings (every character, or the named ones) in one read-only
snapshot, prints any problem and exits 1 if they do not reconcile. It is
safe to run while the game is up.

## Database

Postgres via sqlx. Migrations are in `db/migrations/NNNN_*.sql`, embedded in
the binary (`db::MIGRATOR`) and applied when the server starts. Add a new
numbered file for every change; never edit an applied one. (sqlx refuses
to start when an applied migration's checksum changed: a development
database that ran an earlier draft of a migration, such as
`0024_multiple_characters.sql`, must be recreated.)

Main tables:

| Area | Tables |
| --- | --- |
| Accounts | `accounts` (normalized email, Argon2id hash, adult attestation time, verified time, `is_bot`), `email_verifications`, `sessions` (token hash, expiry, the selected `character_id`: `ON DELETE SET NULL`), `password_resets` (token hashes only) |
| Characters | `characters` (up to 5 per account, indexed by `characters_account_idx`; unique name, house, appearance, resume zone and position, Crowns, XP and level, timestamps) |
| Items | `items` (owner, item definition, dyes, finish, quantity, maker, location: `inventory`, `equipped`, `dorm`, `mail`, `exchange`), `equipment`, `saved_outfits` |
| Social | `friendships` (two rows per friendship), `friend_requests`, `blocks` |
| Dorms | `dorms` (open to friends), `dorm_placements` |
| Ledgers | `item_ledger`, `crown_ledger`, `xp_ledger`: append-only (a trigger refuses `UPDATE` and `DELETE`), no foreign keys |
| Economy | `mail`, `mail_attachments`, `shop_orders`, `exchange_listings`, `trades` |
| Crafting | `skills`, `known_recipes`, `crafts`, `find_cooldowns`, `active_effects` |
| School | `daily_caps`, `known_spells`, `loadouts`, `class_sessions`, `class_attendance`, `lore_unlocks`, `house_points` (append-only), `house_cups`, `duels` |
| Exploration | `map_reveals`, `discoveries` |
| NPCs | `npc_gifts` (one per character, NPC and UTC day) |
| Clubs | `clubs` (unique `name_key` and `tag`, message of the day, `version` bumped by every change), `club_members` (one row per character, the primary key: role `owner`, `officer` or `member`, `joined_at`; at most one owner per club) |

**Integrity rules.**

- Every trade, purchase, listing, letter, collection, craft, find, drink and
  reward is one transaction.
- **Lock order**: every `characters` row whose items, Crowns or XP change
  first (`FOR NO KEY UPDATE`, in id order), then listing rows, then item rows
  by ascending id. Every item change of a character happens under that
  character's lock, so no two economy transactions can deadlock.
- Item rows change only through `server::items` (or with a matching
  `ledger::record_item_move`), Crowns only through `ledger::adjust_crowns`,
  XP only through `progression::grant_xp`, in the same transaction.
  Grants with a reference (`ref_id`) happen at most once.
- A deleted character's own rows go with it (`ON DELETE CASCADE`); the
  rows it shares stay with the other side: `duels.a_id`/`b_id` and
  `exchange_listings.seller_id` are `ON DELETE SET NULL`, and the check
  `exchange_listings_active_seller` lets only a closed listing lose its
  seller (the server deletes active listings itself first). The ledgers
  have no foreign keys and keep its rows.
- A character's balance is the sum of its Crown ledger; units of an item
  definition held are those created and granted minus those consumed and
  destroyed. `ledger::audit::reconcile` checks this (and more), including
  that every Crown row's reason has the right sign (payments, and
  `character_deleted`, only take); every economy test ends with it.

The integration tests run against a real database (see
[Testing](#testing-and-checks)).

## Game systems

### Dorms and decorating

- Each character owns a dorm room (an instance of
  `data/world/instances/dorm_<house>.json`), entered through their house's
  `dorm_door`. The room is alive while its owner is online and anyone is in
  it; it holds the owner and at most 11 guests.
- Guests are friends only: an invitation (`DormInvite`, valid two minutes)
  or a visit while the room is open to friends (`DormSetOpen`). The owner
  can kick a guest; when the owner leaves, guests return to where they were.
- **Furniture placement** (`sim_core::placement::check_placement`, the rule
  the server enforces and the client previews through `SimWorld.canPlace`):
  floor items lie inside a floor area; table items on a table area, the
  floor, or the top or shelf of a placed piece (checked in that piece's own
  axes at any rotation); wall items on a wall area facing its direction.
  At most 150 items per room. A piece something stands on cannot be moved
  or put away (`NotEmpty`). Snapping is client-side (0.25 m grid, 22.5°
  turns; precise mode 5 cm and fine turns); the server checks the quantized
  wire values.
- Placed furniture blocks movement through its catalog `blocker`, and
  chairs, stools, armchairs and beds have seats a sitting character is
  drawn on.

### Parties

Parties (`realtime/party.rs`, `protocol::party`) are groups of up to six
characters (`PARTY_MAX`) of any house. They live in the world's memory
only, so a restart disbands them, and they follow characters, not accounts:
a character keeps its party across a relog, and another character of the
same account is in a party of its own or none.

- **Joining**: any member, or someone in no party, invites an online
  character in no party by name (`PartyInvite`); the invitation is valid two
  minutes (`PARTY_INVITE_TTL_SECS`, as dorm invitations). Accepting
  (`PartyRespond`) joins the inviter's party, or starts one the inviter
  leads. Refused while either has blocked the other (`Blocked` to the
  blocker, `NotOnline` to the blocked, so blocks stay private), when either
  is already in a party (`InParty`) or when the party is full (`Full`).
  Declining tells the inviter.
- **Leaving**: a member leaves with `PartyLeave`; the leader removes
  anyone (`PartyKick`) and hands leadership to an online member
  (`PartyPromote`). When the leader leaves, or goes offline while another
  member is online, the next member in joining order leads (online members
  first). A party left with one member disbands. An offline member stays,
  shown offline, for 30 minutes, then leaves.
- **What members get**: after every change, the whole party
  (`PartyState { party }`, one frame for every online member, sent once per
  command or tick however many changes it made; `None` to someone who is no
  longer in one): each member's name, house, level and, while online, their
  entity, a zone number (members with the same one are together) and their
  place (`PartyPlace`: a castle place name, a dorm room, a Wishing Room, a
  class, a duel arena). `PartyNotice` says what happened, for the chat log.
  Party actions share the action rate limit and go out through the notice
  backlog ([Delivery and limits](#delivery-and-limits)).
- **Seeing each other**: members in the same zone are each other's group
  entities in visibility, ranked ahead of every other priority entity, so
  they are always in the visible set whatever the distance (within the view
  limit, which they count toward). Nobody outside the party is sent any of
  it.
- **Party chat**: `ChatChannel::Party` reaches every online member in any
  zone ([Chat](#chat)).

On the client (`src/game/party.ts`, `src/ui/party.ts`, `party-state.ts`):
the party list at the HUD's top left (every member, ourselves included,
the leader first with a crown, then in joining order; level; members
elsewhere dimmed with their place, offline ones greyed; a click offers
whisper, make leader, remove or leave); invitation toasts; "Invite to party"
on the player card, the players list, a player's right-click menu and the
friends list; the chat commands `/invite` (or `/pinvite`) Name, `/leave` (or
`/pleave`), `/pkick` Name and `/ppromote` Name. Party members' nameplates
are party blue (`src/ui/groups.ts`: a plate shows the first group of
`GROUP_PRECEDENCE` the character shares with the player, so another kind
of group can tint plates without outranking the party). On the map, members
drawn with the player are pinned where they stand; members in a class, a
dorm room or a duel at that place's entrance with a note (the class door,
the Dueling Club, the dormitory door of the player's own house, or for
another house the public ground outside its common room, which is not on
the player's map); a Wishing Room, being secret, is only named in the list.

### Clubs

Clubs (`server::clubs`, `realtime/club.rs`, `protocol::club`) are lasting
groups of up to 100 characters (`members_max`, `data/clubs.json`) of any
house, kept in the database (`clubs`, `club_members`), so they outlive
restarts. Like parties they follow characters, not accounts: each character
is in one club at most, and another character of the same account joins its
own or none. Clubs have no house points and no shared purse.

- **Founding**: only at the desk of Miss Odette Penrose, Secretary of Clubs
  and Societies (`clerk_clubs`, a clerk: [NPCs](#npcs)), against the
  Entrance Hall's east wall opposite the Owl Exchange counter. Talking to
  her opens her desk (client-side lines from `data/clubs.json`): found a
  club, tell me about the clubs (the register, `GET /api/clubs`), goodbye.
  Founding needs level `found_level` (4) and `found_crowns` (150) Crowns
  and no club; she says which is missing, or asks for a name and tag.
  `ClubFound { secretary, name, tag }` is refused unless the sender stands
  within talking reach of her (`TooFar`); the database transaction locks
  the founder's `characters` row, checks the level, the Crowns and that it
  is in no club, creates the club and its owner and takes the fee
  (`crown_ledger` reason `club_found`, `ref_id` the club), all at once.
- **Names**: a name is 3-24 letters (as character names), digits, single
  spaces, apostrophes, hyphens and ampersands, starting and ending with a
  letter or digit; a tag 2-4 letters or digits, kept upper case. Both pass
  the chat word filter and are unique whatever the case (`NameTaken`,
  `TagTaken`); so is a renamed club's.
- **Ranks**: the owner (shown as the president), officers and members. The
  owner renames the club, disbands it, appoints and dismisses officers
  (`ClubSetRole`) and hands the club to another member (`ClubTransfer`,
  becoming an officer). Officers (and the owner) invite and set the pinned
  message of the day (filtered, 200 characters at most); officers remove
  members, the owner anyone else (`ClubKick`). Everyone can chat and leave.
  When the owner leaves, or its character is deleted, the longest-serving
  officer owns the club, else the longest-serving member; the last member
  leaving disbands it. There are no operator tools for clubs.
- **Invitations**: an officer or the owner invites an online character in
  no club by name (`ClubInvite`), valid two minutes
  (`CLUB_INVITE_TTL_SECS`, as party invitations), held in the world's
  memory only. Refused while either has blocked the other (`Blocked` to
  the blocker, `NotOnline` to the blocked), when they are in a club
  (`InClub`) or the club is full (`Full`). Accepting joins in a
  transaction that checks again that the inviter may still invite, the
  joiner is in no club and there is room. Declining tells the inviter.
- **How the world keeps them**: the database decides every change; each
  is one transaction that locks the club's row, checks the rules
  (`clubs::Club`, the same ones the world uses to refuse at once), writes,
  bumps `clubs.version` and returns the whole club. The world keeps a copy
  of each club with a member online (loaded with the character as it
  joins, `Joining::club`), replaces it with every newer version and drops
  it when its last member online leaves. A character deleted over HTTP
  tells the world (`WorldHandle::club_changed`), which reloads the club.
  Club actions share the action rate limit and their messages to others go
  out through the notice backlog ([Delivery and limits](#delivery-and-limits)).
- **What members get**: `ClubState { club }` (name, tag, message of the
  day, and every member with rank, level and whether online; the owner
  first, then officers, then members, each longest-serving first) after
  every change of members, ranks, name, tag or message of the day, and
  after `Welcome`; `None` to someone no longer in one. `ClubPresence` when
  a member comes online, goes offline or changes level. `ClubNotice` says
  what happened, for the chat log. Club chat reaches every online member
  in any zone ([Chat](#chat)).

On the client (`src/game/club.ts`, `src/ui/club.ts`, `club-desk.ts`,
`club-state.ts`): the club window (J, or the flag button on the menu bar):
name and tag, members online, the message of the day (officers and the
owner pin it), inviting by name, the roster (rank, level, online), and per
row what one's rank allows (whisper, make officer, dismiss as officer, make
president, remove; leave on one's own row); the owner renames and disbands
the club. Handing the club over, disbanding and the owner's leaving ask to
confirm. Invitation toasts; "Invite to club" (officers and the owner) on
the player card, the players list, a player's right-click menu and the
friends list; the chat commands `/cinvite Name` and `/cleave`. The player
card shows a character's club and tag. Club members' nameplates are club
green (`--club`); someone in the player's party and club shows party blue
(`GROUP_PRECEDENCE` in `src/ui/groups.ts`: the party first).

### Economy and crafting

- **Crowns** are whole numbers. New characters get a starting purse.
- **Stations**: `Interact` with a crafting station, cauldron, owl-order stand
  or the Owl Exchange counter in reach (or a cauldron placed in the dorm)
  answers `StationOpened` and records a station grant (900 s, held only
  while the character stays within reach). The HTTP side checks it
  (`realtime::stations::require`), so the Exchange or a crafting panel only
  works at the station.
- **Owl-order shop**: basic items at catalogue prices, delivered by mail.
- **Owl mail**: letters with up to six attachments and Crowns; postage per
  letter; collected mail is cleaned up after the retention period, mail
  with uncollected items or Crowns never.
- **Owl Exchange**: listings with a fee and a sales tax, five days by
  default; the item and proceeds travel by mail; expired listings return by
  mail.
- **Trades**: a realtime window between two players within a few metres:
  request, offers, lock, confirm. Any change bumps a revision and clears
  both confirmations; the commit is one transaction keyed by a random trade
  key so it never applies twice.
- **Crafting**: three disciplines (charms, transfiguration, potions) with
  skill levels, recipes learned from pattern items, restyles within a
  clothing family, dyeable outputs.
- **Finds**: hidden find spots, each searchable once per real day per
  character, roll a loot table.
- **Potions** (`protocol::effects`, numbers in `data/economy.json`): glow,
  colour change, shrinking and swelling, babbling chat, a friend's disguise
  (with their consent), luck, speed, a hidden nameplate, featherlight
  (faster brooms). One effect per kind; effects survive a relog and expire
  on their own. Scale is cosmetic; speed changes the move check (and the
  client's prediction) at once.

### Classes and progression

- The **game clock** is computed from UTC on every end; `Welcome` and
  `Pong` carry the server's time so the client can correct its own. Lighting,
  the HUD's timetable and class countdowns all come from it.
- **Classes**: a student `Interact`s with a class door during the join
  window and enters a section (an instance; more sections open as they
  fill). The professor runs the lesson script; practice rounds send beats
  that the student traces on the gesture pad (`TraceGesture`, scored by the
  shared `sim_core::gesture` scorer, one attempt per beat, repeated traces
  refused) or answers by number key (`PickAnswer`). At the end each
  participant gets a grade and rewards (XP, subject skill XP, house points,
  and by class Crowns, items, recipes, spells, lore, hints or a fortune).
- **Progression**: levels 1-20 from XP, school years every three levels,
  per-source daily XP caps, more loadout slots at higher levels. An item's
  `required_level` is checked when it is equipped.
- **House points and the House Cup**: classes, duels, finds, crafts, NPC
  gifts and secrets award house points under daily caps; the House Cup is
  the weekly sum per house, closed on the configured weekday with a reward
  by mail for contributors of the winning house.

### Spells and dueling

- **Spells outside duels** (`CastSpell`, `EndSpell`): wand light, levitating
  a dorm piece, flourishes, colour changes, summoning, revealing hidden
  things, conjured birds, a guardian animal, and more, each with its
  cooldown, range and target rules from `data/spells.json`. Duel arenas
  refuse them.
- **Dueling** (`sim_core::duel`, fully server-side): two players in the
  Dueling Club's area challenge each other; the duel runs in an arena
  instance. Each duelist has Poise 100; the first at 0 loses, or the higher
  Poise after the time limit (equal is a draw). Casts are telegraphed and
  take time; attacks fly at where the opponent stood and can be sidestepped;
  shields block and, early, reflect; some spells disarm, bind, slow, pull or
  cleanse. Clients get only the events of each eventful tick
  (`DuelEvents`) and fly projectiles from their launch parameters.
  Spectators join from the club. A disconnected duelist has a short grace
  period to return. Results are recorded in one transaction, with rewards
  under daily and same-opponent limits.

### Flying

Brooms are a second movement mode (`Mount`, `sim_core::flight`): speed
tiers by Flying level (`data/flying.json`), boosted by the featherlight
effect; only inside `airspace` areas (the grounds, the pitch and the lake,
never inside the castle), up to a ceiling. Mounted moves carry `Fly` or
`Hover` and are checked with `check_flight`. Doors and stations refuse
riders.

### Moving staircases

Three flights of the Grand Staircase swing between two positions on a
schedule (`data/world/stairs.json`, `sim_core::stairs`): every `period_s`
seconds, with a warning and a swing beforehand. The schedule is a pure
function of the server clock, so no message is sent: every client computes
it. Riders are carried with the flight; landing gates close while a flight
is away; moves within a grace window of a switch are accepted against
either position. A development server can shorten the period
(`SHS_STAIRS_PERIOD_S`, with `SHS_DEV=1`; the client's `?stairsPeriod=`).

### Secrets and the map

- **Hidden passages** (`data/world/secrets.json`, `sim_core::secrets`):
  five shortcuts with walk-through entrances. Standing on one the first time
  discovers it (`Discovered`): XP, a house point and for two of them a
  recipe.
- **The Wishing Room**: pacing between two marks in a corridor a few times
  within a minute opens a door (`WishingRoomDoor`); inside, the opener picks
  one of two rooms (instances on the dorm code) for up to 20 people.
- **The map** (`GET /api/map`): places are revealed as the character stands
  in them and saved on the server; the map shows the character, friends by
  place, key places, class doors with their next lesson, and found passages.
- **The wayfinding trail** (client): a glowing trail to a chosen class or
  key place, planned on the walkable layout (`sim_core::nav`), re-planned
  when a staircase moves; it never uses secret passages.

## NPCs

`data/npcs/*.json`, checked by `sim_core::npcs` (`NpcData::from_json`, and
against the castle by `validate_castle` at server start and in tests).
Every NPC is the project's own invention; names and personalities are data.

| Kind | Where it runs | What it does |
| --- | --- | --- |
| Professors | Server, inside class sections | Run the lesson script (from the class files) |
| Talking portraits | Client only (never an entity) | Hang at chosen places, breathe and blink, turn to look, say short local lines and hints; the "Portrait chatter" setting quiets them |
| House ghosts | Server entities | Drift through their common room and nearby corridors; talk; a daily gift to their own house |
| Resident staff | Server entities | Routines on the game clock; talk; a daily gift |
| Shopkeepers | Server entities | Stand at the shops; interacting opens the shop UI |
| Clerks | Server entities | Stand at a desk (one routine step); talking opens their feature's own dialogue (the clubs secretary: [Clubs](#clubs)) |
| Background students | Server entities, capped, lowest visibility priority | Walk to classes by the timetable, idle, go to bed at night; short lines |

Files: `roster.json` (who they are and the rules' numbers), `looks.json`
(each NPC's `Appearance`), `portraits.json` (where portraits hang),
`routines.json` (routine points and timed steps; students' routines derive
from the timetable), `lines.json` (what they say and when), `gifts.json`
(daily gift tables).

- Entity NPCs are ordinary castle-zone entities in snapshots
  (`EntityInfo.npc` = roster id); their pose is a pure function of the
  server clock (`RoutinePlan::pose`), walked over NPC navigation graphs that
  skip moving flights and secret passages.
- **Lines are client-side** (`NpcBook.pick`, seeded): words earn nothing, so
  no message carries them and nobody else sees them.
- **Gifts are server-side**: `NpcInteract` within reach answers `NpcReply`
  (`Talk`, `Gift`, or `Refused`); a gift is claimed once per character per
  NPC per UTC day in one transaction (`npc_gifts`).

## Client

`client/` is a Vite + TypeScript app using Babylon.js. It supports **Google
Chrome only** (`client/src/browser.ts`): other browsers, including every iOS
browser, draw parts of it wrong, so they see a card asking for Chrome.
`?browser=any` skips the check (for testing and automation); the Vite dev
server never checks.

### Structure

| Path | What |
| --- | --- |
| `src/main.ts`, `boot.ts`, `ui/screens.ts`, `ui/auth-flow.ts` | Screen flow: sign in, register, verify, password reset, the character picker, character creation ([below](#screen-flow)). A `429` shows the minutes left from `retry_after`; signing in to an unconfirmed account over the email limit shows the "check your email" card with the wait time instead of a new link. The game is a separate chunk loaded on first entry |
| `src/api.ts` | HTTP client for `/api` |
| `src/game/game.ts`, `scene.ts`, `net.ts` | The running game: scene setup, the WebSocket and decoder, the main loop |
| `src/game/player.ts`, `input.ts`, `camera.ts`, `interp.ts`, `remotes.ts` | Own character prediction, input, follow camera, interpolation of others |
| `src/game/zones*.ts`, `world.ts`, `cells.ts`, `cellplan.ts` | Zones, the layout, streaming castle cells by distance |
| `src/game/castle/` | Castle kit materials, KTX2 textures, cell visibility, LOD, seats, terrain, water, foliage, moving staircases, HLOD |
| `src/game/characters/` | Assembling dressed characters into one skinned mesh, animation, LOD governor, faces, seated poses, head turns |
| `src/game/toon/` | The toon shading, lighting by hour, sky, torches, screen passes, outlines |
| `src/game/classes/`, `dueling/`, `spells/`, `effects/`, `flight/`, `dorm/`, `npc/`, `wayfinding/`, `clock/`, `fire/`, `vfx/`, `guardian/` | Feature code by system |
| `src/ui/` | HUD, windows and panels (chat, wardrobe, mail, Exchange, crafting, timetable, map, players, party list, club window and the clubs secretary's desk, settings, ...) |
| `src/economy/` | Shared item and money helpers, icons, the economy panels' state |
| `src/audio/` | Sound and music ([Sound and music](#sound-and-music)) |
| `src/content/` | Content helpers and house definitions |
| `src/settings.ts` | Settings (quality, audio, keys, chat size, portrait chatter), stored in `localStorage` (`shs.settings`) |
| `src/assets.ts` | Asset URLs (below) |
| `src/wasm/` | Generated sim-wasm bindings (not committed) |

### Screen flow

After signing in, the client asks `GET /api/characters`: no characters
opens the creator, otherwise the picker. In the picker the player selects a
character (`POST /api/characters/{id}/select`) and enters the game; **Delete**
opens a card where they type the character's name to confirm; **+ Make a new
character** opens the creator, which has a "Back to your characters" button.
After creating, the client returns to the picker with the new character
preselected. `localStorage` `shs.lastCharacter` holds the id of the last
character played, which the picker preselects (else the first). A reconnect whose session
now plays another character, or none, goes back to the picker.

UI state that can be tested without a browser lives in pure `*-state.ts`
modules with their own `*.test.ts` (vitest).

### Rendering

The anime toon look is done at run time by the client (`src/game/toon/`;
art rules in [style-guide.md](art/style-guide.md)):

- `material.ts`: a StandardMaterial plugin replacing how lights add up (a
  banded ramp with tinted shadows, hemisphere fill, rim, crisp highlights)
  inside the material's own light loop, so it costs no extra draw calls.
  Characters pick a ramp row per vertex (the region stored in the vertex
  colour alpha); world materials one row by name; both read a baked shade.
- `convert.ts`: glTF PBR materials of castle cells, rooms and furniture
  become toon materials on load. World vertex colours are a greyscale baked
  shade; colour lives in textures and `toon_palette.json`.
- `lighting.ts`, `sky.ts`, `torches.ts`: the light by game hour from
  `data/art/lighting.json`, the sky dome, a fixed pool of torch lights.
- `post.ts`: a quarter-resolution bloom, 4x MSAA scene target; on High also
  screen-space AO. `outline.ts`: inverted-hull character outlines (Medium
  and High).
- **Quality levels** (`settings.ts` `qualityProfile`): Low draws straight to
  the canvas at reduced resolution without outlines, bloom, AO or shadows;
  Medium adds outlines, bloom and native resolution; High adds the sun's
  shadow map on outdoor materials and AO. A dynamic-resolution guard steps
  Medium and High down when frames run slow. Low only switches things off;
  it has no assets of its own. A character LOD governor shares a triangle
  budget per quality level between the world in view and the characters.
- **Faces**: each body has a face texture and mask sampled on the face
  region, tinted by skin, eye and hair colours; on Medium and High a
  face-shadow map follows the key light. Still one draw per character.
- **Dyes**: vertex colour `COLOR_0` on furniture and characters is a tint
  mask, `albedo = base · ((1 − r − g − b) + r·C0 + g·C1 + b·C2)`, with
  C the three dye colours (characters: skin and hair on bodies and hair).
  Furniture dye variants are drawn with thin instances; a dressed character
  is merged into one skinned mesh (one draw per LOD).
- **Fire** is a stylized animated flame drawn by the client from flame lists
  the art tools export with each burning piece (`src/game/fire/`); there are
  no modelled flames.

Debug helpers in the URL: `?layout` (draw the walkable rectangles),
`?probe` (exposes `window.shsProbe` for measurement scripts), `?sees=off`
(no cell hiding), `?ktx2=off` (PNG textures). The F3 key shows a
performance overlay.

### Asset URLs

Every file under `public/assets/` and `public/audio/` is loaded through
`assetUrl("<logical path>")` (`src/assets.ts`), never a hand-built URL. A
production build compiles an asset map (`client/scripts/asset-map.mjs`: path
→ content hash) into the game and writes `dist/asset-map.json`. When the
server names an asset bucket (`SHS_ASSETS_CDN_URL`, injected as
`<meta name="shs-assets-cdn">`), files load from the bucket by their
content-addressed keys; otherwise from the server with `?v=<hash>`, cached
immutably. Code that probes for optional files asks `assetShipped(path)`
first. Fonts are self-hosted (`@fontsource` packages, licences in
`client/public/fonts/`) and the KTX2 decoder is served from the page's own
origin: the client contacts no host but its own and the asset bucket.

### Credits

`data/credits.json` is the single source of the art, model, animation, font
and tool credits; sound rows come from `client/public/audio/CREDITS.md` and
the code libraries from `client/package.json` and the Rust crates compiled
into the WebAssembly. `client/scripts/credits.mjs` turns them into:

- the module `virtual:shs-credits`, compiled into the bundle by
  `creditsPlugin` (`client/vite.config.ts`) and drawn by the Credits panel
  (`src/ui/credits.ts`). The panel opens from the sign-in screens and from
  Settings, and is loaded lazily, so the credits stay out of the entry chunk;
- `client/dist/THIRD-PARTY-NOTICES.txt`, the licence texts of every library
  in the shipped client, emitted on `vite build` and served at
  `/THIRD-PARTY-NOTICES.txt` by the dev server;
- `public/assets/CREDITS.md`, `LICENSE-CC-BY-SA-4.0.txt` (characters and hair)
  and `LICENSE-MIT.txt` (everything else), written beside
  the built assets after every asset build or fetch and packed into every
  asset release (not build inputs, so the fingerprint ignores them).

`client/scripts/credits.test.mjs` checks that `data/credits.json` agrees with
`art/CREDITS.md`.

## Sound and music

Plain WebAudio (`client/src/audio/`); Babylon's audio engine stays off.

- **Engine** (`engine.ts`): one AudioContext created at the first user
  gesture (nothing plays before it); buses `master` > `music`, `effects`,
  `ambience`, `interface`, each with its volume setting and a mute; a shared
  synthesized reverb per kind of space (none on Low); world sounds are 3D
  (equal-power panning, inverse distance, a hearing distance beyond which a
  sound is not started); per-quality voice caps where the player's own
  sounds are never stolen; the context suspends while the tab is hidden.
- **What is synthesized**: interface sounds, magic, class beats, duels,
  brooms, owls, potions, bells and the like are WebAudio recipes
  (`sfx/defs.ts`) rendered once into buffers.
- **What is recorded**: footsteps per surface, doors, coins, cloth, paper
  and nature ambience are vetted **CC0** recordings, committed as MP3 under
  `client/public/audio/sfx/`, loaded in banks by area. Every file is
  recorded in `data/audio/sources.json` (source, author, licence, hash) and
  listed in [client/public/audio/CREDITS.md](../client/public/audio/CREDITS.md);
  `sources.test.ts` enforces CC0, complete records and the size budget.
  [tools/audio/README.md](../tools/audio/README.md) covers fetching and
  converting them.
- **Music** is the project's own: written scores (`music/scores/`) played
  live by the music engine, chosen by place (`places.ts`) with day and night
  variants, crossfades and rests between plays. Nothing is downloaded for
  it. An originality check (`music/motifs.ts`, run by the music tests)
  compares every score's melody against the openings of famous film and
  game themes, stored only as interval patterns (`FAMOUS`; no melody is
  transcribed): `imitates()` fails a score that follows one exactly for a
  run of six notes in any key and rhythm, or imitates one closely.
- **Places** (`places.ts`) map every castle cell and instance layout to an
  area with its ambience, music and reverb space; a test fails when a new
  cell is not placed.

## Assets

Everything 3D the client loads is generated by headless Blender scripts in
`tools/blender/` from the layouts and data files, and lands in
`client/public/assets/` (never committed):

| Output | Built by |
| --- | --- |
| `world/` (castle cells, manifest, instances, textures) | `greybox.py` with the castle kit (`castle_kit/`), terrain and foliage (`world/`) |
| `furniture/` | `world/build_furniture.py` (every catalog furniture model) |
| `instances/` | Dorm rooms and school rooms |
| `characters/`, `gear/`, `hair/` | `characters/` (bodies via MPFB, rig, animations, parametric clothing and hair) |
| `icons/` | `icons/` (a 128 px icon per catalog item) |
| `vfx/` | `vfx/` (spell effects and the wand) |
| `ktx2/` | The self-hosted KTX2 decoder (from `@babylonjs/ktx2decoder`, no Blender) |

A content hash of every build input (the **asset fingerprint**,
`client/scripts/assets.mjs`) names what a checkout needs.
`client/scripts/prepare.mjs` compares it with the one recorded in
`client/public/assets/`; when they differ it downloads the published asset
release for that fingerprint (from the GitHub repository named in
`package.json`, or `SHS_ASSETS_URL`), else builds the missing steps with the
pinned Blender, else warns and the client draws greybox stand-ins.
`SHS_ASSETS=fetch|build` forces one way. Maintainers publish releases with
`npm run assets:publish`; CI checks that the release for a commit's
fingerprint exists.

Details: [tools/blender/README.md](../tools/blender/README.md). Art
direction and specs: [docs/art/](art/) ([style guide](art/style-guide.md),
[castle kit](art/castle-kit.md), [character look](art/character-look.md),
[base body](art/base-body-spec.md), [garment shapes](art/garment-shapes.md),
[character adoption](art/character-adoption.md), [repository
layout](art/repo-layout.md)). Credits: [art/CREDITS.md](../art/CREDITS.md).

## Testing and checks

`scripts/check.sh` runs what CI runs:

1. `node scripts/check-committed-files.mjs --all` and its tests (no
   screenshots, renders, `.blend` files or build outputs in the tree).
2. `cargo fmt --all --check`, `cargo clippy --workspace --all-targets -- -D warnings`.
3. `cargo test --workspace`.
4. `cargo build -p sim-wasm --target wasm32-unknown-unknown --release`.
5. The client's `typecheck`, `test` (vitest) and `build`.

Notes:

- **Server integration tests** (`crates/server/tests/`) run against a real
  Postgres: `DATABASE_URL`, default `postgres://localhost/shs_test`
  (`createdb shs_test`). Tests share the database and use unique names
  instead of truncating tables. `tests/migrations.rs` checks the migrations;
  `tests/economy_stress.rs` races economy actions and reconciles the ledgers
  (`SHS_STRESS_SEED`, `SHS_STRESS_ROUNDS`); `tests/exchange_plans.rs`
  (ignored by default) EXPLAINs the Exchange's queries on a large data set.
- **sim-core** tests load every shipped data file and validate it.
- **sim-wasm**: `scripts/build-wasm.sh --test` runs its tests under Node.
- **Blender tools**: `blender -b --factory-startup --python
  tools/blender/tests/run_tests.py` (needs the pinned Blender); some
  checks run with plain `python3 -I` ([tools/blender/README.md](../tools/blender/README.md)).
- **Load tests**: `tools/loadtest` drives up to thousands of bot clients
  against a dev server ([README](../tools/loadtest/README.md)).
- **Performance**: the client's `?probe` hook and the camera paths in
  `tools/loadtest/perf_paths.json` measure frame times, draw calls and
  triangles in view per quality level.
