/// <reference types="vitest/config" />
import { defineConfig } from "vite";

import { assetMapPlugin } from "./scripts/asset-map.mjs";
import { creditsPlugin } from "./scripts/credits.mjs";

// The game server (HTTP API and /ws) listens on 8080 in development; see docs/architecture.md.
// Parallel checkouts (several agents or worktrees at once) point at their own
// server and ports with SHS_SERVER, SHS_VITE_PORT and SHS_PREVIEW_PORT.
const server = process.env.SHS_SERVER || "http://localhost:8080";

function port(name: string, fallback: number): number {
  const raw = process.env[name];
  if (!raw) return fallback;
  const n = Number(raw);
  if (!Number.isInteger(n) || n <= 0 || n > 65535) throw new Error(`${name} must be a port number, not ${JSON.stringify(raw)}`);
  return n;
}

export default defineConfig({
  // The asset map (scripts/asset-map.mjs): every file under public/assets and
  // public/audio to a hash of its content, compiled into the bundle and written
  // as dist/asset-map.json (docs/deploy.md, "Assets from a bucket").
  // The credits (scripts/credits.mjs, from data/credits.json): compiled in as
  // `virtual:shs-credits` for the Credits panel, and the third-party licence
  // texts written as dist/THIRD-PARTY-NOTICES.txt.
  plugins: [assetMapPlugin(), creditsPlugin()],
  server: {
    port: port("SHS_VITE_PORT", 5173),
    strictPort: true,
    proxy: {
      "/api": server,
      "/ws": { target: server, ws: true },
    },
  },
  preview: {
    port: port("SHS_PREVIEW_PORT", 4173),
    proxy: {
      "/api": server,
      "/ws": { target: server, ws: true },
    },
  },
  build: {
    target: "es2022",
    // Vite's own content-hashed files (scripts, styles, fonts) go to dist/app/,
    // apart from the game assets in dist/assets/ (copied from public/): the
    // server caches app/ for a year, and an image whose assets come from a
    // bucket leaves assets/ out (docker/Dockerfile).
    assetsDir: "app",
    // Babylon lives in the lazily loaded game chunk, which is legitimately large;
    // the entry chunk (sign-in screens) is what has to stay small.
    chunkSizeWarningLimit: 2500,
  },
  test: {
    // The on-disk module cache kept a stale import.meta.glob of data/classes
    // after class files were added, failing the fx tests until cleared.
    fsModuleCache: false,
  },
});
