// Everything about dorm rooms on the client: the room's furniture (state,
// rendering, decorating), the room panel and the decorating strip, and dorm
// invitations. game.ts routes the dorm messages here and tells it when the
// player leaves a zone (`leaving`) and when a new one is shown (`entered`).
//
// `DormState` follows every arrival in a dorm and may come before the room's
// scene has loaded; the room state takes it at once and the renderer draws
// it when the scene is shown.

import type { Camera } from "@babylonjs/core/Cameras/camera";
import type { Scene } from "@babylonjs/core/scene";
import {
  encodeDormAcceptInvite,
  encodeDormDeclineInvite,
  encodeDormInvite,
  encodeDormKick,
  encodeDormSetOpen,
  encodeDormVisit,
  encodeLeaveInstance,
  type ServerMessage,
  type Vec3,
  type Zone,
} from "../../wasm/sim_wasm";
import { DecorStrip, DormPanel, type Occupant } from "../../ui/dorm";
import { loadIconManifest } from "../../economy/icons";
import type { SeatLookup } from "../characters/cast";
import { InviteToasts } from "../../ui/invites";
import type { WorldData } from "../world";
import { dormRole, roomRole } from "../zones";
import { wishingRoomName } from "../../ui/wishing-room-state";
import { furnitureDef } from "./catalog";
import { DecorateMode } from "./decorate";
import { FurnitureRenderer } from "./furniture";
import { buildRows, loadAllFurniture, type CategoryFilter } from "./furniture-list";
import { listFurniture, type FurnitureItem } from "./inventory";
import { dormErrorText, isDecorError, wishingRoomErrorText } from "./messages";
import { YAW_UNITS } from "./placement";
import { DormRoom } from "./room";
import { FurnitureSeats } from "./seats";

type DormMessage = Extract<
  ServerMessage,
  { type: "dormState" | "dormFurniture" | "dormOpenChanged" | "dormInviteReceived" | "dormInviteDeclined" | "dormError" }
>;

export interface DormControllerOptions {
  scene: Scene;
  canvas: HTMLCanvasElement;
  camera: Camera;
  /** Where the panels go (the HUD layer). */
  hudRoot: HTMLElement;
  /** `assets/` (a logical path, src/assets.ts), furniture asset keys resolve under it. */
  assetsBase: string;
  myName: string;
  send: (frame: Uint8Array) => void;
  /** A line in the chat log. */
  notify: (text: string) => void;
  /** Other characters in the zone (the room's occupants while in one). */
  occupants: () => Occupant[];
  /** Where everyone in the zone stands, ourselves included (solid pieces may not go on anyone). */
  people: () => Vec3[];
  /** Names of online friends, for the invite list. */
  invitable: () => string[];
}

/** How long a status line stays on the strip (ms). */
const STATUS_MS = 4000;
/** Reload the list this long after the last confirmed edit (ms). */
const RELOAD_MS = 250;

export class DormController {
  readonly room = new DormRoom();
  private zone: Zone | null = null;
  private world: WorldData | null = null;
  private renderer: FurnitureRenderer | null = null;
  private decorate: DecorateMode | null = null;
  private readonly panel: DormPanel;
  private readonly strip: DecorStrip;
  private readonly invites: InviteToasts;
  /** The trunk's furniture (every page), the search and the category shown. */
  private stripItems: FurnitureItem[] = [];
  private query = "";
  private category: CategoryFilter = "all";
  private stripLoading = false;
  private stripLoad = 0;
  private reloadTimer: ReturnType<typeof setTimeout> | null = null;
  private status = "";
  private statusTimer: ReturnType<typeof setTimeout> | null = null;
  /** The friend named by the last invite or visit, for error messages. */
  private lastTarget: string | undefined;
  /** When we last asked to enter the Wishing Room (its refusals are `DormError`s). */
  private enteringAt = -Infinity;
  private panelKey = "";

  constructor(private readonly opts: DormControllerOptions) {
    this.panel = new DormPanel({
      onDecorate: () => this.toggleDecorating(),
      onSetOpen: (open) => opts.send(encodeDormSetOpen(open)),
      onInvite: (name) => this.invite(name),
      onKick: (entity) => opts.send(encodeDormKick(entity)),
      onLeave: () => opts.send(encodeLeaveInstance()),
    });
    this.strip = new DecorStrip({
      onPick: (item) => this.decorate?.selectFromInventory(item),
      onSearch: (query) => {
        this.query = query;
        this.updateStrip();
      },
      onCategory: (category) => {
        this.category = category;
        this.updateStrip();
      },
      onRotate: (dir) => this.decorate?.rotate(dir),
      onPrecise: () => this.decorate?.togglePrecise(),
      onPutAway: () => this.decorate?.putAway(),
      onCancel: () => this.decorate?.cancel(),
      onDone: () => this.toggleDecorating(false),
    });
    this.invites = new InviteToasts({
      onAccept: (id) => opts.send(encodeDormAcceptInvite(id)),
      onDecline: (id) => opts.send(encodeDormDeclineInvite(id)),
    });
    this.panel.root.hidden = true;
    this.strip.root.hidden = true;
    opts.hudRoot.append(this.panel.root, this.strip.root, this.invites.root);
  }

