r"""Convert native candidate-owner actions onto a production-basis duplicate.

The source .blend is opened but never saved. Every changed rig, mesh, and
action is a disposable duplicate written to a new output .blend.

Example:
  blender --background Waluigi_Sluggers_2368_autosave.blend --python
  tools/blender/convert_native_owner_actions.py -- --output preview.blend
"""
import argparse
import json
import math
import os
import sys

import bpy
from mathutils import Matrix, Vector

SOURCE_RIG = "Armature.001"
TARGET_RIG = "123475520_waluigi.gpl"
ACTION_NAMES = ("sequence_10.003", "sequence_15.003", "sequence_24")
ARM_BONES = ("bone_22", "bone_23", "bone_24", "bone_25", "bone_26",
             "bone_27", "bone_29", "bone_30")


def matrix_rows(m):
    return [[float(m[r][c]) for c in range(4)] for r in range(4)]


def matrix_error(a, b):
    return max(abs(float(a[r][c]) - float(b[r][c])) for r in range(4) for c in range(4))


def action_bag(action, slot):
    bags = []
    for layer in action.layers:
        for strip in layer.strips:
            for bag in getattr(strip, "channelbags", []):
                if bag.slot == slot:
                    bags.append((layer, strip, bag))
    if len(bags) != 1:
        raise RuntimeError(f"{action.name}: expected one channelbag for {slot.identifier}, found {len(bags)}")
    return bags[0]


def bound_meshes(rig):
    result = []
    for obj in bpy.data.objects:
        if obj.type != 'MESH':
            continue
        if any(m.type == 'ARMATURE' and m.object == rig for m in obj.modifiers):
            result.append(obj)
    return result


def duplicate_rig(source, name):
    dup = source.copy()
    dup.data = source.data.copy()
    dup.name = name
    dup.data.name = name + "_Data"
    bpy.context.scene.collection.objects.link(dup)
    dup.matrix_world = source.matrix_world.copy()
    dup.animation_data_clear()
    for pb in dup.pose.bones:
        pb.location = (0, 0, 0)
        pb.rotation_mode = 'QUATERNION'
        pb.rotation_quaternion = (1, 0, 0, 0)
        pb.scale = (1, 1, 1)
        pb.matrix_basis = Matrix.Identity(4)
    return dup


def duplicate_meshes(sources, target_rig, prefix):
    copies = []
    for source in sources:
        dup = source.copy()
        dup.data = source.data.copy()
        dup.name = prefix + source.name
        dup.data.name = prefix + source.data.name
        bpy.context.scene.collection.objects.link(dup)
        world = source.matrix_world.copy()
        dup.parent = target_rig
        dup.matrix_world = world
        for modifier in dup.modifiers:
            if modifier.type == 'ARMATURE':
                modifier.object = target_rig
        copies.append(dup)
    return copies


def exact_key_times(action, slot):
    _layer, _strip, bag = action_bag(action, slot)
    times = sorted({float(point.co.x) for fc in bag.fcurves for point in fc.keyframe_points})
    if len(times) < 2:
        raise RuntimeError(action.name + " has fewer than two keyed times")
    # Converted local TRS curves are nonlinear combinations of source curves.
    # Midpoints limit interpolation error while retaining all original keys.
    samples = sorted(set(times + [(a + b) * 0.5 for a, b in zip(times, times[1:])]))
    return times, samples


def set_frame(scene, time):
    whole = math.floor(time)
    scene.frame_set(int(whole), subframe=float(time - whole))
    bpy.context.view_layer.update()


def evaluated_bone_matrices(rig):
    ev = rig.evaluated_get(bpy.context.evaluated_depsgraph_get())
    return {pb.name: pb.matrix.copy() for pb in ev.pose.bones}


def converted_matrices(source, target, source_pose, C):
    inv_c = C.inverted_safe()
    out = {}
    for name in target.data.bones.keys():
        if name not in source_pose or name not in source.data.bones:
            continue
        Rs = source.data.bones[name].matrix_local
        Rt = target.data.bones[name].matrix_local
        out[name] = C @ source_pose[name] @ Rs.inverted_safe() @ inv_c @ Rt
    return out


