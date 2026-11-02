"""Read-only joint 11D semantic support audit for Luigi walk vs raw ANM."""
from __future__ import annotations

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

import fit_luigi_walk_native_scaffold as scaffold
import fit_luigi_walk_semantic_waluigi as fit
import fit_luigi_walk_semantic_local_native as local_fit
from anatomical_semantic_frames import derive_anatomical_frames
from context_dataset import normalize_context_dataset
from semantic_arm_fit_math import semantic_features


FEATURES = (
    "shoulder_rot_x", "shoulder_rot_y", "shoulder_rot_z", "elbow_flexion",
    "forearm_roll", "wrist_rot_x", "wrist_rot_y", "wrist_rot_z",
    "hand_radial", "hand_tangential", "hand_depth",
)
TOLERANCES = np.asarray((0.15, 0.15, 0.15, 0.08, 0.15,
                         0.15, 0.15, 0.15, 0.08, 0.08, 0.08))
GROUPS = {
    "shoulder": (0, 1, 2), "elbow": (3,), "forearm_roll": (4,),
    "wrist": (5, 6, 7), "hand_plane": (8, 9, 10),
    "shoulder_elbow": (0, 1, 2, 3),
    "shoulder_wrist": (0, 1, 2, 5, 6, 7),
    "elbow_wrist": (3, 5, 6, 7),
    "wrist_hand": (5, 6, 7, 8, 9, 10),
    "shoulder_elbow_wrist": (0, 1, 2, 3, 5, 6, 7),
    "shoulder_elbow_wrist_hand": tuple(range(11)),
}
ARM_BONES = {
    "left": ["bone_22", "bone_23", "bone_24", "bone_25",
             "bone_26", "bone_27", "bone_29", "bone_30"],
    "right": ["bone_57", "bone_58", "bone_59", "bone_60",
              "bone_61", "bone_62", "bone_64", "bone_65"],
}


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--source-rig", default="luigi.001_armature")
    parser.add_argument("--source-action", default="luigi.001_armature_walk")
    parser.add_argument("--source-slot", default="OBluigi.001_armature")
    parser.add_argument("--target-rig", default="123475520_waluigi.gpl")
    parser.add_argument("--neighbors", type=int, default=96)
    parser.add_argument("--continuity-passes", type=int, default=3)
    return parser.parse_args(argv)


def continuity_neighbors(samples, targets, scales, k=96, passes=3):
    count = len(samples)
    neighborhoods = [None] * len(targets)
    for iteration in range(max(1, passes)):
        for direction in (range(len(targets)), range(len(targets) - 1, -1, -1)):
            for frame in direction:
                adjacent = []
                if frame > 0 and neighborhoods[frame - 1] is not None:
                    adjacent.extend(neighborhoods[frame - 1].tolist())
                if frame + 1 < len(targets) and neighborhoods[frame + 1] is not None:
                    adjacent.extend(neighborhoods[frame + 1].tolist())
                previous = set(np.unique(adjacent).tolist()) if adjacent else set()
                distances = np.linalg.norm((samples - targets[frame]) / scales, axis=1)
                penalty = max(float(np.quantile(distances, 0.25)), 0.02) * (0.5 + 0.25 * iteration)
                score = distances + np.asarray([
                    0.0 if index in previous else penalty for index in range(count)])
                neighborhoods[frame] = np.argpartition(score, k - 1)[:k]
    return neighborhoods


def simplex_project(values):
    values = np.asarray(values, dtype=np.float64)
    order = np.sort(values)[::-1]
    cumulative = np.cumsum(order) - 1.0
    ids = np.arange(1, len(values) + 1)
    valid = order - cumulative / ids > 0.0
    rho = int(np.flatnonzero(valid)[-1])
    theta = cumulative[rho] / float(rho + 1)
    return np.maximum(values - theta, 0.0)


