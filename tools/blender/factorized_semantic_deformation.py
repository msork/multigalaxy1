"""NumPy-only factorized semantic-to-rest-relative-pose regression."""
from __future__ import annotations

import numpy as np


FACTOR_GROUPS = {
    "shoulder_orientation": (0, 1, 2),
    "elbow_flexion": (3,),
    "forearm_roll": (4,),
    "wrist_orientation": (5, 6, 7),
    "arm_plane_hand_position": (8, 9, 10),
}
CROSS_FACTOR_PAIRS = (
    ("shoulder_orientation", "elbow_flexion"),
    ("shoulder_orientation", "wrist_orientation"),
    ("elbow_flexion", "wrist_orientation"),
    ("wrist_orientation", "arm_plane_hand_position"),
    ("elbow_flexion", "hand_radial"),
)


def _standardize_features(values, mean=None, scale=None):
    x = np.asarray(values, dtype=np.float64)
    if mean is None:
        mean = x.mean(axis=0)
    if scale is None:
        scale = np.maximum(x.std(axis=0), 0.05)
    return (x - mean) / scale, np.asarray(mean), np.asarray(scale)


def _pairwise_products(values, left_indices, right_indices):
    return np.column_stack([
        values[:, i] * values[:, j]
        for i in left_indices for j in right_indices
    ])


def factorized_design(values, mean=None, scale=None, quadratic_center=None):
    """Build additive factor blocks plus the predeclared audited cross terms."""
    z, mean, scale = _standardize_features(values, mean, scale)
    fit_centers = quadratic_center is None
    if quadratic_center is None:
        quadratic_center = {}
    blocks = {}
    columns = [np.ones((len(z), 1), dtype=np.float64)]
    cursor = 1
    for name, indices in FACTOR_GROUPS.items():
        part = z[:, indices]
        q = np.column_stack([part[:, i] * part[:, j]
                             for i in range(part.shape[1])
                             for j in range(i, part.shape[1])])
        if fit_centers:
            qcenter = q.mean(axis=0)
        else:
            qcenter = np.asarray(quadratic_center[name], dtype=np.float64)
        block = np.column_stack((part, q - qcenter))
        columns.append(block)
        blocks[name] = (cursor, cursor + block.shape[1])
        cursor += block.shape[1]
        quadratic_center[name] = qcenter

    interactions = {}
    for left, right in CROSS_FACTOR_PAIRS:
        if right == "hand_radial":
            left_idx, right_idx = FACTOR_GROUPS["elbow_flexion"], (8,)
            label = "elbow_flexion_x_hand_radial"
        else:
            left_idx, right_idx = FACTOR_GROUPS[left], FACTOR_GROUPS[right]
            label = left + "_x_" + right
        block = _pairwise_products(z, left_idx, right_idx)
        columns.append(block)
        interactions[label] = (cursor, cursor + block.shape[1])
        cursor += block.shape[1]
    design = np.column_stack(columns)
    return design, {"factors": blocks, "interactions": interactions,
                    "mean": mean.tolist(), "scale": scale.tolist(),
                    "quadratic_center": {k: v.tolist() for k, v in quadratic_center.items()},
                    "feature_names": int(design.shape[1])}


def global_quadratic_design(values, mean=None, scale=None):
    """Second-order all-feature supervised inverse baseline."""
    z, mean, scale = _standardize_features(values, mean, scale)
    quad = np.column_stack([z[:, i] * z[:, j]
                            for i in range(z.shape[1])
                            for j in range(i, z.shape[1])])
    design = np.column_stack((np.ones(len(z)), z, quad))
    return design, {"mean": mean.tolist(), "scale": scale.tolist(),
                    "feature_names": int(design.shape[1])}


def _weighted_ridge(design, target, weights, alpha):
    x = np.asarray(design, dtype=np.float64)
    y = np.asarray(target, dtype=np.float64)
    w = np.asarray(weights, dtype=np.float64)
    xw = x * np.sqrt(w[:, None])
    yw = y * np.sqrt(w[:, None])
    gram = xw.T @ xw
    penalty = np.eye(gram.shape[0]) * float(alpha)
    penalty[0, 0] = 0.0
    return np.linalg.solve(gram + penalty, xw.T @ yw)


def action_balanced_weights(action_ids):
    action_ids = np.asarray(action_ids)
    unique, inverse, counts = np.unique(action_ids, return_inverse=True,
                                        return_counts=True)
    weights = 1.0 / counts[inverse]
    return weights * len(weights) / weights.sum()


def action_group_folds(action_ids, fold_count=5):
    names = sorted(set(str(x) for x in action_ids))
    fold_for = {name: i % int(fold_count) for i, name in enumerate(names)}
    return np.asarray([fold_for[str(x)] for x in action_ids], dtype=np.int32)


