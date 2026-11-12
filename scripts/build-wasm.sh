#!/usr/bin/env bash
# Builds crates/sim-wasm for the browser and writes the bindings to client/src/wasm/:
#   sim_wasm.js        ES module (wasm-bindgen --target web; `await init()` first)
#   sim_wasm_bg.wasm   the module itself
#   sim_wasm.d.ts      TypeScript declarations, including the ServerMessage types
# Installs the wasm-bindgen CLI matching the wasm-bindgen crate in Cargo.lock if
# it is missing or a different version (the two must match exactly).
#
# Usage: scripts/build-wasm.sh [--test]
#   --test  also runs crates/sim-wasm/tests under Node via wasm-bindgen-test-runner
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/env.sh

run_tests=0
for arg in "$@"; do
  case "$arg" in
    --test) run_tests=1 ;;
    *) echo "usage: $0 [--test]" >&2; exit 2 ;;
  esac
done

target=wasm32-unknown-unknown
out_dir=client/src/wasm
target_dir="${CARGO_TARGET_DIR:-target}"

# The locked wasm-bindgen version: the line after `name = "wasm-bindgen"`.
version=$(awk '$0 == "name = \"wasm-bindgen\"" { getline; gsub(/version = |"/, ""); print; exit }' Cargo.lock)
if [ -z "$version" ]; then
  echo "error: wasm-bindgen is not in Cargo.lock (run cargo build -p sim-wasm once)" >&2
  exit 1
fi

installed=$(wasm-bindgen --version 2>/dev/null | awk '{ print $2 }' || true)
if [ "$installed" != "$version" ]; then
  echo "Installing wasm-bindgen-cli $version (found: ${installed:-none})"
  cargo install wasm-bindgen-cli --version "$version" --locked
fi

cargo build -p sim-wasm --target "$target" --release
mkdir -p "$out_dir"
wasm-bindgen --target web --out-dir "$out_dir" \
  "$target_dir/$target/release/sim_wasm.wasm"
echo "Wrote $(ls "$out_dir" | tr '\n' ' ')to $out_dir/ ($(wc -c < "$out_dir/sim_wasm_bg.wasm" | tr -d ' ') bytes of wasm)"

if [ "$run_tests" = 1 ]; then
  CARGO_TARGET_WASM32_UNKNOWN_UNKNOWN_RUNNER=wasm-bindgen-test-runner \
    cargo test -p sim-wasm --target "$target"
fi
