"""Read-only Luigi/raw-Waluigi semantic coverage and coordinate audit.

Run in Blender background with a workfile containing the Luigi source rig and
walk action. Reads the raw ANM calibration JSON and never assigns/actions or
keys any rig.
"""
import argparse
import gzip
import json
import math
import os
import sys

import bpy
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fit_luigi_walk_semantic_waluigi as fit
from analyze_native_arm_context import source_pose_from_curves
from anatomical_semantic_frames import derive_anatomical_frames
from context_dataset import normalize_context_dataset
from semantic_arm_fit_math import _twist_about_axis, semantic_features


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--source-rig", default="luigi.001_armature")
    parser.add_argument("--source-action", default="luigi.001_armature_walk")
    parser.add_argument("--source-slot", default="OBluigi.001_armature")
    parser.add_argument("--target-rig", default="123475520_waluigi.gpl")
    parser.add_argument("--elbow-gain", type=float, default=1.5,
                        help="include the graph's historical frame-zero elbow excursion mapping")
    return parser.parse_args(argv)


FEATURE_NAMES = (
    "shoulder_rot_x", "shoulder_rot_y", "shoulder_rot_z", "elbow_flexion",
    "forearm_roll", "wrist_rot_x", "wrist_rot_y", "wrist_rot_z",
    "hand_radial", "hand_tangential", "hand_depth",
)
GROUPS = {
    "shoulder_orientation": (0, 1, 2), "elbow_flexion": (3,),
    "forearm_roll": (4,), "wrist_orientation": (5, 6, 7),
    "arm_plane_hand_position": (8, 9, 10),
}


def json_safe(value):
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_safe(item) for item in value]
    if isinstance(value, np.ndarray):
        return json_safe(value.tolist())
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def distribution(values):
    values = np.asarray(values, dtype=np.float64).reshape(-1)
    return {"count": int(len(values)), "min": float(values.min()),
            "q05": float(np.quantile(values, 0.05)),
            "q25": float(np.quantile(values, 0.25)),
            "median": float(np.median(values)),
            "q75": float(np.quantile(values, 0.75)),
            "q95": float(np.quantile(values, 0.95)),
            "max": float(values.max()), "range": float(np.ptp(values)),
            "mean": float(values.mean()), "std": float(values.std())}


def range_overlap(source, native):
    smin, smax = float(np.min(source)), float(np.max(source))
    nmin, nmax = float(np.min(native)), float(np.max(native))
    overlap = max(0.0, min(smax, nmax) - max(smin, nmin))
    return {"source_interval": [smin, smax], "native_interval": [nmin, nmax],
            "intersection_width": overlap,
            "source_range_covered": overlap / max(smax - smin, 1e-12),
            "native_range_covered": overlap / max(nmax - nmin, 1e-12)}


def matrix_pose(sample, bones):
    return {name: np.asarray(sample["bones"][name]["armature_pose"], dtype=np.float64)
            for name in bones}


def make_rest_pose(rest):
    return {name: np.asarray(row["rest_matrix"], dtype=np.float64)
            for name, row in rest.items()}


def current_frame_maps(source, source_rest, target, target_rest):
    body = fit.frame_map(source, source_rest, fit.SOURCE_PROFILE["torso"],
                         target, target_rest, fit.TARGET_PROFILE["torso"])
    forearm = fit.frame_map(source, source_rest, fit.SOURCE_PROFILE["forearm"],
                            target, target_rest, fit.TARGET_PROFILE["forearm"])
    return body, forearm


def correlation(a, b):
    a, b = np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64)
    if len(a) < 3 or np.std(a) < 1e-12 or np.std(b) < 1e-12:
        return None
    return float(np.corrcoef(a, b)[0, 1])


