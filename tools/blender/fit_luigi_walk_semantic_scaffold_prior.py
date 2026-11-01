"""Joint semantic arm fit with the Running action used only as a soft prior."""
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
from fit_native_context_pca import encode_sample, fit as fit_pca
from native_pose_graph import pose_pair_distance


FINGER_ROOTS = ("bone_30", "bone_65")
SEMANTIC_WEIGHTS = np.asarray(
    [8.0] * 3 + [12.0] + [3.0] + [6.0] * 3 + [4.0] * 3,
    dtype=np.float64)
SEMANTIC_NAMES = ("shoulder_x", "shoulder_y", "shoulder_z",
    "elbow_flexion", "forearm_roll", "wrist_x", "wrist_y", "wrist_z",
    "hand_x", "hand_y", "hand_z")


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--dataset", required=True)
    p.add_argument("--report", required=True)
    p.add_argument("--preview", required=True)
    p.add_argument("--prior-report", default="tools/blender/reports/luigi_walk_native_scaffold_v1.json")
    p.add_argument("--source-rig", default="luigi.001_armature")
    p.add_argument("--source-action", default="luigi.001_armature_walk")
    p.add_argument("--source-slot", default="OBluigi.001_armature")
    p.add_argument("--target-rig", default="123475520_waluigi.gpl")
    p.add_argument("--clean-rig", default="Waluigi_Arm_Clean_Backup")
    p.add_argument("--baseline-rig", default="Waluigi_Arm_Test")
    p.add_argument("--walk-action", default="Waluigi_GalaxyWalk_Legs_Baked")
    p.add_argument("--walk-slot", default="")
    p.add_argument("--scaffold-action", default="Waluigi_8_Running_seq10")
    p.add_argument("--mesh-prefix", default="ARMTEST_")
    p.add_argument("--latent-dimensions", type=int, default=24)
    p.add_argument("--iterations", type=int, default=14)
    p.add_argument("--preview-rig", default="SemanticScaffold_Waluigi_Rig")
    p.add_argument("--preview-collection", default="SemanticScaffold_Waluigi_Preview")
    p.add_argument("--action-name", default="Waluigi_LuigiWalk_SemanticScaffoldPrior_v1")
    return p.parse_args(argv)


def pose_feature(sample, bones):
    return np.asarray(encode_sample(sample, bones), dtype=np.float64)


def decode_pose(z, model, bones, rests):
    mean, scale, components = model
    feature = mean + (np.asarray(z, dtype=np.float64) @ components) * scale
    output = {}
    for index, name in enumerate(bones):
        row = feature[index * 6:index * 6 + 6]
        translation = Vector(row[:3].tolist())
        rotvec = Vector(row[3:6].tolist())
        angle = float(rotvec.length)
        rotation = (Quaternion(rotvec / angle, angle) if angle > 1e-10
                    else Quaternion((1.0, 0.0, 0.0, 0.0)))
        delta = Matrix.LocRotScale(translation, rotation, Vector((1.0, 1.0, 1.0)))
        output[name] = scaffold.fit.numpy_matrix(rests[name]["rest_matrix"]) @ np.asarray(
            [[float(delta[r][c]) for c in range(4)] for r in range(4)],
            dtype=np.float64)
    return output


def matrix_local(parent_pose, child_pose):
    return np.linalg.inv(scaffold.fit.numpy_matrix(parent_pose)) @ scaffold.fit.numpy_matrix(child_pose)


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


