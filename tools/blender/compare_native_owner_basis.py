"""Compare the candidate action owner with the production rig in one autosave.

The operation is read-only apart from temporary duplicates used to examine
neutral pose channels. The input file is never saved.
"""
import bpy
import json
import math
import os
import sys
from mathutils import Matrix

ARM_BONES = ("bone_22", "bone_23", "bone_24", "bone_25", "bone_26",
             "bone_27", "bone_29", "bone_30")


def rows(m):
    return [[float(m[r][c]) for c in range(4)] for r in range(4)]


def maxerr(a, b):
    return max(abs(float(a[r][c]) - float(b[r][c])) for r in range(4) for c in range(4))


def qangle(a, b):
    qa, qb = a.to_quaternion().normalized(), b.to_quaternion().normalized()
    dot = abs(qa.dot(qb))
    return math.degrees(2.0 * math.acos(min(1.0, max(-1.0, dot))))


def main():
    argv = sys.argv[sys.argv.index("--") + 1:]
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", default="Armature.001")
    parser.add_argument("--production", default="123475520_waluigi.gpl")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    candidate = bpy.data.objects.get(args.candidate)
    if not candidate or candidate.type != "ARMATURE":
        raise RuntimeError("candidate armature missing: " + args.candidate)
    prod = bpy.data.objects.get(args.production)
    if not prod or prod.type != 'ARMATURE':
        raise RuntimeError("production armature missing: " + args.production)

    # Preserve source data. Clear only temporary copies to inspect neutral pose channels.
    cand_neutral = candidate.copy()
    cand_neutral.data = candidate.data.copy()
    bpy.context.scene.collection.objects.link(cand_neutral)
    cand_neutral.animation_data_clear()
    prod_neutral = prod.copy()
    prod_neutral.data = prod.data.copy()
    bpy.context.scene.collection.objects.link(prod_neutral)
    prod_neutral.animation_data_clear()
    bpy.context.view_layer.update()

    C = prod.matrix_world.inverted_safe() @ candidate.matrix_world
    common = sorted(set(candidate.data.bones.keys()) & set(prod.data.bones.keys()))
    parents_equal = all((candidate.data.bones[n].parent.name if candidate.data.bones[n].parent else None) ==
                        (prod.data.bones[n].parent.name if prod.data.bones[n].parent else None) for n in common)
    per_bone = {}
    world_errors, local_errors, orient_errors = [], [], []
    for name in common:
        cs, pt = candidate.data.bones[name], prod.data.bones[name]
        Rs, Rt = cs.matrix_local.copy(), pt.matrix_local.copy()
        world_s, world_t = candidate.matrix_world @ Rs, prod.matrix_world @ Rt
        parent_s = (cs.parent.matrix_local.inverted_safe() @ Rs) if cs.parent else Rs
        parent_t = (pt.parent.matrix_local.inverted_safe() @ Rt) if pt.parent else Rt
        mapped_rest = C @ Rs
        constant_right = Rs.inverted_safe() @ C.inverted_safe() @ Rt
        werr, lerr = maxerr(world_s, world_t), maxerr(parent_s, parent_t)
        rerr = qangle(mapped_rest, Rt)
        head_err = ((candidate.matrix_world @ cs.head_local) - (prod.matrix_world @ pt.head_local)).length
        tail_err = ((candidate.matrix_world @ cs.tail_local) - (prod.matrix_world @ pt.tail_local)).length
        world_errors.append(werr); local_errors.append(lerr); orient_errors.append(rerr)
        if name in ARM_BONES:
            per_bone[name] = {
                "parent_candidate": cs.parent.name if cs.parent else None,
                "parent_production": pt.parent.name if pt.parent else None,
                "candidate_rest_length": float(cs.length),
                "production_rest_length": float(pt.length),
                "rest_world_matrix_max_error": werr,
                "rest_world_head_position_error": float(head_err),
                "rest_world_tail_position_error": float(tail_err),
                "parent_space_matrix_max_error": lerr,
                "C_rest_orientation_error_degrees": rerr,
                "constant_per_bone_right_correction_K": rows(constant_right),
                "candidate_rest_local": rows(Rs), "production_rest_local": rows(Rt),
                "candidate_parent_space": rows(parent_s), "production_parent_space": rows(parent_t),
                "candidate_pose_default": rows(cand_neutral.pose.bones[name].matrix_basis),
                "production_pose_default": rows(prod_neutral.pose.bones[name].matrix_basis),
            }
    output = {
        "candidate": candidate.name, "candidate_data": candidate.data.name,
        "production": prod.name, "production_data": prod.data.name,
        "candidate_object_matrix_world": rows(candidate.matrix_world),
        "production_object_matrix_world": rows(prod.matrix_world),
        "candidate_scale": list(candidate.scale), "production_scale": list(prod.scale),
        "candidate_action": candidate.animation_data.action.name if candidate.animation_data and candidate.animation_data.action else None,
        "production_action": prod.animation_data.action.name if prod.animation_data and prod.animation_data.action else None,
        "candidate_pose_defaults_source": "temporary candidate duplicate with animation data cleared",
        "production_pose_defaults_source": "temporary production duplicate with animation data cleared",
        "coordinate_conversion_C_prod_from_candidate": rows(C),
        "common_bone_count": len(common), "parent_hierarchies_equal": parents_equal,
        "max_rest_world_matrix_error": max(world_errors, default=0.0),
        "max_parent_space_matrix_error": max(local_errors, default=0.0),
        "max_rest_orientation_error_after_object_C_degrees": max(orient_errors, default=0.0),
        "arm_bones": per_bone,
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, allow_nan=False)
        f.write("\n")
    print("MG1_OWNER_BASIS=" + json.dumps({k: output[k] for k in (
        "candidate", "production", "common_bone_count", "parent_hierarchies_equal",
        "max_rest_world_matrix_error", "max_parent_space_matrix_error",
        "max_rest_orientation_error_after_object_C_degrees", "coordinate_conversion_C_prod_from_candidate")}))


if __name__ == "__main__":
    main()
