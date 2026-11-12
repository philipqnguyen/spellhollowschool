# Configuration

Everything about a SpellHollow School server is set with environment
variables. This page lists them all: the game server's own
(`crates/server/src/config.rs`), the ones the Docker Compose file reads, and
the ones the development scripts read. The Kamal deployment's `DEPLOY_*`
settings are listed in [deploy.md](deploy.md#settings).

Every server variable is optional: an empty environment gives a working
development server on `http://localhost:8080` with a local Postgres
database named `shs`. A blank value (`SHS_SMTP_URL=`) counts as unset, as
Docker and Kamal pass blank variables. A value the server cannot parse stops
it at start-up with a message naming the variable.

## Game server

| Variable | Default | What it sets |
| --- | --- | --- |
| `DATABASE_URL` | `postgres://localhost/shs` | The Postgres database. The server applies its migrations (`db/migrations/`) when it starts. |
| `SHS_BIND` | `0.0.0.0:8080` | The address the HTTP API, the WebSocket (`/ws`) and the served client listen on. |
| `SHS_PUBLIC_URL` | `http://localhost:8080` | The base URL players open, used in emailed links (account confirmation, password reset). Must start with `http://` or `https://`; a trailing slash is removed. |
| `SHS_PLAYER_CAP` | `1000` | Players allowed in the world at once; beyond it, logins wait in a queue. |
| `SHS_SMTP_URL` | unset | The mail server, as `smtps://user:pass@host:465` (TLS) or `smtp://user:pass@host:587?tls=required` (STARTTLS). Percent-encode special characters in the user and password. Unset: emails are written to the log instead ([below](#running-without-email)). |
| `SHS_MAIL_FROM` | unset | The sender of every email, e.g. `SpellHollow School <no-reply@example.org>`. Required when `SHS_SMTP_URL` is set. |
| `SHS_STATIC_DIR` | unset | A built client (`client/dist`) to serve next to the API: `/` is its `index.html`, other paths its files. Unset: the server serves no client (in development Vite does). The Docker image sets `/app/public`. |
| `SHS_ASSETS_CDN_URL` | unset | The public URL of an asset bucket (`https://assets.example.org`, no query or fragment) that the client loads the game assets from. The server names it in the `index.html` it serves. Unset: the client loads the assets from this server. See [Assets from a bucket](deploy.md#assets-from-a-bucket). |
| `SHS_TRUST_PROXY` | unset | `1`: the server is behind a reverse proxy that appends the client's address to `X-Forwarded-For`, and per-address rate limits use that address instead of the proxy's. Only set it when the proxy is the only way to reach the server, or clients can choose their own address. |
| `SHS_CLIENT_IP_HEADER` | unset | A header the edge in front of everything sets to the client's address, e.g. `cf-connecting-ip` behind Cloudflare. Rate limits use it, ahead of `SHS_TRUST_PROXY`. Same caveat: only when that edge is the only way in. |
| `SHS_DEV` | unset | `1`: development mode. **Never on a server reachable from the internet** ([below](#shs_dev-development-mode)). Any other value is off. |
| `SHS_LAYOUT` | `data/world/layout.json` | The world layout file. Relative paths are relative to the working directory. The Docker image sets `/app/data/world/layout.json`. |
| `SHS_INSTANCES` | `data/world/instances` | The directory of instance layouts (dorm rooms, classrooms, the duel arena). The Docker image sets `/app/data/world/instances`. |
| `SHS_STAIRS_PERIOD_S` | unset | Makes the moving staircases switch every this many seconds instead of the period in `data/world/stairs.json`. Honoured only with `SHS_DEV=1` (development and load tests). |
| `RUST_LOG` | `info` | The log filter, in [tracing `EnvFilter`](https://docs.rs/tracing-subscriber/latest/tracing_subscriber/filter/struct.EnvFilter.html) syntax, e.g. `info` or `info,server=debug`. |
| `TOKIO_WORKER_THREADS` | one per CPU core | Read by the async runtime, not by the server's code: caps its worker threads. Useful for load tests on a large machine (`tools/loadtest/README.md`). |

### Running without email

New accounts must confirm their email address before they can sign in, and
password resets go by email too. With `SHS_SMTP_URL` unset the server still
runs, but it sends nothing: it writes each email, including its link, to the
log (`email (not sent)`) and warns at start-up that no mail transport is
configured.

For an operator that means players cannot confirm their accounts or reset
their passwords on their own. You can copy a link out of the log
(`docker compose -f docker/compose.yml logs server`, `kamal logs -d <destination>`)
and hand it to the player, or mark an account confirmed in the database:

```sql
UPDATE accounts SET email_verified_at = now() WHERE lower(email) = lower('player@example.org');
```

That is workable for a private server with a few players you know. For
anything public, configure SMTP; any provider works (Postmark, Resend, Amazon
SES, Mailgun, your own mail server).

### `SHS_DEV`: development mode

`SHS_DEV=1` exists for local development and load tests. It:

- returns confirmation and password-reset links in the API's responses (the
  client shows them on screen), so **anyone who knows an account's email
  address can reset its password and take the account over**;
- mounts `/api/dev/*`: sessions for load-test bots without a password, and
  helpers that grant items, Crowns, XP and spells, teleport characters and
  shift the game clock for everyone;
- honours `SHS_STAIRS_PERIOD_S`.

Never set it on a server that anyone else can reach. `bin/dev` sets it by
default for the local server it starts.

### Fixed settings

These are not read from the environment. They are fields of `Config` in
`crates/server/src/config.rs` with code-only defaults (tests tighten some of
them); to change one, change the default and rebuild.

| Setting (`Config` field) | Value |
| --- | --- |
| Chat and in-game mail word filter (`blocked_words_path`) | `data/moderation/blocked_words.txt`, relative to the working directory (`/app/data/moderation/blocked_words.txt` in the image), read at start-up. Missing: no filter. |
| Auth requests per client address (`auth_rate_limit`) | Sign-up, sign-in, confirmation and password-reset requests: 10 in a burst, then one every 6 seconds. Over it: `429 rate_limited`. |
| Emails per account (`email_limit`) | 3 per 15 minutes, shared by password-reset links and re-sent confirmation links (the email sent on registering does not count). Over it nothing is sent, earlier links stay valid, and the API answers `429 too_many_emails`. |
| Wrong passwords per account (`login_failure_limit`) | 10 per 15 minutes. Over it sign-in answers `429 too_many_attempts`, even with the right password, until the window ends; a successful sign-in clears the count. |
| Session re-check (`session_recheck`) | Every 5 minutes a live game connection checks that its session still exists and has not expired, and is kicked (`SessionExpired`) otherwise. Signing out and password resets end the connection at once. |
| Session lifetime (`session_ttl`) | 30 days |
| Confirmation link lifetime (`verification_ttl`) | 48 hours |
| Password reset link lifetime (`password_reset_ttl`) | 1 hour |

Every `429` carries `retry_after` (seconds) in its JSON body and a
`Retry-After` header; the client shows the minutes left. The per-address
and per-account counts are kept in the server's memory: a restart clears
them, and a fixed window's count clears completely when the window ends.

**The word list ships with placeholders.** `data/moderation/blocked_words.txt`
holds a few neutral test words so the filter can be tested end to end; it
blocks nothing real. Supply your own list before opening a server to the
public (one word per line, matched case-insensitively against whole words;
lines starting with `#` are comments). The Docker image copies the file in at
build time, so edit it before building, or mount your own file over
`/app/data/moderation/blocked_words.txt`.

### Other things to know

- `/metrics` (Prometheus format) is served on the same port as everything
  else, without authentication. It holds counts such as players online and
  requests, nothing about any one player. Block it at your proxy if you
  don't want it public.
- `/api/health` answers when the server is up; `shs-server healthcheck`
  probes it (the image has no curl).
- `shs-server audit [NAME ...]` replays the item and Crown ledgers against
  what every character (or the named ones) holds. It reads the same
  variables, is read-only and safe while the game runs.

## Docker Compose (`docker/compose.yml`)

`docker/compose.yml` passes the server variables above from `docker/.env`
(copy `docker/.env.example`). It also reads:

| Variable | Default | What it sets |
| --- | --- | --- |
| `POSTGRES_PASSWORD` | required | The password of the database's `shs` user; the compose file builds `DATABASE_URL` from it. |
| `SHS_HTTP_HOST` | `127.0.0.1` | The host address the game port is published on. Keep the loopback default when a reverse proxy on the same host is in front; `0.0.0.0` exposes the server on every interface (only without a proxy, and then leave `SHS_TRUST_PROXY` and `SHS_CLIENT_IP_HEADER` unset). |
| `SHS_HTTP_PORT` | `8080` | The host port for the game server. |
| `SHS_DB_PORT` | `5432` | The host port Postgres is published on (always on `127.0.0.1`). |

Compose sets `SHS_BIND=0.0.0.0:8080` inside the container and passes
`SHS_PUBLIC_URL`, `SHS_PLAYER_CAP`, `SHS_DEV`, `SHS_SMTP_URL`,
`SHS_MAIL_FROM`, `SHS_TRUST_PROXY`, `SHS_CLIENT_IP_HEADER`,
`SHS_ASSETS_CDN_URL` and `RUST_LOG`. [deploy.md](deploy.md#docker-compose-behind-your-own-reverse-proxy)
shows a full setup.

## Kamal

`config/deploy.yml` sets the server's variables from your
`.kamal/deploy.<destination>.env`: `SHS_PUBLIC_URL` from `DEPLOY_DOMAIN`,
`SHS_TRUST_PROXY=1` (kamal-proxy forwards the client address),
`SHS_CLIENT_IP_HEADER=cf-connecting-ip` behind a Cloudflare Tunnel,
`SHS_ASSETS_CDN_URL` from `DEPLOY_ASSETS_PUBLIC_URL`, and `SHS_PLAYER_CAP`,
`SHS_SMTP_URL`, `SHS_MAIL_FROM` and `RUST_LOG` as you set them. The
`DEPLOY_*` settings are in [deploy.md](deploy.md#settings).

## Development

Read by `bin/dev`, the Vite config and the asset scripts
(`client/scripts/prepare.mjs`, `client/scripts/assets.mjs`):

| Variable | Default | What it sets |
| --- | --- | --- |
| `SHS_VITE_PORT` | `5173` | The Vite dev server's port. |
| `SHS_PREVIEW_PORT` | `4173` | The port of `vite preview` (the built client). |
| `SHS_SERVER` | `http://localhost:<port of SHS_BIND>` | Where Vite proxies `/api` and `/ws`. |
| `SHS_DEV` | `1` under `bin/dev` | As above. Set `SHS_DEV=0` to try the production behaviour locally. |
| `SHS_PUBLIC_URL` | `http://localhost:<SHS_VITE_PORT>` under `bin/dev` | Emailed links open the client, which Vite serves in development. |
| `SHS_ASSETS` | `auto` | How `client/public/assets/` is made: `auto` (download the published release for this checkout, else build with Blender, else greybox stand-ins), `fetch` (download only) or `build` (Blender only). `bin/dev --fetch-assets` / `--build-assets` set it. |
| `SHS_ASSETS_URL` | the GitHub repository in `package.json` | Where asset releases are downloaded from: a URL or a folder laid out like the GitHub Releases. Empty turns downloading off. |
| `SHS_ASSETS_IDLE_SECONDS` | `60` | How long an asset download may receive nothing before it is given up. |
| `SHS_SKIP_ASSETS` | unset | `1`: skip the asset step entirely (`bin/dev --skip-assets`; CI). |
| `SHS_SKIP_WASM_BUILD` | unset | `1`: don't rebuild the WebAssembly bindings. |
| `BLENDER` | `blender` on `PATH` | The Blender 5.2 executable for asset builds. |
| `BLENDER_USER_RESOURCES` | `.blender-user/` when it exists | Blender's user folder, where `tools/blender/install_mpfb.sh` installs MPFB. |
| `BASISU` | `basisu` on `PATH` (and a few standard locations) | The basisu encoder that makes the KTX2 textures. Optional. |

The asset pipeline and its fallbacks are described in
[tools/blender/README.md](../tools/blender/README.md#asset-releases).
