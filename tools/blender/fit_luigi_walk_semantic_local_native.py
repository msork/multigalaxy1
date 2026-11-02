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
from fit_native_context_pca import encode_sample
from local_native_deformation import fit_local_sequence
from native_pose_graph import pose_pair_distance


SEMANTIC_NAMES = ("shoulder_x", "shoulder_y", "shoulder_z",
    "elbow_flexion", "forearm_roll", "wrist_x", "wrist_y", "wrist_z",
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
        enforce_glove_attachment(final, target_rest_all, parent_names)
        left_sem, right_sem = scaffold.target_profile_semantics(
            final, target_rest_all, anatomy)
        semantics = np.r_[left_sem, right_sem]
        return (semantics, final, root_delta) if return_pose else semantics
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
    native_samples, native_actions = [], []
    for action in dataset["actions"]:
        if action["split"] != "train":
            continue
        for sample_index, sample in enumerate(action["samples"]):
            pose = scaffold.load_pose(sample, bones)
            full_pose = dict(pose)
            for left_name, right_name in fit.LEFT_TO_RIGHT_BONES.items():
                if left_name not in pose or right_name not in rests:
                    continue
                full_pose[right_name] = fit.mirror_pose_across_sagittal(
                    pose[left_name], pose["bone_21"],
                    rests["bone_21"]["rest_matrix"],
                    rests[left_name]["rest_matrix"],
                    rests[right_name]["rest_matrix"])
            native_samples.append((sample, full_pose))
            native_actions.append({"action": action["action"],
                                   "time": float(sample["normalized_time"]),
                                   "sample_index": sample_index})
    native_semantics = np.asarray([np.r_[
        scaffold.target_profile_semantics(pose, rests, target_anatomy)]
        for _sample, pose in native_samples], dtype=np.float64)
    x_train = np.asarray([pose_feature(sample, bones)
                          for sample, _pose in native_samples], dtype=np.float64)
    native_scale = np.maximum(native_semantics.std(axis=0), 0.05)
    pose_scale = np.maximum(x_train.std(axis=0), 0.01)
    local_features, local_info = fit_local_sequence(
        native_semantics, x_train, source_features, native_scale,
        k=args.neighbors, passes=args.local_passes, pose_scale=pose_scale)
    # Identity decoding: each locally regressed feature row is a full set of
    # rest-relative transforms, not a global PCA latent vector.
    model = (np.zeros(x_train.shape[1]), np.ones(x_train.shape[1]),
             np.eye(x_train.shape[1]))

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
    predicted = np.asarray([evaluate(local_features[frame], frame)
                            for frame in range(61)])
    pose_rows, predicted_exact, root_deltas = [], [], []
    for frame in range(61):
        sem, pose, root_delta = evaluate(local_features[frame], frame, return_pose=True)
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
    locally_supported = all(bool(row["supported"]) for row in local_info)
    semantic_rmse_ok = shoulder_rmse < 0.18 and elbow_rmse < 0.10 and wrist_rmse < 0.20
    gate = {
        "semantic_excursions_80_to_120_percent": bool(excursion_ok),
        "shoulder_source_envelope": bool(shoulder_envelope_ok),
        "elbow_source_rest_calibrated_range": bool(elbow_envelope_ok),
        "semantic_rmse_limits": bool(semantic_rmse_ok),
        "both_fists_locked": bool(finger_local_error < 1e-6),
        "max_pose_jump_below_0_5": bool(worst_jump["max_normalized_se3"] < 0.5),
        "glove_gap_below_0_02": bool(glove_gap < 0.02),
        "no_unsupported_local_extrapolation": bool(locally_supported),
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
        "schema": "mg1.semantic-walk-local-native.v1",
        "source": {"rig": source.name, "action": source_action.name,
                   "slot": source_slots[0].identifier, "frames": [0, 60]},
        "target_model": {"type": "local affine raw-ANM deformation model",
                         "training_samples": len(native_samples), "profile_bones": bones,
                         "pose_feature_dimensions": int(x_train.shape[1]),
                         "neighbors_per_frame": args.neighbors,
                         "local_joint_passes": args.local_passes},
        "semantic_targets": {"features": list(SEMANTIC_NAMES),
                             "coordinate_frame": "rest-calibrated character-neutral anatomical frames",
                             "source_ranges": {
                                 "left": {"min": source_features[:, :11].min(axis=0).tolist(),
                                          "max": source_features[:, :11].max(axis=0).tolist()},
                                 "right": {"min": source_features[:, 11:].min(axis=0).tolist(),
                                           "max": source_features[:, 11:].max(axis=0).tolist()}},
                             "rmse": rmse, "excursion_retention": retention},
        "local_support": [{
            "nearest_semantic_distance": row["nearest_semantic_distance"],
            "local_semantic_radius_90": row["local_semantic_radius_90"],
            "inside_local_semantic_box": row["inside_local_semantic_box"],
            "unsupported_semantic_features": [SEMANTIC_NAMES[i % 11] +
                ("_left" if i < 11 else "_right")
                for i in row["unsupported_semantic_axes"]],
            "supported": row["supported"],
            "local_pose_trust_radius": row["local_pose_trust_radius"],
            "predicted_pose_distance": row["predicted_pose_distance"],
            "neighbor_actions": sorted({native_actions[int(i)]["action"]
                                         for i in row["neighbors"]})
            } for row in local_info],
        "optimization": {"method": "neighbor-weighted affine Jacobian with forward/backward sequence neighborhood passes",
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
