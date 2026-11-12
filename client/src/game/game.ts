// The running game: wires the world scene, cell streaming, the local player,
// other players, the network connection and the HUD together, and runs the
// frame loop. Loaded as its own chunk (see main.ts).

import { SceneInstrumentation } from "@babylonjs/core/Instrumentation/sceneInstrumentation";
import * as api from "../api";
import { stillPlaying } from "../boot";
import { audio, CLOCK_BELL, emittersIn, footSurface, HEAR, placeOf, STRIDE_M, Strides, VOICE_CAPS, type AudioPlace, type LoopHandle, type PlacedEmitter, type Vec3 } from "../audio";
import { houseInfo } from "../content/houses";
import { Economy } from "../economy/economy";
import { loadSettings, saveSettings, type Settings } from "../settings";
import { ChatPanel, kickNotice, quoteName } from "../ui/chat";
import { FriendsWindow } from "../ui/friends";
import { FriendsState } from "../ui/friends-state";
import { Hud } from "../ui/hud";
import { Nameplates, type PlateTarget } from "../ui/nameplates";
import { levelSightTest, type SightLevel } from "./plate-sight-ray";
import type { AbstractMesh } from "@babylonjs/core/Meshes/abstractMesh";
import { PerfOverlay, jsHeapBytes } from "../ui/perf";
import { CraftingHub } from "../ui/crafting-hub";
import { WindowStack } from "../ui/windows";
import { PlayersWindow } from "../ui/players";
import { PlayerCardWindow } from "../ui/player-card";
import { plateGroup, type GroupMembership } from "../ui/groups";
import { PartyHub } from "./party";
import { ClubHub } from "./club";
import { CLUB_RULES } from "../ui/club-state";
import npcRoster from "../../../data/npcs/roster.json";
import { sameName, type PlayerAction } from "../ui/players-state";
import { tabIsOurs } from "../ui/selection-state";
import { WorldSelection, type SelectCandidate } from "./selection";
import { LookDirector, NPC_ANSWER_MS, type Drawn } from "./look-at";
import { encodeChat, encodeLookAt, encodeMount, encodeMove, encodeSetViewLimit, moveParams, type FlyParams, type House, type ServerMessage } from "../wasm/sim_wasm";
import { FollowCamera, TARGET_HEIGHT, type FloorAt } from "./camera";
import { Vector3 } from "@babylonjs/core/Maths/math.vector";
import { FlightChip } from "../ui/flight";
import { MoveChip } from "../ui/move-chip";
import { BroomFlight, flightChipText, KEY_TURN_RATE, mountRefusedText, MountState, yawOfLook, type FlightInput } from "./flight/flight";
import { Brooms, HIPS_FALLBACK, SEAT_BELOW_HIPS } from "./flight/brooms";
import { bodyType } from "../content/catalog";

/** The default height of body type 0 (m), which `TARGET_HEIGHT` is tuned for. */
const DEFAULT_HEIGHT_M = bodyType(0).default_height_m;
import { EffectsController, type FxSubject } from "./effects/controller";
// Spells L20 (GUARDIAN): Custodio's silver companions.
import { GuardianHub } from "./guardian/hub";
import { parseManifest, type CellInfo, type Manifest } from "./cellplan";
import { DormController } from "./dorm/controller";
import { DuelingController } from "./dueling/controller";
import { SchoolHub } from "./classes/school";
import { ZoneDirector } from "./zones-director";
import { castleTorches } from "./zones";
import { Cast, type SelfPose } from "./characters/cast";
import { autoRunStopFor, cameraStep, isTyping, Keyboard, StallWatch, type MoveInput } from "./input";
import { Connection, gameSocketUrl, type ConnectionStatus } from "./net";
import { LocalPlayer, MoveSender } from "./player";
import { RemotePlayers, type Remote } from "./remotes";
import { WorldScene } from "./scene";
import { loadWorldData, type WorldData } from "./world";
import { CastleLibrary, skyEmission } from "./castle/materials";
import type { FoliageKitInfo } from "./castle/foliage";
import { KitLods } from "./castle/lod";
import { KitCandles } from "./castle/candles";
import { KitCup } from "./castle/cup";
import { LakeWater } from "./castle/water";
import { parseHlod } from "./castle/hlod";
import { HouseCupHub } from "./house-cup";
import { devStairsPeriod, GRAND_STAIRCASE_CELLS, Staircases } from "./castle/stairs";
import { KitOwls } from "./castle/owls";
import { KitEntrances } from "./castle/entrances";
import { SecretsHub } from "./secrets";
import { NpcHub } from "./npc/hub";
import { paletteRgb } from "./castle/terrain";
import { isOwlMail } from "./zones";
// The moving staircases' schedule, the same file the server checks moves with.
import stairsText from "../../../data/world/stairs.json?raw";
import { Seats, wrapAngle } from "./castle/seats";
import { BenchTap } from "./bench-flip";
import { inputDirection } from "./motion";
import { cameraMaxFor, type CellCamera } from "./castle/camera-max";
import { ssaoState } from "./castle/shade";
import type { LayoutOverlay } from "./castle/layout-overlay";
import { castleLight, sceneLights } from "./toon/lighting";
import type { HemisphericLight } from "@babylonjs/core/Lights/hemisphericLight";
import type { Quality } from "../settings";
import type { LayoutSurface } from "./world";
import { assetUrl } from "../assets";

export interface GameOptions {
  character: api.Character;
  /** Shows a loading or waiting message on the full-screen card. */
  onStatus: (heading: string, body: string) => void;
  /** The world is loaded around the player: hide the card. */
  onReady: () => void;
  /** The session is over (kicked, or the world could not load). */
  onFatal: (heading: string, body: string) => void;
  onSignOut: () => void;
}

export interface RunningGame {
  dispose(): void;
}

/** Logical folders of the game files (src/assets.ts turns a path under them into its URL). */
const ASSETS_BASE = "assets/";
const WORLD_BASE = `${ASSETS_BASE}world/`;
/** Cells within this distance of the player are loaded, beyond `unload` disposed (m). */
const STREAM_RADII = { load: 70, unload: 100 };
/** Low streams closer. */
const STREAM_RADII_LOW = { load: 50, unload: 80 };
const radiiFor = (q: Quality) => (q === "low" ? STREAM_RADII_LOW : STREAM_RADII);
/** The castle's sky fill at noon, against which windows scale their glow (castle/materials.ts `skyEmission`). */
const NOON_FILL = sceneLights(castleLight(13));
const luma = (r: number, g: number, b: number) => 0.3 * r + 0.59 * g + 0.11 * b;
const HUD_INTERVAL = 0.25;
/** A move this long (m) between two place updates (HUD_INTERVAL apart) is a jump, not a walk. */
const PLACE_JUMP = 8;
const NO_INPUT: MoveInput = { forward: 0, strafe: 0, run: false };
const NO_FLIGHT_INPUT: FlightInput = { throttle: 0, turn: 0, climb: 0 };
const byDistance = (a: { d: number }, b: { d: number }): number => a.d - b.d;
const NO_WALLS: readonly AbstractMesh[] = [];
/** Sound: a remote within this of us whose footsteps may be heard (m; the nearest few, VOICE_CAPS). */
const FOOTSTEPS_M: number = HEAR.footstepsM;
/** The staircase's dust (a palette stone colour). */
const DUST_RGB = paletteRgb(["statue_stone", "parcel_tan"]) ?? ([0.6, 0.58, 0.55] as const);

export async function runGame(opts: GameOptions): Promise<RunningGame> {
  const token = api.sessionToken();
  if (!token) throw new api.ApiError(401, "unauthorized", "Please sign in.");

  const [world, manifest] = await Promise.all([
    // Predict with the castle as this house walks it: rival common rooms are walls.
    loadWorldData(houseInfo(opts.character.house).id as House),
    fetch(assetUrl(`${WORLD_BASE}manifest.json`)).then(async (r) => {
      if (!r.ok) throw new Error(`The castle's map is missing (manifest.json: ${r.status}). Generate it with tools/blender/greybox.py.`);
      return parseManifest(await r.json());
    }),
  ]);
  return new Game(opts, token, world, manifest);
}

class Game implements RunningGame {
  private readonly settings: Settings = loadSettings();
  private readonly canvas = document.getElementById("world") as HTMLCanvasElement;
  private readonly hudRoot = document.getElementById("hud") as HTMLElement;
  private readonly view: WorldScene;
  private readonly camera: FollowCamera;
  /** How characters look and act: the skinned characters, emotes and the wardrobe. */
  private readonly characters: Cast;
  /** Zone changes, scenes (castle cells, dorm rooms) and the door prompt. */
  private readonly zones: ZoneDirector;
  private readonly dorm: DormController;
  private readonly friendState = new FriendsState();
  private readonly friends: FriendsWindow;
  /** Purse, owl mail, the catalogue, the Owl Exchange and trading. */
  private readonly economy: Economy;
  /** Potion effects on characters (and find-spot shimmer). */
  private readonly effects: EffectsController;
  /** Spells L20 (GUARDIAN): every drawn character's guardian companion (the effects book's `guardian`). */
  private readonly guardian: GuardianHub;
  /** Crafting stations, potions, skills and finds. */
  private readonly crafting: CraftingHub;
  /** The game clock and timetable, levels, classes, NPCs and spells outside duels. */
  private readonly school: SchoolHub;
  /** The Dueling Club, challenges, the loadout, spectating and the duel itself. */
  private readonly dueling: DuelingController;
  /** Our party: its list at the top left, invitations, /invite and /leave, party nameplates and map pins. */
  private readonly party: PartyHub;
  /** Our club: its window, invitations, /c, /cinvite and /cleave, club nameplates, the secretary's desk. */
  private readonly club: ClubHub;
  /** The groups whose members wear a tinted nameplate (ui/groups.ts), in no particular order. */
  private readonly groups: GroupMembership[];
  /** Characters drawn this frame, nearest first, for the effects (pooled). */
  private readonly fxSubjects: FxSubject[] = [];
  private readonly fxPool: (FxSubject & { d: number })[] = [];
  private readonly keyboard = new Keyboard();
  /** Seated on a two-sided bench, a tap of the way behind us turns us round; a hold stands us up (bench-flip.ts). */
  private readonly benchTap = new BenchTap();
  /** Replaced on each zone change: it predicts with that zone's world. */
  private player: LocalPlayer;
  private readonly sender: MoveSender;
  private readonly conn: Connection;
  private readonly hud: Hud;
  private readonly chat: ChatPanel;
  private readonly perf = new PerfOverlay();
  private readonly plates: Nameplates;
  private instrumentation: SceneInstrumentation | null = null;
  /** The castle kit's shared materials (one copy of each texture for all cells). */
  private readonly library: CastleLibrary;
  private readonly lods: KitLods;
  /** The floating candles (castle kit M3): bobbed once a frame. */
  private readonly candles = new KitCandles();
  /** The castle's seats for the sit emote (castle kit M3; visual only). */
  private readonly castleSeats: Seats;
  /** The common rooms' rugs and hearth steps for sitting on the floor (visual only). */
  private readonly castleFloorSeats: Seats;
  /** The Great Hall's hourglass sand and cup banners. */
  private readonly cupKit = new KitCup();
  /** The lake's toon water (its bands move once a frame). */
  private readonly water: LakeWater;
  /** The House Cup: standings, the panel, the notice boards, the nameplate badge. */
  private readonly cup: HouseCupHub;
  /** The moving staircases (the swing nodes, the gates, carrying riders) and the Owl Loft's owls. */
  private readonly stairs: Staircases;
  private readonly owls: KitOwls;
  /** The map, the hidden passages (their entrances, discoveries), and the Wishing Room's door. */
  private readonly secrets: SecretsHub;
  private readonly entrances: KitEntrances;
  /** NPCs in the open castle: talking portraits, ghosts, staff, shopkeepers, students (game/npc). */
  private readonly residents: NpcHub;
  /** Our feet for the residents while we walk the castle (reused). */
  private readonly residentFeet: [number, number, number] = [0, 0, 0];
  /** The surface we stood on at the last place update (the map's "you"). */
  private placeSurface: string | undefined = undefined;
  /** Everyone's feet drawn this frame in the castle (the entrances), with their house (the house doors), pooled. */
  private readonly feetList: [number, number, number, string][] = [];
  private readonly feetPool: [number, number, number, string][] = [];
  /** Manifest entries by cell id (camera.max and the other kit fields). */
  private readonly cellInfo = new Map<string, CellInfo & CellCamera>();
  /** Surface id → cell id of the zone shown, for the camera's per-cell zoom. */
  private cellOfSurface = new Map<string, string>();
  private lastCell: string | null = null;
  // Where updatePlace last saw the player, to tell a jump (teleport, respawn) from a walk.
  private placeX = NaN;
  private placeZ = NaN;
  /** The furthest zoom last given to the camera (m). */
  private shownCameraMax = -1;
  /** `?layout`: the walkable rectangles drawn over the scene (null without the flag). */
  private layoutOverlay: LayoutOverlay | null = null;
  /** Our house (lower-case id): the house doors open for it. */
  private readonly myHouse: string;
  /** The HUD's windows stack (the last opened on top; Esc closes the top one). */
  private readonly windows: WindowStack;
  /** The players list (U) and a player's card. */
  private readonly players: PlayersWindow;
  private readonly card: PlayerCardWindow;
  /** The character selected in the world (a ring; Tab, E, Esc). */
  private readonly selection: WorldSelection;
  /** Characters look at their target: whom each drawn character looks at (look-at.ts), and who is drawn this frame. */
  private readonly lookAt: LookDirector;
  private lookDrawn: readonly Drawn[] = [];
  private readonly aimAt = { x: 0, y: 0, z: 0 };

