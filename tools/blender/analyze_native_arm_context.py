"""Read-only arm vertex influence and native full-pose context analysis.

All animation is evaluated on disposable candidate/production armatures; the
production rig and its actions are never assigned, keyed, or saved.
"""
import argparse
import gzip
import json
import math
import os
import re
import sys
from collections import defaultdict

import bpy
from mathutils import Matrix, Quaternion, Vector

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from convert_native_owner_actions import (
    converted_matrices,
)

DEFAULT_ARM_BONES = ("bone_22", "bone_23", "bone_24", "bone_25", "bone_26",
                     "bone_27", "bone_29", "bone_30")


def args_parse():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-rig", default="Armature.001")
    parser.add_argument("--target-rig", default="123475520_waluigi.gpl")
    parser.add_argument("--slot-id", default="OBArmature.004")
    parser.add_argument("--arm-bones", nargs="+", default=list(DEFAULT_ARM_BONES))
    parser.add_argument("--compare-dataset", default="tools/blender/reports/native_waluigi_arm_calibration_production_basis.json.gz")
    parser.add_argument("--output", required=True)
    return parser.parse_args(argv)


def action_bag(action, slot):
    bags = [bag for layer in action.layers for strip in layer.strips
            for bag in getattr(strip, "channelbags", []) if bag.slot == slot]
    if len(bags) != 1:
        raise ValueError(f"{action.name}: expected one channelbag for slot {slot.identifier}")
    return bags[0]


def action_sample_times(action, slot):
    bag = action_bag(action, slot)
    keyed = sorted({float(point.co.x) for curve in bag.fcurves
                    if curve.data_path.startswith('pose.bones[')
                    for point in curve.keyframe_points})
    return keyed, sorted(set(keyed + [float(x) for x in action.frame_range]))


def keyed_bones(action, slot):
    bag = action_bag(action, slot)
    output = defaultdict(lambda: {"curves": 0, "keys": 0})
    for curve in bag.fcurves:
        match = re.match(r'pose\.bones\["([^"]+)"\]', curve.data_path)
        if not match:
            continue
        name = match.group(1)
        output[name]["curves"] += 1
        output[name]["keys"] += len(curve.keyframe_points)
    return dict(output)


def source_pose_from_curves(rig, action, slot, time):
    """Evaluate action channels explicitly, then compose armature-space poses.

    Some orphaned Blender 5 action slots do not evaluate on a duplicate ID even
    when their slot handle is assigned. FCurve.evaluate preserves the stored
    subframe curves; hierarchy composition uses the candidate rest matrices.
    """
    bag = action_bag(action, slot)
    values = {}
    for pb in rig.pose.bones:
        if pb.rotation_mode == 'QUATERNION':
            rotation = [1.0, 0.0, 0.0, 0.0]
        elif pb.rotation_mode == 'AXIS_ANGLE':
            rotation = [0.0, 0.0, 1.0, 0.0]
        else:
            rotation = [0.0, 0.0, 0.0]
        values[pb.name] = {"location": [0.0, 0.0, 0.0],
            "rotation_quaternion" if pb.rotation_mode == 'QUATERNION' else
            "rotation_axis_angle" if pb.rotation_mode == 'AXIS_ANGLE' else "rotation_euler": rotation,
            "scale": [1.0, 1.0, 1.0]}
    regex = re.compile(r'pose\.bones\["([^"]+)"\]\.([a-zA-Z_]+)')
    for curve in bag.fcurves:
        match = regex.fullmatch(curve.data_path)
        if not match:
            continue
        bone_name, prop = match.groups()
        if bone_name not in values or prop not in values[bone_name]:
            continue
        channels = values[bone_name][prop]
        if curve.array_index < len(channels):
            channels[curve.array_index] = float(curve.evaluate(float(time)))
    basis_by_name = {}
    for name, channels in values.items():
        mode = rig.pose.bones[name].rotation_mode
        if mode == 'QUATERNION':
            # Blender normalizes animated quaternion channels when it builds
            # the evaluated pose. FCurve.evaluate() returns raw components, so
            # normalize explicitly before composing the source hierarchy.
            rotation = Quaternion(channels["rotation_quaternion"]).normalized()
        elif mode == 'AXIS_ANGLE':
            angle, x, y, z = channels["rotation_axis_angle"]
            axis = Vector((x, y, z))
            rotation = Quaternion(axis.normalized(), angle) if axis.length > 1e-10 else Quaternion()
        else:
            from mathutils import Euler
            rotation = Euler(channels["rotation_euler"], mode).to_quaternion()
        basis_by_name[name] = Matrix.LocRotScale(
            Vector(channels["location"]), rotation, Vector(channels["scale"]))
    pose_by_name = {}
    remaining = set(rig.data.bones.keys())
    while remaining:
        progressed = False
        for name in list(remaining):
            bone = rig.data.bones[name]
            parent = bone.parent
            if parent and parent.name in remaining:
                continue
            if parent:
                pose = (pose_by_name[parent.name] @ parent.matrix_local.inverted_safe() @
                        bone.matrix_local @ basis_by_name[name])
            else:
                pose = bone.matrix_local @ basis_by_name[name]
            pose_by_name[name] = pose
            remaining.remove(name)
            progressed = True
        if not progressed:
            raise RuntimeError("source skeleton hierarchy cannot be composed")
    return pose_by_name, basis_by_name


