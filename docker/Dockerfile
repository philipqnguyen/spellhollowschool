# syntax=docker/dockerfile:1
#
# The game image: shs-server plus the built client it serves (SHS_STATIC_DIR).
# Build from the repository root after building the client:
#
#   npm run build -w client          # client/dist, with the game assets
#   docker build -f docker/Dockerfile -t shs-server .
#   # assets from a bucket (SHS_ASSETS_CDN_URL), left out of the image:
#   docker build -f docker/Dockerfile --build-arg ASSETS_FROM_BUCKET=1 -t shs-server .
#
# `kamal deploy` (config/deploy.yml) and docker/compose.yml do both. Ignore
# rules: docker/Dockerfile.dockerignore.
#
# The server is cross-compiled on the build machine's own platform for the
# target platform (--platform linux/amd64 or linux/arm64), so an arm64 laptop
# builds an amd64 image at native speed instead of under emulation.

# ---------------------------------------------------------------- server
# The Rust release rust-toolchain.toml pins.
FROM --platform=$BUILDPLATFORM rust:1.99.1-slim-trixie AS build
ARG TARGETARCH
WORKDIR /src

# The Rust target for TARGETARCH and, when it differs from the build machine,
# a C cross compiler and linker for it (ring, the TLS crypto, compiles C).
RUN set -eu; \
    case "$TARGETARCH" in \
      amd64) triple=x86_64-unknown-linux-gnu; gnu=x86_64-linux-gnu; pkg=x86-64-linux-gnu ;; \
      arm64) triple=aarch64-unknown-linux-gnu; gnu=aarch64-linux-gnu; pkg=aarch64-linux-gnu ;; \
      *) echo "unsupported target architecture: $TARGETARCH" >&2; exit 1 ;; \
    esac; \
    echo "$triple" > /target-triple; \
    : > /target-env; \
    if [ "$(dpkg --print-architecture)" != "$TARGETARCH" ]; then \
      apt-get update; \
      apt-get install -y --no-install-recommends "gcc-$pkg" "libc6-dev-$TARGETARCH-cross"; \
      rm -rf /var/lib/apt/lists/*; \
      rustup target add "$triple"; \
      env_triple="$(echo "$triple" | tr 'a-z-' 'A-Z_')"; \
      echo "export CARGO_TARGET_${env_triple}_LINKER=$gnu-gcc" >> /target-env; \
      echo "export CC_$(echo "$triple" | tr '-' '_')=$gnu-gcc" >> /target-env; \
    fi

COPY . .
# Cache mounts keep the registry and build artefacts between builds; the binary
# is copied out because the target directory is not part of the image layer.
RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/src/target,id=shs-target-$TARGETARCH \
    . /target-env \
    && triple="$(cat /target-triple)" \
    && cargo build --release --locked -p server --bin shs-server --target "$triple" \
    && cp "target/$triple/release/shs-server" /usr/local/bin/shs-server

# ---------------------------------------------------------------- client
# The built client, with a .gz beside every file that compresses well (the
# server sends those to browsers that accept gzip). Platform-independent.
#
# ASSETS_FROM_BUCKET=1 (Kamal passes it when DEPLOY_ASSETS_BUCKET is set): the
# game assets load from an asset bucket (docs/deploy.md, "Assets from a
# bucket"), so the image leaves out client/dist/assets/ and audio/ (about
# 230 MB) but the KTX2 decoder (assets/ktx2/, which workers load from the
# game's own origin) and asset-map.json: about 40 MB instead of 240.
#
# Credits and licences always stay in the image (client/scripts/credits.mjs):
# assets/CREDITS.md, assets/LICENSE-CC-BY-SA-4.0.txt and assets/LICENSE-MIT.txt
# (the attribution the CC BY works in the assets need, and the assets' licences), audio/CREDITS.md, fonts/*-OFL.txt and
# THIRD-PARTY-NOTICES.txt. A client built with assets must carry them.
FROM --platform=$BUILDPLATFORM debian:trixie-slim AS client
ARG ASSETS_FROM_BUCKET=0
COPY client/dist /public
RUN test -f /public/index.html \
      || { echo "client/dist is missing: run npm run build -w client first" >&2; exit 1; } \
    && test -f /public/THIRD-PARTY-NOTICES.txt \
      || { echo "client/dist/THIRD-PARTY-NOTICES.txt is missing: build the client with client/vite.config.ts (creditsPlugin)" >&2; exit 1; } \
    && if [ -f /public/assets/world/manifest.json ] && [ ! -f /public/assets/CREDITS.md ]; then \
         echo "client/dist/assets has no CREDITS.md: run node client/scripts/prepare.mjs (or npm run build -w client) again" >&2; exit 1; \
       fi \
    && if [ "$ASSETS_FROM_BUCKET" = "1" ]; then \
         test -f /public/asset-map.json \
           || { echo "client/dist/asset-map.json is missing: the client must be built with the asset map" >&2; exit 1; }; \
         find /public/assets -mindepth 1 -maxdepth 1 ! -name ktx2 ! -name CREDITS.md ! -name 'LICENSE*' -exec rm -rf {} +; \
         find /public/audio -mindepth 1 -maxdepth 1 ! -name CREDITS.md -exec rm -rf {} +; \
       fi \
    && find /public -type f -size +1k \
         \( -name '*.js' -o -name '*.css' -o -name '*.html' -o -name '*.json' \
            -o -name '*.svg' -o -name '*.wasm' -o -name '*.glb' -o -name '*.txt' \) \
         -exec gzip -9 -k -n {} +

# ---------------------------------------------------------------- runtime
FROM debian:trixie-slim AS runtime

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 shs \
    && useradd --system --uid 10001 --gid shs --home-dir /app --shell /usr/sbin/nologin shs

WORKDIR /app
COPY --from=build /usr/local/bin/shs-server /usr/local/bin/shs-server
COPY --chown=root:root data/world/layout.json /app/data/world/layout.json
COPY --chown=root:root data/world/instances /app/data/world/instances
# The chat word filter (Config::blocked_words_path, relative to /app).
COPY --chown=root:root data/moderation/blocked_words.txt /app/data/moderation/blocked_words.txt
COPY --from=client --chown=root:root /public /app/public

ENV SHS_BIND=0.0.0.0:8080 \
    SHS_LAYOUT=/app/data/world/layout.json \
    SHS_INSTANCES=/app/data/world/instances \
    SHS_STATIC_DIR=/app/public \
    RUST_LOG=info

USER shs:shs
EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=3 \
    CMD ["shs-server", "healthcheck"]

ENTRYPOINT ["shs-server"]
