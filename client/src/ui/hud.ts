// In-game overlay: who you are and where, players online and nearby, the
// connection banner, the login-queue card and the settings menu.
//
// The layout (docs/architecture.md "Client"): who
// you are, where, and the online and nearby counts in the top-right corner,
// with the level badge, the clock and the class chip under them (`corner`);
// the purse, every feature's menu button and Settings in one bar along the
// bottom-right edge (`dock`), the emote bar just above it; the top centre
// is left to passing chips and toasts. The corners are measured when they
// change size (`hudCorners`), for nameplates to keep clear of and for CSS
// custom properties other parts of the HUD sit by.

import { audio } from "../audio";
import { fullscreen, showEnterFullscreen, type Fullscreen } from "../fullscreen";
import { QUALITY_HINTS, VIEW_LIMITS, type AudioBus, type AudioSettings, type Quality, type Settings, type ViewLimit } from "../settings";
import type { ChatterMode } from "../game/npc/chatter";
import { ALL_FILTERABLE, ALWAYS_IN_ALL, CHAT_TAB_LABELS, type ChatAllFilter } from "./chat-state";
import { h } from "./dom";
import { SHORTCUTS } from "./shortcuts";
import { hudCorners, type Box } from "./hud-corners";
import type { Keepout } from "./nameplates";

export interface HudOptions {
  name: string;
  houseName: string;
  houseColour: string;
  settings: Settings;
  onViewLimit: (n: ViewLimit) => void;
  onQuality: (q: Quality) => void;
  /** Sound: a volume or the mute changed (`commit`: the slider was let go, or the mute clicked: save it). */
  onAudio: (a: AudioSettings, commit: boolean) => void;
  onTogglePerf: () => void;
  /** Settings > Sign out: leave the world for the character picker (the account stays signed in). */
  onSignOut: () => void;
  /** The page's fullscreen (main.ts enters it from the picker); the shared one when omitted. */
  fullscreen?: Fullscreen;
  /** The "online" count opens the players list. */
  onPlayers?: () => void;
  /** Settings, Graphics: every window back to where it opens by default. */
  onResetWindows?: () => void;
  /** NPCs: Portrait chatter changed (On / Quiet / Off). */
  onChatter?: (c: ChatterMode) => void;
  /** Settings > Chats: the channels the chat's All tab shows changed (saved by the caller). */
  onChatAll?: (f: ChatAllFilter) => void;
}

/** NPCs: the Portrait chatter choices and what each means. */
const CHATTERS: { id: ChatterMode; label: string }[] = [
  { id: "on", label: "On" },
  { id: "quiet", label: "Quiet" },
  { id: "off", label: "Off" },
];
const CHATTER_HINTS: Readonly<Record<ChatterMode, string>> = {
  on: "Talking portraits nod or say a short line as you pass, and talk when you press E.",
  quiet: "Talking portraits speak only when you press E beside them.",
  off: "Talking portraits keep their words to themselves.",
};

/** The Sound fieldset's sliders, in order. */
const VOLUMES: { bus: AudioBus; label: string }[] = [
  { bus: "master", label: "Master" },
  { bus: "music", label: "Music" },
  { bus: "effects", label: "Effects" },
  { bus: "ambience", label: "Ambience" },
  { bus: "interface", label: "Interface" },
];

/**
 * The Settings window's tabs. The keys are listed there, not on the screen all
 * the time. "credits" is a page without a tab: the Credits button opens it.
 */
type SettingsTab = "graphics" | "audio" | "chats" | "keys" | "credits";
const SETTINGS_TABS: { id: SettingsTab; label: string }[] = [
  { id: "graphics", label: "Graphics" },
  { id: "audio", label: "Audio" },
  { id: "chats", label: "Chats" },
  { id: "keys", label: "Keyboard shortcuts" },
];

