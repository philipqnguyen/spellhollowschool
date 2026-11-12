// Fullscreen while playing (docs/architecture.md "Client").
//
// Entering the world from the character picker asks for fullscreen inside
// the click (browsers allow it only from a user gesture); a refusal leaves
// the game windowed. In fullscreen, Chromium's Keyboard Lock API takes Esc,
// so a quick Esc reaches the game (closing windows, Settings) and holding it
// exits fullscreen, which is the browser's own behaviour under the lock.
// Without the API, Esc exits fullscreen as browsers always do. Settings
// offers "Enter fullscreen" whenever the game is windowed; Settings > Sign
// out leaves fullscreen and releases the lock.

/** The parts of `document` used here (a fake in the tests). */
export interface FullscreenDoc {
  readonly fullscreenEnabled: boolean;
  readonly fullscreenElement: Element | null;
  readonly documentElement: { requestFullscreen?: (options?: FullscreenOptions) => Promise<void> };
  exitFullscreen(): Promise<void>;
  addEventListener(type: "fullscreenchange", fn: () => void): void;
  removeEventListener(type: "fullscreenchange", fn: () => void): void;
}

/** The Keyboard Lock API (`navigator.keyboard`, Chromium only; not in TypeScript's DOM types). */
export interface KeyboardLock {
  lock(keyCodes?: string[]): Promise<void>;
  unlock(): void;
}

/** The keys held while fullscreen: a quick Esc reaches the game, a held one exits. */
export const LOCKED_KEYS = ["Escape"];

export class Fullscreen {
  private readonly doc: FullscreenDoc;
  private readonly keyboard: KeyboardLock | null;

  constructor(doc: FullscreenDoc = document, keyboard: KeyboardLock | null = navigatorKeyboard()) {
    this.doc = doc;
    this.keyboard = keyboard;
    // Leaving fullscreen any way (a held Esc, F11, Sign out) lets the keys go;
    // entering again takes them back.
    doc.addEventListener("fullscreenchange", () => {
      if (!this.active) this.unlock();
    });
  }

  /** The page may go fullscreen (false on iPhone Safari and in iframes without permission). */
  get supported(): boolean {
    return this.doc.fullscreenEnabled === true && typeof this.doc.documentElement.requestFullscreen === "function";
  }

  get active(): boolean {
    return this.doc.fullscreenElement !== null && this.doc.fullscreenElement !== undefined;
  }

  /** Whether the Keyboard Lock API is there (a held Esc exits; a quick one reaches the game). */
  get canLockEsc(): boolean {
    return this.keyboard !== null;
  }

  /**
   * Fullscreen, then the Esc lock. Call it synchronously inside a click (or
   * key) handler: the browser checks the gesture when `requestFullscreen` is
   * called. Never throws or rejects; a refusal keeps the game windowed.
   */
  enter(): Promise<boolean> {
    if (!this.supported) return Promise.resolve(false);
    if (this.active) {
      this.lock();
      return Promise.resolve(true);
    }
    let pending: Promise<void>;
    try {
      pending = this.doc.documentElement.requestFullscreen!({ navigationUI: "hide" });
    } catch {
      return Promise.resolve(false);
    }
    return Promise.resolve(pending).then(
      () => {
        this.lock();
        return true;
      },
      () => false,
    );
  }

  /** Windowed again, the keys released. Never rejects. */
  exit(): Promise<void> {
    this.unlock();
    if (!this.active) return Promise.resolve();
    try {
      return Promise.resolve(this.doc.exitFullscreen()).catch(() => undefined);
    } catch {
      return Promise.resolve();
    }
  }

  /** Calls `fn` on every change (entered or left, by any means); returns the unsubscribe. */
  onChange(fn: (active: boolean) => void): () => void {
    const handler = () => fn(this.active);
    this.doc.addEventListener("fullscreenchange", handler);
    return () => this.doc.removeEventListener("fullscreenchange", handler);
  }

  private lock(): void {
    this.keyboard?.lock(LOCKED_KEYS).catch(() => undefined);
  }

  private unlock(): void {
    try {
      this.keyboard?.unlock();
    } catch {
      // Nothing held.
    }
  }
}

function navigatorKeyboard(): KeyboardLock | null {
  const k = (globalThis.navigator as { keyboard?: Partial<KeyboardLock> } | undefined)?.keyboard;
  return k && typeof k.lock === "function" && typeof k.unlock === "function" ? (k as KeyboardLock) : null;
}

/**
 * Settings' "Enter fullscreen" button: shown only while the game is windowed
 * and the page can go fullscreen.
 */
export function showEnterFullscreen(supported: boolean, active: boolean): boolean {
  return supported && !active;
}

/** The one controller the page uses (main.ts and the HUD share it). */
let shared: Fullscreen | null = null;
export function fullscreen(): Fullscreen {
  shared ??= new Fullscreen();
  return shared;
}
