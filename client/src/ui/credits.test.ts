// The Credits panel's data (`virtual:shs-credits`, client/scripts/credits.mjs):
// every section has something to show, with a licence and safe links.

import { describe, expect, it } from "vitest";
import credits from "virtual:shs-credits";

describe("the credits the panel shows", () => {
  it("has every kind of credit, each with a licence", () => {
    const ids = credits.sections.map((s) => s.id);
    for (const id of ["models", "hair", "animation", "sound", "fonts", "code"]) expect(ids).toContain(id);
    for (const s of credits.sections) {
      expect(s.entries.length, s.id).toBeGreaterThan(0);
      for (const e of s.entries) {
        expect(e.work, s.id).toBeTruthy();
        expect(e.licence, `${s.id}: ${e.work}`).toBeTruthy();
      }
    }
  });

  it("links only to http(s) pages", () => {
    const links = credits.sections.flatMap((s) => s.entries.flatMap((e) => [e.link, e.licenceUrl])).filter((l): l is string => !!l);
    links.push(credits.game.repository, ...credits.game.licences.map((l) => l.licenceUrl));
    expect(links.length).toBeGreaterThan(20);
    for (const l of links) expect(l).toMatch(/^https?:\/\/[^\s"<>]+$/);
  });

  it("credits the CC BY sculpts by name", () => {
    const models = credits.sections.find((s) => s.id === "models")!;
    expect(models.entries.map((e) => e.author)).toEqual(expect.arrayContaining(["ALexzart", "DJP3D"]));
    expect(credits.notices).toBe("THIRD-PARTY-NOTICES.txt");
  });
});