/** The "Keyboard shortcuts" tab: a section per group, each row its key caps and what they do. */
function buildShortcuts(): HTMLElement[] {
  return SHORTCUTS.map((g) =>
    h(
      "section",
      { class: "shortcut-group" },
      h("h3", {}, g.title),
      h(
        "dl",
        {},
        ...g.items.flatMap((s) => [
          h("dt", {}, ...s.keys.map((k) => h("kbd", {}, k))),
          h("dd", {}, s.what),
        ]),
      ),
    ),
  );
}

const QUALITIES: { id: Quality; label: string }[] = [
  { id: "low", label: "Low" },
  { id: "medium", label: "Medium" },
  { id: "high", label: "High" },
];

export class Hud {
  readonly root: HTMLElement;
  private readonly online: HTMLElement;
  private readonly nearby: HTMLElement;
  private readonly place: HTMLElement;
  private readonly banner: HTMLElement;
  private readonly queue: HTMLElement;
  private readonly queuePos: HTMLElement;
  private readonly menu: HTMLElement;
  private readonly menuButton: HTMLButtonElement;
  /** Buttons other features add to the menu bar at the bottom right (Friends, ...), left of the settings button. */
  readonly actions: HTMLElement;
  /** The purse and the mail owl (economy/economy.ts), at the left end of the menu bar. */
  readonly purse: HTMLElement;
  /** The top-right stack: the name and counts panel; the school appends the level and clock strip under it. */
  readonly corner: HTMLElement;
  /** The menu bar along the bottom-right edge: the purse, the feature buttons, Settings. */
  readonly dock: HTMLElement;
  private readonly top: HTMLElement;
  private bar: Keepout | null = null;
  private keeps: Keepout[] = [];
  private emotes: Element | null = null;
  /** Panels along the top edge on the left (the party list) that plates keep clear of too. */
  private readonly topLeft: HTMLElement[] = [];
  private readonly observer: ResizeObserver | null;
  private readonly onResize = () => this.measureBar();
  /** Settings' "Enter fullscreen", shown while the game is windowed. */
  private readonly fullscreenButton: HTMLButtonElement;
  private readonly fullscreen: Fullscreen;
  private readonly offFullscreen: () => void;

  constructor(private readonly opts: HudOptions) {
    this.online = h("strong", {}, "–");
    this.nearby = h("strong", {}, "–");
    // The place's words, inside `.hud-place` (a size container: squeezed too narrow, it hides them rather than show a sliver).
    this.place = h("span", { class: "hud-place-text" }, "");
    this.banner = h("div", { class: "banner", role: "status", hidden: true });
    this.queuePos = h("strong", {}, "–");
    this.queue = h(
      "div",
      { class: "queue", hidden: true, role: "status" },
      h("div", { class: "card small" }, h("h2", {}, "The castle is full"), h("p", {}, "You are number ", this.queuePos, " in the queue."), h("p", { class: "muted" }, "Keep this page open; you'll be let in automatically.")),
    );
    this.fullscreen = opts.fullscreen ?? fullscreen();
    // Inside the click: the browser allows fullscreen only from a gesture. Fullscreen takes the Esc lock back too.
    this.fullscreenButton = h("button", { type: "button", class: "secondary", "data-testid": "settings-fullscreen", onclick: () => void this.fullscreen.enter() }, "Enter fullscreen");
    this.syncFullscreen();
    this.offFullscreen = this.fullscreen.onChange(() => this.syncFullscreen());
    this.menuButton = h("button", { type: "button", class: "icon-button", "aria-label": "Settings", "aria-expanded": "false", title: "Settings (Esc closes)", onclick: () => this.toggleMenu() }, "⚙");
    this.menu = this.buildMenu();
    this.actions = h("div", { class: "hud-actions" });
    this.purse = h("div", { class: "hud-purse" });

    this.top = h(
      "header",
      { class: "hud-top panel", style: `--house: ${opts.houseColour}` },
      // A long name and house are cut short with an ellipsis (style.css), never into the counts: the whole on hover.
      // The house sits under the name, so the name keeps the house's width too.
      h(
        "div",
        { class: "hud-me", title: `${opts.name} · ${opts.houseName}` },
        h("span", { class: "crest", "aria-hidden": "true" }),
        h("span", { class: "hud-who" }, h("span", { class: "hud-name" }, opts.name), h("span", { class: "hud-house" }, opts.houseName)),
      ),
      h(
        "div",
        { class: "hud-counts" },
        // The count opens the players list (U).
        opts.onPlayers ? h("button", { type: "button", class: "hud-online", title: "Players online (U)", onclick: () => opts.onPlayers?.() }, this.online, " online") : h("span", {}, this.online, " online"),
        h("span", {}, this.nearby, " nearby"),
      ),
      h("span", { class: "hud-place" }, this.place),
    );
    this.corner = h("div", { class: "hud-corner" }, this.top);
    this.dock = h("nav", { class: "hud-dock panel", "aria-label": "Menu" }, this.purse, this.actions, this.menuButton);
    this.root = h(
      "div",
      { class: "hud-layer" },
      this.corner,
      this.dock,
      this.menu,
      this.banner,
      this.queue,
    );
    // Fires once when the bar is first laid out, then on every size change.
    this.observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(this.onResize);
    this.observer?.observe(this.corner);
    this.observer?.observe(this.dock);
    window.addEventListener("resize", this.onResize);
  }