  private remotes: RemotePlayers | null = null;
  private selfId = -1;
  /** `clientSeq` of the last chat line sent (named by `chatRejected`). */
  private chatSeq = 0;
  private ready = false;
  private disposed = false;
  private readonly visible: Remote[] = [];
  private readonly plateTargets: PlateTarget[] = [];
  /** What hides a nameplate (plate-sight-ray.ts): the camera's walls, and the castle cells not shown. */
  private readonly sightLevel: SightLevel = {
    walls: () => (this.zones.ready ? this.zones.scenes.walls : NO_WALLS),
    hiddenAt: (x, y, z) => {
      const scenes = this.zones.scenes;
      if (!this.zones.ready || !scenes.inCastle) return false;
      const cell = this.world.sim.cellAt(x, z, y);
      return cell !== undefined && !scenes.streamer.isCellShown(cell);
    },
  };
  /** The one line-of-sight test the nameplates and the speech bubbles share. */
  private readonly sightTest = levelSightTest(this.sightLevel);
  private readonly platePool: PlateTarget[] = [];
  private readonly eye: [number, number, number] = [0, 0, 0];
  private readonly selfPose: SelfPose = { id: -1, x: 0, y: 0, z: 0, yaw: 0, anim: "idle" };
  private readonly feet: [number, number, number] = [0, 0, 0];
  private hudTimer = 0;
  private lastPlace = "";
  private readonly onKey = (e: KeyboardEvent) => this.handleKey(e);
  /** The mount state (the server's word) and our broom's flight (predicted). */
  private readonly mount = new MountState();
  private readonly broom = new BroomFlight();
  /** A mount that came while a zone was still loading (a Flying class mounts its students as they arrive): its words wait for the zone. */
  private mountNoteDue = false;
  /** Every broom in view, one draw call. */
  private readonly brooms: Brooms;
  private readonly flightChip = new FlightChip();
  /** Auto run's chip and Z's pace (docs/architecture.md "Client"). */
  private readonly moveChip = new MoveChip();
  /** Stops auto run pressed against a wall. */
  private readonly stall = new StallWatch();
  /** The trade window was open last frame (a trade opening stops auto run). */
  private wasTrading = false;
  private readonly hipsAt = new Vector3();
  /** The flight numbers for the tier and percent last asked (`SimWorld.flyParams` allocates). */
  private flyParamsKey = "";
  private flyParamsCache: FlyParams | null = null;
  /** The floor under the flight camera (the zone's world). */
  private readonly floorAt: FloorAt = (x, z, y) => this.world.sim.floorAt(x, z, y);
  private readonly flightInput: FlightInput = { throttle: 0, turn: 0, climb: 0 };
  // ---------------------------------------------------------------- sound (docs/architecture.md "Sound and music")
  /** Ground covered by each character on foot (ourselves and remotes), for their footsteps. */
  private readonly strides = new Strides();
  /** The remotes moving on foot near us this frame, nearest first (pooled). */
  private readonly walkers: { r: Remote; d: number }[] = [];
  private readonly walkerPool: { r: Remote; d: number }[] = [];
  /** The place last given to the audio engine (a doorway keeps the area before it). */
  private audioPlace: AudioPlace | null = null;
  /** Our broom's wind while mounted, and where we were last frame (its speed). */
  private wind: LoopHandle | null = null;
  private readonly windFrom: [number, number, number] = [NaN, 0, 0];
  private windSpeed = 0;
  /** Riders within a flyby's distance last frame (a new one is heard passing). */
  private flybyNear = new Set<number>();
  private flybyNow = new Set<number>();
  /** The castle's looping emitters (fountain, hearths, cauldrons, owls) and the clock tower bell's spot. */
  private readonly castleEmitters: PlacedEmitter[];
  private readonly bellAt: Vec3 | null;
  /** The game hour the bell last tolled for (null: not yet known). */
  private bellHour: number | null = null;
  /** The zone shown's surfaces by id (the place and the floor under our feet). */
  private surfaceById = new Map<string, LayoutSurface>();
  /** `audio.playCount` as a click began: a button that made its own sound is not clicked over. */
  private clickMark = -1;
  private readonly listenerAt: [number, number, number] = [0, 0, 0];
  private readonly listenerFwd: [number, number, number] = [0, 0, 1];
  private readonly onClickCapture = () => {
    this.clickMark = audio.playCount;
  };
  private readonly onClick = (e: MouseEvent) => {
    const b = (e.target as Element | null)?.closest?.("button");
    // `data-sound="own"`: the button's sound comes with the server's reply (brewing, drinking), so no click first.
    if (!b || (b as HTMLButtonElement).disabled || (b as HTMLElement).dataset.sound === "own") return;
    const mark = this.clickMark;
    // After the click's own handlers and the windows stack's observer (both run first).
    queueMicrotask(() => {
      if (audio.playCount === mark) audio.play("ui.click");
    });
  };

