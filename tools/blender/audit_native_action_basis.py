"""Audit surviving Sluggers action provenance against the recovered Waluigi rig.

This is read-only: it queries the loopback Blender bridge and reads the local
source .blend provenance JSON and GLB. It writes one compact JSON report.
"""
import argparse
import json
import math
import struct
from pathlib import Path
import re

from client import request

ARM_BONES = ("bone_22", "bone_23", "bone_24", "bone_25", "bone_26", "bone_27", "bone_29", "bone_30")


def read_glb(path):
    data = Path(path).read_bytes()
    magic, version, length = struct.unpack_from("<4sII", data, 0)
    if magic != b"glTF" or version != 2 or length > len(data):
        raise ValueError("not a valid glTF 2 binary: " + str(path))
    offset = 12
    document = None
    while offset < length:
        chunk_length, chunk_type = struct.unpack_from("<I4s", data, offset)
        offset += 8
        chunk = data[offset:offset+chunk_length]
        offset += chunk_length
        if chunk_type == b"JSON":
            document = json.loads(chunk.decode("utf-8").rstrip(" \0"))
            break
    if document is None:
        raise ValueError("GLB has no JSON chunk")
    return {"version": version, "byte_length": length,
            "node_count": len(document.get("nodes", [])),
            "skin_count": len(document.get("skins", [])),
            "skins": [{"name": s.get("name"), "joint_count": len(s.get("joints", [])),
                       "skeleton_node": s.get("skeleton")} for s in document.get("skins", [])],
            "animation_count": len(document.get("animations", [])),
            "animation_names": [a.get("name") for a in document.get("animations", [])]}


def qnorm(q):
    return math.sqrt(sum(float(x) ** 2 for x in q))


