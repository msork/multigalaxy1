"""Read-only inspection of a Blender file's rigs, actions, and mesh bindings."""
import bpy
import json
import sys


def matrix(obj):
    return [[round(float(v), 8) for v in row] for row in obj.matrix_world]


def main():
    out = sys.argv[sys.argv.index("--") + 1] if "--" in sys.argv else "-"
    bpy.context.view_layer.update()
    bpy.context.scene.frame_set(0)
    bpy.context.view_layer.update()
    objects = []
    for obj in bpy.data.objects:
        rec = {"name": obj.name, "type": obj.type, "matrix_world": matrix(obj),
               "matrix_basis": [[round(float(v), 8) for v in row] for row in obj.matrix_basis],
               "matrix_local": [[round(float(v), 8) for v in row] for row in obj.matrix_local],
               "matrix_parent_inverse": [[round(float(v), 8) for v in row] for row in obj.matrix_parent_inverse],
               "scale": list(obj.scale), "parent": obj.parent.name if obj.parent else None,
               "hide_viewport": obj.hide_viewport, "hide_render": obj.hide_render}
        if obj.type == "ARMATURE":
            rec["bone_count"] = len(obj.data.bones)
            rec["bones"] = [{"name": b.name, "parent": b.parent.name if b.parent else None,
                             "head": list(b.head_local), "tail": list(b.tail_local),
                             "use_deform": b.use_deform} for b in obj.data.bones]
            anim = obj.animation_data
            rec["action"] = anim.action.name if anim and anim.action else None
            rec["action_slot"] = getattr(anim, "action_slot", None).identifier if anim and getattr(anim, "action_slot", None) else None
            rec["constraints"] = [c.type for p in obj.pose.bones for c in p.constraints]
        if obj.type == "MESH":
            rec["vertex_count"] = len(obj.data.vertices)
            rec["group_names"] = [g.name for g in obj.vertex_groups]
            rec["weighted_vertices"] = sum(1 for v in obj.data.vertices if v.groups)
            group_weight_counts = {g.name: 0 for g in obj.vertex_groups}
            group_weight_sums = {g.name: 0.0 for g in obj.vertex_groups}
            for vertex in obj.data.vertices:
                for assignment in vertex.groups:
                    if assignment.group < len(obj.vertex_groups):
                        name = obj.vertex_groups[assignment.group].name
                        if assignment.weight > 0.0:
                            group_weight_counts[name] += 1
                            group_weight_sums[name] += float(assignment.weight)
            rec["group_weight_counts"] = {k: v for k, v in group_weight_counts.items() if v}
            rec["group_weight_sums"] = {k: round(v, 8) for k, v in group_weight_sums.items() if v}
            if len(obj.data.vertices):
                coords = [obj.matrix_world @ v.co for v in obj.data.vertices]
                rec["world_bounds"] = [[min(c[i] for c in coords), max(c[i] for c in coords)] for i in range(3)]
            rec["armature_modifiers"] = [{"object": m.object.name if m.object else None,
                                          "use_vertex_groups": m.use_vertex_groups,
                                          "use_bone_envelopes": m.use_bone_envelopes}
                                         for m in obj.modifiers if m.type == "ARMATURE"]
            rec["materials"] = [m.name if m else None for m in obj.data.materials]
        objects.append(rec)
    action_records = []
    for action in bpy.data.actions:
        slots = []
        if hasattr(action, "layers"):
            for slot in action.slots:
                curves = [curve for layer in action.layers for strip in layer.strips
                          for bag in strip.channelbags if bag.slot == slot for curve in bag.fcurves]
                slots.append({"identifier": slot.identifier, "target_id_type": slot.target_id_type,
                              "curve_count": len(curves),
                              "paths": sorted(set(curve.data_path for curve in curves))[:20]})
        action_records.append({"name": action.name, "frame_range": list(action.frame_range),
                               "slots": slots})
    result = {"filepath": bpy.data.filepath,
              "actions": action_records,
              "objects": objects}
    payload = json.dumps(result, indent=2)
    if out == "-":
        print("BIND_PROBE_JSON=" + payload)
    else:
        with open(out, "w", encoding="utf-8") as f:
            f.write(payload)


if __name__ == "__main__":
    main()
