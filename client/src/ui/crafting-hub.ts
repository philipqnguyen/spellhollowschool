// Crafting, potions and finds in the running game, in one place for
// game.ts: the crafting window opened by stations (`StationOpened`), the
// prompt for a cauldron placed in a dorm (`UseFurniture`), the potions
// satchel and the Mimic Draught consent prompt, the skills panel, our effect
// timers, and exploration finds (discovery toasts, and which spots should
// stop shimmering). game.ts routes the matching server messages here; the
// effects on characters themselves are game/effects.
//
// A station grant lasts while the player stays in reach (plus a metre of
// slack, as the server checks); walking away closes the window here too,
// so it never sits open on requests the server would refuse.

import findsJson from "../../../data/world/finds.json";
import { audio } from "../audio";
import type { EffectsController } from "../game/effects/controller";
import type { WorldData } from "../game/world";
import { zoneKey } from "../game/zones";
import { encodeUseFurniture, encodeMimicDraughtAnswer, type Interactable, type ServerMessage, type Vec3, type Zone } from "../wasm/sim_wasm";
import * as craftingApi from "./crafting-api";
import { def1c, itemName } from "./crafting-content";
import { CraftingWindow, type OpenStation } from "./crafting";
import { h } from "./dom";
import { EffectTimers, MimicDraughtConsent } from "./effects";
import { FindToasts } from "./finds";
import { FindCooldowns, secondsUntil } from "./finds-state";
import { PotionsWindow } from "./potions";
import { mimicDraughtCandidates } from "./potions-state";
import { SkillsPanel } from "./skills";
import type { Appearance } from "../wasm/sim_wasm";

/** The server's slack on top of a station's reach (`REACH_SLACK_M`). */
const REACH_SLACK_M = 1;
/** A placed cauldron is in reach within this distance of its origin (m), as the server checks. */
const FURNITURE_REACH_M = 1.5;
const FIND_COOLDOWN_SECS: number = findsJson.cooldown_secs;
const UPDATE_INTERVAL = 0.25;

/** A placed dorm item, as the dorm room knows it. */
export interface PlacedItem {
  itemId: number;
  itemDef: number;
  pos: Vec3;
}

export interface CraftingHubOptions {
  hudRoot: HTMLElement;
  /** The HUD bar's button row. */
  hudActions: HTMLElement;
  effects: EffectsController;
  send: (frame: Uint8Array) => void;
  notify: (text: string) => void;
  appearance: () => Appearance | null;
  /** The zone shown and its world (interactables), null while loading. */
  zone: () => Zone | null;
  world: () => WorldData;
  /** Furniture placed in the dorm the player stands in. */
  dormItems: () => Iterable<PlacedItem>;
  /** Characters in view with their distance from us (m). */
  nearby: () => { id: number; name: string; distance: number }[];
  isFriend: (name: string) => boolean;
  /** Play the wand-wave emote while a craft is charmed. */
  onCharm: () => void;
  /** The room name where we stand, for a find toast. */
  placeName: () => string | null;
}

export class CraftingHub {
  readonly crafting: CraftingWindow;
  readonly potions: PotionsWindow;
  readonly skills: SkillsPanel;
  private readonly timers: EffectTimers;
  private readonly consent: MimicDraughtConsent;
  private readonly finds = new FindToasts();
  private readonly cooldowns = new FindCooldowns();
  private readonly cauldronPrompt: HTMLButtonElement;
  private nearCauldron: PlacedItem | null = null;
  private interactUntil = 0;
  private timer = 0;
  /** Where the open station is (for walking away). */
  private stationAt: { pos: Vec3; reach: number; zoneKey: string } | null = null;

