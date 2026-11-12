#!/usr/bin/env bash
# Installs the pinned MPFB (MakeHuman Plugin For Blender) into the Blender user
# resources the character build runs with: .blender-user at the repository root
# (gitignored), or $BLENDER_USER_RESOURCES when it is set.
#
#   tools/blender/install_mpfb.sh              download the pinned zip (network) and install it
#   tools/blender/install_mpfb.sh --zip FILE   install from a zip you already have (no network)
#   tools/blender/install_mpfb.sh --check      only report whether the pinned version is installed
#
# The version, the zip's sha256 and its URL are in tools/blender/MPFB_VERSION;
# the zip is checked against the sha256 before anything is installed. Needs the
# pinned Blender (tools/blender/BLENDER_VERSION) as `blender` or $BLENDER.
# MPFB itself is GPL and is never committed; only its CC0 assets are used by the
# build (art/CREDITS-makehuman.md). Re-running is safe: an installed pinned
# version is left alone, another version is replaced.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
blender="${BLENDER:-blender}"
resources="${BLENDER_USER_RESOURCES:-$repo/.blender-user}"

pin() { sed -n "s/^$1[[:space:]]*=[[:space:]]*//p" "$here/MPFB_VERSION" | head -n 1; }
version="$(pin version)"
sha256="$(pin sha256)"
url="$(pin url)"
[[ -n "$version" && -n "$sha256" && -n "$url" ]] || { echo "install_mpfb: $here/MPFB_VERSION needs version, sha256 and url" >&2; exit 1; }

zip=""
check_only=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --zip) zip="${2:?--zip needs a file}"; shift 2 ;;
    --check) check_only=1; shift ;;
    -h|--help) sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "install_mpfb: unknown option $1 (see --help)" >&2; exit 2 ;;
  esac
done

manifest="$resources/extensions/user_default/mpfb/blender_manifest.toml"
installed() { [[ -f "$manifest" ]] && sed -n 's/^version[[:space:]]*=[[:space:]]*"\{0,1\}\([^"]*\)"\{0,1\}.*/\1/p' "$manifest" | head -n 1; }

have="$(installed || true)"
if [[ "$have" == "$version" ]]; then
  echo "install_mpfb: MPFB $version is installed in $resources"
  exit 0
fi
if [[ $check_only == 1 ]]; then
  echo "install_mpfb: MPFB $version is not installed in $resources (found: ${have:-nothing}); run tools/blender/install_mpfb.sh" >&2
  exit 1
fi

# The pinned Blender (major.minor) installs it, the same one the build runs.
want_blender="$(grep -v '^[[:space:]]*#' "$here/BLENDER_VERSION" | tr -d '[:space:]')"
if ! have_blender="$("$blender" --version 2>/dev/null | sed -n 's/^Blender \([0-9]*\.[0-9]*\).*/\1/p' | head -n 1)" || [[ -z "$have_blender" ]]; then
  echo "install_mpfb: Blender not found ($blender). Install Blender $want_blender and put it on PATH, or set BLENDER=/path/to/blender." >&2
  exit 1
fi
if [[ "$have_blender" != "$want_blender" ]]; then
  echo "install_mpfb: this repository is pinned to Blender $want_blender (tools/blender/BLENDER_VERSION); $blender is $have_blender." >&2
  echo "              Install Blender $want_blender (https://download.blender.org/release/Blender$want_blender/) or set BLENDER=/path/to/blender." >&2
  exit 1
fi

tmp="$(mktemp -d "${TMPDIR:-/tmp}/mpfb.XXXXXX")"
trap 'rm -rf "$tmp"' EXIT
if [[ -z "$zip" ]]; then
  zip="$tmp/add-on-mpfb-v$version.zip"
  echo "install_mpfb: downloading MPFB $version ($url)"
  curl -fL --retry 3 --progress-bar -o "$zip" "$url"
fi
[[ -f "$zip" ]] || { echo "install_mpfb: no such file: $zip" >&2; exit 1; }

if command -v shasum >/dev/null 2>&1; then
  got="$(shasum -a 256 "$zip" | cut -d' ' -f1)"
else
  got="$(sha256sum "$zip" | cut -d' ' -f1)"
fi
if [[ "$got" != "$sha256" ]]; then
  echo "install_mpfb: $zip has sha256 $got, expected $sha256 (MPFB $version, tools/blender/MPFB_VERSION)" >&2
  exit 1
fi

mkdir -p "$resources"
if [[ -n "$have" ]]; then
  echo "install_mpfb: replacing MPFB $have with $version"
  BLENDER_USER_RESOURCES="$resources" "$blender" -b --factory-startup --command extension remove user_default.mpfb >/dev/null
fi
echo "install_mpfb: installing MPFB $version into $resources"
BLENDER_USER_RESOURCES="$resources" "$blender" -b --command extension install-file -r user_default -e "$zip" >"$tmp/install.log" 2>&1 || {
  cat "$tmp/install.log" >&2
  exit 1
}

have="$(installed || true)"
if [[ "$have" != "$version" ]]; then
  cat "$tmp/install.log" >&2
  echo "install_mpfb: after installing, $manifest reports '${have:-nothing}', not $version" >&2
  exit 1
fi
echo "install_mpfb: MPFB $version installed. Run the character build with BLENDER_USER_RESOURCES=$resources"
echo "              (client/scripts/prepare.mjs sets it when .blender-user exists at the repository root)."
