#!/usr/bin/env python3
"""Convert the vetted CC0 downloads into the game's MP3s and their records.

    python3 -I tools/audio/convert.py [--only <out-prefix>] [--check]

Reads tools/audio/downloads.json (where each download came from) and
tools/audio/manifest.json (what each committed file is made of), takes the
sources from .out/audio-downloads/<name>/ (gitignored; fetched by hand, see
README.md), and writes:

  client/public/audio/sfx/<out>.mp3   the converted file
  data/audio/sources.json             one record per file (checked by sources.test.ts)
  client/public/audio/CREDITS.md      the same, for people

Per file: each layer is trimmed, filtered, resampled to 44.1 kHz, delayed and
mixed (ffmpeg); a loop is cut `loop + xfade` seconds long and its tail
crossfaded into its head (equal power) so it repeats without a seam; then the
level: one-shots to an active RMS of -20 dBFS with the peak held at -1 dBFS
or below, loops to `lufs` (default -23 LUFS integrated) with the same peak
ceiling; then MP3 (one-shots mono VBR ~70 kbps, loops 64 kbps mono or
96 kbps stereo). Nothing in the downloads is ever executed; only ffmpeg reads
them. Python's standard library only (no numpy).
"""

import argparse
import array
import json
import math
import os
import re
import subprocess
import sys
import tempfile

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DOWNLOADS = os.environ.get("SHS_AUDIO_DOWNLOADS", os.path.join(REPO, ".out", "audio-downloads"))
FF = os.environ.get("FFMPEG", "ffmpeg")
SR = 44100
CC0_URL = "https://creativecommons.org/publicdomain/zero/1.0/"


def run(cmd, binary=False):
    r = subprocess.run(cmd, capture_output=True, text=not binary)
    if r.returncode != 0:
        sys.exit(f"ffmpeg failed: {' '.join(cmd)}\n{r.stderr if not binary else r.stderr.decode(errors='replace')}")
    return r


def layer_chain(i, L, ch):
    """The filter chain for input i (one layer)."""
    parts = []
    rate = L.get("rate")
    if rate:
        # Pitch and speed together, like a slower tape: resample-trick.
        parts.append(f"aresample={SR},asetrate={SR * rate:.0f}")
    parts.append(f"aresample={SR}")
    if L.get("af"):
        parts.append(L["af"])
    parts.append(f"aformat=sample_fmts=fltp:sample_rates={SR}:channel_layouts={'mono' if ch == 1 else 'stereo'}")
    if L.get("gain"):
        parts.append(f"volume={L['gain']}dB")
    if L.get("at"):
        ms = int(round(L["at"] * 1000))
        parts.append(f"adelay={ms}:all=1")
    return f"[{i}:a]" + ",".join(parts) + f"[l{i}]"


def render_layers(entry, ch):
    """Mix the entry's layers to raw float32 (interleaved) at 44.1 kHz."""
    cmd = [FF, "-hide_banner", "-loglevel", "error", "-y"]
    for L in entry["layers"]:
        src = os.path.join(DOWNLOADS, L["dl"], L["f"])
        if not os.path.exists(src):
            sys.exit(f"missing download file: {src}")
        if L.get("ss") is not None:
            cmd += ["-ss", str(L["ss"])]
        if L.get("t") is not None:
            cmd += ["-t", str(L["t"])]
        cmd += ["-i", src]
    chains = [layer_chain(i, L, ch) for i, L in enumerate(entry["layers"])]
    n = len(entry["layers"])
    mix = "".join(f"[l{i}]" for i in range(n)) + (f"amix=inputs={n}:normalize=0:duration=longest" if n > 1 else "anull")
    post = entry.get("post")
    graph = ";".join(chains) + ";" + mix + (("," + post) if post else "") + "[out]"
    cmd += ["-filter_complex", graph, "-map", "[out]", "-f", "f32le", "-ac", str(ch), "-ar", str(SR), "-"]
    raw = run(cmd, binary=True).stdout
    a = array.array("f")
    a.frombytes(raw)
    return a


def trim_silence(a, ch, thresh_db=-55.0, keep_head=0.004, keep_tail=0.03):
    th = 10 ** (thresh_db / 20)
    frames = len(a) // ch
    first, last = None, None
    for f in range(frames):
        if max(abs(a[f * ch + c]) for c in range(ch)) > th:
            first = f
            break
    for f in range(frames - 1, -1, -1):
        if max(abs(a[f * ch + c]) for c in range(ch)) > th:
            last = f
            break
    if first is None:
        sys.exit("silent result")
    s = max(0, first - int(keep_head * SR))
    e = min(frames, last + int(keep_tail * SR))
    return a[s * ch:e * ch]


