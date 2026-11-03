"""Read-only inventory of a Super Mario Galaxy Luigi BCK animation directory.

Runs inside Blender so the installed J3DBlend BCK parser can be reused. It
never imports meshes, changes source files, or writes outside --output.
"""
import argparse
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

import bpy


def cli():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--bck-dir", required=True)
    p.add_argument("--source-rig", default="luigi.001_armature")
    p.add_argument("--profile", default=os.path.join(os.path.dirname(__file__),
                                                       "profiles", "luigi_to_waluigi.json"))
    p.add_argument("--addon-dir", default=os.path.expandvars(
        r"%APPDATA%\Blender Foundation\Blender\5.2\scripts\addons\J3DBlend-master"))
    p.add_argument("--output", required=True)
    return p.parse_args(argv)


def load_bck_module(addon_dir):
    parent = os.path.dirname(addon_dir)
    package_name = "mg1_j3dblender"
    spec = importlib.util.spec_from_file_location(
        package_name, os.path.join(addon_dir, "__init__.py"),
        submodule_search_locations=[addon_dir])
    package = importlib.util.module_from_spec(spec)
    sys.modules[package_name] = package
    spec.loader.exec_module(package)
    return importlib.import_module(package_name + ".Bck")


def family(name):
    stem = Path(name).stem.lower()
    rules = [
        ("walk", r"walk|step|stroll"),
        ("run", r"run|dash|sprint"),
        ("jump", r"jump|hop|bound"),
        ("swim", r"swim|dive|float|breast|flutter"),
        ("fall_or_land", r"fall|land|drop"),
        ("wait_or_idle", r"wait|idle|stand|look"),
        ("hurt_or_reaction", r"hurt|damage|hit|react|knock"),
        ("attack_or_action", r"attack|kick|punch|throw|grab|climb"),
    ]
    for label, pattern in rules:
        if re.search(pattern, stem):
            return label
    return "other"


def component_active(keys):
    return len(keys) > 1


def main():
    args = cli()
    root = Path(args.bck_dir).resolve()
    source = bpy.data.objects.get(args.source_rig)
    if source is None or source.type != "ARMATURE":
        raise RuntimeError("source rig not found: " + args.source_rig)
    bone_names = [bone.name for bone in source.data.bones]
    with open(args.profile, "r", encoding="utf-8") as stream:
        profile = json.load(stream)
    mapped = set(profile["source_to_target_bones"])
    ignored = set(profile.get("explicitly_ignored_source_bones", []))
    Bck = load_bck_module(args.addon_dir)
    records = []
    for path in sorted(root.glob("*.bck"), key=lambda p: p.name.lower()):
        parser = Bck.Bck_in()
        record = {"file": path.name, "family": family(path.name),
                  "loop_type": None, "length_frames": None,
                  "joint_count": 0, "animated_bones": [],
                  "animated_channels": {}, "compatible": False,
                  "error": None}
        try:
            parser.LoadBck(str(path), len(bone_names))
            record["loop_type"] = parser.loopType.name
            record["length_frames"] = int(parser.animationLength)
            record["joint_count"] = len(parser.anims)
            record["compatible"] = len(parser.anims) == len(bone_names)
            animated = []
            channels = {}
            for index, joint in enumerate(parser.anims):
                if index >= len(bone_names):
                    continue
                active = []
                for name in ("rotation", "translation", "scale"):
                    for axis in "XYZ":
                        keys = getattr(joint, name + "s" + axis, None)
                        if keys is not None and component_active(keys):
                            active.append(name + "_" + axis.lower())
                if active:
                    bone = bone_names[index]
                    animated.append(bone)
                    channels[bone] = active
            record["animated_bones"] = animated
            record["animated_channels"] = channels
            record["unmapped_animated_bones"] = sorted(set(animated) - mapped - ignored)
            record["explicitly_ignored_animated_bones"] = sorted(set(animated) & ignored)
        except Exception as exc:
            record["error"] = f"{type(exc).__name__}: {exc}"
        records.append(record)
    counts = {}
    for item in records:
        counts[item["family"]] = counts.get(item["family"], 0) + 1
    report = {
        "schema": "mg1.galaxy-bck-bank-inventory.v1",
        "source_rig": source.name,
        "source_bone_count": len(bone_names),
        "source_bone_order": bone_names,
        "bck_directory": str(root),
        "clip_count": len(records),
        "compatible_count": sum(bool(item["compatible"]) for item in records),
        "category_counts_by_filename": counts,
        "animated_bone_coverage": {
            "profile_mapped_source_bones": sorted(mapped),
            "explicitly_ignored_source_bones": sorted(ignored),
            "source_bones_never_animated": sorted(set(bone_names) -
                set(b for item in records for b in item["animated_bones"])),
            "source_bones_unmapped_anywhere": sorted(set(
                b for item in records for b in item["unmapped_animated_bones"])),
        },
        "clips": records,
        "notes": [
            "Categories are filename heuristics; the source BCK metadata has no semantic category field.",
            "BCK animation joint index is interpreted against the Luigi BDL/source armature bone order.",
            "An unmapped-bone list is completed by the converter against the selected character profile."
        ]
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
    print("BCK_BANK_INVENTORY", json.dumps({k: report[k] for k in (
        "clip_count", "compatible_count", "category_counts_by_filename")}, sort_keys=True))


if __name__ == "__main__":
    main()
