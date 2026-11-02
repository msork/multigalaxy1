"""Locally supported native deformation fit for the Luigi walk."""
from __future__ import annotations

import argparse
import gzip
import json
import math
import os
import sys

import bpy
import numpy as np
from mathutils import Matrix, Quaternion, Vector

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import fit_luigi_walk_native_scaffold as scaffold
import fit_luigi_walk_semantic_waluigi as fit
from anatomical_semantic_frames import derive_anatomical_frames
from context_dataset import normalize_context_dataset
from convert_native_owner_actions import evaluated_bone_matrices
from local_native_deformation import fit_local_sequence, joint_temporal_refine
from native_pose_graph import pose_pair_distance


SEMANTIC_NAMES = ("shoulder_rot_x", "shoulder_rot_y", "shoulder_rot_z",
    "elbow_flexion", "forearm_roll", "wrist_rot_x", "wrist_rot_y", "wrist_rot_z",
    "hand_radial", "hand_tangential", "hand_depth")


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--dataset", required=True)
    p.add_argument("--report", required=True)
    p.add_argument("--preview", required=True)
    p.add_argument("--source-rig", default="luigi.001_armature")
    p.add_argument("--source-action", default="luigi.001_armature_walk")
    p.add_argument("--source-slot", default="OBluigi.001_armature")
    p.add_argument("--target-rig", default="123475520_waluigi.gpl")
    p.add_argument("--clean-rig", default="Waluigi_Arm_Clean_Backup")
    p.add_argument("--baseline-rig", default="Waluigi_Arm_Test")
    p.add_argument("--walk-action", default="Waluigi_GalaxyWalk_Legs_Baked")
    p.add_argument("--walk-slot", default="")
    p.add_argument("--mesh-prefix", default="ARMTEST_")
    p.add_argument("--neighbors", type=int, default=96)
    p.add_argument("--local-passes", type=int, default=3)
    p.add_argument("--preview-rig", default="SemanticScaffold_Waluigi_Rig")
    p.add_argument("--preview-collection", default="SemanticScaffold_Waluigi_Preview")
    p.add_argument("--action-name", default="Waluigi_LuigiWalk_LocalNative_v1")
    return p.parse_args(argv)


def pose_feature(sample, bones, torso="bone_21"):
    """Native arm transforms relative to the shared torso, in six SE(3) values."""
    torso_pose = np.asarray(sample["bones"][torso]["armature_pose"], dtype=np.float64)
    inverse_torso = np.linalg.inv(torso_pose)
    features = []
    for name in bones:
        pose = np.asarray(sample["bones"][name]["armature_pose"], dtype=np.float64)
        relative = inverse_torso @ pose
        quaternion = Matrix(relative[:3, :3].tolist()).to_quaternion()
        quaternion.normalize()
        if quaternion.w < 0.0:
            quaternion.negate()
        quat_vector = Vector((quaternion.x, quaternion.y, quaternion.z))
        angle = 2.0 * math.atan2(float(quat_vector.length),
                                 float(quaternion.w))
        axis = quat_vector.normalized() if angle > 1e-10 else Vector((0, 0, 0))
        features.extend(relative[:3, 3].tolist())
        features.extend(float(v) for v in axis * angle)
    return np.asarray(features, dtype=np.float64)


def decode_relative_pose(values, bones, torso_pose):
    result = {}
    values = np.asarray(values, dtype=np.float64)
    for index, name in enumerate(bones):
        row = values[index * 6:index * 6 + 6]
        translation, rotvec = Vector(row[:3].tolist()), Vector(row[3:6].tolist())
        angle = float(rotvec.length)
        rotation = (Quaternion(rotvec / angle, angle) if angle > 1e-10
                    else Quaternion((1.0, 0.0, 0.0, 0.0)))
        delta = Matrix.LocRotScale(translation, rotation, Vector((1.0, 1.0, 1.0)))
        result[name] = scaffold.fit.numpy_matrix(torso_pose) @ np.asarray(
            [[float(delta[r][c]) for c in range(4)] for r in range(4)],
            dtype=np.float64)
    return result