def convex_mix(target, candidates, iterations=80):
    """Active-set Frank-Wolfe projection onto the candidate convex hull.

    Each major step adds the native sample with the strongest linear support
    direction. A small projected-gradient solve fully corrects the active
    weights. The reported dual gap indicates whether the finite iteration run
    reached a reliable convex-hull distance.
    """
    values = np.asarray(candidates, dtype=np.float64)
    target = np.asarray(target, dtype=np.float64)
    nearest = int(np.argmin(np.sum((values - target) ** 2, axis=1)))
    active = [nearest]
    weights = np.asarray([1.0], dtype=np.float64)
    duality_gap = float("inf")
    completed = 0
    for completed in range(1, max(1, int(iterations)) + 1):
        # Fully-correct the active simplex by solving its equality constrained
        # least-squares problem. If the unconstrained optimum has negative
        # coefficients, remove the most negative vertex and solve again.
        while True:
            rows = values[active]
            gram = rows @ rows.T
            kkt = np.zeros((len(active) + 1, len(active) + 1), dtype=np.float64)
            kkt[:-1, :-1] = gram + np.eye(len(active)) * 1e-12
            kkt[:-1, -1] = 1.0
            kkt[-1, :-1] = 1.0
            rhs = np.r_[rows @ target, 1.0]
            solution = np.linalg.lstsq(kkt, rhs, rcond=1e-12)[0][:-1]
            if len(solution) == 1 or float(solution.min()) >= -1e-9:
                weights = np.maximum(solution, 0.0)
                weights /= weights.sum()
                break
            remove = int(np.argmin(solution))
            active.pop(remove)
        prediction = weights @ values[active]
        residual = prediction - target
        scores = values @ residual
        candidate = int(np.argmin(scores))
        duality_gap = max(0.0, float(residual @ (prediction - values[candidate])))
        if duality_gap < 1e-7 or candidate in active:
            break
        if completed >= max(1, int(iterations)):
            break
        if len(active) >= 64:
            break
        active.append(candidate)
    full_weights = np.zeros(len(values), dtype=np.float64)
    full_weights[active] = weights
    prediction = weights @ values[active]
    residual = prediction - target
    scores = values @ residual
    duality_gap = max(0.0, float(residual @ (prediction - values[int(np.argmin(scores))])))
    return full_weights, prediction, float(np.linalg.norm(residual)), {
        "active_sample_count": len(active), "duality_gap": duality_gap,
        "major_iterations": completed,
        "projection_converged": bool(duality_gap < 1e-4),
    }


def covariance_distance(samples, target, shrink=0.08):
    delta = np.asarray(target, dtype=np.float64) - samples.mean(axis=0)
    covariance = np.cov(samples, rowvar=False)
    diagonal = np.diag(np.diag(covariance))
    covariance = (1.0 - shrink) * covariance + shrink * diagonal
    eigenvalues, eigenvectors = np.linalg.eigh(covariance)
    floor = max(float(eigenvalues.max()) * 1e-5, 1e-6)
    inv = (eigenvectors * (1.0 / np.maximum(eigenvalues, floor))) @ eigenvectors.T
    return float(math.sqrt(max(float(delta @ inv @ delta), 0.0)))


def convex_hull_2d(points):
    pts = sorted(set((float(p[0]), float(p[1])) for p in points))
    if len(pts) <= 2:
        return pts
    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower = []
    for point in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 1e-12:
            lower.pop()
        lower.append(point)
    upper = []
    for point in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 1e-12:
            upper.pop()
        upper.append(point)
    return lower[:-1] + upper[:-1]


def inside_convex_hull_2d(point, hull, tolerance=1e-9):
    if len(hull) < 3:
        return False
    for index, a in enumerate(hull):
        b = hull[(index + 1) % len(hull)]
        cross = (b[0] - a[0]) * (point[1] - a[1]) - (b[1] - a[1]) * (point[0] - a[0])
        if cross < -tolerance:
            return False
    return True


