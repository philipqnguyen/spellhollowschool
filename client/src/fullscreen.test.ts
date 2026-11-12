// Fullscreen while playing (fullscreen.ts): entering takes the Esc lock, leaving
// any way releases it, refusals keep the game windowed, and Settings' button
// shows only while windowed.

import { describe, expect, it } from "vitest";
import { Fullscreen, LOCKED_KEYS, showEnterFullscreen, type FullscreenDoc, type KeyboardLock } from "./fullscreen";

class FakeDoc implements FullscreenDoc {
  fullscreenEnabled = true;
  fullscreenElement: Element | null = null;
  refuse = false;
  private listeners = new Set<() => void>();
  readonly documentElement: { requestFullscreen?: (o?: FullscreenOptions) => Promise<void> } = {
    requestFullscreen: () => {
      if (this.refuse) return Promise.reject(new TypeError("Permissions check failed"));
      this.set(true);
      return Promise.resolve();
    },
  };
  exitFullscreen(): Promise<void> {
    this.set(false);
    return Promise.resolve();
  }
  /** The browser entering or leaving (a held Esc, F11). */
  set(on: boolean): void {
    this.fullscreenElement = on ? ({} as Element) : null;
    for (const fn of [...this.listeners]) fn();
  }
  addEventListener(_t: "fullscreenchange", fn: () => void): void {
    this.listeners.add(fn);
  }
  removeEventListener(_t: "fullscreenchange", fn: () => void): void {
    this.listeners.delete(fn);
  }
}

class FakeKeyboard implements KeyboardLock {
  locked: string[] | null = null;
  lock(keys?: string[]): Promise<void> {
    this.locked = keys ?? [];
    return Promise.resolve();
  }
  unlock(): void {
    this.locked = null;
  }
}

describe("fullscreen", () => {
  it("enters, then locks Esc so a quick press reaches the game", async () => {
    const doc = new FakeDoc();
    const kb = new FakeKeyboard();
    const fs = new Fullscreen(doc, kb);
    expect(fs.supported).toBe(true);
    expect(fs.canLockEsc).toBe(true);
    expect(await fs.enter()).toBe(true);
    expect(fs.active).toBe(true);
    expect(kb.locked).toEqual(LOCKED_KEYS);
    expect(LOCKED_KEYS).toEqual(["Escape"]);
  });

  it("exits and releases the lock (Settings > Sign out)", async () => {
    const doc = new FakeDoc();
    const kb = new FakeKeyboard();
    const fs = new Fullscreen(doc, kb);
    await fs.enter();
    await fs.exit();
    expect(fs.active).toBe(false);
    expect(kb.locked).toBeNull();
    // Exiting while windowed is harmless.
    await fs.exit();
    expect(fs.active).toBe(false);
  });

  it("releases the lock when the browser leaves fullscreen (a held Esc), and takes it back on re-entry", async () => {
    const doc = new FakeDoc();
    const kb = new FakeKeyboard();
    const fs = new Fullscreen(doc, kb);
    await fs.enter();
    doc.set(false);
    expect(kb.locked).toBeNull();
    await fs.enter();
    expect(kb.locked).toEqual(["Escape"]);
  });

  it("stays windowed when the browser refuses, or cannot go fullscreen", async () => {
    const doc = new FakeDoc();
    doc.refuse = true;
    const kb = new FakeKeyboard();
    expect(await new Fullscreen(doc, kb).enter()).toBe(false);
    expect(kb.locked).toBeNull();

    const none = new FakeDoc();
    none.fullscreenEnabled = false;
    const fs = new Fullscreen(none, kb);
    expect(fs.supported).toBe(false);
    expect(await fs.enter()).toBe(false);
    expect(fs.active).toBe(false);

    const missing = new FakeDoc();
    delete missing.documentElement.requestFullscreen;
    expect(new Fullscreen(missing, kb).supported).toBe(false);
  });

  it("works without the Keyboard Lock API (Esc simply exits, as browsers do)", async () => {
    const doc = new FakeDoc();
    const fs = new Fullscreen(doc, null);
    expect(fs.canLockEsc).toBe(false);
    expect(await fs.enter()).toBe(true);
    await fs.exit();
    expect(fs.active).toBe(false);
  });

  it("reports every change to subscribers until they unsubscribe", async () => {
    const doc = new FakeDoc();
    const fs = new Fullscreen(doc, new FakeKeyboard());
    const seen: boolean[] = [];
    const off = fs.onChange((on) => seen.push(on));
    await fs.enter();
    doc.set(false);
    off();
    await fs.enter();
    expect(seen).toEqual([true, false]);
  });

  it("shows Settings' Enter fullscreen only while windowed and supported", () => {
    expect(showEnterFullscreen(true, false)).toBe(true);
    expect(showEnterFullscreen(true, true)).toBe(false);
    expect(showEnterFullscreen(false, false)).toBe(false);
  });
});
