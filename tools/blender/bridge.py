"""Loopback JSON-lines inspection bridge for Blender 5.x.

Run in Blender's Python Console: exec(compile(open(r'.../bridge.py').read(), 'bridge.py', 'exec'))
All bpy access is dispatched onto Blender's main thread.
"""
import json
import queue
import math
import os
import re
import socket
import threading
import uuid

import bpy
from mathutils import Matrix, Vector

try:
    from .rotation_math import angular_distance, pearson, relative
except ImportError:
    try:
        from rotation_math import angular_distance, pearson, relative
    except ImportError:
        import math
        def _qnorm(q):
            n = math.sqrt(sum(float(v)*float(v) for v in q))
            if n <= 1e-15:
                raise ValueError("zero-length quaternion")
            return tuple(float(v)/n for v in q)
        def angular_distance(a, b):
            cosine = abs(sum(x*y for x,y in zip(_qnorm(a), _qnorm(b))))
            return 2*math.acos(min(1.0, max(-1.0, cosine)))
        def relative(a, b):
            aw, ax, ay, az = _qnorm(a)
            bw, bx, by, bz = _qnorm(b)
            inv_a = (aw, -ax, -ay, -az)
            q = (inv_a[0]*bw-inv_a[1]*bx-inv_a[2]*by-inv_a[3]*bz,
                 inv_a[0]*bx+inv_a[1]*bw+inv_a[2]*bz-inv_a[3]*by,
                 inv_a[0]*by-inv_a[1]*bz+inv_a[2]*bw+inv_a[3]*bx,
                 inv_a[0]*bz+inv_a[1]*by-inv_a[2]*bx+inv_a[3]*bw)
            return _qnorm(q)
        def pearson(a, b):
            if len(a) != len(b) or len(a) < 2:
                return None
            ma, mb = sum(a)/len(a), sum(b)/len(b)
            da, db = [x-ma for x in a], [x-mb for x in b]
            denom = math.sqrt(sum(x*x for x in da)*sum(x*x for x in db))
            return sum(x*y for x,y in zip(da,db))/denom if denom > 1e-15 else None

PROTOCOL_VERSION = 1
HOST = "127.0.0.1"
PORT = 47831
TAG = "_mg1_bridge_experiment"
_requests = queue.Queue()
_server = None
_running = False


def _mat(matrix):
    return [[float(matrix[r][c]) for c in range(4)] for r in range(4)]


def _object(name):
    obj = bpy.data.objects.get(name)
    if obj is None:
        raise ValueError("object not found: " + name)
    return obj


def _inspect_objects(_args):
    return [{"name": o.name, "type": o.type,
             "data": o.data.name if o.data else None,
             "location": list(o.location), "rotation_euler": list(o.rotation_euler),
             "scale": list(o.scale), "bridge_experiment": bool(o.get(TAG, False))}
            for o in bpy.data.objects]


def _inspect_armature(args):
    obj = _object(args["object"])
    if obj.type != "ARMATURE":
        raise ValueError("object is not an armature")
    return {"name": obj.name, "data": obj.data.name, "matrix_world": _mat(obj.matrix_world),
            "bones": [{"name": b.name, "parent": b.parent.name if b.parent else None,
                       "head_local": list(b.head_local), "tail_local": list(b.tail_local),
                       "matrix_local": _mat(b.matrix_local), "use_connect": b.use_connect,
                       "use_deform": b.use_deform} for b in obj.data.bones]}


def _inspect_weights(args):
    obj = _object(args["object"])
    if obj.type != "MESH":
        raise ValueError("object is not a mesh")
    requested = set(args.get("groups", []))
    groups = {g.index: g.name for g in obj.vertex_groups}
    counts = {g.name: {"weighted_vertices": 0, "total_weight": 0.0} for g in obj.vertex_groups
              if not requested or g.name in requested}
    for vertex in obj.data.vertices:
        for assignment in vertex.groups:
            name = groups.get(assignment.group)
            if name in counts and assignment.weight > 0:
                counts[name]["weighted_vertices"] += 1
                counts[name]["total_weight"] += float(assignment.weight)
    return {"mesh": obj.name, "groups": counts}


def _inspect_actions(_args):
    result = []
    for action in bpy.data.actions:
        curves = []
        if hasattr(action, "layers"):
            for layer in action.layers:
                for strip in layer.strips:
                    for bag in getattr(strip, "channelbags", []):
                        curves.extend((fc, layer.name, getattr(bag, "slot", None)) for fc in bag.fcurves)
        if hasattr(action, "fcurves"):
            curves.extend((fc, None, None) for fc in action.fcurves)
        result.append({"name": action.name, "frame_range": list(action.frame_range),
                       "layers": [l.name for l in action.layers] if hasattr(action, "layers") else [],
                       "fcurves": [{"data_path": fc.data_path, "array_index": fc.array_index,
                                    "keyframes": [[float(k.co.x), float(k.co.y)] for k in fc.keyframe_points],
                                    "layer": layer, "slot": str(slot) if slot is not None else None}
                                   for fc, layer, slot in curves]})
    return result


def _inspect_pose(args):
    obj = _object(args["object"])
    if obj.type != "ARMATURE":
        raise ValueError("object is not an armature")
    scene = bpy.context.scene
    old_frame, old_subframe = scene.frame_current, scene.frame_subframe
    try:
        scene.frame_set(int(args["frame"]), subframe=float(args.get("subframe", 0.0)))
        bpy.context.view_layer.update()
        return {"object": obj.name, "frame": scene.frame_current,
                "bones": [{"name": p.name, "matrix": _mat(p.matrix),
                           "matrix_basis": _mat(p.matrix_basis)} for p in obj.pose.bones]}
    finally:
        scene.frame_set(old_frame, subframe=old_subframe)


