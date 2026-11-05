"""Drive a Waluigi deform rig from an unchanged Galaxy Luigi control action.

This is an isolated experiment. The Luigi action is copied curve-for-curve to
a disposable 30-bone control rig. A second disposable armature retains the
full Waluigi hierarchy and original vertex groups. Semantic local rotations
are rest-axis converted onto selected Waluigi bones and baked densely; the
Waluigi finger pose is held at the input V2 fist pose. Source assets/actions
and the input .blend are never saved over.
"""
import argparse
import json
import math
import os
import sys

import bpy
from mathutils import Matrix, Quaternion, Vector
from mathutils.kdtree import KDTree

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def cli():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--profile", default=os.path.join(HERE, "profiles", "luigi_hybrid_waluigi.json"))
    p.add_argument("--source-rig", default="luigi.001_armature")
    p.add_argument("--source-action", required=True)
    p.add_argument("--target-rig", default="123475520_waluigi.gpl")
    p.add_argument("--target-mesh", action="append", default=[])
    p.add_argument("--reference-rig", default="LiteralWalk_Waluigi_Target")
    p.add_argument("--reference-mesh", action="append", default=[])
    p.add_argument("--output", required=True)
    p.add_argument("--report", required=True)
    p.add_argument("--renders", required=True)
    p.add_argument("--step", type=float, default=0.25)
    p.add_argument("--render-frames", nargs="*", type=float, default=[])
    p.add_argument("--mapping-mode", choices=("local_delta", "absolute_rest_delta"), default="local_delta")
    p.add_argument("--body-context", choices=("v2_reference", "full_source"), default="v2_reference")
    return p.parse_args(argv)


def rows_mat(m):
    return [[float(m[r][c]) for c in range(4)] for r in range(4)]


def vec(v):
    return [float(x) for x in v]


def fcurve_list(action, slot):
    return [fc for layer in action.layers for strip in layer.strips
            for bag in strip.channelbags if bag.slot == slot for fc in bag.fcurves]


def choose_source_slot(action):
    rows = []
    for slot in action.slots:
        curves = fcurve_list(action, slot)
        if curves and any('pose.bones[' in fc.data_path for fc in curves):
            rows.append((slot, curves))
    if len(rows) != 1:
        raise RuntimeError(f"action {action.name}: expected one bone slot, got {len(rows)}")
    return rows[0]


def clone_action(source_action, source_slot, source_curves, rig, name):
    out = bpy.data.actions.new(name)
    slot = out.slots.new('OBJECT', rig.name)
    layer = out.layers.new("Curve-identical original Galaxy action")
    strip = layer.strips.new(type='KEYFRAME')
    bag = strip.channelbag(slot, ensure=True)
    for curve in source_curves:
        kwargs = {"index": curve.array_index}
        if curve.group:
            kwargs["group_name"] = curve.group.name
        dst = bag.fcurves.new(curve.data_path, **kwargs)
        dst.keyframe_points.add(len(curve.keyframe_points))
        for a, b in zip(curve.keyframe_points, dst.keyframe_points):
            b.co = a.co
            b.interpolation = a.interpolation
            b.handle_left_type = a.handle_left_type
            b.handle_right_type = a.handle_right_type
            b.handle_left = a.handle_left
            b.handle_right = a.handle_right
            b.type = a.type
            b.easing = a.easing
            b.amplitude = a.amplitude
            b.back = a.back
            b.period = a.period
        dst.extrapolation = curve.extrapolation
        dst.mute = curve.mute
    rig.animation_data_create()
    rig.animation_data.action = out
    rig.animation_data.action_slot = slot
    return out, slot


