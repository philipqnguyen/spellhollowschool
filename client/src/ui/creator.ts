// The character creator, shown once after sign-up: house (crest cards),
// body (Male / Female and height), face, skin and eyes, hair, a starter
// outfit and a name, with a
// live 3D preview you can turn. It sends `POST /api/characters` with the
// appearance; the server grants the starter wardrobe and furniture and
// equips the chosen outfit (docs/architecture.md, "HTTP API").
//
// The preview needs Babylon and the character pipeline, which live in the
// game chunk: they load in the background while the first step is shown.

import "./creator.css";
import { ApiError, authedRequest, type Character } from "../api";
import { heightLabel, heightMetres, sliderValue, toWire } from "../content/appearance";
import { bodyType, CREATOR, hairStylesFor, paletteHex, type Slider, type Swatch } from "../content/catalog";
import { HOUSES, houseInfo, type HouseInfo } from "../content/houses";
import type { CharacterPreview } from "../game/characters/preview";
import type { GearSlot, House } from "../wasm/sim_wasm";
import { h, mount } from "./dom";
import {
  chooseHouse,
  choiceDyes,
  createRequest,
  goTo,
  initialState,
  nextStep,
  outfitChoices,
  randomLook,
  setBodyType,
  setEyeColour,
  setHairColour,
  setHairStyle,
  setOutfit,
  setSkinTone,
  setSliderById,
  STEPS,
  submitProblem,
  type CreatorState,
} from "./creator-state";
import { SLOT_LABELS } from "./wardrobe-state";
import { wearTag } from "./wear";
import { NAME_MAX } from "./validate";

export interface CreatorOptions {
  /** Created: the session now plays it. */
  onCreated: (c: Character) => void;
  onSignOut: () => void;
  /** Back to the picker without creating (offered when the account already has characters). */
  onBack?: () => void;
}

const TITLE = "SpellHollow School";
const SLOTS: GearSlot[] = ["body", "legs", "shoes", "hat"];
/** Camera angles for the turn buttons (the character faces +Z: front is π/2). */
const VIEWS: { label: string; alpha: number }[] = [
  { label: "Front", alpha: Math.PI / 2 },
  { label: "Side", alpha: 0 },
  { label: "Back", alpha: -Math.PI / 2 },
];

/** Renders the creator into `root` (the full-screen #screen element). */
export function renderCreator(root: HTMLElement, opts: CreatorOptions): () => void {
  const creator = new CreatorScreen(root, opts);
  return () => creator.dispose();
}

class CreatorScreen {
  private state: CreatorState = initialState(HOUSES[0]?.id as House | undefined);
  private readonly canvas: HTMLCanvasElement;
  private readonly loading: HTMLElement;
  private readonly stepNav: HTMLElement;
  private readonly stepBody: HTMLElement;
  private readonly error: HTMLElement;
  private readonly back: HTMLButtonElement;
  private readonly next: HTMLButtonElement;
  private preview: CharacterPreview | null = null;
  private previewPending = false;
  private submitting = false;
  private disposed = false;
  /** Disconnects the preview when the screen is replaced. */
  private readonly watch: MutationObserver;

