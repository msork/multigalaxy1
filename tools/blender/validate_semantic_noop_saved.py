"""Validate no-op preview action replay after reopening a saved .blend."""
import argparse
import json
import os
import sys

import bpy


def args_parse():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True)
    parser.add_argument("--baseline-report", required=True)
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--end", type=int, default=60)
    return parser.parse_args(argv)


def max_matrix_error(a, b):
    return max(abs(float(a[r][c]) - float(b[r][c]))
               for r in range(4) for c in range(4))


def vertices(objects):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    out = []
    for obj in objects:
        evaluated = obj.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh()
        try:
            out.append([evaluated.matrix_world @ v.co for v in mesh.vertices])
        finally:
            evaluated.to_mesh_clear()
    return out


def weighted_centroid(objects, mesh_vertices, group_name):
    total = 0.0
    sums = [0.0, 0.0, 0.0]
    for obj, points in zip(objects, mesh_vertices):
        group = obj.vertex_groups.get(group_name)
        if group is None:
            continue
        for index, point in enumerate(points):
            try:
                weight = group.weight(index)
            except RuntimeError:
                continue
            total += weight
            for axis in range(3):
                sums[axis] += float(point[axis]) * weight
    return [v / total for v in sums] if total else None


def main():
    args = args_parse()
    ref_rig = bpy.data.objects.get("ValidatedWalk_NoOp_Reference_Rig")
    preview_rig = bpy.data.objects.get("ValidatedWalk_NoOp_Preview_Rig")
    if not ref_rig or not preview_rig:
        raise RuntimeError("saved no-op file is missing its two duplicate rigs")
    ref_meshes = sorted((o for o in bpy.data.objects if o.name.startswith("ValidatedWalk_NoOp_Reference_") and o.type == "MESH"), key=lambda o:o.name)
    preview_meshes = sorted((o for o in bpy.data.objects if o.name.startswith("ValidatedWalk_NoOp_Preview_") and o.type == "MESH"), key=lambda o:o.name)
    if len(ref_meshes) != len(preview_meshes) or not ref_meshes:
        raise RuntimeError("reference/preview mesh sets do not match")
    ref_collection = bpy.data.collections.get("ValidatedWalk_NoOp_Reference")
    if ref_collection:
        ref_collection.hide_viewport = False
        ref_collection.hide_render = False
    for obj in ref_meshes + [ref_rig]:
        obj.hide_viewport = False
        obj.hide_render = False
        obj.hide_set(False)

    frames = list(range(args.start, args.end + 1))
    max_vertex = {"error": 0.0}
    max_pose = {"error": 0.0}
    max_basis = {"error": 0.0}
    motion = 0.0
    first_ref_vertices = None
    first_preview_vertices = None
    vertex_sum = 0.0
    vertex_count = 0
    per_mesh = {}
    action_info = {}
    with open(args.baseline_report, "r", encoding="utf-8") as stream:
        baseline_report = json.load(stream)
    if baseline_report.get("schema") != "mg1.validated-walk-noop-baseline.v1":
        raise RuntimeError("baseline report does not describe the validated walk comparison")
    baseline_frames = baseline_report.get("frames", [])
    foot_names = list(baseline_report.get("foot_tracks_world", {}).keys())
    cap_group = baseline_report.get("source_comparison", {}).get("head_cap_weighted_centroid_group")
    if baseline_frames != frames or not foot_names or not cap_group:
        raise RuntimeError("baseline report lacks matching foot/head validation tracks")
    saved_foot_error = {"error": 0.0}
    saved_cap_error = 0.0
    for rig, label in ((ref_rig, "reference"), (preview_rig, "preview")):
        ad = rig.animation_data
        if not ad or not ad.action:
            raise RuntimeError(f"{label} rig has no saved action")
        action_info[label] = {"action": ad.action.name,
                              "slot": ad.action_slot.identifier if ad.action_slot else None}

    for frame in frames:
        bpy.context.scene.frame_set(frame)
        bpy.context.view_layer.update()
        depsgraph = bpy.context.evaluated_depsgraph_get()
        rr = ref_rig.evaluated_get(depsgraph)
        pr = preview_rig.evaluated_get(depsgraph)
        for bone in rr.pose.bones:
            err = max_matrix_error(bone.matrix, pr.pose.bones[bone.name].matrix)
            if err > max_pose["error"]:
                max_pose = {"error": err, "frame": frame, "bone": bone.name}
            err = max_matrix_error(bone.matrix_basis, pr.pose.bones[bone.name].matrix_basis)
            if err > max_basis["error"]:
                max_basis = {"error": err, "frame": frame, "bone": bone.name}
        ref_v = vertices(ref_meshes)
        prev_v = vertices(preview_meshes)
        sample_index = len([f for f in frames if f < frame])
        for name in foot_names:
            ref_tail = rr.matrix_world @ rr.pose.bones[name].tail
            prev_tail = pr.matrix_world @ pr.pose.bones[name].tail
            err = float((ref_tail - prev_tail).length)
            baseline_tail = baseline_report["foot_tracks_world"][name][sample_index]
            err = max(err, sum((float(prev_tail[i]) - float(baseline_tail[i])) ** 2
                               for i in range(3)) ** 0.5)
            if err > saved_foot_error["error"]:
                saved_foot_error = {"error": err, "frame": frame, "bone": name}
        ref_cap = weighted_centroid(ref_meshes, ref_v, cap_group)
        prev_cap = weighted_centroid(preview_meshes, prev_v, cap_group)
        if ref_cap is None or prev_cap is None:
            raise RuntimeError(f"head/cap weighted group {cap_group} is absent")
        cap_err = sum((ref_cap[i] - prev_cap[i]) ** 2 for i in range(3)) ** 0.5
        baseline_cap = baseline_report["head_cap_centroid_track_world"][sample_index]
        cap_err = max(cap_err, sum((prev_cap[i] - float(baseline_cap[i])) ** 2 for i in range(3)) ** 0.5)
        saved_cap_error = max(saved_cap_error, cap_err)
        if first_ref_vertices is None:
            first_ref_vertices = ref_v
            first_preview_vertices = prev_v
        else:
            for mesh_i, mesh_vertices in enumerate(prev_v):
                for vi, point in enumerate(mesh_vertices):
                    motion = max(motion, float((point - first_preview_vertices[mesh_i][vi]).length))
        for mesh_i, (a, b) in enumerate(zip(ref_v, prev_v)):
            name = preview_meshes[mesh_i].name
            if len(a) != len(b):
                raise RuntimeError(f"vertex count differs for {name}")
            mesh_max = per_mesh.setdefault(name, {"error": 0.0})
            for vi, (va, vb) in enumerate(zip(a, b)):
                err = float((va - vb).length)
                vertex_sum += err * err
                vertex_count += 1
                if err > mesh_max["error"]:
                    mesh_max.update(error=err, frame=frame, vertex=vi)
                if err > max_vertex["error"]:
                    max_vertex = {"error": err, "frame": frame,
                                  "mesh": name, "vertex": vi}

    report = {
        "schema": "mg1.validated-walk-noop-saved-validation.v2",
        "file": bpy.data.filepath,
        "frames": frames,
        "actions": action_info,
        "baseline_source_comparison": baseline_report["source_comparison"],
        "saved_foot_trajectory_max_error": saved_foot_error,
        "saved_head_cap_centroid_max_error": saved_cap_error,
        "rig_world_error": max_matrix_error(ref_rig.matrix_world, preview_rig.matrix_world),
        "rig_world_scale": list(preview_rig.matrix_world.to_scale()),
        "preview_mesh_bindings": [{
            "object": mesh.name,
            "object_scale": list(mesh.scale),
            "world_scale": list(mesh.matrix_world.to_scale()),
            "parent": mesh.parent.name if mesh.parent else None,
            "parent_inverse_scale": list(mesh.matrix_parent_inverse.to_scale()),
            "armature_modifier_targets": [m.object.name if m.object else None
                for m in mesh.modifiers if m.type == "ARMATURE"],
        } for mesh in preview_meshes],
        "pose_matrix_max_error": max_pose,
        "matrix_basis_max_error": max_basis,
        "mesh_vertex_world_max_error": max_vertex,
        "mesh_vertex_world_rmse": (vertex_sum / max(1, vertex_count)) ** 0.5,
        "mesh_vertex_samples": vertex_count,
        "per_mesh_max_error": per_mesh,
        "preview_motion_from_first_frame_max": motion,
        "gate": "pass" if max_vertex["error"] <= 1e-5 and max_pose["error"] <= 1e-5 and motion > 1e-4 and
            saved_foot_error["error"] <= 1e-5 and saved_cap_error <= 1e-4 and
            baseline_report.get("gate") == "pass" else "fail",
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.report)), exist_ok=True)
    with open(args.report, "w", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print("MG1_NOOP_SAVED_VALIDATION=" + json.dumps(report))
    if report["gate"] != "pass":
        raise RuntimeError("reopened no-op preview differs from reference or has no animation")


if __name__ == "__main__":
    main()