  constructor(private readonly opts: CraftingHubOptions) {
    this.crafting = new CraftingWindow({
      appearance: opts.appearance,
      notify: opts.notify,
      onCharm: opts.onCharm,
      onLook: () => undefined, // the server announces our new look (AppearanceChanged)
      onSkill: (s) => this.skills.skillChanged(s),
      onLearned: () => void this.skills.reload(),
    });
    this.potions = new PotionsWindow({
      send: opts.send,
      notify: opts.notify,
      own: opts.effects.own,
      candidates: () => mimicDraughtCandidates(opts.nearby(), opts.isFriend),
    });
    this.skills = new SkillsPanel({ notify: opts.notify, onLearned: () => this.crafting.refresh() });
    this.timers = new EffectTimers(opts.effects.own, () => opts.effects.ownGuardianGuarding);
    this.consent = new MimicDraughtConsent({ onAnswer: (id, accept) => opts.send(encodeMimicDraughtAnswer(id, accept)) });
    this.cauldronPrompt = h("button", { type: "button", class: "interact-prompt panel station-prompt", hidden: true, onclick: () => this.useCauldron() }, h("kbd", {}, "E"), h("span", {}, "Brew"));
    opts.hudActions.append(this.potions.button, this.skills.button);
    opts.hudRoot.append(this.timers.root, this.consent.root, this.finds.root, this.cauldronPrompt, this.crafting.root, this.potions.root, this.skills.root);
  }

  /** After the first Welcome: which find spots we already searched today. */
  async start(): Promise<void> {
    try {
      const now = Date.now();
      for (const c of await craftingApi.findCooldowns()) this.markCooling(c.spot, secondsUntil(c.ready_at, now));
    } catch (e) {
      // Without the list every spot shimmers; searching one again just says "come back later".
      console.warn("[finds] could not read find cooldowns:", e);
    }
  }

  // ---------------------------------------------------------------- messages

  /** The crafting side's server messages. Returns whether `msg` was one. */
  onMessage(msg: ServerMessage): boolean {
    switch (msg.type) {
      case "stationOpened":
        if (msg.kind === "owl_exchange" || msg.kind === "owl_order") return false; // the Exchange and the catalogue are the economy UI's
        this.openStation(msg.kind, msg.target);
        return true;
      case "findResult":
        this.onFind(msg.spot, msg.outcome);
        return true;
      case "drank":
        audio.play("potion.drink");
        this.potions.drank(msg.effect);
        return true;
      case "potionError":
        this.potions.potionError(msg.code);
        return true;
      case "mimicDraughtRequest":
        this.consent.add(msg.requestId, msg.fromName, msg.expiresInSecs);
        return true;
      case "mimicDraughtDeclined":
        this.potions.declined(msg.byName);
        return true;
      default:
        return false;
    }
  }

  private openStation(kind: OpenStation["kind"], target: string): void {
    const zone = this.opts.zone();
    let title = "Crafting";
    let at: { pos: Vec3; reach: number } | null = null;
    if (target.startsWith("item:")) {
      const id = Number(target.slice(5));
      const item = [...this.opts.dormItems()].find((i) => i.itemId === id);
      title = item ? itemName(item.itemDef) : "Cauldron";
      if (item) at = { pos: item.pos, reach: FURNITURE_REACH_M };
    } else {
      const it = this.interactable(target);
      title = it?.name ?? title;
      if (it) at = { pos: it.pos, reach: it.radius };
    }
    this.stationAt = at && zone ? { ...at, zoneKey: zoneKey(zone) } : null;
    this.potions.toggle(false);
    this.skills.toggle(false);
    audio.play("craft.station_open");
    this.crafting.open({ kind, target, title });
  }

  private interactable(id: string): Interactable | undefined {
    return this.opts.world().sim.interactables().find((i) => i.id === id);
  }

