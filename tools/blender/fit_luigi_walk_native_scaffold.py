"""Fit Luigi walk to one raw-ANM native Waluigi locomotion scaffold.

The input scene and production rig/actions are read-only. A visible disposable
preview is emitted only when every numerical acceptance gate passes.
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import os
import sys

import bpy
import numpy as np
from mathutils import Matrix

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import fit_luigi_walk_semantic_waluigi as fit
from anatomical_semantic_frames import derive_anatomical_frames
from context_dataset import normalize_context_dataset
from convert_native_owner_actions import (
    basis_from_pose, duplicate_rig, evaluated_bone_matrices, make_action, set_frame,
)
from native_scaffold_sequence import solve_scaffold_sequence
from native_pose_graph import pose_pair_distance
from semantic_arm_fit_math import semantic_features
from test_semantic_preview_noop import duplicate_mesh


PRIMARY = "Waluigi_8_Running_seq10"
SECONDARY = "Waluigi_6_Fielding_seq21"


def args_parse():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--primary", default=PRIMARY)
    parser.add_argument("--secondary", default=SECONDARY)
    parser.add_argument("--source-rig", default="luigi.001_armature")
    parser.add_argument("--source-action", default="luigi.001_armature_walk")
    parser.add_argument("--source-slot", default="OBluigi.001_armature")
    parser.add_argument("--target-rig", default="123475520_waluigi.gpl")
    parser.add_argument("--clean-rig", default="Waluigi_Arm_Clean_Backup")
    parser.add_argument("--baseline-rig", default="Waluigi_Arm_Test")
    parser.add_argument("--walk-action", default="Waluigi_GalaxyWalk_Legs_Baked")
    parser.add_argument("--walk-slot", default="")
    parser.add_argument("--mesh-prefix", default="ARMTEST_")
    parser.add_argument("--report", required=True)
    parser.add_argument("--preview", required=True)
    parser.add_argument("--action-name", default="Waluigi_LuigiWalk_NativeScaffold_v1")
    parser.add_argument("--preview-rig-name", default="ScaffoldWalk_Waluigi_Rig")
    parser.add_argument("--preview-collection", default="ScaffoldWalk_Waluigi_Preview")
    parser.add_argument("--phase-radius", type=float, default=6.0)
    return parser.parse_args(argv)


def set_visible(obj):
    obj.hide_viewport = False
    obj.hide_render = False
    obj.hide_set(False)


def local_rest(obj):
    return fit.source_rest_from_rig(obj)


def calibrated_sem(rest, pose, profile, anatomy, side):
    side_frames = anatomy["sides"][side]
    return semantic_features(rest, pose, profile,
        anatomy["torso_to_anatomical"],
        side_frames["forearm_to_anatomical"],
        side_frames["forearm_axes_local"], elbow_absolute=True)


def build_baseline_rows(scene, clean, baseline, walk_action, walk_slot):
    root = fit.object_transform_matrix(baseline)
    rig = duplicate_rig(clean, "_Scaffold_Baseline_Eval_Disposable")
    rig.matrix_world = root.copy()
    set_visible(rig)
    fit.clear_constraints(rig)
    rig.animation_data_create()
    rig.animation_data.action = walk_action
    rig.animation_data.action_slot = walk_slot
    result = []
    old_frame, old_sub = scene.frame_current, scene.frame_subframe
    try:
        for frame in range(61):
            set_frame(scene, float(frame))
            result.append({name: fit.numpy_matrix(m)
                           for name, m in evaluated_bone_matrices(rig).items()})
    finally:
        scene.frame_set(old_frame, subframe=old_sub)
        data = rig.data
        bpy.data.objects.remove(rig, do_unlink=True)
        if data.users == 0:
            bpy.data.armatures.remove(data)
    return result, root


def load_source_rows(scene, rig, action, slot, rest, anatomy):
    rows, left, right = [], [], []
    old_frame, old_sub = scene.frame_current, scene.frame_subframe
    try:
        for frame in range(61):
            pose, _basis = fit.source_pose_from_curves(rig, action, slot,
                                                        float(frame))
            pose = {name: fit.numpy_matrix(matrix) for name, matrix in pose.items()}
            rows.append(pose)
            left.append(calibrated_sem(rest, pose, fit.SOURCE_PROFILE,
                                       anatomy, "left"))
            right.append(calibrated_sem(rest, pose, fit.SOURCE_PROFILE_RIGHT,
                                        anatomy, "right"))
    finally:
        scene.frame_set(old_frame, subframe=old_sub)
    return rows, np.asarray(left), np.asarray(right)


def load_pose(sample, bones):
    return {name: np.asarray(sample["bones"][name]["armature_pose"],
                             dtype=np.float64) for name in bones}


def densify_action(action, rest_bones, subdivisions=4):
    """Interpolate each bone's rest-relative SE(3) between keyed native poses."""
    original = action["samples"]
    rest = {name: Matrix(row["rest_matrix"])
            for name, row in rest_bones.items()}
    dense = []
    for first, second in zip(original, original[1:]):
        for sub in range(subdivisions):
            factor = sub / float(subdivisions)
            bones = {}
            for name in first["bones"]:
                rest_inv = rest[name].inverted_safe()
                a = Matrix(first["bones"][name]["armature_pose"])
                b = Matrix(second["bones"][name]["armature_pose"])
                a_loc, a_rot, a_scale = (rest_inv @ a).decompose()
                b_loc, b_rot, b_scale = (rest_inv @ b).decompose()
                rotation = a_rot.slerp(b_rot, factor)
                location = a_loc.lerp(b_loc, factor)
                scale = a_scale.lerp(b_scale, factor)
                pose = rest[name] @ Matrix.LocRotScale(location, rotation, scale)
                bones[name] = {**first["bones"][name],
                    "armature_pose": [[float(v) for v in row] for row in pose]}
            dense.append({"bones": bones,
                "time": float(first["time"] + factor * (second["time"] - first["time"])),
                "normalized_time": float(first["normalized_time"] + factor *
                    (second["normalized_time"] - first["normalized_time"]))})
    dense.append(original[-1])
    return {**action, "original_sample_count": len(original), "samples": dense}


