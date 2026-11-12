#!/usr/bin/env bash
# Rebuilds the Blender scenes the build can save under art/: generated files,
# gitignored and never committed (docs/art/repo-layout.md). The game never
# reads them; the character checks and hair tools open art/characters/<body>.blend.
#
#   tools/blender/build_art_blends.sh                  everything below, in this order
#   tools/blender/build_art_blends.sh world vfx        only some groups
#
#   world       art/world/castle_greybox.blend (greybox.py --blend),
#               art/world/furniture.blend (build_furniture.py),
#               art/world/dorm_house_{a,b,c,d}.blend (build_dorms.py),
#               art/world/<room cell>.blend for the class rooms and the duel
#               arena, e.g. class_charms.blend, duel_arena.blend (build_school.py)
#   characters  art/characters/body_a.blend, body_b.blend (build_characters.py,
#               the full shipped build, about 16 minutes; needs MPFB)
#   vfx         art/vfx/vfx.blend (build_vfx.py --blend; after the characters)
#
# Each script also re-exports its game assets into client/public/assets, as the
# client's prepare step does (which passes --no-blend / --blend-dir "" instead).
# Needs the pinned Blender (tools/blender/BLENDER_VERSION) as `blender` or
# $BLENDER, and for the characters the pinned MPFB (tools/blender/install_mpfb.sh).
# Run `node client/scripts/prepare.mjs` first
# so the castle textures and the characters' manifest exist.
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo"
blender="${BLENDER:-blender}"
if [[ -z "${BLENDER_USER_RESOURCES:-}" && -d "$repo/.blender-user" ]]; then
  export BLENDER_USER_RESOURCES="$repo/.blender-user"
fi
assets="client/public/assets"

groups=("$@")
[[ ${#groups[@]} -gt 0 ]] || groups=(world characters vfx)

run() {
  echo "== $*"
  # --python-exit-code: a Python error in the script fails this run (Blender exits 0 otherwise).
  "$blender" -b --factory-startup --python-exit-code 1 --python "$@"
}

for g in "${groups[@]}"; do
  case "$g" in
    world)
      if [[ ! -f "$assets/world/textures/textures.json" ]]; then
        run tools/blender/castle_kit/textures.py -- --out "$assets/world/textures"
      fi
      run tools/blender/greybox.py -- --layout data/world/layout.json --out "$assets/world" \
        --blend art/world/castle_greybox.blend
      run tools/blender/world/build_furniture.py -- --blend art/world/furniture.blend
      run tools/blender/world/build_dorms.py -- --blend-dir art/world
      run tools/blender/world/build_school.py -- --blend-dir art/world
      ;;
    characters)
      run tools/blender/characters/build_characters.py -- --assets "$assets" --art art
      ;;
    vfx)
      run tools/blender/vfx/build_vfx.py -- --assets "$assets" --blend art/vfx/vfx.blend
      ;;
    *)
      echo "build_art_blends: unknown group '$g' (world, characters, vfx)" >&2
      exit 2
      ;;
  esac
done
echo "build_art_blends: done (the .blend files are gitignored; git status stays clean)"