  constructor(
    private readonly opts: GameOptions,
    token: string,
    /** The world of the zone shown (the castle, or a dorm room). */
    private world: WorldData,
    manifest: Manifest,
  ) {
    const house = houseInfo(opts.character.house);
    this.myHouse = house.id;
    const cells = manifest.cells;
    // Sound: armed now, heard from the first click or key (the browsers' rule).
    audio.init(this.settings.audio, this.settings.quality);
    const castleLayout = JSON.parse(world.layoutText) as {
      landmarks?: { id: string; pos: number[] }[];
      interactables?: { id: string; kind?: string; house?: string; pos: number[] }[];
      surfaces?: { kind: string; house?: string; min: number[]; max: number[]; y: number }[];
    };
    // Where the party map pins a member in a dorm room: our own house's dormitory door; another house's
    // common room is not on our map, so its doorway (the map moves the pin to the public ground beside it).
    const dormEntrance = new Map<string, [number, number, number]>();
    for (const s of castleLayout.surfaces ?? []) {
      if (s.kind === "door" && s.house && s.house !== house.id && !dormEntrance.has(s.house)) dormEntrance.set(s.house, [(s.min[0]! + s.max[0]!) / 2, s.y, (s.min[1]! + s.max[1]!) / 2]);
    }
    const ownDoor = castleLayout.interactables?.find((i) => i.kind === "dorm_door" && i.house === house.id)?.pos;
    if (ownDoor && ownDoor.length >= 3) dormEntrance.set(house.id, [ownDoor[0]!, ownDoor[1]!, ownDoor[2]!]);
    this.castleEmitters = emittersIn(castleLayout);
    const bell = castleLayout.landmarks?.find((l) => l.id === CLOCK_BELL.anchor)?.pos;
    this.bellAt = bell && bell.length >= 3 ? [bell[0]!, bell[1]! + 8, bell[2]!] : null;

    this.canvas.hidden = false;
    this.view = new WorldScene(this.canvas, this.settings.quality);
    const scene = this.view.scene;
    this.camera = new FollowCamera(scene, this.canvas, this.view.quality.viewDistance + 50);
    // The toon look's outline and bloom passes run on the follow camera; the torch pool lights the castle.
    this.view.attachCamera(this.camera.camera);
    this.view.torches.setTorches(castleTorches(cells));
    for (const c of cells) this.cellInfo.set(c.id, c);
    this.indexCells(world);
    this.library = new CastleLibrary(scene, `${WORLD_BASE}textures/`, this.settings.quality);
    this.lods = new KitLods(scene, this.candles);
    this.lods.setForced(this.settings.quality === "low");
    // At Low only each cell's nearest candle group is drawn: its flames go with it.
    this.view.fire.riderShown = (node) => !this.candles.isLow || this.candles.drawnAtLow(node);
    this.water = new LakeWater(scene);
    this.castleSeats = Seats.fromCells(cells);
    this.castleFloorSeats = Seats.floorFromCells(cells);
    this.owls = new KitOwls(scene, ASSETS_BASE);
    this.entrances = KitEntrances.forCastle(scene, world.layoutSurfaces, world.sim.interactables());
    this.stairs = new Staircases(scene, ASSETS_BASE, world.sim, world.layoutSurfaces, stairsText, devStairsPeriod(location.search, import.meta.env.DEV, location.hostname), {
      carried: (pos, yaw) => this.player.placeAt(pos, yaw),
      warn: (text) => this.chat.system(text),
      dust: (x, y, z) => this.school.dust(x, y, z, DUST_RGB),
      trail: (e, riding) => this.school.onStairs(e, riding),
    }, this.library);
    this.zones = new ZoneDirector(
      {
        view: this.view,
        worldBase: WORLD_BASE,
        castleCells: cells,
        castleWorld: world,
        house: house.id as House,
        radii: radiiFor(this.settings.quality),
        kit: { library: this.library, lods: this.lods, candles: this.candles, cup: this.cupKit, water: this.water, swings: this.stairs.swings, owls: this.owls, entrances: this.entrances, fire: this.view.fire },
        hlod: parseHlod(manifest.hlod),
        foliageKit: (manifest.foliageKit as FoliageKitInfo | undefined) ?? null,
      },
      this.hudRoot,
      opts.character.name,
      {
        onLeaving: () => {
          // The new zone resends its visible set; the room is rebuilt on arrival.
          this.remotes?.clear();
          this.characters.reset();
          this.school.leaving();
          this.dorm.leaving();
          this.residents.leaving();
        },
        onShown: (zoneWorld, arrival, changed) => {
          if (changed) {
            this.world = zoneWorld;
            // A potion's speed carries over (the server validates with it in every zone).
            this.player = new LocalPlayer(zoneWorld.sim, this.effects.speedPct === 100 ? zoneWorld.params : moveParams(this.effects.speedPct));
            this.lastPlace = "";
            this.indexCells(zoneWorld);
            this.layoutOverlay?.show(zoneWorld.layoutSurfaces);
          }
          this.player.placeAt(arrival.spawn, arrival.yaw);
          // The Flying class mounts its students: on a broom over the room's spawn.
          if (this.mount.mounted) this.liftOnBroom();
          if (this.mountNoteDue) {
            this.mountNoteDue = false;
            if (this.mount.mounted) this.chat.system(mountedText(arrival.zone.kind === "class"));
          }
          // The castle's seats, or the room's own (in its coordinates: the classroom's desks).
          // The common rooms' floor seats too (rugs and hearth steps: the floor sit).
          const castle = arrival.zone.kind === "castle";
          // Sound: the castle's emitters; a room has none.
          if (changed) {
            audio.setEmitters(castle ? this.castleEmitters : []);
            this.audioPlace = null;
            this.strides.clear();
          }
          // In a dorm, the placed chairs, stools, armchairs and beds are seats too.
          const roomSeats = arrival.zone.kind === "dorm" ? this.dorm.seats(zoneWorld.seats ?? null) : (zoneWorld.seats ?? null);
          this.characters.setSeats(castle ? this.castleSeats : roomSeats, castle ? this.castleFloorSeats : (zoneWorld.floorSeats ?? null));
          this.camera.faceAlong(arrival.yaw);
          if (changed) this.dorm.entered(arrival.zone, zoneWorld);
          if (changed) this.crafting.zoneShown(zoneWorld);
          if (changed) this.school.zoneShown(arrival.zone, zoneWorld);
        },
        onFailed: (e) => {
          console.error(e);
          this.dispose();
          opts.onFatal("The way is blocked", "This room could not be loaded. Please try again.");
        },
        send: (frame) => this.conn.send(frame),
        // A common room's notice board opens the House Cup and the house's next classes (client only).
        // The Owl Loft's owl mail opens the mail panel (client only, like the notice board).
        // The Wishing Room's door shows (and is offered) only while it is open for us.
        offers: (it) => this.secrets.offers(it),
        onLocalInteract: (it) => {
          // Using the Room's door offers the two rooms (the choice is `EnterWishingRoom`).
          if (this.secrets.localInteract(it)) return true;
          if (isOwlMail(it)) {
            this.economy.mail.toggle(true);
            return true;
          }
          if (it.kind !== "notice_board") return false;
          this.cup.open(true);
          return true;
        },
      },
    );
    this.player = new LocalPlayer(world.sim, world.params);
    this.sender = new MoveSender(world.protocol.clientMoveHz);

    this.hud = new Hud({
      name: opts.character.name,
      houseName: house.name,
      houseColour: house.colour,
      settings: this.settings,
      onViewLimit: (n) => {
        this.settings.viewLimit = n;
        saveSettings(this.settings);
        this.conn.send(encodeSetViewLimit(n));
      },
      onQuality: (q) => {
        this.settings.quality = q;
        saveSettings(this.settings);
        this.view.setQuality(q);
        this.camera.setFarPlane(this.view.farPlane);
        this.library.setQuality(q);
        this.lods.setForced(q === "low");
        this.zones.scenes.streamer.setRadii(radiiFor(q));
        this.updateCameraMax(true);
        this.school.setQuality(q);
        this.characters.setQuality(q);
        audio.setQuality(q);
      },
      onAudio: (a, commit) => {
        this.settings.audio = a;
        audio.setSettings(a);
        if (commit) saveSettings(this.settings);
      },
      onTogglePerf: () => this.togglePerf(),
      onSignOut: () => opts.onSignOut(),
      onPlayers: () => this.players.toggle(true),
      onResetWindows: () => this.windows.resetPositions(),
      onChatter: (c) => {
        this.settings.chatter = c;
        saveSettings(this.settings);
      },
      onChatAll: (f) => {
        this.settings.chatAll = f;
        saveSettings(this.settings);
        this.chat.setAllFilter(f);
      },
    });
    this.chat = new ChatPanel({
      maxBytes: world.protocol.chatMaxLen,
      houseName: house.name,
      allFilter: this.settings.chatAll,
      knownNames: () => [...(this.remotes?.names() ?? []), ...this.friendState.friendList().map((f) => f.name)],
      send: (cmd) => {
        if (!this.conn.isOpen) return "You are not connected right now.";
        if (cmd.channel === "party" && !this.party.inParty) return "You're not in a party. Invite someone with /invite Name.";
        if (cmd.channel === "club" && !this.club.inClub) return "You're not in a club.";
        try {
          this.conn.send(encodeChat(cmd.channel, cmd.to ?? null, cmd.text, ++this.chatSeq));
        } catch (e) {
          return e instanceof Error ? e.message : String(e);
        }
        // Shown right away; the server's echo of our own message is skipped in
        // onMessage, and a refused line is taken back out (chatRejected).
        this.chat.add({ channel: cmd.channel, from: opts.character.name, to: cmd.to, outgoing: true, text: cmd.text, clientSeq: this.chatSeq });
        return null;
      },
      localCommand: (line) =>
        this.party.command(line) ||
        this.club.command(line) ||
        this.characters.command(line) ||
        this.economy.command(line) ||
        this.dueling.command(line, () => this.visible.filter((r) => this.residents.selectable(r.id)).map((r) => ({ id: r.id, name: r.name, x: r.pose.x, y: r.pose.y, z: r.pose.z }))) ||
        this.school.command(line) ||
        this.cup.command(line),
      onFocusChange: (typing) => typing && this.keyboard.clear(),
      clubTag: () => this.club.tag,
    });
    this.dorm = new DormController({
      scene,
      canvas: this.canvas,
      camera: this.camera.camera,
      hudRoot: this.hudRoot,
      assetsBase: ASSETS_BASE,
      myName: opts.character.name,
      send: (frame) => this.conn.send(frame),
      notify: (text) => this.chat.system(text),
      occupants: () => (this.remotes ? [...this.remotes.byId.values()].map((r) => ({ id: r.id, name: r.name })) : []),
      people: () => [
        [this.player.x, this.player.y, this.player.z],
        ...(this.remotes ? [...this.remotes.byId.values()].map((r): [number, number, number] => [r.pose.x, r.pose.y, r.pose.z]) : []),
      ],
      invitable: () => this.friendState.onlineFriends().map((f) => f.name),
    });
    this.party = new PartyHub({
      hudRoot: this.hudRoot,
      myCharacterId: opts.character.id,
      myName: opts.character.name,
      max: world.protocol.partyMax,
      send: (frame) => this.conn.send(frame),
      notify: (text) => this.chat.system(text),
      whisper: (name) => this.chat.open(`/w ${quoteName(name)} `),
      poseOf: (id) => {
        const r = this.remotes?.byId.get(id);
        return r?.posed ? { x: r.pose.x, z: r.pose.z, surfaceId: this.world.sim.surfaceIdAt(r.pose.x, r.pose.z, r.pose.y) } : null;
      },
      keyPlace: (id) => this.school.keyPlaceSpot(id),
      dormDoor: (h) => {
        const door = dormEntrance.get(h);
        if (!door) return null;
        const [x, y, z] = door;
        return { x, y, z, surfaceId: world.sim.surfaceIdAt(x, z, y) };
      },
    });
    this.friends = new FriendsWindow({
      myName: opts.character.name,
      state: this.friendState,
      onWhisper: (name) => this.chat.open(`/w ${quoteName(name)} `),
      onInvite: (name) => this.dorm.invite(name),
      onPartyInvite: (name) => this.party.invite(name),
      onClubInvite: (name) => this.club.invite(name),
      canClubInvite: () => this.club.mayInvite,
      onVisit: (name) => this.dorm.visit(name),
      notify: (text) => this.chat.system(text),
      onFindPlayers: () => this.players.toggle(true),
    });
    this.hud.actions.append(this.friends.button);
    const secretary = (npcRoster as { npcs: { id: string; name: string; title?: string }[] }).npcs.find((n) => n.id === CLUB_RULES.secretary);
    this.club = new ClubHub({
      hudRoot: this.hudRoot,
      hudActions: this.hud.actions,
      myCharacterId: opts.character.id,
      myName: opts.character.name,
      secretary: secretary ? [secretary.name, secretary.title].filter(Boolean).join(", ") : "the Secretary of Clubs and Societies",
      send: (frame) => this.conn.send(frame),
      notify: (text) => this.chat.system(text),
      whisper: (name) => this.chat.open(`/w ${quoteName(name)} `),
      nameOf: (id) => this.remotes?.byId.get(id)?.name ?? null,
      level: () => this.school.level,
      crowns: () => this.economy.crowns,
      inReach: (id) => {
        const at = this.poseOf(id);
        const reach = (this.residents.npcs?.rules.talk_reach_m ?? 4.5) + 1;
        return !!at && Math.abs(at.y - this.player.y) < 2.5 && Math.hypot(at.x - this.player.x, at.z - this.player.z) <= reach;
      },
    });
    // Nameplate tints (ui/groups.ts): party blue wins over club green.
    this.groups = [this.party.membership, this.club.membership];
    this.characters = new Cast({
      scene,
      shadows: { add: (m) => this.view.addDynamicCaster(m), remove: (m) => this.view.removeDynamicCaster(m) },
      characterShadows: this.view.characterShadows,
      quality: this.settings.quality,
      speeds: { walk: world.params.walkSpeed, run: world.params.runSpeed },
      emotes: { waveMs: world.protocol.emoteWaveMs, bowMs: world.protocol.emoteBowMs },
      hudRoot: this.hudRoot,
      hudActions: this.hud.actions,
      send: (frame) => this.conn.send(frame),
      notify: (text) => this.chat.system(text),
      house: house.id as House,
      // On a broom no emote plays (the cast refuses it): auto run keeps flying.
      stopAutoRun: () => !this.mount.mounted && this.keyboard.state.stopAutoRun("emote"),
    });
    // Spells L20: a duel's binds, slows and wards sound through its cues (arena-fx), not twice.
    this.effects = new EffectsController({ scene, headHeight: (id) => this.characters.headHeight(id), inDuel: (id) => this.dueling?.isDuelist(id) ?? false });
    // Spells L20 (GUARDIAN): the companions read the effects book.
    this.guardian = new GuardianHub({ scene, assetsBase: ASSETS_BASE, effectsOf: (id) => this.effects.book.get(id), wandTip: (id, out) => this.characters.wandTip(id, out) });
    this.characters.setLooks((id) => this.effects.look(id));
    this.crafting = new CraftingHub({
      hudRoot: this.hudRoot,
      hudActions: this.hud.actions,
      effects: this.effects,
      send: (frame) => this.conn.send(frame),
      notify: (text) => this.chat.system(text),
      appearance: () => this.characters.selfAppearance,
      zone: () => (this.zones.ready ? this.zones.zone : null),
      world: () => this.world,
      dormItems: () => this.dorm.room.items.values(),
      nearby: () => this.visible.filter((r) => this.residents.selectable(r.id)).map((r) => ({ id: r.id, name: r.name, distance: Math.hypot(r.pose.x - this.player.x, r.pose.y - this.player.y, r.pose.z - this.player.z) })),
      isFriend: (name) => this.friendState.friendList().some((f) => f.name === name && f.online),
      onCharm: () => this.characters.play("wave"),
      placeName: () => this.lastPlace || null,
    });
    this.plates = new Nameplates(this.hudRoot);
    // A name shows only while its head could be seen: never through a wall, a floor or a ceiling.
    this.plates.setSightTest(this.sightTest);
    this.economy = new Economy({
      hudRoot: this.hudRoot,
      purseSlot: this.hud.purse,
      canvas: this.canvas,
      myName: opts.character.name,
      protocol: world.protocol,
      send: (frame) => this.conn.send(frame),
      notify: (text) => this.chat.system(text),
      whisper: (name) => this.chat.open(`/w ${quoteName(name)} `),
      selfId: () => this.selfId,
      selfPos: () => [this.player.x, this.player.y, this.player.z],
      nearby: () => this.visible.filter((r) => this.residents.selectable(r.id)).map((r) => ({ id: r.id, name: r.name, x: r.pose.x, y: r.pose.y, z: r.pose.z, height: this.characters.plateHeight(r.id) })),
      viewProjection: () => this.view.scene.getTransformMatrix().m,
      interactables: () => this.world.sim.interactables(),
      friendNames: () => this.friendState.friendList().map((f) => f.name),
      menuItems: (t) => [
        ...this.dueling.menuItems(t, [this.player.x, this.player.y, this.player.z]),
        ...(this.school.isNpc(t.id) ? [] : [{ label: "Examine", run: () => void this.card.open(t.name) }]),
        ...(this.school.isNpc(t.id) || this.party.isMember(t.name) ? [] : [{ label: "Invite to party", run: () => this.party.invite(t.name) }]),
        ...(this.school.isNpc(t.id) || !this.club.mayInvite || this.club.isMember(t.name) ? [] : [{ label: "Invite to club", run: () => this.club.invite(t.name) }]),
      ],
    });
    this.dueling = new DuelingController({
      scene,
      hudRoot: this.hudRoot,
      hudActions: this.hud.actions,
      send: (frame) => this.conn.send(frame),
      notify: (text) => this.chat.system(text),
      selfId: () => this.selfId,
      rttMs: () => this.conn.rttMs,
      viewProjection: () => this.view.scene.getTransformMatrix().m,
      viewport: () => ({ width: this.canvas.clientWidth, height: this.canvas.clientHeight }),
      headHeight: (id) => this.characters.headHeight(id),
      castAnimMs: world.protocol.castAnimMs,
      // Spells L20 (DUELCLIENT): a Guardian ward stopped a spell: the guarding guardian flares.
      onWardBlock: (entity) => this.guardian.flare(entity),
    });
    this.school = new SchoolHub({
      scene,
      canvas: this.canvas,
      camera: this.camera.camera,
      hudRoot: this.hudRoot,
      hudActions: this.hud.actions,
      house: house.id as House,
      topBar: () => this.hud.topBar,
      stripHost: this.hud.corner,
      quality: this.settings.quality,
      houseName: house.name,
      characterName: opts.character.name,
      castle: world,
      send: (frame) => this.conn.send(frame),
      notify: (text) => this.chat.system(text),
      // A professor's words: the NPC line kind (apricot, All only), never a player's say.
      say: (from, text) => this.chat.npc(from, text),
      selfId: () => this.selfId,
      zone: () => (this.zones.ready ? this.zones.zone : null),
      openSky: (layout) => this.zones.scenes.isOpenSky(layout),
      rttMs: () => this.conn.rttMs,
      effectsOf: (id) => this.effects.book.get(id),
      drawn: () => this.fxSubjects,
      poseOf: (id) => this.poseOf(id),
      nameOf: (id) => this.remotes?.byId.get(id)?.name ?? null,
      headHeight: (id) => this.characters.headHeight(id),
      plateHeight: (id) => this.characters.plateHeight(id),
      wandTip: (id, out) => this.characters.wandTip(id, out),
      feet: () => [this.player.x, this.player.y, this.player.z],
      look: () => this.camera.lookDirection(),
      room: () => this.dorm.room,
      liftFurniture: (itemId, dy) => this.dorm.liftFurniture(itemId, dy),
      onSelfCast: () => this.characters.playCast(world.protocol.castAnimMs),
      // Spells L20 (SOCIAL): spells aimed at the selected character; being an ally only words the refusal (the server decides).
      selected: () => this.selection.selected?.id ?? null,
      isAlly: (id) => {
        const name = this.remotes?.byId.get(id)?.name;
        return !!name && this.isSpellAlly(name);
      },
      onLevel: (level) => this.characters.setLevel(level),
      viewProjection: () => this.view.scene.getTransformMatrix().m,
      canvasSize: () => [this.canvas.clientWidth, this.canvas.clientHeight],
    });
    // Speech bubbles hide behind walls and floors as the nameplates do.
    this.school.setSightTest(this.sightTest);
    this.cup = new HouseCupHub({
      hudRoot: this.hudRoot,
      hudActions: this.hud.actions,
      house: house.id,
      kit: this.cupKit,
      boardClasses: () => this.school.boardClasses(),
      onWay: (classId) => this.school.pointToClass(classId),
      notify: (text) => this.chat.system(text),
    });
    this.secrets = new SecretsHub({
      hudRoot: this.hudRoot,
      hudActions: this.hud.actions,
      castle: world,
      entrances: this.entrances,
      houseName: house.name,
      send: (frame) => this.conn.send(frame),
      notify: (text) => this.chat.system(text),
      serverNow: () => this.school.clock.now(),
      zone: () => (this.zones.ready ? this.zones.zone : null),
      walk: (flight) => this.stairs.flightViews.find((v) => v.id === flight)?.walk,
      friends: () => this.friendState.onlineFriends().map((f) => ({ name: f.name, place: f.location?.kind === "castle" ? f.location.place : null })),
      party: () => this.party.mapMembers(),
      marks: () => this.school.mapMarks(),
      me: () => (this.ready && this.zones.ready && this.zones.zone?.kind === "castle" ? { x: this.player.x, z: this.player.z, yaw: this.player.yaw, surfaceId: this.placeSurface } : null),
      onEnter: () => this.dorm.enteringWishingRoom(),
      // A friend's place only comes by itself at a zone change: reload the list as the map opens.
      onMapOpen: () => void this.friends.start(),
    });
    this.residents = new NpcHub({
      scene,
      library: this.library,
      hudRoot: this.hudRoot,
      assetsBase: ASSETS_BASE,
      castle: world,
      house: house.id as House,
      houseName: house.name,
      characterName: opts.character.name,
      send: (frame) => this.conn.send(frame),
      notify: (text) => this.chat.system(text),
      chatter: () => this.settings.chatter,
      inView: () => this.school.npcsInView(),
      npcOf: (id) => this.school.npcOf(id),
      poseOf: (id) => this.poseOf(id),
      headHeight: (id) => this.characters.headHeight(id),
      feet: () => {
        if (!this.ready || !this.zones.ready || this.zones.zone?.kind !== "castle" || this.mount.mounted) return null;
        const f = this.residentFeet;
        f[0] = this.player.x;
        f[1] = this.player.y;
        f[2] = this.player.z;
        return f;
      },
      selected: () => this.selection.selected?.id ?? null,
      period: () => this.school.period(),
      cupHouses: () => this.cup.current?.houses ?? null,
      appearance: () => this.characters.selfAppearance,
      found: () => this.secrets.found,
      doorPrompt: () => this.zones.prompting,
      stationInReach: (station) => this.zones.ready && this.zones.scenes.world.sim.inReach(station, this.player.x, this.player.y, this.player.z),
      serverNow: () => this.school.clock.now(),
      openLore: () => this.school.openLore(),
      greet: (kind, name, text) => this.economy.greet(kind, name, text),
      desk: (id, name, title) => this.club.openDesk(id, name, title),
      deskOpen: () => this.club.deskOpen,
    });
    this.characters.setStyles(this.residents.styleOf);
    this.players = new PlayersWindow({ myName: opts.character.name, layer: this.hudRoot, onAction: (a, name) => this.playerAction(a, name), canClubInvite: () => this.club.mayInvite });
    this.card = new PlayerCardWindow({
      myName: opts.character.name,
      subjectName: (id) => this.school.subjectName(id),
      isFriend: (name) => this.friendState.friendList().some((f) => sameName(f.name, name)),
      isPartyMember: (name) => this.party.isMember(name),
      canClubInvite: () => this.club.mayInvite,
      isClubMember: (name) => this.club.isMember(name),
      isAlly: (name) => this.isSpellAlly(name),
      onAction: (a, name) => this.playerAction(a, name),
      // Spells L20 (SOCIAL): the card's "Cast on" row.
      castable: () => this.school.spells.allySpells(),
      onCast: (spell, name) => this.school.spells.castOnName(spell, name, (n) => this.remoteByName(n)),
    });
    this.selection = new WorldSelection({
      scene,
      canvas: this.canvas,
      inView: () => this.selectable(),
      feet: () => [this.player.x, this.player.y, this.player.z],
      viewProjection: () => this.view.scene.getTransformMatrix().m,
      openCard: (name) => void this.card.open(name),
      enabled: () => this.ready && !this.dorm.decorating && !this.school.aiming,
      onNpcDouble: () => this.residents.talkSelected(),
    });
    this.lookAt = new LookDirector(
      {
        selfId: () => this.selfId,
        self: () => (this.ready && this.selfId >= 0 ? this.selfPose : null),
        drawn: () => this.lookDrawn,
        isNpc: (id) => this.school.isNpc(id),
        hidden: (id) => this.effects.plateHidden(id),
        own: () => ({
          duelOpponent: this.dueling.opponent,
          spellAim: this.school.spells.aimPoint(this.aimAt) ? this.aimAt : null,
          tradePartner: this.economy.tradePartner,
          selected: this.selection.selected?.id ?? null,
        }),
        engages: (id) => {
          if (this.selection.selected?.id === id) return true;
          // NPCs: the resident we talk to turns to us.
          if (this.residents.engaged(id)) return true;
          const a = this.school.answering;
          return a.professor === id && performance.now() - a.at < NPC_ANSWER_MS;
        },
        send: (target) => this.conn.send(encodeLookAt(target)),
      },
      world.protocol.lookRangeM,
      world.protocol.lookSendsPerSec,
    );
    this.characters.setLookTargets(this.lookAt);
    this.hudRoot.append(this.hud.root, this.chat.root, this.perf.root, this.friends.root, this.players.root, this.card.root);
    // The party list sits after the performance overlay (party.css moves it below when F3 shows it).
    this.hudRoot.append(this.party.root);
    this.hud.keepClearOf(this.party.root);
    this.windows = new WindowStack(this.hudRoot);
    this.windows.setCloser(this.hud.menuRoot, () => this.hud.toggleMenu(false));
    this.windows.setCloser(this.economy.tradeRoot, () => this.economy.tradeEscape());
    this.windows.keepOpen(this.economy.tradeRoot);
    this.hudRoot.hidden = false;
    this.hudRoot.addEventListener("click", this.onClickCapture, true);
    this.hudRoot.addEventListener("click", this.onClick);
    this.perf.setVisible(this.settings.showPerf);
    if (this.settings.showPerf) this.instrumentation = this.makeInstrumentation();
    window.addEventListener("keydown", this.onKey);

    this.conn = new Connection(gameSocketUrl(), token, this.opts.character.id, {
      onMessage: (msg) => this.onMessage(msg),
      onStatus: (s) => this.onConnectionStatus(s),
      beforeReconnect: async () => {
        let playing: api.Character | null;
        try {
          playing = await api.myCharacter();
        } catch (e) {
          // A 401 has already sent the player to sign-in (api.onUnauthorized).
          return !(e instanceof api.ApiError && e.status === 401);
        }
        if (!stillPlaying(playing, this.opts.character)) {
          // The session (shared with other windows) now plays another
          // character, or none: this client must not come back as it.
          this.dispose();
          const notice = kickNotice(playing ? "replaced" : "no_character", "");
          this.opts.onFatal(notice.heading, notice.body);
          return false;
        }
        return true;
      },
    });
    this.conn.connect();
    this.brooms = new Brooms(scene, ASSETS_BASE);
    this.hudRoot.append(this.flightChip.root, this.moveChip.root);
    this.view.engine.runRenderLoop(() => this.frame());
    // Development only, `?secretsdev`: feed this client a server message by hand (the map, the toast, the Room's door).
    if (import.meta.env.DEV && new URLSearchParams(location.search).has("secretsdev")) {
      (window as unknown as { shsSecrets: unknown }).shsSecrets = { inject: (msg: ServerMessage) => this.onMessage(msg), serverNow: () => this.school.clock.now() };
    }
    // Development only, `?npcdev`: bring a roster NPC into view (as the server's residents will) and feed this client replies.
    if (import.meta.env.DEV && new URLSearchParams(location.search).has("npcdev")) {
      (window as unknown as { shsNpcs: unknown }).shsNpcs = {
        spawn: (id: number, npc: string, pos: [number, number, number], yaw = 0, anim: "idle" | "walk" | "sit" = "idle") => {
          const msg = this.residents.devSnapshot(id, npc, pos, yaw, anim);
          if (msg) this.onMessage(msg);
          return !!msg;
        },
        inject: (msg: ServerMessage) => this.onMessage(msg),
        me: () => [this.player.x, this.player.y, this.player.z, this.player.yaw],
      };
    }
    // Renderer measurements (perf-probe.ts): only with `?probe` in the URL.
    if (new URLSearchParams(location.search).has("probe")) {
      void import("./perf-probe").then((p) => p.installProbe({ engine: this.view.engine, scene, camera: this.camera.camera, view: this.view }));
      // Characters look at their target: who looks at whom here, and how far each head is turned (playtests, screenshots).
      (window as unknown as { shsLook: unknown }).shsLook = {
        self: () => this.selfId,
        visible: () => this.visible.map((r) => ({ id: r.id, name: r.name, npc: this.school.isNpc(r.id) })),
        target: (id: number) => this.lookAt.targetOf(id),
        turned: (id: number) => this.characters.looking(id),
        pose: (id: number) => this.poseOf(id),
        // Turning round on a bench (bench-flip.ts): where we sit (side, two-sided), whether `id` is mid-turn.
        seat: () => this.characters.seatedFacing(),
        seatTurning: (id: number) => this.characters.turningOnSeat(id),
        // Nameplates seen past the level (plate-sight-ray.ts): from the camera to (x, y, z), is it hidden, and why.
        sight: (x: number, y: number, z: number) => {
          const e = this.camera.camera.position;
          let walls = 0;
          for (const _ of this.sightLevel.walls()) walls++;
          const ray = levelSightTest({ walls: this.sightLevel.walls })(e.x, e.y, e.z, x, y, z);
          return { walls, cellHidden: this.sightLevel.hiddenAt?.(x, y, z) ?? false, ray };
        },
      };
    }
    // The walkable rectangles over the scene, for reviewing the kit: only with `?layout`.
    if (new URLSearchParams(location.search).has("layout")) {
      void import("./castle/layout-overlay").then((o) => {
        if (this.disposed) return;
        this.layoutOverlay = o.layoutOverlayFromUrl(scene, location.search);
        this.layoutOverlay?.show(this.world.layoutSurfaces);
      });
    }
  }

