// Runs zone changes for the game: on `Welcome` and `ZoneChanged` it fades
// out, loads the new zone's scene (zones-scene.ts), swaps it in and tells
// the game to switch prediction to the new world and place the player, then
// fades back in. Overtaken loads are dropped (ZoneMachine, zones.ts).
//
// Also the door prompt: near an interactable the player can use, a
// "E  Enter your dormitory" prompt shows; E (or a click on it) sends
// `Interact`. A common room's notice board is read on the
// client only (`onLocalInteract`): the server would answer nothing.

import { audio, ZONE_DOOR_SOUND, type SoundId } from "../audio";
import { encodeInteract, type Interactable, type Zone } from "../wasm/sim_wasm";
import { h } from "../ui/dom";
import type { WorldData } from "./world";
import { ZoneMachine, interactPrompt, type InteractPrompt, type ZoneArrival } from "./zones";
import { ZoneScenes, type ZoneScenesOptions } from "./zones-scene";

export interface ZoneDirectorHooks {
  /** A new zone begins (remotes, prediction and the dorm room should be dropped). Runs synchronously on arrival. */
  onLeaving(next: Zone): void;
  /** `world` is shown: predict with it and put the player at `arrival`. `changed` is false for a re-arrival in the same zone. */
  onShown(world: WorldData, arrival: ZoneArrival, changed: boolean): void;
  /** The zone could not be loaded. */
  onFailed(error: unknown): void;
  send(frame: Uint8Array): void;
  /** An interactable used on the client alone (a notice board): true when it was handled, and nothing is sent. */
  onLocalInteract?(it: Interactable): boolean;
  /** Whether `it` may be offered now (the Wishing Room's door only while it is open for us). */
  offers?(it: Interactable): boolean;
}

/** How often the prompt and streaming are updated (s). */
const PROMPT_INTERVAL = 0.2;
const STREAM_INTERVAL = 0.5;
/** After sending `Interact`, wait this long before allowing another (ms). */
const INTERACT_COOLDOWN_MS = 1500;
/** Sound: a zone change this soon after using a door is heard as that door (ms). */
const DOOR_HEARD_MS = 4000;

export class ZoneDirector {
  readonly scenes: ZoneScenes;
  private readonly machine = new ZoneMachine();
  private readonly prompt: HTMLElement;
  private readonly promptLabel: HTMLElement;
  private current: InteractPrompt | null = null;
  private interactables: Interactable[] = [];
  private promptTimer = 0;
  private streamTimer = 0;
  private interactUntil = 0;
  /** Sound: the zone door last used and when (local ms); heard if the zone changes soon after. */
  private doorUsed: { sound: SoundId; at: number } | null = null;
  /**
   * Set by `dispose`. A zone load still running then (a "Signed in
   * elsewhere" kick during the first load) must not touch the scenes or
   * their wasm worlds, which the dispose has freed ("null pointer passed to
   * rust"): it drops what it loaded and stops.
   */
  private disposed = false;

  constructor(
    scenes: ZoneScenesOptions,
    hudRoot: HTMLElement,
    private readonly myName: string,
    private readonly hooks: ZoneDirectorHooks,
  ) {
    this.scenes = new ZoneScenes(scenes);
    this.promptLabel = h("span", {}, "");
    this.prompt = h("button", { type: "button", class: "interact-prompt panel", hidden: true, onclick: () => this.interact() }, h("kbd", {}, "E"), this.promptLabel);
    hudRoot.append(this.prompt);
  }

  /** A scene is shown and nothing is loading: the player may move. */
  get ready(): boolean {
    return this.machine.ready;
  }

  get zone(): Zone | null {
    return this.machine.zone;
  }

  /** Whether a door's or station's prompt is up (E is its: an NPC's talk prompt hides meanwhile). */
  get prompting(): boolean {
    return this.current !== null;
  }

  get world(): WorldData {
    return this.scenes.world;
  }