def make_pose_evaluator(model, bones, rests, baseline_rows, target_rest,
                        target_rest_all, target_original, anatomy,
                        finger_relatives, parent_names, right_subtree,
                        right_fingers):
    all_arm = set(bones) - fit.TORSO_CONTEXT
    semantic_names = {"bone_22", "bone_23", "bone_24", "bone_25",
                      "bone_26", "bone_27", "bone_29", "bone_30"}
    right_semantic = {fit.LEFT_TO_RIGHT_BONES[name] for name in semantic_names}

    def evaluate(z, frame, return_pose=False):
        left_pose = decode_pose(z, model, bones, rests)
        final, root_delta = fit.compose_final_semantic_pose(
            baseline_rows[frame], left_pose, left_pose,
            target_rest=target_rest, target_rest_all=target_rest_all,
            arm_bones=all_arm, torso_context=fit.TORSO_CONTEXT,
            semantic_arm_names=semantic_names,
            left_to_right=fit.LEFT_TO_RIGHT_BONES,
            right_subtree=right_subtree, right_finger_names=right_fingers,
            parent_by_bone=parent_names, right_root_name="bone_57",
            torso_name=fit.TARGET_PROFILE["torso"])
        apply_fixed_fingers(final, finger_relatives, parent_names)
        left_sem, right_sem = scaffold.target_profile_semantics(
            final, target_rest_all, anatomy)
        semantics = np.r_[left_sem, right_sem]
        return (semantics, final, root_delta) if return_pose else semantics
    return evaluate


def temporal_penalty_gradient(z, prior, lambda_anchor=0.025,
                              lambda_velocity=0.35,
                              lambda_acceleration=0.12):
    grad = 2.0 * lambda_anchor * (z - prior)
    hessian_scale = np.full(len(z), 2.0 * lambda_anchor, dtype=np.float64)
    velocity_residual = np.diff(z, axis=0) - np.diff(prior, axis=0)
    for t, residual in enumerate(velocity_residual):
        value = 2.0 * lambda_velocity * residual
        grad[t] -= value
        grad[t + 1] += value
        hessian_scale[t] += 2.0 * lambda_velocity
        hessian_scale[t + 1] += 2.0 * lambda_velocity
    accel_residual = np.diff(z, n=2, axis=0) - np.diff(prior, n=2, axis=0)
    for t, residual in enumerate(accel_residual, start=1):
        value = 2.0 * lambda_acceleration * residual
        grad[t - 1] += value
        grad[t] -= 2.0 * value
        grad[t + 1] += value
        hessian_scale[t - 1] += 2.0 * lambda_acceleration
        hessian_scale[t] += 8.0 * lambda_acceleration
        hessian_scale[t + 1] += 2.0 * lambda_acceleration
    return grad, hessian_scale


