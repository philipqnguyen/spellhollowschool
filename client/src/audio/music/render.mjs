#!/usr/bin/env node
// Preview renders of the music (dev only): drives the listening page
// (listen.html, `window.__music.render`) in headless Chrome, so the clips are
// the game's own engine and voices through an OfflineAudioContext, then
// encodes them with ffmpeg and measures their loudness.
//
//   node client/src/audio/music/render.mjs [ids...] [--url http://127.0.0.1:5173] [--out .out/previews]
//        [--day-s 60] [--night-s 45] [--full] [--quality medium]
//
// Needs a running Vite dev server for the client (the --url) and Playwright:
// `playwright` resolvable from client/, or SHS_PLAYWRIGHT set to its package
// directory. ffmpeg on the PATH (or SHS_FFMPEG).
//
// Prints per clip: length, integrated loudness (LUFS), true peak (dBTP) and the
// render speed (x real time; lower is heavier for the audio thread).

import { execFileSync } from "node:child_process";
import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, "../../../..");
const argv = process.argv.slice(2);
const opt = (f, d) => {
  const i = argv.indexOf(f);
  return i >= 0 ? argv[i + 1] : d;
};
const valued = new Set(["--url", "--out", "--day-s", "--night-s", "--quality"]);
const ids = argv.filter((a, i) => !a.startsWith("--") && !valued.has(argv[i - 1]));
const base = opt("--url", "http://127.0.0.1:5173");
const out = resolve(opt("--out", join(ROOT, ".out/previews")));
const daySeconds = Number(opt("--day-s", 60));
const nightSeconds = Number(opt("--night-s", 45));
const full = argv.includes("--full");
const quality = opt("--quality", "medium");
const ffmpeg = process.env.SHS_FFMPEG || "ffmpeg";

function playwright() {
  if (process.env.SHS_PLAYWRIGHT) return createRequire(import.meta.url)(process.env.SHS_PLAYWRIGHT);
  return createRequire(join(ROOT, "client", "package.json"))("playwright");
}

function ebur(file) {
  const res = execFileSync("bash", ["-c", `"${ffmpeg}" -hide_banner -nostats -i "${file}" -af ebur128=peak=true -f null - 2>&1 | tail -14`], { encoding: "utf8" });
  const i = /I:\s+(-?[\d.]+) LUFS/.exec(res)?.[1];
  const p = /Peak:\s+(-?[\d.]+|-inf) dBFS/.exec(res)?.[1];
  return { lufs: i ? Number(i) : NaN, peak: p ? Number(p) : NaN };
}

const { chromium } = playwright();
const browser = await chromium.launch({ channel: "chrome", headless: true });
const page = await browser.newPage();
page.on("console", (m) => {
  if (m.type() === "error") console.error("page:", m.text());
});
await page.goto(`${base}/src/audio/music/listen.html`);
await page.waitForFunction(() => !!window.__music, null, { timeout: 30000 });
// Vite may reload the page once while it settles (a fresh dependency scan): wait it out.
await page.waitForLoadState("networkidle");
await new Promise((r) => setTimeout(r, 1500));
await page.waitForFunction(() => !!window.__music, null, { timeout: 30000 });
const all = await page.evaluate(() => window.__music.ids());
const todo = ids.length ? all.filter((id) => ids.some((x) => id === x || id.endsWith(`.${x}`))) : all;
mkdirSync(out, { recursive: true });
const tmp = join(out, "wav");
mkdirSync(tmp, { recursive: true });

const rows = [];
for (const id of todo) {
  const info = await page.evaluate((x) => window.__music.info(x), id);
  const stinger = id.startsWith("stinger.");
  const takes = stinger ? [{ night: false, seconds: info.dayS + 2.5, tag: "" }] : [
    { night: false, seconds: full ? info.dayS + 3 : Math.min(daySeconds, info.dayS + 3), tag: "_day" },
    { night: true, seconds: full ? info.nightS + 3 : Math.min(nightSeconds, info.nightS + 3), tag: "_night" },
  ];
  for (const t of takes) {
    const name = `${id.replace(/^music\./, "").replace(/^stinger\./, "stinger_")}${t.tag}`;
    const t0 = Date.now();
    const r = await page.evaluate(([x, o]) => window.__music.render(x, o), [id, { night: t.night, seconds: t.seconds, quality }]);
    const speed = (Date.now() - t0) / 1000 / t.seconds;
    const wav = join(tmp, `${name}.wav`);
    writeFileSync(wav, Buffer.from(r.wav, "base64"));
    const m4a = join(out, `${name}.m4a`);
    const fadeAt = Math.max(0, t.seconds - 2);
    execFileSync(ffmpeg, ["-y", "-hide_banner", "-loglevel", "error", "-i", wav, "-af", `afade=t=out:st=${fadeAt}:d=2`, "-c:a", "aac", "-b:a", "160k", m4a]);
    const m = ebur(wav);
    rows.push({ name, seconds: t.seconds.toFixed(1), lufs: m.lufs, peak: m.peak, speed: speed.toFixed(3), piece: `${info.title} (${Math.round(info.bpm)} bpm, ${info.meter.join("/")}, day ${info.dayS.toFixed(0)} s)` });
    console.log(`${name.padEnd(26)} ${t.seconds.toFixed(1).padStart(6)} s  ${String(m.lufs).padStart(6)} LUFS  peak ${String(m.peak).padStart(6)} dBFS  render x${speed.toFixed(3)}  ${t.tag === "" || t.tag === "_day" ? rows.at(-1).piece : ""}`);
  }
}
// How the game moves between pieces: the crossfade between areas, the night
// variant arriving at a section boundary, a stinger over a loop.
const DEMOS = [
  { name: "demo_crossfade_castle_to_great_hall", seconds: 42, steps: [{ at: 0, cue: { id: "music.castle", night: false } }, { at: 22, cue: { id: "music.great_hall", night: false } }] },
  { name: "demo_night_falls_on_the_grounds", seconds: 50, steps: [{ at: 0, cue: { id: "music.grounds", night: false } }, { at: 8, cue: { id: "music.grounds", night: true } }] },
  { name: "demo_level_up_over_common_room", seconds: 22, steps: [{ at: 0, cue: { id: "music.common_room", night: false } }, { at: 12, stinger: "stinger.level_up" }] },
];
if (!ids.length && !full) {
  for (const d of DEMOS) {
    const r = await page.evaluate(([s, n]) => window.__music.renderSequence(s, n), [d.steps, d.seconds]);
    const wav = join(tmp, `${d.name}.wav`);
    writeFileSync(wav, Buffer.from(r.wav, "base64"));
    execFileSync(ffmpeg, ["-y", "-hide_banner", "-loglevel", "error", "-i", wav, "-af", `afade=t=out:st=${d.seconds - 2}:d=2`, "-c:a", "aac", "-b:a", "160k", join(out, `${d.name}.m4a`)]);
    const m = ebur(wav);
    console.log(`${d.name.padEnd(40)} ${d.seconds} s  ${m.lufs} LUFS  peak ${m.peak} dBFS`);
  }
}
await browser.close();
rmSync(tmp, { recursive: true, force: true });
writeFileSync(join(out, "levels.json"), JSON.stringify(rows, null, 2));