def basis_from_pose(target, pose_matrices):
    basis = {}
    # Blender armature bone order is parent-before-child for imported skeletons;
    # explicit recursive availability check keeps this safe for reordered data.
    remaining = set(pose_matrices)
    while remaining:
        progressed = False
        for name in list(remaining):
            bone = target.data.bones[name]
            parent = bone.parent
            if parent and parent.name in remaining:
                continue
            local_rest = (parent.matrix_local.inverted_safe() @ bone.matrix_local) if parent else bone.matrix_local
            basis[name] = (local_rest.inverted_safe() @ pose_matrices[parent.name].inverted_safe() @ pose_matrices[name]
                           if parent else bone.matrix_local.inverted_safe() @ pose_matrices[name])
            remaining.remove(name)
            progressed = True
        if not progressed:
            raise RuntimeError("target bone hierarchy cannot be traversed")
    return basis


def assign_basis(target, bases):
    for name, matrix in bases.items():
        pb = target.pose.bones[name]
        pb.rotation_mode = 'QUATERNION'
        pb.matrix_basis = matrix
    bpy.context.view_layer.update()


def weighted_centroids(meshes, depsgraph, bone_names):
    sums = {name: Vector((0, 0, 0)) for name in bone_names}
    totals = {name: 0.0 for name in bone_names}
    for obj in meshes:
        group_by_index = {vg.index: vg.name for vg in obj.vertex_groups}
        ev = obj.evaluated_get(depsgraph)
        mesh = ev.to_mesh()
        try:
            for i, vert in enumerate(mesh.vertices):
                pos = ev.matrix_world @ vert.co
                for assignment in obj.data.vertices[i].groups:
                    name = group_by_index.get(assignment.group)
                    if name in sums and assignment.weight > 0:
                        sums[name] += pos * assignment.weight
                        totals[name] += assignment.weight
        finally:
            ev.to_mesh_clear()
    return {n: list(sums[n] / totals[n]) if totals[n] else None for n in bone_names}


def evaluated_world_vertices(meshes, depsgraph):
    result = {}
    for obj in meshes:
        ev = obj.evaluated_get(depsgraph)
        mesh = ev.to_mesh()
        try:
            result[obj.name] = [ev.matrix_world @ v.co for v in mesh.vertices]
        finally:
            ev.to_mesh_clear()
    return result


def max_vertex_difference(a, b):
    if len(a) != len(b):
        raise RuntimeError("source/target validation mesh counts differ")
    errors = []
    for (name_a, verts_a), (name_b, verts_b) in zip(a.items(), b.items()):
        if len(verts_a) != len(verts_b):
            raise RuntimeError(f"source/target validation vertex counts differ: {name_a} / {name_b}")
        errors.extend((x - y).length for x, y in zip(verts_a, verts_b))
    return max(errors, default=0.0)


def joint_gaps(rig):
    world = rig.matrix_world
    p = rig.pose.bones
    pairs = {"shoulder": ("bone_21", "tail", "bone_22", "head"),
             "elbow_main": ("bone_24", "tail", "bone_26", "head"),
             "elbow_overlap": ("bone_25", "tail", "bone_29", "head"),
             "wrist_glove": ("bone_27", "tail", "bone_30", "head")}
    out = {}
    for key, (a, endpoint_a, b, endpoint_b) in pairs.items():
        va = world @ getattr(p[a], endpoint_a)
        vb = world @ getattr(p[b], endpoint_b)
        out[key] = float((va - vb).length)
    return out


def centroid_delta_error(a, b, baseline_a, baseline_b):
    errors = []
    for name in ARM_BONES:
        if a.get(name) is None or b.get(name) is None or baseline_a.get(name) is None or baseline_b.get(name) is None:
            continue
        da = Vector(a[name]) - Vector(baseline_a[name])
        db = Vector(b[name]) - Vector(baseline_b[name])
        errors.append((name, (da - db).length))
    return errors