  /** The owner of the dorm shown (decorating is a dorm owner's). */
  get isOwner(): boolean {
    return dormRole(this.zone, this.opts.myName) === "owner";
  }

  /** The owner of the room shown, a dorm or the Wishing Room (invites, open to friends, sending out). */
  get isRoomOwner(): boolean {
    return roomRole(this.zone, this.opts.myName) === "owner";
  }

  get decorating(): boolean {
    return this.decorate?.active ?? false;
  }

  /** The seats a sitter in this dorm is drawn on: the placed furniture's, then `base` (the room's own). */
  seats(base: SeatLookup | null): SeatLookup {
    return new FurnitureSeats(this.room, base);
  }

  /** Invites a friend to your room (works from anywhere). */
  invite(name: string): void {
    this.lastTarget = name;
    this.enteringAt = -Infinity;
    this.opts.send(encodeDormInvite(name));
    // From inside your own Wishing Room the invitation is into it (the server's rule).
    const into = this.zone?.kind === "wishing_room" && this.isRoomOwner ? "your Wishing Room" : "your room";
    this.opts.notify(`You invited ${name} to ${into}.`);
  }

  /** Walks into a friend's room, if it is open to friends. */
  visit(name: string): void {
    this.lastTarget = name;
    this.enteringAt = -Infinity;
    this.opts.send(encodeDormVisit(name));
  }

  /** We asked to enter the Wishing Room: a refusal in the next few seconds is about that. */
  enteringWishingRoom(): void {
    this.enteringAt = performance.now();
  }

  // ---------------------------------------------------------------- zones

  /** The player is leaving the zone shown: tear down the room. */
  leaving(): void {
    // A guest can be sent back without asking (the owner went offline or
    // sent them out); say where they were, so the jump is not a mystery.
    if (this.zone?.kind === "dorm" && dormRole(this.zone, this.opts.myName) === "guest") {
      this.opts.notify(`Back from ${this.zone.ownerName}'s room.`);
    }
    if (this.zone?.kind === "wishing_room" && roomRole(this.zone, this.opts.myName) === "guest") {
      this.opts.notify(`Back from ${this.zone.ownerName}'s Wishing Room.`);
    }
    this.toggleDecorating(false);
    this.decorate?.dispose();
    this.decorate = null;
    this.renderer?.dispose();
    this.renderer = null;
    this.room.clear();
    this.zone = null;
    this.world = null;
    this.panel.root.hidden = true;
  }

  /** A zone is shown; in a dorm, draw it and show the panel. */
  entered(zone: Zone, world: WorldData): void {
    this.zone = zone;
    this.world = world;
    // A Wishing Room shows the panel (invite, open to friends, send out, leave); no furniture.
    if (zone.kind === "wishing_room") {
      this.panel.root.hidden = false;
      this.panelKey = "";
      this.refreshPanel();
      return;
    }
    if (zone.kind !== "dorm") return;
    this.renderer ??= new FurnitureRenderer(this.opts.scene, this.opts.assetsBase, this.room);
    this.renderer.sync(true);
    this.syncSolids();
    if (this.isOwner) {
      this.decorate = new DecorateMode(this.opts.scene, this.opts.canvas, this.opts.camera, this.room, this.renderer, {
        sim: () => this.world!.sim,
        people: () => this.opts.people(),
        send: this.opts.send,
        notify: (text) => this.setStatus(text),
        onChange: () => this.updateStrip(),
      });
    }
    this.panel.root.hidden = false;
    this.panelKey = "";
    this.refreshPanel();
  }

  /**
   * Solid furniture: the room's placed pieces are solid in the world the
   * player predicts with, exactly as the server has them (a dorm only; the
   * room state may arrive before the zone is shown, and `entered` catches up).
   */
  private syncSolids(): void {
    if (this.zone?.kind !== "dorm" || !this.world) return;
    try {
      this.world.sim.setFurniture(this.room.pieces());
    } catch (e) {
      console.error("[dorm] solid furniture:", e);
    }
  }

  // ---------------------------------------------------------------- messages

  onMessage(msg: DormMessage): void {
    switch (msg.type) {
      case "dormState":
        this.room.reset(msg);
        this.syncSolids();
        this.decorate?.resync();
        if (this.decorating) this.scheduleStripReload();
        this.panelKey = "";
        break;
      case "dormFurniture": {
        const change = this.room.apply(msg.edit);
        this.syncSolids();
        this.renderer?.sync();
        if (change.kind !== "none") {
          this.decorate?.confirmed(change.item.itemId);
          if (this.decorating) this.scheduleStripReload();
        }
        this.updateStrip();
        break;
      }
      case "dormOpenChanged":
        this.room.openToFriends = msg.open;
        this.panelKey = "";
        if (this.isRoomOwner) this.opts.notify(msg.open ? "Your room is open to friends." : "Your room is closed to visitors.");
        break;
      case "dormInviteReceived":
        this.invites.add(msg.inviteId, msg.fromName, msg.expiresInSecs, msg.wishingRoom ? wishingRoomName(msg.wishingRoom) : null);
        break;
      case "dormInviteDeclined":
        this.opts.notify(`${msg.byName} declined your invitation.`);
        break;
      case "dormError": {
        if (performance.now() - this.enteringAt < 5000 && msg.itemId === undefined) {
          this.enteringAt = -Infinity;
          this.opts.notify(wishingRoomErrorText(msg.code));
          break;
        }
        const text = dormErrorText(msg.code, this.lastTarget);
        if (isDecorError(msg.code) || msg.itemId !== undefined) {
          this.decorate?.failed(msg.itemId);
          if (this.decorating) {
            this.setStatus(text);
            this.scheduleStripReload();
            break;
          }
        }
        this.opts.notify(text);
        break;
      }
    }
    this.refreshPanel();
  }

