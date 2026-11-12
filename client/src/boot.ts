// The screen flow's decisions (main.ts draws the screens; docs/architecture.md
// "Client"). Kept pure for the tests.

import type { Character } from "./api";

/** What the page opens on. */
export type BootStep = { kind: "verify"; token: string } | { kind: "reset"; token: string } | { kind: "login" } | { kind: "lookup" };

/**
 * A `?verify=` or `?reset=` (password reset) link first, then a saved session goes to look the character up,
 * else sign in. A session never opens the world by itself: `lookup` ends at
 * the picker or the creator (`characterStep`).
 */
export function bootStep(search: string, hasSession: boolean): BootStep {
  const params = new URLSearchParams(search);
  const token = params.get("verify");
  if (token) return { kind: "verify", token };
  const reset = params.get("reset");
  if (reset) return { kind: "reset", token: reset };
  return hasSession ? { kind: "lookup" } : { kind: "login" };
}

/**
 * After the lookup: the creator when there is no character, else the picker.
 * Never straight into the world. `canCreate` offers a new character while
 * the account has fewer than its `max`.
 */
export type CharacterStep = { kind: "create" } | { kind: "pick"; characters: Character[]; selected: number; canCreate: boolean };

/**
 * The picker lists the account's characters (up to `max`) with the one
 * played last selected (`remembered`, its id; the first otherwise).
 * Entering the world is always the player's click on the picker, which is also
 * the gesture fullscreen needs.
 */
export function characterStep(characters: readonly Character[], remembered: string | null = null, max = 5): CharacterStep {
  if (characters.length === 0) return { kind: "create" };
  const i = remembered ? characters.findIndex((c) => String(c.id) === remembered) : -1;
  return { kind: "pick", characters: [...characters], selected: Math.max(0, i), canCreate: characters.length < max };
}

/** Whether `typed` confirms deleting the character `name`: its name, ignoring case and surrounding spaces (as the server checks). */
export function confirmsDeletion(typed: string, name: string): boolean {
  return typed.trim().toLowerCase() === name.trim().toLowerCase();
}

/**
 * Whether a game client started for `started` may reconnect: the session
 * (shared by every window of the browser) still plays that character.
 * `playing` is `GET /api/characters/me`, null for 404 (none picked, or it
 * was deleted). Otherwise the client leaves for the picker.
 */
export function stillPlaying(playing: Character | null, started: Character): boolean {
  return playing !== null && playing.id === started.id;
}
