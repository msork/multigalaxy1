r"""Rebuild arm calibration rows only after candidate-to-target conversion.

This is read-only with respect to the input .blend. It uses disposable rig
duplicates, writes a gzip JSON dataset and a compact validation summary, then
removes its temporary objects/actions. Blender 5.x layered action slots are
handled explicitly.

Example:
  blender --background Waluigi_Sluggers_2368_autosave.blend --python
  tools/blender/build_converted_native_arm_dataset.py -- --output dataset.json.gz
"""
import argparse
import gzip
import json
import math
import os
import sys
import traceback
from pathlib import Path
from mathutils import Matrix, Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from convert_native_owner_actions import (
    ARM_BONES as DEFAULT_BONES,
    basis_from_pose,
    converted_matrices,
    duplicate_rig,
    evaluated_bone_matrices,
    joint_gaps,
    make_action,
    matrix_error,
    set_frame,
)

DEFAULT_HOLDOUTS = ("sequence_11.003", "sequence_15.003", "sequence_20.001",
                    "sequence_24", "sequence_26")
DEFAULT_LANDMARKS = {
    "shoulder": ("bone_22", "head"),
    "main_elbow": ("bone_26", "head"),
    "overlap_elbow": ("bone_29", "head"),
    "main_wrist": ("bone_27", "tail"),
    "glove_wrist": ("bone_30", "head"),
    "glove_tip": ("bone_30", "tail"),
}
DEFAULT_RELATIVE_PAIRS = (("bone_23", "bone_25"), ("bone_26", "bone_29"),
                          ("bone_27", "bone_30"))
DEFAULT_CONTINUITY = {
    "shoulder": ("bone_21", "tail", "bone_22", "head"),
    "elbow_main": ("bone_24", "tail", "bone_26", "head"),
    "elbow_overlap": ("bone_25", "tail", "bone_29", "head"),
    "wrist_glove": ("bone_27", "tail", "bone_30", "head"),
}


def mat_rows(m):
    return [[float(m[r][c]) for c in range(4)] for r in range(4)]


def quat_values(q):
    q = q.normalized()
    return [float(v) for v in (q.w, q.x, q.y, q.z)]


def rest_signature(armature):
    import hashlib
    payload = []
    for b in sorted(armature.bones, key=lambda x: x.name):
        payload.append((b.name, b.parent.name if b.parent else None,
                        tuple(round(float(v), 7) for row in b.matrix_local for v in row),
                        round(float(b.length), 7)))
    return hashlib.sha256(repr(payload).encode("utf-8")).hexdigest()


def action_bag(action, slot):
    found = []
    for layer in action.layers:
        for strip in layer.strips:
            for bag in getattr(strip, "channelbags", []):
                if bag.slot == slot:
                    found.append((layer, strip, bag))
    if len(found) != 1:
        raise ValueError(f"expected exactly one channelbag for slot {slot.identifier}; found {len(found)}")
    return found[0]


def arm_curve_times(action, slot, bone_names):
    _, _, bag = action_bag(action, slot)
    prefixes = tuple(f'pose.bones["{name}"]' for name in bone_names)
    times = sorted({float(k.co.x) for fc in bag.fcurves if fc.data_path.startswith(prefixes)
                    for k in fc.keyframe_points})
    if not times:
        return [], []
    endpoints = [float(v) for v in action.frame_range]
    return times, sorted(set(times + endpoints))


def reset_pose(rig):
    rig.animation_data_create()
    rig.animation_data.action = None
    for pb in rig.pose.bones:
        pb.matrix_basis = Matrix.Identity(4)
        pb.rotation_mode = 'QUATERNION'
    bpy.context.view_layer.update()


def duplicate_bound_meshes(meshes, rig, prefix):
    copies = []
    for src in meshes:
        dup = src.copy()
        dup.data = src.data.copy()
        dup.name = prefix + src.name
        dup.data.name = prefix + src.data.name
        bpy.context.scene.collection.objects.link(dup)
        world = src.matrix_world.copy()
        dup.parent = rig
        dup.matrix_world = world
        for mod in dup.modifiers:
            if mod.type == 'ARMATURE':
                mod.object = rig
        copies.append(dup)
    return copies