def build_semantics(args):
    with gzip.open(args.dataset, "rt", encoding="utf-8") as stream:
        dataset = normalize_context_dataset(json.load(stream))
    source = bpy.data.objects.get(args.source_rig)
    target = bpy.data.objects.get(args.target_rig)
    action = bpy.data.actions.get(args.source_action)
    if not source or not target or not action:
        raise RuntimeError("source rig/action or production-basis target rig is missing")
    slots = [slot for slot in action.slots if slot.identifier == args.source_slot]
    if len(slots) != 1:
        raise RuntimeError("source action slot is not unique")
    source_rest = fit.source_rest_from_rig(source)
    source_anatomy = derive_anatomical_frames(
        source_rest, fit.numpy_matrix(fit.object_transform_matrix(source)),
        fit.SOURCE_PROFILE, fit.SOURCE_PROFILE_RIGHT)
    target_rest = fit.source_rest_from_rig(target)
    target_anatomy = derive_anatomical_frames(
        target_rest, fit.numpy_matrix(fit.object_transform_matrix(target)),
        fit.TARGET_PROFILE, fit.TARGET_PROFILE_RIGHT)
    _source_poses, source_left, source_right = scaffold.load_source_rows(
        bpy.context.scene, source, action, slots[0], source_rest, source_anatomy)
    native = {"left": [], "right": []}
    native_pose_features = {side: [] for side in ("left", "right")}
    metadata = []
    bones = list(dataset["bones"])
    for action_row in dataset["actions"]:
        if action_row["split"] != "train":
            continue
        for sample_index, sample in enumerate(action_row["samples"]):
            pose = scaffold.load_pose(sample, bones)
            left, right = scaffold.target_profile_semantics(pose, target_rest, target_anatomy)
            native["left"].append(left)
            native["right"].append(right)
            native_pose_features["left"].append(local_fit.pose_feature(sample, ARM_BONES["left"]))
            native_pose_features["right"].append(local_fit.pose_feature(sample, ARM_BONES["right"]))
            metadata.append({"action": action_row["action"], "bank": action_row["bank"],
                "bank_index": int(action_row["bank_index"]),
                "sequence_index": int(action_row["sequence_index"]),
                "normalized_time": float(sample["normalized_time"]),
                "sample_index": sample_index})
    return {"left": np.asarray(source_left), "right": np.asarray(source_right)}, {
        side: np.asarray(rows, dtype=np.float64) for side, rows in native.items()}, {
        side: np.asarray(rows, dtype=np.float64) for side, rows in native_pose_features.items()}, \
        metadata, dataset, target_rest, target_anatomy


def support_stats(source, native, scales, neighborhoods, k_values):
    result = {"neighborhood_sizes": {}, "current_continuity_neighborhood": {}}
    source = np.asarray(source, dtype=np.float64)
    native = np.asarray(native, dtype=np.float64)
    normalized = native / scales
    target_normalized = source / scales
    all_distances = np.linalg.norm(target_normalized[:, None, :] - normalized[None, :, :], axis=2)
    nearest = np.argmin(all_distances, axis=1)
    result["global_nearest"] = {
        "mean_standardized_l2": float(np.mean(all_distances[np.arange(len(source)), nearest])),
        "p95_standardized_l2": float(np.quantile(all_distances[np.arange(len(source)), nearest], 0.95)),
        "max_standardized_l2": float(np.max(all_distances[np.arange(len(source)), nearest])),
    }
    for k in k_values:
        boxes, dists, mah = [], [], []
        for frame, target in enumerate(source):
            ids = np.argpartition(all_distances[frame], k - 1)[:k]
            samples = native[ids]
            outside = np.maximum(samples.min(axis=0) - target,
                                 target - samples.max(axis=0))
            outside = np.maximum(outside, 0.0) / scales
            boxes.append({"count": int(np.count_nonzero(outside > 1e-8)),
                          "features": np.flatnonzero(outside > 1e-8).tolist(),
                          "max_normalized_excess": float(outside.max())})
            dists.append(float(all_distances[frame, nearest[frame]]))
            mah.append(covariance_distance((samples - np.mean(samples, axis=0)) / scales,
                                           (target - np.mean(samples, axis=0)) / scales))
        result["neighborhood_sizes"][str(k)] = {
            "frames_inside_axis_aligned_local_box": int(sum(row["count"] == 0 for row in boxes)),
            "frames_outside_local_box": int(sum(row["count"] > 0 for row in boxes)),
            "mean_nearest_neighbor_distance": float(np.mean(dists)),
            "p95_nearest_neighbor_distance": float(np.quantile(dists, 0.95)),
            "mahalanobis_mean": float(np.mean(mah)),
            "mahalanobis_p95": float(np.quantile(mah, 0.95)),
            "mahalanobis_max": float(np.max(mah)),
        }
    box_rows = []
    for frame, ids in enumerate(neighborhoods):
        target = source[frame]
        samples = native[ids]
        outside = np.maximum(samples.min(axis=0) - target,
                             target - samples.max(axis=0))
        outside = np.maximum(outside, 0.0) / scales
        box_rows.append({"outside_feature_indices": np.flatnonzero(outside > 1e-8).tolist(),
            "outside_feature_names": [FEATURES[i] for i in np.flatnonzero(outside > 1e-8)],
            "max_normalized_excess": float(outside.max()),
            "mahalanobis_distance": covariance_distance(
                (samples - samples.mean(axis=0)) / scales,
                (target - samples.mean(axis=0)) / scales)})
    result["current_continuity_neighborhood"] = {
        "size": int(len(neighborhoods[0])),
        "frames_inside_axis_aligned_local_box": int(sum(not row["outside_feature_indices"] for row in box_rows)),
        "frames_outside_local_box": int(sum(bool(row["outside_feature_indices"]) for row in box_rows)),
        "per_frame": box_rows,
    }
    return result, all_distances