_ARM_BONES = {
    "left": ["bone_22", "bone_23", "bone_24", "bone_25", "bone_26", "bone_27", "bone_29", "bone_30"],
}


def _quat(q):
    return [float(v) for v in q]


def _custom_props(id_block):
    def plain(value):
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        if hasattr(value, "to_list"):
            return value.to_list()
        if hasattr(value, "keys"):
            return {k: plain(value[k]) for k in value.keys()}
        try:
            return [plain(v) for v in value]
        except TypeError:
            return str(value)
    return {key: plain(id_block[key]) for key in id_block.keys() if key != "_RNA_UI"}


def _action_curves(action):
    result = []
    if hasattr(action, "layers"):
        for layer in action.layers:
            for strip in layer.strips:
                for bag in getattr(strip, "channelbags", []):
                    result.extend((fc, layer.name, bag.slot) for fc in bag.fcurves)
    if hasattr(action, "fcurves"):
        result.extend((fc, None, None) for fc in action.fcurves)
    return result


def _armature_action_bag(action, slot):
    curves = []
    for fc, layer, bag_slot in _action_curves(action):
        if bag_slot is None or slot is None or bag_slot == slot:
            curves.append((fc, layer))
    return curves


def _analyze_waluigi_arm(args):
    obj = _object(args.get("object", "123475520_waluigi.gpl"))
    if obj.type != "ARMATURE":
        raise ValueError("object is not an armature")
    side = args.get("side", "left").lower()
    if side not in _ARM_BONES:
        raise ValueError("only side=left is currently supported")
    names = args.get("bones", _ARM_BONES[side])
    missing = [n for n in names if obj.data.bones.get(n) is None]
    if missing:
        raise ValueError("armature is missing requested bones: " + ", ".join(missing))

    action = bpy.data.actions.get(args["action"]) if args.get("action") else None
    if args.get("action") and action is None:
        raise ValueError("action not found: " + args["action"])
    curves_by_bone = {name: {} for name in names}
    action_slot = obj.animation_data.action_slot if obj.animation_data and obj.animation_data.action == action else None
    if action and action_slot is None and hasattr(action, "slots"):
        action_slot = next((s for s in action.slots if getattr(s, "target_id_type", "") == "OBJECT" and
                            (obj.name in getattr(s, "name", "") or obj.name in getattr(s, "identifier", ""))), None)
        if action_slot is None:
            action_slot = next((s for s in action.slots if getattr(s, "target_id_type", "") == "OBJECT"), None)
        if action_slot is None and len(action.slots):
            raise ValueError("action has no object-compatible slot")
    if action:
        for fc, layer in _armature_action_bag(action, action_slot):
            match = re.match(r'^pose\.bones\["([^"]+)"\]\.([A-Za-z_]+)$', fc.data_path)
            if match and match.group(1) in curves_by_bone:
                curves_by_bone[match.group(1)].setdefault(match.group(2), []).append((fc, layer))

    meshes = []
    mesh_names = args.get("meshes")
    candidates = [_object(n) for n in mesh_names] if mesh_names else [o for o in bpy.data.objects if o.type == "MESH"]
    for mesh in candidates:
        if mesh.type != "MESH":
            raise ValueError("weight source is not a mesh: " + mesh.name)
        group_indices = {g.index: g.name for g in mesh.vertex_groups if g.name in names}
        if not group_indices:
            continue
        per_bone = {name: {"weighted_vertex_count": 0, "weight_sum": 0.0, "weighted_position_sum": Vector((0,0,0)),
                           "points": []} for name in names}
        mesh_to_arm = obj.matrix_world.inverted_safe() @ mesh.matrix_world
        for vertex in mesh.data.vertices:
            point = mesh_to_arm @ vertex.co
            for membership in vertex.groups:
                bone_name = group_indices.get(membership.group)
                if bone_name and membership.weight > 0:
                    slot = per_bone[bone_name]
                    w = float(membership.weight)
                    slot["weighted_vertex_count"] += 1
                    slot["weight_sum"] += w
                    slot["weighted_position_sum"] += point * w
                    slot["points"].append(tuple(float(x) for x in point))
        meshes.append((mesh.name, per_bone))

    weight_results = {}
    for name in names:
        count, total, pos_sum, points = 0, 0.0, Vector((0,0,0)), []
        by_mesh = {}
        for mesh_name, all_bones in meshes:
            slot = all_bones[name]
            by_mesh[mesh_name] = {"weighted_vertex_count": slot["weighted_vertex_count"], "weight_sum": slot["weight_sum"]}
            count += slot["weighted_vertex_count"]
            total += slot["weight_sum"]
            pos_sum += slot["weighted_position_sum"]
            points.extend(slot["points"])
        centroid = list(pos_sum / total) if total > 1e-15 else None
        bounds = {"min": [min(p[i] for p in points) for i in range(3)],
                  "max": [max(p[i] for p in points) for i in range(3)]} if points else None
        weight_results[name] = {"weighted_vertex_count": count, "weight_sum": total, "zero_weights": count == 0,
                                "weighted_centroid_armature_space": centroid, "weighted_bounds_armature_space": bounds,
                                "per_mesh": by_mesh}

    scene = bpy.context.scene
    old_frame, old_subframe = scene.frame_current, scene.frame_subframe
    eval_obj = obj
    temp_obj = None
    if action:
        temp_obj = obj.copy()
        temp_obj.data = obj.data.copy()
        temp_obj.name = "_MG1_ArmAnalysis_Temporary"
        temp_obj.data.name = "_MG1_ArmAnalysis_Temporary_Data"
        try:
            scene.collection.objects.link(temp_obj)
            eval_obj = temp_obj
            eval_obj.animation_data_create()
            eval_obj.animation_data.action = action
            if hasattr(eval_obj.animation_data, "action_slot"):
                if action_slot is None:
                    action_slot = next((s for s in action.slots if getattr(s, "target_id_type", "") == "OBJECT"), None)
                if action_slot is None and getattr(action, "slots", None):
                    raise ValueError("action has no object-compatible slot")
                eval_obj.animation_data.action_slot = action_slot
        except Exception:
            data = temp_obj.data
            bpy.data.objects.remove(temp_obj, do_unlink=True)
            if data.users == 0:
                bpy.data.armatures.remove(data)
            raise
    try:
        if action:
            start, end = float(action.frame_range[0]), float(action.frame_range[1])
            if args.get("frames") is not None:
                frames = [float(f) for f in args["frames"]]
                if not frames:
                    raise ValueError("frames must not be empty")
            else:
                count_samples = max(2, int(args.get("sample_count", 24)))
                frames = [start + (end-start)*i/(count_samples-1) for i in range(count_samples)] if end > start else [start]
        else:
            frames = [float(f) for f in args.get("frames", [scene.frame_current])]
        samples, rotations = [], {name: {"local": [], "armature": [], "world": []} for name in names}
        for frame in frames:
            base = math.floor(frame)
            scene.frame_set(int(base), subframe=frame-base)
            bpy.context.view_layer.update()
            sample_bones = {}
            for name in names:
                pose = eval_obj.pose.bones[name]
                local_q = pose.matrix_basis.to_quaternion().normalized()
                arm_q = pose.matrix.to_quaternion().normalized()
                world_q = (eval_obj.matrix_world @ pose.matrix).to_quaternion().normalized()
                rotations[name]["local"].append(_quat(local_q))
                rotations[name]["armature"].append(_quat(arm_q))
                rotations[name]["world"].append(_quat(world_q))
                sample_bones[name] = {"local_rotation": _quat(local_q), "armature_rotation": _quat(arm_q),
                                      "world_rotation": _quat(world_q), "matrix_basis": _mat(pose.matrix_basis),
                                      "matrix_armature": _mat(pose.matrix)}
            samples.append({"frame": frame, "bones": sample_bones})

        bone_results = {}
        for name in names:
            bone = obj.data.bones[name]
            channels = {}
            key_count = 0
            for path, group in curves_by_bone[name].items():
                channel_data = []
                for fc, layer in group:
                    values = [float(k.co.y) for k in fc.keyframe_points]
                    key_count += len(fc.keyframe_points)
                    channel_data.append({"array_index": fc.array_index, "key_count": len(values),
                                         "value_range": [min(values), max(values)] if values else None,
                                         "keyframes": [[float(k.co.x), float(k.co.y)] for k in fc.keyframe_points],
                                         "layer": layer})
                channels[path] = channel_data
            angular = {}
            for space in ("local", "armature", "world"):
                quats = rotations[name][space]
                angular[space] = [math.degrees(angular_distance(quats[0], q)) for q in quats]
            rest_q = bone.matrix_local.to_quaternion().normalized()
            direction = (bone.tail_local - bone.head_local)
            bone_results[name] = {"parent": bone.parent.name if bone.parent else None,
                                  "children": [c.name for c in bone.children],
                                  "rest_head": list(bone.head_local), "rest_tail": list(bone.tail_local),
                                  "rest_length": float(bone.length), "rest_matrix": _mat(bone.matrix_local),
                                  "rest_quaternion": _quat(rest_q), "rest_direction": list(direction.normalized()) if direction.length else None,
                                  "action_channels": channels, "key_count": key_count,
                                  "maximum_angular_displacement_degrees": {k: max(v) if v else 0.0 for k,v in angular.items()},
                                  "metadata": _custom_props(bone), "track_id": bone.get("TrackId", bone.get("track_id"))}

        adjacent_angles = []
        for name in names:
            bone = obj.data.bones[name]
            if bone.parent and bone.parent.name in names:
                a = bone.parent.tail_local - bone.parent.head_local
                b = bone.tail_local - bone.head_local
                angle = math.degrees(a.angle(b)) if a.length and b.length else None
                adjacent_angles.append({"parent": bone.parent.name, "child": name, "angle_degrees": angle})

        pairs = []
        pair_by_name = {}
        for i, a_name in enumerate(names):
            for b_name in names[i+1:]:
                a_local = [math.degrees(angular_distance(rotations[a_name]["local"][0], q)) for q in rotations[a_name]["local"]]
                b_local = [math.degrees(angular_distance(rotations[b_name]["local"][0], q)) for q in rotations[b_name]["local"]]
                a_arm, b_arm = rotations[a_name]["armature"], rotations[b_name]["armature"]
                rels = [relative(a, b) for a,b in zip(a_arm,b_arm)]
                rel_change = [math.degrees(angular_distance(rels[0], q)) for q in rels]
                pair_result = {"bone_a": a_name, "bone_b": b_name,
                              "local_angular_displacement_correlation": pearson(a_local, b_local),
                              "relative_orientation_change_degrees": rel_change,
                              "maximum_relative_orientation_change_degrees": max(rel_change) if rel_change else 0.0,
                              "relative_orientation_change_range_degrees": [min(rel_change), max(rel_change)] if rel_change else None}
                pairs.append(pair_result)
                pair_by_name[(a_name, b_name)] = pair_result

        overlap_pairs = {(names.index("bone_23"), names.index("bone_25")),
                         (names.index("bone_26"), names.index("bone_29"))} if all(
                             n in names for n in ("bone_23", "bone_25", "bone_26", "bone_29")) else set()
        for i, name in enumerate(names):
            bone = obj.data.bones[name]
            relevant = {c.name for c in bone.children}
            if bone.parent:
                relevant.add(bone.parent.name)
            relevant.intersection_update(names)
            relevant.update(names[j] for pair in overlap_pairs if i in pair for j in pair if j != i)
            relative_map = {}
            for neighbor in sorted(relevant):
                key = (name, neighbor) if names.index(name) < names.index(neighbor) else (neighbor, name)
                if key in pair_by_name:
                    pair = pair_by_name[key]
                    relative_map[neighbor] = {"maximum_change_degrees": pair["maximum_relative_orientation_change_degrees"],
                                              "sampled_change_degrees": pair["relative_orientation_change_degrees"]}
            bone_results[name]["relative_orientation_to_relevant_bones"] = relative_map

        return {"object": obj.name, "side": side, "action": action.name if action else None,
                "action_slot": getattr(action_slot, "identifier", None) if action_slot else None,
                "action_frame_range": list(action.frame_range) if action else None, "sample_frames": frames,
                "object_scale": list(obj.scale), "production_object_read_only": True,
                "bone_analysis": bone_results, "weights": weight_results, "adjacent_rest_angles": adjacent_angles,
                "pairwise_motion": pairs, "samples": samples,
                "action_metadata": _custom_props(action) if action else {},
                "track_metadata_available": any(v["track_id"] is not None for v in bone_results.values()),
                "metadata_sources_checked": ["armature bone custom properties", "action custom properties"]}
    finally:
        scene.frame_set(old_frame, subframe=old_subframe)
        if temp_obj:
            data = temp_obj.data
            bpy.data.objects.remove(temp_obj, do_unlink=True)
            if data.users == 0:
                bpy.data.armatures.remove(data)


