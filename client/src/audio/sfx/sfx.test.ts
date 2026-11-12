// The sound definitions' acceptance (docs/architecture.md "Sound and music"):
// every effect and ambience id has a definition; the CC0 files and their
// records agree; beds are flat; every synthesized recipe renders (against a
// stand-in context that enforces WebAudio's rules), stays inside its length
// and renders the same twice; the computed loops are seamless; every synth
// has a measured level.

import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { isSoundId, soundEntry, SOUNDS } from "../catalogue";
import type { SoundDef } from "../types";
import { missingDefs, RAW_SOUND_DEFS, SOUND_DEFS } from "./defs";
import { biquad, onePole, white } from "./dsp";
import { SYNTH_LEVELS } from "./levels";
import { MockContext } from "./mock-context";

const here = dirname(fileURLToPath(import.meta.url));
const repo = join(here, "../../../..");
const records = (JSON.parse(readFileSync(join(repo, "data/audio/sources.json"), "utf8")) as { files: { file: string; ids: string[] }[] }).files;
const defs = Object.entries(SOUND_DEFS) as [string, SoundDef][];
const SR = 22050;

function render(def: Extract<SoundDef, { kind: "synth" }>, variant: number): MockContext {
  const ctx = new MockContext(def.channels ?? 1, Math.ceil(def.seconds * SR), SR);
  def.draw(ctx as unknown as OfflineAudioContext, ctx.destination as unknown as AudioNode, variant);
  return ctx;
}

describe("sound definitions", () => {
  it("define every effect and ambience id (no placeholder beeps)", () => {
    expect(missingDefs()).toEqual([]);
  });

  it("play files only for ids the catalogue marks as recordings, and the reverse where a recording was found", () => {
    for (const [id, d] of defs) if (d.kind === "file") expect(soundEntry(id as never).source, id).toBe("cc0");
  });

  it("list files that exist and are recorded for that id, and every record's ids use the file", () => {
    const byFile = new Map(records.map((r) => [r.file, r]));
    const users = new Map<string, Set<string>>();
    for (const [id, d] of defs) {
      if (d.kind !== "file") continue;
      for (const f of d.files) {
        const rec = byFile.get(f);
        expect(rec, `${id}: ${f} has no record`).toBeDefined();
        expect(rec!.ids, `${f}'s record lacks ${id}`).toContain(id);
        expect(existsSync(join(repo, "client/public/audio", f)), f).toBe(true);
        if (!users.has(f)) users.set(f, new Set());
        users.get(f)!.add(id);
      }
    }
    for (const r of records) for (const id of r.ids) expect(users.get(r.file)?.has(id), `${r.file} records ${id}, which does not play it`).toBe(true);
  });

  it("keep beds flat: layers are loops defined as files or synths, sprinkles are defined", () => {
    for (const [id, d] of defs) {
      if (d.kind !== "bed") continue;
      // An indoor bed may be sprinkles alone (quiet: no noise bed indoors), or nothing at all (silence).
      for (const l of d.layers) {
        const ld = SOUND_DEFS[l.id as keyof typeof SOUND_DEFS];
        expect(ld, `${id} layer ${l.id}`).toBeDefined();
        expect(ld!.kind === "file" || ld!.kind === "synth", `${id} layer ${l.id} is a ${ld!.kind}`).toBe(true);
        expect(l.gain).toBeGreaterThan(0);
        expect(l.gain).toBeLessThanOrEqual(1.5);
      }
      for (const s of d.sprinkles) {
        expect(SOUND_DEFS[s.id as keyof typeof SOUND_DEFS], `${id} sprinkle ${s.id}`).toBeDefined();
        expect(s.everyS[0]).toBeGreaterThan(0);
        expect(s.everyS[1]).toBeGreaterThanOrEqual(s.everyS[0]);
        expect(s.distanceM[1]).toBeGreaterThanOrEqual(s.distanceM[0]);
      }
    }
  });

  it("loop where the catalogue loops: looping synths are seamless", () => {
    for (const [id, d] of defs) {
      const loop = soundEntry(id as never).loop;
      if (d.kind === "synth") expect(!!d.seamless, `${id}: seamless iff a loop`).toBe(loop);
      if (d.kind === "bed") expect(loop, id).toBe(true);
    }
  });

  it("have gains in a sane range", () => {
    for (const [id, d] of defs) if (d.kind === "file" || d.kind === "synth") expect(d.gain ?? 1, id).toBeGreaterThan(0), expect(d.gain ?? 1, id).toBeLessThanOrEqual(8);
  });
});