def fit_sequence(prior, source, evaluator, semantic_scale, source_ranges,
                 iterations, dimensions):
    z = np.asarray(prior, dtype=np.float64).copy()
    scale = np.r_[semantic_scale, semantic_scale]
    group_weights = np.r_[SEMANTIC_WEIGHTS, SEMANTIC_WEIGHTS]
    feature_weight = group_weights / np.maximum(scale, 0.05)
    weight_squared = feature_weight * feature_weight
    shoulder_low = np.r_[source_ranges["left"]["shoulder_min"],
                         source_ranges["right"]["shoulder_min"]]
    shoulder_high = np.r_[source_ranges["left"]["shoulder_max"],
                          source_ranges["right"]["shoulder_max"]]
    elbow_low = np.asarray([source_ranges["left"]["elbow_min"],
                            source_ranges["right"]["elbow_min"]])
    elbow_high = np.asarray([source_ranges["left"]["elbow_max"],
                             source_ranges["right"]["elbow_max"]])
    shoulder_margin = np.r_[np.maximum(0.025, 0.10 * (shoulder_high[:3] - shoulder_low[:3])),
                            np.maximum(0.025, 0.10 * (shoulder_high[3:] - shoulder_low[3:]))]
    elbow_margin = 0.02
    history = []
    for iteration in range(iterations):
        predicted = np.asarray([evaluator(z[t], t) for t in range(len(z))])
        residual = predicted - source
        gradients = np.zeros_like(z)
        hessians = []
        for t in range(len(z)):
            jacobian = np.empty((22, dimensions), dtype=np.float64)
            epsilon = 0.003
            for dim in range(dimensions):
                plus, minus = z[t].copy(), z[t].copy()
                plus[dim] += epsilon
                minus[dim] -= epsilon
                jacobian[:, dim] = (evaluator(plus, t) -
                                    evaluator(minus, t)) / (2.0 * epsilon)
            weighted_j = jacobian * weight_squared[:, None]
            hessian = jacobian.T @ weighted_j
            gradients[t] = jacobian.T @ (weight_squared * residual[t])
            # Hinge penalties keep shoulder components and elbow bends within
            # the measured Luigi envelope even when the latent manifold cannot
            # meet a per-frame semantic target exactly.
            for side_index in range(2):
                offset = side_index * 11
                for component in range(3):
                    feature = offset + component
                    lower = shoulder_low[side_index * 3 + component] - shoulder_margin[
                        side_index * 3 + component]
                    upper = shoulder_high[side_index * 3 + component] + shoulder_margin[
                        side_index * 3 + component]
                    value = predicted[t, feature]
                    bound_residual = value - lower if value < lower else (
                        value - upper if value > upper else 0.0)
                    if bound_residual:
                        weight = 18.0 / max(scale[component], 0.05)
                        row = jacobian[feature]
                        gradients[t] += (weight * weight * bound_residual) * row
                        hessian += (weight * weight) * np.outer(row, row)
                feature = offset + 3
                value = predicted[t, feature]
                lower = elbow_low[side_index] - elbow_margin
                upper = elbow_high[side_index] + elbow_margin
                bound_residual = value - lower if value < lower else (
                    value - upper if value > upper else 0.0)
                if bound_residual:
                    weight = 24.0 / max(scale[3], 0.05)
                    row = jacobian[feature]
                    gradients[t] += (weight * weight * bound_residual) * row
                    hessian += (weight * weight) * np.outer(row, row)
            hessians.append(hessian)
        prior_grad, prior_hdiag = temporal_penalty_gradient(z, prior)
        gradients += prior_grad
        for t in range(len(z)):
            hessians[t] += np.eye(dimensions) * prior_hdiag[t]
            hessians[t] += np.eye(dimensions) * 0.08
        delta = np.asarray([-np.linalg.solve(hessians[t], gradients[t])
                            for t in range(len(z))])
        norms = np.linalg.norm(delta, axis=1)
        factors = np.minimum(1.0, 0.8 / np.maximum(norms, 1e-12))
        z += delta * factors[:, None]
        history.append({
            "iteration": iteration,
            "semantic_weighted_rmse": float(np.sqrt(np.mean(
                (residual * feature_weight[None, :]) ** 2))),
            "latent_delta_rms": float(np.sqrt(np.mean(delta * delta))),
            "latent_offset_from_scaffold_rms": float(np.sqrt(np.mean((z - prior) ** 2))),
        })
    predicted = np.asarray([evaluator(z[t], t) for t in range(len(z))])
    return z, predicted, history