def make_candidate_comparison_meshes(target_meshes, source_rig, inverse_c):
    copies = []
    for target_mesh in target_meshes:
        dup = target_mesh.copy()
        dup.data = target_mesh.data.copy()
        dup.name = "_MG1_CandidateFrame_" + target_mesh.name
        dup.data.name = dup.name + "_Data"
        dup.data.transform(inverse_c, shape_keys=True)
        bpy.context.scene.collection.objects.link(dup)
        dup.parent = source_rig
        dup.matrix_world = source_rig.matrix_world.copy()
        for mod in dup.modifiers:
            if mod.type == 'ARMATURE':
                mod.object = source_rig
        copies.append(dup)
    return copies


def eval_vertices(meshes, depsgraph):
    output = {}
    for obj in meshes:
        ev = obj.evaluated_get(depsgraph)
        mesh = ev.to_mesh()
        try:
            output[obj.name] = [ev.matrix_world @ v.co for v in mesh.vertices]
        finally:
            ev.to_mesh_clear()
    return output


def vertex_error(source, target):
    if len(source) != len(target):
        raise ValueError("source and production comparison mesh counts differ")
    worst = 0.0
    for a, b in zip(source.items(), target.items()):
        if len(a[1]) != len(b[1]):
            raise ValueError(f"comparison mesh vertex counts differ: {a[0]} / {b[0]}")
        for va, vb in zip(a[1], b[1]):
            worst = max(worst, float((va - vb).length))
    return worst


def centroid_motion_error(src, dst, src_base, dst_base, bones):
    errors = {}
    for name in bones:
        vals = []
        for (_, src_rows), (_, dst_rows), (_, src_base_rows), (_, dst_base_rows) in zip(
                src.items(), dst.items(), src_base.items(), dst_base.items()):
            a, b = src_rows.get(name), dst_rows.get(name)
            ba, bb = src_base_rows.get(name), dst_base_rows.get(name)
            if not a or not b or not ba or not bb:
                continue
            if any(row["weighted_centroid_world"] is None for row in (a, b, ba, bb)):
                continue
            da = Vector(a["weighted_centroid_world"]) - Vector(ba["weighted_centroid_world"])
            db = Vector(b["weighted_centroid_world"]) - Vector(bb["weighted_centroid_world"])
            vals.append(float((da - db).length))
        errors[name] = max(vals, default=0.0)
    return errors


def centroid_pairs(a, b, bones):
    errors = {}
    for (mesh_name, rows), (other_name, other) in zip(a.items(), b.items()):
        for bone in bones:
            ca = rows.get(bone, {}).get("weighted_centroid_world")
            cb = other.get(bone, {}).get("weighted_centroid_world")
            if ca is not None and cb is not None:
                errors.setdefault(bone, []).append(float((Vector(ca) - Vector(cb)).length))
    return {bone: max(values, default=0.0) for bone, values in errors.items()}


def continuity_values(rig, pairs):
    out = {}
    for label, (a, endpoint_a, b, endpoint_b) in pairs.items():
        if not rig.pose.bones.get(a) or not rig.pose.bones.get(b):
            continue
        va = rig.matrix_world @ getattr(rig.pose.bones[a], endpoint_a)
        vb = rig.matrix_world @ getattr(rig.pose.bones[b], endpoint_b)
        out[label] = float((va - vb).length)
    return out


def mesh_centroids(meshes, depsgraph, bones):
    rows = {}
    for obj in meshes:
        mesh_label = obj.name.removeprefix("_MG1_Dataset_Target_")
        group_names = {g.index: g.name for g in obj.vertex_groups}
        sums = {name: Vector((0.0, 0.0, 0.0)) for name in bones}
        weight_sums = {name: 0.0 for name in bones}
        counts = {name: 0 for name in bones}
        evaluated = obj.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh()
        try:
            inv_armature = obj.find_armature().matrix_world.inverted_safe()
            for index, vertex in enumerate(mesh.vertices):
                world = evaluated.matrix_world @ vertex.co
                armature = inv_armature @ world
                for assignment in obj.data.vertices[index].groups:
                    name = group_names.get(assignment.group)
                    if name in sums and assignment.weight > 0:
                        sums[name] += world * assignment.weight
                        weight_sums[name] += assignment.weight
                        counts[name] += 1
        finally:
            evaluated.to_mesh_clear()
        rows[mesh_label] = {}
        for name in bones:
            centroid_world = sums[name] / weight_sums[name] if weight_sums[name] else None
            centroid_armature = (inv_armature @ centroid_world) if centroid_world is not None else None
            rows[mesh_label][name] = {
                "weighted_vertex_count": counts[name],
                "weighted_centroid_world": [float(v) for v in centroid_world] if centroid_world is not None else None,
                "weighted_centroid_armature": [float(v) for v in centroid_armature] if centroid_armature is not None else None,
            }
    return rows


