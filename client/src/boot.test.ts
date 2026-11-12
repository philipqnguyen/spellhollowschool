// The screen flow's decisions (boot.ts): a saved session opens the picker, never the world.

import { describe, expect, it } from "vitest";
import { bootStep, characterStep, confirmsDeletion, stillPlaying } from "./boot";

const ada = { id: 11, name: "Ada Quill", house: "Skytalon" };
const bram = { id: 12, name: "Bram Thorn", house: "Burrowsby" };

describe("boot", () => {
  it("follows a verification link first, then a saved session, else sign in", () => {
    expect(bootStep("?verify=abc", true)).toEqual({ kind: "verify", token: "abc" });
    expect(bootStep("?verify=abc", false)).toEqual({ kind: "verify", token: "abc" });
    expect(bootStep("", true)).toEqual({ kind: "lookup" });
    expect(bootStep("?other=1", false)).toEqual({ kind: "login" });
  });

  it("follows a password reset link even with a saved session", () => {
    expect(bootStep("?reset=xyz", true)).toEqual({ kind: "reset", token: "xyz" });
    expect(bootStep("?reset=xyz", false)).toEqual({ kind: "reset", token: "xyz" });
    expect(bootStep("?reset=", true)).toEqual({ kind: "lookup" });
  });

  it("goes to the creator without a character", () => {
    expect(characterStep([])).toEqual({ kind: "create" });
    expect(characterStep([], "11")).toEqual({ kind: "create" });
  });

  it("shows the picker with a character, even only one and even remembered", () => {
    expect(characterStep([ada])).toEqual({ kind: "pick", characters: [ada], selected: 0, canCreate: true });
    expect(characterStep([ada], "11")).toEqual({ kind: "pick", characters: [ada], selected: 0, canCreate: true });
  });

  it("preselects the character played last, else the first", () => {
    expect(characterStep([ada, bram], "12")).toMatchObject({ kind: "pick", selected: 1 });
    // A deleted character's id (or a name, as older clients stored) selects the first.
    expect(characterStep([ada, bram], "99")).toMatchObject({ kind: "pick", selected: 0 });
    expect(characterStep([ada, bram], "Bram Thorn")).toMatchObject({ kind: "pick", selected: 0 });
    expect(characterStep([ada, bram], null)).toMatchObject({ kind: "pick", selected: 0 });
  });

  it("offers a new character only while the account has room", () => {
    const five = [1, 2, 3, 4, 5].map((id) => ({ id, name: `Student ${id}`, house: "Roarwing" }));
    expect(characterStep(five.slice(0, 4), null, 5)).toMatchObject({ kind: "pick", canCreate: true });
    expect(characterStep(five, null, 5)).toMatchObject({ kind: "pick", canCreate: false });
    expect(characterStep(five, "5", 5)).toMatchObject({ selected: 4 });
  });

  it("deletes only when the typed name matches, ignoring case and spaces", () => {
    expect(confirmsDeletion("Ada Quill", "Ada Quill")).toBe(true);
    expect(confirmsDeletion("  ada quill ", "Ada Quill")).toBe(true);
    expect(confirmsDeletion("ÉMILE", "Émile")).toBe(true);
    expect(confirmsDeletion("Ada", "Ada Quill")).toBe(false);
    expect(confirmsDeletion("", "Ada Quill")).toBe(false);
  });

  it("reconnects only while the session still plays the character the game started for", () => {
    expect(stillPlaying(ada, ada)).toBe(true);
    // Another window picked Bram on the shared session: back to the picker.
    expect(stillPlaying(bram, ada)).toBe(false);
    // Ada was deleted, or the session picked nothing.
    expect(stillPlaying(null, ada)).toBe(false);
  });
});
