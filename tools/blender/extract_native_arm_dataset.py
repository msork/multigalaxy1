"""Collect exact-key native arm samples through the local Blender bridge."""
import argparse
import json
from pathlib import Path

from client import request

DEFAULT_MESHES = ["ARMTEST_29_0x75c1640_submesh0", "ARMTEST_29_0x75c1640_submesh1"]
DEFAULT_HOLDOUTS = ["sequence_11.003", "sequence_15.003", "sequence_20.001", "sequence_24", "sequence_26"]
WALUIGI_SLOT = "OBArmature.004"
ANCHOR_ACTION = "sequence_10.003"


def extract(object_name, meshes, holdouts):
    provenance = request("inspect.action_provenance", timeout=60)["result"]
    target_rig = next((a for a in provenance["armatures"] if a["object"] == object_name), None)
    production_rig = next((a for a in provenance["armatures"] if a["object"] == "123475520_waluigi.gpl"), None)
    if not target_rig or not production_rig:
        raise RuntimeError("both disposable target and production Waluigi armature must be present for basis verification")
    if target_rig["rest_signature_sha256"] != production_rig["rest_signature_sha256"]:
        raise RuntimeError("disposable target rest basis does not exactly match production Waluigi; refusing extraction")
    actions = [a for a in provenance["actions"]
               if any(s["name"] == WALUIGI_SLOT for s in a["slots"])]
    if not any(a["name"] == ANCHOR_ACTION for a in actions):
        raise RuntimeError("known native Waluigi anchor action/slot is missing")
    missing_holdouts = sorted(set(holdouts) - {a["name"] for a in actions})
    if missing_holdouts:
        raise RuntimeError("requested whole-action holdouts are not in the Waluigi slot family: " + ", ".join(missing_holdouts))
    rows = []
    rest_weights = {}
    for action in actions:
        result = request("extract.native_arm_dataset", {
            "object": object_name, "actions": [action["name"]],
            "include_slots": [WALUIGI_SLOT], "meshes": meshes,
            "holdout_actions": holdouts,
        }, timeout=60)["result"]
        if result["dataset"]:
            rows.extend(result["dataset"])
            if not rest_weights:
                rest_weights = result["weighted_mesh_vertices_rest"]
    # The known Waluigi anchor identifies the .004 slot family. The original source object
    # name is not retained, so family membership is high-confidence inferred provenance.
    for row in rows:
        row["provenance_confidence"] = "tested_anchor" if row["action"] == ANCHOR_ACTION else "high_family_inference"
        row["training_eligible"] = row["split"] == "train"
    slot_families = {}
    for action in provenance["actions"]:
        for slot in action["slots"]:
            family = slot_families.setdefault(slot["name"], {"action_count": 0, "bone_count_histogram": {},
                "action_names": [], "animated_bone_names": sorted(slot["bone_names"])})
            family["action_count"] += 1
            family["action_names"].append(action["name"])
            family["animated_bone_names"] = sorted(set(family["animated_bone_names"]) | set(slot["bone_names"]))
            count_key = str(len(slot["bone_names"]))
            family["bone_count_histogram"][count_key] = family["bone_count_histogram"].get(count_key, 0) + 1
    return {
        "schema": "mg1.native-arm-calibration-bundle.v1",
        "read_only": True,
        "rig_object": object_name,
        "rig_data": next((a["data"] for a in provenance["armatures"] if a["object"] == object_name), None),
        "slot_family": WALUIGI_SLOT,
        "provenance": {
            "anchor_action": ANCHOR_ACTION,
            "anchor_basis": "previously analyzed native Sluggers action evaluated on the disposable Waluigi rest-basis copy; same slot family; source owner is not recorded",
            "family_confidence": "high but inferred; original source owner object is not preserved in the live scene",
            "basis_match_production": True,
            "rig_rest_signature_sha256": target_rig["rest_signature_sha256"],
            "production_object_scale": production_rig["scale"],
            "other_slot_families_excluded": sorted({s["name"] for a in provenance["actions"] for s in a["slots"]} - {WALUIGI_SLOT}),
            "slot_families": slot_families,
            "inventory": provenance,
        },
        "bones": ["bone_22", "bone_23", "bone_24", "bone_25", "bone_26", "bone_27", "bone_29", "bone_30"],
        "weighted_mesh_vertices_rest": rest_weights,
        "sample_time_policy": "exact FCurve keys plus action range endpoints",
        "pose_reset_policy": "all temporary pose bases reset to identity before each action",
        "holdout_actions": sorted(holdouts),
        "actions": rows,
        "training_eligible_count": sum(row["training_eligible"] for row in rows),
        "high_confidence_candidate_count": sum(row["provenance_confidence"] == "high_family_inference" for row in rows),
        "holdout_count": sum(row["split"] == "holdout" for row in rows),
        "sample_count": sum(len(row["samples"]) for row in rows),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--object", default="Waluigi_Arm_Test")
    parser.add_argument("--mesh", action="append", dest="meshes")
    parser.add_argument("--holdout", action="append", dest="holdouts")
    parser.add_argument("--output", type=Path, default=Path("tools/blender/reports/native_waluigi_arm_calibration.json"))
    args = parser.parse_args()
    report = extract(args.object, args.meshes or DEFAULT_MESHES, args.holdouts or DEFAULT_HOLDOUTS)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "actions": len(report["actions"]),
                      "training_eligible": report["training_eligible_count"],
                      "family_candidates": report["high_confidence_candidate_count"],
                      "holdouts": report["holdout_count"], "samples": report["sample_count"]}, indent=2))


if __name__ == "__main__":
    main()
