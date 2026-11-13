# Deploying and self-hosting

A SpellHollow School server is one program, `shs-server`, plus a Postgres
database, on one Linux machine. It serves the API, the WebSocket and the built
browser client from one port. There are two ready-made ways to run it:

- **[Kamal](#deploying-with-kamal)** (`config/deploy.yml`): builds the image
  on your machine, deploys it over SSH with zero-downtime switches, and gets a
  Let's Encrypt certificate (or sits behind a Cloudflare Tunnel). This is the
  fuller setup and most of this page.
- **[Docker Compose behind your own reverse proxy](#docker-compose-behind-your-own-reverse-proxy)**
  (`docker/compose.yml`): Postgres and the game on one host, with nginx,
  Caddy, Traefik or similar in front for HTTPS.

Every server setting is listed in [configuration.md](configuration.md).
Before opening a server to the public, read
[Before you open a server to the public](#before-you-open-a-server-to-the-public).

## Deploying with Kamal

The game deploys to one Linux server with [Kamal 2](https://kamal-deploy.org).
Nothing in the configuration is tied to one deployment: everything about
yours goes in a gitignored settings file, so anyone can deploy their own
server from an unchanged checkout.

### What runs where

```
players ──HTTPS──▶ kamal-proxy (:80, :443, Let's Encrypt)          DEPLOY_EDGE=direct
players ──HTTPS──▶ Cloudflare ══tunnel══▶ cloudflared ──▶ kamal-proxy (no ports)
                                                         DEPLOY_EDGE=cloudflare-tunnel
                      │  /, /assets/...  /api/...  /ws (WebSocket)
                      ▼
                spellhollow-web  (docker/Dockerfile: shs-server + the built client)
                      │
                      ▼
                spellhollow-db   (postgres:18, volume spellhollow_pgdata)

optional, DEPLOY_ASSETS_BUCKET ("Assets from a bucket"):
players ──HTTPS──▶ Cloudflare cache (assets.example.org) ──▶ R2 bucket
                   /assets/..., /audio/... by content-addressed key
```

- **One image.** `docker/Dockerfile` builds `shs-server` and copies in the
  built client (`client/dist`, with the game assets). The server answers the
  API and the WebSocket and serves the client's files (`SHS_STATIC_DIR`).
  Optionally the game assets come from an S3-compatible bucket behind a CDN
  instead, and the image leaves them out ("Assets from a bucket").
- **kamal-proxy** switches traffic to a new container once its `/api/health`
  answers. With `DEPLOY_EDGE=direct` (the default) it also listens on ports 80
  and 443 and gets the certificate from Let's Encrypt. With
  `DEPLOY_EDGE=cloudflare-tunnel` it publishes no ports at all: every request
  arrives through a Cloudflare Tunnel ("Cloudflare Tunnel" below).
- **Postgres** runs as a Kamal accessory on the same machine. It is only on the
  Docker network, never published on the host. The server applies its
  migrations when it starts.
- **One server is one world.** The game keeps the world in memory, so it runs
  on one machine. During a deploy the new container takes new connections and
  the old one stops, saving its players' positions; connected players reconnect.

`config/deploy.yml` is the configuration, `.kamal/secrets-common` names the
secrets, `.kamal/hooks/pre-build` builds the client before each image build,
`.kamal/hooks/pre-deploy` uploads the game assets to the asset bucket, if
there is one, before the new version goes live, and `.kamal/hooks/post-deploy`
deletes the bucket's objects no recent version uses once it is live.

### Destinations

Every Kamal command names a destination: `-d production`, `-d staging`, or any
name you like. Each destination has:

- `config/deploy.<destination>.yml`: configuration only that destination
  needs, merged over `config/deploy.yml`. `production` and `staging` are
  committed (empty); copy one for another name.
- `.kamal/deploy.<destination>.env`: its settings (server, domain, passwords),
  yours and gitignored, made from `.kamal/deploy.env.example`.

Settings the file sets win over your shell, so a staging deploy never picks
up production's server by accident. Leave a secret out of the file to supply
it from your shell or a password manager (`.kamal/secrets.<destination>`)
instead. Give each destination its own server: the database container is named
`spellhollow-db` on every one.

### What you need

- **A server**: Ubuntu or Debian, amd64 or arm64, with SSH access as root (or
  as a user in the `docker` group) and ports 22, 80 and 443 open (only 22
  behind a Cloudflare Tunnel). Kamal
  installs Docker on it. A 2 vCPU, 4 GB machine is enough for 1,000 players
  ([tools/loadtest/README.md](../tools/loadtest/README.md)).
- **A domain** whose DNS A record (and AAAA, if the server has IPv6) points at
  the server, for Let's Encrypt on the first deploy. Or, with a Cloudflare
  Tunnel, a domain on Cloudflare and no web ports open at all.
- **An SMTP account** (any provider: Postmark, Resend, Amazon SES, Mailgun,
  your own server...). Players must confirm their email before signing in.
  Without SMTP the server only writes the confirmation and reset links to its
  log, and you would have to hand them to players yourself
  ([Running without email](configuration.md#running-without-email)).
- **On your machine**: the development setup from the README (Rust, Node 25),
  Docker, Ruby 3.2 or newer, and Kamal 2.12 or newer:
  `gem install kamal`. You also need the game assets for your checkout:
  `bin/dev` or `node client/scripts/prepare.mjs` downloads them (or builds them
  with Blender).

### First deploy

1. Make the destination's settings file and fill it in:

   ```sh
   cp .kamal/deploy.env.example .kamal/deploy.production.env
   ```

   Required: `DEPLOY_HOST` (the server's IP or hostname), `DEPLOY_DOMAIN`
   (e.g. `game.example.org`) and `POSTGRES_PASSWORD` (letters and digits only,
   e.g. `openssl rand -hex 32`). Set `SHS_SMTP_URL` and `SHS_MAIL_FROM` too,
   unless you will confirm accounts by hand. The file lists the optional ones
   with their defaults.

2. Check that your SSH key gets into the server (`ssh root@<DEPLOY_HOST>`),
   then:

   ```sh
   kamal setup -d production
   ```

   This installs Docker on the server, starts Postgres and kamal-proxy, builds
   the image (the pre-build hook runs `npm run build -w client` first),
   pushes it, and starts the game. Open `https://<DEPLOY_DOMAIN>`.

The first build compiles the server from scratch and takes several minutes.
The Dockerfile cross-compiles, so building an amd64 image on an Apple
Silicon laptop runs at native speed. The image is about 250 MB; most of it is
the game assets. With an asset bucket it is about 40 MB, and a deploy that
changes only code pushes the server and client layers, about 15 MB ("Assets
from a bucket").

### Cloudflare Tunnel

With `DEPLOY_EDGE=cloudflare-tunnel` the server accepts no web traffic of its
own. `cloudflared` runs as a Kamal accessory (`spellhollow-tunnel`), dials out
to Cloudflare, and hands each request to kamal-proxy over the Docker network.
kamal-proxy publishes no ports, Cloudflare serves HTTPS to players, and the
server's IP address never appears in DNS.

1. In the Cloudflare dashboard (the domain's zone must be on Cloudflare): go to
   Zero Trust, then Networks, Tunnels, and create a tunnel of type
   Cloudflared. Copy its token, the long string after `--token` in the
   install command (you don't run that command; Kamal runs cloudflared).
2. In the tunnel, add a public hostname: your `DEPLOY_DOMAIN` (for example
   `game` on `example.org`), service type **HTTP**, URL
   **`kamal-proxy:80`**. Cloudflare creates the DNS record for it. Delete any
   older A or AAAA record for that name first.
3. In `.kamal/deploy.<destination>.env` set:

   ```sh
   DEPLOY_EDGE=cloudflare-tunnel
   CLOUDFLARE_TUNNEL_TOKEN=<the token>
   ```

4. `kamal setup -d <destination>` boots the tunnel with the database. To add
   the tunnel to a server that already runs the game directly:
   `kamal proxy reboot -d <destination>` (kamal-proxy drops its published
   ports), then `kamal accessory boot tunnel -d <destination>`, then
   `kamal deploy -d <destination>`.

Then close ports 80 and 443 in the server's firewall (nothing listens on them
any more) and keep only SSH, ideally limited to your own addresses.

What changes for the game:
- `SHS_PUBLIC_URL` stays `https://<DEPLOY_DOMAIN>`; Cloudflare does TLS, so
  kamal-proxy gets no certificate and `DEPLOY_SSL` is ignored.
- The server keys its rate limits on the `CF-Connecting-IP` header Cloudflare
  sets (`SHS_CLIENT_IP_HEADER`). That is safe only because the tunnel is the one
  way in: don't publish the server's ports alongside it.
- WebSockets work through the tunnel without settings.

### Assets from a bucket

The game assets (models, textures, icons, manifests and sounds: about 240 MB
in some 800 files under `client/dist/assets/` and `audio/`) can be served from
an S3-compatible bucket behind a CDN instead of by the game server. This is
optional; without it everything works as described above.

How it works:

- **Every file has its own immutable URL.** The client build hashes every
  asset file and compiles the map, logical path to content hash, into the
  game's script bundle (Vite names the bundle by its own hash, so the map is
  cached with it), and writes it as `client/dist/asset-map.json`. A file's
  bucket key carries its hash: `assets/world/cell_12.3f9a1c7e5b2d4f60.glb`.
  A key's content never changes, so browsers and the CDN keep it forever,
  and after a deploy players download only the files that changed.
- **The pre-deploy hook uploads** (`client/scripts/upload-assets.mjs`, no
  dependencies) every key the bucket does not have yet, with its
  `Content-Type` and `Cache-Control: public, max-age=31536000, immutable`.
  It never overwrites or deletes anything, so open tabs on an older version
  and rollbacks keep finding their files. Models, JSON and other types that
  shrink are stored gzip-compressed (`Content-Encoding: gzip`; 240 MB of
  files take about 116 MB), because Cloudflare does not compress
  `model/gltf-binary` on its own; browsers decode them transparently. After
  uploading, the hook loads one compressed object through the public URL as
  the game's page would, and fails the deploy if the bytes, the CORS header
  or the `Cache-Control` are wrong.
- **The image leaves the assets out** (the build argument
  `ASSETS_FROM_BUCKET=1`, set by `config/deploy.yml`): it shrinks from about
  240 MB to about 40 MB. It keeps the KTX2 texture decoder
  (`assets/ktx2/`), which runs in Web Workers and so must come from the
  game's own origin, and `asset-map.json`. The credits and licences always
  stay in the image: `assets/CREDITS.md`, `assets/LICENSE*`,
  `audio/CREDITS.md`, `fonts/*-OFL.txt` and `THIRD-PARTY-NOTICES.txt`. The
  Dockerfile refuses a `client/dist` without `THIRD-PARTY-NOTICES.txt`, or
  one with assets but no `assets/CREDITS.md` (run
  `node client/scripts/prepare.mjs` or rebuild the client).
- **The server tells the client where the assets are**: it gets
  `SHS_ASSETS_CDN_URL` (your `DEPLOY_ASSETS_PUBLIC_URL`) and adds
  `<meta name="shs-assets-cdn" content="...">` to the `index.html` it serves.
  The same image works without a bucket: without the variable the client
  loads every asset from the game server as `/assets/<path>?v=<hash>`, which
  the server caches for a year only when the hash is the one of the file it
  has (an old tab during a deploy gets `no-cache`).
- **Old versions are pruned after each deploy.** Once the new version is
  live, the post-deploy hook (`client/scripts/prune-assets.mjs`) stores its
  asset map in the bucket as `deploys/<time>-<version>.json`, keeps the
  newest three records (`DEPLOY_ASSETS_PRUNE_KEEP`) and deletes every object
  under `assets/` and `audio/` that none of them names, along with the older
  records. The live version and the two before it keep their files, which
  matches the containers Kamal keeps for `kamal rollback` (`retain_containers`
  in `config/deploy.yml`). Until three deploys are recorded it deletes
  nothing, and a record it cannot read stops it. A rollback does not prune.
  A failed prune only warns. `DEPLOY_ASSETS_PRUNE=0` turns it off; to see
  what it would delete, run
  `node client/scripts/prune-assets.mjs --version <id> --env-file .kamal/deploy.production.env --dry-run`.

#### Setting it up with Cloudflare R2

The domain's zone must be on Cloudflare (as for the tunnel). The assets get a
hostname of their own, here `assets.example.org`.

1. **The bucket.** In the Cloudflare dashboard: R2 Object Storage, Create
   bucket, e.g. `spellhollow-assets` (location automatic, Standard storage).
2. **Its public hostname.** In the bucket's Settings, Custom Domains, Add:
   `assets.example.org`. Cloudflare creates the DNS record and the
   certificate. Leave the `r2.dev` public URL disabled: it is rate-limited
   and not cached.
3. **CORS.** The game's page loads the assets with `fetch` and as WebGL
   textures, so the bucket must allow the game's origin. In the bucket's
   Settings, CORS Policy, Add CORS policy, paste (one entry per game origin;
   add `https://staging.example.org` if staging uses the same bucket):

   ```json
   [
     {
       "AllowedOrigins": ["https://game.example.org"],
       "AllowedMethods": ["GET", "HEAD"],
       "AllowedHeaders": ["*"],
       "ExposeHeaders": ["Content-Length", "Content-Type", "ETag"],
       "MaxAgeSeconds": 86400
     }
   ]
   ```

   Cloudflare's cache does not vary on `Origin`: a copy cached for one origin
   carries that origin's `Access-Control-Allow-Origin`. When two game origins
   (production and staging) read one bucket, give each its own bucket, or use
   `"AllowedOrigins": ["*"]` (the assets are public anyway). After changing
   the policy, purge the hostname's cache (Caching, Configuration, Purge
   Cache, Custom Purge, Hostname).
4. **Caching.** Requests to an R2 custom domain go through Cloudflare's
   cache, but by default only for file extensions Cloudflare caches on its
   own (`.png` yes, `.glb`, `.json`, `.ktx2`, `.mp3` no). In the zone:
   Caching, Cache Rules, Create rule, named e.g. "Game assets":
   - When incoming requests match: Custom filter expression, Field
     *Hostname*, Operator *equals*, Value `assets.example.org`;
   - Then: Cache eligibility **Eligible for cache**; Edge TTL **Use cache-control
     header if present, bypass cache if not**; Browser TTL **Respect origin
     TTL**;
   - Deploy.

   Every object carries `max-age=31536000, immutable`, so the edge and
   browsers keep it a year. Optionally turn on Tiered Cache (Caching, Tiered
   Cache) so fewer requests reach R2.
5. **An API token for the uploader.** R2 Object Storage, Manage API tokens,
   Create API token: permission **Object Read & Write**, applied to the one
   bucket, no expiry (or one you renew). Copy the Access Key ID and the
   Secret Access Key (shown once), and note the S3 endpoint
   `https://<account id>.r2.cloudflarestorage.com` from the same page.
6. **The settings**, in `.kamal/deploy.<destination>.env`:

   ```sh
   DEPLOY_ASSETS_BUCKET=spellhollow-assets
   DEPLOY_ASSETS_S3_ENDPOINT=https://<account id>.r2.cloudflarestorage.com
   DEPLOY_ASSETS_PUBLIC_URL=https://assets.example.org
   DEPLOY_ASSETS_ACCESS_KEY_ID=<access key id>
   DEPLOY_ASSETS_SECRET_ACCESS_KEY=<secret access key>
   ```

   Only the deploying machine uses the keys (the pre-deploy and
   post-deploy hooks run there); the game server never gets them. They may also come from your
   shell instead of the file.
7. `kamal deploy -d <destination>`. The first deploy uploads everything
   (about 116 MB); later ones only what changed. To upload without
   deploying: `node client/scripts/upload-assets.mjs --env-file
   .kamal/deploy.<destination>.env` (`--dry-run` lists what it would send).

To check by hand that Cloudflare hands out a gzip object once compressed, not
twice: `curl -sI -H 'Accept-Encoding: gzip' -H 'Origin: https://<DEPLOY_DOMAIN>'
https://assets.example.org/<a .glb key from the upload's output>` should show
`content-encoding: gzip`, `access-control-allow-origin`, and after a second
request `cf-cache-status: HIT`; the hook's own check fails the deploy if the
body comes back compressed twice. Should that ever happen,
`DEPLOY_ASSETS_GZIP=0` stores new objects uncompressed (objects already in
the bucket stay as they are: delete them to have them uploaded again).

Other S3-compatible services work the same way: AWS S3
(`DEPLOY_ASSETS_S3_ENDPOINT=https://s3.<region>.amazonaws.com`,
`DEPLOY_ASSETS_S3_REGION=<region>`, a public-read bucket policy, CORS, and a
CDN in front), MinIO (`http://host:9000`, region `us-east-1`). Requests are
path-style (`<endpoint>/<bucket>/<key>`).

To stop using the bucket, remove `DEPLOY_ASSETS_BUCKET` and deploy: the
image carries the assets again and the server serves them.

### Every deploy after that

```sh
kamal deploy -d production
```

The image is built from your working tree (it needs the gitignored
`client/dist`). Kamal tags it with the commit id, and adds `_uncommitted_...`
when the tree has uncommitted changes. Deploy committed code.

If `client/public/assets/` does not match the checkout (for example after a
pull that changed the art), the pre-build hook stops: run
`node client/scripts/prepare.mjs` to fetch or build the assets, then deploy
again. `DEPLOY_ALLOW_GREYBOX=1` deploys anyway, with greybox stand-ins.

### Settings

All of them go in the destination's `.kamal/deploy.<destination>.env`.

| Setting | Default | What it does |
| --- | --- | --- |
| `DEPLOY_HOST` | required | The server Kamal deploys to over SSH |
| `DEPLOY_DOMAIN` | required | The domain kamal-proxy answers and gets a certificate for; also `SHS_PUBLIC_URL` |
| `POSTGRES_PASSWORD` | required | The database password (`.kamal/secrets-common` builds `DATABASE_URL` from it) |
| `SHS_SMTP_URL` | empty | The mail server: `smtps://user:pass@host:465` or `smtp://user:pass@host:587?tls=required`. Empty: emails are only logged |
| `SHS_MAIL_FROM` | empty | The sender, e.g. `SpellHollow School <no-reply@example.org>`; required with `SHS_SMTP_URL` |
| `DEPLOY_SSH_USER`, `DEPLOY_SSH_PORT` | `root`, `22` | How Kamal logs in |
| `DEPLOY_ARCH` | `amd64` | The server's CPU architecture (`amd64` or `arm64`) |
| `DEPLOY_EDGE` | `direct` | `cloudflare-tunnel`: every request through a Cloudflare Tunnel, no published web ports ("Cloudflare Tunnel") |
| `CLOUDFLARE_TUNNEL_TOKEN` | - | The tunnel's token, with `DEPLOY_EDGE=cloudflare-tunnel` |
| `DEPLOY_SSL` | `true` | With `direct`: `false` serves plain HTTP (behind a TLS-terminating load balancer) |
| `DEPLOY_REGISTRY` | `localhost:5555` | The image registry. The default runs one on your machine and the server pulls through SSH, so no account is needed |
| `DEPLOY_REGISTRY_USERNAME`, `KAMAL_REGISTRY_PASSWORD`, `DEPLOY_IMAGE` | -, -, `spellhollow` | For a hosted registry, e.g. `ghcr.io`, your user, a token, and `<user>/spellhollow` |
| `DEPLOY_BUILDER_REMOTE` | empty | Build on another machine, e.g. `ssh://root@<host>` |
| `SHS_PLAYER_CAP` | `1000` | Players in the castle at once; beyond it logins queue |
| `RUST_LOG` | `info` | The server's log filter |
| `GOODSORCERY_API_KEY` | empty | The Good Sorcery API key ("Error tracking and performance"). Empty: nothing is reported |
| `GOODSORCERY_PERFORMANCE_MONITORING` | `1` | `0` reports errors only, without response times |
| `DEPLOY_ALLOW_GREYBOX` | `0` | `1` deploys without the matching game assets |
| `DEPLOY_ASSETS_BUCKET` | empty | The asset bucket ("Assets from a bucket"). Empty: the image carries the assets and the server serves them |
| `DEPLOY_ASSETS_S3_ENDPOINT` | required with a bucket | Its S3 API endpoint: `https://<account id>.r2.cloudflarestorage.com` for R2 |
| `DEPLOY_ASSETS_S3_REGION` | `auto` | The bucket's region (`auto` for R2; e.g. `eu-west-1` on AWS, `us-east-1` on MinIO) |
| `DEPLOY_ASSETS_PUBLIC_URL` | required with a bucket | The bucket's public URL, e.g. `https://assets.example.org`; becomes the server's `SHS_ASSETS_CDN_URL` |
| `DEPLOY_ASSETS_ACCESS_KEY_ID`, `DEPLOY_ASSETS_SECRET_ACCESS_KEY` | required with a bucket | The uploader's API token (Object Read & Write on the bucket); used on your machine only, never sent to the server |
| `DEPLOY_ASSETS_GZIP` | `1` | `0` stores new objects uncompressed |
| `DEPLOY_ASSETS_VERIFY` | `1` | `0` skips the check of the public URL after uploading |

The server's own variables are listed in [configuration.md](configuration.md).
`config/deploy.yml` sets `SHS_TRUST_PROXY=1` (rate limits key
on the address kamal-proxy forwards), `SHS_CLIENT_IP_HEADER` behind the tunnel,
`SHS_ASSETS_CDN_URL` with an asset bucket, and `SHS_PUBLIC_URL`.

### Error tracking and performance

Optional. The server can report its errors (panics, `500`s with their
cause, failed database work in the game world and the background jobs) and
the API's response times to [Good Sorcery](https://app.goodsorcery.com).
Without a key nothing is sent and the deploy works the same.

1. Create a project at [app.goodsorcery.com](https://app.goodsorcery.com) (or
   on your own instance) and copy its API key from **Settings › Setup & API
   keys**.
2. Put it in `.kamal/deploy.<destination>.env`:
   `GOODSORCERY_API_KEY=gs_live_…` (for your own instance, also
   `GOODSORCERY_ENDPOINT=https://errors.example.org` there, and add
   `GOODSORCERY_ENDPOINT` to `env.clear` in `config/deploy.yml`).
3. `kamal deploy -d production` (or, without a new image,
   `kamal app boot -d production`, which restarts the server with the new
   settings).

Each report names the deployed commit (Kamal's `KAMAL_VERSION`). Performance
monitoring (`GOODSORCERY_PERFORMANCE_MONITORING`, default `1`) adds the
API's response times per endpoint, sent once a minute; `0` turns it off.
What is sent, and what never is (request bodies, headers, cookies, players'
emails), is in [configuration.md](configuration.md#error-tracking-and-performance).

### Running things on the server

```sh
kamal logs -d production                 # follow the game server's log
kamal console -d production              # a shell in the running container
kamal ledger-audit -d production         # replay the item and Crown ledgers (read-only)
kamal psql -d production                 # psql on the game database
kamal app details -d production          # what is running
kamal rollback <version> -d production   # back to an earlier image (kamal app images -d production lists them)
```

### Backups

The database lives in the Docker volume `spellhollow_pgdata` on the server.
Back it up off the machine regularly, for example from a cron job on the
server:

```sh
docker exec spellhollow-db pg_dump -U shs -Fc shs > /root/backups/shs-$(date +%F).dump
```

Restore with `pg_restore -U shs -d shs --clean` inside the container
(`kamal accessory exec db --interactive --reuse sh -d production`).
`kamal accessory remove db -d production`
deletes the container, but the volume stays until you remove it with
`docker volume rm`.

## Docker Compose behind your own reverse proxy

`docker/compose.yml` runs Postgres and the game server on one host, without
Kamal. It publishes the game on `127.0.0.1:8080` only, for a reverse proxy on
the same host (nginx, Caddy, Traefik, ...) that terminates HTTPS and forwards
to it. Postgres is published on `127.0.0.1:5432` for your own tools and
backups, never on a public interface.

### What you need

- A Linux host with Docker and the Compose plugin. 2 vCPU and 4 GB is
  enough for 1,000 players; the compose file sizes Postgres for that.
- A domain pointing at the host, and a reverse proxy with a certificate for it.
- The image takes the client built on the host (`client/dist`), so the build
  machine needs the development setup from the README (Rust with the
  `wasm32-unknown-unknown` target, Node 25) and the game assets for the
  checkout, which `npm run build -w client` downloads (or builds) itself.
- An SMTP account, ideally ([Running without email](configuration.md#running-without-email)).

### Setting it up

1. Make the settings file:

   ```sh
   cp docker/.env.example docker/.env
   ```

   Set at least:

   ```sh
   POSTGRES_PASSWORD=<openssl rand -hex 32>
   SHS_PUBLIC_URL=https://game.example.org
   SHS_SMTP_URL=smtps://USER:PASSWORD@smtp.example.org:465
   SHS_MAIL_FROM=SpellHollow School <no-reply@example.org>
   SHS_TRUST_PROXY=1
   ```

   Leave `SHS_DEV` empty and `SHS_HTTP_HOST=127.0.0.1`.
   [configuration.md](configuration.md#docker-compose-dockercomposeyml)
   describes every variable.

2. Build the client and start everything:

   ```sh
   npm ci
   npm run build -w client
   docker compose -f docker/compose.yml up -d --build
   ```

   The server applies its database migrations when it starts.
   `docker compose -f docker/compose.yml ps` shows both containers healthy.

3. Point your proxy at `127.0.0.1:8080` (examples below) and open
   `https://game.example.org`.

To update: pull, rebuild the client, and run the same `up -d --build`. The
old container stops, saving its players, and the new one starts; players
reconnect after a short gap (there is no zero-downtime switch as with Kamal).

### The client's address: `SHS_TRUST_PROXY` or `SHS_CLIENT_IP_HEADER`

The server rate-limits sign-ups, sign-ins and password resets per client
address. Behind a proxy every request comes from the proxy, so tell the
server where the real address is. Set exactly one:

- `SHS_TRUST_PROXY=1` when your proxy appends the client's address to
  `X-Forwarded-For` (nginx with `$proxy_add_x_forwarded_for`, Caddy and
  Traefik by default). The server takes the last entry, the one your proxy
  added.
- `SHS_CLIENT_IP_HEADER=<header>` when an edge in front of everything sets a
  header of its own, e.g. `cf-connecting-ip` when all traffic comes through
  Cloudflare.

Either one is safe only if the proxy is the only way to reach the server:
keep `SHS_HTTP_HOST=127.0.0.1` (the default). If the port were published on
a public interface, anyone could connect directly and pick their own
address. Without a proxy at all (plain HTTP, testing only) set
`SHS_HTTP_HOST=0.0.0.0` and leave both unset.

### Proxy examples

The proxy must pass WebSocket upgrades on `/ws` and should not buffer the
large asset downloads. Caddy does both by default and gets the certificate
itself:

```
game.example.org {
    reverse_proxy 127.0.0.1:8080
}
```

nginx (certificate lines left out):

```nginx
server {
    listen 443 ssl;
    server_name game.example.org;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
        proxy_buffering off;
        proxy_read_timeout 1h;
    }
}

# In the http block:
map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}
```

### Assets from a bucket, with Compose

Compose passes `SHS_ASSETS_CDN_URL` to the server, so the client can load the
game assets from a bucket as described in
[Assets from a bucket](#assets-from-a-bucket). Without Kamal's pre-deploy
hook, upload them yourself before starting a new version:

```sh
node client/scripts/upload-assets.mjs --env-file assets.env --origin https://game.example.org
```

where `assets.env` (keep it out of git) holds the `DEPLOY_ASSETS_*` settings
from the [Settings](#settings) table; the script's header lists them. Then set
`SHS_ASSETS_CDN_URL` to the bucket's public URL. The compose file builds the
image with the assets inside either way.

### Day to day

```sh
docker compose -f docker/compose.yml logs -f server              # the server's log
docker compose -f docker/compose.yml exec server shs-server audit  # replay the ledgers (read-only)
docker compose -f docker/compose.yml exec db psql -U shs shs     # psql on the game database
docker compose -f docker/compose.yml exec db pg_dump -U shs -Fc shs > shs-$(date +%F).dump
```

The database lives in the `shs_pgdata` volume. Back it up off the machine
regularly.

## Before you open a server to the public

- **`SHS_DEV` must be off.** With `SHS_DEV=1` the API hands out password
  reset links to whoever asks, so anyone can take over any account, and the
  `/api/dev/*` helpers are open to everyone. Never set it on a server
  reachable from the internet ([configuration.md](configuration.md#shs_dev-development-mode)).
- **Supply your own chat word list.** `data/moderation/blocked_words.txt`
  ships with neutral placeholders that block nothing real. Replace it with
  your list before building the image, or mount yours over
  `/app/data/moderation/blocked_words.txt`.
- **Configure email**, or plan to confirm accounts by hand
  ([Running without email](configuration.md#running-without-email)).
- **Keep the game port private** when you set `SHS_TRUST_PROXY` or
  `SHS_CLIENT_IP_HEADER` (Kamal and the compose file do by default).
- **Back up the database** off the machine.
- Players must be 18 or older; sign-up asks them to confirm it.

## Notes

- `/metrics` (Prometheus) is served publicly along with everything else. It
  shows counts such as players online and requests, nothing about any player.
  Block it at your proxy if you prefer.
- The game has no paths of its own: everything is on `/`, with links that
  carry query strings (`/?reset=...`). Any path that is not a file, `/api` or
  `/ws` is a 404.
- The client supports Google Chrome only. Other browsers, including every
  browser on iPhone and iPad, see a card asking for Chrome.
