// The Blender export steps of client/public/assets and what each is built
// from: the single list client/scripts/prepare.mjs builds from (incrementally,
// by modification time) and client/scripts/assets.mjs fingerprints (by
// content, to know whether a published asset release matches this checkout).
//
// A step's sources (stepSources) are its listed `sources`, every Python
// module its script runs, directly or through other modules (pythonClosure),
// the data files those modules name (dataRefs), the pins it is built with,
// and this file. So editing any module a step runs rebuilds that step, and
// the fingerprint (the union of every step's sources) covers what the build
// reads and nothing else: tools no step runs (install_mpfb.sh, the review
// renders) do not change it.
//
// This file is itself a source of every step: a change here (a new source,
// other arguments, another Blender command line) rebuilds everything and
// changes the fingerprint. prepare.mjs is not: it only decides when to run
// these and logs, so editing it does not orphan the published releases.
import { existsSync, readFileSync, statSync } from "node:fs";
import { dirname, join, posix, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE_REPO = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
export const ASSET_STEPS_FILE = "client/scripts/asset-steps.mjs";

// The pinned tools (tools/blender/README.md "Pinned Blender and MPFB"): every
// step needs the Blender major.minor in BLENDER_VERSION, the character step
// also the MPFB in MPFB_VERSION. A pin change rebuilds what it affects.
export const BLENDER_PIN = "tools/blender/BLENDER_VERSION";
export const MPFB_PIN = "tools/blender/MPFB_VERSION";
export const CHARACTER_SCRIPT = "tools/blender/characters/build_characters.py";
// basisu, the KTX2 encoder, is optional (without it these steps write PNG
// only, which the client also loads), so another version only warns.
export const BASISU_PIN = "tools/blender/BASISU_VERSION";
export const BASISU_SCRIPTS = new Set(["tools/blender/castle_kit/textures.py", "tools/blender/npcs/build_portraits.py"]);

/**
 * Blender's arguments for one step (after the binary): headless, factory
 * settings, and --python-exit-code 1, without which Blender exits 0 even when
 * the script raises.
 */
export function blenderArgs(step) {
  return ["-b", "--factory-startup", "--python-exit-code", "1", "--python", step.script, "--", ...step.args];
}

// ---------------------------------------------------------------------------
// What a step's script runs

const BLENDER_DIR = "tools/blender";
/**
 * The folders the build scripts put on sys.path, besides each module's own
 * folder and the step script's. An absolute import is looked up in all of
 * them and every match counts (an extra source only costs a rebuild).
 */
const IMPORT_ROOTS = [BLENDER_DIR, `${BLENDER_DIR}/world`, `${BLENDER_DIR}/characters`];

function isFile(repo, rel) {
  try {
    return statSync(join(repo, rel)).isFile();
  } catch {
    return false;
  }
}

/** A Python file's source without its comment lines. */
function pythonCode(repo, rel) {
  return readFileSync(join(repo, rel), "utf8")
    .split("\n")
    .filter((l) => !l.trim().startsWith("#"))
    .join("\n");
}

/**
 * The imports in Python source, as { name, level, from } (level: the leading
 * dots of a relative import; from: the names after `from x import`). Covers
 * `import a.b as c, d`, `from a import (b, c)` over several lines, relative
 * imports, imports inside functions, and the dynamic forms the build uses:
 * import_module / __import__ / outdoor.optional_module with a literal name
 * and, in a file that calls import_module with a variable (class_rooms.py's
 * room modules), every string literal that names a module.
 */
export function pythonImports(code) {
  const out = [];
  for (const m of code.matchAll(/^[ \t]*import[ \t]+([\w. \t,]+)/gm)) {
    for (const part of m[1].split(",")) {
      const name = part.trim().split(/\s+/)[0];
      if (name) out.push({ name, level: 0, from: [] });
    }
  }
  for (const m of code.matchAll(/^[ \t]*from[ \t]+(\.*)([\w.]*)[ \t]+import[ \t]+(\([^)]*\)|[^\n]*)/gm)) {
    const from = m[3]
      .replace(/[()\\]/g, " ")
      .split(",")
      .map((p) => p.trim().split(/\s+/)[0])
      .filter((n) => n && n !== "*");
    out.push({ name: m[2], level: m[1].length, from });
  }
  for (const m of code.matchAll(/(?:import_module|optional_module|__import__)\(\s*["']([\w.]+)["']/g)) {
    out.push({ name: m[1], level: 0, from: [] });
  }
  if (/import_module\(\s*[A-Za-z_]/.test(code)) {
    for (const m of code.matchAll(/["']([A-Za-z_]\w*)["']/g)) out.push({ name: m[1], level: 0, from: [] });
  }
  return out;
}

function isDir(repo, rel) {
  try {
    return statSync(join(repo, rel)).isDirectory();
  } catch {
    return false;
  }
}

/**
 * The files (repo-relative) importing dotted module `name` from `base` runs:
 * its packages' __init__.py (a folder without one is a namespace package,
 * as tools/blender/npcs is) and the module.
 */
function moduleFiles(repo, base, name) {
  const parts = name.split(".").filter(Boolean);
  if (parts.length === 0) return [];
  const files = [];
  for (let i = 1; i < parts.length; i++) {
    const dir = posix.join(base, ...parts.slice(0, i));
    if (!isDir(repo, dir)) return [];
    if (isFile(repo, `${dir}/__init__.py`)) files.push(`${dir}/__init__.py`);
  }
  const mod = `${posix.join(base, ...parts)}.py`;
  const pkg = posix.join(base, ...parts, "__init__.py");
  if (isFile(repo, mod)) return [...files, mod];
  if (isFile(repo, pkg)) return [...files, pkg];
  return [];
}

const closureCache = new Map();

/**
 * Every Python file under tools/blender (tests aside) that running `script`
 * (repo-relative) can load: the script and, transitively, what it imports
 * (pythonImports), looked up in the importing file's folder, the script's
 * folder and IMPORT_ROOTS. Standard library and Blender modules (bpy,
 * mathutils) are not files here and drop out. Sorted, repo-relative.
 */
export function pythonClosure(script, repo = HERE_REPO) {
  const key = `${repo}\0${script}`;
  if (closureCache.has(key)) return closureCache.get(key);
  const scriptDir = posix.dirname(script);
  const seen = new Set();
  const queue = [script];
  while (queue.length > 0) {
    const file = queue.pop();
    if (seen.has(file) || !file.startsWith(`${BLENDER_DIR}/`) || file.split("/").includes("tests") || !isFile(repo, file)) continue;
    seen.add(file);
    const dir = posix.dirname(file);
    for (const imp of pythonImports(pythonCode(repo, file))) {
      let bases = [...new Set([dir, scriptDir, ...IMPORT_ROOTS])];
      if (imp.level > 0) {
        let b = dir;
        for (let i = 1; i < imp.level; i++) b = posix.dirname(b);
        bases = [b];
      }
      for (const base of bases) {
        const found = moduleFiles(repo, base, imp.name);
        queue.push(...found);
        // `from pkg import mod` (and `from . import mod`) can load a submodule.
        const pkgDir = imp.name ? posix.join(base, ...imp.name.split(".")) : base;
        if (imp.from.length > 0 && isDir(repo, pkgDir)) {
          for (const n of imp.from) queue.push(...moduleFiles(repo, pkgDir, n));
        }
      }
    }
  }
  const files = [...seen].sort();
  closureCache.set(key, files);
  return files;
}

/**
 * The data files (repo-relative, existing) Python source names: "data/x/y.json"
 * literals and os.path.join(..., "data", "x", "y.json") chains.
 */
export function dataRefs(code, repo = HERE_REPO) {
  const refs = new Set();
  for (const m of code.matchAll(/["'](data\/[\w./-]+\.\w+)["']/g)) refs.add(m[1]);
  for (const m of code.matchAll(/["']data["']((?:\s*,\s*["'][\w.-]+["'])+)/g)) {
    const parts = [...m[1].matchAll(/["']([\w.-]+)["']/g)].map((x) => x[1]);
    refs.add(["data", ...parts].join("/"));
  }
  return [...refs].filter((r) => existsSync(join(repo, r))).sort();
}

/**
 * The step's sources (repo-relative files or folders): its listed sources,
 * every module its script runs (pythonClosure) and the data files they name
 * (dataRefs), the pins it is built with, and this file.
 */
export function stepSources(step, repo = HERE_REPO) {
  const modules = pythonClosure(step.script, repo);
  const data = modules.flatMap((m) => dataRefs(pythonCode(repo, m), repo));
  return [
    ...new Set([
      ...step.sources,
      ...modules,
      ...data,
      BLENDER_PIN,
      ...(step.script === CHARACTER_SCRIPT ? [MPFB_PIN] : []),
      ...(BASISU_SCRIPTS.has(step.script) ? [BASISU_PIN] : []),
      ASSET_STEPS_FILE,
    ]),
  ];
}

/** Each export step: its manifest, what it is built from, and how. Order matters (textures, greybox, then dorms). */
export const STEPS = [
  {
    // The castle kit (docs/art/castle-kit.md): the shared castle_* texture
    // library the kitted cells and the props atlas sample by material name.
    // Its own step, before the greybox, so editing kit pieces or rules does
    // not repaint it. The props atlas's slots come from props.json's kit and
    // the models in tools/blender/world.
    what: "the castle textures (public/assets/world/textures)",
    output: "world/textures/textures.json",
    sources: [
      "data/art/castle_textures.json",
      "data/world/props.json",
      "tools/blender/castle_kit/textures.py",
      "tools/blender/castle_kit/trim.py",
      "tools/blender/castle_kit/props_atlas.py",
      // The portrait painter, and grid.py (the portrait sheet layout, the KTX2 modes).
      "tools/blender/castle_kit/portraits.py",
      "tools/blender/castle_kit/grid.py",
      // The tapestry painter (docs/art/castle-kit.md "Textures").
      "tools/blender/castle_kit/tapestries.py",
      "tools/blender/world",
    ],
    script: "tools/blender/castle_kit/textures.py",
    args: ["--out", "client/public/assets/world/textures"],
  },
  {
    what: "the greybox castle (public/assets/world)",
    output: "world/manifest.json",
    // tools/blender/world holds the castle kit (stations, Potions classroom, find-spot tells) baked into the cells;
    // castle_kit.json and tools/blender/castle_kit dress the kitted cells.
    sources: [
      "data/world/layout.json",
      "data/world/props.json",
      "data/world/castle_kit.json",
      // The moving staircases (castle_kit/rules.py) and the grounds keep-out (world/grounds_dressing.py).
      "data/world/stairs.json",
      "data/world/keep_out.json",
      // The grounds' heights (the terrain export) and the far silhouette (the HLOD export).
      "data/world/terrain",
      "data/world/exterior.json",
      "data/art/castle_textures.json",
      "tools/blender/greybox.py",
      "tools/blender/layout_lib.py",
      "tools/blender/world",
      "tools/blender/castle_kit",
      // NPCs: the decorative portrait clusters keep clear of the talking frames.
      "data/npcs/portraits.json",
      "data/npcs/roster.json",
      "tools/blender/npcs",
    ],
    script: "tools/blender/greybox.py",
    args: ["--layout", "data/world/layout.json", "--out", "client/public/assets/world"],
  },
  {
    // NPCs (docs/art/castle-kit.md "Portraits and tapestries"): the talking portraits' shadow-box
    // frames and painted backdrops, after the castle textures (the trim sheet).
    what: "the talking portraits' frames and backdrops (public/assets/npcs)",
    output: "npcs/manifest.json",
    sources: ["data/npcs/portraits.json", "data/npcs/roster.json", "data/art/castle_textures.json", "tools/blender/npcs", "tools/blender/castle_kit"],
    script: "tools/blender/npcs/build_portraits.py",
    args: ["--out", "client/public/assets/npcs"],
  },
  {
    what: "the furniture (public/assets/furniture)",
    output: "furniture/manifest.json",
    sources: ["data/items/catalog.json", "data/items/palette.json", "tools/blender/world"],
    script: "tools/blender/world/build_furniture.py",
    // No .blend: the saved scenes are for inspection (tools/blender/build_art_blends.sh).
    args: ["--blend", ""],
  },
  {
    what: "the dorm rooms (public/assets/instances)",
    output: "world/instances.json",
    sources: ["data/world/instances", "tools/blender/world"],
    script: "tools/blender/world/build_dorms.py",
    args: ["--blend-dir", ""],
  },
  {
    // The Charms classroom and the duel arena (after the dorms:
    // both merge their rooms into world/instances.json).
    what: "the class and arena rooms (public/assets/instances/school.json)",
    output: "instances/school.json",
    // class_potions and class_classroom are built from castle-kit
    // pieces with the castle textures embedded (tools/blender/world/instance_kit.py),
    // so kit, texture and props edits rebuild them too. Runs after the textures step.
    sources: [
      "data/world/instances/class_charms.json",
      "data/world/instances/duel_arena.json",
      "data/world/instances/class_potions.json",
      "data/world/instances/class_classroom.json",
      // The rooms' modules (tools/blender/world/school_rooms_<room>.py).
      "data/world/instances/class_defence.json",
      "data/world/instances/class_divination.json",
      "data/world/instances/class_astronomy.json",
      // The Greenhouse and Paddock modules (and their props).
      "data/world/instances/class_greenhouse.json",
      "data/world/instances/class_paddock.json",
      "data/classes/gestures.json",
      "data/art/castle_textures.json",
      // The Astronomy platform's moonlight (world/school_rooms_astronomy.py).
      "data/art/lighting.json",
      "data/world/props.json",
      "data/world/stairs.json",
      "tools/blender/world",
      "tools/blender/castle_kit",
    ],
    script: "tools/blender/world/build_school.py",
    args: ["--blend-dir", ""],
  },
  {
    // The two Wishing Rooms (the Lost Property Vault, the
    // Gathering Room), built like the kitted class rooms but in a step of their
    // own (class_rooms.KIT_ROOMS stays the class set). After the textures (the
    // props atlas) and the school step (it merges into the same manifests).
    what: "the Wishing Rooms (public/assets/instances/lost_property_vault.glb, wishing_room_gathering.glb)",
    output: "instances/wishing_room_gathering.glb",
    sources: [
      "data/world/instances/lost_property_vault.json",
      "data/world/instances/wishing_room_gathering.json",
      "data/art/castle_textures.json",
      "data/world/props.json",
      "data/world/stairs.json",
      "tools/blender/world",
      "tools/blender/castle_kit",
    ],
    script: "tools/blender/world/build_wishing_room.py",
    args: ["--blend-dir", ""],
  },
  {
    // The landing gates' rail (the staircase's balustrade),
    // drawn by the client from data/world/stairs.json `gates` (src/game/castle/stairs.ts).
    what: "the landing rail prop (public/assets/instances/props/stair_rail.glb)",
    output: "instances/props/stair_rail.glb",
    sources: ["data/art/castle_textures.json", "tools/blender/world/stair_rail.py", "tools/blender/castle_kit"],
    script: "tools/blender/world/stair_rail.py",
    args: ["--out", "client/public/assets/instances/props"],
  },
  {
    // The Owl Loft's owl, drawn by the client at the owl_loft
    // cell's owl marker nodes (src/game/castle/owls.ts).
    what: "the owl prop (public/assets/instances/props/owl.glb)",
    output: "instances/props/owl.glb",
    sources: [
      "data/art/castle_textures.json",
      "tools/blender/world/owl_prop.py",
      "tools/blender/world/beast_props.py",
      "tools/blender/castle_kit/grid.py",
    ],
    script: "tools/blender/world/owl_prop.py",
    args: ["--out", "client/public/assets/instances/props"],
  },
  {
    // Spells L20 (SOCIAL): Bird Conjuring's songbirds, drawn by the client as thin
    // instances round the caster (src/game/spells/volucra.ts).
    what: "Bird Conjuring's birds (public/assets/volucra)",
    output: "volucra/manifest.json",
    sources: ["data/art/castle_textures.json", "tools/blender/volucra", "tools/blender/world/beast_props.py", "tools/blender/world/meshkit.py"],
    script: "tools/blender/volucra/build_birds.py",
    args: ["--out", "client/public/assets/volucra"],
  },
  {
    what: "the characters, clothing and hair (public/assets/characters, gear, hair; several minutes)",
    output: "characters/manifest.json",
    // gestures.json: the wand-gesture clips trace its templates (charlib/anim_spells.py).
    sources: [
      "data/items/catalog.json",
      "data/items/garment_shapes.json",
      "data/items/garment_designs.json",
      // The broom's colours (charlib/broom.py).
      "data/items/palette.json",
      "data/characters/creator.json",
      "data/classes/gestures.json",
      "tools/blender/characters",
      // The committed sources a build cannot make (docs/art/repo-layout.md): the
      // hair source meshes and the bodies' shape targets.
      "art/characters/hair_meshy",
      "art/characters/shape_targets",
    ],
    script: "tools/blender/characters/build_characters.py",
    args: ["--assets", "client/public/assets", "--no-blend"],
  },
  {
    // After the characters: clothing icons are rendered from the fitted body_a files.
    what: "the item icons (public/assets/icons)",
    output: "icons/manifest.json",
    sources: [
      "data/items/catalog.json",
      "data/items/palette.json",
      "tools/blender/icons",
      "tools/blender/world",
      "client/public/assets/characters/manifest.json",
    ],
    script: "tools/blender/icons/render_icons.py",
    args: ["--assets", "client/public/assets"],
  },
  {
    // After the characters: the spell effects and the wand,
    // whose attach transform is read from the exported bodies.
    what: "the spell effects and the wand (public/assets/vfx)",
    output: "vfx/manifest.json",
    sources: [
      "data/spells.json",
      "tools/blender/vfx",
      "tools/blender/characters/charlib/anim_spells.py",
      "client/public/assets/characters/manifest.json",
    ],
    script: "tools/blender/vfx/build_vfx.py",
    args: ["--assets", "client/public/assets"],
  },
  {
    // Spells L20 (GUARDIAN): Guardian Charm's six silver animals, merged
    // per animal and shaded by the client (src/game/guardian/).
    what: "the guardian animals (public/assets/guardian)",
    output: "guardian/manifest.json",
    sources: ["tools/blender/guardian"],
    script: "tools/blender/guardian/build_guardian.py",
    args: ["--out", "client/public/assets/guardian"],
  },
];

/**
 * What the asset fingerprint hashes (repo-relative files or folders, sorted):
 * the union of every step's sources (stepSources: the listed sources, the
 * modules the scripts run, the data files they name, the pins, this file).
 * Outputs read back by later steps (under client/public/assets) are not
 * inputs. assets.mjs `isFingerprintInput` leaves out tests, docs, caches and
 * the generated files under art/.
 */
export function fingerprintRoots(repo = HERE_REPO) {
  return [...new Set(STEPS.flatMap((step) => stepSources(step, repo)).filter((s) => !s.startsWith("client/public/assets")))].sort();
}