describe("synthesized recipes", () => {
  const synths = defs.filter((e): e is [string, Extract<SoundDef, { kind: "synth" }>] => e[1].kind === "synth");

  it("cover a good share of the catalogue", () => {
    expect(synths.length).toBeGreaterThan(80);
    expect(SOUNDS.length).toBeGreaterThan(synths.length);
  });

  it.each(synths.map(([id, d]) => [id, d] as const))("%s renders within its length, deterministically", (id, d) => {
    expect(d.seconds).toBeGreaterThan(0);
    expect(d.seconds).toBeLessThanOrEqual(60);
    for (let v = 0; v < (d.variants ?? 1); v++) {
      const a = render(d, v);
      expect(a.sources().length, `${id} v${v} starts nothing`).toBeGreaterThan(0);
      for (const s of a.sources()) {
        expect(s.started, `${id}: a source never started`).not.toBeNull();
        expect(s.started!).toBeLessThan(d.seconds);
        if (s.stopped !== null) expect(s.stopped, `${id}: a voice runs past ${d.seconds}s`).toBeLessThanOrEqual(d.seconds + 0.06);
        else expect((s as unknown as { buffer?: { duration: number }; loop?: boolean }).loop, `${id}: an unstopped source must be a one-shot buffer`).toBeFalsy();
      }
      for (const b of a.buffers) for (let c = 0; c < b.numberOfChannels; c++) for (const x of b.getChannelData(c)) if (!Number.isFinite(x)) throw new Error(`${id}: non-finite sample`);
      const again = render(d, v);
      expect(JSON.stringify(again.log), `${id} v${v} renders differently twice`).toBe(JSON.stringify(a.log));
    }
  });

  it("have a measured level", () => {
    for (const [id] of synths) expect(SYNTH_LEVELS[id], `${id} has no level in levels.ts (preview page: Measure all)`).toBeDefined();
    for (const id of Object.keys(SYNTH_LEVELS)) expect(isSoundId(id) && RAW_SOUND_DEFS[id as keyof typeof RAW_SOUND_DEFS]?.kind === "synth", `levels.ts: ${id}`).toBe(true);
  });
});

describe("computed loops (dsp.ts)", () => {
  it("filters wrap without a seam: the second pass equals a long run's steady state", () => {
    const n = 4000;
    const x = white(n, 7);
    const wrapped = biquad(x, "bandpass", 700, 2, SR);
    const long = new Float32Array(n * 4);
    for (let k = 0; k < 4; k++) long.set(x, k * n);
    const steady = biquad(long, "bandpass", 700, 2, SR).subarray(n * 3);
    // biquad runs its input twice, so compare with the run's fourth lap.
    let err = 0;
    for (let i = 0; i < n; i++) err = Math.max(err, Math.abs(wrapped[i]! - steady[i]!));
    expect(err).toBeLessThan(1e-3);
    const lp = onePole(x, 100, SR);
    expect(Math.abs(lp[0]! - lp[n - 1]!)).toBeLessThan(0.05);
  });

  it("every looping synth repeats without a jump at the seam", () => {
    for (const [id, d] of defs) {
      if (d.kind !== "synth" || !d.seamless) continue;
      const ctx = render(d, 0);
      expect(ctx.buffers.length, id).toBeGreaterThan(0);
      const b = ctx.buffers[ctx.buffers.length - 1]!;
      for (let c = 0; c < b.numberOfChannels; c++) {
        const y = b.getChannelData(c);
        let step = 0;
        for (let i = 1; i < y.length; i++) step += Math.abs(y[i]! - y[i - 1]!);
        step /= y.length - 1;
        const seam = Math.abs(y[0]! - y[y.length - 1]!);
        expect(seam, `${id} ch${c}: seam ${seam.toFixed(4)} vs mean step ${step.toFixed(4)}`).toBeLessThan(step * 6 + 1e-4);
      }
    }
  });
});
