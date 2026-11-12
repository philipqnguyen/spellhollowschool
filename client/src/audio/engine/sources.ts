// What each sound id plays (docs/architecture.md "Sound and music"): its
// buffers, from the definitions (sfx/defs.ts). CC0 files are
// fetched by bank and decoded once; synthesized recipes are rendered once per
// variant through an OfflineAudioContext; aliases point at another id; an id
// with no definition yet gets a short, quiet placeholder tone (one-shots
// only: a looping id without one stays silent).

import { SOUNDS, soundEntry, type BankId, type SoundId } from "../catalogue";
import type { SoundDef, SoundDefs } from "../types";
import { hashOf } from "./logic";

export interface Playable {
  buffers: readonly AudioBuffer[];
  /** The definition's own gain (aliases multiply theirs in). */
  gain: number;
  rate: number;
  seamless: boolean;
  placeholder: boolean;
}

export interface SourceDeps {
  fetchBytes: (url: string) => Promise<ArrayBuffer>;
  makeOffline: (channels: number, length: number, sampleRate: number) => OfflineAudioContext;
  /** The URL of a file under `public/` by its logical path (`audio/sfx/...`; src/assets.ts `assetUrl`). */
  resolveUrl: (path: string) => string;
  warn: (msg: string) => void;
}

/** Files fetched at once while a bank loads. */
const FETCH_PARALLEL = 4;

export class Sources {
  private readonly ready = new Map<SoundId, Playable>();
  private readonly pending = new Map<SoundId, Promise<Playable | null>>();
  private readonly banks = new Map<BankId, Promise<void>>();
  private readonly loadedBanks = new Set<BankId>();
  private renderChain: Promise<unknown> = Promise.resolve();
  private disposed = false;

  constructor(
    private readonly ctx: BaseAudioContext,
    private readonly defs: SoundDefs,
    private readonly deps: SourceDeps,
  ) {}

  def(id: SoundId): SoundDef | undefined {
    return (this.defs as Record<string, SoundDef | undefined>)[id];
  }

  /** The buffers of `id` if they are ready now (null: loading, not loaded yet, a bed, or silent). */
  get(id: SoundId): Playable | null {
    return this.ready.get(id) ?? null;
  }

  /** The buffers of `id`, loading or rendering them if needed (files: only once their bank loads). */
  load(id: SoundId, depth = 0): Promise<Playable | null> {
    const have = this.ready.get(id);
    if (have) return Promise.resolve(have);
    const p = this.pending.get(id);
    if (p) return p;
    const def = this.def(id);
    let job: Promise<Playable | null>;
    if (!def) job = Promise.resolve(soundEntry(id).loop ? null : this.placeholder(id));
    else if (def.kind === "bed") job = Promise.resolve(null);
    else if (def.kind === "alias") {
      if (depth > 4) return Promise.resolve(null);
      const d = def;
      job = this.load(d.of, depth + 1).then((of) => (of ? { ...of, gain: of.gain * (d.gain ?? 1), rate: of.rate * (d.rate ?? 1) } : null));
    } else if (def.kind === "synth") job = this.render(id, def);
    else job = this.loadBank(soundEntry(id).bank).then(() => this.ready.get(id) ?? null);
    this.pending.set(id, job);
    void job.then(
      (r) => {
        this.pending.delete(id);
        if (r) this.ready.set(id, r);
      },
      () => this.pending.delete(id),
    );
    return job;
  }

  /** The file bank `id` loads with. */
  bankOf(id: SoundId): BankId {
    return soundEntry(id).bank;
  }

  isBankLoaded(bank: BankId): boolean {
    return this.loadedBanks.has(bank);
  }

  get banksLoaded(): BankId[] {
    return [...this.loadedBanks];
  }

  /** Fetches and decodes every file of `bank` (once). */
  loadBank(bank: BankId): Promise<void> {
    let p = this.banks.get(bank);
    if (p) return p;
    p = this.fetchBank(bank).then(() => {
      if (!this.disposed) this.loadedBanks.add(bank);
    });
    this.banks.set(bank, p);
    return p;
  }

  private async fetchBank(bank: BankId): Promise<void> {
    const jobs: { id: SoundId; def: Extract<SoundDef, { kind: "file" }> }[] = [];
    for (const s of SOUNDS) {
      if (s.bank !== bank) continue;
      const def = this.def(s.id);
      if (def?.kind === "file" && def.files.length) jobs.push({ id: s.id, def });
    }
    let next = 0;
    const worker = async () => {
      while (next < jobs.length && !this.disposed) {
        const job = jobs[next++]!;
        const buffers: AudioBuffer[] = [];
        for (const f of job.def.files) {
          const b = await this.decode(f);
          if (b) buffers.push(b);
        }
        if (buffers.length) this.ready.set(job.id, { buffers, gain: job.def.gain ?? 1, rate: 1, seamless: job.def.seamless === true, placeholder: false });
      }
    };
    await Promise.all(Array.from({ length: Math.min(FETCH_PARALLEL, jobs.length) }, worker));
  }

  private async decode(file: string): Promise<AudioBuffer | null> {
    const url = this.deps.resolveUrl(`audio/${file}`);
    try {
      const bytes = await this.deps.fetchBytes(url);
      return await this.ctx.decodeAudioData(bytes);
    } catch (e) {
      this.deps.warn(`audio: ${url} did not load (${e instanceof Error ? e.message : String(e)})`);
      return null;
    }
  }

  /** Renders a synthesized definition's variants (one after another across ids: no CPU spike). */
  private render(id: SoundId, def: Extract<SoundDef, { kind: "synth" }>): Promise<Playable | null> {
    const job = this.renderChain.then(async () => {
      if (this.disposed) return null;
      const sr = this.ctx.sampleRate;
      const length = Math.max(1, Math.ceil(def.seconds * sr));
      const buffers: AudioBuffer[] = [];
      for (let v = 0; v < Math.max(1, def.variants ?? 1); v++) {
        try {
          const off = this.deps.makeOffline(def.channels ?? 1, length, sr);
          def.draw(off, off.destination, v);
          buffers.push(await off.startRendering());
        } catch (e) {
          this.deps.warn(`audio: ${id} variant ${v} did not render (${e instanceof Error ? e.message : String(e)})`);
        }
      }
      return buffers.length ? { buffers, gain: def.gain ?? 1, rate: 1, seamless: def.seamless === true, placeholder: false } : null;
    });
    this.renderChain = job.catch(() => undefined);
    return job;
  }

  /** A short soft tone, its pitch from the id (so two placeholders can be told apart). */
  private placeholder(id: SoundId): Playable {
    const sr = this.ctx.sampleRate;
    const seconds = 0.09;
    const len = Math.round(sr * seconds);
    const b = this.ctx.createBuffer(1, len, sr);
    const d = b.getChannelData(0);
    const f = 360 * Math.pow(2, (hashOf(id) % 24) / 12);
    for (let i = 0; i < len; i++) {
      const t = i / sr;
      const env = Math.min(1, t / 0.005) * Math.exp(-t / 0.03);
      d[i] = Math.sin(2 * Math.PI * f * t) * env * 0.5;
    }
    return { buffers: [b], gain: 0.25, rate: 1, seamless: false, placeholder: true };
  }

  /** Renders the given ids' recipes in the background (the core bank's, after the unlock). */
  prerender(ids: readonly SoundId[]): void {
    for (const id of ids) if (this.def(id)?.kind === "synth") void this.load(id);
  }

  dispose(): void {
    this.disposed = true;
    this.ready.clear();
    this.pending.clear();
  }
}
