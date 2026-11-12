#!/usr/bin/env bash
# Everything CI runs. Usage: scripts/check.sh
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/env.sh
# No screenshots, renders, .blend files or build outputs in the tree (docs/repo-hygiene.md).
node scripts/check-committed-files.mjs --all
node --test scripts/check-committed-files.test.mjs
cargo fmt --all --check
cargo clippy --workspace --all-targets -- -D warnings
cargo test --workspace
cargo build -p sim-wasm --target wasm32-unknown-unknown --release
if [ -f client/package.json ]; then
  npm run --workspace client typecheck
  npm run --workspace client test
  npm run --workspace client build
fi
