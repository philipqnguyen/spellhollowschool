// A tiny element builder so the screens read as markup without a framework.

type Child = Node | string | null | undefined | false;
type Attrs = Record<string, string | number | boolean | EventListener | undefined>;

/**
 * `h("button", { class: "primary", onclick: go }, "Enter")`. Attributes whose
 * name starts with `on` are listeners; `true` sets a boolean attribute and
 * `false`/`undefined` omits it. Strings become text nodes (never HTML).
 */
export function h<K extends keyof HTMLElementTagNameMap>(tag: K, attrs: Attrs = {}, ...children: Child[]): HTMLElementTagNameMap[K] {
  const el = document.createElement(tag);
  for (const [name, value] of Object.entries(attrs)) {
    if (value === undefined || value === false) continue;
    if (name.startsWith("on") && typeof value === "function") {
      el.addEventListener(name.slice(2), value);
    } else {
      el.setAttribute(name, value === true ? "" : String(value));
    }
  }
  for (const child of children) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child);
  }
  return el;
}

/** Whether `el` takes typed text (an input, a text area, an editable element). */
function takesText(el: Element): boolean {
  if (el instanceof HTMLTextAreaElement) return true;
  if (el instanceof HTMLElement && el.isContentEditable) return true;
  if (!(el instanceof HTMLInputElement)) return false;
  return !["button", "checkbox", "radio", "range", "color", "submit", "reset", "file", "image"].includes(el.type);
}

/**
 * The element to give focus back to when a window that is opening now
 * closes: the button that opened it, say. Never a text field: a window
 * opened by a chat command (`/mail`) would otherwise hand the keyboard back
 * to the chat box on closing, and every key after that would go into chat
 * instead of moving the player. Then nothing is focused (the game has the
 * keys).
 */
export function focusOrigin(): HTMLElement | null {
  const el = document.activeElement;
  if (!(el instanceof HTMLElement) || el === document.body || takesText(el)) return null;
  return el;
}

/**
 * Focus back to `origin` (from `focusOrigin`) when it is still on the page
 * and shown; otherwise whatever has focus inside the closed window (`root`)
 * lets go, so the keys go back to the game.
 */
export function restoreFocus(origin: HTMLElement | null, root?: HTMLElement): void {
  if (origin && origin.isConnected && origin.getClientRects().length > 0 && !takesText(origin)) {
    origin.focus();
    return;
  }
  const active = document.activeElement;
  if (active instanceof HTMLElement && active !== document.body && (!root || root.contains(active))) active.blur();
}

/** Replaces every child of `parent` with `children`. */
export function mount(parent: HTMLElement, ...children: Child[]): void {
  parent.replaceChildren();
  for (const child of children) if (child) parent.append(child);
}