def extract_pose(target, bones, landmarks, relative_pairs, centroids):
    pose_rows = {}
    for name in bones:
        pb = target.pose.bones[name]
        rest = target.data.bones[name].matrix_local
        delta = rest.inverted_safe() @ pb.matrix
        pose_rows[name] = {
            "local_basis": mat_rows(pb.matrix_basis),
            "armature_pose": mat_rows(pb.matrix),
            "armature_pose_rotation_quaternion": quat_values(pb.matrix.to_quaternion()),
            "world_pose": mat_rows(target.matrix_world @ pb.matrix),
            "world_pose_rotation_quaternion": quat_values((target.matrix_world @ pb.matrix).to_quaternion()),
            "rest_relative_se3": {"matrix": mat_rows(delta),
                "translation": [float(v) for v in delta.translation],
                "rotation_quaternion": quat_values(delta.to_quaternion()),
                "scale": [float(v) for v in delta.to_scale()]},
            "head_armature": [float(v) for v in pb.head],
            "tail_armature": [float(v) for v in pb.tail],
        }
    landmark_rows = {}
    for label, (bone, endpoint) in landmarks.items():
        if bone not in pose_rows:
            continue
        point = getattr(target.pose.bones[bone], endpoint)
        landmark_rows[label] = {"bone": bone, "endpoint": endpoint,
            "armature": [float(v) for v in point],
            "world": [float(v) for v in target.matrix_world @ point]}
    relative_rows = {}
    for a, b in relative_pairs:
        if a not in pose_rows or b not in pose_rows:
            continue
        rel = target.pose.bones[a].matrix.inverted_safe() @ target.pose.bones[b].matrix
        relative_rows[f"{a}__{b}"] = {"matrix": mat_rows(rel),
            "rotation_quaternion": quat_values(rel.to_quaternion()),
            "translation": [float(v) for v in rel.translation]}
    return {"bones": pose_rows, "branch_landmarks": landmark_rows,
            "relative_pose": relative_rows, "weighted_mesh_centroids": centroids}


def static_mesh_weights(meshes, rig, bones):
    output = {}
    inv_rig = rig.matrix_world.inverted_safe()
    for mesh in meshes:
        group_names = {g.index: g.name for g in mesh.vertex_groups if g.name in bones}
        arm_from_mesh = inv_rig @ mesh.matrix_world
        by_bone = {name: [] for name in bones}
        for vertex in mesh.data.vertices:
            for assignment in vertex.groups:
                name = group_names.get(assignment.group)
                if name and assignment.weight > 0:
                    by_bone[name].append({"vertex_index": int(vertex.index),
                        "weight": float(assignment.weight),
                        "position_armature": [float(v) for v in arm_from_mesh @ vertex.co]})
        output[mesh.name.removeprefix("_MG1_Dataset_Target_")] = by_bone
    return output


def args_parse():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-rig", default="Armature.001")
    parser.add_argument("--target-rig", default="123475520_waluigi.gpl")
    parser.add_argument("--mesh", action="append", dest="meshes")
    parser.add_argument("--bones", nargs="+", default=list(DEFAULT_BONES))
    parser.add_argument("--slot-id", default="OBArmature.004")
    parser.add_argument("--anchor-action", default="sequence_10.003")
    parser.add_argument("--holdout", action="append", dest="holdouts")
    parser.add_argument("--output", required=True, help="gzip JSON converted dataset path")
    parser.add_argument("--validation-output", required=True, help="compact validation JSON path")
    parser.add_argument("--max-matrix-error", type=float, default=1e-4)
    parser.add_argument("--max-vertex-error", type=float, default=1e-3)
    parser.add_argument("--max-centroid-error", type=float, default=1e-3)
    parser.add_argument("--max-continuity-gap", type=float, default=1e-3)
    return parser.parse_args(argv)


