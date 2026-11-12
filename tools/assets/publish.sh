#!/usr/bin/env bash
# Builds client/public/assets with the pinned tools, packs it as the asset
# release for this commit's fingerprint, and uploads it as a GitHub Release
# (tag assets-<fingerprint>) that `bin/dev` and `npm run dev` download
# instead of building (tools/blender/README.md, "Asset releases").
#
#   npm run assets:publish                    clean build of HEAD in a temporary worktree, pack, upload
#   npm run assets:publish -- --reuse         pack this checkout's client/public/assets after an
#                                             incremental build (faster; the inputs must be committed;
#                                             it trusts the build's modification-time checks, so
#                                             publish main's releases with the default clean build)
#   npm run assets:publish -- --pack-only     stop after packing (no upload)
#   npm run assets:publish -- --out DIR       where to pack (default .out/asset-releases)
#   npm run assets:publish -- --allow-dirty   with --reuse: pack although inputs have uncommitted changes
#
# Needs the pinned Blender, MPFB and basisu (node client/scripts/assets.mjs tools).
# Uploading needs a git remote, HEAD pushed to it, and the GitHub CLI (gh)
# signed in; it goes to the GitHub repository prepare.mjs downloads from
# (package.json "repository", else the origin remote). Those checks, and
# whether the release is already published, run before the build. Without a
# remote the script stops after packing and says so: serve the packed folder
# (SHS_ASSETS_URL=<that folder>) to test the fetch.
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo"
out="$repo/.out/asset-releases"
reuse=0
pack_only=0
allow_dirty=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --reuse) reuse=1 ;;
    --pack-only) pack_only=1 ;;
    --allow-dirty) allow_dirty=1 ;;
    --out) shift; [[ $# -gt 0 ]] || { echo "publish: --out needs a folder" >&2; exit 2; }; out="$1" ;;
    -h|--help) awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"; exit 0 ;;
    *) echo "publish: unknown option $1 (see --help)" >&2; exit 2 ;;
  esac
  shift
done
mkdir -p "$out"
out="$(cd "$out" && pwd)"
say() { echo "[publish] $*"; }
die() { echo "[publish] ERROR: $*" >&2; exit 1; }

# 1. The pinned tools, all three: a release must be what the pins build.
if ! node client/scripts/assets.mjs tools --strict; then
  die "a release must be built with the pinned tools (above): install them (tools/blender/README.md, \"Pinned Blender and MPFB\")."
fi

# 2. The inputs must be committed, so the fingerprint names a commit others can check out.
roots=()
while IFS= read -r line; do roots+=("$line"); done < <(node --input-type=module -e \
  'import { fingerprintRoots } from "./client/scripts/asset-steps.mjs"; console.log(fingerprintRoots().join("\n"))')
dirty="$(git status --porcelain --untracked-files=all -- "${roots[@]}")"
if [[ -n "$dirty" ]]; then
  if [[ $reuse = 1 && $allow_dirty = 1 ]]; then
    say "warning: packing uncommitted inputs (--allow-dirty); such a release is never uploaded:"
    echo "$dirty"
    pack_only=1
  elif [[ $reuse = 1 ]]; then
    echo "$dirty"
    die "these build inputs have uncommitted changes; commit them (or --allow-dirty to pack anyway, for a local test)."
  else
    say "note: uncommitted input changes are not in the release (it is built from HEAD):"
    echo "$dirty"
  fi
fi

# 3. What is built: this checkout (--reuse) or HEAD in a temporary worktree, and its fingerprint.
if [[ $reuse = 1 ]]; then
  src="$repo"
else
  tmp="$(mktemp -d "${TMPDIR:-/tmp}/shs-publish.XXXXXX")"
  src="$tmp/src"
  cleanup() {
    git -C "$repo" worktree remove --force "$src" >/dev/null 2>&1 || true
    rm -rf "$tmp"
  }
  trap cleanup EXIT
  git worktree add --quiet --detach "$src" HEAD