def rest_similarity(source, target, landmarks):
    # Source Galaxy (X,Y,Z up) to Waluigi armature-local (X,Z,-Y).
    axis = Matrix(((1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, -1.0, 0.0)))
    src, dst = [], []
    for s, t in landmarks:
        if source.data.bones.get(s) and target.data.bones.get(t):
            src.append(source.data.bones[s].head_local.copy())
            dst.append(target.data.bones[t].head_local.copy())
    if len(src) < 4:
        raise RuntimeError("insufficient rest landmarks")
    cs = sum(src, Vector()) / len(src)
    ct = sum(dst, Vector()) / len(dst)
    sa = [axis @ (p-cs) for p in src]
    da = [p-ct for p in dst]
    scale = sum(a.dot(b) for a, b in zip(sa, da)) / sum(a.length_squared for a in sa)
    trans = ct - scale * (axis @ cs)
    transform = Matrix.Translation(trans) @ axis.to_4x4() @ Matrix.Scale(scale, 4)
    errs = []
    for (sn, tn), a, b in zip(landmarks, src, dst):
        errs.append({"source": sn, "target": tn,
                     "head_error": float(((transform @ a) - b).length)})
    return transform, scale, errs


def action_copy_error(a, b, slot_a, slot_b, lo, hi, step=0.25):
    ca = {(x.data_path, x.array_index): x for x in fcurve_list(a, slot_a)}
    cb = {(x.data_path, x.array_index): x for x in fcurve_list(b, slot_b)}
    if ca.keys() != cb.keys():
        return float("inf")
    err, f = 0.0, lo
    while f <= hi + 1e-5:
        err = max(err, max(abs(float(ca[k].evaluate(f))-float(cb[k].evaluate(f))) for k in ca))
        f += step
    return err


def reset_pose(rig):
    rig.animation_data_clear()
    for pb in rig.pose.bones:
        pb.location = (0, 0, 0)
        pb.rotation_quaternion = (1, 0, 0, 0)
        pb.scale = (1, 1, 1)
        pb.matrix_basis = Matrix.Identity(4)


def duplicate_rig(source, name, collection):
    rig = source.copy()
    rig.data = source.data.copy()
    rig.name = name
    rig.data.name = name + "_Data"
    for col in list(rig.users_collection):
        col.objects.unlink(rig)
    collection.objects.link(rig)
    rig.parent = None
    rig.matrix_world = source.matrix_world.copy()
    reset_pose(rig)
    return rig


def duplicate_mesh(source, rig, collection, name):
    obj = source.copy()
    obj.data = source.data.copy()
    obj.name = name
    obj.data.name = name + "_Data"
    for col in list(obj.users_collection):
        col.objects.unlink(obj)
    collection.objects.link(obj)
    obj.parent = None
    obj.matrix_parent_inverse = Matrix.Identity(4)
    obj.matrix_world = source.evaluated_get(bpy.context.evaluated_depsgraph_get()).matrix_world.copy()
    for mod in obj.modifiers:
        if mod.type == 'ARMATURE':
            mod.object = rig
            mod.use_vertex_groups = True
            mod.use_bone_envelopes = False
    obj.hide_viewport = obj.hide_render = False
    obj.hide_set(False)
    return obj


def normalized_quat(q):
    out = q.copy()
    out.normalize()
    return out


def quat_map(source_rig, target_rig, transform, src_name, dst_name):
    sr = source_rig.data.bones[src_name].matrix_local.to_3x3().to_quaternion()
    tr = target_rig.data.bones[dst_name].matrix_local.to_3x3().to_quaternion()
    p = transform.to_3x3().to_quaternion()
    # Maps a source bone-local rotation delta into the corresponding target
    # bone-local axes, including the independently authored target bone roll.
    c = normalized_quat(tr.inverted() @ p @ sr)
    return c


def pose_points(obj):
    dg = bpy.context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(dg)
    me = ev.to_mesh()
    try:
        return [ev.matrix_world @ v.co for v in me.vertices]
    finally:
        ev.to_mesh_clear()


def armature_local_points(mesh, rig, root_bone="bone_6"):
    dg = bpy.context.evaluated_depsgraph_get()
    ev = mesh.evaluated_get(dg)
    re = rig.evaluated_get(dg)
    me = ev.to_mesh()
    root = re.pose.bones.get(root_bone)
    anchor = re.matrix_world @ root.matrix if root else re.matrix_world
    inv = anchor.inverted_safe()
    try:
        return [inv @ (ev.matrix_world @ v.co) for v in me.vertices]
    finally:
        ev.to_mesh_clear()


def percentile(values, q):
    if not values:
        return 0.0
    a = sorted(float(x) for x in values)
    return a[min(round(q * (len(a)-1)), len(a)-1)]