  /** `Welcome` or `ZoneChanged`. Resolves when the zone is shown (or the load was overtaken). */
  async arrive(arrival: ZoneArrival): Promise<void> {
    const first = this.machine.phase === "none";
    const step = this.machine.arrive(arrival);
    if (step.kind === "wait") return;
    if (step.kind === "place") {
      this.hooks.onShown(this.scenes.world, step.arrival, false);
      return;
    }
    this.hooks.onLeaving(arrival.zone);
    // Sound: the door used to leave (a class, a dorm, the Room), heard as the fade starts.
    const door = this.doorUsed;
    this.doorUsed = null;
    if (door && performance.now() - door.at < DOOR_HEARD_MS) audio.play(door.sound);
    this.setPrompt(null);
    const fade = first ? Promise.resolve() : this.scenes.fadeOut(caption(arrival.zone, this.myName));
    let prepared;
    try {
      [prepared] = await Promise.all([this.scenes.prepare(arrival.zone, arrival.spawn), fade]);
    } catch (e) {
      // After a dispose the failure is the dispose's own ("disposed"): nothing to report.
      if (!this.disposed && this.machine.isCurrent(step.ticket)) this.hooks.onFailed(e);
      return;
    }
    if (this.disposed) {
      prepared.dispose(); // a room's shell and world are still the load's own (the castle's: nothing to do)
      return;
    }
    const settled = this.machine.loaded(step.ticket);
    if (!settled) {
      prepared.dispose(); // a newer arrival took over
      return;
    }
    prepared.activate();
    this.interactables = prepared.world.sim.interactables();
    this.hooks.onShown(prepared.world, settled, true);
    this.scenes.fadeIn();
  }

  /** Per frame with the player's feet: castle streaming and the door prompt. */
  update(pos: [number, number, number], dt: number): void {
    if (this.disposed) return;
    this.streamTimer -= dt;
    if (this.streamTimer <= 0 && this.ready) {
      this.streamTimer = STREAM_INTERVAL;
      this.scenes.update(pos);
    }
    this.promptTimer -= dt;
    if (this.promptTimer <= 0) {
      this.promptTimer = PROMPT_INTERVAL;
      const sim = this.scenes.world.sim;
      const offers = this.hooks.offers;
      const list = offers ? this.interactables.filter((it) => offers(it)) : this.interactables;
      this.setPrompt(this.ready ? interactPrompt(list, pos, (id) => sim.inReach(id, pos[0], pos[1], pos[2]), this.zone, this.myName) : null);
    }
  }

  private setPrompt(p: InteractPrompt | null): void {
    if (p?.id === this.current?.id && p?.label === this.current?.label) return;
    this.current = p;
    this.prompt.hidden = !p;
    if (p) this.promptLabel.textContent = p.label;
  }

  /** Uses the interactable in reach, if any. */
  interact(): boolean {
    if (this.disposed || !this.current || !this.ready) return false;
    const now = performance.now();
    if (now < this.interactUntil) return true;
    const it = this.interactables.find((i) => i.id === this.current?.id);
    if (it && this.hooks.onLocalInteract?.(it)) return true;
    this.interactUntil = now + INTERACT_COOLDOWN_MS;
    const sound = it ? ZONE_DOOR_SOUND[it.kind] : undefined;
    this.doorUsed = sound ? { sound, at: now } : null;
    this.hooks.send(encodeInteract(this.current.id));
    this.prompt.setAttribute("aria-busy", "true");
    setTimeout(() => this.prompt.removeAttribute("aria-busy"), INTERACT_COOLDOWN_MS);
    return true;
  }

  /** E uses the door in reach. */
  handleKey(e: KeyboardEvent): boolean {
    return e.code === "KeyE" && this.interact();
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    this.scenes.dispose();
    this.prompt.remove();
  }
}

/** The line shown while a zone loads. */
function caption(zone: Zone, myName: string): string {
  if (zone.kind === "castle") return "Returning to the castle";
  if (zone.kind === "class") return "Entering the classroom";
  if (zone.kind === "arena") return "Stepping into the dueling arena";
  if (zone.kind === "wishing_room") return zone.ownerName === myName ? "The Wishing Room" : `${zone.ownerName}'s Wishing Room`;
  return zone.ownerName === myName ? "Your dormitory" : `${zone.ownerName}'s room`;
}
