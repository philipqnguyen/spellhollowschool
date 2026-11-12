// The Wishing Room's choice card: using the door that
// appeared in the blank wall asks which room to walk into, the Lost
// Property Vault or the Gathering Room (wishing-room-state.ts `roomChoices`).
// Choosing sends `EnterWishingRoom` (the caller's `onChoose`); the card
// closes, and closes by itself when the door goes.

import type { WishingRoomKind } from "../wasm/sim_wasm";
import { focusOrigin, h, restoreFocus } from "./dom";
import type { RoomChoice } from "./wishing-room-state";
import "./wishing-room.css";

export class RoomChoiceCard {
  readonly root: HTMLElement;
  private readonly list: HTMLElement;
  private readonly clock: HTMLElement;
  private origin: HTMLElement | null = null;

  constructor(private readonly onChoose: (room: WishingRoomKind) => void) {
    this.list = h("div", { class: "wishing-room-choices" });
    this.clock = h("p", { class: "wishing-room-clock" }, "");
    this.root = h(
      "section",
      { class: "wishing-room-card panel", role: "dialog", "aria-label": "The Wishing Room", hidden: true },
      h("header", { class: "craft-head" }, h("h2", {}, "The Wishing Room"), h("button", { type: "button", class: "icon-button", "aria-label": "Close", title: "Close (Esc)", onclick: () => this.close() }, "×")),
      h("p", { class: "wishing-room-sub" }, "The door waits for you alone. Make your wish, and step through."),
      this.list,
      this.clock,
    );
  }

  get isOpen(): boolean {
    return !this.root.hidden;
  }

  /** Shows the choices (`secondsLeft`: how long the door stays). */
  open(choices: readonly RoomChoice[], secondsLeft: number): void {
    if (!this.isOpen) this.origin = focusOrigin();
    this.list.replaceChildren(
      ...choices.map((c) =>
        h(
          "button",
          { type: "button", class: "wishing-room-choice", "data-room": c.room, onclick: () => (this.close(), this.onChoose(c.room)) },
          h("span", { class: "wishing-room-name" }, c.name),
          h("span", { class: "wishing-room-blurb" }, c.blurb),
        ),
      ),
    );
    this.root.hidden = false;
    this.tick(secondsLeft);
    (this.list.querySelector("button") as HTMLButtonElement | null)?.focus({ preventScroll: true });
  }

  /** The door's countdown; 0 closes the card (the door has gone). */
  tick(secondsLeft: number): void {
    if (!this.isOpen) return;
    if (secondsLeft <= 0) {
      this.close();
      return;
    }
    this.clock.textContent = `The door fades in ${secondsLeft} s.`;
  }

  close(): void {
    if (!this.isOpen) return;
    this.root.hidden = true;
    restoreFocus(this.origin, this.root);
    this.origin = null;
  }

  handleKey(e: KeyboardEvent): boolean {
    if (e.key === "Escape" && this.isOpen) {
      this.close();
      return true;
    }
    return false;
  }
}
