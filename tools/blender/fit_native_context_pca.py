"""Fit and evaluate a PCA-only native arm context pose baseline.

Five whole actions remain validation-only. No nonlinear decoder is fit here.
Run with Blender's bundled Python or another NumPy-equipped Python.
"""
import argparse
import gzip
import json
import math
import os
import sys

import numpy as np


def args_parse():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else None
    p = argparse.ArgumentParser()
    p.add_argument("--dataset", required=True)
    p.add_argument("--predictions", required=True)
    p.add_argument("--report", required=True)
    p.add_argument("--folds", type=int, default=5)
    return p.parse_args(argv)


def encode_sample(sample, bones):
    features = []
    for bone in bones:
        tr = sample["bones"][bone]["rest_relative_se3"]
        q = np.asarray(tr["rotation_quaternion"], dtype=np.float64)
        q /= np.linalg.norm(q)
        if q[0] < 0:
            q = -q
        xyz = q[1:]
        sn = float(np.linalg.norm(xyz))
        angle = 2.0 * math.atan2(sn, float(q[0]))
        rotvec = xyz * angle / sn if sn > 1e-10 else 2.0 * xyz
        features.extend(tr["translation"])
        features.extend(rotvec.tolist())
    return np.asarray(features, dtype=np.float64)


def folds(actions, count):
    count = min(count, len(actions))
    bins, totals = [[] for _ in range(count)], [0] * count
    for action in sorted(actions, key=lambda a: (-len(a["x"]), a["name"])):
        i = min(range(count), key=lambda j: (totals[j], j))
        bins[i].append(action["name"])
        totals[i] += len(action["x"])
    return [set(row) for row in bins]


def fit(x, dims):
    mean = x.mean(axis=0)
    scale = x.std(axis=0)
    scale = np.maximum(scale, max(float(np.median(scale)) * 1e-4, 1e-8))
    z = (x - mean) / scale
    _u, _s, vt = np.linalg.svd(z, full_matrices=False)
    comp = vt[:dims]
    return mean, scale, comp


def predict(x, model):
    mean, scale, comp = model
    z = (x - mean) / scale
    return ((z @ comp.T) @ comp) * scale + mean


def rotvec_angle(a, b):
    # Quaternion angle from two rotation vectors, sign invariant by construction.
    def q(v):
        angle = float(np.linalg.norm(v))
        if angle < 1e-12:
            return np.asarray([1.0, 0.0, 0.0, 0.0])
        return np.r_[math.cos(angle / 2), v * (math.sin(angle / 2) / angle)]
    qa, qb = q(a), q(b)
    dot = min(1.0, abs(float(qa @ qb)))
    return 2.0 * math.acos(dot)


def action_metrics(action, pred, bones, training_scale):
    actual = action["x"]
    per_bone, rot_errors, trans_errors = {}, [], []
    for i, bone in enumerate(bones):
        sl = slice(i * 6, i * 6 + 6)
        trans = np.linalg.norm(pred[:, sl.start:sl.start+3] - actual[:, sl.start:sl.start+3], axis=1)
        angle = np.asarray([rotvec_angle(a, b) for a, b in zip(
            actual[:, sl.start+3:sl.stop], pred[:, sl.start+3:sl.stop])])
        per_bone[bone] = {"translation_rmse_armature": float(np.sqrt(np.mean(trans**2))),
            "translation_max_armature": float(np.max(trans)),
            "rotation_rmse_degrees": float(np.rad2deg(np.sqrt(np.mean(angle**2)))),
            "rotation_max_degrees": float(np.rad2deg(np.max(angle)))}
        trans_errors.extend(trans.tolist())
        rot_errors.extend(angle.tolist())
    return {"sample_count": len(actual), "feature_rmse_standardized": float(np.sqrt(np.mean(
            ((pred - actual) / np.maximum(training_scale, 1e-8))**2))),
        "per_bone": per_bone,
        "worst_rotation_bone": max(bones, key=lambda b: per_bone[b]["rotation_rmse_degrees"]),
        "worst_translation_bone": max(bones, key=lambda b: per_bone[b]["translation_rmse_armature"])}


