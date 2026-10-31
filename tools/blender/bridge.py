"""Loopback JSON-lines inspection bridge for Blender 5.x.

Run in Blender's Python Console: exec(compile(open(r'.../bridge.py').read(), 'bridge.py', 'exec'))
All bpy access is dispatched onto Blender's main thread.
"""
import json
import queue
import socket
import threading
import uuid

import bpy

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
         "inspect.pose": _inspect_pose}
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


print("MultiGalaxy Blender bridge:", start())
