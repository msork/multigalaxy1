"""Read-only multi-action study of Waluigi's native left-arm branches."""
import argparse
import json
import math
from pathlib import Path

from client import request

DEFAULT_ACTIONS = ["sequence_10.003", "sequence_12.001", "sequence_15.001", "sequence_4.001", "sequence_8.001"]
MESHES = ["ARMTEST_29_0x75c1640_submesh0", "ARMTEST_29_0x75c1640_submesh1"]


def _head(matrix):
    return [matrix[i][3] for i in range(3)]


def _tail(matrix, length):
    return [matrix[i][3] + matrix[i][1] * length for i in range(3)]


def _distance(a, b):
    return math.sqrt(sum((x-y)**2 for x,y in zip(a,b)))


def study(object_name, actions, sample_count=41):
    rows = []
    for action in actions:
        data = request("analyze.waluigi_arm", {
            "object": object_name, "action": action, "side": "left",
            "sample_count": sample_count, "meshes": MESHES,
        })["result"]
        bones = data["bone_analysis"]
        pairs = {(p["bone_a"], p["bone_b"]): p for p in data["pairwise_motion"]}
        pair_23_25 = pairs.get(("bone_23", "bone_25"), pairs.get(("bone_25", "bone_23")))
        pair_26_29 = pairs.get(("bone_26", "bone_29"), pairs.get(("bone_29", "bone_26")))
        elbow_separation, overlap_end_distance = [], []
        scale = max(abs(x) for x in data["object_scale"])
        for sample in data["samples"]:
            matrices = {n: sample["bones"][n]["matrix_armature"] for n in bones}
            main_elbow = _head(matrices["bone_26"])
            overlap_elbow = _head(matrices["bone_29"])
            glove_head = _head(matrices["bone_30"])
            overlap_tail = _tail(matrices["bone_29"], bones["bone_29"]["rest_length"])
            elbow_separation.append(_distance(main_elbow, overlap_elbow) * scale)
            overlap_end_distance.append(_distance(glove_head, overlap_tail) * scale)
        rows.append({
            "action": action,
            "action_slot": data.get("action_slot"),
            "frame_range": data.get("action_frame_range"),
            "sample_count": len(data["sample_frames"]),
            "track_ids": {n: bones[n].get("track_id") for n in bones},
            "weighted_vertex_counts": {n: data["weights"][n]["weighted_vertex_count"] for n in bones},
            "max_local_angular_displacement_degrees": {
                n: bones[n]["maximum_angular_displacement_degrees"]["local"] for n in bones},
            "pair_23_25": {
                "local_displacement_correlation": pair_23_25["local_angular_displacement_correlation"],
                "max_relative_orientation_change_degrees": pair_23_25["maximum_relative_orientation_change_degrees"]},
            "pair_26_29": {
                "local_displacement_correlation": pair_26_29["local_angular_displacement_correlation"],
                "max_relative_orientation_change_degrees": pair_26_29["maximum_relative_orientation_change_degrees"]},
            "max_main_overlap_elbow_head_separation_world": max(elbow_separation),
            "max_glove_to_overlap_tail_distance_world": max(overlap_end_distance),
        })
    return {"schema": "mg1.native-waluigi-arm-study.v1", "object": object_name,
            "read_only": True, "sample_count_per_action": sample_count,
            "actions": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--object", default="Waluigi_Arm_Test")
    parser.add_argument("--action", action="append", dest="actions", help="action to inspect; repeatable")
    parser.add_argument("--sample-count", type=int, default=41)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = study(args.object, args.actions or DEFAULT_ACTIONS, args.sample_count)
    rendered = json.dumps(report, indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        print(json.dumps({"output": str(args.output), "action_count": len(report["actions"]), "read_only": True}))
    else:
        print(rendered)


if __name__ == "__main__":
    main()