  // ---------------------------------------------------------------- frame

  /** Draws a placed piece `dy` m above its place (Elevara); 0 puts it back. */
  liftFurniture(itemId: number, dy: number): void {
    this.renderer?.setLift(itemId, dy);
  }

  /** Per frame: furniture follows the room state, the ghost follows the cursor. */
  update(): void {
    this.renderer?.sync();
    this.decorate?.update();
  }

  /** A few times a second: the panel's occupant and friend lists. */
  refreshPanel(): void {
    const zone = this.zone;
    if (this.panel.root.hidden || (zone?.kind !== "dorm" && zone?.kind !== "wishing_room")) return;
    const occupants = this.opts.occupants();
    const invitable = this.isRoomOwner ? this.opts.invitable() : [];
    const key = `${this.room.openToFriends}|${this.decorating}|${occupants.map((o) => `${o.id}:${o.name}`).join(",")}|${invitable.join(",")}`;
    if (key === this.panelKey) return;
    this.panelKey = key;
    this.panel.update({
      ownerName: zone.ownerName,
      owner: this.isRoomOwner,
      wishingRoom: zone.kind === "wishing_room" ? wishingRoomName(zone.room) : undefined,
      openToFriends: this.room.openToFriends,
      decorating: this.decorating,
      occupants,
      invitable,
    });
  }

  handleKey(e: KeyboardEvent): boolean {
    if (this.decorate?.handleKey(e)) {
      if (!this.decorating) this.toggleDecorating(false); // Esc ended it
      return true;
    }
    if (e.code === "KeyB" && this.isOwner) {
      this.toggleDecorating();
      return true;
    }
    return false;
  }

  // ---------------------------------------------------------------- decorating

  private toggleDecorating(on = !this.decorating): void {
    const d = this.decorate;
    if (!d) return;
    if (on) {
      d.start();
      this.strip.root.hidden = false;
      loadIconManifest();
      this.loadStrip();
    } else {
      d.stop();
      this.strip.root.hidden = true;
    }
    this.panelKey = "";
    this.refreshPanel();
  }

  private loadStrip(): void {
    const ticket = ++this.stripLoad;
    this.stripLoading = true;
    this.updateStrip();
    loadAllFurniture((after) => listFurniture(after))
      .then((items) => {
        if (ticket !== this.stripLoad) return;
        this.stripItems = items.filter((i) => furnitureDef(i.itemDef));
        this.stripLoading = false;
        this.updateStrip();
      })
      .catch((e: unknown) => {
        if (ticket !== this.stripLoad) return;
        this.stripLoading = false;
        this.setStatus(e instanceof Error ? e.message : "Could not open your trunk.");
      });
  }

  private scheduleStripReload(): void {
    if (this.reloadTimer !== null) clearTimeout(this.reloadTimer);
    this.reloadTimer = setTimeout(() => {
      this.reloadTimer = null;
      if (this.decorating) this.loadStrip();
    }, RELOAD_MS);
  }

  private setStatus(text: string): void {
    this.status = text;
    if (this.statusTimer !== null) clearTimeout(this.statusTimer);
    this.statusTimer = setTimeout(() => {
      this.status = "";
      this.statusTimer = null;
      this.updateStrip();
    }, STATUS_MS);
    this.updateStrip();
  }

  private updateStrip(): void {
    const d = this.decorate;
    if (!d || this.strip.root.hidden) return;
    const sel = d.selection;
    this.strip.update({
      rows: buildRows(this.stripItems, this.room.items.values(), d.pending),
      loading: this.stripLoading,
      query: this.query,
      category: this.category,
      selected: sel ? { itemId: sel.itemId, existing: sel.existing, name: furnitureDef(sel.itemDef)?.name ?? "This piece" } : null,
      count: this.room.count,
      cap: this.room.cap,
      precise: d.precise,
      degrees: Math.round((d.yawUnits * 3600) / YAW_UNITS) / 10,
      status: this.status,
    });
  }

  dispose(): void {
    this.leaving();
    if (this.reloadTimer !== null) clearTimeout(this.reloadTimer);
    if (this.statusTimer !== null) clearTimeout(this.statusTimer);
    this.invites.dispose();
    this.panel.root.remove();
    this.strip.root.remove();
    this.invites.root.remove();
  }
}
