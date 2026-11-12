# SpellHollow School

A social 3D MMO that runs in the browser, set at SpellHollow, a castle school
of magic in a lakeside hollow. Players attend classes, dress up, decorate their
dorm, explore the castle and its grounds, team up in parties, found and join
clubs, fly, and duel when they choose to.
Each account can have up to five characters. Players must be 18 or older.

The official server is at <https://game.spellhollowschool.com>. Anyone can 
run their own server with this repository.

This is a non-commercial, open-source game.

**Browser support:** the client supports Google Chrome only.
Other browsers, including every browser on iPhone and iPad, draw parts of it
wrong and are shown a card asking for Chrome. The Vite dev server skips the
check, and `?browser=any` skips it on a deployed server for testing.

## Documentation

| Document | What it covers |
| --- | --- |
| [docs/deploy.md](docs/deploy.md) | Self-hosting and deploying: Kamal, or Docker Compose behind your own reverse proxy; backups; what to check before going public |
| [docs/configuration.md](docs/configuration.md) | Every server setting (environment variable), with defaults and examples |
| [docs/architecture.md](docs/architecture.md) | How the code fits together: crates, client, data, protocol, HTTP API, database, testing |
| [CONTRIBUTING.md](CONTRIBUTING.md) | How to contribute: sign-off, checks, what never gets committed, project rules |
| [SECURITY.md](SECURITY.md) | Reporting a vulnerability privately, and what is in scope |
| [tools/blender/README.md](tools/blender/README.md) | The asset pipeline: building, publishing and downloading the game's 3D assets |
| [docs/art/](docs/art/) | The art direction and the art pipeline: [style guide](docs/art/style-guide.md), [castle kit](docs/art/castle-kit.md), [characters](docs/art/base-body-spec.md), [clothing](docs/art/garment-shapes.md), [what is under `art/`](docs/art/repo-layout.md) |
| [tools/loadtest/README.md](tools/loadtest/README.md) | Load-testing a server with simulated players |
| [docs/repo-hygiene.md](docs/repo-hygiene.md) | What is committed and what never is |
| [art/CREDITS.md](art/CREDITS.md) | Credits for third-party art; sounds in [client/public/audio/CREDITS.md](client/public/audio/CREDITS.md) |

## What's in the repository

| Path | What it is |
| --- | --- |
| `crates/server` | The game server (`shs-server`): HTTP API, WebSocket realtime, Postgres, the economy ledger |
| `crates/sim-core` | Shared simulation (movement, collision, the world layout), used by the server and the client |
| `crates/sim-wasm` | `sim-core` compiled to WebAssembly for the client |
| `crates/protocol` | Messages shared by the client and the server |
| `client/` | The browser client: TypeScript, Babylon.js, Vite |
| `data/` | Game content as JSON: items, spells, classes, NPCs, the world layout, economy |
| `db/migrations/` | Postgres schema, applied by the server when it starts |
| `tools/blender/` | The Blender pipeline that exports every 3D asset the client draws |
| `tools/loadtest/` | Bots that load-test a server |
| `tools/audio/` | Converts the recorded sound effects |
| `art/` | Art sources the build can't regenerate (hair meshes, body shape targets) and their credits |
| `docker/` | The container image, and a compose file for a deployment without Kamal |
| `config/deploy.yml`, `.kamal/` | The Kamal deployment ([docs/deploy.md](docs/deploy.md)) |
| `bin/dev` | Runs everything locally |

## Running it locally

### Requirements