def target_profile_semantics(pose, rest, anatomy):
    return (calibrated_sem(rest, pose, fit.TARGET_PROFILE, anatomy, "left"),
            calibrated_sem(rest, pose, fit.TARGET_PROFILE_RIGHT, anatomy, "right"))


def semantic_metrics(source, target):
    labels = {"shoulder_orientation": (0, 3), "elbow_flexion": (3, 4),
              "forearm_roll": (4, 5), "wrist_orientation": (5, 8),
              "normalized_hand_position": (8, 11)}
    return {name: float(np.sqrt(np.mean((source[:, a:b] - target[:, a:b]) ** 2)))
            for name, (a, b) in labels.items()}


def semantic_range(values):
    values = np.asarray(values, dtype=np.float64)
    return {"min": float(values.min()), "max": float(values.max()),
            "range": float(np.ptp(values))}


def motion_retention(source, target):
    shoulder_source = float(np.max(np.linalg.norm(source[:, :3] - source[0, :3], axis=1)))
    shoulder_target = float(np.max(np.linalg.norm(target[:, :3] - target[0, :3], axis=1)))
    elbow_source = float(np.ptp(source[:, 3]))
    elbow_target = float(np.ptp(target[:, 3]))
    return {
        "shoulder_source_excursion_rad": shoulder_source,
        "shoulder_target_excursion_rad": shoulder_target,
        "shoulder_retention_percent": 100.0 * shoulder_target / max(shoulder_source, 1e-9),
        "elbow_source_range_rad": elbow_source,
        "elbow_target_range_rad": elbow_target,
        "elbow_retention_percent": 100.0 * elbow_target / max(elbow_source, 1e-9),
    }


