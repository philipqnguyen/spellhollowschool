#!/usr/bin/env bash
# Fails unless the asset release for this checkout's fingerprint is published
# (tools/blender/README.md, "Asset releases"). CI runs it on every push and
# pull request: a commit that changes what the Blender build reads changes the
# fingerprint, and until `npm run assets:publish` uploads that release,
# checkouts without Blender get stale assets or greybox stand-ins.
#
#   bash tools/assets/check-release.sh
#
# Needs Node and the GitHub CLI signed in (in CI: GH_TOKEN). Nothing is built
# or downloaded: it computes the fingerprint and asks GitHub for the release.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/../.."
say() { echo "[asset-release] $*"; }
fail() {
  [[ -n "${GITHUB_ACTIONS:-}" ]] && echo "::error title=Asset release missing::$1"
  echo "[asset-release] ERROR: $1" >&2
  shift
  for line in "$@"; do echo "[asset-release]   $line" >&2; done
  exit 1
}

fp="$(node client/scripts/assets.mjs fingerprint)"
[[ "$fp" =~ ^[0-9a-f]{16}$ ]] || fail "could not compute the asset fingerprint (got '${fp}')"
gh_repo="$(node client/scripts/assets.mjs release-repo)" || fail "no GitHub repository for asset releases: $gh_repo"
command -v gh >/dev/null || fail "the GitHub CLI (gh) is needed to look up the release"
tag="assets-$fp"

if ! names="$(gh release view "$tag" --repo "$gh_repo" --json assets --jq '.assets[].name' 2>&1)"; then
  if [[ "$names" == *"release not found"* ]]; then
    fail "no asset release $tag on $gh_repo for this commit's build inputs." \
      "The Blender build inputs (the sources under art/ and tools/blender/, the data they read, the pins," \
      "client/scripts/asset-steps.mjs) changed since the last published release. A maintainer with the" \
      "pinned tools runs this on this commit (a pushed pull request branch works too: the release is keyed" \
      "by the inputs' content, so it still matches after the merge), then re-runs this job:" \
      "    npm run assets:publish" \
      "node client/scripts/assets.mjs fingerprint --list shows every input and its hash."
  fi
  fail "could not look up $tag on $gh_repo: $names"
fi
for want in "$tag.tar.gz" "$tag.tar.gz.sha256"; do
  grep -qxF "$want" <<<"$names" || fail "$tag on $gh_repo has no $want (a partial upload?): delete the release and run npm run assets:publish again."
done
say "$tag is published on $gh_repo"