def main():
    args = args_parse()
    import bpy
    bones = list(args.bones)
    holdouts = set(args.holdouts or DEFAULT_HOLDOUTS)
    landmarks = DEFAULT_LANDMARKS
    relative_pairs = DEFAULT_RELATIVE_PAIRS
    continuity_pairs = DEFAULT_CONTINUITY
    source_original = bpy.data.objects.get(args.source_rig)
    target_original = bpy.data.objects.get(args.target_rig)
    if not source_original or source_original.type != 'ARMATURE':
        raise RuntimeError("candidate source armature missing: " + args.source_rig)
    if not target_original or target_original.type != 'ARMATURE':
        raise RuntimeError("production target armature missing: " + args.target_rig)
    if set(source_original.data.bones.keys()) != set(target_original.data.bones.keys()):
        raise RuntimeError("source and target bone names differ; provide a verified matching skeleton")
    for name in bones:
        if not source_original.data.bones.get(name) or not target_original.data.bones.get(name):
            raise RuntimeError("required calibration bone missing: " + name)
    for name in source_original.data.bones.keys():
        a = source_original.data.bones[name].parent
        b = target_original.data.bones[name].parent
        if (a.name if a else None) != (b.name if b else None):
            raise RuntimeError("source/target hierarchy mismatch at " + name)

    mesh_names = args.meshes or [o.name for o in bpy.data.objects if o.type == 'MESH' and
        any(m.type == 'ARMATURE' and m.object == target_original for m in o.modifiers)]
    target_mesh_sources = [bpy.data.objects.get(name) for name in mesh_names]
    if not target_mesh_sources or any(o is None or o.type != 'MESH' for o in target_mesh_sources):
        raise RuntimeError("one or more production mesh sources are missing")
    # Keep meshes that contain at least one requested arm group; include them in the
    # converted dataset so centroids are always computed from production geometry.
    target_mesh_sources = [o for o in target_mesh_sources if any(
        g.name in bones and any(a.group == g.index and a.weight > 0 for v in o.data.vertices for a in v.groups)
        for g in o.vertex_groups)]
    if not target_mesh_sources:
        raise RuntimeError("no production mesh has weights for the requested bones")

    actions = []
    inventory = []
    for action in bpy.data.actions:
        if not action.name.startswith("sequence_"):
            continue
        slots = list(action.slots)
        matching = [slot for slot in slots if slot.identifier == args.slot_id]
        if len(slots) == 1 and len(matching) == 1:
            actions.append((action, matching[0]))
        elif matching:
            inventory.append({"action": action.name, "status": "excluded_ambiguous_slots",
                              "slot_ids": [s.identifier for s in slots]})
    actions.sort(key=lambda pair: pair[0].name)
    if not any(action.name == args.anchor_action for action, _ in actions):
        raise RuntimeError("candidate-owner anchor action missing from requested slot: " + args.anchor_action)
    missing_holdouts = sorted(holdouts - {a.name for a, _ in actions})
    if missing_holdouts:
        raise RuntimeError("requested holdout actions are absent from candidate slot: " + ", ".join(missing_holdouts))

    scene = bpy.context.scene
    old_frame, old_subframe = scene.frame_current, scene.frame_subframe
    old_object_names = {obj.name for obj in bpy.data.objects}
    old_action_names = {action.name for action in bpy.data.actions}
    created_objects, created_actions = [], []
    dataset_rows, action_reports = [], []
    source = target = None
    target_meshes = source_meshes = []
    C = None
    try:
        source = duplicate_rig(source_original, "_MG1_Dataset_Candidate_Source")
        target = duplicate_rig(target_original, "_MG1_Dataset_Converted_Production")
        created_objects.extend([source, target])
        C = target.matrix_world.inverted_safe() @ source.matrix_world
        inverse_c = C.inverted_safe()
        target_meshes = duplicate_bound_meshes(target_mesh_sources, target, "_MG1_Dataset_Target_")
        source_meshes = make_candidate_comparison_meshes(target_mesh_sources, source, inverse_c)
        created_objects.extend(target_meshes + source_meshes)
        baseline_gaps = continuity_values(target, continuity_pairs)
        rest_mesh_weights = static_mesh_weights(target_meshes, target, bones)
        rest_data = {name: {
            "parent": target.data.bones[name].parent.name if target.data.bones[name].parent else None,
            "children": [c.name for c in target.data.bones[name].children],
            "head": [float(v) for v in target.data.bones[name].head_local],
            "tail": [float(v) for v in target.data.bones[name].tail_local],
            "length": float(target.data.bones[name].length),
            "rest_matrix": mat_rows(target.data.bones[name].matrix_local),
            "track_id": target.data.bones[name].get("TrackId", target.data.bones[name].get("track_id")),
        } for name in bones}
        for action, slot in actions:
            record = {"action": action.name, "slot_id": slot.identifier,
                      "split": "holdout" if action.name in holdouts else "train",
                      "provenance_confidence": "visually_validated_anchor" if action.name == args.anchor_action else "candidate_family_inference"}
            try:
                exact_keys, sample_times = arm_curve_times(action, slot, bones)
                if not exact_keys:
                    raise ValueError("no keyed channels for requested arm bones")
                source.animation_data_create()
                target.animation_data_create()
                source.animation_data.action = action
                source.animation_data.action_slot = slot
                reset_pose(target)
                basis_samples = []
                pre_matrix_err = 0.0
                pre_tr_err = 0.0
                for time in sample_times:
                    set_frame(scene, time)
                    src_pose = evaluated_bone_matrices(source)
                    converted = converted_matrices(source, target, src_pose, C)
                    bases = basis_from_pose(target, converted)
                    for name, basis in bases.items():
                        pb = target.pose.bones[name]
                        pb.rotation_mode = 'QUATERNION'
                        pb.matrix_basis = basis
                    bpy.context.view_layer.update()
                    actual = evaluated_bone_matrices(target)
                    pre_matrix_err = max(pre_matrix_err, max((matrix_error(actual[n], converted[n]) for n in converted), default=0.0))
                    pre_tr_err = max(pre_tr_err, max((matrix_error(bases[n], Matrix.LocRotScale(*bases[n].decompose())) for n in bases), default=0.0))
                    basis_samples.append((time, bases))
                converted_action = make_action(target, "_MG1_Converted_" + action.name, basis_samples)
                created_actions.append(converted_action)
                target.animation_data.action = converted_action
                target.animation_data.action_slot = converted_action.slots[0]

                src_base = dst_base = None
                rows = []
                max_pose_err = max_written_err = max_vertex_err = max_centroid_motion_err = 0.0
                max_abs_centroid_err = {name: 0.0 for name in bones}
                gap_series = {label: [] for label in baseline_gaps}
                for time in sample_times:
                    set_frame(scene, time)
                    src_pose = evaluated_bone_matrices(source)
                    expected = converted_matrices(source, target, src_pose, C)
                    target_pose = evaluated_bone_matrices(target)
                    max_pose_err = max(max_pose_err, max((matrix_error(target_pose[n], expected[n]) for n in expected), default=0.0))
                    expected_bases = basis_samples[sample_times.index(time)][1]
                    basis_err = max((matrix_error(target.pose.bones[n].matrix_basis, expected_bases[n])
                                     for n in expected_bases), default=0.0)
                    max_written_err = max(max_written_err, basis_err)
                    depsgraph = bpy.context.evaluated_depsgraph_get()
                    source_centroids = mesh_centroids(source_meshes, depsgraph, bones)
                    target_centroids = mesh_centroids(target_meshes, depsgraph, bones)
                    if src_base is None:
                        src_base, dst_base = source_centroids, target_centroids
                    motion_errors = centroid_motion_error(source_centroids, target_centroids, src_base, dst_base, bones)
                    max_centroid_motion_err = max(max_centroid_motion_err, max(motion_errors.values(), default=0.0))
                    absolute_centroid_errors = centroid_pairs(source_centroids, target_centroids, bones)
                    for name, error in absolute_centroid_errors.items():
                        max_abs_centroid_err[name] = max(max_abs_centroid_err[name], error)
                    source_vertices = eval_vertices(source_meshes, depsgraph)
                    target_vertices = eval_vertices(target_meshes, depsgraph)
                    max_vertex_err = max(max_vertex_err, vertex_error(source_vertices, target_vertices))
                    gaps = continuity_values(target, continuity_pairs)
                    for label, value in gaps.items():
                        gap_series[label].append(value)
                    feature = extract_pose(target, bones, landmarks, relative_pairs, target_centroids)
                    rows.append({"time": float(time), **feature,
                        "conversion_validation": {"max_pose_matrix_error": max_pose_err,
                            "max_mesh_vertex_error_world": max_vertex_err,
                            "weighted_centroid_motion_error_world": max(motion_errors.values(), default=0.0),
                            "target_joint_gaps_world": gaps}})
                gap_maxima = {label: max(values, default=0.0) for label, values in gap_series.items()}
                gap_deviation = {label: max((abs(value - baseline_gaps[label]) for value in values), default=0.0)
                                 for label, values in gap_series.items()}
                failed = []
                if max_pose_err > args.max_matrix_error or max_written_err > args.max_matrix_error:
                    failed.append("pose_matrix_tolerance")
                if max_vertex_err > args.max_vertex_error:
                    failed.append("mesh_vertex_tolerance")
                if max_centroid_motion_err > args.max_centroid_error:
                    failed.append("weighted_centroid_tolerance")
                if max(gap_deviation.values(), default=0.0) > args.max_continuity_gap:
                    failed.append("continuity_gap_variation")
                record.update({"status": "failed_validation" if failed else "converted",
                    "failure_flags": failed,
                    "frame_range": [float(v) for v in action.frame_range],
                    "arm_key_time_count": len(exact_keys), "sample_time_count": len(sample_times),
                    "max_prebake_pose_matrix_error": pre_matrix_err,
                    "max_prebake_trs_decomposition_error": pre_tr_err,
                    "max_pose_matrix_error": max_pose_err,
                    "max_written_action_basis_error": max_written_err,
                    "max_mesh_vertex_error_world": max_vertex_err,
                    "max_weighted_centroid_motion_error_world": max_centroid_motion_err,
                    "max_absolute_centroid_difference_world_by_bone": max_abs_centroid_err,
                    "production_joint_gap_max_world": gap_maxima,
                    "production_joint_gap_deviation_from_rest_world": gap_deviation})
                action_reports.append(record)
                if not failed:
                    dataset_rows.append({"action": action.name,
                        "source_basis": "Armature.001 candidate owner family",
                        "converted_basis": "production Waluigi rest/object basis",
                        "slot_id": slot.identifier, "split": record["split"],
                        "provenance_confidence": record["provenance_confidence"],
                        "frame_range": record["frame_range"],
                        "exact_key_times": exact_keys,
                        "samples": rows})
            except Exception as exc:
                record.update({"status": "failed_conversion", "failure_flags": [str(exc)],
                    "traceback": traceback.format_exc(limit=4)})
                action_reports.append(record)
            finally:
                if target.animation_data:
                    target.animation_data.action = None
                if converted_action := next((a for a in created_actions if a.name == "_MG1_Converted_" + action.name), None):
                    bpy.data.actions.remove(converted_action, do_unlink=True)
                    created_actions.remove(converted_action)
                reset_pose(target)

        report = {
            "schema": "mg1.native-arm-conversion-validation.v1",
            "source_scene": bpy.data.filepath,
            "source_rig": source_original.name,
            "target_rig": target_original.name,
            "slot_id": args.slot_id,
            "anchor_action": args.anchor_action,
            "bones": bones,
            "action_count_candidate": len(actions),
            "converted_action_count": sum(r["status"] == "converted" for r in action_reports),
            "failed_action_count": sum(r["status"] != "converted" for r in action_reports),
            "holdout_actions_requested": sorted(holdouts),
            "holdout_actions_converted": sorted(r["action"] for r in action_reports if r["split"] == "holdout" and r["status"] == "converted"),
            "train_actions_converted": sorted(r["action"] for r in action_reports if r["split"] == "train" and r["status"] == "converted"),
            "total_samples": sum(len(row["samples"]) for row in dataset_rows),
            "train_samples": sum(len(row["samples"]) for row in dataset_rows if row["split"] == "train"),
            "holdout_samples": sum(len(row["samples"]) for row in dataset_rows if row["split"] == "holdout"),
            "max_pose_matrix_error": max((r.get("max_pose_matrix_error", 0.0) for r in action_reports), default=0.0),
            "max_mesh_vertex_error_world": max((r.get("max_mesh_vertex_error_world", 0.0) for r in action_reports), default=0.0),
            "max_weighted_centroid_motion_error_world": max((r.get("max_weighted_centroid_motion_error_world", 0.0) for r in action_reports), default=0.0),
            "thresholds": {"pose_matrix": args.max_matrix_error, "mesh_vertex_world": args.max_vertex_error,
                           "centroid_motion_world": args.max_centroid_error, "continuity_gap_deviation_world": args.max_continuity_gap},
            "conversion": "P_target_i(t)=C@P_candidate_i(t)@inverse(R_candidate_i)@inverse(C)@R_target_i; local basis is solved top-down.",
            "actions": action_reports,
        }
        dataset = {
            "schema": "mg1.native-arm-calibration.production-basis.v1",
            "read_only_source": True,
            "source_rig": source_original.name,
            "production_rig_basis": target_original.name,
            "rig_data": target_original.data.name,
            "rig_rest_signature_sha256": rest_signature(target_original.data),
            "rig_matrix_world": mat_rows(target_original.matrix_world),
            "rig_scale": [float(v) for v in target_original.scale],
            "slot_id": args.slot_id,
            "anchor_action": args.anchor_action,
            "provenance_policy": "source actions were evaluated only on Armature.001 duplicates, converted to production rest basis, then extracted from production-basis duplicates; raw orphaned channels are never emitted as training features",
            "bones": bones,
            "rest_bones": rest_data,
            "weighted_mesh_vertices_rest": rest_mesh_weights,
            "pose_reset_policy": "all temporary source/target pose bases reset to identity before each source/converted action",
            "sample_time_policy": "union of exact source FCurve arm-bone key times (including subframes) and action range endpoints",
            "holdout_actions": sorted(holdouts),
            "actions": dataset_rows,
            "training_action_count": sum(row["split"] == "train" for row in dataset_rows),
            "holdout_action_count": sum(row["split"] == "holdout" for row in dataset_rows),
            "sample_count": sum(len(row["samples"]) for row in dataset_rows),
        }
        os.makedirs(os.path.dirname(os.path.abspath(args.validation_output)), exist_ok=True)
        with open(args.validation_output, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, allow_nan=False)
            f.write("\n")
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        with gzip.open(args.output, "wt", encoding="utf-8", compresslevel=6) as f:
            json.dump(dataset, f, separators=(",", ":"), allow_nan=False)
        print("MG1_CONVERTED_DATASET=" + json.dumps({"output": args.output,
            "validation": args.validation_output, "actions": report["converted_action_count"],
            "failed": report["failed_action_count"], "samples": report["total_samples"],
            "max_pose_matrix_error": report["max_pose_matrix_error"],
            "max_mesh_vertex_error_world": report["max_mesh_vertex_error_world"],
            "max_centroid_error": report["max_weighted_centroid_motion_error_world"]}))
    finally:
        scene.frame_set(old_frame, subframe=old_subframe)
        for action in list(bpy.data.actions):
            if action.name not in old_action_names and action.name.startswith("_MG1_Converted_"):
                bpy.data.actions.remove(action, do_unlink=True)
        for obj in list(bpy.data.objects):
            if obj.name in old_object_names:
                continue
            if obj.type == 'MESH' and obj.data:
                mesh_data = obj.data
                bpy.data.objects.remove(obj, do_unlink=True)
                if mesh_data.users == 0:
                    bpy.data.meshes.remove(mesh_data)
            elif obj.type == 'ARMATURE' and obj.data:
                arm_data = obj.data
                bpy.data.objects.remove(obj, do_unlink=True)
                if arm_data.users == 0:
                    bpy.data.armatures.remove(arm_data)


if __name__ == "__main__":
    import bpy
    main()
