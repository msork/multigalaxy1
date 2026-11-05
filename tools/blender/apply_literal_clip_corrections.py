"""Apply isolated clip corrections to disposable literal-transfer previews.

This postprocessor does not touch the shared retarget converter or the golden
walk. It duplicates the baked target action, scales only named rest-relative
quaternion deltas from an explicit clip profile, then checks actual skinned
mesh vertices at quarter frames before saving a new preview.
"""
import argparse
import hashlib
import json
import math
import os
import sys

import bpy
from mathutils import Quaternion

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from prototype_direct_luigi_walk_literal import bag_for, setup_render, slot_for


def args_cli():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--target-rig", default="LiteralWalk_Waluigi_Target")
    p.add_argument("--mesh-prefix", default="LiteralWalk_ARMTEST_")
    p.add_argument("--corrections", default=os.path.join(
        HERE, "profiles", "luigi_clip_corrections_v1.json"))
    p.add_argument("--output", required=True)
    p.add_argument("--report", required=True)
    p.add_argument("--renders", required=True)
    p.add_argument("--step", type=float, default=0.25)
    return p.parse_args(argv)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def action_bag(rig, action):
    old_slot = rig.animation_data.action_slot if rig.animation_data else None
    slot = slot_for(action, old_slot.identifier if old_slot else None)
    return slot, bag_for(action, slot)


def bone_rotation_curves(bag, bone_name):
    path = f'pose.bones["{bone_name}"].rotation_quaternion'
    return {fc.array_index: fc for fc in bag.fcurves if fc.data_path == path}


def scale_action_bone_rotation(bag, bone_name, factor):
    curves = bone_rotation_curves(bag, bone_name)
    if len(curves) != 4 or set(curves) != {0, 1, 2, 3}:
        return {"key_count": 0, "max_angular_change_rad": 0.0,
                "skipped": "no complete quaternion channel set"}
    frames = sorted({float(p.co.x) for fc in curves.values() for p in fc.keyframe_points})
    changed = 0.0
    for frame in frames:
        q = Quaternion(tuple(float(curves[i].evaluate(frame)) for i in range(4)))
        if q.magnitude < 1.0e-10:
            raise RuntimeError(f"zero quaternion in {bone_name} at frame {frame}")
        q.normalize()
        if q.w < 0:
            q.negate()
        adjusted = Quaternion((1.0, 0.0, 0.0, 0.0)).slerp(q, float(factor))
        changed = max(changed, q.rotation_difference(adjusted).angle)
        for index, fc in curves.items():
            point = next((p for p in fc.keyframe_points if abs(float(p.co.x)-frame) < 1e-6), None)
            if point is None:
                raise RuntimeError(f"inconsistent quaternion key times in {bone_name}")
            point.co.y = float(adjusted[index])
        for fc in curves.values():
            fc.update()
    return {"key_count": len(frames), "max_angular_change_rad": changed,
            "factor": float(factor)}


def evaluated_vertices(meshes):
    dg = bpy.context.evaluated_depsgraph_get()
    all_rows = []
    for obj in meshes:
        ev = obj.evaluated_get(dg)
        me = ev.to_mesh()
        try:
            pts = [ev.matrix_world @ v.co for v in me.vertices]
            if any(not all(math.isfinite(float(c)) for c in p) for p in pts):
                raise RuntimeError(f"non-finite evaluated vertex in {obj.name}")
            all_rows.append(pts)
        finally:
            ev.to_mesh_clear()
    return all_rows


def vertex_delta(a, b):
    if len(a) != len(b):
        raise RuntimeError("evaluated vertex count changed")
    return max(((x-y).length for x, y in zip(a, b)), default=0.0)