_PROTOTYPE_MAP = [
    ("bone_23", "ShoulderL", "local"),
    ("bone_24", "ArmL1", "local"),
    ("bone_25", "ArmL1", "armature"),
    ("bone_26", "ArmL2", "local"),
    ("bone_27", "HandL", "local"),
    ("bone_29", "HandL", "armature"),
]
_PROTOTYPE_BONES = ["bone_22", "bone_23", "bone_24", "bone_25", "bone_26", "bone_27", "bone_29", "bone_30"]


def _unique_id_name(collection, base):
    candidate, index = base, 1
    while collection.get(candidate):
        candidate = base + "_" + str(index)
        index += 1
    return candidate


def _link_like(source, duplicate, fallback):
    collections = list(source.users_collection) or [fallback]
    for collection in collections:
        collection.objects.link(duplicate)


def _remove_owned_object(obj):
    if not obj:
        return
    data = obj.data
    bpy.data.objects.remove(obj, do_unlink=True)
    if data and data.users == 0:
        if data.__class__.__name__ == "Armature":
            bpy.data.armatures.remove(data)
        elif data.__class__.__name__ == "Mesh":
            bpy.data.meshes.remove(data)


def _action_slot_for(action, obj):
    current = obj.animation_data.action_slot if obj.animation_data and obj.animation_data.action == action else None
    if current:
        return current
    slots = list(getattr(action, "slots", []))
    slot = next((s for s in slots if s.target_id_type == "OBJECT" and
                 (obj.name in s.name or obj.name in s.identifier)), None)
    return slot or next((s for s in slots if s.target_id_type == "OBJECT"), None)


