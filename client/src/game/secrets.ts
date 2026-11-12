// The castle's secrets in the running game (docs/architecture.md
// "Secrets and the map"), in one place for game.ts:
//
// - The map (M, ui/map.ts): the saved reveals (`GET /api/map` once in game
//   and on each reconnect), and our own reveals predicted every frame with
//   the server's rule (`SimWorld.revealAt`: the surface we stand on, whole).
// - The hidden passages: their entrances move aside as anyone walks through
//   (castle/entrances.ts); the first time we stand in one the server says
//   `discovered`: a toast, a chat line, and the passage is on the map.
// - The Wishing Room: the server counts our paces past the blank wall
//   and says `wishingRoomDoor`; the door fades in (for us only), its prompt
//   shows, and using it offers the two rooms (ui/wishing-room.ts); the choice
//   is `EnterWishingRoom`. Inside, the dorm panel serves the room (dorm/).
//
// Nothing here names or draws a secret before it is found.

import { ApiError, authedRequest } from "../api";
import { audio } from "../audio";
import { MapModel, parseMapSave, type FriendPlace, type PartyMapMember } from "../ui/map-state";
import { MapWindow, type MapMark } from "../ui/map";
import { RoomChoiceCard } from "../ui/wishing-room";
import { discoveryText, PaceWhisper, paceWhisperText, WishingRoomDoor, roomChoices } from "../ui/wishing-room-state";
import { ClassToasts } from "../ui/wayfinding";
import { recipeById } from "../ui/crafting-content";
import { encodeEnterWishingRoom, type Interactable, type WishingRoomView, type ServerMessage, type Zone } from "../wasm/sim_wasm";
import type { KitEntrances, Feet } from "./castle/entrances";
import type { WorldData } from "./world";
import { WISHING_ROOM_DOOR } from "./zones";

/** The discovery toast stays up this long (ms). */
const DISCOVERY_TOAST_MS = 12_000;

export interface SecretsHubOptions {
  hudRoot: HTMLElement;
  hudActions: HTMLElement;
  /** The castle's world (its sim has the secrets: `setSecrets`). */
  castle: WorldData;
  /** The entrances and the door, wired by the cell streamer (`KitEntrances.forCastle`). */
  entrances: KitEntrances;
  houseName: string;
  send: (frame: Uint8Array) => void;
  notify: (text: string) => void;
  /** The server's clock now (Unix ms). */
  serverNow: () => number;
  /** The zone shown (null while one loads). */
  zone: () => Zone | null;
  /** Where moving flight `flight` stands now (`FlightView.walk`). */
  walk: (flight: string) => "a" | "b" | undefined;
  friends: () => FriendPlace[];
  /** Our party's other members for the map (game/party.ts). */
  party?: () => PartyMapMember[];
  marks: () => MapMark[];
  /** Where we stand in the castle for the map (null in a room). */
  me: () => { x: number; z: number; yaw: number; surfaceId: string | undefined } | null;
  /** We chose a room (`EnterWishingRoom` was sent): its refusal is a `DormError`. */
  onEnter: () => void;
  /** The map opened (game.ts reloads the friend list, so friends' places are current). */
  onMapOpen?: () => void;
}

export class SecretsHub {
  readonly entrances: KitEntrances;
  readonly map: MapWindow;
  private readonly model: MapModel | null;
  /** Whether the saved map has loaded (until then `found` is not known: NPCs keep their hints). */
  private mapLoaded = false;
  private readonly rules: WishingRoomView | null;
  private readonly door = new WishingRoomDoor();
  private readonly card: RoomChoiceCard;
  private readonly toasts = new ClassToasts();
  private readonly names = new Map<string, string>();
  /** The blank wall's pace marks (layout `pace_mark` areas, in the rules' order) and our passes between them (words only). */
  private paceMarks: { min: [number, number, number]; max: [number, number, number] }[] = [];
  private pace: PaceWhisper | null = null;
  private firstWhisperSaid = false;
  private mapLoad = 0;
  private time = 0;

