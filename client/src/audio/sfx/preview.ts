// The sound effects' preview page (dev server only:
// /src/audio/sfx/preview.html). Lists every effect and ambience id by group; clicking
// one plays it as the engine would (a file decoded, a synth rendered once
// offline, a bed's layers and a few sprinkles for some seconds). "Measure
// all" renders every synthesized recipe as written (RAW_SOUND_DEFS) and
// prints levels.ts: each recipe's gain to land on its family's loudness
// target (TARGETS), with the peak held at -1 dBFS.

import { GROUPS, soundEntry, soundsIn, type EffectId } from "../catalogue";
import type { SoundDef } from "../types";
import { RAW_SOUND_DEFS, SOUND_DEFS } from "./defs";
import { assetUrl } from "../../assets";

type Synth = Extract<SoundDef, { kind: "synth" }>;

/** Loudness targets (dBFS: the active RMS of a one-shot, the RMS of a loop), the first matching prefix wins. */
const TARGETS: readonly [string, number][] = [
  ["ui.", -27],
  ["spell.trail", -27],
  ["spell.monstra_viam", -24],
  ["spell.reveal_glint", -26],
  ["spell.recoloured", -23],
  ["spell.", -21],
  ["volucra.chirp", -26],
  ["volucra.", -24],
  ["guardian.ambient", -31],
  ["guardian.summon", -22],
  ["guardian.", -24],
  ["class.beat_tick", -27],
  ["class.beat_open", -25],
  ["class.pick", -27],
  ["class.reject", -27],
  ["class.miss", -26],
  ["class.grade_", -24],
  ["class.ring_glow", -26],
  ["class.ring_through", -23],
  ["class.", -24],
  ["duel.countdown", -24],
  ["duel.ward_faded", -26],
  ["duel.", -20],
  ["broom.wind", -24],
  ["broom.land", -24],
  ["broom.", -22],
  ["craft.", -24],
  ["find.", -24],
  ["potion.", -23],
  ["mail.", -23],
  ["owl.", -22],
  ["trade.", -24],
  ["cup.", -24],
  ["secret.pace", -26],
  ["secret.", -22],
  ["level.", -21],
  ["world.", -20],
  ["entrance.", -21],
  ["stairs.swing", -24],
  ["stairs.", -21],
  ["loop.cauldron", -24],
  ["loop.owls", -26],
  ["amb.wishing_room", -42],
  ["amb.arena", -44],
  ["amb.under_lake", -39],
  ["amb.tower", -28],
  ["amb.sky", -26],
  ["sprinkle.gust", -26],
  ["sprinkle.frog", -25],
  ["sprinkle.", -24],
];

function target(id: string): number {
  return TARGETS.find(([p]) => id.startsWith(p))?.[1] ?? -24;
}

const SR = 44100;

async function renderSynth(d: Synth, variant: number): Promise<AudioBuffer> {
  const ctx = new OfflineAudioContext(d.channels ?? 1, Math.ceil(d.seconds * SR), SR);
  const out = ctx.createGain();
  out.gain.value = 1;
  out.connect(ctx.destination);
  d.draw(ctx, out, variant);
  return ctx.startRendering();
}

/** Active RMS (blocks within 40 dB of the peak) for a one-shot, plain RMS for a loop; and the peak. */
function measure(b: AudioBuffer, loop: boolean): { rms: number; peak: number } {
  let peak = 0;
  for (let c = 0; c < b.numberOfChannels; c++) for (const x of b.getChannelData(c)) peak = Math.max(peak, Math.abs(x));
  const blk = Math.floor(0.01 * b.sampleRate);
  const gate = loop ? 0 : peak * 0.01;
  let sum = 0,
    count = 0;
  for (let c = 0; c < b.numberOfChannels; c++) {
    const d = b.getChannelData(c);
    for (let i = 0; i < d.length; i += blk) {
      let s = 0;
      const end = Math.min(d.length, i + blk);
      for (let j = i; j < end; j++) s += d[j]! * d[j]!;
      if (Math.sqrt(s / (end - i)) >= gate) {
        sum += s;
        count += end - i;
      }
    }
  }
  return { rms: count ? Math.sqrt(sum / count) : 0, peak };
}

const db = (x: number) => 20 * Math.log10(Math.max(x, 1e-9));

export interface Measured {
  id: string;
  rmsDb: number;
  peakDb: number;
  level: number;
}

