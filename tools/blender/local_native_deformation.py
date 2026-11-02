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
    # Keep the neighborhood fixed-size. A growing union with adjacent frames
    # silently changes the requested k and can turn the local fit into a
    # sequence-wide/global fit. Continuity only biases selection among the
    # native candidates; each frame still receives exactly k neighbors.
    score = distances.copy()
    prior_set = set()
    if previous_neighbors is not None:
        prior_set = set(np.asarray(previous_neighbors, dtype=np.int64).tolist())
        continuity_penalty = max(float(np.quantile(distances, 0.25)), 0.02) * float(continuity_weight)
        score += np.asarray([0.0 if int(i) in prior_set else continuity_penalty
                             for i in range(len(score))])
    candidates = np.argpartition(score, count - 1)[:count]
    local_delta = delta[candidates]
    local_distance = np.linalg.norm(local_delta, axis=1)
    bandwidth = max(float(np.quantile(local_distance, 0.75)), 0.05)
    weights = np.exp(-0.5 * (local_distance / bandwidth) ** 2)
    if previous_neighbors is not None:
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
        "pose_support_low": p[candidates].min(axis=0),
        "pose_support_high": p[candidates].max(axis=0),
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


def joint_temporal_refine(pose_sequence, feature_scale=None,
                          lambda_velocity=0.25,
                          lambda_acceleration=0.75,
                          trust_radii=None, iterations=80):
    """Jointly minimize pose fidelity plus SE(3)-coordinate velocity/acceleration.

    Rows are per-frame concatenated translation/rotation-vector descriptors.
    Translations should be divided by a characteristic limb length before
    calling, so translation and angular coordinates have comparable meaning.
    This is an in-fit constrained optimization; it is never applied to a
    baked action. Per-frame trust radii bound deviation from local predictions.
    """
    values = np.asarray(pose_sequence, dtype=np.float64)
    if values.ndim != 2 or len(values) < 3:
        raise ValueError("joint temporal fit expects a frame-by-feature matrix")
    scale = (np.ones(values.shape[1], dtype=np.float64) if feature_scale is None
             else np.maximum(np.asarray(feature_scale, dtype=np.float64), 1e-8))
    if scale.shape != (values.shape[1],):
        raise ValueError("feature_scale has the wrong shape")
    y = values / scale
    count = len(y)
    velocity = np.zeros((count - 1, count), dtype=np.float64)
    for row in range(count - 1):
        velocity[row, row:row + 2] = (-1.0, 1.0)
    acceleration = np.zeros((count - 2, count), dtype=np.float64)
    for row in range(count - 2):
        acceleration[row, row:row + 3] = (1.0, -2.0, 1.0)
    hessian = (np.eye(count) + float(lambda_velocity) * velocity.T @ velocity +
               float(lambda_acceleration) * acceleration.T @ acceleration)
    # Projected gradient solves the joint quadratic with a local-pose trust
    # ball for every frame. The Lipschitz step is conservative and deterministic.
    lipschitz = float(np.linalg.eigvalsh(hessian)[-1])
    step = 1.0 / max(lipschitz, 1e-8)
    x = y.copy()
    radii = (None if trust_radii is None else
             np.asarray(trust_radii, dtype=np.float64).reshape(count))
    for _ in range(max(1, int(iterations))):
        gradient = hessian @ x - y
        updated = x - step * gradient
        if radii is not None:
            delta = updated - y
            norms = np.linalg.norm(delta, axis=1)
            factors = np.minimum(1.0, radii / np.maximum(norms, 1e-12))
            updated = y + delta * factors[:, None]
        if float(np.max(np.abs(updated - x))) < 1e-9:
            x = updated
            break
        x = updated
    output = x * scale
    return output, {
        "lambda_velocity": float(lambda_velocity),
        "lambda_acceleration": float(lambda_acceleration),
        "max_frame_deviation": float(np.linalg.norm((output - values) / scale, axis=1).max()),
        "rms_frame_deviation": float(np.sqrt(np.mean(np.sum(((output - values) / scale) ** 2, axis=1)))),
        "max_velocity": float(np.linalg.norm(np.diff(output / scale, axis=0), axis=1).max()),
        "max_acceleration": float(np.linalg.norm(np.diff(output / scale, n=2, axis=0), axis=1).max()),
    }
