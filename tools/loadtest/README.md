# Load-test bots

`loadtest` runs N simulated players against a server started with
`SHS_DEV=1` (it uses the development endpoint `POST /api/dev/bot-session`, so
never point it at a public server). Each bot gets a session, dresses through
the wardrobe API, connects to `/ws`, walks its scenario with `sim-core`'s own
movement and navigation (so the server never has a reason to correct it),
chats, emotes and pings, and decodes every snapshot as the browser does.
While the bots run, the tool scrapes the server's `/metrics` and, optionally,
`docker stats`. At the end it prints a summary against the budgets below,
writes it as JSON to the gitignored `loadtest-results/`, and exits 0 only if
every budget passed.

```sh
cargo run --release -p loadtest -- --help
```

lists every option. The module docs in `src/main.rs` describe the bots in
more detail.

## Running a load test

### Against a local server

```sh
createdb shs_load
cargo build --release -p server -p loadtest
SHS_DEV=1 SHS_BIND=127.0.0.1:8080 DATABASE_URL=postgres://localhost/shs_load \
  target/release/shs-server &
target/release/loadtest --url http://127.0.0.1:8080 --bots 200 --scenario spread
```

A fast development machine has far more cores than a small game server.
To get numbers closer to a 2-CPU host, cap the server's async runtime with
`TOKIO_WORKER_THREADS=2` (tokio reads it; no code change). That does not cap
the world thread, the snapshot encoders (one per core) or tokio's blocking
pool, so compare such runs against each other rather than against real
hardware. The bots' own runtime is separate (`--threads N`).

### Emulating a 2 vCPU / 4 GB server with Docker

`docker/compose.loadtest.yml` runs Postgres and the game server pinned to the
same two CPUs, with memory capped at 3 GB for the server and 1 GB for
Postgres, as on a small VPS. The bots run on the host, outside those limits.

```sh
docker compose -f docker/compose.loadtest.yml up -d --build --wait
cargo run --release -p loadtest -- --bots 1000 --scenario spread \
    --containers shs-loadtest-server-1,shs-loadtest-db-1
docker compose -f docker/compose.loadtest.yml down -v
```

`SHS_LT_CPUSET` picks other CPUs (for example `2,3`) if 0 and 1 are busy.

## Budgets

The summary checks these (`src/report.rs`):

| Budget | Limit |
| --- | --- |
| World tick, p99 | 15 ms |
| Data per client | 3.5 KB/s at the default view limit (50), 6 KB/s above it |
| Server memory | 1 GiB |
| Server CPU (with `--containers`) | 150 % (75 % of 2 CPUs) |
| Corrections, desyncs, disconnects | none expected |

On the reference setup (1,000 bots, the server capped to two worker
threads, every role active), the server stays well inside these: around
1.2 KB/s per client, a tick p99 under 4 ms and under 200 MiB of memory.

## Scenarios (`scenarios.json`)

| Name | Who goes where |
| --- | --- |
| `spread` (default) | Everyone roams the castle and the grounds; destinations are weighted by floor area, so about 40 % of the trips end outside |
| `packed` | Everyone stays in the Entrance Hall and the Great Hall |
| `exchange` | A crowd in the Owl Exchange court and the Exchange Yard |
| `grounds` | 300 walkers tour the hilly grounds (lake shore, boathouse, cottage, stone circle, greenhouses); the rest stay packed in the halls |
| `pitch` | 300 flyers over the Flying Pitch, 100 spectators on its stands; the rest packed in the halls |
| `stairs` | 300 walkers ride the Grand Staircase's moving flights; the rest packed in the halls |
| `secrets` | Every role at once: grounds walkers, stair walkers, explorers through the hidden passages and bots that open the Wishing Room; the rest packed in the halls |

Each named role's numbers (`grounds walker`, `flyer`, `pitch spectator`,
`stair walker`, `explorer`, `ritual`, `student`) are reported separately on
the summary's `roles:` line.

## Options worth knowing

- `--bots N`, `--ramp-secs S` (bots join evenly over S), `--settle-secs S`
  (left out of the measured rates), `--measure-secs S` or `--duration-secs S`.
  `--long-window` measures at least 660 s, so a class run covers whole
  sessions.
- `--view-limit N` sends `SetViewLimit` after joining (the server default is 50).
- `--class-pct P` sends P % of the bots to class (it shifts the dev clock so
  the slot's sessions open as the run starts); `--class-slot HH:MM` and
  `--class-only ID` choose the slot and classes. `--class-pct 6 --class-only
  flying` puts 60 students in the Flying class's ring course.
- `--duel-pct P`: P % at the Dueling Club, in pairs, one in five watching.
- `--fly-pct P`: P % of the walking bots fly loops over the grounds and the lake.
- `--stairs-period-s S`: the bots expect the moving staircases to switch every
  S seconds. Run the server with the same `SHS_STAIRS_PERIOD_S=S` (honoured
  only with `SHS_DEV=1`).
- `--effects-pct P`: P % drink a potion, so their looks carry visible effects.
- `--npc-talk-every-secs S`: every bot talks to a resident NPC in view about
  every S seconds.
- `--players-pct P`, `--cup-poll-secs S`, `--cup-close`: exercise the
  players list, the House Cup endpoint and the weekly close.
- `--containers A,B` samples those containers with `docker stats`;
  `--out-dir DIR` moves the JSON summary.

A full all-roles run looks like this:

```sh
target/release/loadtest --url http://127.0.0.1:8080 --bots 1000 --ramp-secs 60 \
  --settle-secs 30 --scenario secrets --stairs-period-s 20 --class-pct 35 \
  --class-slot 10:30 --duel-pct 10 --fly-pct 75 --cup-poll-secs 30 \
  --long-window --view-limit 50
```

(with the server started with `SHS_DEV=1 SHS_STAIRS_PERIOD_S=20`).

## Client performance

The browser side is measured separately: with `?probe` in the page URL the
client exposes `window.shsProbe` (`client/src/game/perf-probe.ts`), which
drives the camera along fixed paths and reports frame times, draw calls and
GPU memory. The castle's camera paths are in `perf_paths.json` in this
folder; a test in `src/nav.rs` checks that each stop stands on walkable
ground.
