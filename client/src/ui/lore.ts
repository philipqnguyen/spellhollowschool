// The lore book (text from the server): the lore entries
// a character has unlocked in class (History of Magic), from `GET /api/lore`.
// The server sends each unlocked entry's title and text; the client bundles
// no lore at all (data/classes/lore/lore.json stays on the server, so locked
// entries cannot be read in the build). Opened with /lore. Pages of entries
// are asked for with the server's `?after=<cursor>`.

import "./wayfinding.css";
import { authedRequest } from "../api";
import { focusOrigin, h, restoreFocus } from "./dom";

export interface LoreEntry {
  id: number;
  title: string;
  text: string;
}

/** One page of `GET /api/lore`: the entries unlocked (newest first) and the cursor of the next page. */
export interface LorePage {
  entries: LoreEntry[];
  /** The opaque keyset cursor for `?after=`, or null on the last page. */
  next: string | null;
}

/**
 * Reads a page of the lore route, the server's keyset page `{ items: [{ id, title, text,
 * unlocked_at }], next: string | null }`. An item without an id is dropped; a missing title or
 * text reads as empty (the book still lists the entry).
 */
export function parseLorePage(body: unknown): LorePage {
  const obj = body && typeof body === "object" && !Array.isArray(body) ? (body as Record<string, unknown>) : null;
  const list = obj?.items;
  const entries: LoreEntry[] = [];
  if (Array.isArray(list)) {
    for (const e of list) {
      const o = e && typeof e === "object" ? (e as Record<string, unknown>) : null;
      const id = o?.id;
      if (typeof id !== "number" || !Number.isInteger(id)) continue;
      entries.push({ id, title: typeof o!.title === "string" ? o!.title : "", text: typeof o!.text === "string" ? o!.text : "" });
    }
  }
  const next = obj?.next;
  return { entries, next: typeof next === "string" && next ? next : null };
}

/** The newest entry unlocked, if it is `id` (the results card names a new entry by it). */
export async function fetchLoreEntry(id: number): Promise<LoreEntry | null> {
  const page = parseLorePage(await authedRequest<unknown>("GET", "/lore?limit=5"));
  return page.entries.find((e) => e.id === id) ?? null;
}

export class LoreBook {
  readonly root: HTMLElement;
  private readonly body: HTMLElement;
  private origin: HTMLElement | null = null;
  private generation = 0;

  constructor() {
    this.body = h("div", { class: "lore-body" });
    this.root = h(
      "section",
      { class: "monstra-viam lore panel", role: "dialog", "aria-label": "Lore book", hidden: true },
      h("header", { class: "craft-head" }, h("h2", {}, "Lore book"), h("button", { type: "button", class: "icon-button", "aria-label": "Close", title: "Close (Esc)", onclick: () => this.toggle(false) }, "×")),
      this.body,
    );
  }

  get isOpen(): boolean {
    return !this.root.hidden;
  }

  toggle(open = !this.isOpen): void {
    if (open === this.isOpen) return;
    if (open) this.origin = focusOrigin();
    this.root.hidden = !open;
    if (open) void this.load();
    else restoreFocus(this.origin, this.root);
  }

  handleKey(e: KeyboardEvent): boolean {
    if (e.key === "Escape" && this.isOpen) {
      this.toggle(false);
      return true;
    }
    return false;
  }

  private async load(): Promise<void> {
    const gen = ++this.generation;
    this.body.replaceChildren(h("p", { class: "muted-line" }, "Turning the pages…"));
    const entries: LoreEntry[] = [];
    try {
      let after: string | null = null;
      for (let page = 0; page < 20; page++) {
        const path: string = after === null ? "/lore" : `/lore?after=${encodeURIComponent(after)}`;
        const got = parseLorePage(await authedRequest<unknown>("GET", path));
        entries.push(...got.entries);
        if (got.next === null || !got.entries.length) break;
        after = got.next;
      }
    } catch {
      if (gen === this.generation) this.body.replaceChildren(h("p", { class: "muted-line" }, "The lore book will not open just now. Please try again."));
      return;
    }
    if (gen !== this.generation) return;
    this.body.replaceChildren(
      ...(entries.length
        ? entries.map((e) => h("article", { class: "lore-entry" }, h("h3", {}, e.title || "An untitled page"), h("p", {}, e.text)))
        : [h("p", { class: "muted-line" }, "No lore yet. History of Magic lessons and the castle's residents add its stories here.")]),
    );
  }
}
