# Contributing

Thanks for helping. Start with the [README](README.md) to get the game
running, and [docs/architecture.md](docs/architecture.md) for how the code
fits together. Art contributors: [tools/blender/README.md](tools/blender/README.md)
and [docs/art/](docs/art/).

## Developer Certificate of Origin

Every commit must be signed off (`git commit -s`), certifying the
[Developer Certificate of Origin 1.1](https://developercertificate.org/): you wrote
the change or otherwise have the right to submit it under this project's license.

## Running it

`bin/dev` runs the game locally ([README, "Quick start"](README.md#quick-start)): it fetches or builds
`client/public/assets/`, then starts the server and the client. You need
Blender only to change what the assets are built from; everyone else gets the
published asset release for their checkout, or greybox stand-ins when there is
none.

## What never gets committed

Screenshots, renders and build outputs never go into git
([docs/repo-hygiene.md](docs/repo-hygiene.md) lists everything and the reasons):

- **Screenshots** (playtests, reviews, debugging) go to the gitignored
  `.out/screenshots/` and are attached to the pull request.
- **Build outputs**: `client/public/assets/` (fetched or built by `bin/dev`),
  `client/src/wasm/` (`scripts/build-wasm.sh`), every `.blend` and its
  `.blend1`/`.blend2` backups (`tools/blender/build_art_blends.sh`), Blender
  review renders (`art/renders/`, `.out/`), `.glb`/`.gltf`/`.ktx2`/`.wasm`
  files, any `dist/` folder, `loadtest-results/`, `.playwright-mcp/`.
- **Screen recordings and videos** (`.mp4`, `.webm`, `.mov` ...) stay under
  `.out/`, like screenshots.
- **Raster images** only in the folders listed in
  `scripts/committed-files-allowlist.txt` (none today), and **no file over
  2 MiB** unless that file lists it with a reason.

`npm install` (the `prepare` script) and `bin/dev` set
`core.hooksPath=scripts/git-hooks`, whose pre-commit hook runs
`node scripts/check-committed-files.mjs --staged`. In a real emergency
`git commit --no-verify` skips it, but CI runs the same check on the whole
tree (`--all`, also in `scripts/check.sh`), so the change cannot merge until
the file is gone, and a file once committed stays in the history.

## Rules that keep the project healthy

- Every IP name (houses, spells, characters, places, potions) lives in data or
  localization files, never in code identifiers or asset file names, so a rename is cheap.
- Never add files extracted from films, games or official artwork. Everything is
  made by hand (or by our scripts) from reference.
- Every list endpoint and list UI is paginated.
- Run `scripts/check.sh` before committing.
- Code comments point at the docs: `docs/architecture.md` and
  `docs/art/castle-kit.md` by heading name, the other `docs/art/` documents by
  section number. When you rename a heading or renumber a section, `git grep`
  the old name or number and update the citations in the same change.
- Commit sources, never build outputs. Everything Blender writes is generated
  and gitignored (`client/public/assets/`, and under `art/` the `.blend` scenes
  and `renders/`); change the scripts or data instead of a `.blend`. Only what a
  build cannot remake is committed under `art/` (see `docs/art/repo-layout.md`);
  a new file there needs a row in that table saying why.
- Build assets with the pinned Blender (`tools/blender/BLENDER_VERSION`, 5.2)
  and MPFB (`tools/blender/MPFB_VERSION`, installed by
  `tools/blender/install_mpfb.sh`), and basisu 2.50 if you can
  (`tools/blender/BASISU_VERSION`). The build refuses other versions; moving a
  pin is its own change, with every asset rebuilt and reviewed.
- A change to anything the asset build reads (the modules the build scripts
  run under `tools/blender/`, the data files they read, the sources under
  `art/`) changes the asset fingerprint. Build and check it locally
  (`bin/dev --build-assets`). The modules a step's script imports and the
  `data/...` paths they name are found automatically
  (`client/scripts/asset-steps.mjs` `stepSources`); a data file a script
  reaches through a path built at run time goes into its step's `sources` by
  hand (the tests in `client/scripts/assets.test.mjs` check the known cases
  and run in CI). After such a change lands on `main`, a maintainer runs
  `npm run assets:publish` so others can download the matching assets
  (`tools/blender/README.md`, "Asset releases").
- Files under `art/` are CC BY-SA 4.0 (`art/LICENSE`); third-party work used
  there is credited in `art/CREDITS.md`.
- When you add third-party work, credit it in `art/CREDITS.md` (or, for a
  sound, `client/public/audio/CREDITS.md`) and in `data/credits.json`, in the
  same change: `data/credits.json` feeds the in-game Credits panel and the
  `CREDITS.md` shipped with the built assets, and
  `client/scripts/credits.test.mjs` checks the two agree. The table in
  `client/public/audio/CREDITS.md` is parsed by the build: keep its three
  columns.