def make_action(target, name, matrices_by_time):
    action = bpy.data.actions.new(name)
    action.use_fake_user = True
    slot = action.slots.new('OBJECT', target.name)
    layer = action.layers.new("Converted native pose matrices")
    strip = layer.strips.new(type='KEYFRAME')
    bag = strip.channelbag(slot, ensure=True)
    target.animation_data_create()
    target.animation_data.action = action
    target.animation_data.action_slot = slot
    by_path = {}
    for time, bases in matrices_by_time:
        for bone_name, matrix in bases.items():
            loc, quat, scale = matrix.decompose()
            values = (("location", tuple(loc)), ("rotation_quaternion", tuple(quat)), ("scale", tuple(scale)))
            for prop, components in values:
                path = f'pose.bones["{bone_name}"].{prop}'
                for index, value in enumerate(components):
                    fc = by_path.get((path, index))
                    if fc is None:
                        fc = bag.fcurves.new(path, index=index, group_name=bone_name)
                        by_path[(path, index)] = fc
                    point = fc.keyframe_points.insert(float(time), float(value), options={'FAST'})
                    point.interpolation = 'LINEAR'
    for fc in by_path.values():
        fc.update()
    return action


def main():
    argv = sys.argv[sys.argv.index("--") + 1:]
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-rig", default=SOURCE_RIG)
    parser.add_argument("--target-rig", default=TARGET_RIG)
    parser.add_argument("--actions", nargs="+", default=list(ACTION_NAMES))
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    source_original = bpy.data.objects.get(args.source_rig)
    target_original = bpy.data.objects.get(args.target_rig)
    if not source_original or not target_original:
        raise RuntimeError("candidate and production armatures must both exist in the input scene")
    if set(source_original.data.bones.keys()) != set(target_original.data.bones.keys()):
        raise RuntimeError("source and target bone-name sets differ; refusing guessed mapping")
    if any((source_original.data.bones[n].parent.name if source_original.data.bones[n].parent else None) !=
           (target_original.data.bones[n].parent.name if target_original.data.bones[n].parent else None)
           for n in source_original.data.bones.keys()):
        raise RuntimeError("source and target parent hierarchies differ")

    scene = bpy.context.scene
    saved_frame, saved_subframe = scene.frame_current, scene.frame_subframe
    target_meshes = bound_meshes(target_original)
    target_meshes = [m for m in target_meshes if any(
        g.name in ARM_BONES and any(v.groups and any(a.group == g.index and a.weight > 0 for a in v.groups)
                                   for v in m.data.vertices)
        for g in m.vertex_groups)]
    if not target_meshes:
        raise RuntimeError("both owner and production meshes must be armature-bound")
    source = duplicate_rig(source_original, "NativeCandidate_SourceEval")
    target = duplicate_rig(target_original, "NativeConverted_ProductionBasis")
    target_copies = duplicate_meshes(target_meshes, target, "NativeConverted_")
    # Use the same production vertices and weights on both rigs for a strict
    # skinning-equivalence check. C maps candidate armature coordinates to the
    # production armature coordinates; C^-1 maps the comparison mesh back.
    C = target.matrix_world.inverted_safe() @ source.matrix_world
    inv_C = C.inverted_safe()
    source_copies = []
    for target_mesh in target_meshes:
        ref = target_mesh.copy()
        ref.data = target_mesh.data.copy()
        ref.name = "NativeCandidate_Comparison_" + target_mesh.name
        ref.data.name = ref.name + "_Data"
        ref.data.transform(inv_C, shape_keys=True)
        bpy.context.scene.collection.objects.link(ref)
        ref.parent = source
        ref.matrix_world = source.matrix_world.copy()
        for mod in ref.modifiers:
            if mod.type == 'ARMATURE':
                mod.object = source
        source_copies.append(ref)
    action_reports = {}

    for action_name in args.actions:
        action = bpy.data.actions.get(action_name)
        if action is None:
            raise RuntimeError("missing source action: " + action_name)
        slots = list(action.slots)
        source_slot = (source_original.animation_data.action_slot
                       if source_original.animation_data else None)
        slot = next((s for s in slots if source_slot and s.identifier == source_slot.identifier), None)
        if slot is None and len(slots) == 1:
            slot = slots[0]
        if slot is None:
            raise RuntimeError(f"{action_name}: action slot is ambiguous")
        original_keys, sample_times = exact_key_times(action, slot)
        source.animation_data_create()
        source.animation_data.action = action
        source.animation_data.action_slot = slot
        target.animation_data_create()
        target.animation_data.action = None
        for pb in target.pose.bones:
            pb.matrix_basis = Matrix.Identity(4)

        source_times, target_times = [], []
        vertex_errors = []
        gap_errors = []
        target_basis_samples = []
        sample_matrix_errors = []
        matrix_decompose_errors = []
        motion_centroids = []
        source_centroids, target_centroids = {}, {}
        target_scale_extrema = {n: [float('inf'), float('-inf')] for n in target.data.bones.keys()}
        source_pose_heads = {}
        target_pose_heads = {}
        for time in sample_times:
            set_frame(scene, time)
            src_pose = evaluated_bone_matrices(source)
            src_centers = weighted_centroids(source_copies, bpy.context.evaluated_depsgraph_get(), ARM_BONES)
            desired = converted_matrices(source, target, src_pose, C)
            bases = basis_from_pose(target, desired)
            assign_basis(target, bases)
            actual = evaluated_bone_matrices(target)
            frame_error = max((matrix_error(actual[n], desired[n]) for n in desired), default=0.0)
            sample_matrix_errors.append((time, frame_error))
            decomp_error = max((matrix_error(bases[n], Matrix.LocRotScale(*bases[n].decompose())) for n in bases), default=0.0)
            matrix_decompose_errors.append((time, decomp_error))
            for n in target_scale_extrema:
                scale = bases[n].decompose()[2]
                target_scale_extrema[n][0] = min(target_scale_extrema[n][0], *[float(v) for v in scale])
                target_scale_extrema[n][1] = max(target_scale_extrema[n][1], *[float(v) for v in scale])
            tgt_centers = weighted_centroids(target_copies, bpy.context.evaluated_depsgraph_get(), ARM_BONES)
            src_vertices = evaluated_world_vertices(source_copies, bpy.context.evaluated_depsgraph_get())
            tgt_vertices = evaluated_world_vertices(target_copies, bpy.context.evaluated_depsgraph_get())
            vertex_errors.append((time, max_vertex_difference(src_vertices, tgt_vertices)))
            gap_errors.append((time, joint_gaps(source), joint_gaps(target)))
            source_times.append((time, src_centers))
            target_times.append((time, tgt_centers))
            target_basis_samples.append((time, bases))
            if time in (original_keys[0], original_keys[-1]):
                source_pose_heads[time] = {n: list(source.matrix_world @ source.pose.bones[n].head) for n in ARM_BONES}
                target_pose_heads[time] = {n: list(target.matrix_world @ target.pose.bones[n].head) for n in ARM_BONES}
        baseline_src, baseline_tgt = source_times[0][1], target_times[0][1]
        all_centroid_errs = []
        for (time, src_c), (_, tgt_c) in zip(source_times, target_times):
            all_centroid_errs.extend((time, name, err) for name, err in centroid_delta_error(src_c, tgt_c, baseline_src, baseline_tgt))
        converted_name = "NativeConverted_" + action_name
        converted_action = make_action(target, converted_name, target_basis_samples)

        # Re-evaluate the written action (not the pre-keyed basis) to validate actual playback.
        target.animation_data.action = converted_action
        target.animation_data.action_slot = converted_action.slots[0]
        target_written_errors = []
        for time, expected_bases in target_basis_samples:
            set_frame(scene, time)
            actual_bases = {n: target.pose.bones[n].matrix_basis.copy() for n in expected_bases}
            target_written_errors.append((time, max((matrix_error(actual_bases[n], expected_bases[n]) for n in expected_bases), default=0.0)))
        action_reports[action_name] = {
            "source_action_slot": slot.identifier,
            "source_frame_range": [float(v) for v in action.frame_range],
            "original_key_time_count": len(original_keys),
            "original_key_times": original_keys,
            "converted_sample_count": len(sample_times),
            "converted_action": converted_action.name,
            "evaluated_target_pose_max_matrix_error": max((e for _, e in sample_matrix_errors), default=0.0),
            "tr_decomposition_max_matrix_error": max((e for _, e in matrix_decompose_errors), default=0.0),
            "written_action_max_basis_replay_error": max((e for _, e in target_written_errors), default=0.0),
            "max_baseline_corrected_weighted_centroid_motion_error": max((e for _, _, e in all_centroid_errs), default=0.0),
            "max_same_production_mesh_vertex_error_world": max((e for _, e in vertex_errors), default=0.0),
            "max_joint_gap_world_by_rig": {key: {
                "candidate_max": max((src[key] for _, src, _ in gap_errors), default=0.0),
                "production_converted_max": max((tgt[key] for _, _, tgt in gap_errors), default=0.0),
                "max_abs_difference": max((abs(src[key] - tgt[key]) for _, src, tgt in gap_errors), default=0.0),
                "production_converted_range": [min((tgt[key] for _, _, tgt in gap_errors), default=0.0),
                                                max((tgt[key] for _, _, tgt in gap_errors), default=0.0)]}
                for key in (gap_errors[0][1].keys() if gap_errors else [])},
            "max_centroid_motion_error_by_bone": {n: max((e for _, name, e in all_centroid_errs if name == n), default=0.0) for n in ARM_BONES},
            "max_absolute_centroid_difference_by_bone": {n: max(((Vector(src_c[n]) - Vector(tgt_c[n])).length
                for (_, src_c), (_, tgt_c) in zip(source_times, target_times) if src_c.get(n) and tgt_c.get(n)), default=0.0) for n in ARM_BONES},
            "target_basis_scale_component_range_by_bone": target_scale_extrema,
            "source_head_world_at_endpoints": source_pose_heads,
            "target_head_world_at_endpoints": target_pose_heads,
            "per_sample_errors": [{"time": t, "pose_matrix_error": e, "basis_decomposition_error": d, "written_action_basis_error": w}
                                  for (t, e), (_, d), (_, w) in zip(sample_matrix_errors, matrix_decompose_errors, target_written_errors)],
            "per_sample_vertex_error": [{"time": t, "max_world_vertex_error": e} for t, e in vertex_errors],
        }

    set_frame(scene, math.floor(float(bpy.data.actions.get(args.actions[0]).frame_range[0])))
    # The saved preview opens focused on the converted duplicate. All original
    # scene objects remain intact in this separate output file but are hidden.
    # Source reference copies are hidden only after evaluation; hiding earlier
    # can leave their dependency-graph deformation stale in background mode.
    visible_preview = {target, *target_copies}
    for obj in bpy.data.objects:
        obj.hide_set(obj not in visible_preview)
        obj.hide_render = obj not in visible_preview
    bpy.ops.object.select_all(action='DESELECT')
    target.hide_set(False)
    target.select_set(True)
    bpy.context.view_layer.objects.active = target
    report = {
        "source_scene": bpy.data.filepath,
        "source_rig": source_original.name,
        "production_basis": target_original.name,
        "source_object_matrix_world": matrix_rows(source_original.matrix_world),
        "target_object_matrix_world": matrix_rows(target_original.matrix_world),
        "C_production_from_candidate": matrix_rows(C),
        "constant_per_bone_right_corrections": {
            name: matrix_rows(source_original.data.bones[name].matrix_local.inverted_safe() @
                              C.inverted_safe() @ target_original.data.bones[name].matrix_local)
            for name in ARM_BONES},
        "conversion": "P_target_i(t) = C @ P_source_i(t) @ inverse(R_source_i) @ inverse(C) @ R_target_i; then derive target local matrix_basis top-down from target rest/parent pose matrices.",
        "bone_count": len(target.data.bones),
        "converted_rig": target.name,
        "production_object_scale_before": list(target_original.scale),
        "production_object_scale_after": list(target.scale),
        "production_target_object_constraints": len(target.constraints),
        "production_target_pose_constraint_count": sum(len(pb.constraints) for pb in target.pose.bones),
        "converted_meshes": [m.name for m in target_copies],
        "source_reference_rig_hidden": source.name,
        "actions": action_reports,
    }
    out = os.path.abspath(args.output)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=out)
    with open(os.path.splitext(out)[0] + ".json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, allow_nan=False)
        f.write("\n")
    print("MG1_NATIVE_CONVERSION=" + json.dumps({"output": out, "rig": target.name,
          "actions": {name: {"converted": val["converted_action"],
                              "matrix_error": val["evaluated_target_pose_max_matrix_error"],
                              "centroid_motion_error": val["max_baseline_corrected_weighted_centroid_motion_error"]}
                      for name, val in action_reports.items()}}))
    scene.frame_set(saved_frame, subframe=saved_subframe)


if __name__ == "__main__":
    main()
