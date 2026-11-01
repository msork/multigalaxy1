"""Read one named baked action from the live local Blender bridge as JSON."""
import argparse
import json
import os
import sys

from client import request


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--action", default="Waluigi_GalaxyWalk_Legs_Baked")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    response = request("inspect.actions", timeout=60)
    matches = [row for row in response["result"] if row["name"] == args.action]
    if len(matches) != 1:
        raise RuntimeError(f"expected exactly one live action named {args.action}; found {len(matches)}")
    action = matches[0]
    curves = [curve for curve in action["fcurves"] if curve["slot"] is not None]
    bone_curves = [curve for curve in curves if curve["data_path"].startswith('pose.bones[')]
    if not bone_curves:
        raise RuntimeError("selected action contains no pose-bone curves")
    times = [key[0] for curve in bone_curves for key in curve["keyframes"]]
    if not times:
        raise RuntimeError("selected action contains no keyed samples")
    payload = {"schema": "mg1.live-baked-action-curves.v1", "action": action["name"],
        "frame_range": action["frame_range"],
        "slot_identifiers": sorted({curve["slot"] for curve in bone_curves}),
        "curves": bone_curves, "key_time_min": min(times), "key_time_max": max(times),
        "key_time_count": len(set(times))}
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"output": args.output, "action": payload["action"],
        "frame_range": payload["frame_range"], "slot_count": len(payload["slot_identifiers"]),
        "curve_count": len(bone_curves), "key_time_count": payload["key_time_count"]}))


if __name__ == "__main__":
    main()
