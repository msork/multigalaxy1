"""Build a clean-basis no-op preview from the validated Arm_Test walk.

Run in Blender background mode on a saved recovery scene. The production rig,
action, and source meshes are sampled only; every preview is a duplicate.
"""
import argparse
import json
import os
import sys

import bpy
from mathutils import Matrix, Vector

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from convert_native_owner_actions import duplicate_rig


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-rig", default="Waluigi_Arm_Test")
    parser.add_argument("--clean-rig", default="Waluigi_Arm_Clean_Backup")
    parser.add_argument("--walk-action", default="Waluigi_GalaxyWalk_Legs_Baked")
    parser.add_argument("--walk-slot", default="")
    parser.add_argument("--mesh", action="append", default=[])
    parser.add_argument("--head-group", default="bone_53")
    parser.add_argument("--foot-bones", nargs=2, default=["bone_8", "bone_14"])
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--end", type=int, default=60)
    parser.add_argument("--preview", required=True)
    parser.add_argument("--baseline-report", required=True)
    parser.add_argument("--pose-tolerance", type=float, default=1e-5)
    parser.add_argument("--vertex-tolerance", type=float, default=1e-4)
    return parser.parse_args(argv)


def mat_error(a, b):
    return max(abs(float(a[r][c]) - float(b[r][c]))
               for r in range(4) for c in range(4))


def vec_rows(v):
    return [float(v.x), float(v.y), float(v.z)]


def matrix_rows(m):
    return [[float(m[r][c]) for c in range(4)] for r in range(4)]


def duplicate_mesh(source, target_rig, collection, prefix):
    dup = source.copy()
    dup.data = source.data.copy()
    dup.name = prefix + source.name
    dup.data.name = prefix + source.data.name
    collection.objects.link(dup)
    dup.parent = target_rig
    dup.parent_type = 'OBJECT'
    dup.parent_bone = ''
    dup.matrix_parent_inverse = source.matrix_parent_inverse.copy()
    # Preserve the mesh's authored parent-relative transform.  matrix_world
    # can be stale in reopened workfiles when its original parent armature has
    # the intentional 43.437 scale in matrix_basis but an identity evaluated
    # matrix_world.  Copying that stale world matrix under the correctly scaled
    # preview rig introduces a compensating 1/43.437 object scale and collapses
    # the skinned character near the origin.
    dup.matrix_basis = source.matrix_basis.copy()
    # Evaluate skinned copies immediately.  A copied source object may inherit
    # per-object hide state; Blender can leave its depsgraph transform stale
    # until the final save-time visibility pass otherwise.
    dup.hide_viewport = False
    dup.hide_render = False
    dup.hide_set(False)
    for mod in dup.modifiers:
        if mod.type == 'ARMATURE' and mod.object is not None:
            mod.object = target_rig
    for constraint in dup.constraints:
        if hasattr(constraint, 'target') and constraint.target == source.parent:
            constraint.target = target_rig
    return dup


def copy_action_slot(action, source_slot, target, name):
    bags = [bag for layer in action.layers for strip in layer.strips
            for bag in strip.channelbags if bag.slot == source_slot]
    if len(bags) != 1:
        raise RuntimeError(f"expected one action channelbag for {source_slot.identifier}")
    out = bpy.data.actions.new(name)
    out.use_fake_user = True
    slot = out.slots.new('OBJECT', target.name)
    layer = out.layers.new("Validated walk channels on clean Waluigi basis")
    strip = layer.strips.new(type='KEYFRAME')
    bag = strip.channelbag(slot, ensure=True)
    for curve in bags[0].fcurves:
        # The walk is baked. Old IK pole-angle curves are debug remnants and
        # must not add a constraint-driven layer to the preview.
        if '.constraints[' in curve.data_path:
            continue
        kwargs = {'index': curve.array_index}
        if curve.group:
            kwargs['group_name'] = curve.group.name
        copy = bag.fcurves.new(curve.data_path, **kwargs)
        copy.keyframe_points.add(len(curve.keyframe_points))
        for p, q in zip(curve.keyframe_points, copy.keyframe_points):
            q.co = p.co
            q.interpolation = p.interpolation
            q.handle_left_type = p.handle_left_type
            q.handle_right_type = p.handle_right_type
            q.handle_left = p.handle_left
            q.handle_right = p.handle_right
        copy.update()
    target.animation_data_create()
    target.animation_data.action = out
    target.animation_data.action_slot = slot
    return out


