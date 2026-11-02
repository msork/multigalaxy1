"""Read-only torso deformation and influence audit for a literal-walk preview."""
import argparse
import json
import math
import os
import sys

import bpy
from mathutils import Vector


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--rig", default="LiteralWalk_Waluigi_Target")
    p.add_argument("--mesh-prefix", default="LiteralWalk_ARMTEST_")
    p.add_argument("--output", required=True)
    return p.parse_args(argv)


def evaluated_vertices(meshes, rig):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    inv = rig.matrix_world.inverted_safe()
    rows = []
    for obj in meshes:
        evaluated = obj.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh()
        try:
            rows.append([inv @ (evaluated.matrix_world @ v.co) for v in mesh.vertices])
        finally:
            evaluated.to_mesh_clear()
    return rows


def bbox(points):
    if not points:
        return None
    return {
        "min": [min(p[i] for p in points) for i in range(3)],
        "max": [max(p[i] for p in points) for i in range(3)],
    }


def weighted_bones(obj, vertex_index, threshold=0.001):
    memberships = obj.data.vertices[vertex_index].groups
    result = []
    for membership in memberships:
        if membership.weight <= threshold:
            continue
        group_name = obj.vertex_groups[membership.group].name
        result.append((group_name, float(membership.weight)))
    return result


