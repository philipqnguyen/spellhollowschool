// Emotes: wave, bow and sit, from the HUD's emote bar or the chat box
// (/wave, /bow, /sit). The server plays them for everyone else through the
// character's snapshot `anim` (docs/architecture.md, "Anims and emotes"); `LocalEmote`
// plays the same rules for our own character so it reacts at once: emotes
// start only while standing, wave and bow time out, sit lasts until the
// character moves, and moving ends any of them.

import "./emotes.css";
import type { Anim, Emote } from "../wasm/sim_wasm";
import { h } from "./dom";

export const EMOTES: readonly { id: Emote; label: string; command: string }[] = [
  { id: "wave", label: "Wave", command: "/wave" },
  { id: "bow", label: "Bow", command: "/bow" },
  { id: "sit", label: "Sit", command: "/sit" },
];

/** The emote a chat line asks for (`/wave`, `/bow`, `/sit`, any case, nothing after), or null. */
export function emoteCommand(line: string): Emote | null {
  const m = /^\/(wave|bow|sit)\s*$/i.exec(line.trim());
  return m ? (m[1]!.toLowerCase() as Emote) : null;
}

export interface EmoteDurations {
  /** `EMOTE_WAVE_MS` and `EMOTE_BOW_MS` (protocol constants). */
  waveMs: number;
  bowMs: number;
}

/** Our own character's emote, by the server's rules. Pure, unit tested. */
export class LocalEmote {
  private current: { emote: Emote; until: number } | null = null;

  constructor(private readonly durations: EmoteDurations) {}

  /**
   * Starts `emote` at `nowMs` if the character is standing; returns whether
   * it started (and so whether to send it).
   */
  start(emote: Emote, nowMs: number, moving: boolean): boolean {
    if (moving) return false;
    const ms = emote === "wave" ? this.durations.waveMs : emote === "bow" ? this.durations.bowMs : Infinity;
    this.current = { emote, until: nowMs + ms };
    return true;
  }

  /** The animation to show at `nowMs`: the emote while it lasts, otherwise null. Moving ends it. */
  anim(nowMs: number, moving: boolean): Anim | null {
    const c = this.current;
    if (!c) return null;
    if (moving || nowMs >= c.until) {
      this.current = null;
      return null;
    }
    return c.emote;
  }

  get active(): Emote | null {
    return this.current?.emote ?? null;
  }

  clear(): void {
    this.current = null;
  }
}

/** Little line drawings for the buttons (inline SVG, drawn for this game). */
const ICONS: Record<Emote, string> = {
  wave: "M9 21v-7.5L5.6 9.8a1.3 1.3 0 0 1 1.9-1.8L10 10.6V4.5a1.3 1.3 0 0 1 2.6 0V10V3.3a1.3 1.3 0 0 1 2.6 0V10V4.6a1.3 1.3 0 0 1 2.6 0V14c0 3.8-2.2 7-5.8 7Z M3 6.5c.4-1.6 1.3-2.8 2.7-3.6 M2.2 10c.1-.9.4-1.8.9-2.5",
  bow: "M12 4.2a1.9 1.9 0 1 0 0 .01 M9.5 21l1.4-7.3L7 9.6l1.8-1.9 3.6 3 4-.6.4 2.4-3.7 1 .9 7.5",
  sit: "M13 3.8a1.9 1.9 0 1 0 0 .01 M6 20h12 M8.5 16.5l3-1 1-4.8 3 1.5 1.6 3.3 M7.2 20l1.3-3.5 M12.5 10.7l-3.6 1.5-1.4 2.6",
};

export interface EmoteBarOptions {
  onEmote: (emote: Emote) => void;
}

/** The HUD's emote buttons. */
export class EmoteBar {
  readonly root: HTMLElement;
  private readonly buttons = new Map<Emote, HTMLButtonElement>();

  constructor(opts: EmoteBarOptions) {
    this.root = h("nav", { class: "emote-bar panel", "aria-label": "Emotes" });
    for (const e of EMOTES) {
      const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.setAttribute("viewBox", "0 0 24 24");
      svg.setAttribute("aria-hidden", "true");
      const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      path.setAttribute("d", ICONS[e.id]);
      svg.append(path);
      const b = h(
        "button",
        { type: "button", class: "emote-button", title: `${e.label} (${e.command})`, "aria-pressed": "false", onclick: () => opts.onEmote(e.id) },
        svg,
        h("span", {}, e.label),
      );
      this.buttons.set(e.id, b);
      this.root.append(b);
    }
  }

  /** Marks the emote being played (or none). */
  setActive(emote: Emote | null): void {
    for (const [id, b] of this.buttons) {
      const on = String(id === emote);
      if (b.getAttribute("aria-pressed") !== on) b.setAttribute("aria-pressed", on);
    }
  }
}