  private buildMenu(): HTMLElement {
    const group = <T extends string | number>(label: string, name: string, options: { id: T; label: string }[], current: T, onPick: (v: T) => void) =>
      h(
        "fieldset",
        { class: "segmented" },
        h("legend", {}, label),
        ...options.map((o) =>
          h(
            "label",
            {},
            h("input", {
              type: "radio",
              name,
              value: String(o.id),
              checked: o.id === current,
              onchange: () => {
                audio.play("ui.toggle");
                onPick(o.id);
              },
            }),
            h("span", {}, o.label),
          ),
        ),
      );
    // What each level turns on (the toon look's outlines and bloom go on Low).
    const hint = h("p", { class: "muted quality-hint" }, QUALITY_HINTS[this.opts.settings.quality]);
    const chatterHint = h("p", { class: "muted quality-hint chatter-hint" }, CHATTER_HINTS[this.opts.settings.chatter]);
    const pages: Record<SettingsTab, HTMLElement> = {
      graphics: h(
        "div",
        { class: "settings-page", role: "tabpanel", "aria-label": "Graphics" },
        group("Characters shown", "view-limit", VIEW_LIMITS.map((n) => ({ id: n, label: String(n) })), this.opts.settings.viewLimit, this.opts.onViewLimit),
        group("Graphics quality", "quality", QUALITIES, this.opts.settings.quality, (q) => {
          hint.textContent = QUALITY_HINTS[q];
          this.opts.onQuality(q);
        }),
        hint,
        group("Portrait chatter", "chatter", CHATTERS, this.opts.settings.chatter, (c) => {
          chatterHint.textContent = CHATTER_HINTS[c];
          this.opts.onChatter?.(c);
        }),
        chatterHint,
        h("button", { type: "button", class: "secondary", onclick: this.opts.onTogglePerf }, "Performance stats (F3)"),
        this.opts.onResetWindows
          ? h("button", { type: "button", class: "secondary", title: "Put every window back where it opens; drag a window by its title to move it", onclick: () => this.opts.onResetWindows?.() }, "Reset window positions")
          : null,
      ),
      audio: h("div", { class: "settings-page", role: "tabpanel", "aria-label": "Audio", hidden: true }, this.buildSound()),
      chats: h("div", { class: "settings-page", role: "tabpanel", "aria-label": "Chats", hidden: true }, this.buildChats()),
      keys: h("div", { class: "settings-page shortcuts", role: "tabpanel", "aria-label": "Keyboard shortcuts", hidden: true }, ...buildShortcuts()),
      // Filled from ui/credits.ts (loaded on demand) when first shown.
      credits: h("div", { class: "settings-page settings-credits", role: "region", "aria-label": "Credits", hidden: true }),
    };
    const tabs = h("div", { class: "settings-tabs", role: "tablist" });
    const show = (id: SettingsTab) => {
      for (const [key, page] of Object.entries(pages)) page.hidden = key !== id;
      if (id === "credits" && !pages.credits.hasChildNodes()) void import("./credits").then((m) => pages.credits.replaceChildren(m.creditsBody()));
      for (const b of tabs.children) b.setAttribute("aria-selected", String((b as HTMLElement).dataset.tab === id));
    };
    for (const t of SETTINGS_TABS) {
      tabs.append(
        h("button", {
          type: "button",
          role: "tab",
          class: "settings-tab",
          "data-tab": t.id,
          "aria-selected": String(t.id === "graphics"),
          onclick: () => {
            audio.play("ui.toggle");
            show(t.id);
          },
        }, t.label),
      );
    }
    return h(
      "aside",
      { class: "menu panel", hidden: true, "aria-label": "Settings" },
      h("h2", {}, "Settings"),
      tabs,
      pages.graphics,
      pages.audio,
      pages.chats,
      pages.keys,
      pages.credits,
      this.fullscreenButton,
      h("button", { type: "button", class: "secondary", "data-testid": "settings-credits", title: "Who made the art, sounds, fonts and code in the game", onclick: () => (audio.play("ui.toggle"), show("credits")) }, "Credits"),
      h("button", { type: "button", class: "secondary", "data-testid": "settings-sign-out", title: "Leave the castle for the character picker", onclick: this.opts.onSignOut }, "Sign out"),
    );
  }