- **Rust**, installed with [rustup](https://rustup.rs). `rust-toolchain.toml`
  selects the pinned Rust release and adds the `wasm32-unknown-unknown` target,
  clippy and rustfmt. The first build installs the matching `wasm-bindgen`
  CLI by itself.
- **Node 25**, with npm.
- **Postgres 18** (14 or newer works), installed locally or run in Docker.
- **Blender 5.2** is optional. You need it only to change the 3D assets
  (see [Game assets](#game-assets)).

It runs on Linux and macOS. The scripts use bash, so on Windows use WSL.

### Quick start

```sh
git clone https://github.com/philipqnguyen/spellhollowschool.git
cd spellhollowschool
createdb shs
bin/dev
```

Open <http://localhost:5173> in Chrome, sign up and confirm your email. In
development there is no mail server: the confirmation link appears on screen
(and in the server log).

`bin/dev` does this:

1. installs the client's npm dependencies the first time, plus a git
   pre-commit hook;
2. builds the WebAssembly bindings into `client/src/wasm/`;
3. makes sure `client/public/assets/` matches this checkout (see
   [Game assets](#game-assets));
4. starts the game server (`SHS_DEV=1 cargo run -p server`, which applies the
   database migrations) on port 8080, and the Vite client on port 5173.

Ctrl-C stops both. `bin/dev --help` lists every option. The first run takes a
few minutes because it compiles the Rust workspace and downloads about 110 MB
of assets.

**`SHS_DEV=1` is for your own machine only.** Development mode shows
confirmation and password-reset links to whoever asks for them, so anyone
who can reach the server can take over any account, and it opens the
`/api/dev/*` helpers. Never run a server reachable from the internet with it
([docs/configuration.md](docs/configuration.md#shs_dev-development-mode)).

### Postgres in Docker

If you'd rather not install Postgres, run only the database from the compose
file:

```sh
cp docker/.env.example docker/.env            # then set POSTGRES_PASSWORD
docker compose -f docker/compose.yml up -d db
DATABASE_URL=postgres://shs:<password>@localhost:5432/shs bin/dev
```

### Settings

Everything is optional and set with environment variables. The ones you are
most likely to touch locally are `DATABASE_URL` (default
`postgres://localhost/shs`), `SHS_BIND` (default `0.0.0.0:8080`) and
`SHS_VITE_PORT` (default `5173`). [docs/configuration.md](docs/configuration.md)
lists all of them: the server's, the development scripts' and the asset
pipeline's.

### Development helpers

With `SHS_DEV=1`, the server offers playtest shortcuts:

- `POST /api/dev/grant` gives your character items, Crowns, XP, skill
  XP and spells.
- `POST /api/dev/teleport` moves you anywhere in the castle.
- `POST /api/dev/clock` sets the game clock, so you don't have to wait for the
  next lesson.

[docs/architecture.md](docs/architecture.md), under "HTTP API", documents
their request bodies.

`cargo run -p server -- audit [NAME ...]` replays the item and Crown ledgers
against what every player holds. It is read-only and safe to run while the
game is running.

## Game assets

Everything the client draws (the castle, rooms, furniture, characters,
clothing, hair, icons and spell effects) is exported by Blender into
`client/public/assets/`. That folder is gitignored. Each checkout needs the
assets that match its *fingerprint*, a hash of every asset build input: the
Blender scripts, the data they read, the art sources and the pinned tool
versions. `bin/dev`, and the client's `dev`, `build` and `typecheck` scripts,
use the first of these that works:

1. the assets already in place match the fingerprint, so there is nothing to do;
2. **download** the asset release published for that fingerprint (a GitHub
   Release tagged `assets-<fingerprint>` on this project's repository, about
   110 MB, checked against its sha256);
3. **build** them with Blender 5.2, running only the steps whose inputs
   changed (about twelve minutes from scratch);
4. otherwise it prints why, and the client still runs, drawing greybox
   stand-ins for what is missing.

Releases are downloaded from the GitHub repository named in `package.json`
(`repository`), so clones and forks download this project's releases. A fork
whose asset inputs differ from every published release builds its own assets
with Blender, publishes its own releases, or runs with greybox stand-ins.
`SHS_ASSETS_URL` points the download at another URL or folder laid out the
same way.

To force one source, use `bin/dev --fetch-assets` or `--build-assets` (the same
as `SHS_ASSETS=fetch` or `build`); either fails loudly instead of falling back.
`--skip-assets` skips the asset step altogether.

**Who needs Blender:**

- **Playing, testing or changing code:** nobody. You download the release for
  your checkout. A commit whose assets haven't been published yet runs with
  greybox stand-ins.
- **Changing art:** anyone editing `tools/blender/`, the data the build reads,
  or `art/`. Install:
  - Blender 5.2 (`tools/blender/BLENDER_VERSION`);
  - MPFB 2.0.18, with `tools/blender/install_mpfb.sh` (it installs into the
    gitignored `.blender-user/`);
  - optionally basisu 2.50 (from the
    [basis_universal](https://github.com/BinomialLLC/basis_universal) releases
    or your package manager, e.g. `brew install basis_universal`). Without it
    the castle textures ship as PNG only, without their KTX2 versions.

  The build refuses any other Blender or MPFB version and tells you what to
  install. After asset changes are merged, a maintainer publishes the new
  release with `npm run assets:publish`.

[tools/blender/README.md](tools/blender/README.md) explains the pipeline in
detail, including the "Asset releases" section.
[docs/art/repo-layout.md](docs/art/repo-layout.md) lists every file under
`art/` and the command that rebuilds it.

## Tests and checks

```sh
createdb shs_test        # once: the server's integration tests use a real Postgres
scripts/check.sh         # what CI runs
```

`scripts/check.sh` runs:

- the committed-files check and its tests;
- `cargo fmt --check`;
- clippy, with warnings as errors;
- the Rust tests;
- the WebAssembly build;
- the client typecheck, tests (Vitest) and production build.

The integration tests read `DATABASE_URL`, which defaults to
`postgres://localhost/shs_test`.

## Running your own server

A server is one program and a Postgres database on one Linux machine; a
2 vCPU, 4 GB machine is enough for 1,000 players. It serves the built client
next to the API. [docs/deploy.md](docs/deploy.md) is the guide, with two ways
to run it:

- **[Kamal](https://kamal-deploy.org)** deploys to a server over SSH, with
  automatic HTTPS (or a Cloudflare Tunnel), zero-downtime deploys and
  Postgres on the same machine. Your settings go in a gitignored file:

  ```sh
  gem install kamal                                           # 2.12 or newer
  cp .kamal/deploy.env.example .kamal/deploy.production.env   # server, domain, database password, SMTP
  kamal setup -d production                                   # first time; then: kamal deploy -d production
  ```

- **Docker Compose** (`docker/compose.yml`) runs Postgres and the game on
  one host behind a reverse proxy you provide (nginx, Caddy, Traefik, ...).

Optionally, the game assets can come from an S3-compatible bucket such as
Cloudflare R2 behind a CDN ("Assets from a bucket" in
[docs/deploy.md](docs/deploy.md#assets-from-a-bucket)).

Before opening a server to the public:

- leave `SHS_DEV` unset (see above);
- configure SMTP so players can confirm their accounts. Without it the server
  only writes confirmation and reset links to its log, and you would have to
  hand them out yourself
  ([Running without email](docs/configuration.md#running-without-email));
- replace the chat word list: `data/moderation/blocked_words.txt` ships with
  placeholders only, and operators supply their own.

## Contributing

Read [CONTRIBUTING.md](CONTRIBUTING.md) first. In short:

- **Sign off every commit** (`git commit -s`), certifying the
  [Developer Certificate of Origin](https://developercertificate.org/).
- **Run `scripts/check.sh`** before you push.
- **Don't commit generated files.** That covers screenshots, screen
  recordings, Blender renders, `.blend` files and anything a build produces
  or a script downloads. Keep screenshots in the gitignored
  `.out/screenshots/` and attach them to your pull request. The pre-commit
  hook and CI enforce this rule ([docs/repo-hygiene.md](docs/repo-hygiene.md)
  explains it).
- **Keep it original.** Don't add material from films, games, books or
  official artwork. Names of houses, spells, characters and places live in
  data files, never in code identifiers.

## License

- **Code and game data** (everything outside `art/`, including `data/`): MIT
  (see [LICENSE](LICENSE)).
- **Art sources under `art/`**: CC BY-SA 4.0 (see [art/LICENSE](art/LICENSE)).
  The licence covers contributors' own work; third-party work used there is
  credited in [art/CREDITS.md](art/CREDITS.md) and
  [art/CREDITS-makehuman.md](art/CREDITS-makehuman.md).
- **Built game assets** (`client/public/assets/`, and the published asset
  releases): the characters and hair (and the clothing fitted to them) are
  adaptations of the `art/` sources and of third-party CC BY works, so they
  are CC BY-SA 4.0. Everything else (the castle, grounds, rooms, furniture,
  props, effects, icons and textures) is generated by the pipeline from MIT
  code and data and is MIT.
- **Sound**: the recorded sounds in `client/public/audio/` are CC0 recordings
  by others, credited in
  [client/public/audio/CREDITS.md](client/public/audio/CREDITS.md) (sources
  and edits in `data/audio/sources.json`). Every other sound, and all the
  music, is our own, synthesized in `client/src/audio/`.

### Credits

[data/credits.json](data/credits.json) is the single source of the game's
credits for art, models, animations, fonts and tools (the full records stay
in [art/CREDITS.md](art/CREDITS.md) and
[art/CREDITS-makehuman.md](art/CREDITS-makehuman.md); a test checks they
agree). From it the build makes:

- the in-game **Credits** panel, opened from the sign-in screen and from
  Settings;
- `client/public/assets/CREDITS.md`, `LICENSE-CC-BY-SA-4.0.txt` and
  `LICENSE-MIT.txt`, which
  ship with the built assets and every asset release;
- `THIRD-PARTY-NOTICES.txt` at the root of the built client (`client/dist`),
  with the licence texts of every library in the shipped client (npm
  packages and the Rust crates compiled into the WebAssembly).

Sound credits come from
[client/public/audio/CREDITS.md](client/public/audio/CREDITS.md).
