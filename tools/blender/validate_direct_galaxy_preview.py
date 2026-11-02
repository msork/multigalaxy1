"""Reopen validation for a disposable direct Galaxy walk preview."""
import argparse
import json
import math
import os
import sys

import bpy


def args_parse():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--report", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--rig", default="DirectWalk_Waluigi_Target")
    p.add_argument("--action", default="Waluigi_DirectGalaxyWalk_ArmAdapter_v2_ControlChain")
    return p.parse_args(argv)


def matrix_error(a, rows):
    return max(abs(float(a[r][c]) - float(rows[r][c]))
               for r in range(4) for c in range(4))


def main():
    args = args_parse()
    with open(args.report, "r", encoding="utf-8") as f:
        expected = json.load(f)
    rig = bpy.data.objects.get(args.rig)
    if rig is None or rig.type != 'ARMATURE':
        raise RuntimeError("reopened preview target rig is missing")
    visible_objects = [o for o in bpy.data.objects
                       if o.visible_get(view_layer=bpy.context.view_layer)]
    visible_rigs = [o.name for o in visible_objects if o.type == 'ARMATURE']
    visible_meshes = [o for o in visible_objects if o.type == 'MESH']
    mesh_binding = {o.name: [m.object.name if m.type == 'ARMATURE' and m.object else None
                              for m in o.modifiers if m.type == 'ARMATURE']
                    for o in visible_meshes}
    if any(name != args.rig for name in visible_rigs):
        raise RuntimeError(f"a second character armature is visible: {visible_rigs}")
    if len(visible_meshes) != 2 or any(v != [args.rig] for v in mesh_binding.values()):
        raise RuntimeError(f"visible mesh set/bindings are wrong: {mesh_binding}")
    if rig.animation_data is None or rig.animation_data.action is None or rig.animation_data.action.name != args.action:
        raise RuntimeError("direct walk action is not assigned to the target rig")
    constraints = [(pb.name, c.name) for pb in rig.pose.bones for c in pb.constraints]
    if constraints:
        raise RuntimeError(f"runtime constraints remain: {constraints}")
    scale = list(rig.matrix_basis.to_scale())
    if max(abs(v - 43.437) for v in scale) > 0.01:
        raise RuntimeError(f"intentional Waluigi object scale changed: {scale}")

    scene = bpy.context.scene
    max_pose_error = 0.0
    worst = None
    frame_checks = []
    for frame in range(61):
        scene.frame_set(frame)
        bpy.context.view_layer.update()
        dg = bpy.context.evaluated_depsgraph_get()
        ev = rig.evaluated_get(dg)
        expected_frame = expected["expected_pose_matrices"][str(frame)]
        frame_error = 0.0
        frame_bone = None
        for name, matrix_rows in expected_frame.items():
            error = matrix_error(ev.pose.bones[name].matrix, matrix_rows)
            if error > frame_error:
                frame_error, frame_bone = error, name
            if not all(math.isfinite(float(x)) for row in ev.pose.bones[name].matrix for x in row):
                raise RuntimeError(f"non-finite evaluated matrix at frame {frame}: {name}")
        if frame_error > max_pose_error:
            max_pose_error, worst = frame_error, {"frame": frame, "bone": frame_bone}
        if frame in (0, 15, 30, 45, 60):
            frame_checks.append({"frame": frame, "max_pose_error": frame_error,
                                 "sample_bone": frame_bone})
    report = {
        "schema": "mg1.direct-galaxy-walk-reopen-validation.v1",
        "blend": bpy.data.filepath,
        "rig": rig.name,
        "action": rig.animation_data.action.name,
        "action_slot": rig.animation_data.action_slot.identifier if rig.animation_data.action_slot else None,
        "visible_character_armatures": visible_rigs,
        "visible_meshes": [o.name for o in visible_meshes],
        "mesh_armature_bindings": mesh_binding,
        "runtime_constraint_count": len(constraints),
        "object_scale": scale,
        "max_reopened_pose_matrix_error": max_pose_error,
        "worst": worst,
        "sample_frames": frame_checks,
        "passed": max_pose_error < 1e-4 and len(visible_meshes) == 2 and not constraints,
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, allow_nan=False)
        f.write("\n")
    print(json.dumps(report))
    if not report["passed"]:
        raise RuntimeError("direct preview reopen validation failed")


if __name__ == "__main__":
    main()