def armature_meshes(rig):
    return [obj for obj in bpy.data.objects if obj.type == 'MESH' and
            any(mod.type == 'ARMATURE' and mod.object == rig for mod in obj.modifiers)]


def build_vertex_profile(meshes, rig, arm_bones):
    inv_rig = rig.matrix_world.inverted_safe()
    selected, arm_vertex_keys = [], set()
    group_totals = defaultdict(float)
    group_vertex_keys = defaultdict(set)
    per_mesh = {}
    for mesh in meshes:
        group_names = {group.index: group.name for group in mesh.vertex_groups}
        arm_from_mesh = inv_rig @ mesh.matrix_world
        rows = []
        for vertex in mesh.data.vertices:
            assignments = [(group_names[a.group], float(a.weight))
                           for a in vertex.groups if a.weight > 0 and a.group in group_names]
            arm_weights = [(name, weight) for name, weight in assignments if name in arm_bones]
            if not arm_weights:
                continue
            key = (mesh.name, int(vertex.index))
            arm_vertex_keys.add(key)
            point = arm_from_mesh @ vertex.co
            rows.append({"key": key, "point": point, "influences": assignments,
                         "arm_influences": arm_weights})
            for name, weight in assignments:
                group_totals[name] += weight
                group_vertex_keys[name].add(key)
        per_mesh[mesh.name] = rows
    for rows in per_mesh.values():
        selected.extend(rows)
    profile = []
    for name in sorted(group_vertex_keys):
        profile.append({"bone": name, "vertex_count": len(group_vertex_keys[name]),
            "weight_sum_on_arm_vertices": float(group_totals[name]),
            "arm_vertex_coverage": float(len(group_vertex_keys[name]) / max(1, len(arm_vertex_keys))),
            "direct_arm_bone": name in arm_bones})
    return selected, profile, arm_vertex_keys


def parent_chain(bone):
    rows = []
    parent = bone.parent
    while parent:
        rows.append(parent.name)
        parent = parent.parent
    return rows


def quaternion_delta_degrees(a, b):
    qa, qb = a.to_quaternion().normalized(), b.to_quaternion().normalized()
    dot = min(1.0, abs(float(qa.dot(qb))))
    return math.degrees(2.0 * math.acos(dot))