  // ---------------------------------------------------------------- network

  private onMessage(msg: ServerMessage): void {
    if (this.disposed) return;
    // A zone change, a class, a duel, a crafting station, a broom: auto run stops.
    const stop = autoRunStopFor(msg.type);
    if (stop) this.keyboard.state.stopAutoRun(stop);
    // NPCs: an NPC's reply to our talk (it also watches a shop opening for its keeper's greeting, welcome and zone changes).
    if (this.residents.onMessage(msg)) return;
    // Purse, mail, the Exchange counter and trades; it also notes welcome and zone changes.
    if (this.economy.onMessage(msg)) return;
    // Crafting stations, finds, drinking and Mimic Draught consent.
    if (this.crafting.onMessage(msg)) return;
    // Challenges, the duel list and the duel itself (it also notes zones, spellbooks and levels).
    if (this.dueling.onMessage(msg)) return;
    // Levels, spells outside duels, NPCs and classes (it also notes welcome, pongs, snapshots and zones).
    if (this.school.onMessage(msg)) return;
    // The Wishing Room's door and discoveries (it also notes welcome).
    if (this.secrets.onMessage(msg)) return;
    // The party: its state, invitations, notices and refusals (it also notes welcome).
    if (this.party.onMessage(msg)) return;
    // The club: its state, presence, invitations, notices and refusals (it also notes welcome and zones).
    if (this.club.onMessage(msg)) return;
    switch (msg.type) {
      case "welcome":
        this.effects.welcome(msg);
        this.lookAt.reset();
        // Every connection starts on foot (a logout in the air was saved on the ground below).
        this.mount.clear();
        void this.onWelcome(msg);
        break;
      case "mounted":
        this.onMounted(msg.on, msg.tier, msg.reason);
        break;
      case "snapshot":
        this.remotes?.apply(msg, performance.now());
        this.effects.snapshot(msg.entered, msg.left);
        this.lookAt.snapshot(msg);
        break;
      case "effectsChanged":
        this.effects.effectsChanged(msg);
        break;
      case "ownEffects":
        this.effects.ownEffects(msg);
        // Predict with the speed the server now validates with (Pick-Me-Up Tonic).
        this.player.setParams(this.effects.speedPct === 100 ? this.world.params : moveParams(this.effects.speedPct));
        break;
      case "roster":
        this.hud.setRoster(msg.online, msg.nearby);
        break;
      case "chat":
        if (msg.from === this.selfId) break; // already shown when sent
        if (msg.from === this.world.protocol.serverEntityId) {
          // A notice from the server itself ("… is not online."), not a player.
          this.chat.system(msg.text);
          break;
        }
        this.chat.add({ channel: msg.channel, from: msg.fromName, to: msg.to, text: msg.text });
        if (msg.channel === "whisper") audio.play("ui.whisper");
        break;
      // Spells L20 (SOCIAL): the server slides us (Adveni); our moves are dropped until it is over.
      case "forcedMove":
        this.player.force([msg.to.x / 100, msg.to.y / 100, msg.to.z / 100], msg.moveMs, msg.holdMs);
        this.keyboard.state.stopAutoRun("zone");
        // No sound here: the Adveni SpellCast of the same tick plays spell.pulled
        // on us (the spells controller), a duel pull its duel.pulled cue.
        break;
      case "correction":
        if (this.sender.acceptCorrection(msg.seq)) {
          // A teleport (a jump of PLACE_JUMP or more) stops auto run; a small correction does not.
          if (Math.hypot(msg.pos[0]! - this.player.x, msg.pos[2]! - this.player.z) >= PLACE_JUMP) this.keyboard.state.stopAutoRun("zone");
          this.player.placeAt(msg.pos);
          // On a broom: start again from a hover where the server put us.
          if (this.mount.mounted) this.broom.reset(this.player.yaw);
        }
        break;
      case "queued":
        if (this.ready) this.hud.setQueue(msg.position);
        else this.opts.onStatus("The castle is full", `You are number ${msg.position} in the queue. Keep this page open; you'll be let in automatically.`);
        break;
      case "chatRejected":
        this.chat.reject(msg.clientSeq, msg.reason);
        break;
      case "zoneChanged":
        // The visible set restarts in the new zone (the decoder has reset
        // too), whatever the zone loader decides to do with the arrival.
        this.remotes?.clear();
        this.effects.zoneChanged();
        this.lookAt.reset();
        // A player's card (and the selection) stays behind (it covered a class's panel).
        this.card.close();
        this.selection.clear();
        void this.zones.arrive({ zone: msg.zone, spawn: msg.spawn, yaw: msg.yaw });
        break;
      case "dormInviteReceived":
        audio.play("ui.invite");
        this.dorm.onMessage(msg);
        break;
      case "dormState":
      case "dormFurniture":
      case "dormOpenChanged":
      case "dormInviteDeclined":
      case "dormError":
        this.dorm.onMessage(msg);
        break;
      case "friend":
        this.friends.onEvent(msg.event);
        break;
      case "appearanceChanged":
        this.characters.appearanceChanged(msg, this.selfId, this.remotes);
        if (msg.id === this.selfId) this.crafting.appearanceChanged();
        break;
      case "pong":
      case "kick":
        break; // handled by Connection
    }
  }