def main():
    args = parse_args()
    rig = bpy.data.objects.get(args.rig)
    if rig is None or rig.type != "ARMATURE":
        raise RuntimeError(f"armature not found: {args.rig}")
    meshes = [obj for obj in bpy.data.objects if obj.type == "MESH"
              and obj.name.startswith(args.mesh_prefix)
              and any(mod.type == "ARMATURE" and mod.object == rig for mod in obj.modifiers)]
    if len(meshes) != 2:
        raise RuntimeError(f"expected 2 target meshes, got {len(meshes)}")
    baseline_rig = bpy.data.objects.get("Waluigi_Arm_Test")
    baseline_meshes = [obj for obj in bpy.data.objects if obj.type == "MESH"
                       and obj.name.startswith("ARMTEST_")
                       and "ArmRetarget" not in obj.name
                       and any(mod.type == "ARMATURE" and mod.object == baseline_rig
                               for mod in obj.modifiers)]
    scene = bpy.context.scene
    saved_frame = scene.frame_current + scene.frame_subframe
    saved_basis = {pb.name: pb.matrix_basis.copy() for pb in rig.pose.bones}
    scene.frame_set(0)
    for pb in rig.pose.bones:
        pb.matrix_basis.identity()
    bpy.context.view_layer.update()
    rest = evaluated_vertices(meshes, rig)
    # Waluigi's local vertical axis is Y. Select the central torso area in
    # armature space; retain a broad region and report exact bounds for audit.
    torso_indices = []
    for mesh_index, points in enumerate(rest):
        ids = [i for i, point in enumerate(points)
               if abs(point.x) < 0.60 and 1.55 < point.y < 2.85
               and abs(point.z) < 0.75]
        torso_indices.append(ids)

    meshes_report = {}
    for mi, obj in enumerate(meshes):
        ids = torso_indices[mi]
        per_bone = {}
        weighted_centroids = {}
        weighted_positions = {}
        for vertex in obj.data.vertices:
            for group, weight in weighted_bones(obj, vertex.index):
                per_bone.setdefault(group, {"count": 0, "weight_sum": 0.0})
        for group in per_bone:
            group_index = obj.vertex_groups[group].index
            points, weights = [], []
            for vertex_index in ids:
                for membership in obj.data.vertices[vertex_index].groups:
                    if membership.group == group_index and membership.weight > 0.001:
                        points.append(rest[mi][vertex_index])
                        weights.append(float(membership.weight))
                        per_bone[group]["count"] += 1
                        per_bone[group]["weight_sum"] += float(membership.weight)
            if points:
                total = sum(weights)
                weighted_centroids[group] = [sum(p[axis] * w for p, w in zip(points, weights)) / total
                                             for axis in range(3)]
                weighted_positions[group] = points
        meshes_report[obj.name] = {
            "vertex_count": len(obj.data.vertices),
            "torso_region_vertex_count": len(ids),
            "torso_rest_bounds_armature_space": bbox([rest[mi][i] for i in ids]),
            "torso_influencing_groups": {k: v for k, v in per_bone.items() if v["count"]},
            "torso_group_weighted_centroids": weighted_centroids,
            "torso_group_weighted_bounds": {k: bbox(v) for k, v in weighted_positions.items()},
        }

    # Focus on region vertices and measure bilateral half-width/depth by height
    # bin. This is a deformation/volume proxy, not a watertight volume claim.
    samples = {}
    baseline_samples = {}
    arm_neutral_samples = {}
    isolated_neutral_samples = {}
    for frame in range(0, 61):
        scene.frame_set(frame)
        bpy.context.view_layer.update()
        posed = evaluated_vertices(meshes, rig)
        side_report = {}
        for side, predicate in (("left", lambda p: p.x > 0), ("right", lambda p: p.x < 0)):
            by_band = []
            for lo, hi in ((1.55, 1.95), (1.95, 2.30), (2.30, 2.60), (2.60, 2.85)):
                p_rest, p_pose = [], []
                for mi, ids in enumerate(torso_indices):
                    for i in ids:
                        if lo <= rest[mi][i].y < hi and predicate(rest[mi][i]):
                            p_rest.append(rest[mi][i]); p_pose.append(posed[mi][i])
                if p_pose:
                    center_x = sum(p.x for p in p_pose) / len(p_pose)
                    depth = max(p.z for p in p_pose) - min(p.z for p in p_pose)
                    side_width = sum(abs(p.x) for p in p_pose) / len(p_pose)
                    rest_width = sum(abs(p.x) for p in p_rest) / len(p_rest)
                    by_band.append({"height_band": [lo, hi], "vertex_count": len(p_pose),
                        "mean_abs_lateral_extent": side_width,
                        "rest_mean_abs_lateral_extent": rest_width,
                        "lateral_extent_ratio": side_width / rest_width if rest_width else None,
                        "mean_lateral_center": center_x, "depth": depth})
            side_report[side] = by_band
        bone_report = {}
        for name in ("bone_6", "bone_19", "bone_20", "bone_21", "bone_22", "bone_57"):
            pb = rig.pose.bones.get(name)
            if pb:
                bone_report[name] = {
                    "basis": [[float(x) for x in row] for row in pb.matrix_basis],
                    "pose": [[float(x) for x in row] for row in pb.matrix],
                    "rest": [[float(x) for x in row] for row in rig.data.bones[name].matrix_local],
                    "rotation_degrees": math.degrees(pb.matrix_basis.to_quaternion().angle),
                }
        samples[str(frame)] = {"sides": side_report, "torso_pose_bones": bone_report}
        # Diagnostic counterfactual: retain torso/root pose but neutralize both
        # arm deformation branches. This isolates arm-group pulling of belly
        # vertices without writing any pose or action to disk.
        arm_names = [f"bone_{i}" for i in list(range(22, 31)) + list(range(57, 66))]
        arm_saved = {name: rig.pose.bones[name].matrix_basis.copy()
                     for name in arm_names if name in rig.pose.bones}
        for name in arm_saved:
            rig.pose.bones[name].matrix_basis.identity()
        bpy.context.view_layer.update()
        neutral_arms = evaluated_vertices(meshes, rig)
        neutral_side_report = {}
        for side, predicate in (("left", lambda p: p.x > 0), ("right", lambda p: p.x < 0)):
            bands = []
            for lo, hi in ((1.55, 1.95), (1.95, 2.30), (2.30, 2.60), (2.60, 2.85)):
                rest_points, points = [], []
                for mi, ids in enumerate(torso_indices):
                    for i in ids:
                        if lo <= rest[mi][i].y < hi and predicate(rest[mi][i]):
                            rest_points.append(rest[mi][i]); points.append(neutral_arms[mi][i])
                if points:
                    width = sum(abs(p.x) for p in points) / len(points)
                    rest_width = sum(abs(p.x) for p in rest_points) / len(rest_points)
                    bands.append({"height_band": [lo, hi],
                        "lateral_extent_ratio": width / rest_width if rest_width else None,
                        "depth": max(p.z for p in points) - min(p.z for p in points)})
            neutral_side_report[side] = bands
        arm_neutral_samples[str(frame)] = neutral_side_report
        for name, basis in arm_saved.items():
            rig.pose.bones[name].matrix_basis = basis
        bpy.context.view_layer.update()
        isolated_neutral_samples[str(frame)] = {}
        for isolated in ("bone_6", "bone_19", "bone_20", "bone_21"):
            pb = rig.pose.bones.get(isolated)
            if pb is None:
                continue
            old_basis = pb.matrix_basis.copy()
            pb.matrix_basis.identity()
            bpy.context.view_layer.update()
            isolated_pose = evaluated_vertices(meshes, rig)
            isolated_sides = {}
            for side, predicate in (("left", lambda p: p.x > 0), ("right", lambda p: p.x < 0)):
                bands = []
                for lo, hi in ((1.55, 1.95), (1.95, 2.30), (2.30, 2.60), (2.60, 2.85)):
                    rest_points, points = [], []
                    for mi, ids in enumerate(torso_indices):
                        for i in ids:
                            if lo <= rest[mi][i].y < hi and predicate(rest[mi][i]):
                                rest_points.append(rest[mi][i]); points.append(isolated_pose[mi][i])
                    if points:
                        width = sum(abs(p.x) for p in points) / len(points)
                        rest_width = sum(abs(p.x) for p in rest_points) / len(rest_points)
                        bands.append(width / rest_width if rest_width else None)
                isolated_sides[side] = bands
            isolated_neutral_samples[str(frame)][isolated] = isolated_sides
            pb.matrix_basis = old_basis
            bpy.context.view_layer.update()
        if baseline_rig and len(baseline_meshes) == 2:
            baseline_vertices = evaluated_vertices(baseline_meshes, baseline_rig)
            baseline_side_report = {}
            for side, predicate in (("left", lambda p: p.x > 0), ("right", lambda p: p.x < 0)):
                bands = []
                for lo, hi in ((1.55, 1.95), (1.95, 2.30), (2.30, 2.60), (2.60, 2.85)):
                    points = []
                    for mi, ids in enumerate(torso_indices):
                        for i in ids:
                            if lo <= rest[mi][i].y < hi and predicate(rest[mi][i]):
                                points.append(baseline_vertices[mi][i])
                    if points:
                        width = sum(abs(p.x) for p in points) / len(points)
                        rest_width = sum(abs(rest[mi][i].x) for mi, ids in enumerate(torso_indices)
                                         for i in ids if lo <= rest[mi][i].y < hi
                                         and predicate(rest[mi][i])) / len(points)
                        bands.append({"height_band": [lo, hi],
                            "lateral_extent_ratio": width / rest_width if rest_width else None,
                            "depth": max(p.z for p in points) - min(p.z for p in points)})
                baseline_side_report[side] = bands
            baseline_samples[str(frame)] = baseline_side_report

    # Restore the saved action pose exactly after the read-only measurements.
    for name, matrix in saved_basis.items():
        rig.pose.bones[name].matrix_basis = matrix
    scene.frame_set(math.floor(saved_frame), subframe=saved_frame - math.floor(saved_frame))
    bpy.context.view_layer.update()
    report = {
        "schema": "mg1.literal-torso-deformation-audit.v1",
        "blend": bpy.data.filepath,
        "rig": rig.name,
        "meshes": meshes_report,
        "samples": samples,
        "baseline_samples": baseline_samples,
        "arm_neutral_samples": arm_neutral_samples,
        "isolated_neutral_samples": isolated_neutral_samples,
        "region_definition_armature_space": {"abs_x_lt": 0.60, "y": [1.55, 2.85], "abs_z_lt": 0.75},
        "notes": ["Read-only audit; pose basis restored after sampling.",
                  "Lateral extent is a torso section deformation proxy, not a certified enclosed volume."]
    }
    output = os.path.abspath(args.output)
    os.makedirs(os.path.dirname(output), exist_ok=True)
    with open(output, "w", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
    print("TORSO_AUDIT", json.dumps({"report": output,
        "torso_group_counts": {name: {g: v["count"] for g, v in info["torso_influencing_groups"].items()}
                               for name, info in meshes_report.items()},
        "frames": len(samples)}, sort_keys=True))


if __name__ == "__main__":
    main()