def action_semantics(dataset, name, baseline_rows, target_rest, target_rest_all,
                     target_anatomy, target_original):
    raw_action = next((row for row in dataset["actions"]
                   if row["action"] == name and row["split"] == "train"), None)
    if raw_action is None:
        raise RuntimeError("scaffold action is absent or not in training split: " + name)
    action = densify_action(raw_action, target_rest_all, subdivisions=4)
    all_rest = {name: np.linalg.inv(fit.numpy_matrix(row["rest_matrix"]))
                for name, row in target_rest_all.items()}
    profile_names = set(dataset["bones"])
    arm_bones = profile_names - fit.TORSO_CONTEXT
    left_sem, right_sem, final_poses, descriptors, times = [], [], [], [], []
    full_bones = sorted(profile_names | {fit.LEFT_TO_RIGHT_BONES[n]
        for n in profile_names if n in fit.LEFT_TO_RIGHT_BONES})
    base = baseline_rows[0]
    right_root = fit.descendant_bone_names(target_original.data, "bone_57") | {"bone_57"}
    right_arm = {fit.LEFT_TO_RIGHT_BONES[n] for n in
                 ("bone_22", "bone_23", "bone_24", "bone_25",
                  "bone_26", "bone_27", "bone_29", "bone_30")}
    right_fingers = right_root - right_arm - {"bone_57"}
    parent = {b.name: b.parent.name if b.parent else None
              for b in target_original.data.bones}
    for sample in action["samples"]:
        left_pose = load_pose(sample, dataset["bones"])
        final, _delta = fit.compose_final_semantic_pose(
            base, left_pose, left_pose, target_rest=target_rest,
            target_rest_all=target_rest_all, arm_bones=arm_bones,
            torso_context=fit.TORSO_CONTEXT,
            semantic_arm_names={"bone_22", "bone_23", "bone_24", "bone_25",
                "bone_26", "bone_27", "bone_29", "bone_30"},
            left_to_right=fit.LEFT_TO_RIGHT_BONES,
            right_subtree=right_root, right_finger_names=right_fingers,
            parent_by_bone=parent, right_root_name="bone_57",
            torso_name=fit.TARGET_PROFILE["torso"])
        lsem, rsem = target_profile_semantics(final, target_rest_all,
                                               target_anatomy)
        left_sem.append(lsem)
        right_sem.append(rsem)
        final_poses.append(final)
        descriptors.append(fit.pose_quaternion_descriptor(
            final, full_bones, all_rest,
            max(float(target_rest_all["bone_25"]["length"]), 1e-6)))
        times.append(float(sample["normalized_time"]))
    return action, np.column_stack((left_sem, right_sem)), final_poses, np.asarray(descriptors), times