def evaluated_vertices(objects):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    output = []
    for obj in objects:
        ev = obj.evaluated_get(depsgraph)
        mesh = ev.to_mesh()
        try:
            output.append([ev.matrix_world @ v.co for v in mesh.vertices])
        finally:
            ev.to_mesh_clear()
    return output


def weighted_group_centroid(objects, evaluated, group_name):
    weighted_sum = [0.0, 0.0, 0.0]
    total = 0.0
    for obj, points in zip(objects, evaluated):
        group = obj.vertex_groups.get(group_name)
        if group is None:
            continue
        by_index = {v.index: v for v in obj.data.vertices}
        for index, vertex in by_index.items():
            try:
                weight = group.weight(index)
            except RuntimeError:
                continue
            for axis in range(3):
                weighted_sum[axis] += float(points[index][axis]) * weight
            total += weight
    return [value / total for value in weighted_sum] if total else None


def foot_positions(rig, names):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    ev = rig.evaluated_get(depsgraph)
    result = {}
    for name in names:
        if name not in ev.pose.bones:
            raise RuntimeError(f"foot landmark bone missing: {name}")
        result[name] = vec_rows(ev.matrix_world @ ev.pose.bones[name].tail)
    return result


def max_point_error(a, b):
    return max(((x - y).length for xa, xb in zip(a, b) for x, y in zip(xa, xb)), default=0.0)


