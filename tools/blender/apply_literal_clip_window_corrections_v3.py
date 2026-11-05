"""Build disposable V3 clip previews with smooth, clip-local pose corrections."""
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


def cli():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--target-rig", default="LiteralWalk_Waluigi_Target")
    p.add_argument("--mesh-prefix", default="LiteralWalk_ARMTEST_")
    p.add_argument("--profile", default=os.path.join(HERE, "profiles", "luigi_clip_corrections_v3.json"))
    p.add_argument("--clip", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--report", required=True)
    p.add_argument("--renders", required=True)
    p.add_argument("--step", type=float, default=0.25)
    return p.parse_args(argv)


def digest(value):
    return hashlib.sha256(json.dumps(value, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def action_bag(rig, action):
    old_slot = rig.animation_data.action_slot if rig.animation_data else None
    slot = slot_for(action, old_slot.identifier if old_slot else None)
    return slot, bag_for(action, slot)


def smoothstep(x):
    x = min(1.0, max(0.0, x))
    return x * x * (3.0 - 2.0 * x)


def window_weight(frame, window):
    start, end, fade = float(window["start"]), float(window["end"]), float(window["fade"])
    if start <= frame <= end:
        return 1.0
    if frame < start and fade > 0 and start-fade < frame < start:
        return smoothstep((frame-(start-fade))/fade)
    if frame > end and fade > 0 and end < frame < end+fade:
        return smoothstep(1.0-(frame-end)/fade)
    return 0.0


def curves_for_bone(bag, bone):
    path = f'pose.bones["{bone}"].rotation_quaternion'
    rows = {int(fc.array_index): fc for fc in bag.fcurves if fc.data_path == path}
    if set(rows) != {0, 1, 2, 3}:
        return None
    return rows


def apply_windowed_corrections(bag, windows, rig):
    touched = {}
    for window in windows:
        factor = float(window["factor"])
        for bone in window["bones"]:
            if bone not in rig.pose.bones:
                raise RuntimeError(f"configured correction bone missing: {bone}")
            curves = curves_for_bone(bag, bone)
            if curves is None:
                raise RuntimeError(f"complete quaternion channel set missing: {bone}")
            times = sorted({float(k.co.x) for fc in curves.values() for k in fc.keyframe_points})
            changed = 0
            max_change = 0.0
            for frame in times:
                weight = window_weight(frame, window)
                if weight <= 0:
                    continue
                q = Quaternion(tuple(float(curves[i].evaluate(frame)) for i in range(4)))
                if q.magnitude < 1e-10:
                    raise RuntimeError(f"invalid quaternion at {bone}@{frame}")
                q.normalize()
                if q.w < 0:
                    q.negate()
                effective = 1.0 - (1.0-factor)*weight
                adjusted = Quaternion((1.0, 0.0, 0.0, 0.0)).slerp(q, effective)
                max_change = max(max_change, q.rotation_difference(adjusted).angle)
                for i, fc in curves.items():
                    point = next((p for p in fc.keyframe_points if abs(float(p.co.x)-frame)<1e-6), None)
                    if point is None:
                        raise RuntimeError(f"inconsistent key times for {bone}")
                    point.co.y = float(adjusted[i])
                changed += 1
            for fc in curves.values():
                fc.update()
            row = touched.setdefault(bone, {"windows": [], "changed_key_count": 0,
                                              "max_key_rotation_change_rad": 0.0})
            row["windows"].append({k: window[k] for k in ("start", "end", "fade", "factor")})
            row["changed_key_count"] += changed
            row["max_key_rotation_change_rad"] = max(row["max_key_rotation_change_rad"], max_change)
    return touched


def basis(rig):
    return {pb.name: [[round(float(x), 7) for x in row] for row in pb.matrix_basis]
            for pb in rig.pose.bones}


def world_vertices(meshes):
    dg = bpy.context.evaluated_depsgraph_get()
    rows = []
    for obj in meshes:
        ev = obj.evaluated_get(dg)
        me = ev.to_mesh()
        try:
            points = [[float(c) for c in ev.matrix_world @ v.co] for v in me.vertices]
            if any(not math.isfinite(c) for p in points for c in p):
                raise RuntimeError(f"nonfinite evaluated vertex in {obj.name}")
            rows.append(points)
        finally:
            ev.to_mesh_clear()
    return rows


def max_vertex_delta(a, b):
    if len(a) != len(b):
        raise RuntimeError("mesh object count changed")
    return max((math.dist(x, y) for xa, xb in zip(a, b) for x, y in zip(xa, xb)), default=0.0)


def timing_signature(bag):
    return sorted((fc.data_path, int(fc.array_index),
        tuple(round(float(p.co.x), 7) for p in fc.keyframe_points),
        tuple(p.interpolation for p in fc.keyframe_points)) for fc in bag.fcurves)


def main():
    args = cli()
    src_blend = os.path.abspath(bpy.data.filepath)
    output = os.path.abspath(args.output)
    if not src_blend or output == src_blend:
        raise RuntimeError("V3 output must be a different disposable file")
    with open(args.profile, encoding="utf-8") as f:
        profile = json.load(f)
    if profile.get("schema") != "mg1.literal-retarget-clip-corrections.v3":
        raise RuntimeError("unsupported V3 correction schema")
    correction = profile["clips"].get(args.clip)
    rig = bpy.data.objects.get(args.target_rig)
    if not correction or not rig or rig.type != "ARMATURE":
        raise RuntimeError("clip correction or disposable target rig not found")
    if max(abs(float(x)-43.437) for x in rig.scale) > .02:
        raise RuntimeError("Waluigi intentional object scale changed")
    if not rig.animation_data or not rig.animation_data.action:
        raise RuntimeError("V2 baked action missing")
    v2_action = rig.animation_data.action
    if args.clip not in v2_action.name:
        raise RuntimeError(f"input action {v2_action.name} does not match {args.clip}")
    old_slot, old_bag = action_bag(rig, v2_action)
    # Keep the V2 source action in the disposable file for exact reopen regression.
    v2_action.use_fake_user = True
    before_timing = timing_signature(old_bag)
    action = v2_action.copy()
    action.name = f"Waluigi_{args.clip}_Surgical_V3"
    rig.animation_data.action = action
    slot = next((s for s in action.slots if s.identifier == old_slot.identifier), None)
    if slot is None:
        if len(action.slots) != 1:
            raise RuntimeError("copied action slot is ambiguous")
        slot = action.slots[0]
    rig.animation_data.action_slot = slot
    bag = bag_for(action, slot)
    changes = apply_windowed_corrections(bag, correction["windows"], rig)
    unchanged_timing = timing_signature(bag) == before_timing
    if not unchanged_timing:
        raise RuntimeError("key times or interpolation changed")

    meshes = sorted((o for o in bpy.data.objects if o.type == "MESH" and o.name.startswith(args.mesh_prefix)
        and any(m.type == "ARMATURE" and m.object == rig for m in o.modifiers)), key=lambda o:o.name)
    if len(meshes) != 2:
        raise RuntimeError(f"expected two bound preview meshes, got {len(meshes)}")
    start, end = map(float, v2_action.frame_range)
    frames = [round(start+i*args.step, 7) for i in range(int(math.floor((end-start)/args.step+1e-8))+1)]
    if not frames or frames[-1] < end-1e-6:
        frames.append(end)
    scene = bpy.context.scene
    original_frame = (scene.frame_current, scene.frame_subframe)
    v2_basis, v2_mesh, root = {}, {}, {}
    scene_frames = []
    rig.animation_data.action, rig.animation_data.action_slot = v2_action, old_slot
    for frame in frames:
        whole = math.floor(frame); scene.frame_set(whole, subframe=frame-whole); bpy.context.view_layer.update()
        v2_basis[frame] = basis(rig); v2_mesh[frame] = world_vertices(meshes)
        root[frame] = [float(x) for x in rig.location]
    rig.animation_data.action, rig.animation_data.action_slot = action, slot
    hashes, max_step, max_delta = {}, 0.0, 0.0
    prev = None
    changed_names = set(changes)
    noncorrected_max = 0.0
    fist_local_max = 0.0
    before_basis_at = {}
    for frame in frames:
        whole = math.floor(frame); scene.frame_set(whole, subframe=frame-whole); bpy.context.view_layer.update()
        current = basis(rig)
        verts = world_vertices(meshes)
        max_delta = max(max_delta, max_vertex_delta(verts, v2_mesh[frame]))
        if prev is not None:
            max_step = max(max_step, max_vertex_delta(verts, prev))
        prev = verts
        for name, mat in current.items():
            diff = max(abs(mat[r][c]-v2_basis[frame][name][r][c]) for r in range(4) for c in range(4))
            if name not in changed_names:
                noncorrected_max = max(noncorrected_max, diff)
            if name.startswith(("bone_31", "bone_51")) or any(name.startswith(f"bone_{n}") for n in range(66, 87)):
                fist_local_max = max(fist_local_max, diff)
        hashes[str(frame)] = {"pose": digest({"basis": current,
            "location": [round(float(x), 7) for x in rig.location],
            "scale": [round(float(x), 7) for x in rig.scale]}),
            "mesh": {obj.name: digest([[round(c, 6) for c in p] for p in points])
                     for obj, points in zip(meshes, verts)}}
    if not math.isfinite(max_step) or noncorrected_max > 1e-6 or fist_local_max > 1e-6:
        raise RuntimeError("unexpected uncorrected/fist change or invalid geometry")

    render_frames = {"luigi_armature_luigirun": [0, 8, 15, 30, 36, 45, 61],
                     "luigi_armature_luigijump": [0, 9, 14, 15, 18, 24],
                     "luigi_armature_swimbreast": [0, 5, 11, 12, 13, 14, 30, 59]}
    preview_frames = [f for f in render_frames[args.clip] if start <= f <= end]
    pose_samples, root_samples = {}, {}
    for frame in preview_frames:
        scene.frame_set(int(frame), subframe=frame-int(frame)); bpy.context.view_layer.update()
        pose_samples[frame] = {pb.name: [list(row) for row in pb.matrix_basis] for pb in rig.pose.bones}
        root_samples[float(frame)] = [float(x) for x in rig.location]
    for obj in bpy.data.objects:
        if obj.type in {"CAMERA", "LIGHT"}:
            continue
        visible = obj == rig or obj in meshes
        obj.hide_render = not visible; obj.hide_viewport = not visible; obj.hide_set(not visible)
    renders, render_failures = setup_render(scene, rig, meshes, os.path.abspath(args.renders), pose_samples, root_samples)
    scene.frame_start, scene.frame_end = math.floor(start), math.ceil(end)
    scene.frame_set(math.floor(original_frame[0]), subframe=original_frame[1])
    os.makedirs(os.path.dirname(output), exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=output)
    report = {"schema":"mg1.literal-retarget-v3-preview-validation.v1", "input_v2_blend":src_blend,
        "output_blend":output, "source_action":args.clip, "input_action":v2_action.name,
        "output_action":action.name, "correction_reason":correction["reason"], "bone_corrections":changes,
        "key_times_interpolation_unchanged":unchanged_timing, "quarter_frame_count":len(frames),
        "sample_step":args.step, "sample_range":[start,end], "max_world_vertex_delta_from_v2":max_delta,
        "max_adjacent_quarter_sample_vertex_step":max_step,
        "max_uncorrected_basis_delta":noncorrected_max, "max_fixed_fist_local_basis_delta":fist_local_max,
        "root_translation_unchanged":all(root[f]==[float(x) for x in rig.location] for f in []),
        "scale": [float(x) for x in rig.scale], "runtime_constraints":len(rig.constraints)+sum(len(pb.constraints) for pb in rig.pose.bones),
        "expected_sample_hashes":hashes, "render_paths":renders, "render_failures":render_failures}
    # Explicitly compare target root translation at all samples.
    rig.animation_data.action, rig.animation_data.action_slot = v2_action, old_slot
    root_unchanged = True
    for frame in frames:
        n=math.floor(frame); scene.frame_set(n,subframe=frame-n); bpy.context.view_layer.update()
        root_unchanged &= max(abs(float(a)-b) for a,b in zip(rig.location,root[frame])) < 1e-7
    rig.animation_data.action, rig.animation_data.action_slot = action, slot
    report["root_translation_unchanged"] = root_unchanged
    os.makedirs(os.path.dirname(os.path.abspath(args.report)), exist_ok=True)
    with open(args.report,"w",encoding="utf-8") as f: json.dump(report,f,indent=2,allow_nan=False); f.write("\n")
    if render_failures or not root_unchanged or not unchanged_timing or report["runtime_constraints"] or max(abs(x-43.437) for x in report["scale"])>.02:
        raise RuntimeError("V3 preview validation gate failed")
    print("LITERAL_V3_PREVIEW", json.dumps({"action":action.name,"frames":len(frames),"max_delta":max_delta,"output":output}))


if __name__ == "__main__":
    main()
