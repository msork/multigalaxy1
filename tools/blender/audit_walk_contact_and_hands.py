"""Read-only audit of Luigi walk foot motion and Waluigi contact/hand weights.

Run on the v3 walk preview or its source validation scene. Sampled source
curves are applied only to a disposable Luigi rig duplicate.
"""
import argparse
import json
import math
import os
import sys

import bpy
from mathutils import Vector

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import prototype_direct_luigi_walk as direct
from convert_native_owner_actions import duplicate_rig


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--source-rig", default="luigi.001_armature")
    p.add_argument("--source-action", default="luigi.001_armature_walk")
    p.add_argument("--target-rig", default="DirectWalk_Waluigi_Target")
    p.add_argument("--mesh-prefix", default="DirectWalk_ARMTEST_")
    p.add_argument("--output", required=True)
    return p.parse_args(argv)


def world_points(rig, pose_matrices, name, is_tail=False):
    bone = rig.data.bones[name]
    local = Vector((0, bone.length, 0)) if is_tail else Vector((0, 0, 0))
    return direct.object_basis(rig) @ (pose_matrices[name] @ local)


def evaluated_mesh_vertices(obj):
    ev = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = ev.to_mesh()
    try:
        return [ev.matrix_world @ v.co for v in mesh.vertices]
    finally:
        ev.to_mesh_clear()


def mesh_weights(obj):
    names = {g.index: g.name for g in obj.vertex_groups}
    return [{names[a.group]: float(a.weight) for a in v.groups if a.weight > 0.0}
            for v in obj.data.vertices]


def weighted_stats(obj, vertices, weights, group):
    rows = [(p, w.get(group, 0.0)) for p, w in zip(vertices, weights)
            if w.get(group, 0.0) > 0.01]
    if not rows:
        return {"count_gt_0_01": 0, "weight_sum": 0.0,
                "centroid_world": None, "world_bounds": None}
    total = sum(w for _, w in rows)
    centroid = sum((p * w for p, w in rows), Vector()) / total
    bounds = [[min(float(p[i]) for p, _ in rows), max(float(p[i]) for p, _ in rows)]
              for i in range(3)]
    return {"count_gt_0_01": len(rows), "weight_sum": total,
            "centroid_world": list(centroid), "world_bounds": bounds}


def speed(points):
    return [(points[i + 1] - points[i]).length for i in range(len(points) - 1)] + [0.0]


def stance_candidates(points, up_axis=2):
    velocities = speed(points)
    heights = [p[up_axis] for p in points]
    lo, hi = min(heights), max(heights)
    # Low vertical speed and near the trajectory's minimum identify stance
    # candidates without assuming equal left/right phase.
    v_threshold = max(1e-5, sorted(velocities)[int(0.30 * (len(velocities)-1))])
    h_threshold = lo + 0.12 * max(hi - lo, 1e-8)
    return [i for i, (v, h) in enumerate(zip(velocities, heights))
            if v <= v_threshold and h <= h_threshold], velocities, heights


def runs(indices):
    if not indices:
        return []
    out, start, prev = [], indices[0], indices[0]
    for value in indices[1:]:
        if value != prev + 1:
            out.append([start, prev])
            start = value
        prev = value
    out.append([start, prev])
    return out