  constructor(
    private readonly root: HTMLElement,
    private readonly opts: CreatorOptions,
  ) {
    this.canvas = h("canvas", { class: "creator-canvas", "aria-label": "Your character: drag to turn, scroll to step closer" });
    this.loading = h("div", { class: "stage-loading" }, h("div", { class: "candle", "aria-hidden": "true" }, h("span", { class: "flame" })), h("p", {}, "Fetching a looking glass…"));
    this.stepNav = h("nav", { class: "creator-steps", "aria-label": "Steps" });
    this.stepBody = h("div", { class: "creator-step" });
    this.error = h("p", { class: "form-error", role: "alert", hidden: true });
    this.back = h("button", { type: "button", class: "link", onclick: () => this.move(-1) }, "Back");
    this.next = h("button", { type: "submit", class: "primary" }, "Next");

    const form = h(
      "form",
      { class: "creator-panel", novalidate: true },
      this.stepNav,
      this.stepBody,
      this.error,
      h("div", { class: "creator-foot" }, this.back, this.next),
    );
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      if (this.state.step === "name") void this.submit();
      else this.move(1);
    });

    const stage = h(
      "div",
      { class: "creator-stage" },
      h("div", { class: "stage-frame" }, this.canvas, this.loading),
      h(
        "div",
        { class: "stage-tools" },
        ...VIEWS.map((v) => h("button", { type: "button", class: "stage-button", onclick: () => this.preview?.turnTo(v.alpha) }, v.label)),
        h("button", { type: "button", class: "stage-button", title: "A random body, face, hair and eyes", onclick: () => this.set(randomLook(this.state, Math.random)) }, "Surprise me"),
      ),
    );

    mount(
      root,
      h(
        "section",
        { class: "creator", role: "dialog", "aria-labelledby": "creator-heading" },
        h(
          "header",
          { class: "creator-head" },
          h("p", { class: "card-kicker" }, TITLE),
          h("h1", { id: "creator-heading" }, "Who will you be?"),
          h("p", { class: "card-sub" }, "Choose your house and how you look. You can change clothes and dyes later in your wardrobe; your name is for good."),
        ),
        h("div", { class: "creator-body" }, stage, form),
        h(
          "p",
          { class: "creator-signout" },
          opts.onBack ? h("button", { type: "button", class: "link", "data-testid": "creator-back", onclick: () => opts.onBack?.() }, "Back to your characters") : null,
          opts.onBack ? " · " : null,
          h("button", { type: "button", class: "link", onclick: () => opts.onSignOut() }, "Sign out"),
        ),
      ),
    );
    this.render();
    void this.loadPreview();
    this.watch = new MutationObserver(() => {
      if (!this.root.contains(this.canvas)) this.dispose();
    });
    this.watch.observe(root, { childList: true });
  }

  private async loadPreview(): Promise<void> {
    try {
      const { CharacterPreview } = await import("../game/characters/preview");
      if (this.disposed) return;
      this.preview = new CharacterPreview(this.canvas, this.state.appearance);
      this.loading.hidden = true;
    } catch (e) {
      console.error(e);
      this.loading.replaceChildren(h("p", {}, "The looking glass is clouded: your browser could not show the 3D preview. You can still create your character."));
    }
  }

  private set(next: CreatorState): void {
    const changedLook = next.appearance !== this.state.appearance;
    const rerender = next.step !== this.state.step || next.house !== this.state.house || changedLook;
    this.state = next;
    if (changedLook) this.schedulePreview();
    if (rerender) this.render();
  }

  /** Coalesces preview updates (slider drags fire many inputs a frame). */
  private schedulePreview(): void {
    if (this.previewPending) return;
    this.previewPending = true;
    requestAnimationFrame(() => {
      this.previewPending = false;
      this.preview?.setAppearance(this.state.appearance);
    });
  }

  private move(delta: 1 | -1): void {
    const step = nextStep(this.state, delta);
    if (step) this.set(goTo(this.state, step));
  }

  private async submit(): Promise<void> {
    const problem = submitProblem(this.state);
    if (problem) return this.showError(problem);
    if (this.submitting) return;
    this.submitting = true;
    this.next.disabled = true;
    this.showError(null);
    try {
      const req = createRequest(this.state);
      const r = await authedRequest<{ id: number; name: string; house: string }>("POST", "/characters", {
        name: req.name,
        house: req.house,
        appearance: toWire(req.appearance),
      });
      // A wave goodbye from the looking glass, then into the castle.
      if (this.preview) {
        this.preview.setAnim("wave");
        await new Promise((resolve) => setTimeout(resolve, 1100));
      }
      this.opts.onCreated({ id: r.id, name: r.name, house: r.house });
    } catch (e) {
      if (e instanceof ApiError && e.code === "name_taken") this.set(goTo(this.state, "name"));
      this.showError(e instanceof ApiError ? createError(e) : e instanceof Error ? e.message : String(e));
    } finally {
      this.submitting = false;
      if (this.next.isConnected) this.next.disabled = false;
    }
  }

  private showError(text: string | null): void {
    this.error.textContent = text ?? "";
    this.error.hidden = !text;
  }

  // ---------------------------------------------------------------- rendering

  private render(): void {
    const s = this.state;
    this.stepNav.replaceChildren(
      ...STEPS.map((step, i) =>
        h(
          "button",
          { type: "button", class: "creator-step-tab", "aria-current": step.id === s.step ? "step" : undefined, onclick: () => this.set(goTo(this.state, step.id)) },
          h("span", { class: "step-number" }, String(i + 1)),
          step.label,
        ),
      ),
    );
    const house = houseInfo(s.house);
    this.root.querySelector<HTMLElement>(".creator")?.style.setProperty("--house", house.colour);
    this.root.querySelector<HTMLElement>(".creator")?.style.setProperty("--house-accent", house.accent);
    this.back.hidden = nextStep(s, -1) === null;
    this.next.textContent = s.step === "name" ? "Begin my first year" : "Next";
    this.showError(null);
    switch (s.step) {
      case "house":
        return this.renderHouse();
      case "body":
        return this.renderBody();
      case "face":
        return this.renderFace();
      case "hair":
        return this.renderHair();
      case "outfit":
        return this.renderOutfit();
      case "name":
        return this.renderName();
    }
  }

  private renderHouse(): void {
    const cards = h("div", { class: "crest-cards", role: "radiogroup", "aria-label": "House" });
    for (const house of HOUSES) {
      const on = house.id === this.state.house;
      cards.append(
        h(
          "button",
          {
            type: "button",
            role: "radio",
            "aria-checked": String(on),
            class: "crest-card",
            style: `--house: ${house.colour}; --house-accent: ${house.accent}`,
            onclick: () => this.set(chooseHouse(this.state, house.id as House)),
          },
          crest(house),
          h("span", { class: "crest-name" }, house.name),
          h("span", { class: "crest-virtues" }, house.virtues),
          house.motto ? h("span", { class: "crest-motto" }, `“${house.motto}”`) : null,
        ),
      );
    }
    this.stepBody.replaceChildren(
      h("h2", {}, "Choose your house"),
      h("p", { class: "step-hint" }, "Your house gives you your common room, your dormitory and the colours on your robes."),
      cards,
    );
  }

  private renderBody(): void {
    const types = h(
      "div",
      { class: "choice-row", role: "radiogroup", "aria-label": "Body type" },
      ...CREATOR.body_types.map((b) =>
        h(
          "button",
          { type: "button", role: "radio", class: "choice", "aria-checked": String(this.state.appearance.bodyType === b.index), onclick: () => this.set(setBodyType(this.state, b.index)) },
          b.name,
        ),
      ),
    );
    const body = bodyType(this.state.appearance.bodyType);
    const [lo, hi] = body.height_m;
    this.stepBody.replaceChildren(
      h("h2", {}, "Body"),
      h("p", { class: "step-hint" }, `Almost every piece of clothing fits both; a few are marked Female only or Male only. Height runs from ${heightLabel(lo)} to ${heightLabel(hi)}.`),
      types,
      ...CREATOR.sliders.filter((s) => s.field === "height").map((s) => this.slider(s)),
    );
  }

  private renderFace(): void {
    this.stepBody.replaceChildren(
      h("h2", {}, "Face"),
      ...CREATOR.sliders.filter((s) => s.field === "face").map((s) => this.slider(s)),
      h("p", { class: "field-label" }, "Skin tone"),
      swatchRow(
        "Skin tone",
        CREATOR.skin_tones.map((t) => ({ id: t.index, name: t.name, hex: t.hex })),
        this.state.appearance.skinTone,
        (i) => this.set(setSkinTone(this.state, i)),
      ),
      ...colourGroups("Eye colour", CREATOR.eye_colours, this.state.appearance.eyeColour, (i) => this.set(setEyeColour(this.state, i))),
    );
  }

  private renderHair(): void {
    const styles = h(
      "div",
      { class: "choice-grid", role: "radiogroup", "aria-label": "Hairstyle" },
      ...hairStylesFor(this.state.appearance.bodyType).map((st) =>
        h(
          "button",
          { type: "button", role: "radio", class: "choice", "aria-checked": String(this.state.appearance.hairStyle === st.id), onclick: () => this.set(setHairStyle(this.state, st.id)) },
          st.name,
        ),
      ),
    );
    this.stepBody.replaceChildren(
      h("h2", {}, "Hair"),
      styles,
      ...colourGroups("Hair colour", CREATOR.hair_colours, this.state.appearance.hairColour, (i) => this.set(setHairColour(this.state, i))),
    );
  }

  private renderOutfit(): void {
    const rows = SLOTS.map((slot) => {
      const worn = this.state.appearance.outfit[slot];
      const choices = outfitChoices(this.state, slot);
      const buttons = choices.map((item) =>
        h(
          "button",
          { type: "button", role: "radio", class: "choice outfit-choice", "aria-checked": String(worn?.itemDef === item.id), onclick: () => this.set(setOutfit(this.state, slot, item.id)) },
          h("span", { class: "dye-dots", "aria-hidden": "true" }, ...choiceDyes(this.state, item).map((d) => h("span", { class: "dot", style: `--dot: ${paletteHex(d)}` }))),
          item.name,
          wearTag(item),
        ),
      );
      if (slot === "hat") {
        buttons.unshift(h("button", { type: "button", role: "radio", class: "choice outfit-choice", "aria-checked": String(!worn), onclick: () => this.set(setOutfit(this.state, slot, null)) }, "No hat"));
      }
      return h("div", { class: "outfit-row" }, h("p", { class: "field-label" }, SLOT_LABELS[slot]), h("div", { class: "choice-grid", role: "radiogroup", "aria-label": SLOT_LABELS[slot] }, ...buttons));
    });
    this.stepBody.replaceChildren(
      h("h2", {}, "First-day outfit"),
      h("p", { class: "step-hint" }, "Everything here is in your trunk from day one, with more to wear and dye in your wardrobe."),
      ...rows,
    );
  }

  private renderName(): void {
    const hint = h("span", { class: "field-hint" }, "3 to 24 letters; spaces, hyphens and apostrophes allowed between them.");
    const input = h("input", { type: "text", name: "name", autocomplete: "off", maxlength: NAME_MAX, required: true, spellcheck: "false", value: this.state.name });
    input.addEventListener("input", () => {
      this.state = { ...this.state, name: input.value };
      const problem = input.value.trim() ? submitProblem(this.state) : null;
      hint.textContent = problem ?? "3 to 24 letters; spaces, hyphens and apostrophes allowed between them.";
      hint.classList.toggle("bad", !!problem);
    });
    const house = houseInfo(this.state.house);
    this.stepBody.replaceChildren(
      h("h2", {}, "Your name"),
      h("p", { class: "step-hint" }, "It is how other students will know you, and it cannot be changed later."),
      h("label", { class: "field" }, h("span", { class: "field-label" }, "Name"), input, hint),
      h("p", { class: "summary" }, "You will join ", h("strong", { style: `color: ${house.colour}` }, house.name), ", and arrive on the castle's front steps."),
    );
    queueMicrotask(() => input.focus());
  }

  private slider(s: Slider): HTMLElement {
    const value = sliderValue(this.state.appearance, s);
    const input = h("input", { type: "range", min: 0, max: 255, step: 1, value, "aria-label": s.name });
    // Height reads in metres and feet/inches, live while dragging.
    const readout = s.field === "height" ? h("span", { class: "slider-value", "aria-live": "polite" }, heightLabel(heightMetres(this.state.appearance))) : null;
    input.addEventListener("input", () => {
      this.set(setSliderById(this.state, s.id, Number(input.value)));
      if (readout) {
        const text = heightLabel(heightMetres(this.state.appearance));
        readout.textContent = text;
        input.setAttribute("aria-valuetext", text);
      }
    });
    if (readout) input.setAttribute("aria-valuetext", readout.textContent ?? "");
    const ends = s.shape_keys.length >= 2 || s.field === "height" ? sliderEnds(s) : null;
    return h(
      "label",
      { class: "slider" },
      h("span", { class: "slider-name" }, s.name, readout ? " " : null, readout),
      input,
      ends ? h("span", { class: "slider-ends", "aria-hidden": "true" }, h("span", {}, ends[0]), h("span", {}, ends[1])) : null,
    );
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    this.watch.disconnect();
    this.preview?.dispose();
    this.preview = null;
  }
}