  constructor(private readonly opts: SecretsHubOptions) {
    const sim = opts.castle.sim;
    let model: MapModel | null = null;
    let rules: WishingRoomView | null = null;
    try {
      for (const s of sim.secrets()) this.names.set(s.id, s.name);
      model = new MapModel(sim.mapSurfaces(), (id) => sim.secretOf(id), (id) => this.names.get(id));
      rules = sim.wishingRoom() ?? null;
    } catch (e) {
      console.error("The map is unavailable:", e);
    }
    this.model = model;
    this.rules = rules;
    if (rules) {
      try {
        const areas = (JSON.parse(opts.castle.layoutText) as { areas?: { id: string; min: [number, number, number]; max: [number, number, number] }[] }).areas ?? [];
        const marks = rules.marks.map((id) => areas.find((a) => a.id === id));
        if (marks.every((m) => m !== undefined)) {
          this.paceMarks = marks.map((m) => ({ min: m!.min, max: m!.max }));
          this.pace = new PaceWhisper(rules.passes, rules.window_s * 1000);
        }
      } catch (e) {
        console.warn("[secrets] no pace marks:", e);
      }
    }
    this.entrances = opts.entrances;
    this.map = new MapWindow({ model: () => this.model, me: opts.me, walk: opts.walk, friends: opts.friends, party: opts.party, marks: opts.marks, onOpen: opts.onMapOpen });
    this.card = new RoomChoiceCard((room) => {
      opts.onEnter();
      opts.send(encodeEnterWishingRoom(room));
    });
    this.toasts.root.classList.add("secret-toasts");
    opts.hudRoot.append(this.map.root, this.card.root, this.toasts.root);
    if (this.model) opts.hudActions.append(this.map.button);
  }

  // ---------------------------------------------------------------- messages

  /** `wishingRoomDoor` and `discovered`: true when `msg` was one. */
  onMessage(msg: ServerMessage): boolean {
    switch (msg.type) {
      case "wishingRoomDoor": {
        const change = this.door.set(msg.openUntilMs, this.opts.serverNow());
        if (change === "opened") {
          this.pace?.reset();
          audio.play("secret.door_appear");
          this.opts.notify("A door has appeared in the blank wall. It is there for you alone, and not for long.");
        }
        if (change === "closed") {
          audio.play("secret.door_vanish");
          this.card.close();
        }
        return true;
      }
      case "discovered":
        this.onDiscovered(msg.secret, msg.xp, msg.housePoint, msg.recipe);
        return true;
      case "welcome":
        this.door.clear();
        this.card.close();
        return false;
      default:
        return false;
    }
  }

  private onDiscovered(secret: string, xp: number, housePoint: boolean, recipe: number | null): void {
    this.model?.discover(secret);
    const name = this.names.get(secret) ?? "a hidden passage";
    const recipeName = recipe !== null ? (recipeById(recipe)?.name ?? null) : null;
    const text = discoveryText(name, xp, housePoint, recipeName, this.opts.houseName);
    audio.play("secret.reveal");
    audio.stinger("stinger.discovery");
    this.opts.notify(text);
    this.toasts.show(`secret:${secret}`, text, performance.now() + DISCOVERY_TOAST_MS, () => this.map.toggle(true), "Open the map");
    this.map.refresh();
  }