def feature_rmse(source, target):
    labels = {"shoulder_orientation": (0, 3), "elbow_flexion": (3, 4),
              "forearm_roll": (4, 5), "wrist_orientation": (5, 8),
              "signed_hand_position": (8, 11)}
    result = {}
    for label, (start, stop) in labels.items():
        result[label] = {
            "left": float(np.sqrt(np.mean((source[:, start:stop] -
                                            target[:, start:stop]) ** 2))),
            "right": float(np.sqrt(np.mean((source[:, 11 + start:11 + stop] -
                                             target[:, 11 + start:11 + stop]) ** 2))),
        }
    return result


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
    target_rest = dataset["rest_bones"]
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
    train = [sample for action in dataset["actions"] if action["split"] == "train"
             for sample in action["samples"]]
    x_train = np.asarray([pose_feature(sample, bones) for sample in train],
                         dtype=np.float64)
    model = fit_pca(x_train, args.latent_dimensions)
    mean, scale, components = model
    train_semantics = np.asarray([
        scaffold.calibrated_sem(rests, scaffold.load_pose(sample, bones),
                                fit.TARGET_PROFILE, target_anatomy, "left")
        for sample in train], dtype=np.float64)
    semantic_scale = np.maximum(train_semantics.std(axis=0), 0.06)
    scaffold_action = next((row for row in dataset["actions"]
                            if row["action"] == args.scaffold_action and
                            row["split"] == "train"), None)
    if scaffold_action is None:
        raise RuntimeError("Running scaffold action is missing from raw-ANM training data")
    prior_sample_count = len(scaffold_action["samples"])
    prior_z_samples = np.asarray([
        ((pose_feature(sample, bones) - mean) / scale) @ components.T
        for sample in scaffold_action["samples"]], dtype=np.float64)
    prior_map = None
    if os.path.isfile(args.prior_report):
        prior_json = json.load(open(args.prior_report, "r", encoding="utf-8"))
        primary = next((row for row in prior_json["candidates"]
                        if row["action"] == args.scaffold_action), None)
        if primary:
            prior_map = primary["time_mapping"]
    if prior_map and len(prior_map) == 61:
        scaffold_indices = [float(row["native_sample_bracket"][0]) +
                            float(row["interpolation_fraction"])
                            for row in prior_map]
    else:
        scaffold_indices = np.linspace(0, prior_sample_count - 1, 61).tolist()
    prior = np.asarray([
        np.asarray([
            np.interp(index, np.arange(prior_sample_count), prior_z_samples[:, dim])
            for dim in range(args.latent_dimensions)])
        for index in scaffold_indices], dtype=np.float64)

    scene = bpy.context.scene
    old_frame, old_sub = scene.frame_current, scene.frame_subframe
    scene.frame_set(0)
    bpy.context.view_layer.update()
    evaluated_production = production.evaluated_get(bpy.context.evaluated_depsgraph_get())
    left_fingers = fit.descendant_bone_names(production.data, "bone_30")
    right_fingers = fit.descendant_bone_names(production.data, "bone_65")
    finger_names = left_fingers | right_fingers
    closed_basis = {name: evaluated_production.pose.bones[name].matrix_basis.copy()
                    for name in finger_names}
    closed_pose = {name: fit.numpy_matrix(matrix)
                   for name, matrix in evaluated_bone_matrices(production).items()}
    scene.frame_set(old_frame, subframe=old_sub)
    bpy.context.view_layer.update()
    parent_names = {bone.name: bone.parent.name if bone.parent else None
                    for bone in production.data.bones}
    finger_relatives = {}
    for name in finger_names:
        parent_name = parent_names[name]
        if parent_name:
            finger_relatives[name] = np.linalg.inv(closed_pose[parent_name]) @ closed_pose[name]
    right_subtree = fit.descendant_bone_names(production.data, "bone_57") | {"bone_57"}
    right_arm = {fit.LEFT_TO_RIGHT_BONES[name] for name in
                 ("bone_22", "bone_23", "bone_24", "bone_25",
                  "bone_26", "bone_27", "bone_29", "bone_30")}
    right_finger_names = right_subtree - right_arm - {"bone_57"}
    evaluate = make_pose_evaluator(model, bones, rests, baseline_rows,
        target_rest, rests, production, target_anatomy, finger_relatives,
        parent_names, right_subtree, right_finger_names)
    source_ranges = {
        "left": {"shoulder_min": source_left[:, :3].min(axis=0).tolist(),
                 "shoulder_max": source_left[:, :3].max(axis=0).tolist(),
                 "elbow_min": float(source_left[:, 3].min()),
                 "elbow_max": float(source_left[:, 3].max())},
        "right": {"shoulder_min": source_right[:, :3].min(axis=0).tolist(),
                  "shoulder_max": source_right[:, :3].max(axis=0).tolist(),
                  "elbow_min": float(source_right[:, 3].min()),
                  "elbow_max": float(source_right[:, 3].max())}}
    optimized_z, predicted, history = fit_sequence(
        prior, source_features, evaluate, semantic_scale, source_ranges,
        args.iterations, args.latent_dimensions)
    pose_rows, predicted_exact, root_deltas = [], [], []
    for frame in range(61):
        sem, pose, root_delta = evaluate(optimized_z[frame], frame, return_pose=True)
        pose_rows.append(pose)
        predicted_exact.append(sem)
        root_deltas.append(root_delta)
    predicted_exact = np.asarray(predicted_exact)
    all_bones = sorted(set(bones) | {fit.LEFT_TO_RIGHT_BONES[n] for n in bones
                                    if n in fit.LEFT_TO_RIGHT_BONES})
    rest_inv = {name: np.linalg.inv(fit.numpy_matrix(rests[name]["rest_matrix"]))
                for name in all_bones}
    arm_length = max(float(rests["bone_25"]["length"]), 1e-6)
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
    for offset, side in ((0, "left"), (11, "right")):
        low = source_ranges[side]["elbow_min"] - 0.02
        high = source_ranges[side]["elbow_max"] + 0.02
        elbow_envelope_ok &= bool(np.all(predicted_exact[:, offset + 3] >= low) and
                                  np.all(predicted_exact[:, offset + 3] <= high))
    shoulder_envelope_ok = True
    for offset, side in ((0, "left"), (11, "right")):
        low = np.asarray(source_ranges[side]["shoulder_min"]) - 0.05
        high = np.asarray(source_ranges[side]["shoulder_max"]) + 0.05
        shoulder_envelope_ok &= bool(np.all(predicted_exact[:, offset:offset + 3] >= low) and
                                     np.all(predicted_exact[:, offset:offset + 3] <= high))
    excursion_ok = all(80.0 <= retention[side][kind] <= 120.0
        for side in ("left", "right") for kind in
        ("shoulder_percent", "elbow_percent", "wrist_percent"))
    semantic_rmse_ok = shoulder_rmse < 0.18 and elbow_rmse < 0.10 and wrist_rmse < 0.20
    gate = {
        "semantic_excursions_80_to_120_percent": bool(excursion_ok),
        "shoulder_source_envelope": bool(shoulder_envelope_ok),
        "elbow_source_rest_calibrated_range": bool(elbow_envelope_ok),
        "semantic_rmse_limits": bool(semantic_rmse_ok),
        "both_fists_locked": bool(finger_local_error < 1e-6),
        "max_pose_jump_below_0_5": bool(worst_jump["max_normalized_se3"] < 0.5),
        "glove_gap_below_0_02": bool(glove_gap < 0.02),
    }
    gate_passed = all(gate.values())
    meshes = sorted((obj for obj in bpy.data.objects if obj.type == "MESH" and
        obj.name.startswith(args.mesh_prefix) and any(
        mod.type == "ARMATURE" and mod.object == baseline for mod in obj.modifiers)),
        key=lambda obj: obj.name)
    if not meshes:
        raise RuntimeError("validated walk baseline meshes are missing")
    preview = None
    if gate_passed:
        preview = scaffold.write_preview(args.preview, args.action_name,
            clean, baseline, baseline_rows, pose_rows, meshes, walk_action,
            walk_slot, closed_fist_basis=closed_basis)
    report = {
        "schema": "mg1.semantic-walk-scaffold-prior.v1",
        "source": {"rig": source.name, "action": source_action.name,
                   "slot": source_slots[0].identifier, "frames": [0, 60]},
        "target_model": {"type": "training-only raw-ANM PCA pose manifold",
                         "training_samples": len(train), "profile_bones": bones,
                         "latent_dimensions": args.latent_dimensions,
                         "scaffold_action": args.scaffold_action,
                         "scaffold_is_output_pose": False,
                         "scaffold_phase_map": scaffold_indices},
        "semantic_targets": {"features": list(SEMANTIC_NAMES),
                             "coordinate_frame": "rest-calibrated character-neutral anatomical frames",
                             "source_ranges": source_ranges,
                             "rmse": rmse, "excursion_retention": retention},
        "optimization": {"joint_iterations": history,
                         "final_latent_offset_rms_from_scaffold": float(
                             np.sqrt(np.mean((optimized_z - prior) ** 2))),
                         "post_fit_smoothing": False},
        "fist_lock": {"source_rig": production.name, "sample_frame": 0,
            "left_finger_count": len(left_fingers),
            "right_finger_count": len(right_fingers),
            "max_frame_local_transform_error": finger_local_error},
        "continuity": {"max_final_pose_jump": worst_jump,
                       "mean_rms_jump": float(np.mean([r["rms_normalized_se3"] for r in transition_rows])),
                       "max_glove_gap_armature_units": float(glove_gap)},
        "gate": {"checks": gate, "passed": gate_passed,
                 "failed_checks": [name for name, passed in gate.items() if not passed]},
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