fi
fp="$(node "$src/client/scripts/assets.mjs" fingerprint --repo "$src")"
[[ "$fp" =~ ^[0-9a-f]{16}$ ]] || die "could not compute the asset fingerprint of $src (got '${fp}')"
tag="assets-$fp"
head="$(git rev-parse HEAD)"

# 4. Before the long build: where the release goes, and whether it is needed.
gh_repo=""
if [[ $pack_only = 0 && -n "$(git remote)" ]]; then
  command -v gh >/dev/null || die "uploading needs the GitHub CLI (https://cli.github.com/): install it and run gh auth login (or --pack-only)."
  gh_repo="$(node client/scripts/assets.mjs release-repo)" || die "no GitHub repository to publish to: $gh_repo"
  if gh release view "$tag" --repo "$gh_repo" >/dev/null 2>&1; then
    say "$tag is already published on $gh_repo; nothing to build or upload."
    exit 0
  fi
  [[ -n "$(git branch -r --contains "$head" 2>/dev/null)" ]] || die "HEAD $head is not on the remote: push it first (the release tag points at it)."
fi

# 5. Build.
if [[ $reuse = 1 ]]; then
  say "incremental build in this checkout (SHS_ASSETS=build), fingerprint $fp"
  SHS_ASSETS=build node client/scripts/prepare.mjs
else
  say "clean build of HEAD $(git rev-parse --short HEAD) in a temporary worktree ($src), fingerprint $fp, about 12 minutes"
  # The worktree has no .blender-user: build with this checkout's (the pinned MPFB).
  # No wasm and no node_modules are needed for the Blender steps.
  BLENDER_USER_RESOURCES="${BLENDER_USER_RESOURCES:-$repo/.blender-user}" SHS_SKIP_WASM_BUILD=1 SHS_ASSETS=build \
    node "$src/client/scripts/prepare.mjs"
fi

# 6. Pack: the archive and its sha256, named for the fingerprint.
built="$(cat "$src/client/public/assets/assets-fingerprint.txt" 2>/dev/null || true)"
[[ "$built" = "$fp" ]] || die "client/public/assets was built for ${built:-no fingerprint}, not $fp"
say "packing fingerprint $fp"
node "$src/client/scripts/assets.mjs" pack --repo "$src" --assets "$src/client/public/assets" --out "$out" >/dev/null
archive="$out/$tag/$tag.tar.gz"
checksum="$archive.sha256"
say "packed $archive ($(du -h "$archive" | cut -f1 | tr -d ' ')), sha256 $(cut -c1-16 "$checksum")..."

# 7. Upload, only where there is somewhere to upload to.
if [[ $pack_only = 1 ]]; then
  say "--pack-only: not uploaded. Test the fetch with: SHS_ASSETS_URL=$out SHS_ASSETS=fetch node client/scripts/prepare.mjs"
  exit 0
fi
if [[ -z "$(git remote)" ]]; then
  say "not uploaded: this repository has no git remote, so there is no GitHub repository to publish $tag to."
  say "The release is packed in $out/$tag/. To use it locally:"
  say "    SHS_ASSETS_URL=$out SHS_ASSETS=fetch node client/scripts/prepare.mjs"
  say "Once the repository is on GitHub (and HEAD is pushed), run this script again to upload it."
  exit 0
fi
blender_pin="$(grep -v '^#' tools/blender/BLENDER_VERSION | head -n 1)"
gh release create "$tag" "$archive" "$checksum" \
  --repo "$gh_repo" \
  --target "$head" \
  --latest=false \
  --title "Assets $fp" \
  --notes "Prebuilt client/public/assets for asset fingerprint \`$fp\` (built from $head with Blender $blender_pin). Downloaded by bin/dev and npm run dev when a checkout's fingerprint matches; see tools/blender/README.md, \"Asset releases\"."
say "published $tag on $gh_repo"