  /**
   * Sound (docs/architecture.md "Sound and music"): a mute, then a slider per
   * bus (0-100, step 5) showing its percent. Moving one sets the bus at once
   * and ticks on that bus (music and ambience preview nothing); letting go
   * saves.
   */
  private buildSound(): HTMLElement {
    const a = this.opts.settings.audio;
    const mute = h("input", {
      type: "checkbox",
      checked: a.muted,
      onchange: () => {
        a.muted = mute.checked;
        this.opts.onAudio(a, true);
        if (!a.muted) audio.play("ui.toggle");
      },
    }) as HTMLInputElement;
    const rows = VOLUMES.map(({ bus, label }) => {
      const value = h("output", { class: "volume-value" }, `${Math.round(a[bus] * 100)}%`);
      const input = h("input", {
        type: "range",
        min: "0",
        max: "100",
        step: "5",
        value: String(Math.round(a[bus] * 100)),
        "aria-label": `${label} volume`,
        oninput: () => {
          a[bus] = Number(input.value) / 100;
          value.textContent = `${input.value}%`;
          this.opts.onAudio(a, false);
          audio.preview(bus);
        },
        onchange: () => this.opts.onAudio(a, true),
      }) as HTMLInputElement;
      return h("label", { class: "volume" }, h("span", {}, label), input, value);
    });
    return h("fieldset", { class: "sound-settings" }, h("legend", {}, "Sound"), h("label", { class: "mute" }, mute, h("span", {}, "Mute all")), ...rows);
  }