/** Words for the two ends of a slider, from its shape keys (`face_jaw_narrow` → "narrow"). */
export function sliderEnds(s: Slider): [string, string] {
  if (s.field === "height") return ["shorter", "taller"];
  const word = (key: string) => key.split("_").pop() ?? "";
  return [word(s.shape_keys[0] ?? ""), word(s.shape_keys[1] ?? "")];
}

/** A colour choice: one labelled row of every shade (no natural/fantasy split). */
function colourGroups(label: string, swatches: readonly Swatch[], current: number, pick: (id: number) => void): HTMLElement[] {
  const row = swatches.map((c) => ({ id: c.index, name: c.name, hex: c.hex }));
  return [h("p", { class: "field-label" }, label), swatchRow(label, row, current, pick)];
}

function swatchRow(label: string, items: { id: number; name: string; hex: string }[], current: number, pick: (id: number) => void): HTMLElement {
  return h(
    "div",
    { class: "swatch-row", role: "radiogroup", "aria-label": label },
    ...items.map((it) =>
      h("button", { type: "button", role: "radio", class: "big-swatch", style: `--swatch: ${it.hex}`, title: it.name, "aria-label": it.name, "aria-checked": String(it.id === current), onclick: () => pick(it.id) }),
    ),
  );
}

/** The server's creation errors in the creator's words. */
function createError(e: ApiError): string {
  switch (e.code) {
    case "name_taken":
      return "Another student already has that name. Try another.";
    case "invalid_name":
      return e.message;
    case "character_limit":
      return e.message;
    case "invalid_appearance":
      return "That look is not available. Try a different choice.";
    default:
      return e.message;
  }
}