def qangle_identity(q):
    norm = qnorm(q)
    if norm < 1e-12:
        return None
    return math.degrees(2 * math.acos(min(1.0, abs(float(q[0]) / norm))))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-blend-report", type=Path,
        default=Path("tools/blender/reports/provenance_Waluigi_Sluggers.json"))
    parser.add_argument("--source-glb", type=Path,
        default=Path(r"C:\MultiGalaxy1-Assets\Working\MSS\Waluigi\123475520_waluigi.gpl.glb"))
    parser.add_argument("--action", default="sequence_10.003")
    parser.add_argument("--output", type=Path,
        default=Path("tools/blender/reports/native_action_basis_audit.json"))
    args = parser.parse_args()

    source = json.loads(args.source_blend_report.read_text(encoding="utf-8"))
    original = next(a for a in source["armatures"] if a["object"] == "123475520_waluigi.gpl")
    live_rigs = {name: request("inspect.armature", {"object": name})["result"]
                 for name in ("123475520_waluigi.gpl", "Waluigi_Arm_Test", "Waluigi_Arm_Clean_Backup")}
    rest_comparisons = {}
    for name, rig in live_rigs.items():
        current = {bone["name"]: bone for bone in rig["bones"]}
        differences = {}
        for bone_name, expected in original["bones"].items():
            actual = current.get(bone_name)
            if actual is None:
                differences[bone_name] = None
                continue
            differences[bone_name] = max(abs(actual["matrix_local"][r][c] - expected["matrix_local"][r][c])
                                         for r in range(4) for c in range(4))
        rest_comparisons[name] = {
            "object_matrix_world": rig["matrix_world"],
            "source_blend_object_matrix_world": original["matrix_world"],
            "max_rest_matrix_abs_error": max(x for x in differences.values() if x is not None),
            "bones_over_1e_5": [n for n, x in differences.items() if x is not None and x > 1e-5],
            "missing_bones": [n for n, x in differences.items() if x is None],
        }

    provenance = request("inspect.action_provenance")["result"]
    action_row = next(a for a in request("inspect.actions")["result"] if a["name"] == args.action)
    slot_rows = next(a for a in provenance["actions"] if a["name"] == args.action)["slots"]
    selected_slot = next((s for s in slot_rows if s["identifier"].endswith(".004")),
                         slot_rows[0] if slot_rows else None)
    keyed_quats = {}
    for curve in action_row["fcurves"]:
        match = re.match(r'^pose\.bones\["([^\"]+)"\]\.rotation_quaternion$', curve["data_path"])
        if not match or match.group(1) not in ARM_BONES or not curve["keyframes"]:
            continue
        keyed_quats.setdefault((match.group(1), curve["keyframes"][0][0]), {})[curve["array_index"]] = curve["keyframes"][0][1]
    initial_channels = {}
    for (bone, frame), channels in keyed_quats.items():
        if len(channels) != 4:
            continue
        quat = [channels[i] for i in range(4)]
        initial_channels[bone] = {"time": frame,
            "norm": qnorm(quat), "angular_distance_from_identity_degrees": qangle_identity(quat)}

    sequence_provenance = []
    for row in provenance["actions"]:
        for slot in row["slots"]:
            if slot["identifier"] == "OBArmature.004":
                sequence_provenance.append({"action": row["name"], "frame_range": row["frame_range"],
                    "slot_identifier": slot["identifier"], "slot_target_id_type": slot["target_id_type"],
                    "animated_path_count": slot["data_path_count"], "animated_bone_count": len(slot["bone_names"]),
                    "arm_bones_present": sorted(set(ARM_BONES).intersection(slot["bone_names"])),
                    "assigned_armatures": row["assigned_armatures"], "metadata": row["metadata"],
                    "library": row["library"]})

    result = {
        "schema": "mg1.native-action-basis-audit.v1",
        "read_only": True,
        "action": {"name": args.action, "frame_range": action_row["frame_range"],
            "layers": action_row["layers"], "fcurve_count": len(action_row["fcurves"]),
            "slot": selected_slot, "arm_initial_key_quaternions": initial_channels},
        "provenance": {"sequence_action_count": provenance["sequence_action_count"],
            "missing_slot_owner_objects": sorted({"Armature.004"}),
            "slot_004_action_count": len(sequence_provenance),
            "slot_004_all_unassigned": all(not row["assigned_armatures"] for row in sequence_provenance),
            "slot_004_actions": sequence_provenance},
        "source_blend": {"filepath": source["filepath"], "sequence_actions": [a["name"]
            for a in source["sequence_actions"] if a["name"].startswith("sequence_")],
            "source_armature": {"object": original["object"], "data": original["data"],
                "bone_count": original["bone_count"], "location": original["location"],
                "rotation_euler": original["rotation_euler"], "scale": original["scale"],
                "matrix_world": original["matrix_world"],
                "arm_pose_basis_identity": all(original["bones"][n]["pose_matrix_basis"] ==
                    [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0],
                     [0.0, 0.0, 1.0, 0.0], [0.0, 0.0, 0.0, 1.0]] for n in ARM_BONES)}},
        "source_glb": read_glb(args.source_glb),
        "rest_comparisons_to_source_blend": rest_comparisons,
        "interpretation": {
            "source_model_rest_basis_recovered": True,
            "source_action_owner_rest_basis_recovered": False,
            "action_first_key_is_proven_rest_pose": False,
            "action_channel_values_prove_conversion": False,
            "reason": "The saved source rig and GLB establish Waluigi's bind/rest basis, but the animation GLB contains no clips and the OBArmature.004 action slots have no surviving owner object, library, or custom provenance metadata."}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(args.output), "action": args.action,
        "slot_004_actions": len(sequence_provenance), "source_glb_animations": result["source_glb"]["animation_count"],
        "rest_errors": {k: v["max_rest_matrix_abs_error"] for k, v in rest_comparisons.items()},
        "arm_initial_key_quaternions": initial_channels}, indent=2))


if __name__ == "__main__":
    main()
