"""Reopen validation for a Galaxy-skeleton/Sluggers-mesh bind preview."""
import argparse
import json
import math
import os
import sys

import bpy


def cli():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--rig", default="GalaxySkinBind_Luigi_Rig")
    p.add_argument("--output", required=True)
    p.add_argument("--step", type=float, default=0.25)
    return p.parse_args(argv)


def finite_vector(v):
    return all(math.isfinite(float(x)) for x in v)


def main():
    args = cli()
    scene = bpy.context.scene
    rig = bpy.data.objects.get(args.rig)
    if rig is None or rig.type != 'ARMATURE':
        raise RuntimeError(f"candidate armature missing: {args.rig}")
    action = rig.animation_data.action if rig.animation_data else None
    slot = rig.animation_data.action_slot if rig.animation_data else None
    meshes = [obj for obj in scene.objects if obj.type == 'MESH' and
              any(m.type == 'ARMATURE' and m.object == rig for m in obj.modifiers)]
    visible_meshes = [obj for obj in meshes if not obj.hide_viewport and not obj.hide_render]
    visible_armatures = [obj.name for obj in scene.objects if obj.type == 'ARMATURE' and
                         not obj.hide_viewport and not obj.hide_render]
    if action is None or slot is None:
        raise RuntimeError("candidate has no assigned action/slot")
    curves = [curve for layer in action.layers for strip in layer.strips
              for bag in strip.channelbags if bag.slot == slot for curve in bag.fcurves]
    first, last = map(float, action.frame_range)
    frames, frame = [], first
    while frame <= last + 1e-6:
        frames.append(round(frame, 6))
        frame += args.step
    if not frames or frames[-1] < last - 1e-6:
        frames.append(last)
    nonfinite_samples, sample_vertices = [], 0
    for value in frames:
        base = math.floor(value)
        scene.frame_set(base, subframe=value-base)
        bpy.context.view_layer.update()
        for bone in rig.pose.bones:
            if not all(finite_vector(v) for v in (bone.matrix.translation,
                                                   bone.matrix.to_scale(),
                                                   bone.matrix.to_quaternion())):
                nonfinite_samples.append({"frame": value, "bone": bone.name})
        depsgraph = bpy.context.evaluated_depsgraph_get()
        for mesh_obj in meshes:
            evaluated = mesh_obj.evaluated_get(depsgraph)
            mesh = evaluated.to_mesh()
            try:
                sample_vertices += len(mesh.vertices)
                for index, vertex in enumerate(mesh.vertices):
                    point = evaluated.matrix_world @ vertex.co
                    if not finite_vector(point):
                        nonfinite_samples.append({"frame": value, "mesh": mesh_obj.name,
                                                  "vertex": index})
                        break
            finally:
                evaluated.to_mesh_clear()
    report = {
        "schema": "mg1.galaxy-skeleton-mesh-bind-reopen-validation.v1",
        "preview": bpy.data.filepath,
        "rig": rig.name,
        "bone_count": len(rig.data.bones),
        "action": action.name,
        "action_slot": slot.identifier,
        "action_curve_count": len(curves),
        "frame_range": [first, last],
        "samples_checked": len(frames),
        "evaluated_vertices_checked": sample_vertices,
        "bound_meshes": [obj.name for obj in meshes],
        "visible_bound_meshes": [obj.name for obj in visible_meshes],
        "visible_armatures": visible_armatures,
        "pose_constraints": sum(len(pb.constraints) for pb in rig.pose.bones),
        "mesh_material_slots": {obj.name: len(obj.data.materials) for obj in meshes},
        "nonfinite_samples": nonfinite_samples,
        "gates": {
            "30_bone_source_skeleton_copy": len(rig.data.bones) == 30,
            "two_bound_meshes": len(meshes) == 2,
            "both_bound_meshes_visible": len(visible_meshes) == 2,
            "only_candidate_armature_visible": visible_armatures == [rig.name],
            "action_assigned": action is not None and slot is not None,
            "no_nonfinite_evaluation": not nonfinite_samples,
        }
    }
    report["passed"] = all(report["gates"].values())
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
    print("GALAXY_SKIN_BIND_REOPEN", json.dumps({k: report[k] for k in (
        "action", "samples_checked", "evaluated_vertices_checked", "visible_bound_meshes",
        "visible_armatures", "nonfinite_samples", "gates", "passed")}, sort_keys=True))
    if not report["passed"]:
        raise RuntimeError("saved candidate failed reopen validation")


if __name__ == "__main__":
    main()