def evaluate_scaffold(name, action, semantics, final_poses, descriptors, times,
                      source_sem, source_sem_right, baseline_rows, dataset,
                      target_rest, target_rest_all, target_anatomy,
                      target_original, phase_radius):
    source_pair = np.column_stack((source_sem, source_sem_right))
    result = solve_scaffold_sequence(source_pair, semantics, descriptors,
                                    phase_radius=phase_radius, max_step=8,
                                    max_pose_jump=0.499)
    indices = result["sample_indices"]
    selected_poses = []
    selected_sem = []
    for frame, idx in enumerate(indices):
        # Recompose on the corresponding walk baseline frame. The precomputed
        # candidate pose used frame zero only for semantic comparison; anchor
        # each selected sample in the exact frame's validated torso context.
        sample = action["samples"][idx]
        left_pose = load_pose(sample, dataset["bones"])
        right_root = fit.descendant_bone_names(target_original.data, "bone_57") | {"bone_57"}
        right_arm = {fit.LEFT_TO_RIGHT_BONES[n] for n in
                     ("bone_22", "bone_23", "bone_24", "bone_25", "bone_26", "bone_27", "bone_29", "bone_30")}
        right_fingers = right_root - right_arm - {"bone_57"}
        parent = {b.name: b.parent.name if b.parent else None for b in target_original.data.bones}
        final, _ = fit.compose_final_semantic_pose(
            baseline_rows[frame], left_pose, left_pose, target_rest=target_rest,
            target_rest_all=target_rest_all,
            arm_bones=set(dataset["bones"]) - fit.TORSO_CONTEXT,
            torso_context=fit.TORSO_CONTEXT,
            semantic_arm_names={"bone_22", "bone_23", "bone_24", "bone_25", "bone_26", "bone_27", "bone_29", "bone_30"},
            left_to_right=fit.LEFT_TO_RIGHT_BONES, right_subtree=right_root,
            right_finger_names=right_fingers, parent_by_bone=parent,
            right_root_name="bone_57", torso_name=fit.TARGET_PROFILE["torso"])
        selected_poses.append(final)
        selected_sem.append(np.r_[target_profile_semantics(final, target_rest_all,
                                                           target_anatomy)])
    selected_sem = np.asarray(selected_sem)
    left_target, right_target = selected_sem[:, :11], selected_sem[:, 11:]
    source_pair = np.column_stack((source_sem, source_sem_right))
    target_pair = np.column_stack((left_target, right_target))
    linear_indices = [int(round(frame * (len(semantics) - 1) / 60.0))
                      for frame in range(61)]
    linear_left = semantics[linear_indices, :11]
    linear_right = semantics[linear_indices, 11:]
    rest_inverse = {bone: np.linalg.inv(fit.numpy_matrix(target_rest_all[bone]["rest_matrix"]))
                    for bone in target_rest_all}
    full_bones = sorted(set(dataset["bones"]) | {fit.LEFT_TO_RIGHT_BONES[n]
        for n in dataset["bones"] if n in fit.LEFT_TO_RIGHT_BONES})
    arm_length = max(float(target_rest_all["bone_25"]["length"]), 1e-6)
    descriptors_selected = np.asarray([fit.pose_quaternion_descriptor(
        pose, full_bones, rest_inverse, arm_length) for pose in selected_poses])
    transitions = []
    for i in range(60):
        maximum, rms, per_bone = pose_pair_distance(
            descriptors_selected[i], descriptors_selected[i + 1])
        transitions.append({"from_frame": i, "to_frame": i + 1,
                            "normalized_se3_max": maximum,
                            "normalized_se3_rms": rms,
                            "worst_bone_index": int(np.argmax(per_bone)),
                            "worst_bone": full_bones[int(np.argmax(per_bone))]})
    gaps = []
    for pose in selected_poses:
        left = fit.context_joint_gaps(pose, target_rest_all, fit.GAP_PAIRS)
        right = fit.context_joint_gaps(pose, target_rest_all, fit.RIGHT_GAP_PAIRS)
        gaps.append({"left": left.tolist(), "right": right.tolist()})
    max_jump_row = max(transitions, key=lambda row: row["normalized_se3_max"])
    max_glove = max(max(row["left"][3], row["right"][3]) for row in gaps)
    retention = {"left": motion_retention(source_sem, left_target),
                 "right": motion_retention(source_sem_right, right_target)}
    gate = (min(retention[s]["shoulder_retention_percent"] for s in retention) > 70.0 and
            min(retention[s]["elbow_retention_percent"] for s in retention) > 70.0 and
            max_jump_row["normalized_se3_max"] < 0.5 and max_glove < 0.02)
    return {
        "action": name,
        "sample_count": action.get("original_sample_count", len(action["samples"])),
        "interpolated_candidate_count": len(action["samples"]),
        "time_mapping": [{
            "source_frame": frame,
            "candidate_index_4x": int(idx),
            "native_sample_bracket": [
                min(int(idx // 4), action["original_sample_count"] - 1),
                min(int(idx // 4) + (1 if idx % 4 else 0),
                    action["original_sample_count"] - 1)],
            "interpolation_fraction": float((idx % 4) / 4.0),
            "native_normalized_time": float(times[idx])}
            for frame, idx in enumerate(indices)],
        "semantic_rmse": {"left": semantic_metrics(source_sem, left_target),
                          "right": semantic_metrics(source_sem_right, right_target),
                          "combined": {key: 0.5 * (semantic_metrics(source_sem, left_target)[key] +
                                                    semantic_metrics(source_sem_right, right_target)[key])
                                       for key in semantic_metrics(source_sem, left_target)}},
        "linear_phase_reference_rmse": {
            "left": semantic_metrics(source_sem, linear_left),
            "right": semantic_metrics(source_sem_right, linear_right)},
        "feature_ranges": {
            "source_left": {key: semantic_range(source_sem[:, a:b])
                for key, (a, b) in {"shoulder_orientation": (0, 3), "elbow_flexion": (3, 4),
                    "forearm_roll": (4, 5), "wrist_orientation": (5, 8),
                    "signed_hand_position": (8, 11)}.items()},
            "target_left": {key: semantic_range(left_target[:, a:b])
                for key, (a, b) in {"shoulder_orientation": (0, 3), "elbow_flexion": (3, 4),
                    "forearm_roll": (4, 5), "wrist_orientation": (5, 8),
                    "signed_hand_position": (8, 11)}.items()},
            "source_right": {key: semantic_range(source_sem_right[:, a:b])
                for key, (a, b) in {"shoulder_orientation": (0, 3), "elbow_flexion": (3, 4),
                    "forearm_roll": (4, 5), "wrist_orientation": (5, 8),
                    "signed_hand_position": (8, 11)}.items()},
            "target_right": {key: semantic_range(right_target[:, a:b])
                for key, (a, b) in {"shoulder_orientation": (0, 3), "elbow_flexion": (3, 4),
                    "forearm_roll": (4, 5), "wrist_orientation": (5, 8),
                    "signed_hand_position": (8, 11)}.items()}},
        "motion_retention": retention,
        "max_final_pose_jump": max_jump_row,
        "mean_final_pose_jump_rms": float(np.mean([r["normalized_se3_rms"] for r in transitions])),
        "largest_per_bone_step": max_jump_row,
        "glove_gap_max_armature_units": float(max_glove),
        "glove_gap_frame": int(max(range(61), key=lambda i: max(gaps[i]["left"][3], gaps[i]["right"][3]))),
        "objective_cost": result["total_cost"],
        "phase_radius": phase_radius,
        "gate_passed": bool(gate),
        "selected_pose_rows": selected_poses,
        "selected_semantics": selected_sem,
    }


def write_preview(filepath, action_name, clean, baseline, baseline_rows,
                  selected_pose_rows, meshes, walk_action, walk_slot):
    scene = bpy.context.scene
    collection = bpy.data.collections.new("ScaffoldWalk_Waluigi_Preview")
    scene.collection.children.link(collection)
    target = duplicate_rig(clean, "ScaffoldWalk_Waluigi_Rig")
    target.matrix_world = fit.object_transform_matrix(baseline).copy()
    set_visible(target)
    fit.clear_constraints(target)
    collection.objects.link(target)
    for col in list(target.users_collection):
        if col != collection:
            col.objects.unlink(target)
    rig_meshes = [duplicate_mesh(source, target, collection, "ScaffoldWalk_Waluigi_")
                  for source in meshes]
    target.animation_data_create()
    target.animation_data.action = walk_action
    target.animation_data.action_slot = walk_slot
    bases_by_time = []
    old_frame, old_sub = scene.frame_current, scene.frame_subframe
    try:
        for frame, wanted in enumerate(selected_pose_rows):
            fit.set_frame(scene, float(frame))
            requested = {name: Matrix(value.tolist()) for name, value in wanted.items()}
            fit.assign_pose_matrices_hierarchical(target, requested)
            evaluated = evaluated_bone_matrices(target)
            bases_by_time.append((float(frame), basis_from_pose(target, evaluated)))
        baked = make_action(target, action_name, bases_by_time)
        target.animation_data.action = baked
        target.animation_data.action_slot = baked.slots[0]
        replay_error = 0.0
        for frame, intended in enumerate(selected_pose_rows):
            fit.set_frame(scene, float(frame))
            actual = evaluated_bone_matrices(target)
            replay_error = max(replay_error, max(fit.matrix_error(actual[name], intended[name])
                                                 for name in intended))
        if replay_error > 2e-4:
            raise RuntimeError(f"saved scaffold action replay error {replay_error:.6g} exceeds tolerance")
        for obj in rig_meshes:
            set_visible(obj)
        scene.frame_set(0)
        bpy.context.view_layer.update()
        bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(filepath))
        return {"path": os.path.abspath(filepath), "action": baked.name,
                "rig": target.name, "meshes": [obj.name for obj in rig_meshes],
                "replay_matrix_error": replay_error,
                "runtime_constraints": sum(len(pb.constraints) for pb in target.pose.bones)}
    finally:
        scene.frame_set(old_frame, subframe=old_sub)


def main():
    args = args_parse()
    with gzip.open(args.dataset, "rt", encoding="utf-8") as stream:
        dataset = normalize_context_dataset(json.load(stream))
    source = bpy.data.objects.get(args.source_rig)
    production = bpy.data.objects.get(args.target_rig)
    clean = bpy.data.objects.get(args.clean_rig)
    baseline = bpy.data.objects.get(args.baseline_rig)
    action = bpy.data.actions.get(args.source_action)
    walk_action = bpy.data.actions.get(args.walk_action)
    if any(obj is None for obj in (source, production, clean, baseline)) or action is None or walk_action is None:
        raise RuntimeError("required Luigi/Waluigi rigs or actions are missing")
    if abs(float(production.scale.x) - 43.437) > 0.01:
        raise RuntimeError("production Waluigi scale differs from protected 43.437 transform")
    source_slots = [slot for slot in action.slots if slot.identifier == args.source_slot]
    if len(source_slots) != 1:
        raise RuntimeError("source action slot is not unique")
    walk_slot = fit.select_walk_slot(walk_action, args.walk_slot,
                                     production.data.bones.keys())
    source_rest, target_rest_all = local_rest(source), local_rest(production)
    target_rest = dataset["rest_bones"]
    src_anatomy = derive_anatomical_frames(source_rest,
        fit.numpy_matrix(fit.object_transform_matrix(source)),
        fit.SOURCE_PROFILE, fit.SOURCE_PROFILE_RIGHT)
    tgt_anatomy = derive_anatomical_frames(target_rest_all,
        fit.numpy_matrix(fit.object_transform_matrix(production)),
        fit.TARGET_PROFILE, fit.TARGET_PROFILE_RIGHT)
    source_rows, source_left, source_right = load_source_rows(
        bpy.context.scene, source, action, source_slots[0], source_rest, src_anatomy)
    baseline_rows, _root = build_baseline_rows(bpy.context.scene, clean, baseline,
                                                walk_action, walk_slot)
    target_actions = [row for row in dataset["actions"] if row["split"] == "train"]
    selected_names = {args.primary, args.secondary}
    if not selected_names.issubset({row["action"] for row in target_actions}):
        raise RuntimeError("one or more requested scaffolds are not raw-ANM training actions")
    meshes = sorted((obj for obj in bpy.data.objects if obj.type == "MESH" and
        obj.name.startswith(args.mesh_prefix) and any(
        mod.type == "ARMATURE" and mod.object == baseline for mod in obj.modifiers)),
        key=lambda obj: obj.name)
    if not meshes:
        raise RuntimeError("validated walk baseline meshes are missing")
    candidates = []
    for name in (args.primary, args.secondary):
        action_row, semantics, _pose0, descriptors, times = action_semantics(
            dataset, name, baseline_rows, target_rest, target_rest_all,
            tgt_anatomy, production)
        result = evaluate_scaffold(name, action_row, semantics, _pose0,
            descriptors, times, source_left, source_right, baseline_rows,
            dataset, target_rest, target_rest_all, tgt_anatomy, production,
            args.phase_radius)
        candidates.append(result)
    chosen = next(row for row in candidates if row["action"] == args.primary)
    preview = None
    if chosen["gate_passed"]:
        preview = write_preview(args.preview, args.action_name, clean, baseline,
            baseline_rows, chosen["selected_pose_rows"], meshes, walk_action, walk_slot)
    for row in candidates:
        row.pop("selected_pose_rows", None)
        row.pop("selected_semantics", None)
    report = {
        "schema": "mg1.native-locomotion-scaffold-fit.v1",
        "read_only_production": True,
        "source": {"rig": source.name, "action": action.name,
                   "slot": source_slots[0].identifier, "frames": [0, 60]},
        "method": {"primary_scaffold": args.primary,
                   "comparison_scaffold": args.secondary,
                   "semantic_features": ["rest-calibrated shoulder orientation",
                       "absolute elbow angle from straight", "anatomical-axis forearm roll",
                       "forearm-relative wrist orientation", "signed body-frame hand position"],
                   "path_constraints": "single action only; bounded monotonic local sample transitions; full-pose SE(3) transition and phase costs",
                   "post_bake_smoothing": False},
        "candidates": candidates,
        "preview": preview,
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.report)), exist_ok=True)
    with open(args.report, "w", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
    print(json.dumps({"report": os.path.abspath(args.report),
        "primary_gate": chosen["gate_passed"],
        "primary_motion_retention": chosen["motion_retention"],
        "primary_rmse": chosen["semantic_rmse"],
        "primary_max_jump": chosen["max_final_pose_jump"],
        "primary_glove_gap": chosen["glove_gap_max_armature_units"],
        "preview": preview}, indent=2))


if __name__ == "__main__":
    main()