  // ---------------------------------------------------------------- brooms

  /** B: mount where we stand, or land. The server answers `mounted` (and says why not). */
  private toggleMount(): void {
    const p = this.player;
    this.keyboard.state.stopAutoRun("mount");
    // Landing needs ground within half a metre: say so at once instead of asking (the server's own rule).
    if (this.mount.phase === "flying" && !this.world.sim.dismount(p.x, p.y, p.z)) {
      audio.play("ui.error");
      this.chat.system(mountRefusedText("not_landed", true));
      return;
    }
    const on = this.mount.toggle(performance.now());
    if (on === null) return;
    this.conn.send(encodeMount(on));
  }

  private onMounted(on: boolean, tier: number, reason: Parameters<MountState["onMounted"]>[2]): void {
    const out = this.mount.onMounted(on, tier, reason);
    switch (out.kind) {
      case "refused":
        audio.play("ui.error");
        this.chat.system(out.text);
        break;
      case "lift":
        this.liftOnBroom();
        audio.playAt("broom.mount", [this.player.x, this.player.y, this.player.z], { owner: this.selfId });
        // Joining the Flying class, the zone is still loading: say it once it shows.
        if (this.zones.ready) this.chat.system(mountedText(this.zones.zone?.kind === "class"));
        else this.mountNoteDue = true;
        break;
      case "land": {
        this.landFromBroom();
        const at: Vec3 = [this.player.x, this.player.y, this.player.z];
        audio.playAt("broom.land", at, { owner: this.selfId });
        audio.playAt("foot.land", at, { owner: this.selfId, delayS: 0.15 });
        break;
      }
      case "none":
        break;
    }
    this.hudTimer = 0;
  }

  /** On a broom: lifted where the server lifts a standing character (`SimWorld.mount`), facing as before. */
  private liftOnBroom(): void {
    const p = this.player;
    const m = this.world.sim.mount(p.x, p.y, p.z);
    if ("pos" in m) p.placeAt(m.pos, p.yaw);
    this.broom.reset(p.yaw);
    p.anim = "hover";
  }

  /** Off the broom: standing on the ground below (`SimWorld.dismount`, else the walkable floor under us). */
  private landFromBroom(): void {
    const p = this.player;
    const sim = this.world.sim;
    const at = sim.dismount(p.x, p.y, p.z);
    if (at) {
      p.placeAt(at, p.yaw);
      return;
    }
    const floor = sim.floorAt(p.x, p.z, p.y);
    if (floor !== undefined && sim.surfaceIdAt(p.x, p.z, floor)) p.placeAt([p.x, floor, p.z], p.yaw);
    else p.anim = "idle";
  }

  /** Our broom's flight numbers for its tier and Featherlight's percent (as the server checks them). */
  private flyParams(): FlyParams {
    const pct = this.effects.broomPct;
    const key = `${this.mount.tier}:${pct}`;
    if (key !== this.flyParamsKey || !this.flyParamsCache) {
      this.flyParamsKey = key;
      this.flyParamsCache = this.world.sim.flyParams(this.mount.tier, pct);
    }
    return this.flyParamsCache;
  }

  /** Our flight this frame: the keys, the camera's view, sim-wasm's `flyAndSlide`. */
  private flyFrame(dt: number, keys: MoveInput | null): void {
    const p = this.player;
    // While a landing is asked, hold still (the server lands us where we hover).
    const input = keys && !this.mount.pending ? this.flightInput : NO_FLIGHT_INPUT;
    if (keys && input === this.flightInput) {
      input.throttle = keys.forward;
      input.turn = keys.strafe;
      input.climb = keys.up ? 1 : keys.run ? -1 : 0;
    }
    // A and D swing the camera round; the broom follows the camera's view (flight.ts).
    if (input.turn) this.camera.turn(input.turn * KEY_TURN_RATE * Math.min(dt, 0.1));
    const look = this.camera.lookDirection();
    p.fly(dt, this.broom, input, yawOfLook(look[0], look[1]), this.flyParams(), this.mount.tier, this.effects.broomPct);
  }

  /** Auto run's frame: stuck against a wall for a second, or a trade opening, stops it; the chip follows. */
  private autoRunFrame(dt: number): void {
    const input = this.keyboard.state;
    const trading = !this.economy.tradeRoot.hidden;
    if (trading && !this.wasTrading) input.stopAutoRun("activity");
    this.wasTrading = trading;
    if (!input.autoRun) this.stall.reset();
    else if (this.stall.step(this.player.x, this.player.y, this.player.z, dt)) input.stopAutoRun("stuck");
  }

  /** The brooms under every rider drawn this frame (after the characters are posed). */
  private drawBrooms(): void {
    const b = this.brooms;
    b.begin();
    const me = this.selfPose;
    if (this.ready && me.id >= 0 && (me.anim === "fly" || me.anim === "hover")) this.addBroom(me.id, me.x, me.y, me.z, me.yaw, this.broom.pitch, this.broom.bank);
    for (const v of this.visible) {
      const a = v.pose.anim;
      if (a === "fly" || a === "hover") this.addBroom(v.id, v.pose.x, v.pose.y, v.pose.z, v.pose.yaw, 0, 0);
    }
    b.commit();
  }

  private addBroom(id: number, x: number, y: number, z: number, yaw: number, pitch: number, bank: number): void {
    const h = this.hipsAt;
    const scale = this.characters.hips(id, h);
    if (scale > 0) this.brooms.add(h.x, h.y - SEAT_BELOW_HIPS * scale, h.z, yaw, pitch, bank);
    else this.brooms.add(x, y + HIPS_FALLBACK - SEAT_BELOW_HIPS, z, yaw, pitch, bank);
  }