def fade(a, ch, fin=0.003, fout=0.02):
    frames = len(a) // ch
    nin, nout = min(frames, int(fin * SR)), min(frames, int(fout * SR))
    for f in range(nin):
        g = f / nin
        for c in range(ch):
            a[f * ch + c] *= g
    for k in range(nout):
        f = frames - 1 - k
        g = k / nout
        for c in range(ch):
            a[f * ch + c] *= g


def loop_xfade(a, ch, length_s, xfade_s):
    """A seamless loop `length_s` long from a cut `length_s + xfade_s` long: the tail crossfaded into the head (equal power)."""
    L, X = int(length_s * SR), int(xfade_s * SR)
    frames = len(a) // ch
    if frames < L + X:
        sys.exit(f"loop source too short: {frames / SR:.2f}s < {length_s + xfade_s:.2f}s")
    out = array.array("f", a[: L * ch])
    for i in range(X):
        t = i / X
        gi, go = math.sin(t * math.pi / 2), math.cos(t * math.pi / 2)
        for c in range(ch):
            out[i * ch + c] = a[i * ch + c] * gi + a[(L + i) * ch + c] * go
    return out


def peak(a):
    return max((abs(x) for x in a), default=0.0)


def active_rms(a, ch, gate_db=-40.0):
    """RMS over 10 ms blocks louder than gate_db below the peak (the sound itself, not its tail)."""
    blk = int(0.01 * SR) * ch
    pk = peak(a) or 1e-9
    gate = pk * 10 ** (gate_db / 20)
    s, n = 0.0, 0
    for i in range(0, len(a), blk):
        b = a[i:i + blk]
        if not b:
            continue
        m = sum(x * x for x in b) / len(b)
        if math.sqrt(m) > gate:
            s += m * len(b)
            n += len(b)
    return math.sqrt(s / n) if n else 0.0


def clipped(a, ch):
    """Runs of 3+ samples at full scale in a channel: a sign the source clipped."""
    runs = 0
    for c in range(ch):
        k = 0
        for i in range(c, len(a), ch):
            if abs(a[i]) >= 0.9995:
                k += 1
                if k == 3:
                    runs += 1
            else:
                k = 0
    return runs


def lufs(raw_path, ch):
    r = run([FF, "-hide_banner", "-nostats", "-f", "f32le", "-ar", str(SR), "-ac", str(ch), "-i", raw_path, "-af", "ebur128", "-f", "null", "-"])
    m = re.findall(r"I:\s+(-?[\d.]+) LUFS", r.stderr)
    return float(m[-1])


def db(x):
    return 20 * math.log10(max(x, 1e-9))


def process(entry):
    ch = entry.get("ch", 1)
    loop = entry.get("loop")
    a = render_layers(entry, ch)
    clip = clipped(a, ch)
    if loop:
        a = loop_xfade(a, ch, loop, entry.get("xfade", 2.0))
    else:
        a = trim_silence(a, ch)
        fade(a, ch, fout=entry.get("fadeOut", 0.02))
    with tempfile.NamedTemporaryFile(suffix=".f32", delete=False) as tmp:
        tmp.write(a.tobytes())
        raw = tmp.name
    try:
        pk = peak(a)
        if loop:
            target = entry.get("lufs", -23.0)
            g = target - lufs(raw, ch)
            level = f"{target:.0f} LUFS"
        else:
            target = entry.get("rms", -20.0)
            g = target - db(active_rms(a, ch))
            level = f"rms {target:.0f} dBFS"
        g = min(g, -1.0 - db(pk))  # the peak ceiling wins over the loudness target
        bitrate = ["-b:a", "96k"] if ch == 2 else (["-b:a", "64k"] if loop else ["-q:a", "5"])
        out = os.path.join(REPO, "client", "public", "audio", "sfx", entry["out"] + ".mp3")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        run([FF, "-hide_banner", "-loglevel", "error", "-y", "-f", "f32le", "-ar", str(SR), "-ac", str(ch), "-i", raw,
             "-af", f"volume={g:.2f}dB", "-codec:a", "libmp3lame", *bitrate, "-ar", str(SR), "-map_metadata", "-1", "-id3v2_version", "0", "-write_xing", "1", out])
    finally:
        os.unlink(raw)
    dur = len(a) / ch / SR
    return out, dur, g, level, clip, db(pk) + g


