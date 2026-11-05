"""Measure skinned arm surface continuity on a saved walk preview.

This is read-only with respect to the saved scene. It creates temporary mesh
object/data copies with only their armature modifier enabled, samples evaluated
world-space vertices, then removes those temporary objects.
"""
import argparse
import json
import math
import os
import sys
from collections import Counter, defaultdict, deque

import bpy
from mathutils import Vector
from mathutils.kdtree import KDTree


SEAMS = {
    "left_shoulder": ("bone_23", "bone_25"),
    "left_elbow": ("bone_25", "bone_29"),
    "left_wrist_forearm": ("bone_29", "bone_27"),
    "left_wrist_glove": ("bone_27", "bone_30"),
    "right_shoulder": ("bone_58", "bone_60"),
    "right_elbow": ("bone_60", "bone_64"),
    "right_wrist_forearm": ("bone_64", "bone_62"),
    "right_wrist_glove": ("bone_62", "bone_65"),
}
ARM_ANALYSIS_BONES = (
    "bone_22", "bone_23", "bone_24", "bone_25", "bone_26", "bone_27",
    "bone_29", "bone_30", "bone_57", "bone_58", "bone_59", "bone_60",
    "bone_61", "bone_62", "bone_64", "bone_65",
)


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--target-rig", default="DirectWalk_Waluigi_Target")
    p.add_argument("--baseline-rig", default="Waluigi_Arm_Test")
    p.add_argument("--candidate-prefix", default="DirectWalk_ARMTEST_")
    p.add_argument("--baseline-prefix", default="ARMTEST_")
    p.add_argument("--frames", nargs="+", type=float, default=[0, 15, 30, 45, 60])
    p.add_argument("--output", required=True)
    return p.parse_args(argv)


def connected_components(mesh):
    adjacency = [[] for _ in mesh.vertices]
    for edge in mesh.edges:
        a, b = edge.vertices
        adjacency[a].append(b)
        adjacency[b].append(a)
    seen = set()
    components = []
    for start in range(len(mesh.vertices)):
        if start in seen:
            continue
        queue = [start]
        seen.add(start)
        members = []
        while queue:
            index = queue.pop()
            members.append(index)
            for nxt in adjacency[index]:
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append(nxt)
        components.append(members)
    return components


def weights_for_mesh(obj):
    names = {g.index: g.name for g in obj.vertex_groups}
    return [{names.get(a.group, ""): float(a.weight)
             for a in vertex.groups if a.weight > 0.0}
            for vertex in obj.data.vertices]


def group_static_summary(obj, weights):
    result = {}
    for name in ARM_ANALYSIS_BONES:
        total = count = 0.0
        weighted_points = []
        for vertex, row in zip(obj.data.vertices, weights):
            w = row.get(name, 0.0)
            total += w
            if w > 0.01:
                count += 1
                weighted_points.append((vertex.co.copy(), w))
        if weighted_points:
            weight_sum = sum(w for _, w in weighted_points)
            centroid = sum((co * w for co, w in weighted_points), Vector()) / weight_sum
            bounds = [[min(float(co[i]) for co, _ in weighted_points),
                       max(float(co[i]) for co, _ in weighted_points)] for i in range(3)]
            centroid_row = [float(x) for x in centroid]
        else:
            centroid_row, bounds = None, None
        result[name] = {"weighted_vertex_count_gt_0_01": int(count),
                        "weight_sum": total, "zero_weights": count == 0,
                        "mesh_local_weighted_centroid": centroid_row,
                        "mesh_local_weighted_bounds_xyz": bounds}
    comps = connected_components(obj.data)
    component_rows = []
    for cid, indices in enumerate(comps):
        sums = Counter()
        for index in indices:
            sums.update(weights[index])
        dominant = sums.most_common(8)
        component_rows.append({"component": cid, "vertex_count": len(indices),
                               "dominant_weight_sums": dict(dominant)})
    return {"vertex_count": len(obj.data.vertices), "edge_count": len(obj.data.edges),
            "face_count": len(obj.data.polygons), "arm_group_influence": result,
            "connected_components": component_rows}