def main():
    args = args_parse()
    with gzip.open(args.dataset, "rt", encoding="utf-8") as f:
        data = json.load(f)
    bones = list(data["bones"])
    train, holdout = [], []
    for row in data["actions"]:
        action = {"name": row["action"], "split": row["split"], "samples": row["samples"]}
        action["x"] = np.asarray([encode_sample(s, bones) for s in row["samples"]])
        (holdout if row["split"] == "holdout" else train).append(action)
    if len(train) != 23 or len(holdout) != 5:
        raise ValueError(f"expected 23 train/5 holdout actions, got {len(train)}/{len(holdout)}")
    action_folds = folds(train, args.folds)
    dims_candidates = [2, 4, 6, 8, 12, 16, 24, 32, 40, 48, 64, 80,
                       96, 128, 160, 192, 224]
    scores = {dim: [] for dim in dims_candidates}
    for val_names in action_folds:
        fit_rows = [a for a in train if a["name"] not in val_names]
        val_rows = [a for a in train if a["name"] in val_names]
        xfit = np.concatenate([a["x"] for a in fit_rows])
        xval = np.concatenate([a["x"] for a in val_rows])
        mean, scale, comp = fit(xfit, min(len(xfit)-1, xfit.shape[1]))
        zval = (xval - mean) / scale
        for dim in dims_candidates:
            k = min(dim, len(xfit)-1, xfit.shape[1])
            residual = zval - (zval @ comp[:k].T) @ comp[:k]
            scores[dim].append(float(np.mean(residual**2)))
    cv = {str(k): {"fold_mse": vals, "mean_mse": float(np.mean(vals)),
                   "se_mse": float(np.std(vals, ddof=1) / math.sqrt(len(vals)))}
          for k, vals in scores.items()}
    best = min(dims_candidates, key=lambda k: cv[str(k)]["mean_mse"])
    threshold = cv[str(best)]["mean_mse"] + max(cv[str(best)]["se_mse"], 1e-10)
    dims = min(k for k in dims_candidates if cv[str(k)]["mean_mse"] <= threshold)
    xtrain = np.concatenate([a["x"] for a in train])
    mean, scale, comp = fit(xtrain, min(dims, len(xtrain)-1, xtrain.shape[1]))
    dims = len(comp)
    os.makedirs(os.path.dirname(os.path.abspath(args.predictions)), exist_ok=True)
    pred_rows, arrays = {}, {}
    for action in train + holdout:
        pred = predict(action["x"], (mean, scale, comp))
        pred_rows[action["name"]] = action_metrics(action, pred, bones, scale)
        arrays[action["name"]] = pred.astype(np.float32)
    np.savez_compressed(args.predictions, **arrays)
    report = {"schema": "mg1.native-arm-context-pca-report.v1",
        "dataset": os.path.basename(args.dataset), "bones": bones,
        "bone_count": len(bones), "feature_dimensions": len(bones)*6,
        "split": {"training_actions": sorted(a["name"] for a in train),
                  "holdout_actions": sorted(a["name"] for a in holdout),
                  "training_samples": int(sum(len(a["x"]) for a in train)),
                  "holdout_samples": int(sum(len(a["x"]) for a in holdout))},
        "model": {"type": "PCA linear projection", "latent_dimensions": dims,
                  "dimension_selection": "five-fold action-grouped training-only CV, one-standard-error rule",
                  "cv_mse": cv, "fold_actions": [sorted(x) for x in action_folds]},
        "metrics": {"train": {"action_count": len(train),
            "sample_count": sum(len(a["x"]) for a in train),
            "feature_rmse_standardized": float(math.sqrt(sum(pred_rows[a["name"]]["feature_rmse_standardized"]**2 * len(a["x"]) for a in train) / sum(len(a["x"]) for a in train))),
            "per_action": {a["name"]: pred_rows[a["name"]] for a in train}},
            "holdout": {"action_count": len(holdout),
            "sample_count": sum(len(a["x"]) for a in holdout),
            "feature_rmse_standardized": float(math.sqrt(sum(pred_rows[a["name"]]["feature_rmse_standardized"]**2 * len(a["x"]) for a in holdout) / sum(len(a["x"]) for a in holdout))),
            "per_action": {a["name"]: pred_rows[a["name"]] for a in holdout}}}}
    os.makedirs(os.path.dirname(os.path.abspath(args.report)), exist_ok=True)
    with open(args.report, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, allow_nan=False)
        f.write("\n")
    print("MG1_CONTEXT_PCA=" + json.dumps({"latent_dimensions": dims,
        "feature_dimensions": len(bones)*6,
        "train_feature_rmse": report["metrics"]["train"]["feature_rmse_standardized"],
        "holdout_feature_rmse": report["metrics"]["holdout"]["feature_rmse_standardized"],
        "predictions": args.predictions, "report": args.report}))


if __name__ == "__main__":
    main()
