"""Load raw native calibration datasets for context-model consumers.

This module deliberately rejects inferred legacy action bundles. Training must
use data extracted from source Sluggies banks, with action-level splits already
assigned by the extractor.
"""
import math

import numpy as np


RAW_SCHEMA = "mg1.raw-sluggies-native-calibration.v1"
RAW_SCHEMAS = {RAW_SCHEMA, "mg1.raw-sluggies-native-calibration.v2"}


def _quat_matrix_wxyz(quaternion):
    q = np.asarray(quaternion, dtype=np.float64)
    norm = float(np.linalg.norm(q))
    if not math.isfinite(norm) or norm < 1e-12:
        raise ValueError("invalid quaternion in raw native calibration")
    w, x, y, z = q / norm
    return np.asarray([
        [1 - 2*(y*y + z*z), 2*(x*y - z*w), 2*(x*z + y*w), 0.0],
        [2*(x*y + z*w), 1 - 2*(x*x + z*z), 2*(y*z - x*w), 0.0],
        [2*(x*z - y*w), 2*(y*z + x*w), 1 - 2*(x*x + y*y), 0.0],
        [0.0, 0.0, 0.0, 1.0]], dtype=np.float64)


def _rest_relative_matrix(transform):
    result = _quat_matrix_wxyz(transform["rotation_quaternion_wxyz"])
    scale = np.asarray(transform.get("scale", (1.0, 1.0, 1.0)), dtype=np.float64)
    if scale.shape != (3,) or not np.all(np.isfinite(scale)):
        raise ValueError("invalid scale in raw native calibration")
    result[:3, :3] = result[:3, :3] @ np.diag(scale)
    result[:3, 3] = np.asarray(transform["translation"], dtype=np.float64)
    return result


def normalize_context_dataset(data):
    """Normalize the raw extractor schema without changing its source or split."""
    source_schema = data.get("schema")
    if source_schema not in RAW_SCHEMAS or not data.get("provenance", {}).get("authority", "").startswith("raw native .anm"):
        raise ValueError("context-model training requires an authoritative raw Sluggies .anm dataset")
    bones = list(data["rig"]["profile_bones"])
    rest_bones = data["rig"]["rest_bones"]
    for row in rest_bones.values():
        row["head"] = row.get("rest_head", row.get("head"))
        row["tail"] = row.get("rest_tail", row.get("tail"))
    for source_action in data["actions"]:
        for source_sample in source_action["samples"]:
            sample_bones = {}
            for name in bones:
                raw = source_sample["bones"][name]
                relative = raw["rest_relative_se3"]
                transform = {
                    "translation": list(relative["translation"]),
                    "rotation_quaternion": list(relative["rotation_quaternion_wxyz"]),
                    "scale": list(relative["scale"]),
                }
                rest = np.asarray(rest_bones[name]["rest_matrix"], dtype=np.float64)
                pose = rest @ _rest_relative_matrix(relative)
                exact_arm_pose = raw.get("armature_pose_matrix")
                if exact_arm_pose is not None:
                    pose = np.asarray(exact_arm_pose, dtype=np.float64)
                rest_inverse = np.linalg.inv(rest)
                head = (pose @ rest_inverse @ np.r_[rest_bones[name]["head"], 1.0])[:3]
                tail = (pose @ rest_inverse @ np.r_[rest_bones[name]["tail"], 1.0])[:3]
                sample_bones[name] = {
                    "rest_relative_se3": transform,
                    "armature_pose": pose.tolist(),
                    "local_basis_matrix": raw["local_basis_matrix"],
                    "head_armature": head.tolist(), "tail_armature": tail.tolist(),
                }
            source_sample["bones"] = sample_bones
            source_sample["time"] = float(source_sample["time"])
            source_sample["normalized_time"] = float(source_sample["normalized_time"])
        source_action["bank_index"] = int(source_action["bank_index"])
        source_action["sequence_index"] = int(source_action["sequence_index"])
        source_action["frame_range"] = list(source_action["frame_range"])
    actions = data["actions"]
    training_names = [row["action"] for row in actions if row["split"] == "train"]
    holdout_names = [row["action"] for row in actions if row["split"] == "holdout"]
    if not training_names or not holdout_names:
        raise ValueError("raw calibration must contain train and action-level holdout actions")
    if len(training_names) + len(holdout_names) != len(actions):
        raise ValueError("raw calibration contains an unknown split label")
    data.clear()
    data.update({
        "schema": "mg1.context-model-input.v1", "source_schema": source_schema,
        "source_authority": "raw native .anm bank imported onto matching .sluggie skeleton by TrackId",
        "bones": bones, "rest_bones": rest_bones, "actions": actions,
        "training_actions": training_names, "holdout_actions": holdout_names,
        "training_action_count": len(training_names), "holdout_action_count": len(holdout_names),
        "sample_time_policy": "exact imported context keys plus action endpoints",
    })
    return data
