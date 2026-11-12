// The dev-only listening page (listen.html, served by Vite in development at
// /src/audio/music/listen.html; not part of the build). Plays every piece with
// the game's engine, and offers `window.__music.render` for the preview tool
// (render.mjs): one piece through an OfflineAudioContext, as a WAV.

import type { Quality } from "../../settings";
import type { MusicId, StingerId } from "../catalogue";
import { FADES } from "../rules";
import { compile, pulseBpm } from "./compile";
import { createEngine, type MusicEngineFull } from "./engine";
import { SCORES } from "./library";
import type { Score } from "./score";

let ctx: AudioContext | null = null;
let engine: MusicEngineFull | null = null;
const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
const quality = () => $<HTMLSelectElement>("quality").value as Quality;

function ensure(): MusicEngineFull {
  if (!ctx) ctx = new AudioContext();
  void ctx.resume();
  if (!engine) engine = createEngine(ctx, ctx.destination, quality());
  return engine;
}

/** The scores with their rest shortened (the page's checkbox), so a loop can be heard round. */
function noRest(s: Score): void {
  if ($<HTMLInputElement>("norest").checked) (s as { restS?: readonly [number, number] }).restS = [2, 2];
}

function row(s: Score): HTMLTableRowElement {
  const tr = document.createElement("tr");
  const day = compile(s);
  const isStinger = s.id.startsWith("stinger.");
  const instruments = [...new Set(day.events.map((e) => e.inst))].join(", ");
  tr.innerHTML = `<td><b>${s.title}</b><br><span class="small">${s.id}</span></td>
    <td class="small">${s.style}, ${Math.round(pulseBpm(s))} bpm, ${s.meter[0]}/${s.meter[1]}, ${day.end.toFixed(0)} s<br>${instruments}</td><td></td>`;
  const cell = tr.lastElementChild as HTMLTableCellElement;
  const btn = (label: string, fn: () => void) => {
    const b = document.createElement("button");
    b.textContent = label;
    b.onclick = fn;
    cell.append(b, " ");
  };
  if (isStinger) {
    btn("Play", () => ensure().stinger(s.id as StingerId));
  } else {
    btn("Day", () => {
      noRest(s);
      ensure().setCue({ id: s.id as MusicId, night: false }, FADES.musicOut);
    });
    btn("Night", () => {
      noRest(s);
      ensure().setCue({ id: s.id as MusicId, night: true }, FADES.musicOut);
    });
  }
  return tr;
}

const list = $<HTMLTableElement>("list");
list.innerHTML = "<tr><th>Piece</th><th>Form</th><th></th></tr>";
const sorted = [...SCORES.values()].sort((a, b) => (a.id.startsWith("stinger") ? 1 : 0) - (b.id.startsWith("stinger") ? 1 : 0) || a.id.localeCompare(b.id));
for (const s of sorted) list.append(row(s));

$<HTMLButtonElement>("stop").onclick = () => engine?.setCue(null, FADES.musicOut);
$<HTMLSelectElement>("quality").onchange = () => engine?.setQuality(quality());
setInterval(() => {
  if (!engine) return;
  const st = engine.stats();
  $("stats").textContent = `  ${st.cue ?? "silence"} | ${st.section ?? "-"} | ${st.voices} notes${st.resting ? " | resting" : ""}`;
}, 250);

// ---------------------------------------------------------------- offline renders (render.mjs)

interface RenderOpts {
  night?: boolean;
  quality?: Quality;
  seconds?: number;
  sampleRate?: number;
  /** Start this many seconds into the piece (the clip skips the intro). */
  from?: number;
  /** No reverb (measuring). */
  dry?: boolean;
}