def pose_matrix_feature(pose, bones, torso_name="bone_21"):
    torso_inverse = np.linalg.inv(scaffold.fit.numpy_matrix(pose[torso_name]))
    features = []
    for name in bones:
        relative = torso_inverse @ scaffold.fit.numpy_matrix(pose[name])
        quaternion = Matrix(relative[:3, :3].tolist()).to_quaternion()
        quaternion.normalize()
        if quaternion.w < 0.0:
            quaternion.negate()
        vector = Vector((quaternion.x, quaternion.y, quaternion.z))
        angle = 2.0 * math.atan2(float(vector.length), float(quaternion.w))
        axis = vector.normalized() if angle > 1e-10 else Vector((0, 0, 0))
        features.extend(relative[:3, 3].tolist())
        features.extend(float(v) for v in axis * angle)
    return np.asarray(features, dtype=np.float64)


def encode_final_pose(pose, bones, rests, length_scale):
    output = []
    for name in bones:
        relative = (np.linalg.inv(scaffold.fit.numpy_matrix(
            rests[name]["rest_matrix"])) @ scaffold.fit.numpy_matrix(pose[name]))
        quaternion = Matrix(relative[:3, :3].tolist()).to_quaternion()
        quaternion.normalize()
        if quaternion.w < 0.0:
            quaternion.negate()
        quat_vector = Vector((quaternion.x, quaternion.y, quaternion.z))
        angle = 2.0 * math.atan2(float(quat_vector.length),
                                 float(quaternion.w))
        axis = quat_vector.normalized() if angle > 1e-10 else Vector((0, 0, 0))
        output.extend((relative[:3, 3] / length_scale).tolist())
        output.extend(float(v) for v in axis * angle)
    return np.asarray(output, dtype=np.float64)


def decode_final_pose(values, bones, rests, length_scale):
    values = np.asarray(values, dtype=np.float64)
    output = {}
    for index, name in enumerate(bones):
        row = values[index * 6:index * 6 + 6]
        translation = Vector((row[:3] * length_scale).tolist())
        rotvec = Vector(row[3:6].tolist())
        angle = float(rotvec.length)
        rotation = (Quaternion(rotvec / angle, angle) if angle > 1e-10
                    else Quaternion((1.0, 0.0, 0.0, 0.0)))
        delta = Matrix.LocRotScale(translation, rotation, Vector((1.0, 1.0, 1.0)))
        output[name] = scaffold.fit.numpy_matrix(rests[name]["rest_matrix"]) @ np.asarray(
            [[float(delta[r][c]) for c in range(4)] for r in range(4)],
            dtype=np.float64)
    return output


def apply_fixed_fingers(pose, finger_relatives, parent_names):
    depths = {}
    def depth(name):
        value, parent = 0, parent_names.get(name)
        while parent is not None:
            value += 1
            parent = parent_names.get(parent)
        return value
    for name in finger_relatives:
        depths[name] = depth(name)
    for name in sorted(finger_relatives, key=lambda item: depths[item]):
        parent = parent_names.get(name)
        if parent not in pose:
            continue
        pose[name] = scaffold.fit.numpy_matrix(pose[parent]) @ finger_relatives[name]


def make_pose_evaluator(left_rows, right_rows, left_bones, right_bones,
                        rests, baseline_rows, anatomy, finger_relatives,
                        parent_names):
    def evaluate(_unused, frame, return_pose=False):
        final = {name: scaffold.fit.numpy_matrix(value).copy()
                 for name, value in baseline_rows[frame].items()}
        torso = final[fit.TARGET_PROFILE["torso"]]
        final.update(decode_relative_pose(left_rows[frame], left_bones, torso))
        final.update(decode_relative_pose(right_rows[frame], right_bones, torso))
        apply_fixed_fingers(final, finger_relatives, parent_names)
        enforce_glove_attachment(final, rests, parent_names)
        left_sem, right_sem = scaffold.target_profile_semantics(
            final, rests, anatomy)
        semantics = np.r_[left_sem, right_sem]
        return (semantics, final, 0.0) if return_pose else semantics
    return evaluate


def enforce_glove_attachment(pose, rests, parent_names):
    """Hard-constrain each glove root head to the native wrist endpoint."""
    def endpoint(name, which):
        rest = scaffold.fit.numpy_matrix(rests[name]["rest_matrix"])
        point = np.r_[rests[name][which], 1.0]
        return (scaffold.fit.numpy_matrix(pose[name]) @ np.linalg.inv(rest) @ point)[:3]
    for wrist, glove in (("bone_27", "bone_30"), ("bone_62", "bone_65")):
        delta = endpoint(wrist, "tail") - endpoint(glove, "head")
        descendants = {glove}
        changed = True
        while changed:
            changed = False
            for name, parent in parent_names.items():
                if parent in descendants and name not in descendants:
                    descendants.add(name)
                    changed = True
        for name in descendants:
            if name in pose:
                pose[name] = scaffold.fit.numpy_matrix(pose[name]).copy()
                pose[name][:3, 3] += delta


