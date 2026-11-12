// The page-load shader race (castle M5c: a console error on page load): a
// shader of ours that names a Babylon
// include (`#include<x>`) must register that include itself, by importing
// its module. Babylon's standard material imports its own includes lazily,
// just before its first compile; a ShaderMaterial imports nothing. So the
// character blobs, compiled before any standard material on a page that
// loaded with characters in view, found `instancesDeclaration` missing,
// fetched it over HTTP, got the page's index.html, and Babylon kept that as
// the include (ShaderStore.IncludesShadersStore): every toon material
// compiled after it failed. This checks every client source file, and the
// blobs' registered shader at run time.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it, vi } from "vitest";

const SRC = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const BABYLON_INCLUDES = resolve(SRC, "../../node_modules/@babylonjs/core/Shaders/ShadersInclude");

function sources(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) out.push(...sources(p));
    else if (/\.ts$/.test(name) && !/\.test\.ts$/.test(name) && !/\.d\.ts$/.test(name)) out.push(p);
  }
  return out;
}

const INCLUDE = /#include<(\w+)>/g;

/** The includes `code` names, and the includes those name in turn (Babylon's include modules). */
function includesOf(code: string): Set<string> {
  const out = new Set<string>();
  const queue = [...code.matchAll(INCLUDE)].map((m) => m[1]!);
  while (queue.length) {
    const n = queue.pop()!;
    if (out.has(n)) continue;
    out.add(n);
    let inner = "";
    try {
      inner = readFileSync(join(BABYLON_INCLUDES, `${n}.js`), "utf8");
    } catch {
      continue; // not one of Babylon's: reported below as not imported
    }
    for (const m of inner.matchAll(INCLUDE)) queue.push(m[1]!);
  }
  return out;
}

describe("shader includes are registered by the code that uses them (no HTTP include fetch)", () => {
  it("every client file naming `#include<x>` imports Babylon's ShadersInclude/x", () => {
    const missing: string[] = [];
    let files = 0;
    for (const file of sources(SRC)) {
      const code = readFileSync(file, "utf8");
      const names = includesOf(code);
      if (names.size === 0) continue;
      files++;
      for (const n of names) {
        const imported = new RegExp(`import\\s+(?:[^;]*?from\\s+)?["']@babylonjs/core/Shaders/ShadersInclude/${n}(?:\\.js)?["']`).test(code);
        if (!imported) missing.push(`${file.slice(SRC.length + 1)}: ${n}`);
      }
    }
    expect(files).toBeGreaterThanOrEqual(2); // the blobs and the outline hull
    expect(missing).toEqual([]);
  });

  it("the character blobs' shader finds its includes registered by importing blobs.ts alone", async () => {
    vi.resetModules();
    const { ShaderStore } = await import("@babylonjs/core/Engines/shaderStore");
    for (const n of ["instancesDeclaration", "instancesVertex"]) delete ShaderStore.IncludesShadersStore[n];
    await import("./characters/blobs");
    const vertex = ShaderStore.ShadersStore["characterBlobVertexShader"]!;
    expect(vertex).toContain("#include<instancesDeclaration>");
    for (const n of includesOf(vertex)) {
      expect(ShaderStore.IncludesShadersStore[n], n).toBeTypeOf("string");
      expect(ShaderStore.IncludesShadersStore[n]).not.toMatch(/<!doctype|<html/i);
    }
  });
});
