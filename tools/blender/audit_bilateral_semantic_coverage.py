"""Read-only Luigi L/R coverage against bilateral raw-ANM Waluigi samples."""
from __future__ import annotations

import argparse
import copy
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
from anatomical_semantic_frames import derive_anatomical_frames
from analyze_native_arm_context import source_pose_from_curves
from context_dataset import normalize_context_dataset
from raw_anm_calibration import (WALUIGI_LEFT_PROFILE, WALUIGI_RIGHT_PROFILE,
                                 WALUIGI_RIGHT_ARM_BONES)
from semantic_arm_fit_math import semantic_features

FEATURES = (
    "shoulder_x", "shoulder_y", "shoulder_z", "elbow_flexion",
    "forearm_roll", "wrist_x", "wrist_y", "wrist_z",
    "hand_delta_x", "hand_delta_y", "hand_delta_z",
)
TOLERANCE = np.asarray((0.15, 0.15, 0.15, 0.08, 0.15,
                        0.15, 0.15, 0.15, 0.08, 0.08, 0.08))


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dataset", required=True)
    p.add_argument("--previous-dataset", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--source-rig", default="luigi.001_armature")
    p.add_argument("--source-action", default="luigi.001_armature_walk")
    p.add_argument("--source-slot", default="OBluigi.001_armature")
    p.add_argument("--target-rig", default="123475520_waluigi.gpl")
    return p.parse_args(argv)


def read_dataset(path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return normalize_context_dataset(json.load(stream))


def dataset_semantics(dataset, target_rig, profile, side):
    rest = fit.source_rest_from_rig(target_rig)
    anatomy = derive_anatomical_frames(
        rest, fit.numpy_matrix(fit.object_transform_matrix(target_rig)),
        fit.TARGET_PROFILE, fit.TARGET_PROFILE_RIGHT)
    profile = fit.TARGET_PROFILE if side == "left" else fit.TARGET_PROFILE_RIGHT
    rows, action_names = [], []
    for action in dataset["actions"]:
        if action["split"] != "train":
            continue
        for sample in action["samples"]:
            saved_semantics = sample.get("anatomical_semantics", {}).get(side)
            if saved_semantics is not None:
                rows.append(np.asarray(saved_semantics, dtype=np.float64))
                action_names.append(action["action"])
                continue
            pose = {name: np.asarray(sample["bones"][name]["armature_pose"], dtype=np.float64)
                    for name in dataset["bones"]}
            side_frame = anatomy["sides"][side]
            rows.append(semantic_features(rest, pose, profile,
                anatomy["torso_to_anatomical"],
                side_frame["forearm_to_anatomical"],
                side_frame["forearm_axes_local"], elbow_absolute=True))
            action_names.append(action["action"])
    return np.asarray(rows), action_names


def feature_report(source, native):
    source = np.asarray(source, dtype=np.float64)
    native = np.asarray(native, dtype=np.float64)
    dimensions = {}
    for index, name in enumerate(FEATURES):
        errors = np.min(np.abs(source[:, index, None] - native[None, :, index]), axis=1)
        source_values, native_values = source[:, index], native[:, index]
        intersection = max(0.0, min(source_values.max(), native_values.max()) -
                           max(source_values.min(), native_values.min()))
        tolerance = float(TOLERANCE[index])
        unsupported = np.flatnonzero(errors > tolerance).astype(int).tolist()
        dimensions[name] = {
            "source_range": [float(source_values.min()), float(source_values.max())],
            "native_range": [float(native_values.min()), float(native_values.max())],
            "source_range_overlap_fraction": float(intersection /
                max(float(np.ptp(source_values)), 1e-12)),
            "nearest_abs_error_max": float(errors.max()),
            "nearest_abs_error_p95": float(np.quantile(errors, 0.95)),
            "tolerance": tolerance,
            "unsupported_frame_count": len(unsupported),
            "unsupported_frame_indices": unsupported,
        }
    scale = np.maximum(np.std(np.vstack((source, native)), axis=0), 1e-4)
    joint = np.min(np.linalg.norm(
        (source[:, None, :] - native[None, :, :]) / scale, axis=2), axis=1)
    unsupported_by_frame = sorted(set(i for row in dimensions.values()
                                      for i in row["unsupported_frame_indices"]))
    return {
        "source_frame_count": int(len(source)),
        "native_training_sample_count": int(len(native)),
        "features": dimensions,
        "unsupported_dimension_frequency": {
            name: row["unsupported_frame_count"] for name, row in dimensions.items()},
        "frames_with_any_unsupported_feature": unsupported_by_frame,
        "joint_nearest_standardized_distance": {
            "median": float(np.median(joint)), "p95": float(np.quantile(joint, 0.95)),
            "max": float(joint.max()),
        },
    }


def main():
    args = parse_args()
    current_raw = json.load(gzip.open(args.dataset, "rt", encoding="utf-8"))
    current_source_schema = current_raw.get("schema")
    current_rig_metadata = copy.deepcopy(current_raw["rig"])
    current_provenance = copy.deepcopy(current_raw["provenance"])
    current = current_raw
    training_action_count = sum(a["split"] == "train" for a in current["actions"])
    holdout_action_count = sum(a["split"] == "holdout" for a in current["actions"])
    profile_bone_count = len(current["rig"]["profile_bones"])
    holdout_sample_count = sum(len(a["samples"]) for a in current["actions"]
                               if a["split"] == "holdout")
    source = bpy.data.objects.get(args.source_rig)
    target = bpy.data.objects.get(args.target_rig)
    action = bpy.data.actions.get(args.source_action)
    if not source or not target or not action:
        raise RuntimeError("source Luigi rig/action or Waluigi rig is missing")
    slots = [slot for slot in action.slots if slot.identifier == args.source_slot]
    if len(slots) != 1:
        raise RuntimeError("source action slot is not unique")
    source_rest = fit.source_rest_from_rig(source)
    source_anatomy = derive_anatomical_frames(
        source_rest, fit.numpy_matrix(fit.object_transform_matrix(source)),
        fit.SOURCE_PROFILE, fit.SOURCE_PROFILE_RIGHT)
    source_rows = {"left": [], "right": []}
    for frame in range(61):
        pose, _ = source_pose_from_curves(source, action, slots[0], float(frame))
        pose = {name: fit.numpy_matrix(value) for name, value in pose.items()}
        for side, profile in (("left", fit.SOURCE_PROFILE),
                              ("right", fit.SOURCE_PROFILE_RIGHT)):
            frame_data = source_anatomy["sides"][side]
            source_rows[side].append(semantic_features(
                source_rest, pose, profile, source_anatomy["torso_to_anatomical"],
                frame_data["forearm_to_anatomical"], frame_data["forearm_axes_local"],
                elbow_absolute=True))
    source_rows = {side: np.asarray(rows) for side, rows in source_rows.items()}

    new_left, _ = dataset_semantics(current, target, fit.TARGET_PROFILE, "left")
    new_right, _ = dataset_semantics(current, target, fit.TARGET_PROFILE_RIGHT, "right")
    del current
    import gc
    gc.collect()
    previous = read_dataset(args.previous_dataset)
    old_left, _ = dataset_semantics(previous, target, fit.TARGET_PROFILE, "left")
    del previous
    gc.collect()
    report = {
        "schema": "mg1.bilateral-semantic-coverage.v1",
        "read_only": True,
        "retarget_solver_or_preview_run": False,
        "dataset": {"schema": current_source_schema,
            "training_action_count": training_action_count,
            "holdout_action_count": holdout_action_count,
            "training_samples_per_side": {"left": len(new_left), "right": len(new_right)},
            "holdout_samples_per_side": {"left": holdout_sample_count,
                                          "right": holdout_sample_count},
            "profile_bone_count": profile_bone_count,
            "right_arm_bones": list(WALUIGI_RIGHT_ARM_BONES),
            "right_arm_track_map": {"22": "bone_57", "117": "bone_58",
                "118": "bone_59", "23": "bone_60", "113": "bone_61",
                "114": "bone_62", "24": "bone_64", "25": "bone_65"},
            "right_hand_track_map": {track: bone for track, bone in
                current_rig_metadata["track_id_to_bone"].items()
                if bone.startswith("bone_") and
                   66 <= int(bone.split("_")[1]) <= 86},
            "right_extraction": current_provenance["side_extraction_policy"]},
        "semantic_convention": {
            "frames": "independently rest-calibrated left and right anatomical frames",
            "hand_delta_x": "positive is character-right in one shared torso frame; left outward is negative and right outward is positive; no per-side sign flip",
            "feature_tolerances": dict(zip(FEATURES, TOLERANCE.tolist())),
        },
        "coverage_before_real_right_extraction": {
            "Luigi_left_vs_old_Waluigi_left": feature_report(source_rows["left"], old_left),
            "Luigi_right_vs_old_Waluigi_left_proxy": feature_report(source_rows["right"], old_left),
        },
        "coverage_after_bilateral_extraction": {
            "Luigi_left_vs_Waluigi_left": feature_report(source_rows["left"], new_left),
            "Luigi_right_vs_Waluigi_right": feature_report(source_rows["right"], new_right),
        },
        "gate": {"all_dimensions_supported": False},
    }
    # Both arms are usable only if each semantic dimension stays inside its
    # declared tolerance for at least 90% of the source walk samples.
    sides = report["coverage_after_bilateral_extraction"]
    report["gate"]["all_dimensions_supported"] = all(
        row["unsupported_frame_count"] <= 6
        for side in sides.values() for row in side["features"].values())
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"output": os.path.abspath(args.output),
        "sample_counts": report["dataset"]["training_samples_per_side"],
        "remaining_unsupported": {side: row["unsupported_dimension_frequency"]
            for side, row in sides.items()}, "gate": report["gate"]}, indent=2))


if __name__ == "__main__":
    main()
