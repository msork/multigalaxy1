"""Derive transparent heuristic coverage labels from the native arm dataset."""
import argparse
import json
import math
from pathlib import Path

from rotation_math import angular_distance, relative


def summarize(report):
    candidates = []
    for action in report["actions"]:
        samples = action["samples"]
        if not samples:
            continue
        bone_q = {name: [sample["bones"][name]["rest_relative_se3"]["rotation_quaternion"]
                         for sample in samples] for name in report["bones"]}
        armature_q = {name: [sample["bones"][name]["armature_pose_rotation_quaternion"]
                             for sample in samples] for name in report["bones"]}
        displacement = {name: [math.degrees(angular_distance(values[0], q)) for q in values]
                        for name, values in bone_q.items()}
        maximum = {name: max(values, default=0.0) for name, values in displacement.items()}
        rel_23_25 = [relative(a, b) for a, b in zip(armature_q["bone_23"], armature_q["bone_25"])]
        rel_26_29 = [relative(a, b) for a, b in zip(armature_q["bone_26"], armature_q["bone_29"])]
        relative_change = {
            "23_25": max((math.degrees(angular_distance(rel_23_25[0], q)) for q in rel_23_25), default=0.0),
            "26_29": max((math.degrees(angular_distance(rel_26_29[0], q)) for q in rel_26_29), default=0.0),
        }
        shoulder = max(maximum["bone_22"], maximum["bone_23"], maximum["bone_25"])
        elbow = max(maximum["bone_24"], maximum["bone_26"], maximum["bone_29"])
        wrist = max(maximum["bone_27"], maximum["bone_30"])
        labels = []
        if shoulder >= 25:
            labels.append("shoulder_raise_or_swing_candidate")
        if elbow >= 25:
            labels.append("elbow_or_forearm_candidate")
        if max(relative_change.values()) >= 12:
            labels.append("overlapping_branch_roll_or_coupling_candidate")
        if wrist >= 20:
            labels.append("wrist_or_hand_candidate")
        active = sum(x >= 25 for x in (shoulder, elbow, wrist))
        if active >= 2:
            labels.append("combined_pose_candidate")
        times = action["sample_times"]
        duration = times[-1] - times[0] if len(times) > 1 else 0.0
        endpoint = max((math.degrees(angular_distance(bone_q[n][0], bone_q[n][-1]))
                        for n in ("bone_22", "bone_23", "bone_25", "bone_26", "bone_29", "bone_30")), default=0.0)
        if duration >= 8.0 and endpoint <= 15.0:
            labels.append("cyclic_pose_return_candidate")
        cluster = ("combined" if "combined_pose_candidate" in labels else
                   "shoulder_dominant" if shoulder >= max(elbow, wrist) and shoulder >= 25 else
                   "elbow_forearm_dominant" if elbow >= max(shoulder, wrist) and elbow >= 25 else
                   "wrist_hand_dominant" if wrist >= 20 else "low_or_mixed_motion")
        candidates.append({"action": action["action"], "split": action["split"],
            "provenance_confidence": action["provenance_confidence"],
            "training_eligible": action["training_eligible"], "cluster": cluster,
            "heuristic_labels": labels, "label_confidence": "candidate_only; sequence names are not semantic labels",
            "exact_keyed_time_count": len(action.get("exact_key_times", times)),
            "sample_time_count": len(times), "duration_frames": duration,
            "maximum_displacement_degrees": maximum,
            "branch_relative_change_degrees": relative_change,
            "semantic_motion_scores_degrees": {"shoulder": shoulder, "elbow_forearm": elbow, "wrist_hand": wrist},
            "endpoint_return_degrees": endpoint})
    return {"schema": "mg1.native-arm-candidate-labels.v1", "method": "threshold labels and displacement clusters; no raw quaternion-component correlation",
            "candidates": candidates,
            "coverage": {label: sum(label in row["heuristic_labels"] for row in candidates) for label in (
                "shoulder_raise_or_swing_candidate", "elbow_or_forearm_candidate",
                "overlapping_branch_roll_or_coupling_candidate", "wrist_or_hand_candidate",
                "combined_pose_candidate", "cyclic_pose_return_candidate")}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = summarize(json.loads(args.dataset.read_text(encoding="utf-8")))
    output = args.output or args.dataset.with_name("native_waluigi_arm_candidates.json")
    output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "candidate_actions": len(report["candidates"]), "coverage": report["coverage"]}, indent=2))


if __name__ == "__main__":
    main()
