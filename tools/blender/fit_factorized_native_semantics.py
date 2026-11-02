"""Fit and validate factorized semantic-to-arm deformation on raw ANM holds.

Run in Blender 5.2 background mode with the Waluigi/Luigi validation workfile.
Training uses only raw-ANM training actions. A preview is saved only if every
complete-action holdout passes the predeclared deformation gates.
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import os
import re
import sys

import bpy
import numpy as np
from mathutils import Matrix

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import factorized_semantic_deformation as model_math
import fit_luigi_walk_semantic_waluigi as fit
import semantic_arm_fit_math as semantic_math
from build_converted_native_arm_dataset import mesh_centroids
from context_dataset import _rest_relative_matrix
from convert_native_owner_actions import (
    duplicate_rig, evaluated_bone_matrices, make_action,
)
from test_semantic_preview_noop import duplicate_mesh


TORSO = "bone_21"
ARM_BONES = {
    "left": ("bone_22", "bone_23", "bone_24", "bone_25",
             "bone_26", "bone_27", "bone_29", "bone_30"),
    "right": ("bone_57", "bone_58", "bone_59", "bone_60",
              "bone_61", "bone_62", "bone_64", "bone_65"),
}
LANDMARKS = {
    "left": ("shoulder", "elbow_main", "elbow_overlap", "wrist_main",
             "glove_wrist", "glove_tip"),
    "right": ("shoulder_right", "elbow_main_right", "elbow_overlap_right",
              "wrist_right", "glove_wrist_right", "glove_tip_right"),
}


def parse_args():
    if "--" in sys.argv:
        argv = sys.argv[sys.argv.index("--") + 1:]
    else:
        script_arg = next((i for i, value in enumerate(sys.argv)
                           if value.endswith("fit_factorized_native_semantics.py")), -1)
        argv = sys.argv[script_arg + 1:] if script_arg >= 0 else []
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--preview", required=True)
    parser.add_argument("--target-rig", default="123475520_waluigi.gpl")
    parser.add_argument("--clean-rig", default="Waluigi_Arm_Clean_Backup")
    parser.add_argument("--fist-baseline", default="Waluigi_Arm_Test")
    parser.add_argument("--fist-action", default="Waluigi_GalaxyWalk_Legs_Baked")
    parser.add_argument("--preview-action", default="")
    parser.add_argument("--arm-length", type=float, default=0.0)
    return parser.parse_args(argv)


def matrix(value):
    return np.asarray(value, dtype=np.float64).reshape(4, 4)


def matrix_rows(value):
    return [[float(value[r, c]) for c in range(4)] for r in range(4)]


def rotvec(rotation):
    return semantic_math._rotvec(np.asarray(rotation, dtype=np.float64))


def exp_rotvec(vector):
    v = np.asarray(vector, dtype=np.float64)
    angle = float(np.linalg.norm(v))
    if angle < 1e-12:
        return np.eye(3) + np.asarray([
            [0.0, -v[2], v[1]], [v[2], 0.0, -v[0]],
            [-v[1], v[0], 0.0]])
    axis = v / angle
    x, y, z = axis
    skew = np.asarray([[0.0, -z, y], [z, 0.0, -x], [-y, x, 0.0]])
    return np.eye(3) + math.sin(angle) * skew + (1.0 - math.cos(angle)) * (skew @ skew)


def sample_pose(sample, rest_bones):
    pose = {}
    for name, row in sample["bones"].items():
        value = row.get("armature_pose_matrix")
        if value is not None:
            pose[name] = matrix(value)
        else:
            pose[name] = matrix(rest_bones[name]["rest_matrix"]) @ \
                         _rest_relative_matrix(row["rest_relative_se3"])
    return pose


def arm_transform_features(sample, side, rest_bones, arm_length):
    pose = sample_pose(sample, rest_bones)
    torso_pose = pose[TORSO]
    inv_torso = np.linalg.inv(torso_pose)
    rest_torso = matrix(rest_bones[TORSO]["rest_matrix"])
    result = []
    for name in ARM_BONES[side]:
        rest_body = np.linalg.inv(rest_torso) @ matrix(rest_bones[name]["rest_matrix"])
        current_body = inv_torso @ pose[name]
        delta = np.linalg.inv(rest_body) @ current_body
        result.extend((delta[:3, 3] / arm_length).tolist())
        result.extend(rotvec(delta[:3, :3]).tolist())
    return np.asarray(result, dtype=np.float64)


def decode_arm_features(values, side, torso_pose, rest_bones, arm_length):
    result = {}
    rest_torso = matrix(rest_bones[TORSO]["rest_matrix"])
    for index, name in enumerate(ARM_BONES[side]):
        row = np.asarray(values[index * 6:index * 6 + 6], dtype=np.float64)
        delta = np.eye(4)
        delta[:3, :3] = exp_rotvec(row[3:6])
        delta[:3, 3] = row[:3] * arm_length
        rest_body = np.linalg.inv(rest_torso) @ matrix(rest_bones[name]["rest_matrix"])
        result[name] = matrix(torso_pose) @ rest_body @ delta
    return result


def context_rest(rig):
    return {bone.name: {"rest_matrix": matrix_rows(np.asarray(bone.matrix_local, dtype=np.float64))}
            for bone in rig.data.bones}


def angle_between_rotations(first, second):
    relative = np.asarray(first)[:3, :3].T @ np.asarray(second)[:3, :3]
    vector = rotvec(relative)
    return float(np.linalg.norm(vector))


def matrix_errors(predicted, expected, bones, arm_length):
    rows = {}
    for name in bones:
        a, b = np.asarray(predicted[name]), np.asarray(expected[name])
        rows[name] = {
            "translation_normalized": float(np.linalg.norm(a[:3, 3] - b[:3, 3]) / arm_length),
            "rotation_rad": angle_between_rotations(a, b),
            "matrix_max_abs": float(np.max(np.abs(a - b))),
        }
    return rows


def aggregate(values):
    array = np.asarray(values, dtype=np.float64)
    if not len(array):
        return {"count": 0, "rmse": None, "p95": None, "max": None}
    return {"count": int(len(array)), "rmse": float(np.sqrt(np.mean(array ** 2))),
            "p95": float(np.quantile(array, 0.95)), "max": float(np.max(array))}


def make_training_arrays(dataset, side, rest_bones, arm_length):
    rows, targets, action_ids, sample_ids = [], [], [], []
    action_rows = [action for action in dataset["actions"] if action["split"] == "train"]
    if not action_rows or any(action["split"] == "holdout" for action in action_rows):
        raise RuntimeError("training selection unexpectedly includes non-training actions")
    for action in action_rows:
        for sample_index, sample in enumerate(action["samples"]):
            rows.append(sample["anatomical_semantics"][side])
            targets.append(arm_transform_features(sample, side, rest_bones, arm_length))
            action_ids.append(action["action"])
            sample_ids.append((action["action"], sample_index))
    return (np.asarray(rows, dtype=np.float64), np.asarray(targets, dtype=np.float64),
            np.asarray(action_ids), sample_ids)


def fit_model(model_kind, x_train, y_train, action_ids):
    if model_kind == "factorized":
        design, state = model_math.factorized_design(x_train)
        builder = model_math.factorized_design
    else:
        design, state = model_math.global_quadratic_design(x_train)
        builder = model_math.global_quadratic_design
    fitted = model_math.fit_ridge_cv(design, y_train, action_ids)
    fitted["design_state"] = state
    fitted["kind"] = model_kind
    fitted["design_builder"] = builder
    return fitted


def build_holdout_predictions(fitted, x_hold):
    state = fitted["design_state"]
    if fitted["kind"] == "factorized":
        design, _ = model_math.factorized_design(x_hold,
            np.asarray(state["mean"]), np.asarray(state["scale"]),
            state["quadratic_center"])
    else:
        design, _ = model_math.global_quadratic_design(x_hold,
            np.asarray(state["mean"]), np.asarray(state["scale"]))
    return model_math.predict(fitted, design), design


def capture_fist_locals(scene, baseline, walk_action, clean, target_rig):
    if baseline is None or clean is None or walk_action is None:
        raise RuntimeError("closed-fist baseline rig/action is unavailable")
    rig = duplicate_rig(clean, "_Factorized_ClosedFist_Disposable")
    slot = fit.select_walk_slot(walk_action, "", target_rig.data.bones.keys())
    rig.animation_data_create()
    rig.animation_data.action = walk_action
    rig.animation_data.action_slot = slot
    scene.frame_set(0)
    bpy.context.view_layer.update()
    matrices = {name: np.asarray(m, dtype=np.float64)
                for name, m in evaluated_bone_matrices(rig).items()}
    parents = {bone.name: bone.parent.name if bone.parent else None
               for bone in rig.data.bones}
    finger_map = {}
    for hand in ("bone_30", "bone_65"):
        descendants = fit.descendant_bone_names(rig.data, hand)
        for name in descendants:
            parent = parents[name]
            if parent in matrices:
                finger_map[name] = (parent, np.linalg.inv(matrices[parent]) @ matrices[name])
    data = rig.data
    bpy.data.objects.remove(rig, do_unlink=True)
    if data.users == 0:
        bpy.data.armatures.remove(data)
    return finger_map, parents


def apply_fists_and_glove(pose, rest_bones, finger_map, parents, rig_data):
    # Keep the closed-fist bone transforms in parent-local coordinates.
    depths = {}
    def depth(name):
        if name in depths:
            return depths[name]
        value, parent = 0, parents.get(name)
        while parent is not None:
            value += 1
            parent = parents.get(parent)
        depths[name] = value
        return value
    fist_error = 0.0
    for name in sorted(finger_map, key=depth):
        parent, relative = finger_map[name]
        if parent not in pose:
            continue
        pose[name] = pose[parent] @ relative
        current = np.linalg.inv(pose[parent]) @ pose[name]
        fist_error = max(fist_error, float(np.max(np.abs(current - relative))))

    gaps_before, correction = {}, {}
    for side, wrist, glove in (("left", "bone_27", "bone_30"),
                               ("right", "bone_62", "bone_65")):
        def endpoint(bone, point):
            rest = matrix(rest_bones[bone]["rest_matrix"])
            p = np.ones(4)
            p[:3] = rest_bones[bone][point]
            return (pose[bone] @ np.linalg.inv(rest) @ p)[:3]
        gap = endpoint(wrist, "rest_tail") - endpoint(glove, "rest_head")
        gaps_before[side] = float(np.linalg.norm(gap))
        descendants = fit.descendant_bone_names(rig_data, glove) | {glove}
        for name in descendants:
            if name in pose:
                pose[name][:3, 3] += gap
        correction[side] = float(np.linalg.norm(gap))
    return pose, {"before": gaps_before, "correction": correction,
                  "after": {"left": 0.0, "right": 0.0},
                  "fist_local_max_abs_error": fist_error}


def landmark_positions(pose, rest_bones, object_matrix):
    names = {
        "shoulder": ("bone_22", "rest_head"),
        "elbow_main": ("bone_26", "rest_head"),
        "elbow_overlap": ("bone_29", "rest_head"),
        "wrist_main": ("bone_27", "rest_tail"),
        "glove_wrist": ("bone_30", "rest_head"),
        "glove_tip": ("bone_30", "rest_tail"),
        "shoulder_right": ("bone_58", "rest_head"),
        "elbow_main_right": ("bone_61", "rest_head"),
        "elbow_overlap_right": ("bone_64", "rest_head"),
        "wrist_right": ("bone_62", "rest_tail"),
        "glove_wrist_right": ("bone_65", "rest_head"),
        "glove_tip_right": ("bone_65", "rest_tail"),
    }
    out = {}
    for label, (bone, endpoint) in names.items():
        rest = matrix(rest_bones[bone]["rest_matrix"])
        point = np.ones(4)
        point[:3] = rest_bones[bone][endpoint]
        armature_point = pose[bone] @ np.linalg.inv(rest) @ point
        out[label] = (matrix(object_matrix) @ armature_point)[:3]
    return out


def set_pose(rig, pose):
    fit.assign_pose_matrices_hierarchical(rig, {
        name: Matrix(value.tolist()) for name, value in pose.items()
        if name in rig.pose.bones})
    return {name: np.asarray(rig.pose.bones[name].matrix, dtype=np.float64)
            for name in pose if name in rig.pose.bones}


def create_eval_bundle(target, source_meshes, prefix):
    rig = duplicate_rig(target, prefix + "_Rig")
    # The recovery workfile can keep the intentional 43.437 transform in the
    # authored matrix_basis while matrix_world is stale/identity after reload.
    rig.matrix_basis = target.matrix_basis.copy()
    fit.clear_constraints(rig)
    collection = bpy.data.collections.new(prefix + "_Collection")
    bpy.context.scene.collection.children.link(collection)
    for col in list(rig.users_collection):
        col.objects.unlink(rig)
    collection.objects.link(rig)
    mesh_copies = []
    for source in source_meshes:
        canonical = source.name
        if canonical.startswith("ARMTEST_"):
            canonical = canonical[len("ARMTEST_"):]
        dup = duplicate_mesh(source, rig, collection, prefix + "_")
        dup.name = "_MG1_Dataset_Target_" + canonical
        mesh_copies.append(dup)
    bpy.context.view_layer.update()
    return rig, mesh_copies, collection


def remove_eval_bundle(rig, meshes, collection):
    for obj in [*meshes, rig]:
        data = obj.data
        obj_type = obj.type
        bpy.data.objects.remove(obj, do_unlink=True)
        if data and data.users == 0:
            if obj_type == "MESH":
                bpy.data.meshes.remove(data)
            elif obj_type == "ARMATURE":
                bpy.data.armatures.remove(data)
    bpy.data.collections.remove(collection)


def centroid_errors(measured, sample, bones):
    errors = []
    per_bone = {name: [] for name in bones}
    for mesh_name, row in measured.items():
        reference_name = mesh_name.removeprefix("_MG1_Dataset_Target_")
        reference_name = re.sub(r"\.\d{3}$", "", reference_name)
        reference = sample["weighted_mesh_centroids"].get(reference_name, {})
        for bone in bones:
            actual = row[bone]["weighted_centroid_world"]
            expected = reference.get(bone, {}).get("weighted_centroid_world")
            if actual is None or expected is None:
                continue
            error = float(np.linalg.norm(np.asarray(actual) - np.asarray(expected)))
            per_bone[bone].append(error)
            errors.append(error)
    return errors, per_bone


def evaluation_pass(model_name, fitted_by_side, predictions_by_side, holdout,
                    dataset, target, meshes, rest_bones, arm_length, arm_length_world,
                    finger_map, parents, preview_action_name, rest_signatures):
    scene = bpy.context.scene
    tracked_bones = list(dict.fromkeys(ARM_BONES["left"] + ARM_BONES["right"] +
        tuple(fit.descendant_bone_names(target.data, "bone_30")) +
        tuple(fit.descendant_bone_names(target.data, "bone_65"))))
    rig, mesh_copies, collection = create_eval_bundle(target, meshes,
        "_FactorizedEval_" + model_name)
    scene_times = []
    accum = {"translation": [], "rotation": [], "semantic": [], "landmark": [],
             "centroid": [], "pre_glove_gap": {"left": [], "right": []},
             "post_glove_gap": [],
             "glove_correction": {"left": [], "right": []},
             "fist_error": [], "per_bone": {name: {"translation": [], "rotation": []}
                                             for name in ARM_BONES["left"] + ARM_BONES["right"]},
             "per_semantic": {side: {i: [] for i in range(11)} for side in ("left", "right")},
             "per_landmark": {}, "per_centroid_bone": {name: [] for name in tracked_bones},
             "per_action": {}, "branches": {"left_23_25": [], "left_26_29": [],
                 "right_58_60": [], "right_61_64": []}}
    preview_bases = []
    target_world = np.asarray(fit.object_transform_matrix(target), dtype=np.float64)
    object_scale = tuple(float(v) for v in rig.scale)
    predicted_cursor = 0
    for action in holdout:
        action_result = {"sample_count": len(action["samples"]),
                         "translation": [], "rotation": [], "semantic": [],
                         "landmark": [], "centroid": [], "max_glove_gap_before": 0.0,
                         "max_attachment_correction": 0.0}
        x_values = np.asarray([s["anatomical_semantics"]["left"] for s in action["samples"]])
        x_right = np.asarray([s["anatomical_semantics"]["right"] for s in action["samples"]])
        for sample_index, sample in enumerate(action["samples"]):
            reference_pose = sample_pose(sample, rest_bones)
            torso_pose = reference_pose[TORSO]
            pred_left = predictions_by_side["left"][action["action"]][sample_index]
            pred_right = predictions_by_side["right"][action["action"]][sample_index]
            pose = dict(reference_pose)
            pose.update(decode_arm_features(pred_left, "left", torso_pose,
                                             rest_bones, arm_length))
            pose.update(decode_arm_features(pred_right, "right", torso_pose,
                                             rest_bones, arm_length))
            pose, constraint_info = apply_fists_and_glove(
                pose, rest_bones, finger_map, parents, target.data)

            scene.frame_set(math.floor(float(sample["time"])),
                subframe=float(sample["time"]) - math.floor(float(sample["time"])))
            bpy.context.view_layer.update()
            actual = set_pose(rig, pose)
            expected = reference_pose
            bone_error = matrix_errors(actual, expected,
                ARM_BONES["left"] + ARM_BONES["right"], arm_length)
            for name, row in bone_error.items():
                accum["per_bone"][name]["translation"].append(row["translation_normalized"])
                accum["per_bone"][name]["rotation"].append(row["rotation_rad"])
                accum["translation"].append(row["translation_normalized"])
                accum["rotation"].append(row["rotation_rad"])
                action_result["translation"].append(row["translation_normalized"])
                action_result["rotation"].append(row["rotation_rad"])

            profile_pose = {name: actual.get(name, value) for name, value in pose.items()}
            left_sem = semantic_math.semantic_features(
                rest_bones, profile_pose, fit.TARGET_PROFILE,
                rest_signatures["anatomy"]["torso_to_anatomical"],
                rest_signatures["anatomy"]["sides"]["left"]["forearm_to_anatomical"],
                rest_signatures["anatomy"]["sides"]["left"]["forearm_axes_local"],
                elbow_absolute=True)
            right_sem = semantic_math.semantic_features(
                rest_bones, profile_pose, fit.TARGET_PROFILE_RIGHT,
                rest_signatures["anatomy"]["torso_to_anatomical"],
                rest_signatures["anatomy"]["sides"]["right"]["forearm_to_anatomical"],
                rest_signatures["anatomy"]["sides"]["right"]["forearm_axes_local"],
                elbow_absolute=True)
            for side, target_sem in (("left", x_values[sample_index]),
                                     ("right", x_right[sample_index])):
                err = np.asarray((left_sem if side == "left" else right_sem)) - target_sem
                accum["semantic"].extend(np.abs(err).tolist())
                action_result["semantic"].extend(np.abs(err).tolist())
                for i, val in enumerate(err):
                    accum["per_semantic"][side][i].append(float(val))

            predicted_landmarks = landmark_positions(actual, rest_bones, target_world)
            for first, second in (("wrist_main", "glove_wrist"),
                                  ("wrist_right", "glove_wrist_right")):
                accum["post_glove_gap"].append(float(np.linalg.norm(
                    predicted_landmarks[first] - predicted_landmarks[second]) / arm_length_world))
            landmark_values = []
            for name in LANDMARKS["left"] + LANDMARKS["right"]:
                expected_point = np.asarray(sample["branch_landmarks"][name]["world"])
                err = float(np.linalg.norm(predicted_landmarks[name] - expected_point) / arm_length_world)
                landmark_values.append(err)
                accum["per_landmark"].setdefault(name, []).append(err)
            accum["landmark"].extend(landmark_values)
            action_result["landmark"].extend(landmark_values)

            if model_name == "factorized":
                depsgraph = bpy.context.evaluated_depsgraph_get()
                measured = mesh_centroids(mesh_copies, depsgraph,
                    tracked_bones, set(ARM_BONES["left"] + ARM_BONES["right"]))
                centroid_values, centroid_per_bone = centroid_errors(
                    measured, sample, tracked_bones)
            else:
                centroid_values, centroid_per_bone = [], {}
            accum["centroid"].extend(centroid_values)
            action_result["centroid"].extend(centroid_values)
            for bone, vals in centroid_per_bone.items():
                accum["per_centroid_bone"][bone].extend(vals)

            for side in ("left", "right"):
                gap = constraint_info["before"][side]
                correction = constraint_info["correction"][side]
                accum["pre_glove_gap"][side].append(gap / arm_length)
                accum["glove_correction"][side].append(correction / arm_length)
                action_result["max_glove_gap_before"] = max(
                    action_result["max_glove_gap_before"], gap / arm_length)
                action_result["max_attachment_correction"] = max(
                    action_result["max_attachment_correction"], correction / arm_length)
            accum["fist_error"].append(constraint_info["fist_local_max_abs_error"])

            for key, first, second in (
                ("left_23_25", "bone_23", "bone_25"),
                ("left_26_29", "bone_26", "bone_29"),
                ("right_58_60", "bone_58", "bone_60"),
                ("right_61_64", "bone_61", "bone_64")):
                gap = angle_between_rotations(pose[first], pose[second]) - \
                      angle_between_rotations(expected[first], expected[second])
                accum["branches"][key].append(float(gap))

            if model_name == "factorized" and action["action"] == preview_action_name:
                bases = {name: rig.pose.bones[name].matrix_basis.copy()
                         for name in pose if name in rig.pose.bones}
                preview_bases.append((float(sample["time"]), bases))
        action_result = {
            "sample_count": action_result["sample_count"],
            "translation_normalized": aggregate(action_result["translation"]),
            "rotation_rad": aggregate(action_result["rotation"]),
            "semantic_abs_error": aggregate(action_result["semantic"]),
            "landmark_normalized": aggregate(action_result["landmark"]),
            "weighted_centroid_world": aggregate(action_result["centroid"]),
            "max_glove_gap_before_attachment_normalized": action_result["max_glove_gap_before"],
            "max_attachment_correction_normalized": action_result["max_attachment_correction"],
        }
        accum["per_action"][action["action"]] = action_result

    if tuple(float(v) for v in rig.scale) != object_scale:
        raise RuntimeError("evaluation rig object scale changed during pose assignment")
    summary = {
        "mesh_deformation_evaluated": model_name == "factorized",
        "holdout_action_count": len(holdout),
        "holdout_sample_count": sum(len(a["samples"]) for a in holdout),
        "pose_translation_normalized": aggregate(accum["translation"]),
        "pose_rotation_rad": aggregate(accum["rotation"]),
        "semantic_absolute_error": aggregate(accum["semantic"]),
        "landmark_normalized": aggregate(accum["landmark"]),
        "weighted_centroid_world": aggregate(accum["centroid"]),
        "per_bone": {name: {"translation_normalized": aggregate(row["translation"]),
                            "rotation_rad": aggregate(row["rotation"])}
                     for name, row in accum["per_bone"].items()},
        "per_semantic_signed_rmse": {side: {
            fit.SEMANTIC_FEATURE_NAMES[i] if hasattr(fit, "SEMANTIC_FEATURE_NAMES") else
            semantic_math.SEMANTIC_FEATURE_NAMES[i]: float(np.sqrt(np.mean(
                np.square(accum["per_semantic"][side][i]))))
            for i in range(11)} for side in ("left", "right")},
        "per_landmark_normalized": {name: aggregate(values)
                                    for name, values in accum["per_landmark"].items()},
        "per_weighted_centroid_bone_world": {name: aggregate(values)
                                              for name, values in accum["per_centroid_bone"].items()},
        "pre_attachment_glove_gap_normalized": {side: aggregate(values)
            for side, values in accum["pre_glove_gap"].items()},
        "attachment_correction_normalized": {side: aggregate(values)
            for side, values in accum["glove_correction"].items()},
        "post_attachment_glove_gap_max_normalized": float(max(accum["post_glove_gap"], default=0.0)),
        "fixed_fist_local_max_abs_error": float(max(accum["fist_error"], default=0.0)),
        "branch_relative_angle_error_rad": {key: aggregate(values)
                                             for key, values in accum["branches"].items()},
        "per_action": accum["per_action"],
        "worst_action_by_landmark_p95": max(accum["per_action"], key=lambda name:
            accum["per_action"][name]["landmark_normalized"]["p95"]),
        "worst_action_by_centroid_p95": max(accum["per_action"], key=lambda name:
            (accum["per_action"][name]["weighted_centroid_world"]["p95"] or -1.0)),
    }
    return summary, rig, mesh_copies, collection, preview_bases


def gate_result(report, arm_length):
    max_attachment_p95 = max(row["p95"] for row in report[
        "attachment_correction_normalized"].values())
    max_branch_p95 = max(row["p95"] for row in report[
        "branch_relative_angle_error_rad"].values())
    checks = {
        "arm_rotation_rmse_at_most_5_degrees": report["pose_rotation_rad"]["rmse"] <= math.radians(5.0),
        "arm_translation_rmse_at_most_0_02_lengths": report["pose_translation_normalized"]["rmse"] <= 0.02,
        "landmark_p95_at_most_0_02_lengths": report["landmark_normalized"]["p95"] <= 0.02,
        "centroid_p95_at_most_0_02_lengths": (report["weighted_centroid_world"]["p95"] is not None and
            report["weighted_centroid_world"]["p95"] / arm_length <= 0.02),
        "post_attachment_glove_gap_at_most_0_02_lengths": report["post_attachment_glove_gap_max_normalized"] <= 0.02,
        "attachment_correction_p95_at_most_0_02_lengths": max_attachment_p95 <= 0.02,
        "branch_relative_angle_p95_at_most_0_05_rad": max_branch_p95 <= 0.05,
        "fixed_fist_local_error_below_1e-6": report["fixed_fist_local_max_abs_error"] < 1e-6,
        "all_action_landmark_p95_pass": all(row["landmark_normalized"]["p95"] <= 0.02
            for row in report["per_action"].values()),
        "all_action_centroid_p95_pass": all(row["weighted_centroid_world"]["p95"] is not None and
            row["weighted_centroid_world"]["p95"] / arm_length <= 0.02
            for row in report["per_action"].values()),
    }
    return {"passed": all(checks.values()), "checks": checks,
            "failed": [name for name, value in checks.items() if not value]}


def write_preview(filepath, target, rig, meshes, collection, action_name,
                  preview_bases, preview_action_name):
    if not preview_bases:
        raise RuntimeError("requested holdout action has no sampled poses")
    action, slot = make_action(rig, action_name, preview_bases)
    rig.animation_data.action = action
    rig.animation_data.action_slot = slot
    scene = bpy.data.scenes.new("FactorizedNativeHoldoutPreview")
    preview_collection = bpy.data.collections.new("FactorizedNativeHoldout")
    scene.collection.children.link(preview_collection)
    for obj in [rig, *meshes]:
        for old_collection in list(obj.users_collection):
            old_collection.objects.unlink(obj)
        preview_collection.objects.link(obj)
        obj.hide_viewport = False
        obj.hide_render = False
        obj.hide_set(False)
    scene.frame_set(math.floor(preview_bases[0][0]),
        subframe=preview_bases[0][0] - math.floor(preview_bases[0][0]))
    if bpy.context.window is not None:
        bpy.context.window.scene = scene
    bpy.context.view_layer.update()
    visible = [obj for obj in scene.objects if obj.visible_get(view_layer=bpy.context.view_layer)]
    armatures = [obj for obj in visible if obj.type == "ARMATURE"]
    visible_meshes = [obj for obj in visible if obj.type == "MESH"]
    if armatures != [rig] or set(visible_meshes) != set(meshes):
        raise RuntimeError("saved preview must contain exactly one visible rig and its bound meshes")
    if sum(len(pb.constraints) for pb in rig.pose.bones):
        raise RuntimeError("factorized preview unexpectedly contains pose constraints")
    if max(abs(float(a) - float(b)) for a, b in zip(rig.scale, target.scale)) > 1e-8:
        raise RuntimeError("preview object scale differs from source Waluigi")
    os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(filepath))
    return {"blend": os.path.abspath(filepath), "action": action.name,
            "action_slot": slot.identifier, "rig": rig.name,
            "meshes": [obj.name for obj in meshes],
            "visible_armatures": [obj.name for obj in armatures],
            "visible_meshes": [obj.name for obj in visible_meshes],
            "runtime_constraints": 0, "object_scale": [float(v) for v in rig.scale]}


def main():
    args = parse_args()
    with gzip.open(args.dataset, "rt", encoding="utf-8") as stream:
        dataset = json.load(stream)
    if dataset.get("schema") != "mg1.raw-sluggies-native-calibration.v2":
        raise RuntimeError("the factorized fit requires the corrected bilateral raw-ANM v2 dataset")
    actions = dataset["actions"]
    train_actions = [row for row in actions if row["split"] == "train"]
    holdout = [row for row in actions if row["split"] == "holdout"]
    if not train_actions or not holdout or set(a["action"] for a in train_actions) & set(a["action"] for a in holdout):
        raise RuntimeError("invalid action-level train/holdout split")
    target = bpy.data.objects.get(args.target_rig)
    clean = bpy.data.objects.get(args.clean_rig)
    fist_baseline = bpy.data.objects.get(args.fist_baseline)
    walk_action = bpy.data.actions.get(args.fist_action)
    if not target or target.type != "ARMATURE":
        raise RuntimeError("target Waluigi rig is absent")
    if abs(float(target.scale.x) - 43.437) > 0.01:
        raise RuntimeError("protected Waluigi object scale mismatch; refusing evaluation")
    rest_bones = dataset["rig"]["rest_bones"]
    for row in rest_bones.values():
        row["head"] = row.get("rest_head", row.get("head"))
        row["tail"] = row.get("rest_tail", row.get("tail"))
    if set(rest_bones) - set(target.data.bones.keys()):
        raise RuntimeError("dataset profile contains bones missing from target rig")
    for name in rest_bones:
        error = np.max(np.abs(matrix(rest_bones[name]["rest_matrix"]) -
                              np.asarray(target.data.bones[name].matrix_local, dtype=np.float64)))
        if error > 2e-5:
            raise RuntimeError(f"raw dataset rest basis differs from target at {name}: {error:.6g}")
    rest_context = fit.source_rest_from_rig(target)
    arm_length = args.arm_length or float(rest_bones["bone_25"]["rest_length"])
    if arm_length <= 1e-6:
        raise RuntimeError("arm length normalization is invalid")
    root_matrix = matrix(dataset["rig"]["matrix_world"])
    world_scale = float(np.linalg.norm(root_matrix[:3, 0]))
    arm_length_world = arm_length * world_scale
    source_meshes = [obj for obj in bpy.data.objects if obj.type == "MESH" and
        any(mod.type == "ARMATURE" and mod.object == target for mod in obj.modifiers)]
    if not source_meshes and fist_baseline is not None:
        source_meshes = [obj for obj in bpy.data.objects if obj.type == "MESH" and
            any(mod.type == "ARMATURE" and mod.object == fist_baseline for mod in obj.modifiers)]
    if not source_meshes:
        raise RuntimeError("no armature-bound Waluigi preview meshes are available")

    # Capture a canonical fist from the validated baseline on a disposable rig.
    finger_map, parents = capture_fist_locals(bpy.context.scene,
        fist_baseline, walk_action, clean, target)
    if not finger_map:
        raise RuntimeError("validated closed-fist finger transforms could not be captured")

    # Build train-only semantic/pose arrays and holdout semantic inputs.
    fitted = {"factorized": {}, "global_quadratic": {}}
    prediction_maps = {"factorized": {"left": {}, "right": {}},
                       "global_quadratic": {"left": {}, "right": {}}}
    flat_predictions = {"factorized": {}, "global_quadratic": {}}
    support = {"factorized": {}, "global_quadratic": {}}
    contribution_report = {}
    model_selection = {}
    for side in ("left", "right"):
        x_train, y_train, action_ids, _ = make_training_arrays(
            dataset, side, rest_bones, arm_length)
        hold_x = np.asarray([sample["anatomical_semantics"][side]
            for action in holdout for sample in action["samples"]], dtype=np.float64)
        for kind in ("factorized", "global_quadratic"):
            model_fit = fit_model(kind, x_train, y_train, action_ids)
            pred, design_hold = build_holdout_predictions(model_fit, hold_x)
            fitted[kind][side] = model_fit
            flat_predictions[kind][side] = pred
            support[kind][side] = model_math.factor_confidences(x_train, hold_x)
            model_selection.setdefault(kind, {})[side] = {
                "training_actions": len(set(action_ids.tolist())),
                "training_samples": len(x_train),
                "holdout_actions": len(holdout),
                "holdout_samples": len(hold_x),
                "design_dimensions": int(model_fit["coefficients"].shape[0]),
                "selected_ridge_alpha": model_fit["alpha"],
                "action_grouped_cv": model_fit["cross_validation"],
            }
            if kind == "factorized":
                train_design, slices = model_math.factorized_design(x_train)
                contribution_report[side] = model_math.factor_contribution_summary(
                    train_design, model_fit["coefficients"], slices)
        cursor = 0
        for action in holdout:
            count = len(action["samples"])
            for kind in prediction_maps:
                prediction_maps[kind][side][action["action"]] = flat_predictions[kind][side][cursor:cursor + count]
            cursor += count

    preview_action = args.preview_action or max(holdout, key=lambda row: len(row["samples"]))["action"]
    if preview_action not in {row["action"] for row in holdout}:
        raise RuntimeError("preview action must be one of the complete-action holdouts")
    # The exact rest anatomy frames used by the extractor are retained in the dataset metadata.
    anatomy = dataset.get("anatomical_frames")
    if anatomy is None:
        # Re-derive from the exact target rest basis, independently for each side.
        from anatomical_semantic_frames import derive_anatomical_frames
        anatomy = derive_anatomical_frames(rest_context,
            np.asarray(fit.object_transform_matrix(target), dtype=np.float64),
            fit.TARGET_PROFILE, fit.TARGET_PROFILE_RIGHT)

    # Validate the read-only mesh/rig route with a native holdout identity pose before model evaluation.
    factor_rig, factor_meshes, factor_collection = create_eval_bundle(
        target, source_meshes, "_FactorizedEval_identity")
    identity_action = holdout[0]
    identity_sample = identity_action["samples"][0]
    identity_pose = sample_pose(identity_sample, rest_bones)
    roundtrip_errors = []
    for side in ("left", "right"):
        encoded = arm_transform_features(identity_sample, side, rest_bones, arm_length)
        decoded = decode_arm_features(encoded, side, identity_pose[TORSO],
                                      rest_bones, arm_length)
        roundtrip_errors.extend(float(np.max(np.abs(decoded[name] - identity_pose[name])))
                                for name in ARM_BONES[side])
    encode_decode_error = max(roundtrip_errors, default=0.0)
    scene = bpy.context.scene
    scene.frame_set(math.floor(float(identity_sample["time"])),
        subframe=float(identity_sample["time"]) - math.floor(float(identity_sample["time"])))
    identity_actual = set_pose(factor_rig, identity_pose)
    identity_matrix_error = max(float(np.max(np.abs(identity_actual[name] - identity_pose[name])))
        for name in identity_pose if name in identity_actual)
    identity_mesh_bones = list(dict.fromkeys(ARM_BONES["left"] + ARM_BONES["right"] +
        tuple(fit.descendant_bone_names(target.data, "bone_30")) +
        tuple(fit.descendant_bone_names(target.data, "bone_65"))))
    identity_centroids = mesh_centroids(factor_meshes,
        bpy.context.evaluated_depsgraph_get(), identity_mesh_bones,
        set(ARM_BONES["left"] + ARM_BONES["right"]))
    identity_centroid_error, _ = centroid_errors(identity_centroids,
        identity_sample, identity_mesh_bones)
    identity_max_centroid = max(identity_centroid_error, default=0.0)
    remove_eval_bundle(factor_rig, factor_meshes, factor_collection)
    if identity_matrix_error > 3e-4 or identity_max_centroid > 0.02:
        raise RuntimeError("native identity replay through preview mesh binding failed; refusing model evaluation")

    # Both sides are evaluated together; each model has its own independent pose predictor.
    summaries, retained = {}, {}
    for kind in ("factorized", "global_quadratic"):
        result, rig, copies, collection, preview_bases = evaluation_pass(
            kind, fitted[kind], prediction_maps[kind], holdout, dataset,
            target, source_meshes, rest_bones, arm_length, arm_length_world,
            finger_map, parents,
            preview_action, {"anatomy": anatomy})
        summaries[kind] = result
        if kind == "factorized":
            retained[kind] = (rig, copies, collection, preview_bases)
        else:
            remove_eval_bundle(rig, copies, collection)

    factor_gate = gate_result(summaries["factorized"], arm_length_world)
    preview_report = None
    if factor_gate["passed"]:
        rig, copies, collection, bases = retained["factorized"]
        preview_report = write_preview(args.preview, target, rig, copies,
            collection, "Waluigi_FactorizedNative_" + preview_action, bases,
            preview_action)
    else:
        rig, copies, collection, _bases = retained["factorized"]
        remove_eval_bundle(rig, copies, collection)

    report = {
        "schema": "mg1.factorized-native-semantic-model.v1",
        "read_only_source_and_production": True,
        "source": {"dataset": os.path.abspath(args.dataset),
                   "schema": dataset["schema"], "source_authority": dataset["provenance"]["authority"],
                   "training_actions": len(train_actions), "holdout_actions": len(holdout),
                   "training_samples_per_side": {"left": model_selection["factorized"]["left"]["training_samples"],
                                                  "right": model_selection["factorized"]["right"]["training_samples"]},
                   "holdout_samples_per_side": {"left": model_selection["factorized"]["left"]["holdout_samples"],
                                                 "right": model_selection["factorized"]["right"]["holdout_samples"]},
                   "holdout_names": [row["action"] for row in holdout]},
        "target": {"rig": target.name, "rest_basis_verified": True,
                   "object_scale": [float(v) for v in target.scale],
                   "arm_length_armature_units": arm_length,
                   "arm_length_world_units": arm_length_world,
                   "arm_bones": {side: list(names) for side, names in ARM_BONES.items()},
                   "output_dimensions_per_side": 48,
                   "output_coordinates": "torso-rest-frame-relative per-bone rest-relative SE(3); translation normalized by upper-arm length",
                   "fixed_fist_source": {"rig": fist_baseline.name,
                       "action": walk_action.name, "frame": 0,
                       "finger_bone_count": len(finger_map)},
                   "glove_attachment": "hard wrist-tail to glove-root-head correction, moving the glove subtree as one unit"},
        "factorized_model": {"main_factors": list(model_math.FACTOR_GROUPS),
            "within_factor_basis": "linear plus centered quadratic terms",
            "cross_terms": ["shoulder_orientation x elbow_flexion",
                "shoulder_orientation x wrist_orientation",
                "elbow_flexion x wrist_orientation",
                "wrist_orientation x arm_plane_hand_position",
                "elbow_flexion x hand_radial"],
            "coefficient_contribution_summary": contribution_report,
            "factor_and_cross_term_support": support["factorized"]},
        "model_selection": model_selection,
        "comparison": {"global_supervised_inverse": "second-order polynomial ridge over all 11 features; comparative only",
                       "models_evaluated": summaries},
        "identity_replay": {"sample_action": identity_action["action"],
            "sample_time": float(identity_sample["time"]),
            "encode_decode_max_arm_matrix_error": encode_decode_error,
            "max_pose_matrix_error": identity_matrix_error,
            "max_weighted_centroid_error_world": identity_max_centroid},
        "gate": factor_gate,
        "preview": preview_report,
        "solver_or_luigi_fit_run": False,
        "production_mutation": False,
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.report)), exist_ok=True)
    with open(args.report, "w", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"report": os.path.abspath(args.report),
        "gate": factor_gate,
        "factorized_holdout": {key: summaries["factorized"][key]
            for key in ("pose_translation_normalized", "pose_rotation_rad",
                        "semantic_absolute_error", "landmark_normalized",
                        "weighted_centroid_world", "post_attachment_glove_gap_max_normalized")},
        "global_holdout": {key: summaries["global_quadratic"][key]
            for key in ("pose_translation_normalized", "pose_rotation_rad",
                        "semantic_absolute_error", "landmark_normalized",
                        "weighted_centroid_world")},
        "preview": preview_report}, indent=2))


if __name__ == "__main__":
    main()
