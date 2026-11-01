"""Fit training-only linear and nonlinear models to a converted arm dataset.

Run with Blender's bundled Python (which supplies NumPy):
  blender --background --python tools/blender/fit_native_arm_pose_model.py -- \
    --dataset tools/blender/reports/native_waluigi_arm_calibration_production_basis.json.gz \
    --model-output tools/blender/reports/native_arm_pose_models.npz \
    --predictions-output tools/blender/reports/native_arm_pose_predictions.npz \
    --report-output tools/blender/reports/native_arm_pose_model_metrics.json

Only actions marked train are used for scaling, cross-validation, and fitting.
Holdouts are transformed only after the final model settings are frozen.
"""
import argparse
import gzip
import json
import math
import os
import sys

import numpy as np


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--model-output", required=True)
    parser.add_argument("--predictions-output", required=True)
    parser.add_argument("--report-output", required=True)
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--rbf-centers", type=int, default=48)
    return parser.parse_args(argv)


def load_dataset(path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        data = json.load(stream)
    if data.get("schema") != "mg1.native-arm-calibration.production-basis.v1":
        raise ValueError("unexpected dataset schema")
    if data.get("read_only_source") is not True:
        raise ValueError("dataset is not marked as a read-only derived artifact")
    bones = list(data["bones"])
    train, holdout = [], []
    for action in data["actions"]:
        row = {"name": action["action"], "split": action["split"],
               "times": np.asarray([s["time"] for s in action["samples"]], dtype=np.float64),
               "features": np.asarray([encode_sample(s, bones) for s in action["samples"]], dtype=np.float64),
               "samples": action["samples"]}
        (train if action["split"] == "train" else holdout).append(row)
    expected_train = int(data.get("training_action_count", len(train)))
    expected_holdout = int(data.get("holdout_action_count", len(holdout)))
    if len(train) != expected_train or len(holdout) != expected_holdout or not train or not holdout:
        raise ValueError(f"dataset split counts disagree with metadata: {len(train)}/{len(holdout)}")
    return data, bones, train, holdout


def encode_sample(sample, bones):
    values = []
    for bone in bones:
        transform = sample["bones"][bone]["rest_relative_se3"]
        scale = np.asarray(transform["scale"], dtype=np.float64)
        if np.max(np.abs(scale - 1.0)) > 2e-4:
            raise ValueError(f"non-unit scale in dataset transform for {bone}: {scale}")
        q = np.asarray(transform["rotation_quaternion"], dtype=np.float64)
        q /= np.linalg.norm(q)
        # q and -q encode the same orientation. Canonicalize before log map.
        if q[0] < 0:
            q = -q
        xyz = q[1:]
        sn = float(np.linalg.norm(xyz))
        angle = 2.0 * math.atan2(sn, float(q[0]))
        rotvec = xyz * (angle / sn) if sn > 1e-10 else 2.0 * xyz
        values.extend(transform["translation"])
        values.extend(rotvec.tolist())
    return values


def grouped_folds(actions, count):
    if count < 2:
        raise ValueError("at least two action folds are required")
    count = min(count, len(actions))
    bins = [[] for _ in range(count)]
    totals = [0] * count
    # Greedy action-level balance; action samples never cross a fold boundary.
    for action in sorted(actions, key=lambda a: (-len(a["features"]), a["name"])):
        index = min(range(count), key=lambda i: (totals[i], i))
        bins[index].append(action["name"])
        totals[index] += len(action["features"])
    return [set(group) for group in bins]


def flatten(actions):
    return np.concatenate([a["features"] for a in actions], axis=0)


def standardize_fit(x):
    mean = x.mean(axis=0)
    scale = x.std(axis=0)
    floor = max(float(np.median(scale)) * 1e-4, 1e-8)
    scale = np.maximum(scale, floor)
    return mean, scale


def fit_pca(x, dims):
    mean, scale = standardize_fit(x)
    z = (x - mean) / scale
    _u, _s, vt = np.linalg.svd(z, full_matrices=False)
    components = vt[:dims].copy()
    return {"mean": mean, "scale": scale, "components": components}, components


def pca_predict(model, x):
    z = (x - model["mean"]) / model["scale"]
    components = model["components"]
    return (z @ components.T) @ components * model["scale"] + model["mean"]


def latent_of(model, x):
    return ((x - model["mean"]) / model["scale"]) @ model["components"].T


def farthest_centers(z, count):
    count = min(count, len(z))
    center = z.mean(axis=0)
    dist = np.sum((z - center) ** 2, axis=1)
    chosen = [int(np.argmax(dist))]
    nearest = np.sum((z - z[chosen[0]]) ** 2, axis=1)
    for _ in range(1, count):
        index = int(np.argmax(nearest))
        chosen.append(index)
        nearest = np.minimum(nearest, np.sum((z - z[index]) ** 2, axis=1))
    return z[chosen].copy()


def rbf_design(z, centers, sigma):
    d2 = np.sum((z[:, None, :] - centers[None, :, :]) ** 2, axis=2)
    return np.exp(-0.5 * d2 / max(float(sigma) ** 2, 1e-12))


def fit_rbf_decoder(z, y, count, width_factor, ridge):
    centers = farthest_centers(z, count)
    pair = np.sqrt(np.sum((centers[:, None, :] - centers[None, :, :]) ** 2, axis=2))
    pair[pair == 0] = np.inf
    sigma = float(np.median(np.min(pair, axis=1))) * width_factor
    if not math.isfinite(sigma) or sigma < 1e-8:
        sigma = max(float(np.mean(np.std(z, axis=0))), 1e-3)
    phi = rbf_design(z, centers, sigma)
    gram = phi.T @ phi
    weights = np.linalg.solve(gram + ridge * np.eye(gram.shape[0]), phi.T @ y)
    return {"centers": centers, "sigma": sigma, "weights": weights}


def rbf_predict(model, latent):
    return rbf_design(latent, model["centers"], model["sigma"]) @ model["weights"]


def normalized_mse(predicted, actual, scale):
    return float(np.mean(((predicted - actual) / scale) ** 2))


def cv_pca(train_actions, folds_count):
    folds = grouped_folds(train_actions, folds_count)
    dims = [2, 4, 6, 8, 10, 12, 16, 20, 24, 32, 40, 48]
    by_dim = {dim: [] for dim in dims}
    for val_names in folds:
        fit_actions = [a for a in train_actions if a["name"] not in val_names]
        val_actions = [a for a in train_actions if a["name"] in val_names]
        xfit, xval = flatten(fit_actions), flatten(val_actions)
        mean, scale = standardize_fit(xfit)
        z = (xfit - mean) / scale
        _u, _s, vt = np.linalg.svd(z, full_matrices=False)
        zv = (xval - mean) / scale
        max_dim = min(vt.shape[0], vt.shape[1])
        for dim in dims:
            k = min(dim, max_dim)
            proj = zv @ vt[:k].T @ vt[:k]
            by_dim[dim].append(float(np.mean((zv - proj) ** 2)))
    stats = {}
    for dim, values in by_dim.items():
        stats[dim] = {"fold_mse": values, "mean_mse": float(np.mean(values)),
                      "se_mse": float(np.std(values, ddof=1) / math.sqrt(len(values)))}
    best_dim = min(dims, key=lambda dim: stats[dim]["mean_mse"])
    # Float64 SVD leaves tiny positive residuals for a numerically exact span.
    # Treat sub-1e-10 normalized MSE as a tie so the one-SE rule picks the
    # smallest numerically equivalent model instead of keeping null directions.
    threshold = max(stats[best_dim]["mean_mse"] + stats[best_dim]["se_mse"],
                    stats[best_dim]["mean_mse"] + 1e-10)
    chosen = min(dim for dim in dims if stats[dim]["mean_mse"] <= threshold)
    return chosen, stats, folds


def cv_rbf(train_actions, folds, dims, center_count):
    candidates = [(width, ridge) for width in (0.6, 1.0, 1.8, 3.0)
                  for ridge in (1e-4, 1e-3, 1e-2, 1e-1)]
    scores = {candidate: [] for candidate in candidates}
    for val_names in folds:
        fit_actions = [a for a in train_actions if a["name"] not in val_names]
        val_actions = [a for a in train_actions if a["name"] in val_names]
        xfit, xval = flatten(fit_actions), flatten(val_actions)
        encoder, _ = fit_pca(xfit, dims)
        latent_fit, latent_val = latent_of(encoder, xfit), latent_of(encoder, xval)
        yfit = (xfit - encoder["mean"]) / encoder["scale"]
        yval = (xval - encoder["mean"]) / encoder["scale"]
        centers = farthest_centers(latent_fit, center_count)
        pair = np.sqrt(np.sum((centers[:, None, :] - centers[None, :, :]) ** 2, axis=2))
        pair[pair == 0] = np.inf
        base_sigma = float(np.median(np.min(pair, axis=1)))
        if not math.isfinite(base_sigma) or base_sigma < 1e-8:
            base_sigma = max(float(np.mean(np.std(latent_fit, axis=0))), 1e-3)
        for width, ridge in candidates:
            sigma = base_sigma * width
            phi_fit = rbf_design(latent_fit, centers, sigma)
            gram = phi_fit.T @ phi_fit
            weights = np.linalg.solve(gram + ridge * np.eye(gram.shape[0]), phi_fit.T @ yfit)
            prediction = rbf_design(latent_val, centers, sigma) @ weights
            scores[(width, ridge)].append(float(np.mean((prediction - yval) ** 2)))
    summary = [{"width_factor": w, "ridge": r, "fold_mse": vals,
                "mean_mse": float(np.mean(vals)),
                "se_mse": float(np.std(vals, ddof=1) / math.sqrt(len(vals)))}
               for (w, r), vals in scores.items()]
    chosen = min(summary, key=lambda row: (row["mean_mse"], row["width_factor"], row["ridge"]))
    return chosen, summary


def quat_from_rotvec(v):
    angle = float(np.linalg.norm(v))
    if angle < 1e-10:
        return np.asarray([1.0, *(0.5 * v)], dtype=np.float64)
    return np.asarray([math.cos(angle * 0.5), *(v * (math.sin(angle * 0.5) / angle))])


def quat_angle(a, b):
    qa, qb = quat_from_rotvec(a), quat_from_rotvec(b)
    dot = min(1.0, abs(float(np.dot(qa, qb) / (np.linalg.norm(qa) * np.linalg.norm(qb)))))
    return 2.0 * math.acos(dot)


def predicted_landmarks(feature, dataset, bones):
    output = {}
    world = np.asarray(dataset["rig_matrix_world"], dtype=np.float64)
    for name in bones:
        index = bones.index(name) * 6
        rest = np.asarray(dataset["rest_bones"][name]["rest_matrix"], dtype=np.float64)
        delta = np.eye(4)
        delta[:3, :3] = rotmat_from_rotvec(feature[index + 3:index + 6])
        delta[:3, 3] = feature[index:index + 3]
        pose = rest @ delta
        rest_inv = np.linalg.inv(rest)
        endpoints = dataset["rest_bones"][name]
        for endpoint in ("head", "tail"):
            local_point = rest_inv @ np.asarray([*endpoints[endpoint], 1.0])
            output[(name, endpoint)] = (world @ pose @ local_point)[:3]
    return output


def rotmat_from_rotvec(v):
    angle = float(np.linalg.norm(v))
    if angle < 1e-10:
        x, y, z = v
        return np.asarray([[1, -z, y], [z, 1, -x], [-y, x, 1]], dtype=np.float64)
    axis = v / angle
    x, y, z = axis
    c, s, q = math.cos(angle), math.sin(angle), 1.0 - math.cos(angle)
    return np.asarray([[c+x*x*q, x*y*q-z*s, x*z*q+y*s],
                       [y*x*q+z*s, c+y*y*q, y*z*q-x*s],
                       [z*x*q-y*s, z*y*q+x*s, c+z*z*q]], dtype=np.float64)


def action_metrics(action, predictions, dataset, bones, mean, scale):
    actual = action["features"]
    metrics = {"sample_count": len(actual), "per_bone": {}, "landmarks_world": {},
               "temporal": {}}
    metrics["feature_rmse_standardized"] = float(np.sqrt(np.mean(((predictions - actual) / scale) ** 2)))
    for i, bone in enumerate(bones):
        sl = slice(i * 6, (i + 1) * 6)
        trans = np.linalg.norm(predictions[:, sl.start:sl.start + 3] - actual[:, sl.start:sl.start + 3], axis=1)
        angle = np.asarray([quat_angle(a, b) for a, b in zip(actual[:, sl.start + 3:sl.stop], predictions[:, sl.start + 3:sl.stop])])
        metrics["per_bone"][bone] = {"translation_rmse_armature": float(np.sqrt(np.mean(trans ** 2))),
            "translation_rmse_world": float(np.sqrt(np.mean((trans * dataset["rig_scale"][0]) ** 2))),
            "rotation_rmse_degrees": float(np.rad2deg(np.sqrt(np.mean(angle ** 2)))),
            "rotation_max_degrees": float(np.rad2deg(np.max(angle)))}
    landmarks = {name: (row["bone"], row["endpoint"])
                 for name, row in action["samples"][0]["branch_landmarks"].items()}
    errors = {key: [] for key in landmarks}
    for idx, sample in enumerate(action["samples"]):
        points = predicted_landmarks(predictions[idx], dataset, bones)
        for label, (bone, endpoint) in landmarks.items():
            pred = points[(bone, endpoint)]
            actual_point = np.asarray(sample["branch_landmarks"][label]["world"], dtype=np.float64)
            errors[label].append(float(np.linalg.norm(pred - actual_point)))
    metrics["landmarks_world"] = {key: {"rmse": float(np.sqrt(np.mean(np.square(vals)))),
                                                    "max": float(np.max(vals))}
                                  for key, vals in errors.items()}
    times = action["times"]
    if len(times) >= 3:
        dt = np.diff(times)
        valid = dt > 0
        vel_a = np.diff(actual, axis=0)[valid] / dt[valid, None]
        vel_p = np.diff(predictions, axis=0)[valid] / dt[valid, None]
        acc_a = 2.0 * (vel_a[1:] - vel_a[:-1]) / (times[2:] - times[:-2])[:, None]
        acc_p = 2.0 * (vel_p[1:] - vel_p[:-1]) / (times[2:] - times[:-2])[:, None]
        normalized_acc_err = (acc_p - acc_a) / scale
        acc_source = acc_a / scale
        metrics["temporal"] = {
            "velocity_rmse_standardized_per_frame": float(np.sqrt(np.mean(((vel_p - vel_a) / scale) ** 2))),
            "acceleration_rmse_standardized_per_frame2": float(np.sqrt(np.mean(normalized_acc_err ** 2))),
            "source_acceleration_rms_standardized_per_frame2": float(np.sqrt(np.mean(acc_source ** 2))),
            "reconstructed_acceleration_rms_standardized_per_frame2": float(np.sqrt(np.mean((acc_p / scale) ** 2))),
        }
    return metrics


def aggregate_actions(action_rows, split):
    selected = [row for row in action_rows if row["split"] == split]
    bones = sorted(selected[0]["metrics"]["per_bone"])
    result = {"action_count": len(selected), "sample_count": sum(row["metrics"]["sample_count"] for row in selected),
              "per_bone": {}, "landmarks_world": {}, "temporal": {}}
    count_all = result["sample_count"]
    result["feature_rmse_standardized"] = float(math.sqrt(sum(row["metrics"]["feature_rmse_standardized"] ** 2 * row["metrics"]["sample_count"] for row in selected) / count_all))
    for bone in bones:
        vals = [(row["metrics"]["per_bone"][bone], row["metrics"]["sample_count"]) for row in selected]
        count = sum(n for _, n in vals)
        result["per_bone"][bone] = {key: float(math.sqrt(sum(v[key] ** 2 * n for v, n in vals) / count))
                                    if "rmse" in key else float(max(v[key] for v, _ in vals))
                                    for key in vals[0][0]}
    labels = selected[0]["metrics"]["landmarks_world"]
    for label in labels:
        result["landmarks_world"][label] = {
            "rmse": float(math.sqrt(sum(row["metrics"]["landmarks_world"][label]["rmse"] ** 2 * row["metrics"]["sample_count"] for row in selected) / result["sample_count"])),
            "max": float(max(row["metrics"]["landmarks_world"][label]["max"] for row in selected))}
    for key in selected[0]["metrics"]["temporal"]:
        result["temporal"][key] = float(math.sqrt(sum(row["metrics"]["temporal"][key] ** 2 * row["metrics"]["sample_count"] for row in selected) / result["sample_count"]))
    result["worst_bone_by_rotation_rmse"] = max(bones, key=lambda b: result["per_bone"][b]["rotation_rmse_degrees"])
    result["worst_bone_by_translation_rmse"] = max(bones, key=lambda b: result["per_bone"][b]["translation_rmse_world"])
    return result


def main():
    args = parse_args()
    dataset, bones, train, holdout = load_dataset(args.dataset)
    xtrain = flatten(train)
    latent_dims, cv_pca_report, folds = cv_pca(train, args.folds)
    rbf_choice, cv_rbf_report = cv_rbf(train, folds, latent_dims, args.rbf_centers)
    pca, _ = fit_pca(xtrain, latent_dims)
    latent_train = latent_of(pca, xtrain)
    xmean, xscale = pca["mean"], pca["scale"]
    rbf = fit_rbf_decoder(latent_train, (xtrain - xmean) / xscale, args.rbf_centers,
                          rbf_choice["width_factor"], rbf_choice["ridge"])
    rbf_model = {**pca, **rbf}

    predictions = {"PCA": {}, "RBF": {}}
    action_metric_rows = {"PCA": [], "RBF": []}
    for action in train + holdout:
        pca_pred = pca_predict(pca, action["features"])
        latent = latent_of(pca, action["features"])
        rbf_pred = rbf_predict(rbf_model, latent) * xscale + xmean
        for label, pred in (("PCA", pca_pred), ("RBF", rbf_pred)):
            predictions[label][action["name"]] = pred.astype(np.float32)
            action_metric_rows[label].append({"action": action["name"], "split": action["split"],
                "metrics": action_metrics(action, pred, dataset, bones, xmean, xscale)})

    report = {"schema": "mg1.native-arm-pose-model-report.v1",
        "dataset": os.path.basename(args.dataset), "dataset_schema": dataset["schema"],
        "bone_names": bones, "feature_dimensions": len(bones) * 6,
        "input_representation": "independent production-basis rest-relative SE(3): translation plus sign-invariant quaternion logarithm per bone",
        "action_split": {"train_actions": sorted(a["name"] for a in train),
                         "holdout_actions": sorted(a["name"] for a in holdout),
                         "train_samples": int(sum(len(a["features"]) for a in train)),
                         "holdout_samples": int(sum(len(a["features"]) for a in holdout))},
        "selection": {"method": "action-grouped cross-validation on training actions only",
                      "fold_actions": [sorted(list(fold)) for fold in folds],
                      "pca_latent_dimensions": latent_dims, "pca_cv": cv_pca_report,
                      "rbf_centers": min(args.rbf_centers, len(xtrain)),
                      "rbf_width_factor": rbf_choice["width_factor"], "rbf_ridge": rbf_choice["ridge"],
                      "rbf_cv_candidates": cv_rbf_report},
        "models": {}}
    for label in ("PCA", "RBF"):
        rows = action_metric_rows[label]
        report["models"][label] = {"latent_dimensions": latent_dims,
            "train": aggregate_actions(rows, "train"), "holdout": aggregate_actions(rows, "holdout"),
            "per_action": rows}

    meta = {"schema": "mg1.native-arm-pose-models.v1", "bones": bones,
            "feature_dimensions": len(bones) * 6, "latent_dimensions": latent_dims,
            "feature_layout": "[translation_xyz, rotation_log_xyz] per bone in bones order",
            "rbf_width_factor": rbf_choice["width_factor"], "rbf_ridge": rbf_choice["ridge"],
            "rbf_center_count": len(rbf_model["centers"]), "dataset_schema": dataset["schema"]}
    os.makedirs(os.path.dirname(os.path.abspath(args.model_output)), exist_ok=True)
    np.savez_compressed(args.model_output, mean=pca["mean"], scale=pca["scale"],
        components=pca["components"], rbf_centers=rbf_model["centers"],
        rbf_sigma=np.asarray(rbf_model["sigma"]), rbf_weights=rbf_model["weights"],
        metadata_json=np.asarray(json.dumps(meta, separators=(",", ":"))))
    arrays = {}
    for label, by_action in predictions.items():
        for index, (name, value) in enumerate(sorted(by_action.items())):
            arrays[f"{label}__{index:02d}"] = value
            arrays[f"{label}__name__{index:02d}"] = np.asarray(name)
    np.savez_compressed(args.predictions_output, **arrays)
    os.makedirs(os.path.dirname(os.path.abspath(args.report_output)), exist_ok=True)
    with open(args.report_output, "w", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print("MG1_ARM_MODELS=" + json.dumps({"models": args.model_output,
        "predictions": args.predictions_output, "report": args.report_output,
        "latent_dimensions": latent_dims,
        "pca_train_mse": report["models"]["PCA"]["train"]["feature_rmse_standardized"] if "feature_rmse_standardized" in report["models"]["PCA"]["train"] else None,
        "pca_holdout_worst_bone": report["models"]["PCA"]["holdout"]["worst_bone_by_rotation_rmse"],
        "rbf_holdout_worst_bone": report["models"]["RBF"]["holdout"]["worst_bone_by_rotation_rmse"]}))


if __name__ == "__main__":
    main()