  /**
   * Chats: a box per channel the chat's All tab may leave out (house, party,
   * club, whisper; each keeps its own tab), then say, NPC and system lines,
   * ticked and disabled: All always shows them. A change applies at once.
   */
  private buildChats(): HTMLElement {
    const f = this.opts.settings.chatAll;
    const row = (kind: string, label: string, box: HTMLInputElement, note?: string) =>
      h("label", { class: "mute chat-kind", "data-kind": kind }, box, h("span", {}, label), note ? h("small", { class: "muted" }, note) : null);
    const rows = ALL_FILTERABLE.map((kind) => {
      const box = h("input", {
        type: "checkbox",
        checked: f[kind],
        onchange: () => {
          f[kind] = box.checked;
          audio.play("ui.toggle");
          this.opts.onChatAll?.({ ...f });
        },
      }) as HTMLInputElement;
      return row(kind, CHAT_TAB_LABELS[kind], box);
    });
    const always = ALWAYS_IN_ALL.map(({ kind, label }) => row(kind, label, h("input", { type: "checkbox", checked: true, disabled: true }) as HTMLInputElement, "always shown"));
    return h(
      "fieldset",
      { class: "sound-settings chat-settings" },
      h("legend", {}, "Shown in the All tab"),
      ...rows,
      ...always,
      h("p", { class: "muted quality-hint" }, "A channel left out of All still shows on its own tab."),
    );
  }

  /** Where the top-right stack is (px from the window's top left: the name, counts, level and clock); null before layout. */
  get topBar(): Keepout | null {
    return this.bar;
  }

  /** The boxes nameplates keep clear of: the top-right stack and the bottom-right bars. */
  get keepouts(): readonly Keepout[] {
    return this.keeps;
  }

  /**
   * Measures the corners when they change size (the strip arrives, a button
   * is added, the place changes) or the window does. Reading the layout only
   * then keeps it out of the frame loop.
   */
  private measureBar(): void {
    const box = (el: Element | null): Box | null => {
      if (!el || (el as HTMLElement).hidden) return null;
      const r = el.getBoundingClientRect();
      return r.height > 0 ? { left: r.left, top: r.top, right: r.right, bottom: r.bottom } : null;
    };
    // The emote bar (characters/cast.ts) sits above the menu bar: watched too once it is there.
    const emotes = this.root.parentElement?.querySelector(".emote-bar") ?? null;
    if (emotes && emotes !== this.emotes) {
      this.emotes = emotes;
      this.observer?.observe(emotes);
    }
    const out = hudCorners({
      corner: box(this.corner),
      dock: box(this.dock),
      emotes: box(emotes),
      topLeft: this.topLeft.map(box),
      width: window.innerWidth,
      height: window.innerHeight,
    });
    this.keeps = out.keepouts;
    this.bar = out.keepouts.find((k) => k.edge === "top") ?? null;
    for (const [k, v] of Object.entries(out.vars)) document.documentElement.style.setProperty(k, v);
  }

  /** Nameplates keep clear of `el` too (a panel at the top left, measured whenever its size changes or it hides). */
  keepClearOf(el: HTMLElement): void {
    this.topLeft.push(el);
    this.observer?.observe(el);
    this.measureBar();
  }

  /** "Enter fullscreen" follows `fullscreenchange` (a held Esc, F11, the button itself). */
  private syncFullscreen(): void {
    this.fullscreenButton.hidden = !showEnterFullscreen(this.fullscreen.supported, this.fullscreen.active);
  }

  dispose(): void {
    this.offFullscreen();
    this.observer?.disconnect();
    window.removeEventListener("resize", this.onResize);
  }

  toggleMenu(open = this.menu.hidden): void {
    this.menu.hidden = !open;
    this.menuButton.setAttribute("aria-expanded", String(open));
  }

  /** The settings menu (a window of the stack: Esc closes it like the others). */
  get menuRoot(): HTMLElement {
    return this.menu;
  }

  get menuOpen(): boolean {
    return !this.menu.hidden;
  }

  setRoster(online: number, nearby: number): void {
    this.online.textContent = String(online);
    this.nearby.textContent = String(nearby);
  }

  setPlace(text: string): void {
    if (this.place.textContent !== text) this.place.textContent = text;
  }

  /** A status line at the top of the screen, or null to hide it. */
  setBanner(text: string | null): void {
    this.banner.textContent = text ?? "";
    this.banner.hidden = !text;
  }

  /** The login-queue card, or null to hide it. */
  setQueue(position: number | null): void {
    this.queue.hidden = position === null;
    if (position !== null) this.queuePos.textContent = String(position);
  }
}