def _source_local_rest_rotation(obj, name):
    bone = obj.data.bones[name]
    rest = bone.matrix_local.copy()
    if bone.parent:
        rest = bone.parent.matrix_local.inverted_safe() @ rest
    return rest.to_quaternion().normalized()


def _target_local_rest_rotation(obj, name):
    bone = obj.data.bones[name]
    rest = bone.matrix_local.copy()
    if bone.parent:
        rest = bone.parent.matrix_local.inverted_safe() @ rest
    return rest.to_quaternion().normalized()


def _copy_rotation_mode(source_obj, target_obj, target_name, source_name):
    source_basis = source_obj.pose.bones[source_name].matrix_basis.to_quaternion().normalized()
    source_rest = _source_local_rest_rotation(source_obj, source_name)
    target_rest = _target_local_rest_rotation(target_obj, target_name)
    calibration = target_rest.inverted() @ source_rest
    return (calibration @ source_basis @ calibration.inverted()).normalized()


def _copy_world_delta(source_obj, target_obj, target_name, source_name):
    source_pose = source_obj.pose.bones[source_name].matrix.to_quaternion().normalized()
    source_rest = source_obj.data.bones[source_name].matrix_local.to_quaternion().normalized()
    target_rest = target_obj.data.bones[target_name].matrix_local.to_quaternion().normalized()
    delta = source_pose @ source_rest.inverted()
    return (delta @ target_rest).normalized()