  private onFind(spot: string, outcome: Extract<ServerMessage, { type: "findResult" }>["outcome"]): void {
    // Sound: rummaging, then what came of it.
    if (outcome.kind === "found" || outcome.kind === "nothing") {
      audio.play("find.search");
      audio.play(outcome.kind === "found" ? "find.found" : "find.nothing", { delayS: 0.45 });
    }
    this.finds.show(outcome, this.opts.placeName());
    if (outcome.kind === "cooldown") this.markCooling(spot, outcome.readyInSecs);
    else if (outcome.kind !== "failed") this.markCooling(spot, FIND_COOLDOWN_SECS);
    if (outcome.kind === "found") {
      const list = [...outcome.items.map((i) => `${i.quantity > 1 ? `${i.quantity} × ` : ""}${itemName(i.itemDef)}`), ...(outcome.crowns > 0 ? [`${outcome.crowns} Crowns`] : [])];
      if (list.length) this.opts.notify(`You found ${list.join(", ")}.`);
    }
  }

  private markCooling(spot: string, secs: number): void {
    if (secs <= 0) return;
    this.cooldowns.set(spot, secs, Date.now());
    this.opts.effects.setCooling(spot, true);
  }

  /** A zone is shown: its find spots shimmer (the castle has them; dorms do not). */
  zoneShown(world: WorldData): void {
    const spots = world.sim
      .interactables()
      .filter((i) => i.kind === "find_spot")
      .map((i) => ({ id: i.id, pos: i.pos }));
    this.opts.effects.setFindSpots(spots);
    this.crafting.close();
    this.stationAt = null;
  }

  // ---------------------------------------------------------------- per frame

  /** Per frame with the player's feet: the dorm cauldron prompt, walking away from a station, timers. */
  update(feet: Vec3, dt: number): void {
    this.timer -= dt;
    if (this.timer > 0) return;
    this.timer = UPDATE_INTERVAL;
    const now = performance.now();
    this.timers.update(now);
    for (const spot of this.cooldowns.expire(Date.now())) this.opts.effects.setCooling(spot, false);

    const zone = this.opts.zone();
    // A placed cauldron in reach (in any dorm we stand in).
    let near: PlacedItem | null = null;
    if (zone?.kind === "dorm") {
      let best = FURNITURE_REACH_M;
      for (const item of this.opts.dormItems()) {
        if (def1c(item.itemDef)?.furniture?.station !== "cauldron") continue;
        const d = Math.hypot(item.pos[0] - feet[0], item.pos[2] - feet[2]);
        if (d <= best && Math.abs(item.pos[1] - feet[1]) <= 1.5) {
          best = d;
          near = item;
        }
      }
    }
    if (near?.itemId !== this.nearCauldron?.itemId) {
      this.nearCauldron = near;
      this.cauldronPrompt.hidden = !near;
    }

    // Walking away from the open station closes it (the server's grant ends there too).
    const at = this.stationAt;
    if (this.crafting.isOpen && at) {
      const gone = !zone || zoneKey(zone) !== at.zoneKey || Math.hypot(at.pos[0] - feet[0], at.pos[2] - feet[2]) > at.reach + REACH_SLACK_M || Math.abs(at.pos[1] - feet[1]) > 2.5;
      if (gone) {
        this.stationAt = null;
        this.crafting.close(`You step away from the ${this.crafting.openStation?.title.toLowerCase() ?? "station"}.`);
      }
    }
  }

  private useCauldron(): boolean {
    const c = this.nearCauldron;
    if (!c) return false;
    const now = performance.now();
    if (now < this.interactUntil) return true;
    this.interactUntil = now + 1500;
    this.opts.send(encodeUseFurniture(c.itemId));
    return true;
  }

  /** E at a placed cauldron; P, K and Escape for the windows. */
  handleKey(e: KeyboardEvent): boolean {
    if (this.crafting.handleKey(e) || this.potions.handleKey(e) || this.skills.handleKey(e)) return true;
    if (e.code === "KeyE" && !e.ctrlKey && !e.metaKey && !e.altKey) return this.useCauldron();
    return false;
  }

  /** Our look changed (the crafting preview follows). */
  appearanceChanged(): void {
    this.crafting.appearanceChanged();
  }

  dispose(): void {
    this.crafting.dispose();
    this.potions.dispose();
    this.consent.dispose();
    this.finds.dispose();
  }
}
