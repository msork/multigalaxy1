"""Reopen validation for the disposable direct literal Luigi walk preview."""
import argparse
import json
import math
import os
import sys

import bpy
from mathutils import Matrix, Quaternion


def args_from_cli():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--fit-report", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--rig", default="LiteralWalk_Waluigi_Target")
    return p.parse_args(argv)


def mat_error(a, b):
    return max(abs(float(a[r][c]) - float(b[r][c]))
               for r in range(4) for c in range(4))


def q_angle(a, b):
    qa, qb = a.copy(), b.copy()
    qa.normalize(); qb.normalize()
    return 2.0 * math.acos(min(1.0, max(-1.0, abs(float(qa.dot(qb))))))


def main():
    args = args_from_cli()
    with open(args.fit_report, "r", encoding="utf-8") as stream:
        fit = json.load(stream)
    target = bpy.data.objects.get(args.rig)
    if target is None or target.type != "ARMATURE":
        raise RuntimeError(f"preview armature not found: {args.rig}")
    if not target.animation_data or not target.animation_data.action:
        raise RuntimeError("preview rig has no baked action")
    expected = fit["expected_pose_basis"]
    scene = bpy.context.scene
    max_matrix_error = 0.0
    worst = None
    max_fist_step = 0.0
    fist_reference = {}
    finger_bones = [f"bone_{i}" for i in range(31, 52)] + [f"bone_{i}" for i in range(66, 87)]
    for frame_key in sorted(expected, key=float):
        frame = float(frame_key)
        scene.frame_set(math.floor(frame), subframe=frame - math.floor(frame))
        bpy.context.view_layer.update()
        for name, rows in expected[frame_key].items():
            actual = target.pose.bones[name].matrix_basis
            error = mat_error(actual, Matrix(rows))
            if error > max_matrix_error:
                max_matrix_error = error
                worst = {"frame": frame, "bone": name, "matrix_error": error}
        for name in finger_bones:
            q = target.pose.bones[name].matrix_basis.to_quaternion()
            if name not in fist_reference:
                fist_reference[name] = q.copy()
            else:
                max_fist_step = max(max_fist_step, q_angle(fist_reference[name], q))
    constraints = sum(len(pb.constraints) for pb in target.pose.bones)
    scale = list(target.matrix_basis.to_scale())
    scale_error = max(abs(float(a) - float(b)) for a, b in zip(
        scale, fit["intentional_target_object_scale"]))
    scene_meshes = [obj for obj in scene.objects if obj.type == "MESH" and
                    any(mod.type == "ARMATURE" and mod.object == target for mod in obj.modifiers)]
    visible_meshes = [obj for obj in scene_meshes if not obj.hide_render and not obj.hide_viewport]
    visible_armatures = [obj for obj in scene.objects if obj.type == "ARMATURE" and
                         not obj.hide_render and not obj.hide_viewport]
    report = {
        "schema": "mg1.literal-walk-reopen-validation.v1",
        "preview": bpy.data.filepath,
        "action": target.animation_data.action.name,
        "action_slot": target.animation_data.action_slot.identifier if target.animation_data.action_slot else None,
        "samples_checked": len(expected),
        "max_reopened_pose_basis_error": max_matrix_error,
        "worst_pose_sample": worst,
        "max_closed_fist_local_rotation_change": max_fist_step,
        "runtime_constraints": constraints,
        "object_scale": scale,
        "scale_error": scale_error,
        "bound_meshes": [obj.name for obj in scene_meshes],
        "visible_bound_meshes": [obj.name for obj in visible_meshes],
        "visible_armatures": [obj.name for obj in visible_armatures],
        "gates": {
            "reopened_pose_matches_bake": max_matrix_error < 1e-5,
            "fixed_fists": max_fist_step < 1e-5,
            "no_runtime_constraints": constraints == 0,
            "scale_preserved": scale_error < 1e-6,
            "exactly_two_visible_target_meshes": len(visible_meshes) == 2,
            "exactly_one_visible_armature": visible_armatures == [target],
        }
    }
    report["passed"] = all(report["gates"].values())
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
    print("LITERAL_WALK_REOPEN", json.dumps({k: report[k] for k in (
        "samples_checked", "max_reopened_pose_basis_error",
        "max_closed_fist_local_rotation_change", "scale_error", "gates", "passed")}, sort_keys=True))
    if not report["passed"]:
        raise RuntimeError("saved/reopened preview validation failed")


if __name__ == "__main__":
    main()