def _set_pose_armature_rotation(pose_bone, rotation):
    current = pose_bone.matrix.copy()
    matrix = Matrix.LocRotScale(current.translation, rotation, current.to_scale())
    pose_bone.matrix = matrix


def _finite_tree(value):
    if isinstance(value, dict):
        return all(_finite_tree(v) for v in value.values())
    if isinstance(value, (tuple, list)):
        return all(_finite_tree(v) for v in value)
    if isinstance(value, (int, float)):
        return math.isfinite(value)
    return True


def _mesh_weight_centroids(mesh_sources, mesh_copies, bone_names, armature):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    result = {}
    for source, duplicate in zip(mesh_sources, mesh_copies):
        evaluated = duplicate.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh()
        try:
            groups = {g.index: g.name for g in source.vertex_groups if g.name in bone_names}
            sums = {name: Vector((0,0,0)) for name in bone_names}
            weights = {name: 0.0 for name in bone_names}
            counts = {name: 0 for name in bone_names}
            world = evaluated.matrix_world
            for index, vertex in enumerate(mesh.vertices):
                if index >= len(source.data.vertices):
                    break
                point = world @ vertex.co
                for assignment in source.data.vertices[index].groups:
                    name = groups.get(assignment.group)
                    weight = float(assignment.weight)
                    if name and weight > 0.0:
                        sums[name] += point * weight
                        weights[name] += weight
                        counts[name] += 1
            result[source.name] = {name: {"weighted_vertex_count": counts[name],
                                           "centroid_world": list(sums[name] / weights[name]) if weights[name] else None}
                                   for name in bone_names}
        finally:
            evaluated.to_mesh_clear()
    return result


