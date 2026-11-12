"""Body-trial probe: enables MPFB headless and creates one adult human.

    BLENDER_USER_RESOURCES=$PWD/.blender-user nice -n 15 blender -b --factory-startup -t 2 \\
        --python tools/blender/characters/mpfb_smoke.py -- [--out /path/probe.blend]

--factory-startup ignores the user prefs, so the extension is enabled here by
module name; BLENDER_USER_RESOURCES tells Blender where the extension lives.
Only the CC0 base mesh and targets are used (no MakeHuman clothes/hair/skins).
"""

from __future__ import annotations

import sys

import addon_utils
import bpy

MODULE = "bl_ext.user_default.mpfb"


def main() -> None:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    out = argv[argv.index("--out") + 1] if "--out" in argv else ""
    addon_utils.enable(MODULE, default_set=True, persistent=True, handle_error=None)
    mod = sys.modules.get(MODULE)
    if mod is None:
        raise SystemExit(f"FAIL: {MODULE} did not load")
    from bl_ext.user_default.mpfb.services.humanservice import HumanService
    from bl_ext.user_default.mpfb.services.targetservice import TargetService

    macro = TargetService.get_default_macro_info_dict()
    macro.update({"gender": 1.0, "age": 0.5, "muscle": 0.5, "weight": 0.5})  # adult (25 y) male
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    human = HumanService.create_human(mask_helpers=True, detailed_helpers=False,
                                      extra_vertex_groups=True, feet_on_ground=True,
                                      scale=0.1, macro_detail_dict=macro)
    me = human.data
    zs = [v.co.z for v in me.vertices]
    keys = [k.name for k in me.shape_keys.key_blocks] if me.shape_keys else []
    print(f"MPFB_OK object={human.name} verts={len(me.vertices)} faces={len(me.polygons)} "
          f"height_m={max(zs) - min(zs):.3f} groups={len(human.vertex_groups)} shape_keys={keys[:6]}")
    if out:
        bpy.ops.wm.save_as_mainfile(filepath=out)
        print(f"saved {out}")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        import traceback

        traceback.print_exc()
        sys.exit(1)
