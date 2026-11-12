// Self-hosted fonts (castle D1, docs/art/castle-kit.md): the page asks no
// host but its own for anything. index.html links no other origin; the font
// stylesheet imports the @fontsource packages, at exact versions, whose
// files exist; and the families are the ones style.css names.

import { describe, expect, it } from "vitest";
import { existsSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";

const CLIENT = join(__dirname, "..");
const require = createRequire(join(CLIENT, "package.json"));

describe("self-hosted fonts", () => {
  it("index.html links nothing on another host", () => {
    const html = readFileSync(join(CLIENT, "index.html"), "utf8");
    // Attribute URLs only (the inline SVG icon names its XML namespace, which is never fetched).
    const urls = [...html.matchAll(/(?:href|src)="([^"]+)"/g)].map((m) => m[1]!);
    expect(urls).toContain("/src/fonts.css");
    for (const u of urls) expect(u.startsWith("/") || u.startsWith("data:"), u).toBe(true);
    expect(html).not.toMatch(/googleapis|gstatic/);
  });

  it("imports the font packages at exact versions, and every file they name exists", () => {
    const pkg = JSON.parse(readFileSync(join(CLIENT, "package.json"), "utf8")) as { dependencies: Record<string, string> };
    for (const name of ["@fontsource/cinzel", "@fontsource/eb-garamond"]) expect(pkg.dependencies[name], name).toMatch(/^\d+\.\d+\.\d+$/);
    const css = readFileSync(join(CLIENT, "src/fonts.css"), "utf8");
    const imports = [...css.matchAll(/@import "([^"]+)";/g)].map((m) => m[1]!);
    expect(imports).toEqual(["@fontsource/cinzel/500.css", "@fontsource/cinzel/700.css", "@fontsource/eb-garamond/400.css", "@fontsource/eb-garamond/600.css", "@fontsource/eb-garamond/400-italic.css"]);
    for (const spec of imports) {
      const file = require.resolve(spec);
      const faces = readFileSync(file, "utf8");
      const srcs = [...faces.matchAll(/url\(([^)]+)\)/g)].map((m) => m[1]!);
      expect(srcs.length, spec).toBeGreaterThan(0);
      for (const s of srcs) {
        expect(s.startsWith("./"), `${spec}: ${s}`).toBe(true);
        expect(existsSync(join(dirname(file), s)), `${spec}: ${s}`).toBe(true);
      }
    }
  });

  it("ships each font's licence (OFL 1.1: the copyright notice and the licence travel with the font)", () => {
    for (const [file, owner] of [
      ["cinzel-OFL.txt", "The Cinzel Project Authors"],
      ["eb-garamond-OFL.txt", "The EB Garamond Project Authors"],
    ] as const) {
      const text = readFileSync(join(CLIENT, "public/fonts", file), "utf8");
      expect(text, file).toContain(owner);
      expect(text, file).toContain("SIL OPEN FONT LICENSE Version 1.1");
    }
  });

  it("provides the families and weights style.css asks for", () => {
    const style = readFileSync(join(CLIENT, "src/style.css"), "utf8");
    expect(style).toMatch(/--serif: "EB Garamond"/);
    expect(style).toMatch(/--display: "Cinzel"/);
    const faces = (spec: string) => readFileSync(require.resolve(spec), "utf8");
    expect(faces("@fontsource/cinzel/500.css")).toMatch(/font-family: 'Cinzel';[\s\S]*font-weight: 500;/);
    expect(faces("@fontsource/eb-garamond/400-italic.css")).toMatch(/font-family: 'EB Garamond';[\s\S]*font-style: italic;/);
  });
});
