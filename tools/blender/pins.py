"""The Blender and MPFB versions the asset build is pinned to, and their checks.

The pins live in two files next to this one, so client/scripts/prepare.mjs and
tools/blender/install_mpfb.sh read the same values:

* `BLENDER_VERSION`: the Blender major.minor (for example `5.2`). Every build
  entry script calls `require_blender()` first and exits non-zero on any other
  major.minor: a different Blender writes different geometry, normals and glTF
  files, so outputs would not match what the repository's checks expect.
* `MPFB_VERSION`: the MPFB extension (MakeHuman Plugin For Blender, CC0 assets)
  the character build reads its base mesh, targets and rig from (`version`,
  plus the `sha256` and `url` of its zip for install_mpfb.sh).
  `require_mpfb()` exits non-zero when it is missing from the Blender user
  resources or is another version; `mpfb_problem()` returns the same message
  without exiting (charlib/mh_source.py raises it).

Pure Python at import time (no `bpy`), so plain-Python tests can import it.
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
BLENDER_PIN_FILE = os.path.join(HERE, "BLENDER_VERSION")
MPFB_PIN_FILE = os.path.join(HERE, "MPFB_VERSION")
MPFB_ID = "mpfb"


def blender_pin() -> str:
    """The pinned Blender major.minor, e.g. "5.2"."""
    with open(BLENDER_PIN_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            if line:
                return line
    raise RuntimeError(f"{BLENDER_PIN_FILE} names no version")


def mpfb_pin() -> dict:
    """The MPFB pin: {"version", "sha256", "url"}."""
    out = {}
    with open(MPFB_PIN_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            if "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    if "version" not in out:
        raise RuntimeError(f"{MPFB_PIN_FILE} names no version")
    return out


def blender_install_help(want: str) -> str:
    return (f"  Install Blender {want} (https://www.blender.org/download/, every {want}.x release is at\n"
            f"  https://download.blender.org/release/Blender{want}/), then run it as `blender` or point\n"
            f"  client/scripts/prepare.mjs at it with BLENDER=/path/to/blender. The pin is\n"
            f"  tools/blender/BLENDER_VERSION; change it only when the whole pipeline moves to a new Blender.")


def require_blender() -> None:
    """Exit with a clear message unless this Blender is the pinned major.minor.
    A no-op outside Blender (plain-Python tests)."""
    try:
        import bpy
    except ImportError:
        return
    want = blender_pin()
    have = "%d.%d" % tuple(bpy.app.version[:2])
    if have == want:
        return
    sys.stderr.write(f"\n[pins] ERROR: this build needs Blender {want}; this is Blender {bpy.app.version_string}.\n"
                     f"{blender_install_help(want)}\n\n")
    sys.stderr.flush()
    sys.exit(1)


def user_resources() -> str:
    """The Blender user resources folder the build runs with: BLENDER_USER_RESOURCES,
    else (inside Blender) Blender's own user folder."""
    env = os.environ.get("BLENDER_USER_RESOURCES")
    if env:
        return os.path.abspath(env)
    try:
        import bpy

        return os.path.dirname(bpy.utils.user_resource("EXTENSIONS"))
    except ImportError:
        return os.path.join(REPO, ".blender-user")


def installed_mpfb_version(resources: str | None = None) -> str | None:
    """The MPFB version installed in `resources` (extensions/user_default/mpfb), or None."""
    root = resources or user_resources()
    manifest = os.path.join(root, "extensions", "user_default", MPFB_ID, "blender_manifest.toml")
    try:
        with open(manifest, encoding="utf-8") as f:
            for line in f:
                k, _, v = line.partition("=")
                if k.strip() == "version":
                    return v.strip().strip('"')
    except OSError:
        return None
    return None


def mpfb_install_help(want: str) -> str:
    return ("  Install it (downloads the pinned zip from extensions.blender.org, about 46 MB, and checks its sha256):\n"
            "      tools/blender/install_mpfb.sh\n"
            "  or offline, from a zip you already have:\n"
            f"      tools/blender/install_mpfb.sh --zip add-on-mpfb-v{want}.zip\n"
            "  It installs into .blender-user at the repository root (or $BLENDER_USER_RESOURCES);\n"
            "  run Blender with BLENDER_USER_RESOURCES=$PWD/.blender-user (prepare.mjs sets it).\n"
            "  The pin is tools/blender/MPFB_VERSION.")


def mpfb_problem(resources: str | None = None) -> str | None:
    """Why the character build cannot use MPFB here (a message with install steps), or None."""
    root = resources or user_resources()
    want = mpfb_pin()["version"]
    have = installed_mpfb_version(root)
    if have == want:
        return None
    what = f"MPFB {have} is installed" if have else "MPFB is not installed"
    return (f"the character build needs MPFB {want}; {what} in the Blender user resources {root}.\n"
            f"{mpfb_install_help(want)}")


def require_mpfb(resources: str | None = None) -> None:
    """Exit with a clear message unless the pinned MPFB is installed."""
    problem = mpfb_problem(resources)
    if problem is None:
        return
    sys.stderr.write(f"\n[pins] ERROR: {problem}\n\n")
    sys.stderr.flush()
    sys.exit(1)


if __name__ == "__main__":
    # `blender -b --factory-startup --python tools/blender/pins.py`: check both pins.
    require_blender()
    require_mpfb()
    print(f"[pins] Blender {blender_pin()} and MPFB {mpfb_pin()['version']} OK ({user_resources()})")