  /** The flight chip: how high we fly and whether we may land here. */
  private updateFlightChip(): void {
    if (!this.mount.mounted || !this.ready) {
      this.flightChip.show(null);
      return;
    }
    const p = this.player;
    const sim = this.world.sim;
    const floor = sim.floorAt(p.x, p.z, p.y);
    this.flightChip.show(flightChipText({ height: floor === undefined ? null : p.y - floor, hovering: p.anim === "hover", canLand: !!sim.dismount(p.x, p.y, p.z), landing: this.mount.phase === "landing" }));
  }

  private async onWelcome(msg: Extract<ServerMessage, { type: "welcome" }>): Promise<void> {
    this.selfId = msg.yourId;
    audio.setSelf(msg.yourId);
    this.characters.welcome(msg);
    this.remotes = new RemotePlayers(msg.yourId, msg.tickHz, msg.snapshotHz);
    this.sender.reset();
    this.hud.setQueue(null);
    this.conn.send(encodeSetViewLimit(this.settings.viewLimit));

    if (!this.ready) this.opts.onStatus("Lighting the candles", msg.zone.kind === "dorm" ? "Lighting your dormitory…" : "Loading the castle around you…");
    // New characters (and anyone who left off there) start in their dorm.
    await this.zones.arrive({ zone: msg.zone, spawn: msg.spawn, yaw: msg.yaw });
    // The saved map, at login and after each reconnect (the server saved while we were away).
    if (!this.disposed) void this.secrets.loadMap();
    if (this.disposed || this.ready) return;
    this.ready = true;
    this.opts.onReady();
    audio.stinger("stinger.welcome");
    this.chat.system(`Welcome, ${msg.character.name}. Press Enter to talk.`);
    void this.friends.start();
    void this.crafting.start();
    this.cup.start();
    void this.school.startTour();
  }

  private onConnectionStatus(s: ConnectionStatus): void {
    if (this.disposed) return; // includes the "closed" that dispose() itself causes
    switch (s.kind) {
      case "open":
        this.hud.setBanner(null);
        break;
      case "connecting":
        if (!this.ready) this.opts.onStatus("Opening the great doors", s.attempt ? "Trying again…" : "Connecting…");
        else this.hud.setBanner("Reconnecting…");
        break;
      case "waiting": {
        // The visible set is per connection; a new one resends it.
        this.remotes?.clear();
        const secs = Math.max(1, Math.round(s.delayMs / 1000));
        const why = s.reason ?? "Connection lost.";
        if (!this.ready) this.opts.onStatus("The castle is out of reach", `${s.reason ?? ""} Retrying in ${secs} s…`.trim());
        else this.hud.setBanner(`${why} Reconnecting in ${secs} s…`);
        break;
      }
      case "kicked": {
        this.dispose();
        const notice = kickNotice(s.code, s.reason);
        this.opts.onFatal(notice.heading, notice.body);
        // An expired session is better answered with the sign-in screen: a 401
        // here triggers api.onUnauthorized, which replaces this message.
        api.myCharacter().catch(() => undefined);
        break;
      }
      case "closed":
        break;
    }
  }

  // ---------------------------------------------------------------- input

  private handleKey(e: KeyboardEvent): void {
    if (e.code === "F3") {
      e.preventDefault();
      this.togglePerf();
      return;
    }
    // In fullscreen the Keyboard Lock holds Esc (fullscreen.ts): the first press does Esc's jobs
    // below, and holding it on to leave fullscreen must not close every window one repeat at a time.
    if (e.key === "Escape" && e.repeat) return;
    if (isTyping(e.target) || !this.ready) return;
    // Space climbs on a broom (Keyboard reads it); it must not press a focused button.
    if (e.code === "Space" && this.mount.mounted && !this.hud.menuOpen) e.preventDefault();
    // Settings controls keep focus after a click; arrow keys and space belong to them.
    if (this.hud.menuOpen && e.key !== "Escape") return;
    // Esc stops auto run and still does its other jobs below.
    if (e.key === "Escape") this.keyboard.state.stopAutoRun("escape");
    // Esc clears the selection first, then closes the top window (windows stack).
    if (e.key === "Escape" && (this.selection.clear() || this.windows.closeTop())) {
      e.preventDefault();
      return;
    }
    // Decorating, the friends window and the door prompt take their keys (and Escape) first.
    // A duel's spell keys come first.
    // E with a player selected opens their card. Review: after the door, station
    // or cauldron in reach (their prompt says E), so a selection left on someone never takes them over.
    if (!this.hud.menuOpen && (this.dueling.handleKey(e) || this.secrets.handleKey(e) || this.dorm.handleKey(e) || this.school.handleKey(e) || this.players.handleKey(e) || this.card.handleKey(e) || this.economy.handleKey(e) || this.friends.handleKey(e) || this.characters.handleKey(e) || this.zones.handleKey(e) || this.crafting.handleKey(e) || this.residents.handleKey(e) || this.club.handleKey(e) || this.selectionKey(e) || this.cup.handleKey(e) || this.school.handleTrailKey(e))) {
      e.preventDefault();
      return;
    }
    // The arrow keys nothing above took (spell targeting, a focused list or the trace pad keep theirs) turn the camera.
    if (this.keyboard.cameraKeyDown(e)) {
      e.preventDefault();
      return;
    }
    // R: auto run on or off; Z: walk or run (input.ts).
    if ((e.code === "KeyR" || e.code === "KeyZ") && !e.repeat && !e.ctrlKey && !e.metaKey && !e.altKey) {
      e.preventDefault();
      audio.play("ui.toggle");
      if (e.code === "KeyR") this.keyboard.state.toggleAutoRun();
      else {
        this.keyboard.toggleWalk();
        this.moveChip.flashPace(performance.now());
      }
      return;
    }
    // Tab steps through the characters nearby and never moves the page's focus while playing.
    if (tabIsOurs(e, false)) {
      e.preventDefault();
      if (!this.hud.menuOpen) this.selection.cycle(e.shiftKey ? -1 : 1);
      return;
    }
    if (e.code === "KeyB" && !this.hud.menuOpen && !e.repeat) {
      // Mount or land (a dorm owner's B decorates: the dorm took it above).
      e.preventDefault();
      this.toggleMount();
    } else if (e.key === "Enter") {
      e.preventDefault();
      this.chat.open();
    } else if (e.key === "/") {
      e.preventDefault();
      this.chat.open("/");
    } else if (e.key === "Escape" && this.hud.menuOpen) {
      // Escape closes what is open, Settings included, and never opens
      // anything: one press too many after closing a window must not pop
      // Settings up (it would take the keys and stop the player). Settings
      // opens from its button.
      this.hud.toggleMenu(false);
    }
  }

  /** Spells may be aimed at `name` (a friend, party member or club member; the server decides, blocks included). */
  private isSpellAlly(name: string): boolean {
    return this.friendState.friendList().some((f) => sameName(f.name, name)) || this.party.isMember(name) || this.club.isMember(name);
  }

  /** Spells L20 (SOCIAL): a character in view by name (the card casts on them), or null. */
  private remoteByName(name: string): number | null {
    for (const [id, r] of this.remotes?.byId ?? []) if (sameName(r.name, name)) return id;
    return null;
  }

  /** E with a player selected: their card (asked after doors and stations in reach; E on an NPC, or with nobody selected, does nothing here). */
  private selectionKey(e: KeyboardEvent): boolean {
    if (e.code !== "KeyE" || e.repeat || e.ctrlKey || e.metaKey || e.altKey) return false;
    return this.selection.use();
  }

  /** The characters a click or Tab can select: everyone drawn but the invisible (their name is hidden too). */
  private selectable(): SelectCandidate[] {
    const out: SelectCandidate[] = [];
    for (const r of this.visible) {
      // NPCs: background students are never selected (nor targeted, challenged or traded with).
      if (!r.posed || this.effects.plateHidden(r.id) || !this.residents.selectable(r.id)) continue;
      out.push({ id: r.id, name: r.name, npc: this.school.isNpc(r.id), x: r.pose.x, y: r.pose.y, z: r.pose.z, height: this.characters.plateHeight(r.id) });
    }
    return out;
  }

  /** An action on a player from the list or the card (each reuses what exists). */
  private playerAction(action: PlayerAction, name: string): void {
    switch (action) {
      case "add_friend":
        void this.friends.addFriend(name).then(() => this.card.refresh());
        break;
      case "whisper":
        this.chat.open(`/w ${quoteName(name)} `);
        break;
      case "examine":
        void this.card.open(name);
        break;
      case "challenge": {
        const seen = this.visible.find((r) => sameName(r.name, name));
        this.dueling.challenge(seen?.name ?? name, seen?.id ?? null);
        break;
      }
      case "party_invite":
        this.party.invite(name);
        this.card.refresh();
        break;
      case "club_invite":
        this.club.invite(name);
        this.card.refresh();
        break;
    }
  }

  private togglePerf(): void {
    this.settings.showPerf = !this.perf.visible;
    saveSettings(this.settings);
    this.perf.setVisible(this.settings.showPerf);
    if (this.settings.showPerf) this.instrumentation ??= this.makeInstrumentation();
    else {
      this.instrumentation?.dispose();
      this.instrumentation = null;
    }
  }

  private makeInstrumentation(): SceneInstrumentation {
    const inst = new SceneInstrumentation(this.view.scene);
    inst.captureFrameTime = true;
    return inst;
  }

  // ---------------------------------------------------------------- frame