def main():
    args = args_parse()
    source_original = bpy.data.objects.get(args.candidate_rig)
    target_original = bpy.data.objects.get(args.target_rig)
    if not source_original or source_original.type != 'ARMATURE' or not target_original or target_original.type != 'ARMATURE':
        raise RuntimeError("candidate and production armatures are required")
    if set(source_original.data.bones.keys()) != set(target_original.data.bones.keys()):
        raise RuntimeError("candidate and target skeleton bone sets differ")
    for name in source_original.data.bones.keys():
        a, b = source_original.data.bones[name].parent, target_original.data.bones[name].parent
        if (a.name if a else None) != (b.name if b else None):
            raise RuntimeError("candidate/target hierarchy mismatch at " + name)
    arm_bones = set(args.arm_bones)
    if not arm_bones.issubset(set(target_original.data.bones.keys())):
        raise RuntimeError("one or more profile arm bones are missing")
    meshes = armature_meshes(target_original)
    if not meshes:
        raise RuntimeError("no meshes are bound to the production rig")

    vertex_rows, influence_rows, arm_vertex_keys = build_vertex_profile(meshes, target_original, arm_bones)
    candidates = {row["bone"] for row in influence_rows
                  if target_original.data.bones.get(row["bone"]) is not None}
    hierarchy = set()
    for name in list(candidates | arm_bones):
        hierarchy.update(parent_chain(target_original.data.bones[name]))
    actions = []
    for action in bpy.data.actions:
        if not action.name.startswith("sequence_"):
            continue
        matching = [slot for slot in action.slots if slot.identifier == args.slot_id]
        if len(matching) == 1 and len(action.slots) == 1:
            keys, samples = action_sample_times(action, matching[0])
            actions.append((action, matching[0], keys, samples, keyed_bones(action, matching[0])))
    actions.sort(key=lambda item: item[0].name)
    if len(actions) != 28:
        raise RuntimeError(f"expected the known 28-action candidate family; found {len(actions)}")

    source = target = None
    C = target_original.matrix_world.inverted_safe() @ source_original.matrix_world
    old_dataset = {}
    if args.compare_dataset and os.path.exists(args.compare_dataset):
        with gzip.open(args.compare_dataset, "rt", encoding="utf-8") as stream:
            prior_data = json.load(stream)
        old_dataset = {row["action"]: {float(sample["time"]): sample for sample in row["samples"]}
                       for row in prior_data["actions"]}
    all_dataset_pose_errors = []
    global_dataset_worst = None
    per_bone = {name: {"max_angular_displacement_degrees": 0.0,
        "max_global_pose_angular_displacement_degrees": 0.0,
        "max_local_translation_displacement_world": 0.0,
        "max_translation_displacement_world": 0.0, "weighted_delta_sq_sum": 0.0,
        "weighted_delta_weight_sum": 0.0, "max_weighted_vertex_displacement_world": 0.0,
        "animated_actions": set(), "keyed_actions": set(), "sample_count": 0}
        for name in target_original.data.bones.keys()}
    per_action = []
    try:
        for action, slot, keys, samples, keyed in actions:
            initial_pose = None
            initial_basis = None
            dataset_pose_error = 0.0
            dataset_pose_compared = 0
            action_dataset_worst = None
            action_stats = {name: {"max_angle_deg": 0.0, "max_global_angle_deg": 0.0,
                "max_local_translation_world": 0.0, "max_translation_world": 0.0,
                "weighted_delta_sq_sum": 0.0, "weighted_delta_weight_sum": 0.0,
                "max_weighted_vertex_displacement_world": 0.0} for name in candidates}
            for name in keyed:
                if name in per_bone:
                    per_bone[name]["keyed_actions"].add(action.name)
            for time in samples:
                source_pose, source_basis = source_pose_from_curves(source_original, action, slot, time)
                production_pose = converted_matrices(source_original, target_original, source_pose, C)
                if initial_pose is None:
                    initial_pose = {name: matrix.copy() for name, matrix in production_pose.items()}
                    initial_basis = {name: matrix.copy() for name, matrix in source_basis.items()}
                pose_matrices = production_pose
                prior_sample = old_dataset.get(action.name, {}).get(float(time))
                if prior_sample:
                    for name in args.arm_bones:
                        prior_matrix = prior_sample["bones"][name]["armature_pose"]
                        error = max(abs(float(pose_matrices[name][r][c]) - float(prior_matrix[r][c]))
                                    for r in range(4) for c in range(4))
                        if error > dataset_pose_error:
                            dataset_pose_error = error
                            action_dataset_worst = {"bone": name, "time": float(time), "max_matrix_error": error}
                            if global_dataset_worst is None or error > global_dataset_worst["max_matrix_error"]:
                                global_dataset_worst = {"action": action.name, **action_dataset_worst}
                    dataset_pose_compared += 1
                delta_matrices = {name: pose_matrices[name] @ target_original.data.bones[name].matrix_local.inverted_safe()
                                  for name in candidates}
                for name in target_original.data.bones.keys():
                    rest = target_original.data.bones[name].matrix_local
                    pose = pose_matrices[name]
                    initial = initial_pose[name]
                    angle = quaternion_delta_degrees(initial, pose)
                    translation = float((target_original.matrix_world.to_3x3() @
                                         (pose.translation - initial.translation)).length)
                    local_angle = quaternion_delta_degrees(initial_basis[name], source_basis[name])
                    local_translation = float((source_original.matrix_world.to_3x3() @
                        (source_basis[name].translation - initial_basis[name].translation)).length)
                    row = per_bone[name]
                    row["max_angular_displacement_degrees"] = max(row["max_angular_displacement_degrees"], local_angle)
                    row["max_global_pose_angular_displacement_degrees"] = max(row["max_global_pose_angular_displacement_degrees"], angle)
                    row["max_local_translation_displacement_world"] = max(row["max_local_translation_displacement_world"], local_translation)
                    row["max_translation_displacement_world"] = max(row["max_translation_displacement_world"], translation)
                    row["sample_count"] += 1
                    if name in action_stats:
                        action_stats[name]["max_angle_deg"] = max(action_stats[name]["max_angle_deg"], local_angle)
                        action_stats[name]["max_global_angle_deg"] = max(action_stats[name]["max_global_angle_deg"], angle)
                        action_stats[name]["max_local_translation_world"] = max(action_stats[name]["max_local_translation_world"], local_translation)
                        action_stats[name]["max_translation_world"] = max(action_stats[name]["max_translation_world"], translation)
                    if local_angle > 0.1 or local_translation > 0.01:
                        row["animated_actions"].add(action.name)
                for vertex in vertex_rows:
                    point = vertex["point"]
                    for name, weight in vertex["influences"]:
                        if name not in delta_matrices:
                            continue
                        moved = delta_matrices[name] @ point
                        displacement = target_original.matrix_world.to_3x3() @ (moved - point)
                        weighted = displacement * weight
                        magnitude = float(weighted.length)
                        bstat = per_bone[name]
                        astat = action_stats[name]
                        bstat["weighted_delta_sq_sum"] += magnitude * magnitude
                        bstat["weighted_delta_weight_sum"] += 1.0
                        bstat["max_weighted_vertex_displacement_world"] = max(bstat["max_weighted_vertex_displacement_world"], magnitude)
                        astat["weighted_delta_sq_sum"] += magnitude * magnitude
                        astat["weighted_delta_weight_sum"] += 1.0
                        astat["max_weighted_vertex_displacement_world"] = max(astat["max_weighted_vertex_displacement_world"], magnitude)
            per_action.append({"action": action.name, "keyed_bone_count": len(keyed),
                "sample_count": len(samples), "max_angular_displacement_by_bone": {
                    name: round(action_stats[name]["max_angle_deg"], 6) for name in sorted(candidates)},
                "max_global_pose_angular_displacement_by_bone": {
                    name: round(action_stats[name]["max_global_angle_deg"], 6) for name in sorted(candidates)},
                "prior_dataset_pose_matrix_error": dataset_pose_error,
                "prior_dataset_worst_sample": action_dataset_worst,
                "prior_dataset_compared_samples": dataset_pose_compared,
                "weighted_deformation_rms_world_by_bone": {
                    name: math.sqrt(v["weighted_delta_sq_sum"] / v["weighted_delta_weight_sum"])
                         if v["weighted_delta_weight_sum"] else 0.0 for name, v in action_stats.items()}})
            if dataset_pose_compared:
                all_dataset_pose_errors.append(dataset_pose_error)

        profile_by_name = {row["bone"]: row for row in influence_rows}
        bone_rows = []
        for name, data in target_original.data.bones.items():
            stat = per_bone[name]
            influence = profile_by_name.get(name, {"vertex_count": 0, "weight_sum_on_arm_vertices": 0.0,
                "arm_vertex_coverage": 0.0, "direct_arm_bone": False})
            rms = (math.sqrt(stat["weighted_delta_sq_sum"] / stat["weighted_delta_weight_sum"])
                   if stat["weighted_delta_weight_sum"] else 0.0)
            bone_rows.append({"bone": name, "parent": data.parent.name if data.parent else None,
                "children": [child.name for child in data.children],
                "hierarchy_ancestors": parent_chain(data),
                "is_profile_arm_bone": name in arm_bones,
                "is_hierarchy_dependency": name in hierarchy,
                "co_influences_arm_vertices": influence["vertex_count"] > 0,
                "co_influenced_vertex_count": influence["vertex_count"],
                "weight_sum_on_arm_vertices": influence["weight_sum_on_arm_vertices"],
                "arm_vertex_coverage": influence["arm_vertex_coverage"],
                "keyed_action_count": len(stat["keyed_actions"]),
                "keyed_actions": sorted(stat["keyed_actions"]),
                "animated_action_count": len(stat["animated_actions"]),
                "max_angular_displacement_degrees": stat["max_angular_displacement_degrees"],
                "max_global_pose_angular_displacement_degrees": stat["max_global_pose_angular_displacement_degrees"],
                "max_local_translation_displacement_world": stat["max_local_translation_displacement_world"],
                "max_translation_displacement_world": stat["max_translation_displacement_world"],
                "weighted_deformation_rms_world": rms,
                "max_weighted_vertex_displacement_world": stat["max_weighted_vertex_displacement_world"],
                "rank_score": rms * math.sqrt(max(1.0, influence["vertex_count"]))})
        bone_rows.sort(key=lambda row: (-row["rank_score"], -row["weight_sum_on_arm_vertices"], row["bone"]))
        report = {"schema": "mg1.native-arm-context-analysis.v1",
            "source_rig": source_original.name, "target_rig": target_original.name,
            "slot_id": args.slot_id, "actions_analyzed": len(actions),
            "previous_dataset_pose_comparison": {"dataset": args.compare_dataset,
                "compared_action_sample_count": sum(row["prior_dataset_compared_samples"] for row in per_action),
                "max_matrix_error": max(all_dataset_pose_errors, default=None),
                "worst_action_sample": global_dataset_worst},
            "action_names": [a[0].name for a in actions],
            "arm_bones": sorted(arm_bones), "affected_arm_vertex_count": len(arm_vertex_keys),
            "meshes": [mesh.name for mesh in meshes],
            "object_scale_unchanged": [float(v) for v in target_original.scale],
            "weighted_group_influence": bone_rows,
            "ranked_context_bones": [row["bone"] for row in bone_rows
                if row["bone"] not in arm_bones and (row["co_influences_arm_vertices"] or row["is_hierarchy_dependency"])],
            "per_action": per_action,
            "deformation_metric": "RMS of individual linear-blend skinning vertex displacement attributable to each bone influence (group weight multiplied by transformed bind-position delta), in world units over exact native action samples"}
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2, allow_nan=False)
            stream.write("\n")
        print("MG1_ARM_CONTEXT=" + json.dumps({"output": args.output,
            "actions": len(actions), "affected_vertices": len(arm_vertex_keys),
            "top_context": report["ranked_context_bones"][:25]}))
    finally:
        pass


if __name__ == "__main__":
    main()