def main():
    args = parse_args()
    scene = bpy.context.scene
    baseline = bpy.data.objects.get(args.baseline_rig)
    clean = bpy.data.objects.get(args.clean_rig)
    action = bpy.data.actions.get(args.walk_action)
    if not baseline or baseline.type != 'ARMATURE' or not clean or clean.type != 'ARMATURE' or action is None:
        raise RuntimeError("walk baseline, clean Waluigi rig, or validated walk action is missing")
    slots = [s for s in action.slots if not args.walk_slot or s.identifier == args.walk_slot]
    if len(slots) != 1:
        raise RuntimeError("walk action slot is ambiguous")
    source_slot = slots[0]
    source_meshes = [bpy.data.objects[n] for n in args.mesh] if args.mesh else sorted(
        [o for o in bpy.data.objects if o.name.startswith('ARMTEST_') and o.type == 'MESH'],
        key=lambda o: o.name)
    source_meshes = [o for o in source_meshes if any(m.type == 'ARMATURE' and m.object == baseline for m in o.modifiers)]
    if not source_meshes:
        raise RuntimeError("no full-character ARMTEST meshes bound to the selected walk baseline")
    if abs(float(baseline.matrix_world.to_scale().x) - 43.437) > 0.01:
        raise RuntimeError("validated walk rig does not have its expected 43.437 world scale")
    if set(clean.data.bones.keys()) != set(baseline.data.bones.keys()):
        raise RuntimeError("clean rig and walk rig have different bone sets")
    for bone in clean.data.bones:
        other = baseline.data.bones[bone.name]
        if (bone.parent.name if bone.parent else None) != (other.parent.name if other.parent else None):
            raise RuntimeError(f"parent mismatch against clean basis: {bone.name}")
        if mat_error(bone.matrix_local, other.matrix_local) > 1e-6:
            raise RuntimeError(f"rest matrix mismatch against clean basis: {bone.name}")

    frames = list(range(args.start, args.end + 1))
    old_frame, old_subframe = scene.frame_current, scene.frame_subframe
    old_objects = set(bpy.data.objects)
    old_actions = set(bpy.data.actions)
    old_collections = set(bpy.data.collections)
    preview_collection = bpy.data.collections.new("ValidatedWalk_NoOp_Preview")
    reference_collection = bpy.data.collections.new("ValidatedWalk_NoOp_Reference")
    scene.collection.children.link(preview_collection)
    scene.collection.children.link(reference_collection)
    try:
        # The clean rig is the pose baseline; the validated walk rig supplies
        # object placement and exact mesh objects. No stale production pose is
        # sampled or copied.
        preview_rig = duplicate_rig(clean, "ValidatedWalk_NoOp_Preview_Rig")
        reference_rig = duplicate_rig(clean, "ValidatedWalk_NoOp_Reference_Rig")
        for rig, coll in ((preview_rig, preview_collection), (reference_rig, reference_collection)):
            for c in list(rig.users_collection):
                c.objects.unlink(rig)
            coll.objects.link(rig)
            rig.matrix_world = baseline.matrix_world.copy()
            rig.animation_data_clear()
            for pb in rig.pose.bones:
                pb.matrix_basis = Matrix.Identity(4)
        preview_meshes = [duplicate_mesh(o, preview_rig, preview_collection, "ValidatedWalk_NoOp_Preview_") for o in source_meshes]
        reference_meshes = [duplicate_mesh(o, reference_rig, reference_collection, "ValidatedWalk_NoOp_Reference_") for o in source_meshes]
        preview_action = copy_action_slot(action, source_slot, preview_rig, "Waluigi_ValidatedWalk_NoOp_v2")
        reference_action = copy_action_slot(action, source_slot, reference_rig, "Waluigi_ValidatedWalk_Reference_v2")
        # Clear copied constraints on preview duplicates; this action is baked.
        constraint_counts = {}
        for rig in (preview_rig, reference_rig):
            for pb in rig.pose.bones:
                constraint_counts[pb.name] = len(pb.constraints)
                for c in list(pb.constraints):
                    pb.constraints.remove(c)

        max_pose = {"error": 0.0}
        max_vertex = {"error": 0.0}
        max_foot = 0.0
        max_cap = 0.0
        cap_positions, foot_tracks = [], {name: [] for name in args.foot_bones}
        source_pose_error = {"error": 0.0}
        sample_count = 0
        for frame in frames:
            scene.frame_set(frame)
            bpy.context.view_layer.update()
            depsgraph = bpy.context.evaluated_depsgraph_get()
            source_rig_ev = baseline.evaluated_get(depsgraph)
            ref_ev = reference_rig.evaluated_get(depsgraph)
            prev_ev = preview_rig.evaluated_get(depsgraph)
            for pb in prev_ev.pose.bones:
                err = mat_error(pb.matrix, ref_ev.pose.bones[pb.name].matrix)
                if err > max_pose['error']:
                    max_pose = {"error": err, "frame": frame, "bone": pb.name}
            # Compare the rebound, clean-basis replay against the actual
            # validated Waluigi_Arm_Test walk before hiding the source.
            for name in source_rig_ev.pose.bones.keys():
                err = mat_error(source_rig_ev.pose.bones[name].matrix, ref_ev.pose.bones[name].matrix)
                if err > source_pose_error['error']:
                    source_pose_error = {"error": err, "frame": frame, "bone": name}
            ref_verts = evaluated_vertices(reference_meshes)
            prev_verts = evaluated_vertices(preview_meshes)
            src_verts = evaluated_vertices(source_meshes)
            for mesh_i in range(len(source_meshes)):
                if len(src_verts[mesh_i]) != len(ref_verts[mesh_i]):
                    raise RuntimeError(f"vertex count mismatch for {source_meshes[mesh_i].name}")
                for vi, (a, b) in enumerate(zip(src_verts[mesh_i], ref_verts[mesh_i])):
                    error = float((a - b).length)
                    sample_count += 1
                    if error > max_vertex['error']:
                        max_vertex = {"error": error, "frame": frame,
                            "mesh": source_meshes[mesh_i].name, "vertex": vi}
            for mesh_i, (ref_points, prev_points) in enumerate(zip(ref_verts, prev_verts)):
                for vi, (a, b) in enumerate(zip(ref_points, prev_points)):
                    error = float((a - b).length)
                    if error > max_vertex['error'] and error > args.vertex_tolerance:
                        max_vertex = {"error": error, "frame": frame,
                            "mesh": preview_meshes[mesh_i].name, "vertex": vi}
            src_feet = foot_positions(baseline, args.foot_bones)
            ref_feet = foot_positions(reference_rig, args.foot_bones)
            for name in args.foot_bones:
                foot_tracks[name].append(ref_feet[name])
                e = (Vector(src_feet[name]) - Vector(ref_feet[name])).length
                max_foot = max(max_foot, float(e))
            src_cap = weighted_group_centroid(source_meshes, src_verts, args.head_group)
            ref_cap = weighted_group_centroid(reference_meshes, ref_verts, args.head_group)
            if src_cap is not None and ref_cap is not None:
                cap_positions.append(ref_cap)
                e = (Vector(src_cap) - Vector(ref_cap)).length
                max_cap = max(max_cap, float(e))

        # Keep only the preview visible when opening the saved file.
        reference_collection.hide_viewport = True
        reference_collection.hide_render = True
        for obj in list(reference_collection.objects):
            obj.hide_viewport = True
            obj.hide_render = True
            obj.hide_set(True)
        for obj in list(bpy.data.objects):
            if obj not in old_objects:
                continue
            obj.hide_set(True)
            obj.hide_viewport = True
            obj.hide_render = True
        for obj in [preview_rig] + preview_meshes:
            obj.hide_viewport = False
            obj.hide_render = False
            obj.hide_set(False)
        preview_collection.hide_viewport = False
        preview_collection.hide_render = False
        scene.frame_set(args.start)
        bpy.context.view_layer.update()
        report = {
            "schema": "mg1.validated-walk-noop-baseline.v1",
            "baseline_rig": baseline.name,
            "clean_pose_basis": clean.name,
            "action": action.name,
            "action_source_slot": source_slot.identifier,
            "preview_action": preview_action.name,
            "preview_slot": preview_action.slots[0].identifier,
            "frames": frames,
            "mesh_sources": [o.name for o in source_meshes],
            "preview_meshes": [o.name for o in preview_meshes],
            "rig_world_scale": list(preview_rig.matrix_world.to_scale()),
            "action_pose_channel_paths": sorted({fc.data_path for l in action.layers for st in l.strips
                for bag in st.channelbags if bag.slot == source_slot for fc in bag.fcurves
                if '.constraints[' not in fc.data_path}),
            "source_comparison": {"max_pose_matrix_error": source_pose_error,
                "max_vertex_world_error": max_vertex["error"],
                "worst_vertex": max_vertex, "max_foot_tail_error": max_foot,
                "head_cap_weighted_centroid_group": args.head_group,
                "max_head_cap_centroid_error": max_cap,
                "vertex_samples": sample_count},
            "duplicate_consistency": {"max_pose_matrix_error": max_pose,
                "max_foot_tail_error": max_foot, "max_head_cap_centroid_error": max_cap},
            "foot_tracks_world": foot_tracks,
            "head_cap_centroid_track_world": cap_positions,
            "clean_constraints_removed_from_duplicates": sum(constraint_counts.values()),
            "production_mutated": False,
            "gate": "pass" if source_pose_error['error'] <= args.pose_tolerance and
                max_vertex['error'] <= args.vertex_tolerance and max_foot <= args.pose_tolerance and
                max_cap <= args.vertex_tolerance else "fail"}
        os.makedirs(os.path.dirname(os.path.abspath(args.baseline_report)), exist_ok=True)
        with open(args.baseline_report, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, allow_nan=False)
            f.write('\n')
        os.makedirs(os.path.dirname(os.path.abspath(args.preview)), exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(args.preview))
        print('MG1_VALIDATED_WALK_NOOP=' + json.dumps(report))
        if report['gate'] != 'pass':
            raise RuntimeError('clean-basis no-op does not match validated walk baseline')
    finally:
        scene.frame_set(old_frame, subframe=old_subframe)
        for obj in list(bpy.data.objects):
            if obj in old_objects:
                continue
            data = obj.data
            typ = obj.type
            bpy.data.objects.remove(obj, do_unlink=True)
            if data and data.users == 0:
                if typ == 'MESH': bpy.data.meshes.remove(data)
                elif typ == 'ARMATURE': bpy.data.armatures.remove(data)
        for act in list(bpy.data.actions):
            if act not in old_actions: bpy.data.actions.remove(act, do_unlink=True)
        for coll in list(bpy.data.collections):
            if coll not in old_collections: bpy.data.collections.remove(coll)


if __name__ == '__main__':
    main()
