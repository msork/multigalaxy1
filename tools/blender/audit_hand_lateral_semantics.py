"""Read-only comparison of signed lateral hand-position feature encodings."""
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

import fit_luigi_walk_semantic_waluigi as fit
from anatomical_semantic_frames import derive_anatomical_frames
from analyze_native_arm_context import source_pose_from_curves
from context_dataset import _rest_relative_matrix
from raw_anm_calibration import WALUIGI_LEFT_PROFILE, WALUIGI_RIGHT_PROFILE
from semantic_arm_fit_math import semantic_features

FEATURES = (
    "torso_frame_lateral_delta",
    "shoulder_local_lateral_delta",
    "absolute_normalized_shoulder_hand_lateral",
    "arm_plane_radial", "arm_plane_tangential", "arm_plane_depth",
    "reach_magnitude", "outward_direction_sign", "reach_tangential",
)
TOL = 0.08
SIGN_DEADBAND = 0.015


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dataset", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--source-rig", default="luigi.001_armature")
    p.add_argument("--source-action", default="luigi.001_armature_walk")
    p.add_argument("--source-slot", default="OBluigi.001_armature")
    p.add_argument("--target-rig", default="123475520_waluigi.gpl")
    return p.parse_args(argv)


def json_safe(value):
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    return value


def rest_pose_matrix(rest, raw_bone):
    exact = raw_bone.get("armature_pose_matrix")
    if exact is not None:
        return np.asarray(exact, dtype=np.float64)
    return (np.asarray(rest["rest_matrix"], dtype=np.float64) @
            _rest_relative_matrix(raw_bone["rest_relative_se3"]))


def point(rest, pose, bone, endpoint):
    rest_matrix = np.asarray(rest[bone]["rest_matrix"], dtype=np.float64)
    p = np.r_[np.asarray(rest[bone][endpoint], dtype=np.float64), 1.0]
    return (pose[bone] @ np.linalg.inv(rest_matrix) @ p)[:3]


def body_point(rest, pose, torso, bone, endpoint):
    return (np.linalg.inv(pose[torso]) @ np.r_[point(rest, pose, bone, endpoint), 1.0])[:3]


def rest_body_point(rest, torso, bone, endpoint):
    p = np.r_[np.asarray(rest[bone][endpoint], dtype=np.float64), 1.0]
    return (np.linalg.inv(np.asarray(rest[torso]["rest_matrix"], dtype=np.float64)) @ p)[:3]


