"""Quarter-frame torso section and self-intersection audit for a preview blend."""
import argparse
import json
import os
import sys

import bpy
from mathutils.bvhtree import BVHTree


def arguments():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--rig", default="LiteralWalk_Waluigi_Target")
    parser.add_argument("--mesh-prefix", default="LiteralWalk_ARMTEST_")
    parser.add_argument("--output", required=True)
    parser.add_argument("--reference-validation", required=True,
                        help="torso-validation JSON for the approved pre-fix preview")
    return parser.parse_args(argv)


def evaluated_meshes(meshes, rig):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    inverse = rig.matrix_world.inverted_safe()
    result = []
    for obj in meshes:
        evaluated = obj.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh()
        try:
            points = [inverse @ (evaluated.matrix_world @ vertex.co)
                      for vertex in mesh.vertices]
            polygons = [tuple(poly.vertices) for poly in mesh.polygons]
            result.append((points, polygons))
        finally:
            evaluated.to_mesh_clear()
    return result


def inside_region(point):
    return abs(point.x) < 0.60 and 1.55 < point.y < 2.85 and abs(point.z) < 0.75


def self_intersections(points, polygons, rest_points):
    selected = []
    for face_index, polygon in enumerate(polygons):
        if len(polygon) < 3 or not all(inside_region(rest_points[i]) for i in polygon):
            continue
        selected.append(face_index)
    if len(selected) < 4:
        return {"tested_faces": len(selected), "nonadjacent_intersections": 0, "pairs": []}
    faces = [polygons[i] for i in selected]
    tree = BVHTree.FromPolygons(points, faces, all_triangles=False)
    pairs = []
    for a, b in tree.overlap(tree):
        if a >= b:
            continue
        if set(faces[a]).intersection(faces[b]):
            continue
        pairs.append([selected[a], selected[b]])
    return {"tested_faces": len(faces), "nonadjacent_intersections": len(pairs),
            "pairs": pairs}