def main():
    args = parse_args()
    scene = bpy.context.scene
    source = bpy.data.objects.get(args.source_rig)
    target = bpy.data.objects.get(args.target_rig)
    action = bpy.data.actions.get(args.source_action)
    if not all((source, target, action)):
        raise RuntimeError("source, source action, or target rig is missing")
    control = duplicate_rig(source, "_WalkContact_Audit_Luigi")
    control.matrix_basis = direct.object_basis(source)
    for name in source.pose.bones.keys():
        control.pose.bones[name].rotation_mode = source.pose.bones[name].rotation_mode
    curves = direct.curves_for_slot(action, direct.action_slot(action,
        source.animation_data.action_slot.identifier if source.animation_data and
        source.animation_data.action_slot else None))
    meshes = sorted([o for o in bpy.data.objects if o.type == 'MESH' and
                     o.name.startswith(args.mesh_prefix) and any(
                         m.type == 'ARMATURE' and m.object == target for m in o.modifiers)],
                    key=lambda o: o.name)
    if not meshes:
        raise RuntimeError("no target-bound preview meshes found")
    lower_groups = [f"bone_{i}" for i in range(7, 19)]
    finger_groups = [f"bone_{i}" for i in list(range(31, 52)) + list(range(66, 87))]
    static_mesh = {}
    for obj in meshes:
        weights = mesh_weights(obj)
        ev = evaluated_mesh_vertices(obj)
        static_mesh[obj.name] = {
            "feet": {g: weighted_stats(obj, ev, weights, g) for g in lower_groups},
            "fingers": {g: weighted_stats(obj, ev, weights, g) for g in finger_groups},
        }

    old_frame, old_subframe = scene.frame_current, scene.frame_subframe
    source_data = {"left": [], "right": []}
    target_data = {"left": [], "right": []}
    target_root_height = []
    target_foot_surface = {"left": [], "right": []}
    try:
        for frame in range(61):
            scene.frame_set(frame)
            direct.evaluate_source_curves(control, curves, frame)
            source_pose = direct.pose_matrices_from_basis(control)
            bpy.context.view_layer.update()
            dg = bpy.context.evaluated_depsgraph_get()
            target_ev = target.evaluated_get(dg)
            for side, source_foot, target_ankle, target_toe, root in (
                    ("left", "FootL", "bone_9", "bone_12", "bone_19"),
                    ("right", "FootR", "bone_15", "bone_18", "bone_19")):
                hip_name = "Hip"
                source_hip = world_points(control, source_pose, hip_name)
                source_ankle = world_points(control, source_pose, source_foot)
                source_toe = world_points(control, source_pose, source_foot, True)
                target_ankle_world = target.matrix_world @ target_ev.pose.bones[target_ankle].head
                target_toe_world = target.matrix_world @ target_ev.pose.bones[target_toe].tail
                target_root_world = target.matrix_world @ target_ev.pose.bones[root].head
                source_data[side].append({"hip": list(source_hip), "ankle": list(source_ankle),
                                          "toe": list(source_toe)})
                target_data[side].append({"ankle": list(target_ankle_world),
                                          "toe": list(target_toe_world)})
                foot_group_names = (('bone_9', 'bone_10', 'bone_11') if side == 'left'
                                    else ('bone_15', 'bone_16', 'bone_17'))
                foot_points = []
                for mesh_obj in meshes:
                    mesh_points = evaluated_mesh_vertices(mesh_obj)
                    mesh_weight_rows = mesh_weights(mesh_obj)
                    for point, row in zip(mesh_points, mesh_weight_rows):
                        if any(row.get(g, 0.0) > 0.01 for g in foot_group_names):
                            foot_points.append(point)
                if foot_points:
                    target_foot_surface[side].append({
                        "min_z_world": min(p.z for p in foot_points),
                        "max_z_world": max(p.z for p in foot_points),
                        "centroid_world": list(sum(foot_points, Vector()) / len(foot_points)),
                        "vertex_count": len(foot_points),
                    })
                else:
                    target_foot_surface[side].append({"min_z_world": None,
                        "max_z_world": None, "centroid_world": None, "vertex_count": 0})
                if side == "left":
                    target_root_height.append(float(target_root_world.z))
    finally:
        scene.frame_set(old_frame, subframe=old_subframe)
        bpy.context.view_layer.update()
        data = control.data
        bpy.data.objects.remove(control, do_unlink=True)
        if data.users == 0:
            bpy.data.armatures.remove(data)

    motion = {}
    all_foot_minima = [row["min_z_world"] for side in target_foot_surface
                       for row in target_foot_surface[side] if row["min_z_world"] is not None]
    ground_z = min(all_foot_minima) if all_foot_minima else 0.0
    for side in ("left", "right"):
        s_ankle = [Vector(r["ankle"]) for r in source_data[side]]
        s_toe = [Vector(r["toe"]) for r in source_data[side]]
        s_hip = [Vector(r["hip"]) for r in source_data[side]]
        t_ankle = [Vector(r["ankle"]) for r in target_data[side]]
        t_toe = [Vector(r["toe"]) for r in target_data[side]]
        stance, source_speed, source_height = stance_candidates(s_ankle, up_axis=2)
        target_stance_speed = speed(t_ankle)
        centroids = [Vector(row["centroid_world"]) for row in target_foot_surface[side]]
        centroid_speed = speed(centroids)
        stance_clearance = [target_foot_surface[side][i]["min_z_world"] - ground_z
                            for i in stance if target_foot_surface[side][i]["min_z_world"] is not None]
        stance_centroid_displacement = []
        if stance:
            planted_ref = centroids[stance[0]]
            stance_centroid_displacement = [(centroids[i] - planted_ref).length for i in stance]
        motion[side] = {
            "source_ankle_height_range": [min(p.z for p in s_ankle), max(p.z for p in s_ankle)],
            "source_ankle_speed_max": max(source_speed),
            "source_stance_candidate_frames": stance,
            "source_stance_candidate_runs": runs(stance),
            "source_stance_speed_max": max((source_speed[i] for i in stance), default=0.0),
            "target_ankle_speed_max": max(target_stance_speed),
            "target_ankle_speed_on_source_stance_max": max(
                (target_stance_speed[i] for i in stance if i < 60), default=0.0),
            "target_foot_surface_speed_on_source_stance_max": max(
                (centroid_speed[i] for i in stance if i < 60), default=0.0),
            "target_foot_surface_stance_sliding_max_from_first_candidate": max(
                stance_centroid_displacement, default=0.0),
            "target_foot_surface_stance_clearance_range_world":
                [min(stance_clearance), max(stance_clearance)] if stance_clearance else None,
            "target_foot_surface_ground_z_world": ground_z,
            "target_foot_surface_clearance_range_world": [
                min(row["min_z_world"] - ground_z for row in target_foot_surface[side]
                    if row["min_z_world"] is not None),
                max(row["min_z_world"] - ground_z for row in target_foot_surface[side]
                    if row["min_z_world"] is not None)],
            "target_ankle_height_range_world": [min(p.z for p in t_ankle), max(p.z for p in t_ankle)],
            "target_toe_height_range_world": [min(p.z for p in t_toe), max(p.z for p in t_toe)],
            "source_hip_height_range": [min(p.z for p in s_hip), max(p.z for p in s_hip)],
            "target_hip_height_range_world": [min(target_root_height), max(target_root_height)],
            "sample_world_trajectories": {str(f): {"source_hip": source_data[side][f]["hip"],
                "source_ankle": source_data[side][f]["ankle"], "source_toe": source_data[side][f]["toe"],
                "target_ankle": target_data[side][f]["ankle"], "target_toe": target_data[side][f]["toe"]}
                for f in (0, 15, 30, 45, 60)},
        }
    report = {"schema": "mg1.walk-contact-hand-audit.v1", "blend": bpy.data.filepath,
              "source_action": action.name, "target_rig": target.name,
              "object_scale": list(direct.object_basis(target).to_scale()),
              "mesh_weight_regions": static_mesh,
              "target_foot_surface_by_frame": target_foot_surface,
              "motion": motion}
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, allow_nan=False)
        f.write("\n")
    print(json.dumps({"output": os.path.abspath(args.output),
                      "target_scale": report["object_scale"],
                      "stance_candidates": {s: motion[s]["source_stance_candidate_runs"]
                                            for s in motion}}))


if __name__ == "__main__":
    main()