def hand_measure(rest, pose, profile, anatomy, side):
    torso, shoulder_bone = profile["torso"], profile["shoulder_point_bone"]
    hand_bone = profile["hand"]
    shoulder = body_point(rest, pose, torso, shoulder_bone,
                          profile["shoulder_endpoint"])
    hand = body_point(rest, pose, torso, hand_bone, profile["hand_endpoint"])
    rest_shoulder = rest_body_point(rest, torso, shoulder_bone,
                                    profile["shoulder_endpoint"])
    rest_hand = rest_body_point(rest, torso, hand_bone, profile["hand_endpoint"])
    anatomical = np.asarray(anatomy["torso_to_anatomical"], dtype=np.float64)
    delta = anatomical @ ((hand - shoulder) - (rest_hand - rest_shoulder))
    absolute = anatomical @ (hand - shoulder)

    elbow = 0.5 * (
        rest_body_point(rest, torso, profile["elbow_a"], profile["elbow_endpoint_a"]) +
        rest_body_point(rest, torso, profile["elbow_b"], profile["elbow_endpoint_b"]))
    rest_wrist = rest_body_point(rest, torso, profile["wrist"],
                                 profile["wrist_endpoint"])
    limb_length = (np.linalg.norm(elbow - rest_shoulder) +
                   np.linalg.norm(rest_wrist - elbow))
    limb_length = max(float(limb_length), 1e-8)
    normalized = delta / limb_length
    lateral = np.asarray((1.0, 0.0, 0.0))
    body_lateral = float(np.dot(normalized, lateral))

    torso_rest_r = np.asarray(rest[torso]["rest_matrix"], dtype=np.float64)[:3, :3]
    shoulder_rest_r = np.asarray(rest[shoulder_bone]["rest_matrix"], dtype=np.float64)[:3, :3]
    torso_pose_r = np.asarray(pose[torso], dtype=np.float64)[:3, :3]
    shoulder_pose_r = np.asarray(pose[shoulder_bone], dtype=np.float64)[:3, :3]
    rest_shoulder_rel = torso_rest_r.T @ shoulder_rest_r
    current_shoulder_rel = torso_pose_r.T @ shoulder_pose_r
    shoulder_rest_anat = anatomical @ rest_shoulder_rel
    shoulder_current_anat = anatomical @ current_shoulder_rel
    rest_local_lateral = shoulder_rest_anat.T @ lateral
    current_local_lateral = shoulder_current_anat @ rest_local_lateral
    shoulder_local_lateral = float(np.dot(normalized, current_local_lateral))

    absolute_lateral = float(np.dot(anatomical @ (hand - shoulder), lateral) / limb_length)

    rest_reach = anatomical @ (rest_hand - rest_shoulder)
    radial = rest_reach / max(float(np.linalg.norm(rest_reach)), 1e-10)
    forward = np.asarray((0.0, 0.0, 1.0))
    normal = np.cross(radial, forward)
    if np.linalg.norm(normal) < 1e-8:
        normal = np.cross(radial, np.asarray((0.0, 1.0, 0.0)))
    normal /= max(float(np.linalg.norm(normal)), 1e-10)
    tangent = np.cross(normal, radial)
    tangent /= max(float(np.linalg.norm(tangent)), 1e-10)
    plane = np.asarray((np.dot(normalized, radial),
                        np.dot(normalized, tangent),
                        np.dot(normalized, normal)))
    outward = lateral * (1.0 if float(np.dot(rest_reach, lateral)) >= 0.0 else -1.0)
    outward_delta = float(np.dot(normalized, outward))
    reach_magnitude = float(np.linalg.norm(normalized))
    direction = 0 if abs(outward_delta) < SIGN_DEADBAND else int(np.sign(outward_delta))
    return {
        "current": body_lateral,
        "shoulder_local": shoulder_local_lateral,
        "absolute_lateral": absolute_lateral,
        "arm_plane": plane,
        "reach_magnitude": reach_magnitude,
        "outward_direction": direction,
        "reach_tangential": float(np.dot(normalized, tangent)),
        "limb_length": limb_length,
        "body_delta_vector": normalized,
    }


def endpoint_world(rest, pose, profile, object_matrix):
    p = point(rest, pose, profile["hand"], profile["hand_endpoint"])
    return (np.asarray(object_matrix, dtype=np.float64).reshape(4, 4) @ np.r_[p, 1.0])[:3]


def pearson(a, b):
    a, b = np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64)
    if len(a) < 3 or np.std(a) < 1e-10 or np.std(b) < 1e-10:
        return None
    return float(np.corrcoef(a, b)[0, 1])


def support(source, native, tolerance=TOL):
    source, native = np.asarray(source, dtype=np.float64), np.asarray(native, dtype=np.float64)
    errors = np.min(np.abs(source[:, None] - native[None, :]), axis=1)
    return {"unsupported_frame_count": int(np.count_nonzero(errors > tolerance)),
            "unsupported_frame_indices": np.flatnonzero(errors > tolerance).astype(int).tolist(),
            "nearest_abs_error_p95": float(np.quantile(errors, 0.95)),
            "nearest_abs_error_max": float(errors.max()),
            "tolerance": float(tolerance)}


def sign_support(source, native):
    s, n = np.asarray(source, dtype=np.int64), np.asarray(native, dtype=np.int64)
    bad = [i for i, value in enumerate(s) if value not in set(n.tolist())]
    return {"unsupported_frame_count": len(bad), "unsupported_frame_indices": bad,
            "sign_classes_source": sorted(set(s.tolist())),
            "sign_classes_native": sorted(set(n.tolist())),
            "tolerance": "exact discrete sign class; values within 0.015 are neutral"}


