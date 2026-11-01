"""Emit a review-sized summary from the full local pose-model report."""
import argparse
import json
import os
import sys


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    with open(args.input, "r", encoding="utf-8") as stream:
        full = json.load(stream)
    preview = full.get("preview", {})
    compact = {"schema": "mg1.native-arm-pose-model-summary.v1",
        "dataset": full["dataset"], "dataset_schema": full["dataset_schema"],
        "bone_names": full["bone_names"], "feature_dimensions": full["feature_dimensions"],
        "input_representation": full["input_representation"],
        "action_split": full["action_split"], "selection": full["selection"], "models": {}}
    for name, model in full["models"].items():
        compact["models"][name] = {
            "latent_dimensions": model["latent_dimensions"],
            "train": model["train"], "holdout": model["holdout"],
            "holdout_actions": []}
        for action in model["per_action"]:
            if action["split"] != "holdout":
                continue
            metrics = action["metrics"]
            worst_bone = max(metrics["per_bone"],
                key=lambda bone: metrics["per_bone"][bone]["rotation_rmse_degrees"])
            centroid_action = next((r for r in full.get("weighted_mesh_centroid_reconstruction", {})
                .get(name, {}).get("per_action", []) if r["action"] == action["action"]), None)
            centroid_worst = None
            if centroid_action:
                usable = [(bone, row["rmse"]) for bone, row in centroid_action["per_bone"].items()
                          if row["rmse"] is not None]
                if usable:
                    centroid_worst = {"bone": max(usable, key=lambda pair: pair[1])[0],
                                      "rmse_world": max(value for _, value in usable)}
            compact["models"][name]["holdout_actions"].append({
                "action": action["action"], "sample_count": metrics["sample_count"],
                "feature_rmse_standardized": metrics["feature_rmse_standardized"],
                "worst_rotation_bone": worst_bone,
                "worst_rotation_rmse_degrees": metrics["per_bone"][worst_bone]["rotation_rmse_degrees"],
                "worst_translation_bone": max(metrics["per_bone"],
                    key=lambda bone: metrics["per_bone"][bone]["translation_rmse_world"]),
                "landmark_rmse_world": {key: value["rmse"] for key, value in metrics["landmarks_world"].items()},
                "temporal": metrics["temporal"], "worst_weighted_centroid": centroid_worst})
    compact["weighted_mesh_centroid_reconstruction"] = {
        name: {split: value for split, value in models.items() if split in ("train", "holdout")}
        for name, models in full.get("weighted_mesh_centroid_reconstruction", {}).items()}
    compact["preview"] = {"blend": "tools/blender/reports/" + os.path.basename(preview.get("blend", "native_arm_pose_model_previews.blend")),
                           "models": preview.get("models"),
                           "visual_review_required": preview.get("visual_review_required", True)}
    compact["recommendation_gate"] = {
        "semantic_source_fitting": "blocked_pending_visual_review_and_context_bone_coverage",
        "reason": "PCA transform reconstruction is accurate, but weighted mesh shoulder/glove centroids diverge; the RBF model has high held-out pose/landmark error.",
        "no_semantic_retargeting_performed": True}
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as stream:
        json.dump(compact, stream, indent=2, allow_nan=False)
        stream.write("\n")


if __name__ == "__main__":
    main()
