r"""Build an isolated preview from an autosave containing the Sluggies armature.

This never saves over or edits the input .blend. It duplicates one armature,
its directly parented meshes, and the requested action into a separate output
.blend so the original imported pose/action basis can be inspected intact.

Example (PowerShell):
  & 'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe' --background `
    'C:\Users\maxim\AppData\Local\Temp\Waluigi_Sluggers_2368_autosave.blend' `
    --python tools/blender/reconstruct_native_action_preview.py -- `
    --rig Armature.001 --action sequence_10.003 `
    --output 'C:\MultiGalaxy1-Assets\Working\SMG\Waluigi_NativeAction_Preview.blend'
"""
import argparse
import json
import os
import sys
import hashlib

import bpy
from mathutils import Vector


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--rig", required=True)
    parser.add_argument("--action", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--frame", type=float)
    return parser.parse_args(argv)


def main():
    args = parse_args()
    source = bpy.data.objects.get(args.rig)
    action = bpy.data.actions.get(args.action)
    if source is None or source.type != "ARMATURE":
        raise RuntimeError("Source armature not found: " + args.rig)
    if action is None:
        raise RuntimeError("Action not found: " + args.action)
    if not action.slots:
        raise RuntimeError("Action has no Blender 5.x slots: " + args.action)
    # Select only the slot shown in the saved rig's current binding where possible.
    existing_slot = source.animation_data.action_slot if source.animation_data else None
    slot = next((s for s in action.slots if existing_slot and
                 s.identifier == existing_slot.identifier), None)
    if slot is None:
        slot = next((s for s in action.slots if s.target_id_type == 'OBJECT'), None)
    if slot is None:
        raise RuntimeError("No OBJECT action slot for " + args.action)

    scene = bpy.context.scene
    scene.frame_set(int(args.frame if args.frame is not None else action.frame_range[0]),
                    subframe=(args.frame or action.frame_range[0]) % 1.0)
    source_blend_path = bpy.data.filepath
    meshes = [obj for obj in bpy.data.objects if obj.type == 'MESH' and obj.parent == source]

    # Make the duplicate self-contained at object and armature-data level.
    rig = source.copy()
    rig.data = source.data.copy()
    rig.name = "RecoveredNativePreview_Rig"
    rig.data.name = "RecoveredNativePreview_RigData"
    scene.collection.objects.link(rig)
    rig.matrix_world = source.matrix_world.copy()
    rig.animation_data_create()
    action_copy = action.copy()
    action_copy.name = "RecoveredNativePreview_" + action.name
    rig.animation_data.action = action_copy
    rig.animation_data.action_slot = next(
        (s for s in action_copy.slots if s.identifier == slot.identifier),
        action_copy.slots[0])

    duplicate_meshes = []
    for original in meshes:
        dup = original.copy()
        dup.data = original.data.copy()
        dup.name = "RecoveredNativePreview_" + original.name
        dup.data.name = "RecoveredNativePreview_" + original.data.name
        scene.collection.objects.link(dup)
        world = original.matrix_world.copy()
        dup.parent = rig
        dup.matrix_world = world
        for modifier in dup.modifiers:
            if modifier.type == 'ARMATURE' and modifier.object == source:
                modifier.object = rig
        duplicate_meshes.append(dup)

    # Keep the input's frame and all original datablocks intact; only evaluate the clone.
    scene.frame_set(int(args.frame if args.frame is not None else action.frame_range[0]),
                    subframe=((args.frame if args.frame is not None else action.frame_range[0]) % 1.0))
    bpy.context.view_layer.update()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    rig_eval = rig.evaluated_get(depsgraph)
    arm_bones = ("bone_22", "bone_23", "bone_24", "bone_25", "bone_26",
                 "bone_27", "bone_29", "bone_30")
    pose = {}
    for name in arm_bones:
        pb = rig_eval.pose.bones.get(name)
        if pb:
            pose[name] = [[float(pb.matrix[r][c]) for c in range(4)] for r in range(4)]

    bounds = []
    for obj in duplicate_meshes:
        evaluated = obj.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh()
        try:
            bounds.extend([list(evaluated.matrix_world @ v.co) for v in mesh.vertices])
        finally:
            evaluated.to_mesh_clear()
    if bounds:
        mins = [min(p[i] for p in bounds) for i in range(3)]
        maxs = [max(p[i] for p in bounds) for i in range(3)]
        center = [(mins[i] + maxs[i]) * .5 for i in range(3)]
        radius = max((Vector(p) - Vector(center)).length for p in bounds)
    else:
        mins = maxs = center = [0.0, 0.0, 0.0]
        radius = 1.0
    # Set an informative 3D viewport view if this is opened interactively.
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type == 'VIEW_3D':
                area.spaces.active.region_3d.view_location = Vector(center)
                area.spaces.active.region_3d.view_distance = max(radius * 2.4, 2.0)

    out_dir = os.path.dirname(os.path.abspath(args.output))
    os.makedirs(out_dir, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(args.output))
    result = {
        "source_blend": source_blend_path,
        "source_armature": args.rig,
        "source_rest_signature": hashlib.sha256(repr(tuple(
            (b.name, tuple(round(float(v), 7) for v in b.head_local),
             tuple(round(float(v), 7) for v in b.tail_local),
             b.parent.name if b.parent else None,
             tuple(tuple(round(float(v), 7) for v in row) for row in b.matrix_local))
            for b in sorted(source.data.bones, key=lambda item: item.name))).encode()).hexdigest(),
        "action": args.action,
        "action_slot": slot.identifier,
        "source_object_transform": {
            "matrix_world": [[float(source.matrix_world[r][c]) for c in range(4)] for r in range(4)],
            "scale": list(source.scale),
        },
        "preview_rig": rig.name,
        "preview_action": action_copy.name,
        "preview_meshes": [obj.name for obj in duplicate_meshes],
        "frame": float(args.frame if args.frame is not None else action.frame_range[0]),
        "world_bounds_min": mins,
        "world_bounds_max": maxs,
        "pose_matrices_armature_space": pose,
        "output_blend": os.path.abspath(args.output),
        "note": "Disposable duplicate only; input file and source datablocks were not saved over.",
    }
    report = os.path.splitext(os.path.abspath(args.output))[0] + ".json"
    with open(report, "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, allow_nan=False)
        handle.write("\n")
    print("MG1_NATIVE_PREVIEW=" + json.dumps({"output": args.output, "report": report,
          "rig": rig.name, "action": action_copy.name, "meshes": result["preview_meshes"]}))


if __name__ == "__main__":
    main()
