"""Read-only Blender report for armature/action provenance in a saved .blend.

Run with Blender background mode and the .blend file as the startup file. The
script does not change scene state or save the file.
"""
import hashlib
import json
import os
import bpy

TARGET_BONES = {"bone_21", "bone_22", "bone_23", "bone_24", "bone_25",
                "bone_26", "bone_27", "bone_29", "bone_30"}


def matrix_rows(matrix):
    return [[float(matrix[r][c]) for c in range(4)] for r in range(4)]


def data_signature(armature):
    rows = []
    for bone in sorted(armature.bones, key=lambda item: item.name):
        rows.append((bone.name, tuple(round(float(x), 7) for x in bone.head_local),
                     tuple(round(float(x), 7) for x in bone.tail_local),
                     bone.parent.name if bone.parent else None,
                     tuple(tuple(round(float(x), 7) for x in row) for row in bone.matrix_local)))
    return hashlib.sha256(repr(rows).encode("utf-8")).hexdigest()


def action_slots(action):
    result = []
    for slot in getattr(action, "slots", []):
        curves = []
        all_curves = []
        for layer in action.layers:
            for strip in layer.strips:
                bags = list(getattr(strip, "channelbags", []))
                bag = next((item for item in bags
                            if getattr(item, "slot", None) == slot), None)
                if bag is None:
                    try:
                        bag = strip.channelbag(slot, ensure=False)
                    except (TypeError, RuntimeError):
                        bag = None
                if bag is None:
                    continue
                all_curves.extend((layer.name, curve) for curve in bag.fcurves)
                for curve in bag.fcurves:
                    if 'pose.bones["' not in curve.data_path:
                        continue
                    bone = curve.data_path.split('pose.bones["', 1)[1].split('"', 1)[0]
                    if bone not in TARGET_BONES:
                        continue
                    points = sorted((float(p.co.x), float(p.co.y)) for p in curve.keyframe_points)
                    curves.append({"bone": bone, "data_path": curve.data_path,
                                   "array_index": curve.array_index,
                                   "key_count": len(points),
                                   "first_keys": points[:4], "last_keys": points[-2:],
                                   "value_range": [min((p[1] for p in points), default=None),
                                                   max((p[1] for p in points), default=None)]})
        result.append({"name": getattr(slot, "name_display", slot.identifier), "identifier": slot.identifier,
                       "target_id_type": slot.target_id_type, "curves": curves,
                       "fcurve_count": len(all_curves),
                       "data_path_count": len({curve.data_path for _, curve in all_curves}),
                       "data_paths": sorted({curve.data_path for _, curve in all_curves}),
                       "legacy_fcurve_count": len(action.fcurves) if hasattr(action, "fcurves") else None,
                       "layer_count": len(action.layers)})
    return result


def main():
    armatures = []
    for obj in bpy.data.objects:
        if obj.type != "ARMATURE":
            continue
        armatures.append({"object": obj.name, "data": obj.data.name,
            "bone_count": len(obj.data.bones), "rest_signature_sha256": data_signature(obj.data),
            "location": list(obj.location), "rotation_euler": list(obj.rotation_euler),
            "scale": list(obj.scale), "matrix_world": matrix_rows(obj.matrix_world),
            "action": obj.animation_data.action.name if obj.animation_data and obj.animation_data.action else None,
            "action_slot": (obj.animation_data.action_slot.identifier
                if obj.animation_data and obj.animation_data.action_slot else None),
            "bones": {name: {"parent": obj.data.bones[name].parent.name if obj.data.bones[name].parent else None,
                "head": list(obj.data.bones[name].head_local), "tail": list(obj.data.bones[name].tail_local),
                "matrix_local": matrix_rows(obj.data.bones[name].matrix_local),
                "matrix_local_inverse": matrix_rows(obj.data.bones[name].matrix_local.inverted_safe()),
                "use_deform": obj.data.bones[name].use_deform,
                "use_inherit_rotation": obj.data.bones[name].use_inherit_rotation,
                "inherit_scale": obj.data.bones[name].inherit_scale,
                "use_local_location": obj.data.bones[name].use_local_location,
                "pose_rotation_mode": obj.pose.bones[name].rotation_mode,
                "pose_location": list(obj.pose.bones[name].location),
                "pose_rotation_quaternion": list(obj.pose.bones[name].rotation_quaternion),
                "pose_rotation_euler": list(obj.pose.bones[name].rotation_euler),
                "pose_scale": list(obj.pose.bones[name].scale),
                "pose_matrix_basis": matrix_rows(obj.pose.bones[name].matrix_basis)}
                for name in TARGET_BONES if obj.data.bones.get(name)}})
    actions = []
    assigned = {obj.animation_data.action.name for obj in bpy.data.objects
                if obj.type == "ARMATURE" and obj.animation_data and obj.animation_data.action}
    for action in bpy.data.actions:
        if action.name.startswith("sequence_") or action.name in assigned:
            actions.append({"name": action.name, "frame_range": [float(x) for x in action.frame_range],
                            "slots": action_slots(action), "metadata": dict(action.items())})
    meshes = [{"object": obj.name, "data": obj.data.name,
        "parent": obj.parent.name if obj.parent else None,
        "location": list(obj.location), "rotation_euler": list(obj.rotation_euler),
        "scale": list(obj.scale), "matrix_world": matrix_rows(obj.matrix_world),
        "modifiers": [{"name": m.name, "type": m.type,
            "object": m.object.name if m.type == "ARMATURE" and m.object else None}
            for m in obj.modifiers]}
        for obj in bpy.data.objects if obj.type == "MESH" and any(x in obj.name.lower() for x in ("waluigi", "submesh"))]
    result = {
        "filepath": bpy.data.filepath, "scene": bpy.context.scene.name,
        "armatures": armatures, "sequence_actions": actions, "meshes": meshes}
    payload = json.dumps(result, allow_nan=False)
    output = os.environ.get("MG1_PROVENANCE_OUTPUT")
    if output:
        with open(output, "w", encoding="utf-8") as handle:
            handle.write(payload + "\n")
    print("MG1_SAVED_ARMATURE_PROVENANCE=" + bpy.data.filepath +
          " armatures=" + str(len(armatures)) + " actions=" + str(len(actions)) +
          " report=" + (output or "stdout"))


main()
