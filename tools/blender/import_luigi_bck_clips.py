"""Import selected raw Galaxy Luigi BCK clips using installed J3DBlend semantics.

The importer runs on a temporary copy of the BDL/BCK files because J3DBlend
discovers all sibling BCK files. Only selected clips are staged. Source assets
are read-only; --output is a disposable intermediate .blend.
"""
import argparse
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import bpy


def cli():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--bdl", required=True)
    p.add_argument("--bck-dir", required=True)
    p.add_argument("--clips", nargs="+", required=True,
                   help="BCK filenames, with or without .bck")
    p.add_argument("--addon-dir", default=os.path.expandvars(
        r"%APPDATA%\Blender Foundation\Blender\5.2\scripts\addons\J3DBlend-master"))
    p.add_argument("--output", required=True)
    p.add_argument("--manifest", required=True)
    return p.parse_args(argv)


def load_addon(addon_dir):
    name = "mg1_j3dblender"
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(addon_dir, "__init__.py"),
        submodule_search_locations=[addon_dir])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    module.register()
    # Installed J3DBlend 5.2 build omits this declared class property from
    # RNA registration, while ALL_PARAMS still reads it during execute().
    module.ImportBmd.ic_sc = True
    return module


def main():
    args = cli()
    bdl = Path(args.bdl).resolve()
    bck_dir = Path(args.bck_dir).resolve()
    selected = []
    for value in args.clips:
        name = value if value.lower().endswith(".bck") else value + ".bck"
        path = bck_dir / name
        if not path.is_file():
            raise FileNotFoundError(path)
        selected.append(path)
    module = load_addon(args.addon_dir)
    objects_before = set(bpy.data.objects)
    actions_before = set(bpy.data.actions)
    with tempfile.TemporaryDirectory(prefix="mg1-luigi-bck-stage-") as temp:
        stage = Path(temp)
        model_dir, anim_dir = stage / "bmd", stage / "bck"
        model_dir.mkdir(); anim_dir.mkdir()
        staged_bdl = model_dir / bdl.name
        shutil.copy2(bdl, staged_bdl)
        for path in selected:
            shutil.copy2(path, anim_dir / path.name)
        result = bpy.ops.import_mesh.bmd(
            filepath=str(staged_bdl), files=[{"name": staged_bdl.name}],
            import_anims=True, import_anims_type="SEPARATE",
            anim_rot_smallest=False, use_nodes=False, frc_cr_bn=False,
            nat_bn=False, no_rot_cv=False, val_msh=False, tx_pck="DONT",
            imtype="TGA", boneThickness=10, dvg=False, paranoia=False)
    if result != {"FINISHED"}:
        raise RuntimeError("J3DBlend import failed: " + repr(result))
    armatures = [obj for obj in bpy.data.objects
                 if obj not in objects_before and obj.type == "ARMATURE"]
    actions = [action for action in bpy.data.actions if action not in actions_before]
    manifest = {
        "schema": "mg1.imported-galaxy-bck-clips.v1",
        "source_bdl": str(bdl),
        "source_clips": [str(path) for path in selected],
        "addon_directory": args.addon_dir,
        "importer": "J3DBlend installed Blender 5.2 add-on",
        "import_result": sorted(result),
        "armatures": [{"name": obj.name, "bone_count": len(obj.data.bones),
                       "bone_order": [bone.name for bone in obj.data.bones]}
                      for obj in armatures],
        "actions": [{"name": action.name,
                     "frame_range": [float(x) for x in action.frame_range],
                     "loop_type": getattr(action, "bck_loop_type", None),
                     "slots": [slot.identifier for slot in action.slots]}
                    for action in actions],
        "source_assets_modified": False,
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(args.output))
    os.makedirs(os.path.dirname(os.path.abspath(args.manifest)), exist_ok=True)
    with open(args.manifest, "w", encoding="utf-8") as stream:
        json.dump(manifest, stream, indent=2)
    print("IMPORTED_BCK_CLIPS", json.dumps({"armatures": manifest["armatures"],
        "actions": manifest["actions"]}, sort_keys=True))


if __name__ == "__main__":
    main()