def pairwise_support(source, native):
    ranked = []
    for first in range(11):
        for second in range(first + 1, 11):
            hull = convex_hull_2d(native[:, [first, second]])
            outside = [frame for frame, point in enumerate(source[:, [first, second]])
                       if not inside_convex_hull_2d(point, hull)]
            ranked.append({"features": [FEATURES[first], FEATURES[second]],
                           "outside_frame_count": len(outside),
                           "outside_frames": outside})
    return sorted(ranked, key=lambda row: (-row["outside_frame_count"], row["features"]))


def group_support(source, native, scales):
    rows = []
    norm_native = native / scales
    norm_source = source / scales
    for name, indices in GROUPS.items():
        candidates = norm_native[:, indices]
        counts, distances = [], []
        for target in norm_source[:, indices]:
            outside = np.maximum(candidates.min(axis=0) - target,
                                 target - candidates.max(axis=0))
            outside = np.maximum(outside, 0.0)
            counts.append(int(np.count_nonzero(outside > 1e-8)))
            distances.append(float(np.min(np.linalg.norm(candidates - target, axis=1))))
        rows.append({"group": name, "features": [FEATURES[i] for i in indices],
            "unsupported_group_dimension_count": int(sum(counts)),
            "frames_with_any_group_dimension_outside_global_box": int(sum(c > 0 for c in counts)),
            "nearest_group_distance_mean": float(np.mean(distances)),
            "nearest_group_distance_p95": float(np.quantile(distances, 0.95))})
    return rows


def feature_range_support(source, native, scales):
    rows = []
    for index, name in enumerate(FEATURES):
        source_values = source[:, index]
        native_values = native[:, index]
        nearest = np.min(np.abs(source_values[:, None] - native_values[None, :]), axis=1)
        rows.append({
            "feature": name,
            "source_min_max": [float(source_values.min()), float(source_values.max())],
            "native_min_max": [float(native_values.min()), float(native_values.max())],
            "source_range_over_native_range": float(np.ptp(source_values) /
                max(np.ptp(native_values), 1e-12)),
            "nearest_marginal_abs_error_p95": float(np.quantile(nearest, 0.95)),
        })
    return rows


def nearest_joint_feature_contributions(source, native, distances, scales, metadata):
    rows = []
    for frame, target in enumerate(source):
        index = int(np.argmin(distances[frame]))
        delta = np.abs((target - native[index]) / scales)
        ranked = np.argsort(delta)[::-1]
        rows.append({
            "frame": frame,
            "nearest_action": metadata[index]["action"],
            "nearest_sample_index": int(index),
            "nearest_joint_distance": float(distances[frame, index]),
            "largest_standardized_feature_differences": [
                {"feature": FEATURES[int(i)], "absolute_difference": float(delta[i]),
                 "source": float(target[i]), "native": float(native[index, i])}
                for i in ranked[:5]],
            "standardized_feature_difference_l2": {
                name: float(np.linalg.norm(delta[list(indices)]))
                for name, indices in GROUPS.items()},
        })
    return rows


