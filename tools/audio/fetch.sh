#!/bin/bash
# Fetch the CC0 downloads tools/audio/convert.py reads (tools/audio/downloads.json),
# each into its own new folder .out/audio-downloads/<name>/ (gitignored), check
# its SHA-256 against the record, and unpack archives into <name>/x/. Nothing
# downloaded is ever run; only curl, unzip, bsdtar and ffmpeg read it.
#
#   tools/audio/fetch.sh            # every download not yet present
#   tools/audio/fetch.sh kenney_rpg # one
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../.." && pwd)
dest=${SHS_AUDIO_DOWNLOADS:-$repo/.out/audio-downloads}
py=${PYTHON:-python3}
list=$("$py" -I -c '
import json, sys
d = json.load(open(sys.argv[1]))["downloads"]
for name, r in d.items():
    if len(sys.argv) > 2 and name not in sys.argv[2:]:
        continue
    print("\t".join([name, r["url"], r["file"], r["sha256"]]))
' "$here/downloads.json" "$@")
while IFS=$'\t' read -r name url file sha; do
  dir="$dest/$name"
  if [ -e "$dir" ]; then echo "have $name"; continue; fi
  mkdir -p "$dir"
  curl -sS -f -L -m 600 -o "$dir/$file" "$url"
  got=$(shasum -a 256 "$dir/$file" | cut -d" " -f1)
  if [ "$got" != "$sha" ]; then echo "$name: checksum $got, expected $sha" >&2; exit 1; fi
  case "$file" in
    *.zip) mkdir -p "$dir/x"; unzip -q -o "$dir/$file" -d "$dir/x" ;;
    *.7z) mkdir -p "$dir/x"; bsdtar -xf "$dir/$file" -C "$dir/x" ;;
  esac
  echo "got $name"
done <<< "$list"