async function render(id: string, o: RenderOpts = {}, adhoc?: Score): Promise<{ wav: string; seconds: number }> {
  const s = adhoc ?? SCORES.get(id);
  if (!s) throw new Error(`no score ${id}`);
  const sr = o.sampleRate ?? 48000;
  const c = compile(s, { night: o.night });
  const from = o.from ?? 0;
  const seconds = o.seconds ?? Math.min(c.end - from + 3, 200);
  const off = new OfflineAudioContext(2, Math.ceil(sr * (seconds + from)), sr);
  let t = 0;
  const e = createEngine(off, off.destination, o.quality ?? "medium", { now: () => t, later: () => undefined, random: () => 0.5, timer: false, dry: o.dry, lookup: (x) => (x === s.id ? s : SCORES.get(x)) });
  if (id.startsWith("stinger.")) e.stinger(id as StingerId);
  else e.setCue({ id: id as MusicId, night: !!o.night }, 0);
  for (t = 0; t < seconds + from; t += 0.1) e.tick();
  const buf = await off.startRendering();
  return { wav: toWav(buf, Math.floor(from * sr)), seconds };
}

/** A scripted listen: cues and stingers at given times (s), as the game would send them (crossfade demos). */
interface Step {
  at: number;
  cue?: { id: string; night: boolean } | null;
  stinger?: string;
}

async function renderSequence(steps: Step[], seconds: number, sampleRate = 48000): Promise<{ wav: string; seconds: number }> {
  const off = new OfflineAudioContext(2, Math.ceil(sampleRate * seconds), sampleRate);
  let t = 0;
  const e = createEngine(off, off.destination, "medium", { now: () => t, later: () => undefined, random: () => 0.5, timer: false });
  const todo = [...steps].sort((a, b) => a.at - b.at);
  for (t = 0; t < seconds; t += 0.05) {
    while (todo.length && todo[0]!.at <= t) {
      const s = todo.shift()!;
      if (s.stinger) e.stinger(s.stinger as StingerId);
      if (s.cue !== undefined) e.setCue(s.cue ? { id: s.cue.id as MusicId, night: s.cue.night } : null, FADES.musicOut);
    }
    e.tick();
  }
  const buf = await off.startRendering();
  return { wav: toWav(buf, 0), seconds };
}

function toWav(buf: AudioBuffer, skip: number): string {
  const n = buf.length - skip;
  const ch = [buf.getChannelData(0), buf.getChannelData(1)];
  const bytes = new ArrayBuffer(44 + n * 4);
  const v = new DataView(bytes);
  const str = (o: number, x: string) => [...x].forEach((c, i) => v.setUint8(o + i, c.charCodeAt(0)));
  str(0, "RIFF");
  v.setUint32(4, 36 + n * 4, true);
  str(8, "WAVEfmt ");
  v.setUint32(16, 16, true);
  v.setUint16(20, 1, true);
  v.setUint16(22, 2, true);
  v.setUint32(24, buf.sampleRate, true);
  v.setUint32(28, buf.sampleRate * 4, true);
  v.setUint16(32, 4, true);
  v.setUint16(34, 16, true);
  str(36, "data");
  v.setUint32(40, n * 4, true);
  let o = 44;
  for (let i = 0; i < n; i++) {
    for (const d of ch) {
      const x = Math.max(-1, Math.min(1, d[i + skip]!));
      v.setInt16(o, Math.round(x * 32767), true);
      o += 2;
    }
  }
  const u8 = new Uint8Array(bytes);
  let bin = "";
  for (let i = 0; i < u8.length; i += 0x8000) bin += String.fromCharCode(...u8.subarray(i, i + 0x8000));
  return btoa(bin);
}

(window as unknown as { __music: unknown }).__music = {
  ids: () => sorted.map((s) => s.id),
  info: (id: string) => {
    const s = SCORES.get(id)!;
    const d = compile(s);
    const n = compile(s, { night: true });
    return { title: s.title, style: s.style, bpm: pulseBpm(s), meter: s.meter, dayS: d.end, nightS: n.end, form: s.form, sections: d.marks.map((m) => `${m.key}:${m.bars}`) };
  },
  render,
  renderSequence,
  /** An ad-hoc score (instrument level checks). */
  renderScore: (s: Score, o: RenderOpts) => render(s.id, o, s),
};
