"""Reopen and validate a saved native-scaffold preview."""
import argparse
import json
import math
import os
import sys

import bpy


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--rig", default="ScaffoldWalk_Waluigi_Rig")
    parser.add_argument("--action", default="Waluigi_LuigiWalk_NativeScaffold_v1")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    rig = bpy.data.objects.get(args.rig)
    if rig is None or rig.type != "ARMATURE":
        raise RuntimeError("saved scaffold preview rig is missing")
    if rig.animation_data is None or rig.animation_data.action is None:
        raise RuntimeError("saved scaffold preview action is not assigned")
    if rig.animation_data.action.name != args.action:
        raise RuntimeError("saved scaffold action name differs")
    meshes = [obj for obj in bpy.data.objects if obj.type == "MESH" and
              any(mod.type == "ARMATURE" and mod.object == rig
                  for mod in obj.modifiers)]
    if not meshes:
        raise RuntimeError("no preview meshes are bound to the preview rig")
    failures = []
    scales = []
    vertex_counts = {obj.name: len(obj.data.vertices) for obj in meshes}
    for frame in range(61):
        bpy.context.scene.frame_set(frame)
        bpy.context.view_layer.update()
        depsgraph = bpy.context.evaluated_depsgraph_get()
        evaluated_rig = rig.evaluated_get(depsgraph)
        for pose_bone in evaluated_rig.pose.bones:
            if any(not math.isfinite(float(v)) for row in pose_bone.matrix for v in row):
                failures.append({"frame": frame, "bone": pose_bone.name,
                                 "problem": "non-finite matrix"})
            scales.append(max(abs(float(v) - 1.0) for v in pose_bone.scale))
        for obj in meshes:
            if obj.hide_get() or obj.hide_viewport or obj.hide_render:
                failures.append({"frame": frame, "mesh": obj.name,
                                 "problem": "hidden"})
            evaluated = obj.evaluated_get(depsgraph)
            mesh = evaluated.to_mesh()
            try:
                if len(mesh.vertices) != vertex_counts[obj.name]:
                    failures.append({"frame": frame, "mesh": obj.name,
                                     "problem": "vertex-count changed"})
                for index, vertex in enumerate(mesh.vertices):
                    point = evaluated.matrix_world @ vertex.co
                    if any(not math.isfinite(float(v)) for v in point):
                        failures.append({"frame": frame, "mesh": obj.name,
                                         "vertex": index, "problem": "non-finite"})
                        break
            finally:
                evaluated.to_mesh_clear()
    report = {
        "schema": "mg1.native-scaffold-preview-reopen-validation.v1",
        "reopened_file": bpy.data.filepath,
        "rig": rig.name,
        "action": rig.animation_data.action.name,
        "mesh_objects": [obj.name for obj in meshes],
        "vertex_counts": vertex_counts,
        "runtime_constraints": sum(len(pb.constraints) for pb in rig.pose.bones),
        "object_scale": [float(v) for v in rig.scale],
        "max_pose_scale_delta_from_one": max(scales, default=0.0),
        "frames_checked": list(range(61)),
        "failure_count": len(failures),
        "failures": failures[:50],
        "passed": not failures and sum(len(pb.constraints) for pb in rig.pose.bones) == 0,
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
    print(json.dumps(report, indent=2))
    if failures:
        raise RuntimeError("reopened scaffold preview validation failed")


if __name__ == "__main__":
    main()
