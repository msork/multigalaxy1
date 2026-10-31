"""Command-line client for bridge.py; uses only the Python standard library."""
import argparse
import json
import socket
import uuid


def request(operation, args=None, confirm=False, host="127.0.0.1", port=47831, timeout=35):
    if host != "127.0.0.1":
        raise ValueError("client permits loopback only")
    payload = {"version": 1, "id": str(uuid.uuid4()), "operation": operation,
               "args": args or {}, "confirm": bool(confirm)}
    with socket.create_connection((host, port), timeout=timeout) as conn:
        conn.sendall((json.dumps(payload) + "\n").encode("utf-8"))
        chunks = []
        while True:
            chunk = conn.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
            if b"\n" in chunk:
                break
    response = json.loads(b"".join(chunks).split(b"\n", 1)[0].decode("utf-8"))
    if not response.get("ok"):
        raise RuntimeError(response.get("error", "bridge request failed"))
    return response


def summarize_arm_analysis(result):
    bones = result["bone_analysis"]
    pairs = {(p["bone_a"], p["bone_b"]): p for p in result["pairwise_motion"]}
    def pair(a, b):
        return pairs.get((a, b), pairs.get((b, a)))
    return {
        "object": result["object"], "action": result["action"], "action_slot": result.get("action_slot"),
        "action_frame_range": result.get("action_frame_range"), "sample_count": len(result["sample_frames"]),
        "object_scale": result["object_scale"],
        "bones": {name: {"weighted_vertex_count": result["weights"][name]["weighted_vertex_count"],
                          "track_id": data.get("track_id"),
                          "max_local_angular_displacement_degrees": data["maximum_angular_displacement_degrees"]["local"]}
                  for name, data in bones.items()},
        "adjacent_rest_angles": result["adjacent_rest_angles"],
        "overlap_pairs": {"bone_23_bone_25": {
                               "local_angular_displacement_correlation": pair("bone_23", "bone_25")["local_angular_displacement_correlation"],
                               "maximum_relative_orientation_change_degrees": pair("bone_23", "bone_25")["maximum_relative_orientation_change_degrees"]},
                           "bone_26_bone_29": {
                               "local_angular_displacement_correlation": pair("bone_26", "bone_29")["local_angular_displacement_correlation"],
                               "maximum_relative_orientation_change_degrees": pair("bone_26", "bone_29")["maximum_relative_orientation_change_degrees"]}},
        "track_metadata_available": result["track_metadata_available"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["inspect.objects", "inspect.armature", "inspect.weights",
                                                 "inspect.actions", "inspect.pose", "mutate.duplicate_rig",
                                                 "mutate.restore", "analyze.waluigi_arm"])
    parser.add_argument("--args", default="{}", help="operation arguments as JSON")
    parser.add_argument("--confirm", action="store_true", help="required for mutate.* operations")
    parser.add_argument("--port", type=int, default=47831)
    parser.add_argument("--summary", action="store_true", help="print compact Waluigi arm analysis summary")
    parser.add_argument("--object", dest="object_name", help="armature object for analyze.waluigi_arm")
    parser.add_argument("--action-name", help="action for analyze.waluigi_arm")
    parser.add_argument("--side", choices=["left"], default="left")
    parser.add_argument("--sample-count", type=int, help="uniform analysis samples")
    parser.add_argument("--frames", type=float, nargs="+", help="explicit analysis sample frames")
    parser.add_argument("--mesh", action="append", dest="meshes", help="weight mesh; may be repeated")
    opts = parser.parse_args()
    arguments = json.loads(opts.args)
    if opts.operation == "analyze.waluigi_arm" and (opts.object_name or opts.action_name or opts.sample_count or opts.frames or opts.meshes):
        if opts.object_name:
            arguments["object"] = opts.object_name
        if opts.action_name:
            arguments["action"] = opts.action_name
        arguments["side"] = opts.side
        if opts.sample_count is not None:
            arguments["sample_count"] = opts.sample_count
        if opts.frames:
            arguments["frames"] = opts.frames
        if opts.meshes:
            arguments["meshes"] = opts.meshes
    response = request(opts.operation, arguments, opts.confirm, port=opts.port)
    output = summarize_arm_analysis(response["result"]) if opts.summary and opts.operation == "analyze.waluigi_arm" else response
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