def feature_rmse(source, target):
    labels = {"shoulder_orientation": (0, 3), "elbow_flexion": (3, 4),
              "forearm_roll": (4, 5), "wrist_orientation": (5, 8),
              "arm_plane_hand_position": (8, 11)}
    result = {}
    for label, (start, stop) in labels.items():
        result[label] = {
            "left": float(np.sqrt(np.mean((source[:, start:stop] -
                                            target[:, start:stop]) ** 2))),
            "right": float(np.sqrt(np.mean((source[:, 11 + start:11 + stop] -
                                             target[:, 11 + start:11 + stop]) ** 2))),
        }
    return result


def validate_reopened_action(filepath, rig_name, intended_rows):
    """Reopen a disposable candidate blend and compare every saved pose matrix."""
    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(filepath))
    rig = bpy.data.objects.get(rig_name)
    if rig is None or rig.type != "ARMATURE" or not rig.animation_data or not rig.animation_data.action:
        return {"passed": False, "reason": "reopened target/action is missing"}
    action_name = rig.animation_data.action.name
    scene = bpy.context.scene
    max_error, worst = 0.0, None
    for frame, intended in enumerate(intended_rows):
        scene.frame_set(frame)
        actual = evaluated_bone_matrices(rig)
        for name, expected in intended.items():
            error = fit.matrix_error(actual[name], expected)
            if error > max_error:
                max_error = error
                worst = {"frame": frame, "bone": name, "matrix_error": error}
    return {"passed": bool(max_error <= 2e-4),
            "max_matrix_error": float(max_error), "worst": worst,
            "action": action_name, "frame_count": len(intended_rows),
            "tolerance": 2e-4}


def excursion(source, target):
    result = {}
    for side, offset in (("left", 0), ("right", 11)):
        a, b = source[:, offset:offset + 11], target[:, offset:offset + 11]
        shoulder_a = float(np.max(np.linalg.norm(a[:, :3] - a[0, :3], axis=1)))
        shoulder_b = float(np.max(np.linalg.norm(b[:, :3] - b[0, :3], axis=1)))
        wrist_a = float(np.max(np.linalg.norm(a[:, 5:8] - a[0, 5:8], axis=1)))
        wrist_b = float(np.max(np.linalg.norm(b[:, 5:8] - b[0, 5:8], axis=1)))
        elbow_a, elbow_b = float(np.ptp(a[:, 3])), float(np.ptp(b[:, 3]))
        result[side] = {
            "shoulder_percent": 100.0 * shoulder_b / max(shoulder_a, 1e-8),
            "elbow_percent": 100.0 * elbow_b / max(elbow_a, 1e-8),
            "wrist_percent": 100.0 * wrist_b / max(wrist_a, 1e-8),
            "source_elbow_range": [float(a[:, 3].min()), float(a[:, 3].max())],
            "target_elbow_range": [float(b[:, 3].min()), float(b[:, 3].max())],
        }
    return result