def duplicate_armature_only(source, label):
    temp = source.copy()
    temp.data = source.data.copy()
    temp.name = label
    temp.data.name = label + "_MeshData"
    bpy.context.scene.collection.objects.link(temp)
    if source.parent:
        temp.parent = source.parent
    temp.matrix_parent_inverse = source.matrix_parent_inverse.copy()
    temp.matrix_basis = source.matrix_basis.copy()
    for mod in temp.modifiers:
        if mod.type == 'ARMATURE':
            mod.object = next((m.object for m in source.modifiers
                               if m.type == 'ARMATURE' and m.object), None)
            mod.show_viewport = True
        else:
            mod.show_viewport = False
    temp.hide_render = True
    temp.hide_set(True)
    return temp


def evaluated_armature_vertices(temp, rig):
    dg = bpy.context.evaluated_depsgraph_get()
    ev = temp.evaluated_get(dg)
    rig_ev = rig.evaluated_get(dg)
    world_to_armature = rig_ev.matrix_world.inverted_safe()
    me = ev.to_mesh()
    try:
        if len(me.vertices) != len(temp.data.vertices):
            raise RuntimeError(f"armature-only evaluation changed vertex count: {temp.name}")
        return [world_to_armature @ ev.matrix_world @ v.co for v in me.vertices]
    finally:
        ev.to_mesh_clear()


def nearest_stats(points_a, points_b):
    if not points_a or not points_b:
        return {"min": None, "p50": None, "p95": None, "max": None,
                "count_a": len(points_a), "count_b": len(points_b)}
    tree = KDTree(len(points_b))
    for i, point in enumerate(points_b):
        tree.insert(point, i)
    tree.balance()
    distances = sorted(tree.find(point)[2] for point in points_a)
    def quantile(q):
        return distances[min(int(round(q * (len(distances) - 1))), len(distances) - 1)]
    return {"min": distances[0], "p50": quantile(0.50), "p95": quantile(0.95),
            "max": distances[-1], "count_a": len(points_a), "count_b": len(points_b)}


def overlap_vertex_stats(points_a, points_b, tolerance=1e-5):
    if not points_a or not points_b:
        return {"near_duplicate_pairs": 0, "tolerance": tolerance}
    tree = KDTree(len(points_b))
    for index, point in enumerate(points_b):
        tree.insert(point, index)
    tree.balance()
    count = sum(tree.find(point)[2] <= tolerance for point in points_a)
    return {"near_duplicate_pairs": int(count), "tolerance": tolerance,
            "fraction_of_mesh_a": count / len(points_a)}