def nearest_p95(points_a, points_b):
    if not points_a or not points_b:
        return None
    tree = KDTree(len(points_b))
    for i, p in enumerate(points_b):
        tree.insert(p, i)
    tree.balance()
    return percentile([tree.find(p)[2] for p in points_a], 0.95)


def relative_pose_quat(rig, name):
    root = rig.pose.bones.get("bone_6")
    pb = rig.pose.bones.get(name)
    if not root or not pb:
        return None
    return normalized_quat((root.matrix.inverted_safe() @ pb.matrix).to_quaternion())


def quat_angle_deg(a, b):
    if a is None or b is None:
        return None
    dot = min(1.0, max(-1.0, abs(float(a.dot(b)))))
    return math.degrees(2.0 * math.acos(dot))


def group_rows(mesh):
    names = {g.index: g.name for g in mesh.vertex_groups}
    return [{names[g.group]: float(g.weight) for g in v.groups if g.group in names}
            for v in mesh.data.vertices]


def set_frame(scene, value):
    integer = math.floor(value)
    scene.frame_set(integer, subframe=value-integer)
    bpy.context.view_layer.update()


def main():
    args = cli()
    input_blend = bpy.data.filepath
    scene = bpy.context.scene
    with open(args.profile, encoding='utf-8') as f:
        profile = json.load(f)
    source = bpy.data.objects.get(args.source_rig)
    target = bpy.data.objects.get(args.target_rig)
    reference = bpy.data.objects.get(args.reference_rig)
    action = bpy.data.actions.get(args.source_action)
    if not source or source.type != 'ARMATURE' or not target or target.type != 'ARMATURE':
        raise RuntimeError("source or target armature missing")
    if not reference or reference.type != 'ARMATURE':
        raise RuntimeError(f"V2 comparison rig missing: {args.reference_rig}")
    if not action:
        raise RuntimeError(f"source action missing: {args.source_action}")
    src_slot, src_curves = choose_source_slot(action)
    target_names = args.target_mesh or profile['target_meshes']
    ref_names = args.reference_mesh or profile['reference_meshes']
    original_meshes = [bpy.data.objects.get(n) for n in target_names]
    reference_meshes = [bpy.data.objects.get(n) for n in ref_names]
    if any(not m or m.type != 'MESH' for m in original_meshes+reference_meshes):
        raise RuntimeError("target/reference mesh missing")
    if len(original_meshes) != len(reference_meshes):
        raise RuntimeError("target/reference mesh count mismatch")
    for m in original_meshes:
        if not any(mod.type == 'ARMATURE' and mod.object == reference for mod in m.modifiers):
            raise RuntimeError(f"{m.name} is not bound to V2 reference rig")

    first, last = map(float, action.frame_range)
    step = float(args.step)
    if step <= 0:
        raise ValueError("step must be positive")
    # Anchor source and W rest data at a deterministic first frame.
    set_frame(scene, first)
    transform, fit_scale, rest_landmarks = rest_similarity(source, target, profile['landmarks'])
    # Use V2's proven object/mesh parent-space setup as the target basis. This
    # is essential for submesh1, whose local transform is part of its bind.
    root_target_world = reference.evaluated_get(bpy.context.evaluated_depsgraph_get()).matrix_world.copy()
    control = duplicate_rig(source, "HybridGalaxy_Luigi_Control", scene.collection)
    control.matrix_world = root_target_world @ transform
    ctrl_action, ctrl_slot = clone_action(action, src_slot, src_curves, control,
                                          "HybridGalaxy_Original_" + action.name)
    target_copy = duplicate_rig(target, "HybridWaluigi_Deform_Rig", scene.collection)
    target_copy.matrix_world = root_target_world
    # Output curves are authored consistently as quaternions. Preserve the
    # source control's original Euler/quaternion modes so its original curves
    # continue to evaluate without path conversion.
    for pb in target_copy.pose.bones:
        pb.rotation_mode = 'QUATERNION'
    mesh_copies = [duplicate_mesh(m, target_copy, scene.collection,
                                  "HybridWaluigi_"+m.name) for m in original_meshes]
    v2_ref_bases = {}
    for pb in reference.pose.bones:
        v2_ref_bases[pb.name] = pb.matrix_basis.copy()

    # Capture V2's validated closed-fist local pose at the first source frame.
    set_frame(scene, first)
    fist_names = set(profile['fist_bones'])
    fist_basis = {n: reference.pose.bones[n].matrix_basis.copy()
                  for n in fist_names if reference.pose.bones.get(n)}
    # Other unmodeled accessory/facial bones hold the first-frame V2 context.
    static_bases = {n: v2_ref_bases[n] for n in v2_ref_bases
                    if n in target_copy.pose.bones and n not in fist_names}

    # Retarget each Waluigi joint from the source semantic control using
    # rest-frame conjugation. Multiple deform branches can share a semantic
    # control while keeping their own weights, hierarchy, and rest offsets.
    bone_map = profile['deform_controls']
    maps = []
    for row in bone_map:
        s, t = row['source'], row['target']
        if source.data.bones.get(s) is None or target.data.bones.get(t) is None:
            raise RuntimeError(f"invalid control mapping {s}->{t}")
        maps.append((s, t, row.get('translation', False), row.get('rotation', True)))
    def bone_depth(bone):
        depth = 0
        while bone.parent:
            depth += 1
            bone = bone.parent
        return depth
    maps.sort(key=lambda row: bone_depth(target.data.bones[row[1]]))

    # Remove copied target W action only on the duplicate; mapped bones receive
    # retargeted motion, unmapped bones retain V2 frame-zero context, and digit
    # bones keep a fixed closed-fist pose.
    out_action = bpy.data.actions.new("HybridWaluigi_"+action.name+"_baked")
    out_slot = out_action.slots.new('OBJECT', target_copy.name)
    out_layer = out_action.layers.new("Rest-calibrated hybrid deform output")
    out_strip = out_layer.strips.new(type='KEYFRAME')
    out_bag = out_strip.channelbag(out_slot, ensure=True)
    target_copy.animation_data_create()
    target_copy.animation_data.action = out_action
    target_copy.animation_data.action_slot = out_slot

    frames = []
    f = first
    while f <= last + 1.0e-5:
        frames.append(round(f, 6))
        f += step
    if not frames or frames[-1] < last-1.0e-5:
        frames.append(last)
    # Snapshot the evaluated original source before writing output keys.
    # This avoids Blender 5.2's depsgraph/cache interaction when inserting
    # keys on a second rig in the same scene during source evaluation. The
    # disposable control has an identical copied action, verified separately;
    # the original source pose is read-only and remains the evaluation oracle.
    source_samples = {}
    context_samples = {}
    for frame in frames:
        set_frame(scene, frame)
        depsgraph = bpy.context.evaluated_depsgraph_get()
        source_eval = source.evaluated_get(depsgraph)
        source_samples[frame] = {
            s: {"pose": source_eval.pose.bones[s].matrix.copy(),
                "basis": source_eval.pose.bones[s].matrix_basis.copy()}
            for s, _t, _tr, _rot in maps
        }
        if args.body_context == "v2_reference":
            ref_eval = reference.evaluated_get(depsgraph)
            context_samples[frame] = {
                pb.name: pb.matrix_basis.copy() for pb in ref_eval.pose.bones
            }
    for frame in frames:
        set_frame(scene, frame)
        if args.body_context == "v2_reference":
            for n, basis in context_samples[frame].items():
                if n in target_copy.pose.bones:
                    target_copy.pose.bones[n].matrix_basis = basis
                    pb = target_copy.pose.bones[n]
                    pb.keyframe_insert("location", frame=frame, group=n)
                    pb.keyframe_insert("rotation_quaternion", frame=frame, group=n)
                    pb.keyframe_insert("scale", frame=frame, group=n)
        else:
            for n, basis in static_bases.items():
                if n in target_copy.pose.bones:
                    target_copy.pose.bones[n].matrix_basis = basis
        for n, basis in fist_basis.items():
            if n in target_copy.pose.bones:
                target_copy.pose.bones[n].matrix_basis = basis
        for src_name, dst_name, carry_translation, carry_rotation in maps:
            if args.body_context == "v2_reference" and dst_name not in set(profile["arm_control_targets"]) and not carry_translation:
                continue
            dst_pb = target_copy.pose.bones[dst_name]
            dst_pb.rotation_mode = 'QUATERNION'
            if args.mapping_mode == "absolute_rest_delta":
                source_pose = source_samples[frame][src_name]["pose"]
                source_rest = source.data.bones[src_name].matrix_local
                target_rest = target.data.bones[dst_name].matrix_local
                source_delta = source_pose @ source_rest.inverted_safe()
                if not carry_rotation:
                    source_delta = Matrix.Translation(source_delta.translation)
                desired_target_pose = transform @ source_delta @ transform.inverted_safe() @ target_rest
                dst_pb.matrix = desired_target_pose
                dst_pb.scale = (1, 1, 1)
            else:
                source_basis = source_samples[frame][src_name]["basis"]
                axis_map = quat_map(source, target, transform, src_name, dst_name)
                q_src = normalized_quat(source_basis.to_quaternion())
                if carry_rotation:
                    dst_pb.rotation_quaternion = normalized_quat(axis_map @ q_src @ axis_map.inverted())
                else:
                    dst_pb.rotation_quaternion = normalized_quat(v2_ref_bases[dst_name].to_quaternion())
                dst_pb.location = (axis_map @ source_basis.translation * fit_scale
                                   if carry_translation else Vector((0, 0, 0)))
                dst_pb.scale = (1, 1, 1)
            # Root translation, when present in the source action, is copied
            # in the calibrated target parent space. All other target joints
            # keep Waluigi rest offsets and proportions.
            dst_pb.keyframe_insert("rotation_quaternion", frame=frame, group=dst_name)
            dst_pb.keyframe_insert("location", frame=frame, group=dst_name)
            dst_pb.keyframe_insert("scale", frame=frame, group=dst_name)
        # Key fixed fist and static contextual bases so the action is self
        # contained when opened in another scene.
        for n, basis in fist_basis.items():
            pb = target_copy.pose.bones.get(n)
            if pb:
                pb.matrix_basis = basis
                pb.keyframe_insert("location", frame=frame, group=n)
                pb.keyframe_insert("rotation_quaternion", frame=frame, group=n)
                pb.keyframe_insert("scale", frame=frame, group=n)
        if args.body_context == "full_source":
            for n, basis in static_bases.items():
                if n in {x['target'] for x in bone_map}:
                    continue
                pb = target_copy.pose.bones.get(n)
                if pb:
                    pb.matrix_basis = basis
                    pb.keyframe_insert("location", frame=frame, group=n)
                    pb.keyframe_insert("rotation_quaternion", frame=frame, group=n)
                    pb.keyframe_insert("scale", frame=frame, group=n)

    # Dense linear target samples avoid Bezier overshoot between converted
    # poses; original source curves remain untouched on the separate control.
    for fc in fcurve_list(out_action, out_slot):
        for key in fc.keyframe_points:
            key.interpolation = 'LINEAR'
        fc.update()
    scene.frame_start, scene.frame_end = int(math.floor(first)), int(math.ceil(last))

    # Compare all mesh vertices in each rig's own armature space, removing
    # object/root translation, rotation, and scale before shape error metrics.
    original_weight_rows = [group_rows(m) for m in original_meshes]
    candidate_weight_rows = [group_rows(m) for m in mesh_copies]
    region_groups = profile['regions']
    seam_pairs = profile['seams']
    samples = {}
    all_d = []
    shape_all = []
    seam_rows = {name: [] for name in seam_pairs}
    rotation_rows = {row['target']: [] for row in bone_map}
    worst = {"frame": None, "distance": -1.0, "mesh": None, "vertex": None}
    fist_motion_max = 0.0
    fixed_fist_reference = {n: target_copy.pose.bones[n].matrix_basis.copy() for n in fist_names}
    for frame in frames:
        set_frame(scene, frame)
        mesh_metrics = {}
        for idx, (cand, ref, orig) in enumerate(zip(mesh_copies, reference_meshes, original_meshes)):
            a = armature_local_points(cand, target_copy)
            b = armature_local_points(ref, reference)
            if len(a) != len(b):
                raise RuntimeError(f"vertex count mismatch {cand.name}/{ref.name}")
            delta = [x-y for x, y in zip(a, b)]
            distances = [d.length for d in delta]
            mean = sum(delta, Vector()) / max(len(delta), 1)
            shape = [(d-mean).length for d in delta]
            all_d.extend(distances)
            shape_all.extend(shape)
            for vi, dist in enumerate(distances):
                if dist > worst['distance']:
                    worst = {"frame": frame, "distance": dist, "mesh": orig.name, "vertex": vi}
            groups = original_weight_rows[idx]
            cand_sets = {name: [point for point, row in zip(a, groups)
                                if row and max(row, key=row.get) == name]
                         for name in {g for pair in seam_pairs.values() for g in pair}}
            ref_sets = {name: [point for point, row in zip(b, groups)
                               if row and max(row, key=row.get) == name]
                        for name in {g for pair in seam_pairs.values() for g in pair}}
            regions = {}
            for region, group_names in region_groups.items():
                ids = [i for i, row in enumerate(groups) if any(row.get(g, 0.0) > 0.02 for g in group_names)]
                vals = [distances[i] for i in ids]
                regions[region] = {"vertex_count": len(ids),
                                   "rms": math.sqrt(sum(x*x for x in vals)/max(len(vals), 1)),
                                   "p95": percentile(vals, 0.95),
                                   "max": max(vals, default=0.0)}
            mesh_metrics[orig.name] = {
                "vertices": len(a), "rms_root_normalized": math.sqrt(sum(x*x for x in distances)/max(len(distances), 1)),
                "p95_root_normalized": percentile(distances, 0.95), "max_root_normalized": max(distances, default=0.0),
                "shape_only_rms": math.sqrt(sum(x*x for x in shape)/max(len(shape), 1)),
                "mean_delta": vec(mean), "regions": regions,
            }
            if idx == 0:
                for seam, (ga, gb) in seam_pairs.items():
                    c_gap = nearest_p95(cand_sets[ga], cand_sets[gb])
                    r_gap = nearest_p95(ref_sets[ga], ref_sets[gb])
                    if c_gap is not None and r_gap is not None:
                        seam_rows[seam].append({"frame": frame, "candidate_p95": c_gap,
                                                "reference_p95": r_gap,
                                                "delta": c_gap-r_gap})
        for row in bone_map:
            target_bone = row['target']
            ca = relative_pose_quat(target_copy, target_bone)
            rb = relative_pose_quat(reference, target_bone)
            angle = quat_angle_deg(ca, rb)
            if angle is not None:
                rotation_rows[target_bone].append({"frame": frame, "angle_deg": angle})
        samples[str(frame)] = mesh_metrics
        for n, basis in fixed_fist_reference.items():
            current = target_copy.pose.bones[n].matrix_basis
            err = max(abs(float(current[r][c])-float(basis[r][c])) for r in range(4) for c in range(4))
            fist_motion_max = max(fist_motion_max, err)
    summary = {"samples": len(frames), "frame_range": [first, last], "step": step,
               "rms_root_normalized": math.sqrt(sum(x*x for x in all_d)/max(len(all_d), 1)),
               "p95_root_normalized": percentile(all_d, 0.95),
               "max_root_normalized": max(all_d, default=0.0),
               "shape_only_rms": math.sqrt(sum(x*x for x in shape_all)/max(len(shape_all), 1)),
               "worst_vertex": worst, "fixed_fist_basis_error": fist_motion_max}
    summary["seam_nearest_surface_p95"] = {
        name: {"samples": len(rows),
               "candidate_max": max((r['candidate_p95'] for r in rows), default=None),
               "reference_max": max((r['reference_p95'] for r in rows), default=None),
               "worst_absolute_delta": max((abs(r['delta']) for r in rows), default=None),
               "worst_delta_frame": max(rows, key=lambda r: abs(r['delta']))['frame'] if rows else None}
        for name, rows in seam_rows.items()}
    summary["relative_bone_rotation_error_deg"] = {
        name: {"max": max((r['angle_deg'] for r in rows), default=None),
               "rms": math.sqrt(sum(r['angle_deg']**2 for r in rows)/len(rows)) if rows else None,
               "worst_frame": max(rows, key=lambda r:r['angle_deg'])['frame'] if rows else None}
        for name, rows in rotation_rows.items()}

    # Restore mapped frame and render the actual skinned candidate with the
    # original Waluigi materials from the cloned meshes.
    render_frames = args.render_frames or sorted(set([first, first+(last-first)*0.25,
        (first+last)*0.5, first+(last-first)*0.75, last]))
    from prototype_galaxy_skeleton_skinbind import render_previews
    render_files, render_failures = render_previews(scene, target_copy, mesh_copies,
        [int(round(x)) for x in render_frames], args.renders)

    # Hide every other character/reference in the saved disposable preview.
    # Keep original animated control data in the file, hidden by default; only
    # one Waluigi body/rig is visible for ordinary viewport review.
    visible = {target_copy, *mesh_copies}
    for obj in bpy.data.objects:
        show = obj in visible
        obj.hide_viewport = not show
        obj.hide_render = not show
        obj.hide_set(not show)
    for obj in visible:
        obj.hide_viewport = obj.hide_render = False
        obj.hide_set(False)
    scene.frame_set(int(round(first)))
    bpy.context.view_layer.update()
    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(args.output))

    report = {
        "schema": "mg1.galaxy-waluigi-hybrid-adapter.v1",
        "input_blend": input_blend,
        "source_action": action.name,
        "mapping_mode": args.mapping_mode,
        "body_context": args.body_context,
        "source_action_owner_slot": src_slot.identifier,
        "source_curve_count": len(src_curves),
        "source_curve_copy_error": action_copy_error(action, ctrl_action, src_slot, ctrl_slot,
                                                      first, last),
        "source_control_rig": control.name,
        "source_control_bones": len(control.data.bones),
        "target_deform_rig": target_copy.name,
        "target_deform_bones": len(target_copy.data.bones),
        "target_meshes": [m.name for m in mesh_copies],
        "source_group_counts": [len(m.vertex_groups) for m in original_meshes],
        "candidate_group_counts": [len(m.vertex_groups) for m in mesh_copies],
        "group_membership_preserved": [
            [g.name for g in m.vertex_groups] == [g.name for g in c.vertex_groups]
            and source_rows == candidate_rows
            for m, c, source_rows, candidate_rows in zip(
                original_meshes, mesh_copies, original_weight_rows, candidate_weight_rows)],
        "rest_similarity": rows_mat(transform), "fit_scale": fit_scale,
        "rest_landmark_errors": rest_landmarks,
        "deform_control_map": [{"source": s, "target": t, "carry_translation": tr,
                                "carry_rotation": rot}
                               for s, t, tr, rot in maps],
        "static_fist_bones": sorted(fist_basis),
        "target_action": out_action.name,
        "target_action_slot": out_slot.identifier,
        "target_action_curve_count": len(fcurve_list(out_action, out_slot)),
        "source_evaluated_sample_frame_15": {
            name: [float(x) for x in source_samples[min(source_samples, key=lambda x: abs(x-15.0))][name]["basis"].to_quaternion()]
            for name in ("ArmL1", "ArmL2", "HandL") if name in source_samples[min(source_samples, key=lambda x: abs(x-15.0))]
        },
        "root_normalized_comparison": summary,
        "per_frame": samples,
        "seam_metrics_per_frame": seam_rows,
        "relative_rotation_metrics_per_bone": rotation_rows,
        "preview": os.path.abspath(args.output),
        "render_files": render_files,
        "render_failures": render_failures,
        "limitations": [
            "The mapping is a deterministic per-bone rest-axis conversion; it does not reproduce learned native branch-relative motion.",
            "Target output is densely baked at the configured sample interval; the separate Galaxy control action remains curve-identical.",
            "Closed fists are held at the first-frame validated V2 digit pose; dynamic finger articulation is intentionally absent.",
            "A geometry match to V2 does not itself establish human visual approval."
        ]
    }
    with open(args.report, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2)
    print("HYBRID_ADAPTER_REPORT=" + os.path.abspath(args.report))
    print("HYBRID_ADAPTER_PREVIEW=" + os.path.abspath(args.output))
    print("HYBRID_ADAPTER_SUMMARY=" + json.dumps(summary, separators=(',', ':')))


if __name__ == '__main__':
    main()