def rounded_digest(rows, digits=6):
    payload = json.dumps(rows, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def basis_rows(rig):
    return {pb.name: [[round(float(v), 6) for v in row] for row in pb.matrix_basis]
            for pb in rig.pose.bones}


def curve_timing_signature(bag):
    return sorted((fc.data_path, int(fc.array_index),
        tuple(round(float(p.co.x), 7) for p in fc.keyframe_points),
        tuple(p.interpolation for p in fc.keyframe_points)) for fc in bag.fcurves)


def main():
    args = args_cli()
    source_blend = os.path.abspath(bpy.data.filepath)
    if not source_blend or os.path.abspath(args.output) == source_blend:
        raise RuntimeError("output must be a new path, never the input preview")
    input_hash = sha256_file(source_blend)
    with open(args.corrections, "r", encoding="utf-8") as f:
        correction_profile = json.load(f)
    if correction_profile.get("schema") != "mg1.literal-retarget-clip-corrections.v1":
        raise RuntimeError("unsupported clip correction profile")
    rig = bpy.data.objects.get(args.target_rig)
    if not rig or rig.type != "ARMATURE":
        raise RuntimeError("disposable target armature is missing")
    if max(abs(float(v)-43.437) for v in rig.scale) > 0.02:
        raise RuntimeError("intentional Waluigi scale signature changed")
    if not rig.animation_data or not rig.animation_data.action:
        raise RuntimeError("target action is missing")
    old_action = rig.animation_data.action
    source_action_name = old_action.name
    # The output action embeds the raw source action name in the converter name.
    clip_name = next((name for name in correction_profile["clips"]
                      if name in source_action_name), None)
    if clip_name is None:
        raise RuntimeError(f"no correction is authorized for action {source_action_name}")
    correction = correction_profile["clips"][clip_name]
    old_slot, old_bag = action_bag(rig, old_action)
    original_curve_timing = curve_timing_signature(old_bag)
    action = old_action.copy()
    action.name = f"Waluigi_{clip_name}_ProportionSafe_v2"
    rig.animation_data.action = action
    slot = next((s for s in action.slots if s.identifier == old_slot.identifier), None)
    if slot is None:
        if len(action.slots) != 1:
            raise RuntimeError("copied action slot could not be resolved")
        slot = action.slots[0]
    rig.animation_data.action_slot = slot
    bag = bag_for(action, slot)
    bone_rows = {}
    for bone, factor in correction["bone_factors"].items():
        if bone not in rig.pose.bones:
            raise RuntimeError(f"correction bone {bone} is missing")
        bone_rows[bone] = scale_action_bone_rotation(bag, bone, factor)
    timing_unchanged = curve_timing_signature(bag) == original_curve_timing
    if not timing_unchanged:
        raise RuntimeError("clip correction changed action key times or interpolation")
    # Do not touch key timing or interpolation. Only quaternion values on the
    # explicit clip-local bone list are adjusted above.

    meshes = sorted([o for o in bpy.data.objects if o.type == "MESH" and
        o.name.startswith(args.mesh_prefix) and any(
            m.type == "ARMATURE" and m.object == rig for m in o.modifiers)], key=lambda o:o.name)
    if len(meshes) != 2:
        raise RuntimeError(f"expected two bound Waluigi meshes, found {len(meshes)}")
    frames = [round(float(old_action.frame_range[0]) + i*args.step, 6)
              for i in range(int(math.floor((old_action.frame_range[1]-old_action.frame_range[0]) /
                                            args.step + 1e-8))+1)]
    if not frames or frames[-1] < old_action.frame_range[1]-1e-6:
        frames.append(float(old_action.frame_range[1]))
    frames = sorted(set(frames))
    original_scene = bpy.context.scene
    original_frame = (original_scene.frame_current, original_scene.frame_subframe)
    baseline_by_frame = {}
    baseline_bone_scales = {}
    baseline_basis_by_frame = {}
    baseline_object_locations = {}
    rig.animation_data.action = old_action
    rig.animation_data.action_slot = old_slot
    max_step_before = 0.0
    previous_before = None
    for frame in frames:
        whole = math.floor(frame)
        original_scene.frame_set(whole, subframe=frame-whole)
        bpy.context.view_layer.update()
        before = evaluated_vertices(meshes)
        baseline_by_frame[frame] = before
        baseline_basis_by_frame[frame] = basis_rows(rig)
        baseline_object_locations[frame] = tuple(float(x) for x in rig.location)
        baseline_bone_scales[frame] = {pb.name: tuple(float(x) for x in pb.matrix_basis.to_scale())
                                        for pb in rig.pose.bones}
        if previous_before is not None:
            max_step_before = max(max_step_before,
                max(vertex_delta(a,b) for a,b in zip(before, previous_before)))
        previous_before = before
    rig.animation_data.action = action
    rig.animation_data.action_slot = slot
    max_step_after = 0.0
    previous_after = None
    corrected_by_frame = {}
    max_corrected_delta_from_v1 = 0.0
    max_bone_scale_delta_from_v1 = 0.0
    max_noncorrected_basis_delta_from_v1 = 0.0
    max_corrected_translation_delta_from_v1 = 0.0
    max_object_location_delta_from_v1 = 0.0
    expected_pose_hashes = {}
    expected_mesh_vertex_hashes = {}
    for frame in frames:
        whole = math.floor(frame)
        original_scene.frame_set(whole, subframe=frame-whole)
        bpy.context.view_layer.update()
        after = evaluated_vertices(meshes)
        corrected_by_frame[frame] = after
        expected_pose_hashes[str(frame)] = rounded_digest({
            "basis": basis_rows(rig),
            "location": [round(float(v), 6) for v in rig.location],
            "scale": [round(float(v), 6) for v in rig.scale]})
        expected_mesh_vertex_hashes[str(frame)] = {
            obj.name: rounded_digest([[round(float(c), 6) for c in p] for p in points])
            for obj, points in zip(meshes, after)}
        for pb in rig.pose.bones:
            current_scale = tuple(float(x) for x in pb.matrix_basis.to_scale())
            max_bone_scale_delta_from_v1 = max(max_bone_scale_delta_from_v1,
                max(abs(a-b) for a,b in zip(current_scale, baseline_bone_scales[frame][pb.name])))
            current_basis = [[float(v) for v in row] for row in pb.matrix_basis]
            old_basis = baseline_basis_by_frame[frame][pb.name]
            if pb.name in correction["bone_factors"]:
                max_corrected_translation_delta_from_v1 = max(
                    max_corrected_translation_delta_from_v1,
                    max(abs(current_basis[r][3]-old_basis[r][3]) for r in range(3)))
            else:
                max_noncorrected_basis_delta_from_v1 = max(
                    max_noncorrected_basis_delta_from_v1,
                    max(abs(current_basis[r][c]-old_basis[r][c])
                        for r in range(4) for c in range(4)))
        max_object_location_delta_from_v1 = max(max_object_location_delta_from_v1,
            max(abs(float(a)-float(b)) for a,b in zip(rig.location, baseline_object_locations[frame])))
        max_corrected_delta_from_v1 = max(max_corrected_delta_from_v1,
            max(vertex_delta(a,b) for a,b in zip(after, baseline_by_frame[frame])))
        if previous_after is not None:
            max_step_after = max(max_step_after,
                max(vertex_delta(a,b) for a,b in zip(after, previous_after)))
        previous_after = after
    non_finite = False
    for point_sets in corrected_by_frame.values():
        non_finite |= any(not all(math.isfinite(float(c)) for p in pts for c in p)
                          for pts in point_sets)
    if non_finite:
        raise RuntimeError("quarter-frame mesh validation found non-finite vertices")
    runtime_constraints = sum(len(pb.constraints) for pb in rig.pose.bones) + len(rig.constraints)

    render_frames_by_clip = {
        "luigi_armature_luigirun": [0, 15, 30, 45, 61],
        "luigi_armature_luigijump": [0, 6, 12, 18, 24],
        "luigi_armature_swimbreast": [0, 5, 12, 30, 59],
    }
    preview_frames = [x for x in render_frames_by_clip[clip_name]
                      if frames[0] <= x <= frames[-1]]
    pose_samples, root_samples = {}, {}
    for frame in preview_frames:
        original_scene.frame_set(frame)
        bpy.context.view_layer.update()
        pose_samples[frame] = {pb.name: [list(row) for row in pb.matrix_basis]
                               for pb in rig.pose.bones}
        root_samples[float(frame)] = [float(x) for x in rig.location]

    for obj in bpy.data.objects:
        if obj.type in {"CAMERA", "LIGHT"}:
            continue
        is_target = obj == rig or obj in meshes
        obj.hide_render = not is_target
        obj.hide_viewport = not is_target
        obj.hide_set(not is_target)
    render_paths, render_failures = setup_render(
        original_scene, rig, meshes, os.path.abspath(args.renders), pose_samples, root_samples)
    original_scene.frame_start = math.floor(frames[0])
    original_scene.frame_end = math.ceil(frames[-1])
    original_scene.frame_set(math.floor(frames[0]), subframe=frames[0]-math.floor(frames[0]))
    bpy.context.view_layer.update()
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    scene_scale = [float(x) for x in rig.scale]
    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(args.output))
    report = {
        "schema":"mg1.literal-retarget-clip-correction-validation.v1",
        "input_preview":source_blend,
        "input_preview_sha256_before":input_hash,
        "output_preview":os.path.abspath(args.output),
        "source_action":clip_name,
        "source_target_action":source_action_name,
        "corrected_action":action.name,
        "correction_profile":os.path.abspath(args.corrections),
        "correction_reason":correction["reason"],
        "correction_method":"positive slerp from rest-relative identity toward the baked Waluigi bone-local quaternion; timing, root location, translation, scale, and interpolation remain unchanged",
        "bone_corrections":bone_rows,
        "key_times_and_interpolation_unchanged":timing_unchanged,
        "expected_pose_hashes_rounded_1e-6":expected_pose_hashes,
        "expected_mesh_vertex_hashes_rounded_1e-6":expected_mesh_vertex_hashes,
        "quarter_frame_validation":{"sample_count":len(frames),"step":args.step,
            "frame_range":[frames[0],frames[-1]],"all_mesh_vertices_finite":True,
            "max_world_vertex_step":max_step_after,
            "max_world_vertex_step_before_correction":max_step_before,
            "max_world_vertex_delta_from_v1":max_corrected_delta_from_v1,
            "mesh_object_count":len(meshes),"armature_scale":scene_scale,
            "max_bone_scale_delta_from_v1":max_bone_scale_delta_from_v1,
            "max_noncorrected_local_basis_delta_from_v1":max_noncorrected_basis_delta_from_v1,
            "max_corrected_local_translation_delta_from_v1":max_corrected_translation_delta_from_v1,
            "max_object_location_delta_from_v1":max_object_location_delta_from_v1,
            "runtime_constraints":runtime_constraints},
        "render_paths":render_paths,"render_failures":render_failures,
        "input_preview_hash_unchanged_in_memory":sha256_file(source_blend)==input_hash
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.report)), exist_ok=True)
    with open(args.report,"w",encoding="utf-8") as f:
        json.dump(report,f,indent=2,allow_nan=False)
        f.write("\n")
    if (render_failures or max_bone_scale_delta_from_v1>1e-6 or
        max_noncorrected_basis_delta_from_v1>1e-6 or
        max_corrected_translation_delta_from_v1>1e-6 or
        max_object_location_delta_from_v1>1e-6 or not timing_unchanged or runtime_constraints):
        raise RuntimeError("preview correction validation failed")
    print("CLIP_CORRECTION_PREVIEW",json.dumps({
        "action":clip_name,"frames":len(frames),"preview":args.output,
        "render_count":len(render_paths),"max_step":max_step_after}))


if __name__ == "__main__": main()