async function measureAll(): Promise<Measured[]> {
  const out: Measured[] = [];
  for (const [id, d] of Object.entries(RAW_SOUND_DEFS) as [EffectId, SoundDef][]) {
    if (d.kind !== "synth") continue;
    const loop = soundEntry(id).loop;
    let rms = 0,
      peak = 0;
    const n = d.variants ?? 1;
    for (let v = 0; v < n; v++) {
      const m = measure(await renderSynth(d, v), loop);
      rms += (m.rms * m.rms) / n;
      peak = Math.max(peak, m.peak);
    }
    rms = Math.sqrt(rms);
    let level = Math.pow(10, (target(id) - db(rms)) / 20);
    level = Math.min(level, 0.89 / Math.max(peak, 1e-6));
    out.push({ id, rmsDb: Math.round(db(rms) * 10) / 10, peakDb: Math.round(db(peak) * 10) / 10, level: Math.round(level * 1000) / 1000 });
  }
  return out;
}

function levelsTs(ms: Measured[]): string {
  return ms.map((m) => `  "${m.id}": ${m.level}, // rms ${m.rmsDb} dBFS, peak ${m.peakDb} dBFS`).join("\n");
}

// ---------------------------------------------------------------- listening

let ctx: AudioContext | null = null;
const playing = new Set<AudioScheduledSourceNode>();
const buffers = new Map<string, AudioBuffer>();

function audio(): AudioContext {
  ctx ??= new AudioContext();
  void ctx.resume();
  return ctx;
}

async function bufferFor(id: EffectId, d: SoundDef, variant: number): Promise<AudioBuffer | null> {
  const key = `${id}#${variant}`;
  const hit = buffers.get(key);
  if (hit) return hit;
  let b: AudioBuffer | null = null;
  if (d.kind === "synth") b = await renderSynth(d, variant % (d.variants ?? 1));
  else if (d.kind === "file") {
    const f = d.files[variant % d.files.length]!;
    const res = await fetch(assetUrl(`audio/${f}`));
    b = await audio().decodeAudioData(await res.arrayBuffer());
  }
  if (b) buffers.set(key, b);
  return b;
}

async function play(id: EffectId, variant: number, gain = 1, loopFor = 0): Promise<void> {
  const d = SOUND_DEFS[id];
  if (!d) return;
  if (d.kind === "alias") return play(d.of as EffectId, variant, gain * (d.gain ?? 1));
  if (d.kind === "bed") {
    for (const l of d.layers) void play(l.id as EffectId, 0, gain * l.gain, 8);
    d.sprinkles.forEach((s, i) => setTimeout(() => void play(s.id as EffectId, i, gain * s.gain), 1200 + i * 1700));
    return;
  }
  const b = await bufferFor(id, d, variant);
  if (!b) return;
  const a = audio();
  const src = a.createBufferSource();
  src.buffer = b;
  const g = a.createGain();
  g.gain.value = gain * (d.gain ?? 1);
  src.connect(g).connect(a.destination);
  const loops = soundEntry(id).loop;
  src.loop = loops;
  src.start();
  if (loops) src.stop(a.currentTime + (loopFor || 8));
  playing.add(src);
  src.onended = () => {
    playing.delete(src);
    src.disconnect();
    g.disconnect();
  };
}

function build(): void {
  const list = document.getElementById("list")!;
  const variants = new Map<string, number>();
  for (const g of GROUPS) {
    const ids = soundsIn(g).filter((s) => s.source !== "score");
    if (!ids.length) continue;
    const h = document.createElement("h2");
    h.textContent = g;
    list.append(h);
    const grid = document.createElement("div");
    grid.className = "grid";
    for (const s of ids) {
      const d = SOUND_DEFS[s.id as EffectId];
      const row = document.createElement("div");
      row.className = "row";
      const btn = document.createElement("button");
      btn.textContent = "Play";
      btn.onclick = () => {
        const v = variants.get(s.id) ?? 0;
        variants.set(s.id, v + 1);
        void play(s.id as EffectId, v);
      };
      const label = document.createElement("span");
      label.className = "id";
      label.textContent = s.id;
      const kind = document.createElement("span");
      kind.className = "kind";
      kind.textContent = d ? d.kind : "missing";
      row.append(btn, label, kind);
      grid.append(row);
    }
    list.append(grid);
  }
  document.getElementById("stop")!.onclick = () => {
    for (const s of playing) s.stop();
  };
  document.getElementById("measure")!.onclick = async () => {
    const status = document.getElementById("status")!;
    status.textContent = "Measuring...";
    const t0 = performance.now();
    const ms = await measureAll();
    (window as unknown as { __levels: Measured[] }).__levels = ms;
    const pre = document.getElementById("levels")!;
    pre.hidden = false;
    pre.textContent = levelsTs(ms);
    status.textContent = `${ms.length} recipes in ${Math.round(performance.now() - t0)} ms`;
  };
}

build();
