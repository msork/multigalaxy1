"""Build three skinned native-action previews and compare them to the stored dataset."""
import argparse
import json
from pathlib import Path

from client import request

DEFAULT_ACTIONS = ["sequence_10.003", "sequence_15.003", "sequence_24"]
MESHES = ["ARMTEST_29_0x75c1640_submesh0", "ARMTEST_29_0x75c1640_submesh1"]


def choose_samples(action, count=5):
    samples = action["samples"]
    if len(samples) <= count:
        return samples
    indexes = sorted({round(i * (len(samples)-1) / (count-1)) for i in range(count)})
    return [samples[i] for i in indexes]


def build(dataset, object_name, action_names, spacing, render_dir):
    rows = {row["action"]: row for row in dataset["actions"]}
    requests = []
    for name in action_names:
        row = rows.get(name)
        if row is None:
            raise ValueError("action is absent from the extracted dataset: " + name)
        if row["slot_identifier"] != dataset["slot_family"]:
            raise ValueError("action is not from the dataset's selected native slot family: " + name)
        if row["provenance_confidence"] not in ("tested_anchor", "high_family_inference"):
            raise ValueError("action does not have adequate candidate provenance: " + name)
        requests.append({"name": name, "slot_identifier": row["slot_identifier"],
                         "samples": choose_samples(row)})
    response = request("mutate.native_arm_previews", {
        "object": object_name, "production_object": "123475520_waluigi.gpl",
        "meshes": MESHES, "actions": requests, "spacing_world": spacing,
    }, confirm=True, timeout=60)
    result = response["result"]
    sample_times = {row["action"]: [sample["time"] for sample in row["samples"]]
                    for row in result["previews"]}
    capture = request("mutate.capture_native_arm_previews", {
        "preview_group": result["preview_group"], "samples": sample_times,
        "output_dir": str(render_dir.resolve()), "resolution": 800,
    }, confirm=True, timeout=60)
    result["visual_captures"] = capture["result"]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=Path("tools/blender/reports/native_waluigi_arm_calibration.json"))
    parser.add_argument("--object", default="Waluigi_Arm_Test")
    parser.add_argument("--action", action="append", dest="actions")
    parser.add_argument("--spacing", type=float, default=350.0, help="world-space separation between preview copies")
    parser.add_argument("--render-dir", type=Path, default=Path("tools/blender/reports/native_arm_preview_renders"))
    parser.add_argument("--output", type=Path, default=Path("tools/blender/reports/native_waluigi_preview_validation.json"))
    args = parser.parse_args()
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    result = build(dataset, args.object, args.actions or DEFAULT_ACTIONS, args.spacing, args.render_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(args.output), "preview_group": result["preview_group"],
        "previews": [{"action": row["action"], "armature": row["preview_armature"], "meshes": row["preview_meshes"]}
                     for row in result["previews"]],
        "validation": result["validation"], "visual_captures": result["visual_captures"],
        "new_actions_created": result["new_actions_created"]}, indent=2))


if __name__ == "__main__":
    main()