  /** In game (and after each reconnect): the saved map. A server without it (404) leaves the map to this session's own reveals. */
  async loadMap(): Promise<void> {
    const model = this.model;
    if (!model) return;
    const ticket = ++this.mapLoad;
    try {
      const save = parseMapSave(await authedRequest<unknown>("GET", "/map"));
      if (ticket === this.mapLoad) {
        model.load(save);
        this.mapLoaded = true;
      }
      this.map.refresh();
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) console.info("[map] the server keeps no map yet; showing this session's reveals");
      else console.warn("[map] could not load the saved map:", e);
    }
  }

  // ---------------------------------------------------------------- the door

  /** Whether the zone director may offer `it` now: the Room's door only while it is open for us (and its card is not up). */
  offers(it: Interactable): boolean {
    return it.kind !== WISHING_ROOM_DOOR || (this.door.isOpen(this.opts.serverNow()) && !this.card.isOpen);
  }

  /** Using the Room's door (client only, like the notice board): the room choice. True when handled. */
  localInteract(it: Interactable): boolean {
    if (it.kind !== WISHING_ROOM_DOOR) return false;
    const now = this.opts.serverNow();
    if (!this.door.isOpen(now)) return true;
    this.card.open(roomChoices(this.rules), this.door.secondsLeft(now));
    return true;
  }

  // ---------------------------------------------------------------- per frame

  /**
   * Once a frame after our move. `me` our feet in the castle (null in a room
   * or while flying), `feet` everyone drawn in the castle (the entrances).
   */
  frame(dt: number, nowMs: number, me: readonly [number, number, number] | null, feet: Feet): void {
    this.time += dt;
    const castle = this.opts.zone()?.kind === "castle";
    const sim = this.opts.castle.sim;
    if (castle && me) {
      const surface = sim.revealAt(me[0], me[1], me[2]);
      this.model?.reveal(surface);
      this.paceFrame(surface, me, nowMs);
    } else {
      this.pace?.reset();
    }
    this.entrances.update(dt, this.time, castle ? feet : [], castle ? this.door.alpha(this.opts.serverNow()) : 0);
  }

  /** The blank wall's whispers: our passes between its pace marks, counted with the server's rule (words only). */
  private paceFrame(surface: string | undefined, me: readonly [number, number, number], nowMs: number): void {
    const pace = this.pace;
    const rules = this.rules;
    if (!pace || !rules) return;
    if (surface !== rules.corridor) return pace.reset();
    const k = this.paceMarks.findIndex((a) => me[0] >= a.min[0] && me[0] <= a.max[0] && me[1] >= a.min[1] && me[1] <= a.max[1] && me[2] >= a.min[2] && me[2] <= a.max[2]);
    const n = pace.step(k === 0 || k === 1 ? k : null, nowMs);
    if (n === 1 && this.firstWhisperSaid) return;
    if (this.door.isOpen(this.opts.serverNow())) return;
    const text = paceWhisperText(n, rules.passes);
    if (!text) return;
    if (n === 1) this.firstWhisperSaid = true;
    audio.play("secret.pace");
    this.opts.notify(text);
  }

  /** A few times a second: the map (while open), the card's countdown and the toasts. */
  hudTick(): void {
    this.map.refresh();
    if (this.card.isOpen) this.card.tick(this.door.secondsLeft(this.opts.serverNow()));
    this.toasts.tick(performance.now());
  }

  handleKey(e: KeyboardEvent): boolean {
    return this.card.handleKey(e) || this.map.handleKey(e);
  }

  /** The secrets we found (`GET /api/map`; NPCs' hints only point at the others), or null until the saved map has loaded. */
  get found(): ReadonlySet<string> | null {
    return this.mapLoaded ? (this.model?.found ?? null) : null;
  }

  /** The F3 overlay's line. */
  perfLine(): [string, string] {
    const c = this.entrances.counts;
    const m = this.model;
    return ["secrets", `entrances ${c.open}/${c.wired} open, map ${m ? `${m.revealed.size} revealed, ${m.found.size} found` : "off"}, door ${this.door.isOpen(this.opts.serverNow()) ? "open" : "shut"}`];
  }

  dispose(): void {
    this.mapLoad++;
    this.entrances.dispose();
    this.map.root.remove();
    this.map.button.remove();
    this.card.root.remove();
    this.toasts.root.remove();
  }
}
