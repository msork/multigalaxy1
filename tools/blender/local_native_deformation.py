"""Neighbor-supported local affine deformation mapping for native poses."""
from __future__ import annotations

import numpy as np


def local_affine_predict(native_semantics, native_poses, target_semantics,
                         semantic_scale, k=80, ridge=2e-3,
                         previous_neighbors=None, continuity_weight=0.2,
                         pose_scale=None):
    """Fit a weighted local affine map and predict one full pose feature row.

    Native pose rows are rest-relative SE(3) descriptors. Semantic distances
    are normalized per feature. A previous neighborhood is included as a soft
    continuity prior, while target support is checked against the selected
    neighborhood's convex-box bounds.
    """
    s = np.asarray(native_semantics, dtype=np.float64)
    p = np.asarray(native_poses, dtype=np.float64)
    target = np.asarray(target_semantics, dtype=np.float64)
    scale = np.maximum(np.asarray(semantic_scale, dtype=np.float64), 1e-5)
    delta = (s - target) / scale
    distances = np.linalg.norm(delta, axis=1)
    count = min(max(4, int(k)), len(s))
    nearest = np.argpartition(distances, count - 1)[:count]
    candidates = nearest
    if previous_neighbors is not None:
        prior = np.asarray(previous_neighbors, dtype=np.int64)
        candidates = np.unique(np.r_[nearest, prior])
    local_delta = delta[candidates]
    local_distance = np.linalg.norm(local_delta, axis=1)
    bandwidth = max(float(np.quantile(local_distance, 0.75)), 0.05)
    weights = np.exp(-0.5 * (local_distance / bandwidth) ** 2)
    if previous_neighbors is not None:
        prior_set = set(np.asarray(previous_neighbors, dtype=np.int64).tolist())
        weights *= np.asarray([1.0 + continuity_weight if int(i) in prior_set else 1.0
                               for i in candidates])
    weights = np.maximum(weights, 1e-8)
    weights /= weights.sum()
    s0 = weights @ s[candidates]
    p0 = weights @ p[candidates]
    ds = (s[candidates] - s0) / scale
    dp = p[candidates] - p0
    gram = ds.T @ (weights[:, None] * ds) + ridge * np.eye(s.shape[1])
    jacobian = np.linalg.solve(gram, ds.T @ (weights[:, None] * dp))
    prediction = p0 + ((target - s0) / scale) @ jacobian
    # Local support: target must lie inside the actual semantic neighborhood
    # (with a small numerical margin), and its nearest normalized distance must
    # be no larger than the neighborhood's 90th-percentile radius.
    lo = s[candidates].min(axis=0) - 0.02 * scale
    hi = s[candidates].max(axis=0) + 0.02 * scale
    in_box = bool(np.all(target >= lo) and np.all(target <= hi))
    unsupported_axes = np.flatnonzero((target < lo) | (target > hi)).tolist()
    nearest_distance = float(distances.min())
    radius = float(np.quantile(local_distance, 0.90))
    supported = in_box and nearest_distance <= max(radius, 0.15)
    pose_residuals = np.linalg.norm((p[candidates] - p0) /
        (np.ones(p.shape[1]) if pose_scale is None else pose_scale), axis=1)
    trust_radius = float(np.quantile(pose_residuals, 0.95))
    prediction_distance = float(np.linalg.norm((prediction - p0) /
        (np.ones(p.shape[1]) if pose_scale is None else pose_scale)))
    if prediction_distance > trust_radius and prediction_distance > 1e-12:
        prediction = p0 + (prediction - p0) * (trust_radius / prediction_distance)
    return prediction, {
        "neighbors": candidates,
        "weights": weights,
        "nearest_semantic_distance": nearest_distance,
        "local_semantic_radius_90": radius,
        "inside_local_semantic_box": in_box,
        "unsupported_semantic_axes": unsupported_axes,
        "supported": supported,
        "local_pose_trust_radius": trust_radius,
        "predicted_pose_distance": min(prediction_distance, trust_radius),
        "jacobian": jacobian,
    }


def fit_local_sequence(native_semantics, native_poses, target_semantics,
                       semantic_scale, k=80, passes=3, pose_scale=None):
    """Jointly refresh per-frame neighborhoods, carrying continuity forward."""
    targets = np.asarray(target_semantics, dtype=np.float64)
    outputs = np.zeros((len(targets), np.asarray(native_poses).shape[1]))
    diagnostics = [None] * len(targets)
    neighborhoods = [None] * len(targets)
    for iteration in range(max(1, passes)):
        for direction in (range(len(targets)), range(len(targets) - 1, -1, -1)):
            for frame in direction:
                adjacent = []
                if frame > 0 and neighborhoods[frame - 1] is not None:
                    adjacent.extend(neighborhoods[frame - 1].tolist())
                if frame + 1 < len(targets) and neighborhoods[frame + 1] is not None:
                    adjacent.extend(neighborhoods[frame + 1].tolist())
                previous = np.unique(adjacent) if adjacent else None
                pose, info = local_affine_predict(native_semantics, native_poses,
                    targets[frame], semantic_scale, k=k,
                    previous_neighbors=previous,
                    pose_scale=pose_scale,
                    continuity_weight=0.5 + 0.25 * iteration)
                outputs[frame] = pose
                diagnostics[frame] = info
                neighborhoods[frame] = info["neighbors"]
    return outputs, diagnostics