def main():
    args = parse_args()
    with gzip.open(args.dataset, "rt", encoding="utf-8") as stream:
        raw_dataset = json.load(stream)
    target_obj_matrix = np.asarray(
        raw_dataset["rig"]["production_basis_matrix_world"], dtype=np.float64)
    dataset = normalize_context_dataset(raw_dataset)

    source = bpy.data.objects.get(args.source_rig)
    target = bpy.data.objects.get(args.target_rig)
    action = bpy.data.actions.get(args.source_action)
    if not source or source.type != "ARMATURE" or not target or target.type != "ARMATURE":
        raise RuntimeError("source Luigi or target Waluigi armature is missing")
    if action is None:
        raise RuntimeError("source Luigi walk action is missing")
    slots = [slot for slot in action.slots if slot.identifier == args.source_slot]
    if len(slots) != 1:
        raise RuntimeError("source action slot is not unique")
    source_rest = fit.source_rest_from_rig(source)
    target_rest_all = fit.source_rest_from_rig(target)
    target_rest = dataset["rest_bones"]
    bones = dataset["bones"]
    frames = np.arange(0, 61, dtype=np.float64)
    if tuple(float(x) for x in action.frame_range) != (0.0, 60.0):
        raise RuntimeError("source walk must have the validated 0..60 range")

    current_body, current_forearm = current_frame_maps(
        source, source_rest, target, target_rest_all)
    current_right_body = fit.frame_map(source, source_rest,
        fit.SOURCE_PROFILE_RIGHT["torso"], target, target_rest_all,
        fit.TARGET_PROFILE_RIGHT["torso"])
    current_right_forearm = fit.frame_map(source, source_rest,
        fit.SOURCE_PROFILE_RIGHT["forearm"], target, target_rest_all,
        fit.TARGET_PROFILE_RIGHT["forearm"])

    source_obj_matrix = fit.object_transform_matrix(source)
    source_anatomy = derive_anatomical_frames(
        source_rest, fit.numpy_matrix(source_obj_matrix),
        fit.SOURCE_PROFILE, fit.SOURCE_PROFILE_RIGHT)
    target_anatomy = derive_anatomical_frames(
        target_rest_all, target_obj_matrix,
        fit.TARGET_PROFILE, fit.TARGET_PROFILE_RIGHT)

    source_rows, source_right_rows = [], []
    source_cal_rows, source_cal_right_rows = [], []
    source_poses = []
    for frame in frames:
        pose, _basis = source_pose_from_curves(source, action, slots[0], float(frame))
        pose = {name: fit.numpy_matrix(matrix) for name, matrix in pose.items()}
        source_poses.append(pose)
        source_rows.append(fit.semantic_from_source_sample(
            source_rest, pose, current_body, current_forearm))
        source_right_rows.append(fit.semantic_from_source_sample(
            source_rest, pose, current_right_body, current_right_forearm,
            fit.SOURCE_PROFILE_RIGHT))
        left_frames = source_anatomy["sides"]["left"]
        right_frames = source_anatomy["sides"]["right"]
        source_cal_rows.append(semantic_features(
            source_rest, pose, fit.SOURCE_PROFILE,
            source_anatomy["torso_to_anatomical"],
            left_frames["forearm_to_anatomical"],
            left_frames["forearm_axes_local"], elbow_absolute=True))
        source_cal_right_rows.append(semantic_features(
            source_rest, pose, fit.SOURCE_PROFILE_RIGHT,
            source_anatomy["torso_to_anatomical"],
            right_frames["forearm_to_anatomical"],
            right_frames["forearm_axes_local"], elbow_absolute=True))
    source_rows = np.asarray(source_rows)
    source_right_rows = np.asarray(source_right_rows)
    source_cal_rows = np.asarray(source_cal_rows)
    source_cal_right_rows = np.asarray(source_cal_right_rows)
    graph_source = source_rows.copy()
    graph_source[:, 3] = (graph_source[:, 3] - graph_source[0, 3]) * args.elbow_gain
    graph_source_right = source_right_rows.copy()
    graph_source_right[:, 3] = (graph_source_right[:, 3] -
                                graph_source_right[0, 3]) * args.elbow_gain

    target_samples, action_ids = [], []
    native_current, native_calibrated = [], []
    native_pose_rows = []
    for action_row in dataset["actions"]:
        if action_row["split"] != "train":
            continue
        for sample_index, sample in enumerate(action_row["samples"]):
            pose = matrix_pose(sample, bones)
            target_samples.append(sample)
            action_ids.append((action_row, sample_index))
            native_pose_rows.append(pose)
            native_current.append(fit.semantic_from_target_sample(target_rest, pose))
            native_calibrated.append(semantic_features(
                target_rest, pose, fit.TARGET_PROFILE,
                target_anatomy["torso_to_anatomical"],
                target_anatomy["sides"]["left"]["forearm_to_anatomical"],
                target_anatomy["sides"]["left"]["forearm_axes_local"],
                elbow_absolute=True))
    native_current = np.asarray(native_current)
    native_calibrated = np.asarray(native_calibrated)

    def coverage(source_values, native_values):
        source_values = np.asarray(source_values)
        native_values = np.asarray(native_values)
        scale = np.maximum(np.std(np.vstack((source_values, native_values)), axis=0), 1e-4)
        normalized_nearest = np.min(np.linalg.norm(
            (source_values[:, None, :] - native_values[None, :, :]) / scale, axis=2), axis=1)
        feature_rows = {}
        for index, name in enumerate(FEATURE_NAMES):
            nearest = np.min(np.abs(source_values[:, index, None] -
                                    native_values[None, :, index]), axis=1)
            feature_rows[name] = {
                "source": distribution(source_values[:, index]),
                "native": distribution(native_values[:, index]),
                "range_overlap": range_overlap(source_values[:, index], native_values[:, index]),
                "nearest_native_abs_error": distribution(nearest),
            }
        group_rows = {}
        for group, indices in GROUPS.items():
            source_mag = np.linalg.norm(source_values[:, indices], axis=1)
            native_mag = np.linalg.norm(native_values[:, indices], axis=1)
            group_rows[group] = {"source_magnitude": distribution(source_mag),
                "native_magnitude": distribution(native_mag),
                "magnitude_range_overlap": range_overlap(source_mag, native_mag),
                "nearest_native_magnitude_error": distribution(np.min(
                    np.abs(source_mag[:, None] - native_mag[None, :]), axis=1))}
        return {"feature_dimensions": feature_rows, "groups": group_rows,
                "joint_nearest_distance_standardized": distribution(normalized_nearest),
                "standardization_scale": dict(zip(FEATURE_NAMES, scale.tolist()))}

    # Evaluate per-action coverage against the unmodified and calibrated
    # semantic frames. Action holdouts stay excluded, matching graph training.
    def action_coverage(source_values, target_values):
        grouped = {}
        for index, (action_row, _sample_index) in enumerate(action_ids):
            grouped.setdefault(action_row["action"], {"row": action_row, "ids": []})["ids"].append(index)
        rows = []
        scale = np.maximum(np.std(np.vstack((source_values, target_values)), axis=0), 1e-4)
        for name, entry in grouped.items():
            values = target_values[entry["ids"]]
            nearest = np.min(np.linalg.norm(
                (source_values[:, None, :] - values[None, :, :]) / scale, axis=2), axis=1)
            row = entry["row"]
            rows.append({"action": name, "bank": row["bank"],
                "bank_index": row["bank_index"], "sequence_index": row["sequence_index"],
                "sample_count": len(values), "nearest_distance_mean": float(nearest.mean()),
                "nearest_distance_p90": float(np.quantile(nearest, 0.9)),
                "feature_range_overlap_mean": float(np.mean([
                    range_overlap(source_values[:, i], values[:, i])["source_range_covered"]
                    for i in range(len(FEATURE_NAMES))]))})
        return sorted(rows, key=lambda row: (row["nearest_distance_mean"],
                                             -row["feature_range_overlap_mean"]))

    rest_source_pose = make_rest_pose(source_rest)
    rest_target_pose = make_rest_pose(target_rest_all)
    rest_features = {
        "luigi_left_current": fit.semantic_from_source_sample(
            source_rest, rest_source_pose, current_body, current_forearm).tolist(),
        "luigi_left_anatomical": semantic_features(source_rest, rest_source_pose,
            fit.SOURCE_PROFILE, source_anatomy["torso_to_anatomical"],
            source_anatomy["sides"]["left"]["forearm_to_anatomical"],
            source_anatomy["sides"]["left"]["forearm_axes_local"],
            elbow_absolute=True).tolist(),
        "luigi_right_anatomical": semantic_features(source_rest, rest_source_pose,
            fit.SOURCE_PROFILE_RIGHT, source_anatomy["torso_to_anatomical"],
            source_anatomy["sides"]["right"]["forearm_to_anatomical"],
            source_anatomy["sides"]["right"]["forearm_axes_local"],
            elbow_absolute=True).tolist(),
        "waluigi_left_current": fit.semantic_from_target_sample(
            target_rest, {name: rest_target_pose[name] for name in bones}).tolist(),
        "waluigi_left_anatomical": semantic_features(target_rest,
            {name: rest_target_pose[name] for name in bones}, fit.TARGET_PROFILE,
            target_anatomy["torso_to_anatomical"],
            target_anatomy["sides"]["left"]["forearm_to_anatomical"],
            target_anatomy["sides"]["left"]["forearm_axes_local"],
            elbow_absolute=True).tolist(),
    }

    def semantic_motion_correlations(values, poses, rest, profile, axes):
        correlations = {}
        shoulder_motion, elbow_motion, forearm_motion, wrist_motion, hand_motion = [], [], [], [], []
        for row in poses:
            shoulder_relative = np.linalg.inv(row[profile["torso"]][:3, :3]) @ row[profile["shoulder"]][:3, :3]
            shoulder_rest = np.linalg.inv(np.asarray(rest[profile["torso"]]["rest_matrix"])[:3, :3]) @ np.asarray(rest[profile["shoulder"]]["rest_matrix"])[:3, :3]
            shoulder_delta = np.linalg.inv(shoulder_rest) @ shoulder_relative
            shoulder_motion.append(float(np.linalg.norm(fit_rotation_log(shoulder_delta))))
            elbow_angle = elbow_geometry_angle(row, rest, profile)
            elbow_motion.append(elbow_angle)
            forearm_name = profile["forearm"]
            overlap_name = profile["forearm_overlap"]
            forearm_rest = np.asarray(rest[forearm_name]["rest_matrix"], dtype=np.float64)[:3, :3]
            overlap_rest = np.asarray(rest[overlap_name]["rest_matrix"], dtype=np.float64)[:3, :3]
            twist_a = _twist_about_axis(np.linalg.inv(forearm_rest) @
                row[forearm_name][:3, :3], axes[forearm_name])
            twist_b = _twist_about_axis(np.linalg.inv(overlap_rest) @
                row[overlap_name][:3, :3], axes[overlap_name])
            forearm_motion.append(math.atan2(math.sin(twist_a) + math.sin(twist_b),
                                             math.cos(twist_a) + math.cos(twist_b)))
            forearm_rest_rel = np.linalg.inv(np.asarray(rest[profile["forearm"]]["rest_matrix"])[:3, :3]) @ np.asarray(rest[profile["wrist"]]["rest_matrix"])[:3, :3]
            forearm_pose_rel = np.linalg.inv(row[profile["forearm"]][:3, :3]) @ row[profile["wrist"]][:3, :3]
            wrist_motion.append(float(np.linalg.norm(fit_rotation_log(np.linalg.inv(forearm_rest_rel) @ forearm_pose_rel))))
            def endpoint(bone, label):
                rest_matrix = np.asarray(rest[bone]["rest_matrix"], dtype=np.float64)
                point = np.r_[rest[bone][label], 1.0]
                return (row[bone] @ np.linalg.inv(rest_matrix) @ point)[:3]
            shoulder_point = endpoint(profile["shoulder_point_bone"], profile["shoulder_endpoint"])
            hand_point = endpoint(profile["hand"], profile["hand_endpoint"])
            rest_shoulder = np.asarray(rest[profile["shoulder_point_bone"]][profile["shoulder_endpoint"]])
            rest_hand = np.asarray(rest[profile["hand"]][profile["hand_endpoint"]])
            rest_limb_length = np.linalg.norm(
                np.asarray(rest[profile["elbow_a"]][profile["elbow_endpoint_a"]]) - rest_shoulder)
            rest_limb_length += np.linalg.norm(rest_hand - rest_shoulder)
            hand_motion.append(float(np.linalg.norm(
                (hand_point - shoulder_point) - (rest_hand - rest_shoulder)) /
                max(rest_limb_length, 1e-8)))
        correlations["shoulder_magnitude_vs_shoulder_relative_rotation"] = correlation(
            np.linalg.norm(values[:, :3], axis=1), shoulder_motion)
        correlations["elbow_feature_vs_independent_geometric_angle"] = correlation(
            values[:, 3], elbow_motion)
        correlations["forearm_roll_vs_roll_feature"] = correlation(
            values[:, 4], forearm_motion)
        correlations["wrist_magnitude_vs_forearm_relative_wrist_rotation"] = correlation(
            np.linalg.norm(values[:, 5:8], axis=1), wrist_motion)
        correlations["hand_position_magnitude_vs_endpoint_motion"] = correlation(
            np.linalg.norm(values[:, 8:11], axis=1), hand_motion)
        return correlations

    def fit_rotation_log(rotation):
        trace = float(np.trace(rotation))
        angle = math.acos(float(np.clip((trace - 1.0) * 0.5, -1.0, 1.0)))
        skew = np.asarray((rotation[2, 1] - rotation[1, 2],
                           rotation[0, 2] - rotation[2, 0],
                           rotation[1, 0] - rotation[0, 1]))
        if angle < 1e-8:
            return 0.5 * skew
        return skew * angle / max(2.0 * math.sin(angle), 1e-12)

    def elbow_geometry_angle(pose, rest, profile):
        def point(bone, endpoint):
            rest_matrix = np.asarray(rest[bone]["rest_matrix"], dtype=np.float64)
            vector = np.r_[rest[bone][endpoint], 1.0]
            return (pose[bone] @ np.linalg.inv(rest_matrix) @ vector)[:3]
        shoulder = point(profile["shoulder_point_bone"], profile["shoulder_endpoint"])
        elbow = 0.5 * (point(profile["elbow_a"], profile["elbow_endpoint_a"]) +
                       point(profile["elbow_b"], profile["elbow_endpoint_b"]))
        wrist = point(profile["wrist"], profile["wrist_endpoint"])
        upper, lower = elbow - shoulder, wrist - elbow
        angle = math.atan2(np.linalg.norm(np.cross(upper, lower)),
                           np.clip(np.dot(upper, lower), -1.0, 1.0))
        rest_upper = np.asarray(rest[profile["elbow_a"]][profile["elbow_endpoint_a"]]) - np.asarray(rest[profile["shoulder_point_bone"]][profile["shoulder_endpoint"]])
        rest_elbow = 0.5 * (np.asarray(rest[profile["elbow_a"]][profile["elbow_endpoint_a"]]) + np.asarray(rest[profile["elbow_b"]][profile["elbow_endpoint_b"]]))
        rest_wrist = np.asarray(rest[profile["wrist"]][profile["wrist_endpoint"]])
        return angle

    source_native_motion = semantic_motion_correlations(
        source_cal_rows, source_poses, source_rest, fit.SOURCE_PROFILE,
        source_anatomy["sides"]["left"]["forearm_axes_local"])
    source_right_motion = semantic_motion_correlations(
        source_cal_right_rows, source_poses, source_rest, fit.SOURCE_PROFILE_RIGHT,
        source_anatomy["sides"]["right"]["forearm_axes_local"])
    target_motion = semantic_motion_correlations(
        native_calibrated, native_pose_rows, target_rest, fit.TARGET_PROFILE,
        target_anatomy["sides"]["left"]["forearm_axes_local"])

    source_lr_symmetry = {}
    for group, indices in GROUPS.items():
        left = source_cal_rows[:, indices]
        right = source_cal_right_rows[:, indices]
        if group in ("shoulder_orientation", "wrist_orientation"):
            mirror = np.asarray((1.0, -1.0, -1.0))
            right = right * mirror[:len(indices)]
        elif group == "arm_plane_hand_position":
            right = right * np.asarray((1.0, 1.0, -1.0))
        elif group == "forearm_roll":
            right = -right
        source_lr_symmetry[group] = {
            "correlations_after_expected_mirror": [correlation(left[:, i], right[:, i])
                                                    for i in range(left.shape[1])],
            "rms_difference_after_expected_mirror": float(np.sqrt(np.mean((left - right) ** 2))),
            "direct_component_correlations": [
                correlation(source_cal_rows[:, idx], source_cal_right_rows[:, idx])
                for idx in indices],
        }

    current_cov = coverage(graph_source, native_current)
    calibrated_cov = coverage(source_cal_rows, native_calibrated)
    report = {
        "schema": "mg1.semantic-feature-coverage-audit.v1",
        "read_only": True, "fit_or_preview_run": False,
        "source": {"rig": source.name, "action": action.name,
            "slot": slots[0].identifier, "frames": [0, 60],
            "left_raw_range": {name: distribution(source_rows[:, i])
                               for i, name in enumerate(FEATURE_NAMES)},
            "right_raw_range": {name: distribution(source_right_rows[:, i])
                                for i, name in enumerate(FEATURE_NAMES)},
            "graph_input_left_range_after_frame_zero_elbow_rebase": {
                name: distribution(graph_source[:, i]) for i, name in enumerate(FEATURE_NAMES)},
            "graph_input_right_range_after_frame_zero_elbow_rebase": {
                name: distribution(graph_source_right[:, i]) for i, name in enumerate(FEATURE_NAMES)},
            "anatomical_left_range": {name: distribution(source_cal_rows[:, i])
                                      for i, name in enumerate(FEATURE_NAMES)},
            "anatomical_right_range": {name: distribution(source_cal_right_rows[:, i])
                                       for i, name in enumerate(FEATURE_NAMES)}},
        "native": {"training_sample_count": len(native_current),
            "training_action_count": len({row[0]["action"] for row in action_ids}),
            "holdout_actions_excluded": [row["action"] for row in dataset["actions"]
                                          if row["split"] == "holdout"],
            "current_frame_range": {name: distribution(native_current[:, i])
                                     for i, name in enumerate(FEATURE_NAMES)},
            "anatomical_frame_range": {name: distribution(native_calibrated[:, i])
                                       for i, name in enumerate(FEATURE_NAMES)}},
        "rest_pose_offsets": {"semantic_feature_names": list(FEATURE_NAMES),
            "features": rest_features,
            "interpretation": "rest-relative features should be zero; calibrated absolute elbow bend intentionally retains the rig's geometric rest bend so source and target are compared from the same straight-arm zero"},
        "coordinate_frames": {
            "previous": "source torso/forearm rest frames mapped onto Waluigi bone-local rest frames; elbow is an unsigned geometric angle; forearm roll assumes local +Y for both forearm branches",
            "calibrated": "body basis from labeled left/right shoulder axis and torso-up; forearm basis from elbow-to-wrist axis and projected body-forward; forearm twist extracted around each branch's rest-local distal anatomical axis; elbow is absolute bend angle from straight (not a rig-specific rest-angle delta)",
            "source_body_frame_world": source_anatomy["body_frame_world"].tolist(),
            "target_body_frame_world": target_anatomy["body_frame_world"].tolist(),
            "source_forearm_frame_world": source_anatomy["diagnostics"]["forearm_frame_world"],
            "target_forearm_frame_world": target_anatomy["diagnostics"]["forearm_frame_world"],
            "source_forearm_twist_axes_local": source_anatomy["sides"]["left"]["forearm_axes_local"],
            "target_forearm_twist_axes_local": target_anatomy["sides"]["left"]["forearm_axes_local"],
            "left_right_mirror_convention": {
                "shoulder_and_wrist_rotation_vectors": "axial vector reflection diag(+1,-1,-1)",
                "hand_arm_plane_vector": "radial/tangential components preserve sign; depth component reflects with sign -1",
                "elbow_flexion": "unchanged scalar magnitude",
                "forearm_roll": "sign evaluated around each side's elbow-to-wrist distal axis"},
            "elbow_rest_bend_angle_radians": {
                "luigi": float(rest_features["luigi_left_anatomical"][3]),
                "luigi_right": float(rest_features["luigi_right_anatomical"][3]),
                "waluigi": float(rest_features["waluigi_left_anatomical"][3])},
            "historical_graph_source_elbow_frame_zero_subtracted_before_comparison": {
                "left_reference_radians": float(source_rows[0, 3]),
                "right_reference_radians": float(source_right_rows[0, 3]),
                "gain": float(args.elbow_gain)},
            "object_scale_applied": False},
        "coverage_before_calibration": current_cov,
        "coverage_after_anatomical_calibration": calibrated_cov,
        "best_native_actions_current_frames": action_coverage(graph_source, native_current)[:20],
        "best_native_actions_calibrated_frames": action_coverage(source_cal_rows, native_calibrated)[:20],
        "best_banks_calibrated": bank_coverage(source_cal_rows, native_calibrated,
                                               action_ids, native_calibrated),
        "anatomical_motion_correlations": {"Luigi_left": source_native_motion,
            "Luigi_right": source_right_motion, "Waluigi_left_native": target_motion},
        "source_left_right_symmetry_after_anatomical_calibration": source_lr_symmetry,
        "limitations": ["The calibration dataset contains left-arm profile bones only; Waluigi right-side motion is inferred by the validated mirror map, not measured directly from raw ANM.",
            "Semantic interval overlap is not a retarget-quality guarantee; temporal sequence coverage and pose coherence remain separate gates."],
    }

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as stream:
        json.dump(json_safe(report), stream, indent=2, allow_nan=False)
        stream.write("\n")
    print("MG1_SEMANTIC_COVERAGE=" + json.dumps({
        "output": os.path.abspath(args.output), "training_samples": len(native_current),
        "best_calibrated_actions": [row["action"] for row in
                                    report["best_native_actions_calibrated_frames"][:5]]}))


def bank_coverage(source_values, native_values, action_ids, _all_native):
    scale = np.maximum(np.std(np.vstack((source_values, native_values)), axis=0), 1e-4)
    grouped = {}
    for index, (action_row, _sample_index) in enumerate(action_ids):
        key = (action_row["bank"], int(action_row["bank_index"]))
        grouped.setdefault(key, []).append(index)
    output = []
    for (bank, bank_index), indices in grouped.items():
        values = native_values[indices]
        nearest = np.min(np.linalg.norm(
            (source_values[:, None, :] - values[None, :, :]) / scale, axis=2), axis=1)
        overlap = [range_overlap(source_values[:, i], values[:, i])["source_range_covered"]
                   for i in range(source_values.shape[1])]
        output.append({"bank": bank, "bank_index": bank_index,
            "action_count": len({action_ids[i][0]["action"] for i in indices}),
            "sample_count": len(indices), "nearest_distance_mean": float(nearest.mean()),
            "nearest_distance_p90": float(np.quantile(nearest, 0.9)),
            "feature_range_overlap_mean": float(np.mean(overlap))})
    return sorted(output, key=lambda row: (row["nearest_distance_mean"],
                                           -row["feature_range_overlap_mean"]))


if __name__ == "__main__":
    main()
