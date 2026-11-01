"""Compare stored 38-bone context samples with two fresh action evaluations.

Read-only audit: both action evaluation paths run on disposable copies. No
production object, action, mesh, or rig data is assigned or edited.
"""
import argparse
import gzip
import json
import os
import re
import sys

import bpy
from mathutils import Matrix, Quaternion, Vector

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from analyze_native_arm_context import source_pose_from_curves
from convert_native_owner_actions import (
    converted_matrices, duplicate_rig, evaluated_bone_matrices, set_frame,
)


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--source-rig", default="Armature.001")
    parser.add_argument("--target-rig", default="123475520_waluigi.gpl")
    parser.add_argument("--slot-id", default="OBArmature.004")
    return parser.parse_args(argv)


def err(a, b):
    return max(abs(float(a[r][c]) - float(b[r][c]))
               for r in range(4) for c in range(4))


def action_slot(action, slot_id):
    slots = [slot for slot in action.slots if slot.identifier == slot_id]
    if len(slots) != 1:
        raise RuntimeError(f"{action.name}: expected exactly one {slot_id} slot")
    return slots[0]


def apply_curves_as_pose_basis(rig, action, slot, time):
    """Apply slot FCurve values as pose channels, then let Blender compose bones."""
    bag = next(bag for layer in action.layers for strip in layer.strips
               for bag in strip.channelbags if bag.slot == slot)
    values = {pb.name: {"location": [0.0, 0.0, 0.0],
                        "rotation_quaternion": [1.0, 0.0, 0.0, 0.0],
                        "scale": [1.0, 1.0, 1.0]} for pb in rig.pose.bones}
    path_re = re.compile(r'pose\.bones\["([^\"]+)"\]\.([a-zA-Z_]+)')
    for curve in bag.fcurves:
        match = path_re.fullmatch(curve.data_path)
        if not match:
            continue
        bone, prop = match.groups()
        if bone in values and prop in values[bone] and curve.array_index < len(values[bone][prop]):
            values[bone][prop][curve.array_index] = float(curve.evaluate(time))
    rig.animation_data.action = None
    for pb in rig.pose.bones:
        pb.rotation_mode = "QUATERNION"
        row = values[pb.name]
        pb.location = row["location"]
        pb.rotation_quaternion = Quaternion(row["rotation_quaternion"]).normalized()
        pb.scale = row["scale"]
    bpy.context.view_layer.update()


def main():
    args = parse_args()
    with gzip.open(args.dataset, "rt", encoding="utf-8") as stream:
        data = json.load(stream)
    src_original = bpy.data.objects.get(args.source_rig)
    dst_original = bpy.data.objects.get(args.target_rig)
    if not src_original or not dst_original:
        raise RuntimeError("source and target armatures must exist in the file")
    src = duplicate_rig(src_original, "_MG1_Dataset_Candidate_Source")
    # duplicate_rig is optimized for baking: it forces quaternion mode. Native
    # action evaluation must retain each source pose bone's authored mode.
    for name, source_pb in src_original.pose.bones.items():
        src.pose.bones[name].rotation_mode = source_pb.rotation_mode
    conversion = dst_original.matrix_world.inverted_safe() @ src_original.matrix_world
    scene = bpy.context.scene
    old_frame, old_subframe = scene.frame_current, scene.frame_subframe
    max_errors = {"fresh_blender_action_evaluation": 0.0,
                  "explicit_fcurve_values_composed_by_blender": 0.0,
                  "explicit_fcurve_hierarchy_composition": 0.0}
    worst = {key: None for key in max_errors}
    per_action = []
    try:
        for action_row in data["actions"]:
            action = bpy.data.actions.get(action_row["action"])
            if action is None:
                raise RuntimeError("missing action " + action_row["action"])
            slot = action_slot(action, args.slot_id)
            src.animation_data_create()
            src.animation_data.action = None
            for pb in src.pose.bones:
                pb.matrix_basis = Matrix.Identity(4)
            bpy.context.view_layer.update()
            src.animation_data.action = action
            src.animation_data.action_slot = slot
            action_result = {"action": action.name, "split": action_row["split"],
                             "sample_count": len(action_row["samples"]),
                             "modes": {key: {"max_matrix_error": 0.0, "worst": None}
                                       for key in max_errors}}
            for sample in action_row["samples"]:
                time = float(sample["time"])
                set_frame(scene, time)
                # Keep unkeyed channels at clean rest for every comparison;
                # the manual-channel path below leaves a pose on the copy.
                for pb in src.pose.bones:
                    pb.matrix_basis = Matrix.Identity(4)
                bpy.context.view_layer.update()
                blender_source = evaluated_bone_matrices(src)
                blender_target = converted_matrices(src, dst_original, blender_source, conversion)
                curves_source, _ = source_pose_from_curves(src_original, action, slot, time)
                curves_target = converted_matrices(src_original, dst_original, curves_source, conversion)
                apply_curves_as_pose_basis(src, action, slot, time)
                applied_source = evaluated_bone_matrices(src)
                applied_target = converted_matrices(src, dst_original, applied_source, conversion)
                # Restore native action evaluation for the next sample.
                src.animation_data.action = action
                src.animation_data.action_slot = slot
                for mode, candidate in (("fresh_blender_action_evaluation", blender_target),
                                        ("explicit_fcurve_values_composed_by_blender", applied_target),
                                        ("explicit_fcurve_hierarchy_composition", curves_target)):
                    for bone in data["bones"]:
                        expected = Matrix(sample["bones"][bone]["armature_pose"])
                        value = err(candidate[bone], expected)
                        if value > action_result["modes"][mode]["max_matrix_error"]:
                            action_result["modes"][mode]["max_matrix_error"] = value
                            action_result["modes"][mode]["worst"] = {
                                "bone": bone, "time": time, "matrix_error": value}
                        if value > max_errors[mode]:
                            max_errors[mode] = value
                            worst[mode] = {"action": action.name, "bone": bone,
                                           "time": time, "matrix_error": value}
            per_action.append(action_result)
        report = {"schema": "mg1.native-arm-context-live-dataset-audit.v1",
                  "dataset": args.dataset, "source_rig": src_original.name,
                  "target_rig": dst_original.name, "slot_id": args.slot_id,
                  "bones": data["bones"], "action_count": len(per_action),
                  "sample_count": sum(row["sample_count"] for row in per_action),
                  "max_matrix_error_by_evaluation_path": max_errors,
                  "worst_by_evaluation_path": worst, "actions": per_action}
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2, allow_nan=False)
            stream.write("\n")
        print("MG1_CONTEXT_DATASET_LIVE_AUDIT=" + json.dumps({
            "sample_count": report["sample_count"],
            "max_matrix_error_by_evaluation_path": max_errors,
            "worst_by_evaluation_path": worst}))
    finally:
        scene.frame_set(old_frame, subframe=old_subframe)
        armature_data = src.data
        bpy.data.objects.remove(src, do_unlink=True)
        if armature_data.users == 0:
            bpy.data.armatures.remove(armature_data)


if __name__ == "__main__":
    main()
