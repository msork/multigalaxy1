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
from mathutils import Vector

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
_MUTATE = {"mutate.duplicate_rig": _duplicate_rig, "mutate.restore": _restore}


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