def _prototype_waluigi_arm(args):
    source = _object(args.get("source_object", "luigi.001_armature"))
    source_action = bpy.data.actions.get(args.get("source_action", "luigi.001_armature_walk"))
    target = _object(args.get("target_object", "Waluigi_Arm_Test"))
    if source.type != "ARMATURE" or target.type != "ARMATURE":
        raise ValueError("source and target must be armature objects")
    if source_action is None:
        raise ValueError("source action not found")
    if not source.animation_data or source.animation_data.action != source_action:
        raise ValueError("source action must already be assigned to the source armature for verified slot selection")
    missing_source = sorted({s for _, s, _ in _PROTOTYPE_MAP if source.data.bones.get(s) is None})
    missing_target = [n for n in _PROTOTYPE_BONES if target.data.bones.get(n) is None]
    if missing_source or missing_target:
        raise ValueError("required mapped bones missing; source=" + str(missing_source) + " target=" + str(missing_target))
    source_slot = _action_slot_for(source_action, source)
    if hasattr(source_action, "slots") and len(source_action.slots) and source_slot is None:
        raise ValueError("source action has no compatible object slot")
    frame_start, frame_end = source_action.frame_range
    if frame_start > 0 or frame_end < 60:
        raise ValueError("source action does not cover prototype frames 0 through 60")

    mesh_names = args.get("meshes", [])
    if not mesh_names:
        raise ValueError("provide the disposable weighted meshes explicitly")
    mesh_sources = [_object(n) for n in mesh_names]
    for mesh in mesh_sources:
        if mesh.type != "MESH":
            raise ValueError("target mesh is not a mesh object: " + mesh.name)
        mods = [m for m in mesh.modifiers if m.type == "ARMATURE" and m.object == target]
        if not mods:
            raise ValueError("mesh is not currently bound to the requested disposable target: " + mesh.name)

    scene = bpy.context.scene
    original_frame, original_subframe = scene.frame_current, scene.frame_subframe
    source_temp = None
    target_copy = None
    mesh_copies = []
    action = None
    try:
        target_name = _unique_id_name(bpy.data.objects, args.get("name", "Waluigi_Arm_Retarget_Prototype"))
        target_copy = target.copy()
        target_copy.data = target.data.copy()
        target_copy.name = target_name
        target_copy.data.name = target_name + "_Data"
        target_copy[TAG] = True
        target_copy["_mg1_prototype_role"] = "waluigi_left_arm_target"
        _link_like(target, target_copy, scene.collection)
        if target_copy.animation_data:
            target_copy.animation_data.action = None
        for constraint in list(target_copy.constraints):
            target_copy.constraints.remove(constraint)
        for pose_bone in target_copy.pose.bones:
            for constraint in list(pose_bone.constraints):
                pose_bone.constraints.remove(constraint)
            pose_bone.matrix_basis = Matrix.Identity(4)
        for mesh_source in mesh_sources:
            dup = mesh_source.copy()
            dup.data = mesh_source.data.copy()
            dup.name = _unique_id_name(bpy.data.objects, mesh_source.name + "_ArmRetargetPreview")
            dup.data.name = dup.name + "_Data"
            dup[TAG] = True
            dup["_mg1_prototype_role"] = "waluigi_left_arm_weight_mesh"
            _link_like(mesh_source, dup, scene.collection)
            for modifier in dup.modifiers:
                if modifier.type == "ARMATURE" and modifier.object == target:
                    modifier.object = target_copy
            mesh_copies.append(dup)

        source_temp = source.copy()
        source_temp.data = source.data.copy()
        source_temp.name = _unique_id_name(bpy.data.objects, "_MG1_ArmRetarget_SourceEval")
        source_temp.data.name = source_temp.name + "_Data"
        source_temp[TAG] = True
        _link_like(source, source_temp, scene.collection)
        for source_pose in source.pose.bones:
            source_temp.pose.bones[source_pose.name].rotation_mode = source_pose.rotation_mode
        source_temp.animation_data_create()
        source_temp.animation_data.action = source_action
        if hasattr(source_temp.animation_data, "action_slot"):
            source_temp.animation_data.action_slot = source_slot

        action_name = _unique_id_name(bpy.data.actions, "Waluigi_LeftArm_Retarget_Prototype")
        action = bpy.data.actions.new(action_name)
        action[TAG] = True
        action["_mg1_prototype_source_action"] = source_action.name
        slot = action.slots.new("OBJECT", target_copy.name)
        layer = action.layers.new("Baked Left Arm FK")
        strip = layer.strips.new(type="KEYFRAME")
        channelbag = strip.channelbag(slot, ensure=True)
        target_copy.animation_data_create()
        target_copy.animation_data.action = action
        target_copy.animation_data.action_slot = slot

        frames = list(range(0, 61))
        baked = {name: {"location": [], "rotation_quaternion": [], "scale": []} for name in _PROTOTYPE_BONES}
        source_motion = []
        before = {"target_object": target.name, "target_scale": list(target.scale),
                  "source_object": source.name, "source_action": source_action.name,
                  "source_scale": list(source.scale),
                  "source_constraints": sum(len(p.constraints) for p in source.pose.bones),
                  "target_constraints": sum(len(p.constraints) for p in target.pose.bones),
                  "target_action": target.animation_data.action.name if target.animation_data and target.animation_data.action else None}
        before_samples = []
        for frame in (0, 15, 30, 45, 60):
            scene.frame_set(frame)
            bpy.context.view_layer.update()
            source_motion.append({"frame": frame,
                                  "bones": {name: {"rotation_mode": source_temp.pose.bones[name].rotation_mode,
                                                   "euler": [float(v) for v in source_temp.pose.bones[name].rotation_euler],
                                                   "basis_q": _quat(source_temp.pose.bones[name].matrix_basis.to_quaternion().normalized())}
                                              for name in sorted({s for _, s, _ in _PROTOTYPE_MAP})}})
            original_pose = target.pose.bones
            gaps = {"shoulder": float((target.matrix_world @ original_pose["bone_21"].tail - target.matrix_world @ original_pose["bone_22"].head).length),
                    "elbow_main": float((target.matrix_world @ original_pose["bone_24"].tail - target.matrix_world @ original_pose["bone_26"].head).length),
                    "elbow_overlap": float((target.matrix_world @ original_pose["bone_25"].tail - target.matrix_world @ original_pose["bone_29"].head).length),
                    "wrist_glove": float((target.matrix_world @ original_pose["bone_27"].tail - target.matrix_world @ original_pose["bone_30"].head).length)}
            before_samples.append({"frame": frame, "pose_matrices": {n: _mat(original_pose[n].matrix) for n in _PROTOTYPE_BONES},
                                  "joint_gaps_world": gaps,
                                  "weighted_mesh_centroids": _mesh_weight_centroids(mesh_sources, mesh_sources, _PROTOTYPE_BONES, target)})
        before["sample_frames"] = before_samples
        before["mesh_object_scales"] = {m.name: list(m.scale) for m in mesh_sources}

        for frame in frames:
            scene.frame_set(frame)
            bpy.context.view_layer.update()
            for name in _PROTOTYPE_BONES:
                target_copy.pose.bones[name].matrix_basis = Matrix.Identity(4)
            bpy.context.view_layer.update()
            for target_name_bone, source_name, mode in _PROTOTYPE_MAP:
                if mode == "local":
                    target_pose = target_copy.pose.bones[target_name_bone]
                    target_pose.rotation_mode = "QUATERNION"
                    target_pose.rotation_quaternion = _copy_rotation_mode(source_temp, target_copy, target_name_bone, source_name)
            bpy.context.view_layer.update()
            for target_name_bone, source_name, mode in _PROTOTYPE_MAP:
                if mode == "armature":
                    target_pose = target_copy.pose.bones[target_name_bone]
                    _set_pose_armature_rotation(target_pose, _copy_world_delta(source_temp, target_copy, target_name_bone, source_name))
                    bpy.context.view_layer.update()
            for name in _PROTOTYPE_BONES:
                pose = target_copy.pose.bones[name]
                rotation = pose.matrix_basis.to_quaternion().normalized()
                qlist = _quat(rotation)
                prior = baked[name]["rotation_quaternion"][-1] if baked[name]["rotation_quaternion"] else None
                if prior and sum(a*b for a,b in zip(prior, qlist)) < 0:
                    qlist = [-v for v in qlist]
                baked[name]["location"].append([float(v) for v in pose.location])
                baked[name]["rotation_quaternion"].append(qlist)
                baked[name]["scale"].append([float(v) for v in pose.scale])

        for name in _PROTOTYPE_BONES:
            for path in ("location", "rotation_quaternion", "scale"):
                data_path = target_copy.pose.bones[name].path_from_id(path)
                width = len(baked[name][path][0])
                for index in range(width):
                    curve = channelbag.fcurves.new(data_path, index=index, group_name=name)
                    curve.keyframe_points.add(len(frames))
                    for point, frame, values in zip(curve.keyframe_points, frames, baked[name][path]):
                        point.co = (frame, values[index])
                        point.interpolation = "LINEAR"
                    curve.update()

        sample_frames = [0, 15, 30, 45, 60]
        after_samples = []
        branch_quats = {"bone_23_bone_25": [], "bone_26_bone_29": []}
        for frame in sample_frames:
            scene.frame_set(frame)
            bpy.context.view_layer.update()
            p = target_copy.pose.bones
            edges = [("shoulder", "bone_21", "bone_22"), ("elbow_main", "bone_24", "bone_26"),
                     ("elbow_overlap", "bone_25", "bone_29"), ("wrist_glove", "bone_27", "bone_30")]
            joint_gaps = {}
            for label, parent_name, child_name in edges:
                parent, child = p[parent_name], p[child_name]
                joint_gaps[label] = float((target_copy.matrix_world @ parent.tail - target_copy.matrix_world @ child.head).length)
            mesh_centroids = _mesh_weight_centroids(mesh_sources, mesh_copies, _PROTOTYPE_BONES, target_copy)
            for pair, (a_name, b_name) in {"bone_23_bone_25": ("bone_23", "bone_25"),
                                           "bone_26_bone_29": ("bone_26", "bone_29")}.items():
                branch_quats[pair].append(relative(_quat(p[a_name].matrix.to_quaternion()),
                                                   _quat(p[b_name].matrix.to_quaternion())))
            after_samples.append({"frame": frame,
                                  "pose_matrices": {name: _mat(p[name].matrix) for name in _PROTOTYPE_BONES},
                                  "pose_scales": {name: [float(v) for v in p[name].scale] for name in _PROTOTYPE_BONES},
                                  "joint_gaps_world": joint_gaps, "weighted_mesh_centroids": mesh_centroids})
        branch_relative = {}
        for pair, quaternions in branch_quats.items():
            changes = [math.degrees(angular_distance(quaternions[0], q)) for q in quaternions]
            branch_relative[pair] = {"sampled_change_degrees": changes,
                                     "maximum_change_degrees": max(changes),
                                     "changes_over_time": max(changes) > 0.05}
        after = {"prototype_object": target_copy.name, "prototype_action": action.name,
                 "target_scale": list(target_copy.scale),
                 "mesh_object_scales": {m.name: list(m.scale) for m in mesh_copies},
                 "mesh_objects": [m.name for m in mesh_copies],
                 "runtime_constraint_count": len(target_copy.constraints) + sum(len(p.constraints) for p in target_copy.pose.bones),
                 "action_frame_range": list(action.frame_range), "sample_frames": after_samples,
                 "branch_relative_orientation": branch_relative,
                 "finite_numbers": _finite_tree(after_samples),
                 "object_scale_unchanged": all(abs(a-b) < 1e-8 for a,b in zip(target.scale, target_copy.scale)),
                 "max_joint_gap_world": {label: max(s["joint_gaps_world"][label] for s in after_samples)
                                          for label in after_samples[0]["joint_gaps_world"]}}
        after["max_bone_scale_deviation"] = max(abs(v-1.0) for sample in after_samples
                                                for values in sample["pose_scales"].values() for v in values)
        before_by_frame = {s["frame"]: s for s in before_samples}
        joint_gap_delta_max = {label: max(abs(sample["joint_gaps_world"][label] -
                                                   before_by_frame[sample["frame"]]["joint_gaps_world"][label])
                                               for sample in after_samples)
                               for label in after_samples[0]["joint_gaps_world"]}
        after["max_joint_gap_change_world"] = joint_gap_delta_max
        after["joint_gaps_change_within_0_05_world_units"] = all(v < 0.05 for v in joint_gap_delta_max.values())
        mesh_scale_unchanged = all(
            all(abs(a-b) < 1e-8 for a,b in zip(src.scale, dup.scale))
            for src, dup in zip(mesh_sources, mesh_copies))
        result = {"schema": "mg1.waluigi-arm-prototype.v1", "mapping": [
                      {"target": t, "source": s, "mode": m} for t,s,m in _PROTOTYPE_MAP],
                  "source_frames": frames, "source_pose_samples": source_motion,
                  "source_temp_action": source_temp.animation_data.action.name if source_temp and source_temp.animation_data and source_temp.animation_data.action else None,
                  "source_temp_slot": source_temp.animation_data.action_slot.identifier if source_temp and source_temp.animation_data and getattr(source_temp.animation_data, "action_slot", None) else None,
                  "before": before, "after": after,
                  "validation": {"finite_numbers": after["finite_numbers"],
                                 "scale_unchanged": after["object_scale_unchanged"],
                                 "mesh_scales_unchanged": mesh_scale_unchanged,
                                 "no_runtime_constraints": after["runtime_constraint_count"] == 0,
                                 "bone_scales_unchanged": after["max_bone_scale_deviation"] < 1e-5,
                                 "overlap_relative_motion_present": all(v["changes_over_time"] for v in branch_relative.values()),
                                 "joint_continuity_within_tolerance": after["joint_gaps_change_within_0_05_world_units"],
                                 "shoulder_continuity_max_gap_world": after["max_joint_gap_world"]["shoulder"],
                                 "elbow_main_max_gap_world": after["max_joint_gap_world"]["elbow_main"],
                                 "elbow_overlap_max_gap_world": after["max_joint_gap_world"]["elbow_overlap"],
                                 "wrist_glove_max_gap_world": after["max_joint_gap_world"]["wrist_glove"]}}
        if (not result["validation"]["finite_numbers"] or not result["validation"]["scale_unchanged"] or
                not result["validation"]["mesh_scales_unchanged"] or
                not result["validation"]["no_runtime_constraints"] or not result["validation"]["bone_scales_unchanged"]):
            raise RuntimeError("prototype failed finite/scale/constraint validation: " + json.dumps(result["validation"]))
        return result
    except Exception:
        if action and bpy.data.actions.get(action.name):
            bpy.data.actions.remove(action)
        for obj in [source_temp, *mesh_copies, target_copy]:
            _remove_owned_object(obj)
        raise
    finally:
        scene.frame_set(original_frame, subframe=original_subframe)
        if source_temp and bpy.data.objects.get(source_temp.name):
            _remove_owned_object(source_temp)