def convex_interpolation_audit(source, native, native_pose_features, target_rest,
                               target_anatomy, side, scales, distances,
                               k_values=(32, 96, 192, 512, -1), iterations=240):
    output = {}
    # Profile bone transforms are read only to test whether a shared convex
    # mixture stays within observed per-bone SE(3) coordinate ranges.
    bones = ARM_BONES[side]
    for requested_k in k_values:
        k = len(native) if requested_k < 0 else min(requested_k, len(native))
        errors, residual_counts, support_checks = [], [], []
        max_pose_coordinate_excess = 0.0
        pose_excess_count = 0
        exact_semantic_errors = []
        mixture_action_counts = []
        projection_gaps = []
        projection_converged = []
        for frame, target in enumerate(source):
            ids = np.argpartition(distances[frame], k - 1)[:k]
            candidate_sem = (native[ids] - native.mean(axis=0)) / scales
            target_sem = (target - native.mean(axis=0)) / scales
            weights, predicted, error, projection = convex_mix(
                target_sem, candidate_sem, iterations)
            projection_gaps.append(projection["duality_gap"])
            projection_converged.append(projection["projection_converged"])
            errors.append(error)
            residual_counts.append(int(np.count_nonzero(
                np.abs((predicted - target_sem) * scales) > TOLERANCES)))
            mix_pose = weights @ native_pose_features[ids]
            local_pose = native_pose_features[ids]
            excess = np.maximum(local_pose.min(axis=0) - mix_pose,
                                mix_pose - local_pose.max(axis=0))
            excess = np.maximum(excess, 0.0)
            max_pose_coordinate_excess = max(max_pose_coordinate_excess,
                                              float(excess.max()))
            pose_excess_count += int(np.count_nonzero(excess > 1e-8))
            support_checks.append(bool(np.all(weights >= -1e-12) and
                                       abs(float(weights.sum()) - 1.0) < 1e-8))
            exact_pose = local_fit.decode_relative_pose(
                mix_pose, ARM_BONES[side], target_rest[fit.TARGET_PROFILE["torso"]]["rest_matrix"])
            exact_pose = {name: np.asarray(row["rest_matrix"], dtype=np.float64)
                          for name, row in target_rest.items()} | exact_pose
            target_profile = fit.TARGET_PROFILE if side == "left" else fit.TARGET_PROFILE_RIGHT
            side_frame = target_anatomy["sides"][side]
            exact_sem = semantic_features(target_rest, exact_pose, target_profile,
                target_anatomy["torso_to_anatomical"],
                side_frame["forearm_to_anatomical"],
                side_frame["forearm_axes_local"], elbow_absolute=True)
            exact_error = np.abs(exact_sem - target)
            exact_semantic_errors.append(exact_error)
            mixture_action_counts.append(int(np.count_nonzero(weights > 1e-3)))
        output[str(requested_k if requested_k > 0 else len(native))] = {
            "mean_standardized_semantic_residual": float(np.mean(errors)),
            "p95_standardized_semantic_residual": float(np.quantile(errors, 0.95)),
            "max_standardized_semantic_residual": float(np.max(errors)),
            "frames_with_all_semantic_residuals_within_tolerance": int(
                sum(count == 0 for count in residual_counts)),
            "frames_with_any_semantic_residual_outside_tolerance": int(
                sum(count > 0 for count in residual_counts)),
            "exact_reconstructed_pose_semantic_rmse": np.sqrt(np.mean(
                np.square(np.asarray(exact_semantic_errors)), axis=0)).tolist(),
            "exact_reconstructed_pose_frames_with_all_features_within_tolerance": int(
                np.count_nonzero(np.all(np.asarray(exact_semantic_errors) <= TOLERANCES[None, :], axis=1))),
            "exact_reconstructed_pose_worst_feature_error": float(np.max(exact_semantic_errors)),
            "mean_nontrivial_mixture_sample_count": float(np.mean(mixture_action_counts)),
            "max_convex_projection_duality_gap": float(max(projection_gaps)),
            "frames_with_projection_converged": int(sum(projection_converged)),
            "convex_weights_valid_all_frames": bool(all(support_checks)),
            "bone_transform_coordinates_outside_selected_sample_bounds": pose_excess_count,
            "max_bone_transform_coordinate_excess": max_pose_coordinate_excess,
            "bone_transform_interpolation_space": "rest-relative SE(3) translation plus rotation-vector features, using the same convex weights as semantics",
        }
    return output