def main():
    args = arguments()
    rig = bpy.data.objects.get(args.rig)
    if rig is None or rig.type != "ARMATURE":
        raise RuntimeError(f"armature not found: {args.rig}")
    meshes = [obj for obj in bpy.data.objects if obj.type == "MESH"
              and obj.name.startswith(args.mesh_prefix)
              and any(m.type == "ARMATURE" and m.object == rig for m in obj.modifiers)]
    if len(meshes) != 2:
        raise RuntimeError(f"expected 2 bound target meshes, got {len(meshes)}")

    scene = bpy.context.scene
    saved_frame = scene.frame_current + scene.frame_subframe
    saved_action = rig.animation_data.action
    saved_slot = rig.animation_data.action_slot
    saved_basis = {pb.name: pb.matrix_basis.copy() for pb in rig.pose.bones}
    saved_location = rig.location.copy()
    rig.animation_data.action = None
    for pb in rig.pose.bones:
        pb.matrix_basis.identity()
    scene.frame_set(0)
    bpy.context.view_layer.update()
    rest_data = evaluated_meshes(meshes, rig)
    rest = [row[0] for row in rest_data]
    rest_intersections = [self_intersections(rest[i], rest_data[i][1], rest[i])
                          for i in range(len(meshes))]
    rest_pair_sets = [set(tuple(pair) for pair in row["pairs"])
                      for row in rest_intersections]
    rig.animation_data.action = saved_action
    rig.animation_data.action_slot = saved_slot
    rig.location = saved_location

    bands = ((1.55, 1.95), (1.95, 2.30), (2.30, 2.60), (2.60, 2.85))
    rest_sections = {}
    for side, sign in (("left", 1), ("right", -1)):
        rows = []
        for lo, hi in bands:
            points = [point for body in rest for point in body
                      if inside_region(point) and lo <= point.y < hi and point.x * sign > 0]
            if not points:
                rows.append(None); continue
            mean_width = sum(abs(point.x) for point in points) / len(points)
            depth = max(point.z for point in points) - min(point.z for point in points)
            rows.append({"mean_abs_x": mean_width, "depth": depth})
        rest_sections[side] = rows

    samples = []
    max_intersections = 0
    minimum_width = (float("inf"), None)
    minimum_area = (float("inf"), None)
    for index in range(241):
        frame = index * 0.25
        scene.frame_set(int(frame), subframe=frame - int(frame))
        bpy.context.view_layer.update()
        evaluated = evaluated_meshes(meshes, rig)
        side_values = {}
        intersections = []
        for mi, (points, polygons) in enumerate(evaluated):
            intersections.append(self_intersections(points, polygons, rest[mi]))
        for side, sign in (("left", 1), ("right", -1)):
            rows = []
            for band_index, (lo, hi) in enumerate(bands):
                sample_points = [evaluated[mi][0][i] for mi, body in enumerate(rest)
                                 for i, point in enumerate(body)
                                 if inside_region(point) and lo <= point.y < hi
                                 and point.x * sign > 0]
                ref = rest_sections[side][band_index]
                if not sample_points or ref is None:
                    rows.append(None); continue
                width = sum(abs(point.x) for point in sample_points) / len(sample_points)
                depth = max(point.z for point in sample_points) - min(point.z for point in sample_points)
                width_ratio = width / ref["mean_abs_x"] if ref["mean_abs_x"] else None
                area_ratio = (width * depth) / (ref["mean_abs_x"] * ref["depth"]
                              if ref["mean_abs_x"] * ref["depth"] else 1.0)
                rows.append({"width_ratio": width_ratio, "area_proxy_ratio": area_ratio,
                             "vertex_count": len(sample_points)})
                if width_ratio is not None and width_ratio < minimum_width[0]:
                    minimum_width = (width_ratio, [frame, side, band_index])
                if area_ratio < minimum_area[0]:
                    minimum_area = (area_ratio, [frame, side, band_index])
            side_values[side] = rows
        for mi, row in enumerate(intersections):
            row["new_vs_rest_pairs"] = [pair for pair in row["pairs"]
                                         if tuple(pair) not in rest_pair_sets[mi]]
            row["new_vs_rest_count"] = len(row["new_vs_rest_pairs"])
        count = sum(x["new_vs_rest_count"] for x in intersections)
        max_intersections = max(max_intersections, count)
        samples.append({"frame": frame, "sides": side_values,
                        "self_intersection_by_mesh": intersections,
                        "nonadjacent_intersection_count": count})

    with open(args.reference_validation, "r", encoding="utf-8") as stream:
        reference = json.load(stream)
    reference_samples = {float(row["frame"]): row for row in reference["samples"]}
    changed_intersections = []
    for row in samples:
        old = reference_samples.get(float(row["frame"]))
        if old is None or len(old["self_intersection_by_mesh"]) != len(row["self_intersection_by_mesh"]):
            changed_intersections.append({"frame": row["frame"], "reason": "reference sample missing"})
            continue
        for mesh_index, mesh_row in enumerate(row["self_intersection_by_mesh"]):
            old_pairs = set(tuple(pair) for pair in
                            old["self_intersection_by_mesh"][mesh_index]["new_vs_rest_pairs"])
            new_pairs = set(tuple(pair) for pair in mesh_row["new_vs_rest_pairs"])
            added = sorted(new_pairs - old_pairs)
            if added:
                changed_intersections.append({"frame": row["frame"],
                    "mesh_index": mesh_index, "added_pair_count": len(added),
                    "example_pairs": [list(pair) for pair in added[:8]]})

    # Restore opened preview state before writing the report.
    for name, matrix in saved_basis.items():
        rig.pose.bones[name].matrix_basis = matrix
    rig.animation_data.action = saved_action
    rig.animation_data.action_slot = saved_slot
    rig.location = saved_location
    scene.frame_set(int(saved_frame), subframe=saved_frame - int(saved_frame))
    bpy.context.view_layer.update()
    report = {
        "schema": "mg1.literal-torso-shape-validation.v1",
        "blend": bpy.data.filepath,
        "rig": rig.name,
        "sample_count": len(samples),
        "sample_step": 0.25,
        "minimum_lateral_extent_ratio": {"value": minimum_width[0], "at": minimum_width[1]},
        "minimum_section_area_proxy_ratio": {"value": minimum_area[0], "at": minimum_area[1]},
        "maximum_nonadjacent_self_intersections": max_intersections,
        "reference_preview": reference.get("blend"),
        "added_self_intersection_pairs_vs_reference": changed_intersections,
        "rest_pose_self_intersections_by_mesh": rest_intersections,
        "samples": samples,
        "gates": {
            "no_extreme_lateral_collapse": minimum_width[0] > 0.75,
            "no_extreme_section_volume_loss": minimum_area[0] > 0.50,
            "no_new_self_intersections_vs_approved_preview": not changed_intersections,
            "all_241_quarter_frames_checked": len(samples) == 241,
        },
    }
    report["passed"] = all(report["gates"].values())
    output = os.path.abspath(args.output)
    os.makedirs(os.path.dirname(output), exist_ok=True)
    with open(output, "w", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
    print("TORSO_SHAPE_VALIDATION", json.dumps({k: report[k] for k in (
        "sample_count", "minimum_lateral_extent_ratio", "minimum_section_area_proxy_ratio",
        "maximum_nonadjacent_self_intersections", "gates", "passed")}, sort_keys=True))
    if not report["passed"]:
        raise RuntimeError("torso shape validation failed")


if __name__ == "__main__":
    main()
