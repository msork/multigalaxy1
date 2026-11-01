"""Reopen and validate a saved native-scaffold preview."""
import argparse
import json
import math
import os
import sys

import bpy
from mathutils import Matrix


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
    fist_reference_raw = rig.get("mg1_closed_fist_basis", "")
    if not fist_reference_raw:
        raise RuntimeError("preview does not contain a fixed closed-fist reference pose")
    fist_reference = json.loads(fist_reference_raw)
    meshes = [obj for obj in bpy.data.objects if obj.type == "MESH" and
              any(mod.type == "ARMATURE" and mod.object == rig
                  for mod in obj.modifiers)]
    if not meshes:
        raise RuntimeError("no preview meshes are bound to the preview rig")
    visible_armatures = [obj for obj in bpy.context.scene.objects
        if obj.type == "ARMATURE" and obj.visible_get(view_layer=bpy.context.view_layer)]
    visible_meshes = [obj for obj in bpy.context.scene.objects
        if obj.type == "MESH" and obj.visible_get(view_layer=bpy.context.view_layer)]
    bound_mesh_names = sorted(obj.name for obj in meshes)
    visible_armature_names = sorted(obj.name for obj in visible_armatures)
    visible_mesh_names = sorted(obj.name for obj in visible_meshes)
    failures = []
    if visible_armature_names != [rig.name]:
        failures.append({"problem": "visible character armatures are not exactly the preview rig",
                         "visible_armatures": visible_armature_names})
    if visible_mesh_names != bound_mesh_names:
        failures.append({"problem": "visible character meshes are not exactly the two bound preview meshes",
                         "visible_meshes": visible_mesh_names,
                         "expected_meshes": bound_mesh_names})
    scales = []
    fist_errors = []
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
        for name, expected_rows in fist_reference.items():
            actual = evaluated_rig.pose.bones[name].matrix_basis
            expected = Matrix(expected_rows)
            fist_errors.append(max(abs(float(actual[r][c] - expected[r][c]))
                                   for r in range(4) for c in range(4)))
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
        "visible_character_body_count": int(
            visible_armature_names == [rig.name] and visible_mesh_names == bound_mesh_names),
        "visible_armatures": visible_armature_names,
        "visible_meshes": visible_mesh_names,
        "hidden_reference_object_count": sum(
            not obj.visible_get(view_layer=bpy.context.view_layer)
            for obj in bpy.context.scene.objects),
        "vertex_counts": vertex_counts,
        "runtime_constraints": sum(len(pb.constraints) for pb in rig.pose.bones),
        "object_scale": [float(v) for v in rig.scale],
        "max_pose_scale_delta_from_one": max(scales, default=0.0),
        "locked_finger_bone_count": len(fist_reference),
        "max_closed_fist_basis_error": max(fist_errors, default=0.0),
        "frames_checked": list(range(61)),
        "failure_count": len(failures),
        "failures": failures[:50],
        "passed": not failures and sum(len(pb.constraints) for pb in rig.pose.bones) == 0 and
                  len(fist_reference) > 0 and max(fist_errors, default=math.inf) < 1e-6,
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
    print(json.dumps(report, indent=2))
    if failures:
        raise RuntimeError("reopened scaffold preview validation failed")


if __name__ == "__main__":
    main()
