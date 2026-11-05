"""Compare the Luigi jump clip endpoint with separate fall/landing starts."""
import argparse
import json
import math
import os
import sys

import bpy
from mathutils import Matrix

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from prototype_direct_luigi_walk_literal import bag_for, evaluate_curves, slot_for


def args_cli():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--rig", default="luigi_armature")
    p.add_argument("--jump", default="luigi_armature_luigijump")
    p.add_argument("--transitions", nargs="+", default=[
        "luigi_armature_fall", "luigi_armature_land", "luigi_armature_jumpland"])
    p.add_argument("--output", required=True)
    return p.parse_args(argv)


def curve_set(action, rig):
    wanted = rig.animation_data.action_slot.identifier if (
        rig.animation_data and rig.animation_data.action == action and
        rig.animation_data.action_slot) else None
    slot = slot_for(action, wanted)
    return list(bag_for(action, slot).fcurves)


def snapshot(rig, curves, frame):
    evaluate_curves(rig, curves, frame)
    out = {}
    for bone in rig.data.bones:
        pb = rig.pose.bones[bone.name]
        basis = pb.matrix_basis.copy()
        q = basis.to_quaternion().normalized()
        out[bone.name] = {"basis": [list(row) for row in basis],
                          "rotation": [float(v) for v in q]}
    return out


def quat_error(a, b):
    from mathutils import Quaternion
    qa, qb = Quaternion(a).normalized(), Quaternion(b).normalized()
    return 2.0 * math.acos(min(1.0, max(-1.0, abs(float(qa.dot(qb))))))


def main():
    args = args_cli()
    rig = bpy.data.objects.get(args.rig)
    jump = bpy.data.actions.get(args.jump)
    if not rig or not jump:
        raise RuntimeError("jump rig/action missing")
    original_modes = {pb.name: pb.rotation_mode for pb in rig.pose.bones}
    jump_curves = curve_set(jump, rig)
    jump_end = float(jump.frame_range[1])
    end = snapshot(rig, jump_curves, jump_end)
    comparisons = []
    for action_name in args.transitions:
        action = bpy.data.actions.get(action_name)
        if not action:
            continue
        curves = curve_set(action, rig)
        start = snapshot(rig, curves, float(action.frame_range[0]))
        rows = []
        for name in end:
            ea, sa = Matrix(end[name]["basis"]), Matrix(start[name]["basis"])
            rows.append({"bone": name,
                         "rotation_delta_rad": quat_error(end[name]["rotation"], start[name]["rotation"]),
                         "local_translation_delta": float((ea.translation-sa.translation).length)})
        comparisons.append({"action": action_name,
                            "frame_range": [float(v) for v in action.frame_range],
                            "mean_rotation_delta_rad": sum(x["rotation_delta_rad"] for x in rows)/len(rows),
                            "p95_rotation_delta_rad": sorted(x["rotation_delta_rad"] for x in rows)[int(.95*(len(rows)-1))],
                            "mean_translation_delta": sum(x["local_translation_delta"] for x in rows)/len(rows),
                            "largest_rotation_deltas": sorted(rows, key=lambda r:r["rotation_delta_rad"], reverse=True)[:8]})
    for name, mode in original_modes.items():
        rig.pose.bones[name].rotation_mode = mode
    report = {"schema":"mg1.luigi-jump-transition-comparison.v1",
              "source_action":jump.name,
              "source_loop_type":getattr(jump,"bck_loop_type",None),
              "jump_frame_range":[float(v) for v in jump.frame_range],
              "jump_endpoint_frame":jump_end,
              "conclusion":"Jump endpoint remains the original airborne clip pose. Follow-up fall/landing behavior is represented by separate source actions; no landing is synthesized.",
              "transition_candidates":comparisons}
    with open(args.output,"w",encoding="utf-8") as f: json.dump(report,f,indent=2)
    print("JUMP_TRANSITIONS",json.dumps({"jump_end":jump_end,"comparisons":[
        {k:v for k,v in c.items() if k!="largest_rotation_deltas"} for c in comparisons]}))


if __name__ == "__main__": main()
