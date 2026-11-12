# Repository hygiene: what is committed, what never is

**The rule:** commit sources, never outputs. A file is committed only if
nothing in this repository can make it again: no build step, no script, no
download. Screenshots, renders and build outputs never go into git, not even
"just this once": every version of a binary stays in the history for good
and every clone pays for it.

## What never gets committed

| What | Where it goes instead | How you get it back |
| --- | --- | --- |
| Playtest, review and debug screenshots (any `screenshots/` folder, `*.screenshot.png`, any raster image outside an allowed folder) and screen recordings (`.mp4`, `.webm`, `.mov`, `.mkv`, `.avi`, `.m4v`) | the gitignored `.out/screenshots/` (one subfolder per task is handy); attach them to the pull request | they are throwaway; describe what they showed in the doc or commit message |
| Browser automation output (Playwright MCP snapshots, logs) | `.playwright-mcp/` (gitignored) | - |
| Load test run output | `loadtest-results/` (gitignored); quote the numbers worth keeping in the pull request | `tools/loadtest` |
| Blender review renders and contact sheets | `art/renders/` or `.out/` (both gitignored) | the render commands in `tools/blender/README.md` and `docs/art/repo-layout.md` |
| `.blend` scenes, and Blender's `.blend1`/`.blend2` backups, anywhere | written at their usual paths, gitignored | `tools/blender/build_art_blends.sh` (`docs/art/repo-layout.md`) |
| The exported game assets (`client/public/assets/`: glTF/GLB, KTX2 and PNG textures, icons, manifests) | gitignored | `bin/dev` fetches the asset release for the checkout or builds it with the pinned Blender (`tools/blender/README.md`, "Asset releases") |
| The wasm bindings (`client/src/wasm/`, any `.wasm`) | gitignored | `scripts/build-wasm.sh` (`client/scripts/prepare.mjs` runs it) |
| Built binaries anywhere (`.glb`, `.gltf`, `.ktx2`, `.wasm`) | wherever the build writes them (gitignored) | the build |
| Third-party source assets, the MPFB install | `vendor_assets/`, `.blender-user/` (gitignored, not distributed) | `tools/blender/install_mpfb.sh`; `vendor_assets` is never a build input (`docs/art/repo-layout.md`) |
| Agent git worktrees (Claude Code) | `.claude/worktrees/<name>` (gitignored) | `git worktree add .claude/worktrees/<name>`; merge the branch, never commit the folder |
| `target/`, `node_modules/`, `dist/`, `__pycache__/`, logs, `.env` files, OS and editor files | gitignored | `cargo build`, `npm ci` |
| Any file over 2 MiB | - | almost always generated; a real source that big needs an allowlist line (below) |

What *is* committed, binary-wise: the CC0 sound effects in
`client/public/audio/sfx/` (small MP3s the client plays; made from downloads by
`tools/audio/convert.py`, kept so nobody needs the downloads) and the
character sources under `art/characters/` that no build can make (the Meshy
hair meshes and the sculpt shape targets, `.npz`; `docs/art/repo-layout.md`).

## How it is enforced

- **`.gitignore`** keeps all of the above out of `git status`. Never
  `git add -f` past it.
- **`scripts/check-committed-files.mjs`** (plain Node, no dependencies) is the
  policy. It refuses: anything under `docs/screenshots/`, `.out/`,
  `.playwright-mcp/`, `loadtest-results/`, `art/renders/`,
  `client/public/assets/`, `client/src/wasm/`, `vendor_assets/`,
  `.blender-user/`, `.claude/worktrees/`, `target/`, `node_modules/`; any `dist/`, `node_modules/`
  or `__pycache__/` folder at any depth; any `screenshots/` folder or
  `*.screenshot.png`; any `.blend`, `.blend1`, `.blend2`; videos (`.mp4`,
  `.webm`, `.mov`, `.mkv`, `.avi`, `.m4v`); `.glb`, `.gltf`, `.ktx2`, `.wasm`;
  raster images (png, jpg, jpeg, webp, gif, avif, bmp, tif, tiff, tga, exr,
  hdr, psd, xcf, kra, heic) outside the folders the allowlist names; any file
  over 2 MiB. Sizes are those of the staged blobs. Each refusal says why and
  where the file belongs.
  - `--staged`: the files added or changed in the index (deletions always
    pass). The pre-commit hook runs this.
  - `--all`: every tracked file. CI (the `files` job) and `scripts/check.sh`
    run this, and `npm run check:files` does too.
- **`scripts/committed-files-allowlist.txt`** holds the exceptions, with
  comments: `images <dir>/` (a folder of hand-made raster images the client
  or the build reads) and `file <path> <reason>` (one exact path allowed over
  the size limit, or a video, or with a built-binary extension). Both lists
  are empty today: the tree holds no raster image and no file over 2 MiB. The blocked folders and `.blend` files
  cannot be allowlisted. The check reads the allowlist as it is in the index
  (an unstaged edit to it changes nothing; CI reads the committed one). Add a
  line in the same commit as the file, and a row to the table below.
- **The pre-commit hook** is `scripts/git-hooks/pre-commit`. It is installed
  by pointing `core.hooksPath` at `scripts/git-hooks`, which
  `scripts/install-git-hooks.mjs` does from the root `package.json` `prepare`
  script (every `npm install` / `npm ci`) and from `bin/dev`. Both are
  idempotent and quiet once set; if you set another `core.hooksPath` on
  purpose it warns and leaves yours. By hand:
  `git config core.hooksPath scripts/git-hooks`.
- **Emergency bypass:** `git commit --no-verify` skips the hook. CI still runs
  `--all` on the whole tree, so the commit fails there until the file is
  removed again (and removing it later does not take it out of the history).
- Tests: `node --test scripts/check-committed-files.test.mjs`
  (`npm run test:scripts`; CI and `scripts/check.sh` run them).

## Committed binaries

Every binary file in the tree, and why it is committed:

| Path | Why it is committed |
| --- | --- |
| `client/public/audio/sfx/**.mp3` | CC0 recordings the client plays (`client/src/audio/engine.ts`, checked by `catalogue.test.ts`); made by `tools/audio/convert.py` from downloads that are not committed, so keeping them spares everyone the downloads; credits in `client/public/audio/CREDITS.md` |
| `art/characters/hair_meshy/*.npz` (up to 1.13 MiB each) | source meshes from the Meshy hair (in `vendor_assets/`, not in the repository) read by `build_characters.py` and `build_hair.py`; not regenerable by a contributor |
| `art/characters/shape_targets/body_{a,b}.npz` | shape targets wrapped from CC BY sculpts not in the repository; read by the character build |