def main():
    args = parse_args()
    source, native, native_pose_features, metadata, dataset, target_rest, target_anatomy = build_semantics(args)
    report = {"schema": "mg1.joint-semantic-support-audit.v1",
              "read_only": True, "retarget_solver_run": False,
              "preview_or_production_mutation": False,
              "source": {"action": args.source_action, "frames": 61,
                         "source_semantic_schema": list(FEATURES)},
              "native": {"dataset_schema": dataset["source_schema"],
                         "training_samples_per_side": {"left": len(native["left"]),
                                                        "right": len(native["right"])},
                         "real_right_samples": True},
              "support_tolerances": dict(zip(FEATURES, TOLERANCES.tolist())),
              "sides": {}}
    for side in ("left", "right"):
        source_values, native_values = source[side], native[side]
        scales = np.maximum(native_values.std(axis=0), 0.05)
        continuity = continuity_neighbors(native_values, source_values, scales,
                                            args.neighbors, args.continuity_passes)
        stats, all_distances = support_stats(source_values, native_values,
            scales, continuity, (32, 64, 96, 128, 192, 256, 384, 512, len(native_values)))
        nearest_ids = np.argmin(all_distances, axis=1)
        per_frame = []
        for frame in range(61):
            order = np.argsort(all_distances[frame])[:12]
            nearest = []
            for index in order[:5]:
                meta = metadata[int(index)]
                nearest.append({"sample_index_global": int(index),
                    "action": meta["action"], "bank": meta["bank"],
                    "sequence_index": meta["sequence_index"],
                    "sample_index_in_action": meta["sample_index"],
                    "normalized_time": meta["normalized_time"],
                    "standardized_joint_distance": float(all_distances[frame, index])})
            local = stats["current_continuity_neighborhood"]["per_frame"][frame]
            per_frame.append({"frame": frame,
                "source_semantics": source_values[frame].tolist(),
                "nearest_samples": nearest,
                "continuity_neighborhood_outside_features": local["outside_feature_names"],
                "continuity_neighborhood_max_excess": local["max_normalized_excess"],
                "continuity_neighborhood_mahalanobis": local["mahalanobis_distance"]})
        pairwise = pairwise_support(source_values, native_values)
        groups = group_support(source_values, native_values, scales)
        marginal_ranges = feature_range_support(source_values, native_values, scales)
        joint_contributions = nearest_joint_feature_contributions(
            source_values, native_values, all_distances, scales, metadata)
        problematic = [i for i, row in enumerate(stats["current_continuity_neighborhood"]["per_frame"])
                       if row["outside_feature_indices"]]
        action_scores = {}
        for frame in problematic:
            for index in np.argsort(all_distances[frame])[:1]:
                meta = metadata[int(index)]
                row = action_scores.setdefault(meta["action"], {"action": meta["action"],
                    "bank": meta["bank"], "sequence_index": meta["sequence_index"],
                    "frames_best_covered": 0, "distance_sum": 0.0,
                    "best_samples": []})
                row["frames_best_covered"] += 1
                row["distance_sum"] += float(all_distances[frame, index])
                row["best_samples"].append({"frame": frame,
                    "sample_index_in_action": meta["sample_index"],
                    "normalized_time": meta["normalized_time"],
                    "distance": float(all_distances[frame, index])})
        ranked_actions = sorted(action_scores.values(), key=lambda row: (
            -row["frames_best_covered"], row["distance_sum"] / max(row["frames_best_covered"], 1)))
        interpolation = convex_interpolation_audit(source_values, native_values,
            native_pose_features[side], target_rest, target_anatomy, side,
            scales, all_distances)
        report["sides"][side] = {
            "semantic_scale_native_std": scales.tolist(),
            "support_distance_and_neighborhood_sizes": stats,
            "joint_local_knee": {"current_k": args.neighbors,
                "unsupported_local_frames": problematic,
                "unsupported_count": len(problematic)},
            "feature_group_support": groups,
            "marginal_feature_ranges": marginal_ranges,
            "nearest_joint_feature_contributions": joint_contributions,
            "pairwise_2d_hull_outside_pair_count": int(sum(
                row["outside_frame_count"] > 0 for row in pairwise)),
            "worst_pairwise_2d_hull_gaps": [row for row in pairwise
                if row["outside_frame_count"] > 0][:20],
            "best_native_actions_for_local_failures": ranked_actions[:25],
            "convex_mixture_feasibility": interpolation,
            "per_frame": per_frame,
        }
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    summary = {side: {
        "nearest_p95": row["support_distance_and_neighborhood_sizes"]["global_nearest"]["p95_standardized_l2"],
        "k96_outside": row["joint_local_knee"]["unsupported_count"],
        "worst_pairs": row["worst_pairwise_2d_hull_gaps"][:5],
        "convex_k96": row["convex_mixture_feasibility"]["96"]}
        for side, row in report["sides"].items()}
    print(json.dumps({"output": os.path.abspath(args.output), "summary": summary}, indent=2))


if __name__ == "__main__":
    main()