  private frame(): void {
    const engine = this.view.engine;
    const dt = Math.min(engine.getDeltaTime() / 1000, 0.25);
    const now = performance.now();
    const p = this.player;

    // The staircases first (a switch carries the player before it moves).
    const inCastle = this.ready && this.zones.ready && this.zones.zone?.kind === "castle";
    this.stairs.update(this.school.clock.now(), inCastle ? p : null, inCastle && this.gsShown(), inCastle && this.lastCell !== null && GRAND_STAIRCASE_CELLS.has(this.lastCell));

    // While a zone loads (behind the fade) the player stands still.
    if (this.ready && this.conn.isOpen && this.zones.ready) {
      this.mount.expire(now);
      // Typing or in Settings, no keys move us, but auto run keeps going (input.ts).
      const held = !this.chat.isTyping && !this.hud.menuOpen;
      const keys = this.keyboard.read(this.mount.mounted, held);
      if (held) {
        const turn = cameraStep(this.keyboard.state.cameraKeys(), dt);
        if (turn.dAlpha) this.camera.turn(turn.dAlpha);
        if (turn.dBeta) this.camera.tilt(turn.dBeta);
      }
      if (this.mount.mounted) this.flyFrame(dt, keys);
      // While a mount is asked, stand still (the server lifts us where we stand).
      // And while our flight swings (we ride it; the switch carries us).
      else {
        const was: [number, number, number] = [p.x, p.y, p.z];
        const look = this.camera.lookDirection();
        // Seated: a tap of the way behind us turns us round on a two-sided bench (nothing on a one-way seat), a hold stands us up (bench-flip.ts).
        const tap = this.benchTap.step(now, inputDirection(keys.forward, keys.strafe, look[0], look[1]), this.characters.seatedFacing(), this.keyboard.state.autoRun);
        const seated = this.characters.seatedFacing();
        if (tap === "flip" && seated) p.yaw = wrapAngle(seated.yaw + Math.PI);
        const go = tap === "move" && this.mount.phase !== "mounting" && !this.stairs.ridingSwing;
        const sitYaw = p.yaw;
        p.update(dt, go ? keys : NO_INPUT, look);
        // Pushing into the bench (it is solid) leaves us seated: keep the side we sit on, not the key's way.
        if (seated && p.anim === "idle") p.yaw = sitYaw;
        // Never onto a flight swinging away behind its gate (the server would allow it; we stop at the gate).
        if (this.stairs.blocksStep(was, [p.x, p.y, p.z])) {
          p.x = was[0];
          p.y = was[1];
          p.z = was[2];
          p.anim = "idle";
        }
      }
      this.autoRunFrame(dt);
      const move = this.sender.tick(dt, p.x, p.y, p.z, p.yaw, p.anim);
      if (move) this.conn.send(encodeMove(move.seq, move.x, move.y, move.z, move.yaw, move.anim));
    }

    this.drawCharacters(now, dt);
    this.selection.update();
    this.drawBrooms();
    this.moveChip.update(now, this.ready && this.keyboard.state.autoRun, this.keyboard.state.walking, !this.flightChip.root.hidden);
    this.secretsFrame(dt, now);
    // A teleport or respawn (a jump of PLACE_JUMP or more since the last place
    // update): place at once, so the new spot's cells show (castle kit M2
    // `sees`, docs/art/castle-kit.md "Cell visibility (`sees`)") and the camera's rays test them,
    // instead of up to a HUD tick (0.25 s) of the old set.
    const jx = p.x - this.placeX;
    const jz = p.z - this.placeZ;
    if (jx * jx + jz * jz >= PLACE_JUMP * PLACE_JUMP) this.updatePlace();
    // The camera follows the character's height (a uniform scale of the
    // body's default; docs/art/character-adoption.md) and a shrunk or
    // swollen player at their own shoulder height: the orbit target is the
    // same share of the drawn height as TARGET_HEIGHT is of the default male.
    const drawnHeight = this.ready && this.selfId >= 0 && this.characters.isDrawn(this.selfId) ? this.characters.headHeight(this.selfId) : DEFAULT_HEIGHT_M * this.effects.selfScale;
    // The drawn pose: on a swinging flight it turns with the flight. The flight camera keeps to the zone's open sky.
    const me = this.selfPose;
    const flying = this.mount.mounted && this.ready;
    this.camera.update(me.x, me.y + TARGET_HEIGHT * (drawnHeight / DEFAULT_HEIGHT_M - 1), me.z, dt, this.zones.scenes.walls, flying ? this.floorAt : null, flying ? this.world.flightBounds : null);
    this.view.followShadows(p.x, p.y, p.z, dt);
    this.audioFrame(dt);

    const feet = this.feet;
    feet[0] = p.x;
    feet[1] = p.y;
    feet[2] = p.z;
    if (this.ready) this.zones.update(feet, dt);
    if (this.ready) this.crafting.update(feet, dt);
    this.dorm.update();
    this.effects.render(dt, this.fxSubjects, this.camera.camera, this.ready && this.zones.ready ? feet : null);
    // Spells L20 (GUARDIAN): the companions follow the characters drawn this frame.
    this.guardian.update(dt, this.fxSubjects, this.camera.camera);
    this.dueling.frame(dt, now, this.camera.camera);
    this.school.update(dt);
    this.cup.update(dt);
    this.residents.update(dt);
    this.club.update();

    // Castle kit M3: the candle groups of shown cells bob (a few matrix updates, no allocation).
    this.candles.update(now / 1000, this.camera.camera.globalPosition);
    // Animated fire: the flames' clock and the lights that flicker with them (no work per flame).
    this.view.fire.update(now);
    this.owls.update(now / 1000, (cell) => this.zones.scenes.inCastle && this.zones.scenes.streamer.isCellShown(cell));
    // Vegetation look A: the trees and hedges shown, each at its LOD, swaying.
    this.zones.scenes.foliage?.update(this.camera.camera, now / 1000, this.zones.scenes.foliageShown, this.settings.quality === "low");
    if (this.layoutOverlay) this.layoutOverlay.setFlightTurns(this.stairs.turnsById());
    this.water.tick(now);
    this.view.scene.render();

    this.updateNameplates();
    this.school.overlay();
    this.hudTimer -= dt;
    if (this.hudTimer <= 0) {
      this.hudTimer = HUD_INTERVAL;
      this.updatePlace();
      this.dorm.refreshPanel();
      this.secrets.hudTick();
      this.economy.update();
      if (this.ready) this.dueling.hudTick(feet);
      if (this.perf.visible) this.updatePerf();
    }
  }

  private drawCharacters(now: number, dt: number): void {
    const visible = this.remotes ? this.remotes.update(now, dt, this.visible) : this.visible;
    const p = this.player;
    const me = this.selfPose;
    me.id = this.selfId;
    me.x = p.x;
    me.y = p.y;
    me.z = p.z;
    me.yaw = p.yaw;
    me.anim = p.anim;
    // Riders of a swinging flight are drawn turned with it (in the castle only).
    if (this.zones.ready && this.zones.zone?.kind === "castle") {
      this.stairs.turnDrawn(me);
      for (const v of visible) this.stairs.turnDrawn(v.pose);
    }
    // A duel plays the cast animation for our predicted casts and the duelists' telegraphs.
    this.dueling.pose(this.ready ? me : null, visible, now);
    this.collectFxSubjects(this.ready ? me : null, visible);
    this.effects.prepare(dt, this.fxSubjects, Date.now());
    // Characters look at their target: ours from our own state (and told to the server), the others' as they were sent, the NPCs' here.
    this.lookDrawn = visible;
    this.lookAt.frame(now);
    // NPCs: the talking portraits' subjects near us are drawn with everyone (no entities: client only).
    this.characters.draw(now, dt, this.ready ? me : null, visible, this.camera.camera.position, this.residents.figures(now));
  }

  /** Everyone drawn this frame, nearest the camera first (pooled: no garbage per frame). */
  private collectFxSubjects(me: SelfPose | null, visible: readonly Remote[]): void {
    this.fxSubjects.length = 0;
    if (me && me.id >= 0) this.addFxSubject(me.id, me.x, me.y, me.z, me.yaw);
    for (const v of visible) this.addFxSubject(v.id, v.pose.x, v.pose.y, v.pose.z, v.pose.yaw);
    (this.fxSubjects as (FxSubject & { d: number })[]).sort(byDistance);
  }

  private addFxSubject(id: number, x: number, y: number, z: number, yaw: number): void {
    const eye = this.camera.camera.position;
    const s = (this.fxPool[this.fxSubjects.length] ??= { id: 0, x: 0, y: 0, z: 0, yaw: 0, d: 0 });
    s.id = id;
    s.x = x;
    s.y = y;
    s.z = z;
    s.yaw = yaw;
    s.d = (x - eye.x) ** 2 + (y - eye.y) ** 2 + (z - eye.z) ** 2;
    this.fxSubjects.push(s);
  }

  /** Where a drawn character stands (ourselves included), for spell and NPC effects; null when not in view. */
  private poseOf(id: number): { id: number; x: number; y: number; z: number; yaw: number } | null {
    if (id === this.selfId && this.ready) return this.selfPose;
    const r = this.remotes?.byId.get(id);
    return r?.posed ? { id, x: r.pose.x, y: r.pose.y, z: r.pose.z, yaw: r.pose.yaw } : null;
  }

  private updateNameplates(): void {
    // Runs every frame for up to 100 characters, so the targets are pooled.
    const targets = this.plateTargets;
    targets.length = 0;
    for (const r of this.visible) {
      // Invisibility Draught: the body stays, the name goes.
      if (this.effects.plateHidden(r.id)) continue;
      // NPCs: a resident's plate in the residents' style (name and title); a student wears none.
      const resident = this.residents.plate(r.id);
      if (resident === null) continue;
      const t = (this.platePool[targets.length] ??= { id: 0, name: "", colour: "", badge: false, npc: false, group: null, x: 0, y: 0, z: 0 });
      t.id = r.id;
      t.npc = resident !== undefined;
      // Chatterbrew: a garbled-speech mark after the name (the server scrambles the words).
      t.name = resident ? resident.text : this.effects.babbling(r.id) ? `${r.name} ⋯` : this.school.plateName(r.id, r.name);
      t.colour = houseInfo(r.house).colour;
      // A party member's name is party blue (a later group would tint by precedence: ui/groups.ts).
      t.group = resident ? null : plateGroup(r.id, this.groups);
      // The House Cup's holders wear the cup badge for the week.
      t.badge = this.cup.badged(r.house);
      t.x = r.pose.x;
      t.y = r.pose.y + this.characters.plateHeight(r.id);
      t.z = r.pose.z;
      targets.push(t);
    }
    const eye = this.camera.camera.position;
    const e = this.eye;
    e[0] = eye.x;
    e[1] = eye.y;
    e[2] = eye.z;
    this.plates.update(targets, this.view.scene.getTransformMatrix().m, e, this.canvas.clientWidth, this.canvas.clientHeight, this.hud.keepouts);
  }

  private updatePlace(): void {
    const p = this.player;
    // On a broom, the ground under us names the place (none over the lake: the last name stays).
    const flying = this.mount.mounted;
    const floor = flying ? this.world.sim.floorAt(p.x, p.z, p.y) : undefined;
    const id = this.world.sim.surfaceIdAt(p.x, p.z, floor ?? p.y);
    const meta = id ? this.world.surfaces.get(id) : undefined;
    this.placeSurface = id;
    // Doorways are a step long; keep showing the room you came from.
    if (meta && meta.kind !== "door" && meta.name) this.lastPlace = meta.name;
    this.hud.setPlace(this.lastPlace);
    // The camera's furthest zoom follows the cell (doorways keep the one you came from,
    // unless the player arrived on the doorway by a jump: a teleport or a respawn).
    const jumped = !(Math.hypot(p.x - this.placeX, p.z - this.placeZ) < PLACE_JUMP);
    this.placeX = p.x;
    this.placeZ = p.z;
    if (id && meta && (meta.kind !== "door" || jumped || this.lastCell === null)) this.lastCell = this.cellOfSurface.get(id) ?? null;
    // Castle kit M2: show the cells this surface sees (castle/visibility.ts).
    // And, outdoors, the far castle (castle/hlod.ts) and the longer view (scene.ts fogRange).
    // On a broom we are outdoors (brooms fly only in the open sky; `outdoorsAt` is false in the air).
    this.zones.scenes.place(id, p.x, p.z, p.y, flying);
    this.view.setOutdoors(this.zones.scenes.outdoors, this.school.daylight);
    this.updateAudioPlace(id);
    this.updateFlightChip();
    this.updateCameraMax(false);
    this.updateSkyTint();
  }

  // ---------------------------------------------------------------- sound

  /** The place for sound (HUD tick): the area, ambience and music follow it; the clock tower tolls the hour. */
  private updateAudioPlace(surfaceId: string | undefined): void {
    const zone = this.zones.zone;
    if (!this.ready || !this.zones.ready || !zone) return;
    const p = this.player;
    const flying = this.mount.mounted && (p.anim === "fly" || p.anim === "hover");
    const surface = surfaceId ? this.surfaceById.get(surfaceId) : undefined;
    this.audioPlace = placeOf({ zone, surface, daylight: this.school.daylight, flying, duel: this.dueling.inDuel }, this.audioPlace);
    audio.setPlace(this.audioPlace);
    // The bell: once each game hour, in the castle and its grounds.
    const hour = this.school.gameHour();
    if (hour !== null && hour !== this.bellHour) {
      if (this.bellHour !== null && zone.kind === "castle" && this.bellAt) audio.playAt("world.clock_bell", this.bellAt);
      this.bellHour = hour;
    }
  }