def _duplicate_rig(args):
    source = _object(args["object"])
    if source.type != "ARMATURE":
        raise ValueError("source object is not an armature")
    base = args.get("name", source.name + "_BridgeTest")
    name = base
    suffix = 1
    while bpy.data.objects.get(name):
        name = base + "_" + str(suffix)
        suffix += 1
    duplicate = source.copy()
    duplicate.data = source.data.copy()
    duplicate.name = name
    duplicate.data.name = name + "_Data"
    duplicate[TAG] = True
    duplicate["_mg1_bridge_source"] = source.name
    (args.get("collection") and bpy.data.collections.get(args["collection"]) or bpy.context.scene.collection).objects.link(duplicate)
    return {"name": duplicate.name, "data": duplicate.data.name, "source": source.name, "tagged": True}


def _restore(args):
    requested_objects = set(args.get("objects", []))
    requested_actions = set(args.get("actions", []))
    if requested_objects or requested_actions:
        objects = [bpy.data.objects.get(name) for name in requested_objects]
        actions = [bpy.data.actions.get(name) for name in requested_actions]
        objects = [o for o in objects if o and o.get(TAG, False)]
        actions = [a for a in actions if a and a.get(TAG, False)]
    else:
        objects = [o for o in bpy.data.objects if o.get(TAG, False)]
        actions = [a for a in bpy.data.actions if a.get(TAG, False)]
    removed = {"objects": [], "actions": []}
    for obj in objects:
        removed["objects"].append(obj.name)
        data = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if data and data.users == 0:
            if data.__class__.__name__ == "Armature":
                bpy.data.armatures.remove(data)
            elif data.__class__.__name__ == "Mesh":
                bpy.data.meshes.remove(data)
    for action in actions:
        removed["actions"].append(action.name)
        bpy.data.actions.remove(action)
    return removed