def summarize_candidate(source_rows, native_rows, source_world, source_length,
                        candidate, components, *, sign_pair=False):
    result = {"components": {}, "trajectory_correlation": {}}
    for component_index, name in enumerate(components):
        if candidate == "arm_plane":
            a = np.asarray([row[candidate][component_index] for row in source_rows])
            b = np.asarray([row[candidate][component_index] for row in native_rows])
        else:
            a = np.asarray([row[candidate] for row in source_rows], dtype=np.float64)
            b = np.asarray([row[candidate] for row in native_rows], dtype=np.float64)
        result["components"][name] = support(a, b)
        visible_radius = np.linalg.norm(source_world - source_world[0], axis=1) / max(source_length, 1e-8)
        visible_step = np.r_[0.0, np.linalg.norm(np.diff(source_world, axis=0), axis=1)] / max(source_length, 1e-8)
        result["trajectory_correlation"][name] = {
            "with_visible_hand_path_radius": pearson(a, visible_radius),
            "with_visible_hand_step_length": pearson(a, visible_step),
        }
    if sign_pair:
        result["components"]["outward_direction_sign"] = sign_support(
            [row["outward_direction"] for row in source_rows],
            [row["outward_direction"] for row in native_rows])
    return result


def main():
    args = parse_args()
    with gzip.open(args.dataset, "rt", encoding="utf-8") as stream:
        dataset = json.load(stream)
    source = bpy.data.objects.get(args.source_rig)
    target = bpy.data.objects.get(args.target_rig)
    action = bpy.data.actions.get(args.source_action)
    if not source or not target or not action:
        raise RuntimeError("source Luigi or target Waluigi rig/action missing")
    slots = [slot for slot in action.slots if slot.identifier == args.source_slot]
    if len(slots) != 1:
        raise RuntimeError("source walk action slot is not unique")

    source_rest = fit.source_rest_from_rig(source)
    source_anatomy = derive_anatomical_frames(
        source_rest, fit.numpy_matrix(fit.object_transform_matrix(source)),
        fit.SOURCE_PROFILE, fit.SOURCE_PROFILE_RIGHT)
    target_rest = dataset["rig"]["rest_bones"]
    for row in target_rest.values():
        row["head"] = row.get("rest_head", row.get("head"))
        row["tail"] = row.get("rest_tail", row.get("tail"))
    target_matrix = np.asarray(dataset["rig"]["production_basis_matrix_world"], dtype=np.float64)
    target_anatomy = derive_anatomical_frames(target_rest, target_matrix,
        WALUIGI_LEFT_PROFILE, WALUIGI_RIGHT_PROFILE)
    profiles = {"left": (fit.SOURCE_PROFILE, WALUIGI_LEFT_PROFILE),
                "right": (fit.SOURCE_PROFILE_RIGHT, WALUIGI_RIGHT_PROFILE)}

    source_rows = {"left": [], "right": []}
    source_world = {"left": [], "right": []}
    source_feature_check = {"left": [], "right": []}
    for frame in range(61):
        pose, _ = source_pose_from_curves(source, action, slots[0], float(frame))
        pose = {name: fit.numpy_matrix(value) for name, value in pose.items()}
        for side, (source_profile, _target_profile) in profiles.items():
            values = hand_measure(source_rest, pose, source_profile,
                                  source_anatomy, side)
            source_rows[side].append(values)
            frame_data = source_anatomy["sides"][side]
            reference = semantic_features(source_rest, pose, source_profile,
                source_anatomy["torso_to_anatomical"],
                frame_data["forearm_to_anatomical"],
                frame_data["forearm_axes_local"], elbow_absolute=True)[8:11]
            source_feature_check[side].append(float(np.max(np.abs(
                values["arm_plane"] - reference))))
            source_world[side].append(endpoint_world(
                source_rest, pose, source_profile, fit.numpy_matrix(
                    fit.object_transform_matrix(source))))
    source_rows = {side: list(rows) for side, rows in source_rows.items()}
    source_world = {side: np.asarray(rows) for side, rows in source_world.items()}

    native_rows = {"left": [], "right": []}
    native_counts = {"left": 0, "right": 0}
    for action_row in dataset["actions"]:
        if action_row["split"] != "train":
            continue
        for sample in action_row["samples"]:
            raw_bones = sample["bones"]
            for side, (_source_profile, target_profile) in profiles.items():
                needed = {target_profile[k] for k in (
                    "torso", "shoulder_point_bone", "elbow_a", "elbow_b", "hand")}
                pose = {name: rest_pose_matrix(target_rest[name], raw_bones[name])
                        for name in needed}
                anatomy = {"torso_to_anatomical": target_anatomy["torso_to_anatomical"]}
                native_rows[side].append(hand_measure(target_rest, pose,
                    target_profile, anatomy, side))
                if "anatomical_semantics" in sample:
                    native_rows[side][-1]["source_feature_check"] = float(
                        native_rows[side][-1]["current"] -
                        sample["anatomical_semantics"][side][8])
                native_counts[side] += 1

    candidates = {}
    for side in ("left", "right"):
        srows, nrows = source_rows[side], native_rows[side]
        hand_length = float(np.mean([row["limb_length"] for row in srows]))
        current = summarize_candidate(srows, nrows, source_world[side], hand_length,
            "current", ["current"])
        shoulder = summarize_candidate(srows, nrows, source_world[side], hand_length,
            "shoulder_local", ["shoulder_local"])
        absolute = summarize_candidate(srows, nrows, source_world[side], hand_length,
            "absolute_lateral", ["absolute_lateral"])
        plane = summarize_candidate(srows, nrows, source_world[side], hand_length,
            "arm_plane", ["radial", "tangential", "depth"])
        reach = summarize_candidate(srows, nrows, source_world[side], hand_length,
            "reach_magnitude", ["reach_magnitude"], sign_pair=True)
        reach["components"]["reach_tangential"] = support(
            [row["reach_tangential"] for row in srows],
            [row["reach_tangential"] for row in nrows])
        reach["trajectory_correlation"]["reach_tangential"] = {
            "with_visible_hand_path_radius": pearson(
                [row["reach_tangential"] for row in srows],
                np.linalg.norm(source_world[side] - source_world[side][0], axis=1) / hand_length),
            "with_visible_hand_step_length": pearson(
                [row["reach_tangential"] for row in srows],
                np.r_[0.0, np.linalg.norm(np.diff(source_world[side], axis=0), axis=1)] / hand_length),
        }
        candidates[side] = {
            "sample_counts": {"Luigi_frames": len(srows), "Waluigi_training_samples": native_counts[side]},
            "arm_length_normalization": {"Luigi_mean": hand_length,
                "Waluigi_mean": float(np.mean([row["limb_length"] for row in nrows]))},
            "current_torso_frame": current,
            "shoulder_local_lateral": shoulder,
            "normalized_shoulder_hand_lateral_absolute": absolute,
            "arm_plane_radial_tangential_depth": plane,
            "unsigned_reach_plus_outward_sign": reach,
            "feature_consistency": {
                "Luigi_max_abs_error_vs_shared_semantic_function": float(
                    np.max(np.abs(source_feature_check[side]))),
                "Waluigi_legacy_torso_lateral_max_abs_error_vs_extracted_semantics": float(np.max(
                    np.abs([row.get("source_feature_check", 0.0) for row in nrows]))),
                "note": "Waluigi comparison checks the previous stored torso-x value; the arm-plane output is recomputed from exact stored transforms.",
            },
        }

    summary = {}
    for side, rows in candidates.items():
        summary[side] = {}
        for name, candidate in rows.items():
            if "components" not in candidate:
                continue
            errors = [v["nearest_abs_error_p95"] for k, v in candidate["components"].items()
                      if "nearest_abs_error_p95" in v]
            unsupported = [v["unsupported_frame_count"] for v in candidate["components"].values()]
            summary[side][name] = {"component_unsupported_counts": unsupported,
                                   "worst_component_p95": max(errors, default=None)}
    result = {
        "schema": "mg1.hand-lateral-semantic-audit.v1",
        "read_only": True, "retarget_solver_or_preview_run": False,
        "coordinate_convention": "hand displacement is normalized by each rig's rest limb length and projected into a side-specific rest arm-plane basis: radial follows rest shoulder-to-hand reach, tangential follows projected character-forward, and depth completes the right-handed frame",
        "selected_representation": "arm_plane_radial_tangential_depth",
        "tolerances": {"continuous_feature": TOL,
                       "outward_sign_deadband": SIGN_DEADBAND},
        "candidates": candidates,
        "summary": summary,
        "support_gate": {"minimum_supported_fraction": 0.90,
            "passed": all(all(count <= 6 for count in summary[side][
                "arm_plane_radial_tangential_depth"]["component_unsupported_counts"])
                for side in ("left", "right")),
            "note": "gate applies to the selected signed arm-plane representation; rejected alternatives are reported for comparison"},
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as stream:
        json.dump(json_safe(result), stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"output": os.path.abspath(args.output),
        "summary": summary, "gate": result["support_gate"]}, indent=2))


if __name__ == "__main__":
    main()
