# Notes for AI agents

SpellHollow School: a browser MMO. Rust server and simulation in
`crates/`, Babylon.js TypeScript client in `client/`, game data in `data/`,
the Blender 5.2 asset pipeline in `tools/blender/`, art sources in `art/`.
Read `README.md`, `CONTRIBUTING.md` and `docs/architecture.md` before larger
changes.

## Never commit screenshots or build outputs

- **Screenshots go to `.out/screenshots/`** (gitignored), never into the
  repository: not `docs/screenshots/`, not a new
  `screenshots/` folder, not next to the code. Mention them in your report or
  attach them; describe in docs what they showed instead of linking them.
- Never commit screen recordings or videos (`.mp4`, `.webm`, `.mov` ...): keep
  them under `.out/` too.
- Never commit what a build makes or a script downloads: `client/public/assets/`,
  `client/src/wasm/`, any `.blend`/`.blend1`/`.blend2`, Blender renders
  (`art/renders/`, `.out/`), `.glb`/`.gltf`/`.ktx2`/`.wasm`, `loadtest-results/`,
  `.playwright-mcp/`, `vendor_assets/`, `.blender-user/`, `target/`,
  `node_modules/`. Change the scripts and data they are made from instead.
- Never `git add -f` an ignored file, and never commit with `--no-verify` to
  get past the check.
- Before committing run `node scripts/check-committed-files.mjs --all` (the
  pre-commit hook runs `--staged`; CI runs `--all`). If it refuses a file,
  move the file, do not widen `scripts/committed-files-allowlist.txt` unless
  the file is a genuine source nothing can regenerate (then add a reason and
  a row to `docs/repo-hygiene.md`).
- Full policy and the reasons: `docs/repo-hygiene.md`; what under `art/` is
  committed and the commands that rebuild the rest: `docs/art/repo-layout.md`.

## Worktrees

Put agent git worktrees under `.claude/worktrees/<name>` (gitignored and
refused by the committed-files check), never next to the repository or
elsewhere in it.

## Getting the generated files

- `bin/dev` (or `node client/scripts/prepare.mjs`) builds the wasm bindings
  and makes `client/public/assets/` match the checkout: it downloads the
  published asset release for the checkout's fingerprint, else builds with
  the pinned Blender, else uses greybox stand-ins (`tools/blender/README.md`,
  "Asset releases"). `bin/dev --fetch-assets` / `--build-assets` force one way.
- `tools/blender/build_art_blends.sh` remakes the `.blend` scenes under `art/`.
- `npm run assets:publish` (maintainers) uploads the asset release after asset
  inputs change on `main`.

## Deploying

Kamal (`config/deploy.yml`, `.kamal/`, guide in `docs/deploy.md`). Keep the
config generic: anything specific to one deployment (hosts, domains,
registries, secrets) is read from `.kamal/deploy.<destination>.env`
(gitignored) or the shell, never written into the committed files.

## Checks

`scripts/check.sh` runs what CI runs (the committed-files check and its
tests, cargo fmt, clippy, tests, the wasm build, the client typecheck, tests
and build).