def main():
    args = parse_args()
    if args.neighbors != 96:
        raise RuntimeError("this milestone requires exactly 96 local native neighbors per side and frame")
    with gzip.open(args.dataset, "rt", encoding="utf-8") as stream:
        dataset = normalize_context_dataset(json.load(stream))
    source = bpy.data.objects.get(args.source_rig)
    production = bpy.data.objects.get(args.target_rig)
    clean = bpy.data.objects.get(args.clean_rig)
    baseline = bpy.data.objects.get(args.baseline_rig)
    source_action = bpy.data.actions.get(args.source_action)
    walk_action = bpy.data.actions.get(args.walk_action)
    if any(value is None for value in (source, production, clean, baseline,
                                        source_action, walk_action)):
        raise RuntimeError("required source rigs/actions are absent")
    if abs(float(production.scale.x) - 43.437) > 0.01:
        raise RuntimeError("production Waluigi scale changed; refusing to fit")
    source_slots = [slot for slot in source_action.slots
                    if slot.identifier == args.source_slot]
    if len(source_slots) != 1:
        raise RuntimeError("source action slot is not unique")
    walk_slot = fit.select_walk_slot(walk_action, args.walk_slot,
                                     production.data.bones.keys())
    bones = list(dataset["bones"])
    rests = fit.source_rest_from_rig(production)
    source_rest = fit.source_rest_from_rig(source)
    source_anatomy = derive_anatomical_frames(
        source_rest, fit.numpy_matrix(fit.object_transform_matrix(source)),
        fit.SOURCE_PROFILE, fit.SOURCE_PROFILE_RIGHT)
    target_anatomy = derive_anatomical_frames(
        rests, fit.numpy_matrix(fit.object_transform_matrix(production)),
        fit.TARGET_PROFILE, fit.TARGET_PROFILE_RIGHT)
    source_rows, source_left, source_right = scaffold.load_source_rows(
        bpy.context.scene, source, source_action, source_slots[0],
        source_rest, source_anatomy)
    source_features = np.column_stack((source_left, source_right))
    baseline_rows, _root = scaffold.build_baseline_rows(
        bpy.context.scene, clean, baseline, walk_action, walk_slot)
    left_bones = ["bone_22", "bone_23", "bone_24", "bone_25",
                  "bone_26", "bone_27", "bone_29", "bone_30"]
    right_bones = [fit.LEFT_TO_RIGHT_BONES[name] for name in left_bones]
    native_samples, native_actions = [], []
    native_left_semantics, native_right_semantics = [], []
    for action in dataset["actions"]:
        if action["split"] != "train":
            continue
        for sample_index, sample in enumerate(action["samples"]):
            pose = scaffold.load_pose(sample, bones)
            left_sem, right_sem = scaffold.target_profile_semantics(
                pose, rests, target_anatomy)
            native_samples.append(sample)
            native_left_semantics.append(left_sem)
            native_right_semantics.append(right_sem)
            native_actions.append({"action": action["action"],
                                   "time": float(sample["normalized_time"]),
                                   "sample_index": sample_index})
    native_left_semantics = np.asarray(native_left_semantics, dtype=np.float64)
    native_right_semantics = np.asarray(native_right_semantics, dtype=np.float64)
    x_train_left = np.asarray([pose_feature(sample, left_bones)
                               for sample in native_samples], dtype=np.float64)
    x_train_right = np.asarray([pose_feature(sample, right_bones)
                                for sample in native_samples], dtype=np.float64)
    left_pose_scale = np.maximum(x_train_left.std(axis=0), 0.01)
    right_pose_scale = np.maximum(x_train_right.std(axis=0), 0.01)
    left_features, left_info = fit_local_sequence(
        native_left_semantics, x_train_left, source_features[:, :11],
        np.maximum(native_left_semantics.std(axis=0), 0.05),
        k=args.neighbors, passes=args.local_passes, pose_scale=left_pose_scale)
    right_features, right_info = fit_local_sequence(
        native_right_semantics, x_train_right, source_features[:, 11:],
        np.maximum(native_right_semantics.std(axis=0), 0.05),
        k=args.neighbors, passes=args.local_passes, pose_scale=right_pose_scale)

    scene = bpy.context.scene
    old_frame, old_sub = scene.frame_current, scene.frame_subframe
    scene.frame_set(0)
    bpy.context.view_layer.update()
    evaluated_baseline = baseline.evaluated_get(bpy.context.evaluated_depsgraph_get())
    left_fingers = fit.descendant_bone_names(baseline.data, "bone_30")
    right_fingers = fit.descendant_bone_names(baseline.data, "bone_65")
    finger_names = left_fingers | right_fingers
    closed_basis = {name: evaluated_baseline.pose.bones[name].matrix_basis.copy()
                    for name in finger_names}
    closed_pose = {name: fit.numpy_matrix(matrix)
                   for name, matrix in evaluated_bone_matrices(baseline).items()}
    scene.frame_set(old_frame, subframe=old_sub)
    bpy.context.view_layer.update()
    parent_names = {bone.name: bone.parent.name if bone.parent else None
                    for bone in production.data.bones}
    finger_relatives = {}
    for name in finger_names:
        parent_name = parent_names[name]
        if parent_name:
            finger_relatives[name] = np.linalg.inv(closed_pose[parent_name]) @ closed_pose[name]
    evaluate = make_pose_evaluator(left_features, right_features,
        left_bones, right_bones, rests, baseline_rows, target_anatomy,
        finger_relatives, parent_names)
    pose_rows, predicted_exact = [], []
    for frame in range(61):
        sem, pose, _root_delta = evaluate(None, frame, return_pose=True)
        pose_rows.append(pose)
        predicted_exact.append(sem)
    pre_temporal_semantics = np.asarray(predicted_exact, dtype=np.float64)
    pre_temporal_rmse = feature_rmse(source_features, pre_temporal_semantics)
    modeled_bones = left_bones + right_bones
    arm_length = max(float(rests["bone_25"]["length"]), 1e-6)
    initial_final_se3 = np.asarray([encode_final_pose(
        pose, modeled_bones, rests, arm_length) for pose in pose_rows])
    trust_radii = np.asarray([min(0.35, max(0.08,
        min(left_info[i]["local_pose_trust_radius"],
            right_info[i]["local_pose_trust_radius"]))) for i in range(61)])
    joint_final_se3, temporal_objective = joint_temporal_refine(
        initial_final_se3, feature_scale=np.ones(initial_final_se3.shape[1]),
        lambda_velocity=0.30, lambda_acceleration=0.90,
        trust_radii=trust_radii, iterations=120)
    for frame in range(61):
        pose_rows[frame].update(decode_final_pose(
            joint_final_se3[frame], modeled_bones, rests, arm_length))
        apply_fixed_fingers(pose_rows[frame], finger_relatives, parent_names)
        enforce_glove_attachment(pose_rows[frame], rests, parent_names)
        left_sem, right_sem = scaffold.target_profile_semantics(
            pose_rows[frame], rests, target_anatomy)
        predicted_exact[frame] = np.r_[left_sem, right_sem]
    predicted_exact = np.asarray(predicted_exact)
    def pose_support(side_bones, features, infos, pose_scale):
        failures = []
        max_trust_deviation = 0.0
        for frame, pose in enumerate(pose_rows):
            values = pose_matrix_feature(pose, side_bones)
            info = infos[frame]
            low = np.asarray(info["pose_support_low"], dtype=np.float64)
            high = np.asarray(info["pose_support_high"], dtype=np.float64)
            outside = np.flatnonzero((values < low - 1e-4) | (values > high + 1e-4))
            deviation = float(np.linalg.norm((values - features[frame]) / pose_scale))
            max_trust_deviation = max(max_trust_deviation, deviation)
            if len(outside):
                failures.append({"frame": frame, "unsupported_pose_channels": outside.tolist()})
            if deviation > float(info["local_pose_trust_radius"]) + 1e-5:
                failures.append({"frame": frame, "trust_radius_exceeded": deviation,
                                 "trust_radius": float(info["local_pose_trust_radius"])})
        return {"passed": not failures, "failure_count": len(failures),
                "first_failure": failures[0] if failures else None,
                "max_trust_deviation": max_trust_deviation}
    left_pose_support = pose_support(left_bones, left_features, left_info, left_pose_scale)
    right_pose_support = pose_support(right_bones, right_features, right_info, right_pose_scale)
    all_bones = sorted(set(modeled_bones) | set(finger_names))
    rest_inv = {name: np.linalg.inv(fit.numpy_matrix(rests[name]["rest_matrix"]))
                for name in all_bones}
    descriptors = np.asarray([fit.pose_quaternion_descriptor(
        pose, all_bones, rest_inv, arm_length) for pose in pose_rows])
    transition_rows = []
    for frame in range(60):
        maximum, rms, per_bone = pose_pair_distance(descriptors[frame],
                                                     descriptors[frame + 1])
        transition_rows.append({"from_frame": frame, "to_frame": frame + 1,
            "max_normalized_se3": maximum, "rms_normalized_se3": rms,
            "worst_bone": all_bones[int(np.argmax(per_bone))]})
    worst_jump = max(transition_rows, key=lambda row: row["max_normalized_se3"])
    left_gaps, right_gaps, fist_errors = [], [], []
    for pose in pose_rows:
        left_gaps.append(fit.context_joint_gaps(pose, rests, fit.GAP_PAIRS))
        right_gaps.append(fit.context_joint_gaps(pose, rests, fit.RIGHT_GAP_PAIRS))
        for name, relative in finger_relatives.items():
            parent_name = parent_names[name]
            actual = np.linalg.inv(pose[parent_name]) @ pose[name]
            fist_errors.append(fit.matrix_error(actual, relative))
    glove_gap = max(max(row[3] for row in left_gaps),
                    max(row[3] for row in right_gaps))
    finger_local_error = max(fist_errors, default=0.0)
    rmse = feature_rmse(source_features, predicted_exact)
    retention = excursion(source_features, predicted_exact)
    shoulder_rmse = max(rmse["shoulder_orientation"].values())
    elbow_rmse = max(rmse["elbow_flexion"].values())
    wrist_rmse = max(rmse["wrist_orientation"].values())
    elbow_envelope_ok = True
    for offset in (0, 11):
        low = float(source_features[:, offset + 3].min()) - 0.02
        high = float(source_features[:, offset + 3].max()) + 0.02
        elbow_envelope_ok &= bool(np.all(predicted_exact[:, offset + 3] >= low) and
                                  np.all(predicted_exact[:, offset + 3] <= high))
    shoulder_envelope_ok = True
    for offset in (0, 11):
        low = source_features[:, offset:offset + 3].min(axis=0) - 0.05
        high = source_features[:, offset:offset + 3].max(axis=0) + 0.05
        shoulder_envelope_ok &= bool(np.all(predicted_exact[:, offset:offset + 3] >= low) and
                                     np.all(predicted_exact[:, offset:offset + 3] <= high))
    excursion_ok = all(80.0 <= retention[side][kind] <= 120.0
        for side in ("left", "right") for kind in
        ("shoulder_percent", "elbow_percent", "wrist_percent"))
    locally_supported = (
        all(bool(row["supported"]) for row in left_info + right_info) and
        left_pose_support["passed"] and right_pose_support["passed"])
    semantic_rmse_ok = shoulder_rmse < 0.18 and elbow_rmse < 0.10 and wrist_rmse < 0.20
    semantic_tolerances = np.asarray((0.15, 0.15, 0.15, 0.08, 0.15,
                                      0.15, 0.15, 0.15, 0.08, 0.08, 0.08))
    semantic_max_error = np.max(np.abs(predicted_exact - source_features), axis=0)
    all_semantics_supported = bool(np.all(semantic_max_error <=
        np.r_[semantic_tolerances, semantic_tolerances]))
    gate = {
        "semantic_excursions_80_to_120_percent": bool(excursion_ok),
        "shoulder_source_envelope": bool(shoulder_envelope_ok),
        "elbow_source_rest_calibrated_range": bool(elbow_envelope_ok),
        "semantic_rmse_limits": bool(semantic_rmse_ok),
        "all_semantic_dimensions_within_coverage_tolerance": all_semantics_supported,
        "both_fists_locked": bool(finger_local_error < 1e-6),
        "max_pose_jump_below_0_5": bool(worst_jump["max_normalized_se3"] < 0.5),
        "glove_gap_below_0_02": bool(glove_gap < 0.02),
        "no_unsupported_local_extrapolation": bool(locally_supported),
    }
    pre_preview_gate_passed = all(gate.values())
    meshes = sorted((obj for obj in bpy.data.objects if obj.type == "MESH" and
        obj.name.startswith(args.mesh_prefix) and any(
        mod.type == "ARMATURE" and mod.object == baseline for mod in obj.modifiers)),
        key=lambda obj: obj.name)
    if not meshes:
        raise RuntimeError("validated walk baseline meshes are missing")
    preview = None
    reopen_validation = None
    if pre_preview_gate_passed:
        candidate_path = os.path.abspath(args.preview + ".candidate.blend")
        preview = scaffold.write_preview(candidate_path, args.action_name,
            clean, baseline, baseline_rows, pose_rows, meshes, walk_action,
            walk_slot, closed_fist_basis=closed_basis)
        reopen_validation = validate_reopened_action(
            candidate_path, preview["rig"], pose_rows)
        gate["saved_reopened_action_reproduces_fit"] = bool(reopen_validation["passed"])
        if gate["saved_reopened_action_reproduces_fit"]:
            os.replace(candidate_path, os.path.abspath(args.preview))
            preview["path"] = os.path.abspath(args.preview)
        else:
            if os.path.isfile(candidate_path):
                os.remove(candidate_path)
            preview = None
    else:
        gate["saved_reopened_action_reproduces_fit"] = False
        reopen_validation = {"passed": False,
            "not_run_reason": "prior numerical gates failed; no preview candidate was created"}
    gate_passed = all(gate.values())
    semantic_error_rows = []
    error_matrix = np.abs(predicted_exact - source_features)
    for index, name in enumerate(SEMANTIC_NAMES):
        for side, offset in (("left", 0), ("right", 11)):
            column = offset + index
            frame = int(np.argmax(error_matrix[:, column]))
            semantic_error_rows.append({"side": side, "feature": name,
                "frame": frame, "absolute_error": float(error_matrix[frame, column]),
                "source_value": float(source_features[frame, column]),
                "target_value": float(predicted_exact[frame, column]),
                "coverage_tolerance": float(semantic_tolerances[index])})
    report = {
        "schema": "mg1.semantic-walk-local-native.v1",
        "source": {"rig": source.name, "action": source_action.name,
                   "slot": source_slots[0].identifier, "frames": [0, 60]},
        "target_model": {"type": "independent bilateral local affine raw-ANM deformation models",
                         "training_samples_per_side": len(native_samples),
                         "source_data": "real bilateral raw-ANM samples; right arm is never reflected from left",
                         "modeled_bones": {"left": left_bones, "right": right_bones},
                         "pose_feature_dimensions_per_side": int(len(left_bones) * 6),
                         "neighbors_per_frame": args.neighbors,
                         "neighborhood_passes": args.local_passes},
        "semantic_targets": {"features": list(SEMANTIC_NAMES),
                             "coordinate_frame": "rest-calibrated character-neutral anatomical frames",
                             "source_ranges": {
                                 "left": {"min": source_features[:, :11].min(axis=0).tolist(),
                                          "max": source_features[:, :11].max(axis=0).tolist()},
                                 "right": {"min": source_features[:, 11:].min(axis=0).tolist(),
                                           "max": source_features[:, 11:].max(axis=0).tolist()}},
                             "pre_temporal_rmse": pre_temporal_rmse,
                             "rmse": rmse, "max_abs_error_by_dimension": {
                                 "left": semantic_max_error[:11].tolist(),
                                 "right": semantic_max_error[11:].tolist()},
                             "worst_residuals": sorted(semantic_error_rows,
                                 key=lambda row: row["absolute_error"], reverse=True)[:8],
                             "excursion_retention": retention},
        "local_support": {side: [{
            "frame": frame,
            "nearest_semantic_distance": row["nearest_semantic_distance"],
            "local_semantic_radius_90": row["local_semantic_radius_90"],
            "inside_local_semantic_box": row["inside_local_semantic_box"],
            "unsupported_semantic_features": [SEMANTIC_NAMES[i] for i in
                row["unsupported_semantic_axes"]],
            "supported": row["supported"],
            "local_pose_trust_radius": row["local_pose_trust_radius"],
            "predicted_pose_distance": row["predicted_pose_distance"],
            "neighbor_count": len(row["neighbors"]),
            "neighbor_actions": sorted({native_actions[int(i)]["action"]
                                         for i in row["neighbors"]})
            } for frame, row in enumerate(rows)]
            for side, rows in (("left", left_info), ("right", right_info))},
        "post_refinement_pose_support": {"left": left_pose_support,
                                         "right": right_pose_support},
        "optimization": {"method": "independent 96-neighbor local affine maps followed by one joint 61-frame quadratic objective on composed final arm SE(3) poses",
                         "temporal_penalties": temporal_objective,
                         "post_fit_smoothing": False},
        "fist_lock": {"source_rig": baseline.name, "sample_frame": 0,
            "left_finger_count": len(left_fingers),
            "right_finger_count": len(right_fingers),
            "max_frame_local_transform_error": finger_local_error},
        "continuity": {"max_final_pose_jump": worst_jump,
                       "mean_rms_jump": float(np.mean([r["rms_normalized_se3"] for r in transition_rows])),
                       "max_glove_gap_armature_units": float(glove_gap)},
        "gate": {"checks": gate, "passed": gate_passed,
                 "failed_checks": [name for name, passed in gate.items() if not passed],
                 "reopen_validation": reopen_validation},
        "preview": preview,
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.report)), exist_ok=True)
    with open(args.report, "w", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
    print(json.dumps({"report": os.path.abspath(args.report),
        "gate": gate, "semantic_rmse": rmse, "excursion_retention": retention,
        "max_pose_jump": worst_jump, "max_glove_gap": glove_gap,
        "fist_local_error": finger_local_error, "preview": preview}, indent=2))


if __name__ == "__main__":
    main()