  /**
   * Once a frame after the camera: the listener, our footsteps and the
   * nearest remotes', our broom's wind and riders flying by. The listener
   * faces as the camera looks but stands at the point it orbits (our
   * shoulders): our own sounds stay at full level however far the camera is
   * zoomed out, and others' fall off with their distance from us.
   */
  private audioFrame(dt: number): void {
    const cam = this.camera.camera;
    const eye = cam.globalPosition;
    const t = cam.target;
    const la = this.listenerAt;
    la[0] = t.x;
    la[1] = t.y;
    la[2] = t.z;
    const f = this.listenerFwd;
    const fx = t.x - eye.x;
    const fy = t.y - eye.y;
    const fz = t.z - eye.z;
    const fl = Math.hypot(fx, fy, fz) || 1;
    f[0] = fx / fl;
    f[1] = fy / fl;
    f[2] = fz / fl;
    audio.setListener(la, f);
    const zone = this.zones.zone;
    if (!this.ready || !this.zones.ready || !zone) return;
    const layout = zone.kind === "castle" ? null : zone.layout;
    const p = this.player;
    this.strides.begin();
    // Ours.
    const onFoot = !this.mount.mounted && (p.anim === "walk" || p.anim === "run");
    const run = p.anim === "run";
    if (this.strides.step(this.selfId, p.x, p.z, onFoot ? (run ? STRIDE_M.run : STRIDE_M.walk) : 0)) {
      audio.footstep(footSurface(layout, this.placeSurface ? this.surfaceById.get(this.placeSurface) : undefined), [p.x, p.y, p.z], run, this.selfId);
    }
    // The nearest remotes moving on foot.
    const walkers = this.walkers;
    walkers.length = 0;
    for (const r of this.visible) {
      const a = r.pose.anim;
      if (!r.posed || (a !== "walk" && a !== "run")) {
        this.strides.step(r.id, r.pose.x, r.pose.z, 0);
        continue;
      }
      const d = Math.hypot(r.pose.x - p.x, r.pose.y - p.y, r.pose.z - p.z);
      if (d > FOOTSTEPS_M) continue;
      const w = (this.walkerPool[walkers.length] ??= { r, d: 0 });
      w.r = r;
      w.d = d;
      walkers.push(w);
    }
    walkers.sort(byDistance);
    const heard = VOICE_CAPS[this.settings.quality].footstepSources;
    for (let i = 0; i < walkers.length; i++) {
      const r = walkers[i]!.r;
      const runs = r.pose.anim === "run";
      if (!this.strides.step(r.id, r.pose.x, r.pose.z, runs ? STRIDE_M.run : STRIDE_M.walk) || i >= heard) continue;
      const under = this.world.sim.surfaceIdAt(r.pose.x, r.pose.z, r.pose.y);
      audio.footstep(footSurface(layout, under ? this.surfaceById.get(under) : undefined), [r.pose.x, r.pose.y, r.pose.z], runs, r.id);
    }
    // Our broom's wind: louder, higher and brighter the faster we fly.
    if (this.mount.mounted && (p.anim === "fly" || p.anim === "hover")) {
      const from = this.windFrom;
      const speed = Number.isNaN(from[0]) || dt <= 0 ? 0 : Math.hypot(p.x - from[0], p.y - from[1], p.z - from[2]) / dt;
      const climb = Number.isNaN(from[0]) || dt <= 0 ? 0 : (p.y - from[1]) / dt;
      from[0] = p.x;
      from[1] = p.y;
      from[2] = p.z;
      this.windSpeed += (Math.min(speed, 40) - this.windSpeed) * Math.min(1, dt * 4);
      const v = this.windSpeed;
      this.wind ??= audio.loop("broom.wind", { owner: this.selfId, gain: 0.1 });
      this.wind.setGain(Math.min(1, 0.12 + v / 16), 0.15);
      this.wind.setRate(0.85 + Math.min(0.5, v / 30));
      this.wind.setTone(Math.min(1, Math.max(0, 0.35 + v / 30 - climb / 20)));
    } else if (this.wind) {
      this.wind.stop(0.6);
      this.wind = null;
      this.windFrom[0] = NaN;
      this.windSpeed = 0;
    }
    // Riders passing close.
    const now = this.flybyNow;
    now.clear();
    for (const v of this.visible) {
      if (v.pose.anim !== "fly") continue;
      if (Math.hypot(v.pose.x - p.x, v.pose.y - p.y, v.pose.z - p.z) > HEAR.broomFlybyM) continue;
      now.add(v.id);
      if (!this.flybyNear.has(v.id)) audio.playAt("broom.flyby", [v.pose.x, v.pose.y, v.pose.z], { owner: v.id });
    }
    this.flybyNow = this.flybyNear;
    this.flybyNear = now;
  }

  /** Reveals, the pace marks and the entrances, after our move (everyone drawn is at the doors). */
  private secretsFrame(dt: number, now: number): void {
    const p = this.player;
    const inCastle = this.ready && this.zones.ready && this.zones.zone?.kind === "castle";
    const feet = this.feetList;
    feet.length = 0;
    const add = (x: number, y: number, z: number, house: string) => {
      const f = (this.feetPool[feet.length] ??= [0, 0, 0, ""]);
      f[0] = x;
      f[1] = y;
      f[2] = z;
      f[3] = house;
      feet.push(f);
    };
    if (inCastle) {
      add(p.x, p.y, p.z, this.myHouse);
      for (const v of this.visible) add(v.pose.x, v.pose.y, v.pose.z, v.house);
    }
    const onFoot = inCastle && !this.mount.mounted;
    this.secrets.frame(dt, now, onFoot ? [p.x, p.y, p.z] : null, feet);
  }

  /** Whether a Grand Staircase cell is shown (the gates draw only then). */
  private gsShown(): boolean {
    const s = this.zones.scenes;
    if (!s.inCastle) return false;
    for (const c of GRAND_STAIRCASE_CELLS) if (s.streamer.isCellShown(c)) return true;
    return false;
  }

  /** Surface → cell lookup for `world` (the castle's surfaces name their cell; dorms have none). */
  private indexCells(world: WorldData): void {
    this.cellOfSurface = new Map();
    for (const s of world.layoutSurfaces) if (s.cell) this.cellOfSurface.set(s.id, s.cell);
    this.surfaceById = new Map(world.layoutSurfaces.map((s) => [s.id, s]));
    this.lastCell = null;
  }

  private updateCameraMax(force: boolean): void {
    const cell = this.lastCell;
    const max = cameraMaxFor(cell, cell ? this.cellInfo.get(cell) : undefined, this.settings.quality);
    if (!force && max === this.shownCameraMax) return;
    this.shownCameraMax = max;
    this.camera.setMaxDistance(max);
  }

  /** Windows (`tint: "sky"`) glow with the hour's sky light, not daylight at night. */
  private updateSkyTint(): void {
    const hemi = this.view.scene.getLightByName("sky") as HemisphericLight | null;
    if (!hemi || !this.zones.scenes.inCastle) return;
    const d = hemi.diffuse;
    const noon = NOON_FILL.hemiSky;
    this.library.setSkyLight(skyEmission(hemi.intensity, luma(d.r, d.g, d.b), NOON_FILL.hemiIntensity, luma(noon[0], noon[1], noon[2])));
  }

  private updatePerf(): void {
    const engine = this.view.engine;
    const scene = this.view.scene;
    const p = this.player;
    this.perf.show({
      fps: engine.getFps(),
      frameMs: this.instrumentation?.frameTimeCounter.lastSecAverage ?? 0,
      drawCalls: this.instrumentation?.drawCallsCounter.current ?? 0,
      activeMeshes: scene.getActiveMeshes().length,
      triangles: Math.round(scene.getActiveIndices() / 3),
      heapBytes: jsHeapBytes(),
      cellsLoaded: this.zones.scenes.streamer.loadedCount,
      cellsLoading: this.zones.scenes.streamer.loadingCount,
      cellsTotal: this.zones.scenes.streamer.totalCount,
      characters: 1 + this.visible.length,
      rttMs: this.conn.rttMs,
      resolution: `${engine.getRenderWidth()}×${engine.getRenderHeight()} (${this.settings.quality})`,
      position: `${p.x.toFixed(1)}, ${p.y.toFixed(1)}, ${p.z.toFixed(1)}  ${p.anim}`,
      extra: [
        ["toon", `outlines ${this.view.quality.outlines ? "on" : "off"}, bloom ${this.view.quality.bloom ? "on" : "off"}, ssao ${ssaoState(this.view.quality)}, passes ${(this.view.postBytes / 1048576).toFixed(1)} MB, ${this.view.resolutionLine}, torches ${this.view.torches.count}`],
        ["grounds", this.groundsLine()],
        ["castle kit", `${this.library.size} shared materials, ${(this.library.textureBytes() / 1048576).toFixed(1)} MB textures (${this.library.formatSummary()}), LOD ${this.lods.count}${this.lods.isForced ? " (forced)" : ""}, ${this.library.shadeSummary()}, candles ${this.candles.movedLastFrame}/${this.candles.count} moving, zoom ${this.camera.maxDistance.toFixed(1)} m, ${this.cellsLine()}`],
        this.audioLine(),
        this.stairs.perfLine(), ["owls", this.owls.perfLine()], ["foliage", this.zones.scenes.foliage?.line ?? "none"], this.secrets.perfLine(), this.brooms.perfLine(), ...this.characters.perfLines(), ...this.effects.perfLines(), ...this.guardian.perfLines(), ...this.dueling.perfLines(), ...this.school.perfLines(), this.cup.perfLine(), ...this.residents.perfLines(), this.platesLine()],
    });
  }

  /** For the F3 overlay: line-of-sight rays for the nameplates, a frame on average since the last refresh. */
  private platesLine(): [string, string] {
    const s = this.plates.sight.stats();
    return ["nameplates", `${s.raysPerFrame.toFixed(2)} sight rays/frame, ${(s.msPerFrame * 1000).toFixed(0)} µs/frame, ${s.tracked} tracked`];
  }

  /** For the F3 overlay: the audio context, voices (and dropped), banks, the ambience bed and the music. */
  private audioLine(): [string, string] {
    const s = audio.stats();
    const cap = VOICE_CAPS[this.settings.quality];
    return ["audio", `${s.state}, ${s.voices} voices (cap ${cap.world}), ${s.dropped} dropped, banks ${s.banks.join("+") || "none"}, ${s.ambience ?? "no ambience"}, ${s.music ?? "no music"} (${s.musicVoices}/${cap.musicNotes} notes)`];
  }

  /** For the F3 overlay: outdoors or not, the far castle's chunks drawn, the fog's reach. */
  private groundsLine(): string {
    const scenes = this.zones.scenes;
    const h = scenes.hlod?.counts();
    const hlod = h ? (h.loaded ? `hlod ${h.shown}/${h.total} chunks` : "hlod loading") : "no hlod";
    return `${scenes.outdoors ? "outdoors" : "indoors"}, ${hlod}, view ${Math.round(this.view.farPlane)} m, water ${this.water.built ? "on" : "none"}`;
  }

  /** Visible / resident castle cells (castle kit M2 `sees`), for the F3 overlay. */
  private cellsLine(): string {
    const { shown, loaded } = this.zones.scenes.streamer.visibleCounts();
    return `cells ${shown}/${loaded} shown${this.zones.scenes.visibility.enabled ? "" : " (sees off)"}`;
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    window.removeEventListener("keydown", this.onKey);
    this.hudRoot.removeEventListener("click", this.onClickCapture, true);
    this.hudRoot.removeEventListener("click", this.onClick);
    this.wind = null;
    audio.dispose();
    this.conn.stop();
    this.view.engine.stopRenderLoop();
    this.instrumentation?.dispose();
    this.windows.dispose();
    this.selection.dispose();
    this.players.dispose();
    this.card.dispose();
    this.dorm.dispose();
    this.friends.dispose();
    this.economy.dispose();
    this.crafting.dispose();
    this.dueling.dispose();
    this.party.dispose();
    this.club.dispose();
    this.school.dispose();
    this.cup.dispose();
    this.secrets.dispose();
    this.residents.dispose();
    this.cupKit.dispose();
    this.effects.dispose();
    // Spells L20 (GUARDIAN)
    this.guardian.dispose();
    // Also frees the castle's and any dorm's wasm world.
    this.zones.dispose();
    this.layoutOverlay?.dispose();
    this.lods.dispose();
    this.candles.dispose();
    this.water.dispose();
    this.library.dispose();
    this.characters.dispose();
    this.brooms.dispose();
    this.stairs.dispose();
    this.owls.dispose();
    this.flightChip.root.remove();
    this.moveChip.root.remove();
    this.camera.dispose();
    this.keyboard.dispose();
    this.plates.dispose();
    this.hud.dispose();
    this.chat.dispose();
    this.view.dispose();
    this.hudRoot.replaceChildren();
    this.hudRoot.hidden = true;
    this.canvas.hidden = true;
  }
}

/** What the player hears on mounting: Madam Swift in her class, the controls anywhere else. */
function mountedText(inClass: boolean): string {
  return inClass
    ? "Madam Swift hands you a broom."
    : "You mount your broom. W and S set your speed, A and D, the arrow keys or the mouse steer, Space climbs, Shift dives. Press B near the ground to land.";
}