def describe_edits(entry, level):
    bits = []
    for L in entry["layers"]:
        b = [os.path.basename(L["f"])]
        if L.get("ss") is not None or L.get("t") is not None:
            b.append(f"cut {L.get('ss', 0)}s+{L.get('t', 'end')}s")
        if L.get("rate"):
            b.append(f"rate x{L['rate']}")
        if L.get("af"):
            b.append(L["af"])
        if L.get("gain"):
            b.append(f"{L['gain']} dB")
        if L.get("at"):
            b.append(f"at {L['at']}s")
        bits.append(" ".join(b))
    s = ("mixed: " if len(bits) > 1 else "") + " + ".join(bits)
    if entry.get("post"):
        s += f"; then {entry['post']}"
    if entry.get("loop"):
        s += f"; a {entry['loop']}s seamless loop (tail crossfaded into the head over {entry.get('xfade', 2.0)}s)"
    else:
        s += "; silence trimmed, short fades"
    s += f"; {'mono' if entry.get('ch', 1) == 1 else 'stereo'}, normalised to {level}, peak <= -1 dBFS; MP3 44.1 kHz"
    if entry.get("note"):
        s += f". {entry['note']}"
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="convert only outputs starting with this")
    args = ap.parse_args()
    dls = json.load(open(os.path.join(REPO, "tools", "audio", "downloads.json")))["downloads"]
    manifest = json.load(open(os.path.join(REPO, "tools", "audio", "manifest.json")))
    src_path = os.path.join(REPO, "data", "audio", "sources.json")
    sources = json.load(open(src_path))
    old = {r["file"]: r for r in sources.get("files", [])}
    records = []
    for entry in manifest["files"]:
        file = "sfx/" + entry["out"] + ".mp3"
        if args.only and not entry["out"].startswith(args.only):
            if file in old:
                records.append(old[file])
            continue
        out, dur, g, level, clip, final_pk = process(entry)
        size = os.path.getsize(out)
        print(f"{file:42s} {dur:6.2f}s {size / 1024:7.1f} KB gain {g:+6.1f} dB peak {final_pk:6.1f}{'  CLIPPED x' + str(clip) if clip else ''}")
        first = dls[entry["layers"][0]["dl"]]
        rec = {
            "file": file,
            "ids": entry["ids"],
            "source": first["page"],
            "author": first["author"],
            "licence": "CC0-1.0",
            "licenceUrl": CC0_URL,
            "original": f"{first['file']}: {entry['layers'][0]['f'].removeprefix('x/')}" if entry["layers"][0]["f"].startswith("x/") else first["file"],
            "downloadSha256": first["sha256"],
            "retrieved": first["retrieved"],
            "edits": describe_edits(entry, level),
        }
        others = []
        for L in entry["layers"][1:]:
            d = dls[L["dl"]]
            if L["dl"] == entry["layers"][0]["dl"]:
                continue
            o = {"source": d["page"], "author": d["author"], "licence": "CC0-1.0", "original": f"{d['file']}: {L['f'].removeprefix('x/')}" if L["f"].startswith("x/") else d["file"], "downloadSha256": d["sha256"], "retrieved": d["retrieved"]}
            if o not in others:
                others.append(o)
        if others:
            rec["mixedWith"] = others
        records.append(rec)
    sources["files"] = records
    with open(src_path, "w") as f:
        json.dump(sources, f, indent=2)
        f.write("\n")
    write_credits(records)
    total = sum(os.path.getsize(os.path.join(REPO, "client", "public", "audio", r["file"])) for r in records)
    print(f"{len(records)} files, {total / 1024:.0f} KB")


def write_credits(records):
    by_src = {}
    for r in records:
        for s in [r] + r.get("mixedWith", []):
            key = (s["source"], s["author"])
            by_src.setdefault(key, set()).add(r["file"])
    lines = [
        "# Sound credits",
        "",
        "Every recorded sound under `client/public/audio/` is CC0 (public domain",
        "dedication, https://creativecommons.org/publicdomain/zero/1.0/). CC0 asks",
        "for no credit; we give it anyway, with thanks. The per-file record (source,",
        "original file, download checksum, our edits) is `data/audio/sources.json`;",
        "the conversion is `tools/audio/convert.py`.",
        "",
        "All other sounds (interface, spells, classes, duels, brooms, owls' hoots,",
        "potions, bells, the room tones and the music) are synthesized in the game's",
        "own code (`client/src/audio/sfx/`, `client/src/audio/music/`).",
        "",
        "| Author | Source (CC0) | Our files |",
        "| --- | --- | --- |",
    ]
    for (src, author), files in sorted(by_src.items(), key=lambda kv: kv[0][1].lower()):
        lines.append(f"| {author} | {src} | {', '.join('`' + f + '`' for f in sorted(files))} |")
    lines.append("")
    with open(os.path.join(REPO, "client", "public", "audio", "CREDITS.md"), "w") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    main()
