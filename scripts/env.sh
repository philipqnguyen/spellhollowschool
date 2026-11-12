# Source this file: `source scripts/env.sh`
# Puts the rustup-managed toolchain (with the wasm32 target) ahead of any other
# rust on PATH, e.g. Homebrew's. Homebrew's rustup keeps its proxies outside
# ~/.cargo/bin, so its directory goes first when it exists.
for d in "$HOME/.cargo/bin" /usr/local/opt/rustup/bin /opt/homebrew/opt/rustup/bin; do
  if [ -d "$d" ]; then PATH="$d:$PATH"; fi
done
export PATH