def fit_ridge_cv(design, target, action_ids,
                 alphas=(0.01, 0.1, 1.0, 10.0, 100.0, 1000.0, 10000.0),
                 fold_count=5):
    """Choose ridge strength by complete-action grouped cross-validation."""
    x, raw_y = np.asarray(design), np.asarray(target, dtype=np.float64)
    action_ids = np.asarray(action_ids)
    folds = action_group_folds(action_ids, fold_count)
    scores = []
    for alpha in alphas:
        fold_scores = []
        for fold in range(fold_count):
            train = folds != fold
            valid = ~train
            if not np.any(valid):
                continue
            y_mean = raw_y[train].mean(axis=0)
            y_scale = np.maximum(raw_y[train].std(axis=0), 1e-3)
            y_train = (raw_y[train] - y_mean) / y_scale
            weights = action_balanced_weights(action_ids[train])
            coef = _weighted_ridge(x[train], y_train, weights, alpha)
            pred = (x[valid] @ coef) * y_scale + y_mean
            err = (pred - raw_y[valid]) / y_scale
            # Average within each action first, so long clips do not dominate.
            valid_names = action_ids[valid]
            action_err = [float(np.mean(err[valid_names == name] ** 2))
                          for name in sorted(set(valid_names.tolist()))]
            fold_scores.append(float(np.mean(action_err)))
        scores.append({"alpha": float(alpha),
                       "action_grouped_cv_mse": float(np.mean(fold_scores)),
                       "fold_mse": fold_scores})
    chosen = min(scores, key=lambda row: row["action_grouped_cv_mse"])
    y_mean = raw_y.mean(axis=0)
    y_scale = np.maximum(raw_y.std(axis=0), 1e-3)
    weights = action_balanced_weights(action_ids)
    coef = _weighted_ridge(x, (raw_y - y_mean) / y_scale,
                           weights, chosen["alpha"])
    return {"coefficients": coef, "target_mean": y_mean,
            "target_scale": y_scale, "alpha": chosen["alpha"],
            "cross_validation": scores,
            "chosen_cv_mse": chosen["action_grouped_cv_mse"]}


def predict(model, design):
    return (np.asarray(design) @ model["coefficients"]) * model["target_scale"] + model["target_mean"]


def support_summary(train_values, query_values, indices, scales=None):
    """Training-only range and Mahalanobis confidence for one feature factor."""
    train = np.asarray(train_values, dtype=np.float64)[:, indices]
    query = np.asarray(query_values, dtype=np.float64)[:, indices]
    scale = np.maximum(train.std(axis=0), 0.05) if scales is None else np.asarray(scales)
    ztrain = train / scale
    zquery = query / scale
    center = ztrain.mean(axis=0)
    covariance = np.cov(ztrain, rowvar=False)
    covariance = np.atleast_2d(covariance)
    covariance += np.eye(len(indices)) * 0.08
    inverse = np.linalg.pinv(covariance)
    delta = zquery - center
    distance = np.sqrt(np.maximum(0.0, np.einsum("ni,ij,nj->n", delta, inverse, delta)))
    outside = ((query < train.min(axis=0)) | (query > train.max(axis=0))).any(axis=1)
    confidence = np.exp(-0.5 * distance * distance / max(len(indices), 1))
    return {"features": [int(i) for i in indices],
            "outside_training_axis_range_count": int(outside.sum()),
            "distance_mean": float(distance.mean()),
            "distance_p95": float(np.quantile(distance, 0.95)),
            "confidence_mean": float(confidence.mean()),
            "confidence_p05": float(np.quantile(confidence, 0.05))}


def factor_confidences(train_values, query_values):
    result = {name: support_summary(train_values, query_values, indices)
              for name, indices in FACTOR_GROUPS.items()}
    interaction_indices = {
        "shoulder_orientation_x_elbow_flexion": (0, 1, 2, 3),
        "shoulder_orientation_x_wrist_orientation": (0, 1, 2, 5, 6, 7),
        "elbow_flexion_x_wrist_orientation": (3, 5, 6, 7),
        "wrist_orientation_x_arm_plane_hand_position": (5, 6, 7, 8, 9, 10),
        "elbow_flexion_x_hand_radial": (3, 8),
    }
    result["cross_terms"] = {name: support_summary(train_values, query_values, indices)
                             for name, indices in interaction_indices.items()}
    return result


def factor_contribution_summary(design, coefficients, slices):
    """Summarize block predictions without conflating them with support."""
    output = {}
    for name, (start, end) in slices["factors"].items():
        values = np.asarray(design)[:, start:end] @ coefficients[start:end]
        output[name] = {"rms_target_standardized_contribution": float(
            np.sqrt(np.mean(values ** 2))), "coefficient_shape": list(
                coefficients[start:end].shape)}
    for name, (start, end) in slices["interactions"].items():
        values = np.asarray(design)[:, start:end] @ coefficients[start:end]
        output[name] = {"rms_target_standardized_contribution": float(
            np.sqrt(np.mean(values ** 2))), "coefficient_shape": list(
                coefficients[start:end].shape)}
    return output