// ------------------------------------------------------------------ crests

const SVG_NS = "http://www.w3.org/2000/svg";

function svg(tag: string, attrs: Record<string, string | number>): SVGElement {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, String(v));
  return el;
}

/** A heraldic shield in the house's colours with its charge, drawn here (no artwork is copied). */
function crest(house: HouseInfo): SVGElement {
  const root = svg("svg", { viewBox: "0 0 100 112", class: "crest-svg", "aria-hidden": "true" });
  const shield = "M8 8 H92 V52 C92 80 72 96 50 106 C28 96 8 80 8 52 Z";
  root.append(
    svg("path", { d: shield, fill: house.colour, stroke: house.accent, "stroke-width": 3 }),
    svg("path", { d: "M14 14 H86 V52 C86 76 69 90 50 99 C31 90 14 76 14 52 Z", fill: "none", stroke: house.accent, "stroke-width": 1, opacity: 0.6 }),
  );
  const g = svg("g", { fill: house.accent, stroke: house.accent });
  for (const part of emblem(house.emblem ?? "")) g.append(part);
  root.append(g);
  return root;
}

function emblem(kind: string): SVGElement[] {
  switch (kind) {
    case "lion": {
      // A rayed mane around a face, with two ears.
      const pts: string[] = [];
      for (let i = 0; i < 24; i++) {
        const a = (i / 24) * Math.PI * 2;
        const r = i % 2 ? 15 : 22;
        pts.push(`${(50 + Math.sin(a) * r).toFixed(1)},${(52 - Math.cos(a) * r).toFixed(1)}`);
      }
      return [
        svg("polygon", { points: pts.join(" "), "stroke-width": 0 }),
        svg("circle", { cx: 50, cy: 53, r: 11, fill: "currentColor", class: "emblem-cut" }),
        svg("circle", { cx: 45.5, cy: 51, r: 1.6, "stroke-width": 0 }),
        svg("circle", { cx: 54.5, cy: 51, r: 1.6, "stroke-width": 0 }),
        svg("path", { d: "M46 58 Q50 61 54 58", fill: "none", "stroke-width": 1.6, "stroke-linecap": "round" }),
      ];
    }
    case "badger":
      return [
        svg("ellipse", { cx: 50, cy: 54, rx: 17, ry: 21, "stroke-width": 0 }),
        svg("path", { d: "M50 33 L46 64 Q50 70 54 64 Z", fill: "currentColor", class: "emblem-cut", "stroke-width": 0 }),
        svg("ellipse", { cx: 41, cy: 52, rx: 3.2, ry: 6, fill: "currentColor", class: "emblem-cut", "stroke-width": 0 }),
        svg("ellipse", { cx: 59, cy: 52, rx: 3.2, ry: 6, fill: "currentColor", class: "emblem-cut", "stroke-width": 0 }),
        svg("circle", { cx: 50, cy: 72, r: 3, "stroke-width": 0 }),
      ];
    case "eagle":
      return [
        svg("polygon", { points: "50,40 82,34 70,46 84,48 66,56 72,62 56,60 50,76 44,60 28,62 34,56 16,48 30,46 18,34", "stroke-width": 0 }),
        svg("circle", { cx: 50, cy: 34, r: 6, "stroke-width": 0 }),
        svg("path", { d: "M55 33 L61 35 L55 37 Z", "stroke-width": 0 }),
      ];
    case "serpent":
      return [
        svg("path", { d: "M36 80 C20 70 28 54 46 56 C64 58 72 44 60 32", fill: "none", "stroke-width": 7, "stroke-linecap": "round" }),
        svg("ellipse", { cx: 58, cy: 29, rx: 7, ry: 5, transform: "rotate(-30 58 29)", "stroke-width": 0 }),
        svg("path", { d: "M64 25 L70 21 M64 25 L71 25", fill: "none", "stroke-width": 1.4, "stroke-linecap": "round" }),
      ];
    default:
      return [svg("circle", { cx: 50, cy: 54, r: 14, "stroke-width": 0 })];
  }
}