def edge_length_stats(obj, vertices, weights, group_a, group_b):
    lengths = []
    for edge in obj.data.edges:
        a, b = edge.vertices
        wa = weights[a].get(group_a, 0.0) + weights[a].get(group_b, 0.0)
        wb = weights[b].get(group_a, 0.0) + weights[b].get(group_b, 0.0)
        # Restrict to edges spanning the joint's two weighted regions.
        da = max(weights[a], key=weights[a].get) if weights[a] else ""
        db = max(weights[b], key=weights[b].get) if weights[b] else ""
        if wa > 0.05 and wb > 0.05 and {da, db} == {group_a, group_b}:
            lengths.append((vertices[a] - vertices[b]).length)
    lengths.sort()
    if not lengths:
        return {"count": 0, "min": None, "p50": None, "p95": None, "max": None}
    return {"count": len(lengths), "min": lengths[0],
            "p50": lengths[len(lengths)//2],
            "p95": lengths[min(int(0.95 * (len(lengths)-1)), len(lengths)-1)],
            "max": lengths[-1]}


def main():
    args = parse_args()
    target = bpy.data.objects.get(args.target_rig)
    baseline = bpy.data.objects.get(args.baseline_rig)
    if not target or not baseline:
        raise RuntimeError("target or baseline armature not found")
    candidate_meshes = sorted([o for o in bpy.data.objects if o.type == 'MESH'
                               and o.name.startswith(args.candidate_prefix)], key=lambda o: o.name)
    baseline_meshes = sorted([o for o in bpy.data.objects if o.type == 'MESH'
                              and o.name.startswith(args.baseline_prefix)
                              and any(m.type == 'ARMATURE' and m.object == baseline
                                      for m in o.modifiers)], key=lambda o: o.name)
    if len(candidate_meshes) != len(baseline_meshes) or not candidate_meshes:
        raise RuntimeError(f"candidate/baseline mesh count mismatch: {len(candidate_meshes)} / {len(baseline_meshes)}")

    old_frame, old_subframe = bpy.context.scene.frame_current, bpy.context.scene.frame_subframe
    temp_pairs = []
    mesh_rows = {}
    overlap_rows = {}
    try:
        for cand, base in zip(candidate_meshes, baseline_meshes):
            cand_temp = duplicate_armature_only(cand, "_MeshAudit_Candidate_" + cand.name)
            base_temp = duplicate_armature_only(base, "_MeshAudit_Baseline_" + base.name)
            temp_pairs.append((cand_temp, base_temp))
            mesh_rows[cand.name] = {
                "baseline_mesh": base.name,
                "candidate_static": group_static_summary(cand, weights_for_mesh(cand)),
                "baseline_static": group_static_summary(base, weights_for_mesh(base)),
                "frame_samples": {},
            }

        for frame in args.frames:
            whole = math.floor(frame)
            bpy.context.scene.frame_set(whole, subframe=frame-whole)
            bpy.context.view_layer.update()
            candidate_vertices_for_frame = []
            for (cand_temp, base_temp), (cand, base) in zip(temp_pairs,
                                                           zip(candidate_meshes, baseline_meshes)):
                wcand = evaluated_armature_vertices(cand_temp, target)
                wbase = evaluated_armature_vertices(base_temp, baseline)
                candidate_vertices_for_frame.append((cand.name, wcand))
                cw, bw = weights_for_mesh(cand), weights_for_mesh(base)
                row = {"seams": {}, "max_vertex_delta_vs_baseline": 0.0}
                if len(wcand) == len(wbase):
                    row["max_vertex_delta_vs_baseline"] = max(
                        ((a - b).length for a, b in zip(wcand, wbase)), default=0.0)
                for seam, (ga, gb) in SEAMS.items():
                    def dominant_points(verts, weight_rows, name):
                        return [v for v, weights in zip(verts, weight_rows)
                                if weights and max(weights, key=weights.get) == name]
                    cand_a, cand_b = dominant_points(wcand, cw, ga), dominant_points(wcand, cw, gb)
                    base_a, base_b = dominant_points(wbase, bw, ga), dominant_points(wbase, bw, gb)
                    row["seams"][seam] = {
                        "group_a": ga, "group_b": gb,
                        "candidate_nearest_surface": nearest_stats(cand_a, cand_b),
                        "baseline_nearest_surface": nearest_stats(base_a, base_b),
                        "candidate_cross_weight_edge_lengths": edge_length_stats(cand, wcand, cw, ga, gb),
                        "baseline_cross_weight_edge_lengths": edge_length_stats(base, wbase, bw, ga, gb),
                    }
                mesh_rows[cand.name]["frame_samples"][str(frame)] = row
            pair_rows = {}
            for i, (name_a, points_a) in enumerate(candidate_vertices_for_frame):
                for name_b, points_b in candidate_vertices_for_frame[i + 1:]:
                    pair_rows[f"{name_a} :: {name_b}"] = overlap_vertex_stats(points_a, points_b)
            overlap_rows[str(frame)] = pair_rows

        report = {"schema": "mg1.direct-arm-mesh-continuity-audit.v2",
                  "blend": bpy.data.filepath,
                  "target_rig": target.name,
                  "baseline_rig": baseline.name,
                  "coordinate_space": "each mesh point transformed from world into its bound armature's evaluated local space; this removes object-scale mismatch",
                  "frames": args.frames,
                  "candidate_mesh_pair_overlap": overlap_rows,
                  "meshes": mesh_rows}
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, allow_nan=False)
            f.write("\n")
        print(json.dumps({"output": os.path.abspath(args.output),
                          "mesh_objects": len(mesh_rows), "frames": args.frames}))
    finally:
        bpy.context.scene.frame_set(old_frame, subframe=old_subframe)
        bpy.context.view_layer.update()
        for a, b in temp_pairs:
            for obj in (a, b):
                data = obj.data
                bpy.data.objects.remove(obj, do_unlink=True)
                if data.users == 0:
                    bpy.data.meshes.remove(data)


if __name__ == "__main__":
    main()