_READ = {"inspect.objects": _inspect_objects, "inspect.armature": _inspect_armature,
         "inspect.weights": _inspect_weights, "inspect.actions": _inspect_actions,
         "inspect.pose": _inspect_pose, "analyze.waluigi_arm": _analyze_waluigi_arm}
_MUTATE = {"mutate.duplicate_rig": _duplicate_rig, "mutate.restore": _restore,
           "mutate.prototype_waluigi_arm": _prototype_waluigi_arm}


def _dispatch(req):
    if req.get("version") != PROTOCOL_VERSION:
        raise ValueError("unsupported protocol version")
    op = req.get("operation")
    handler = _READ.get(op) or _MUTATE.get(op)
    if handler is None:
        raise ValueError("unknown operation")
    if op in _MUTATE and req.get("confirm") is not True:
        raise ValueError("mutation requires confirm: true")
    return handler(req.get("args") or {})


def _main_thread_pump():
    while True:
        try:
            req, done, box = _requests.get_nowait()
        except queue.Empty:
            break
        try:
            box["result"] = _dispatch(req)
        except Exception as exc:
            box["error"] = str(exc)
        finally:
            done.set()
    return 0.02 if _running else None


def _client(conn):
    try:
        data = b""
        while not data.endswith(b"\n") and len(data) <= 8 * 1024 * 1024:
            part = conn.recv(65536)
            if not part:
                break
            data += part
        req_id = None
        try:
            req = json.loads(data.decode("utf-8"))
            req_id = req.get("id")
            done, box = threading.Event(), {}
            _requests.put((req, done, box))
            if not done.wait(30):
                raise TimeoutError("Blender did not process request within 30 seconds")
            response = {"version": PROTOCOL_VERSION, "id": req_id,
                        "ok": "error" not in box, "result": box.get("result"), "error": box.get("error")}
        except Exception as exc:
            response = {"version": PROTOCOL_VERSION, "id": req_id, "ok": False, "error": str(exc)}
        conn.sendall((json.dumps(response, allow_nan=False) + "\n").encode("utf-8"))
    finally:
        conn.close()


def start(host=HOST, port=PORT):
    global _server, _running
    if host != "127.0.0.1":
        raise ValueError("bridge binds to 127.0.0.1 only")
    if _running:
        return {"host": host, "port": _server.getsockname()[1], "already_running": True}
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, int(port)))
    sock.listen(8)
    sock.settimeout(0.5)
    _server, _running = sock, True
    def accept_loop():
        while _running:
            try:
                conn, address = sock.accept()
                if address[0] != "127.0.0.1":
                    conn.close()
                    continue
                threading.Thread(target=_client, args=(conn,), daemon=True).start()
            except socket.timeout:
                continue
            except OSError:
                break
    threading.Thread(target=accept_loop, daemon=True).start()
    bpy.app.timers.register(_main_thread_pump, first_interval=0.02)
    return {"host": host, "port": sock.getsockname()[1], "protocol": PROTOCOL_VERSION}


def stop():
    global _running, _server
    _running = False
    if _server:
        _server.close()
        _server = None
    return {"stopped": True}


print("MultiGalaxy Blender bridge:", start(port=int(os.environ.get("MG1_BLENDER_BRIDGE_PORT", PORT))))
