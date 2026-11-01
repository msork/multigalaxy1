"""Read-only transform and mesh diagnostics for a saved semantic preview."""
import json
import os
import bpy

rig = bpy.data.objects.get("SemanticWalk_Waluigi_Target_Rig")
if rig is None:
    raise RuntimeError("semantic target rig not found")
meshes = sorted((o for o in bpy.data.objects if o.name.startswith("SemanticWalk_Waluigi_") and o.type == "MESH"), key=lambda o:o.name)
rows = []
for frame in (0, 15, 30, 45, 60):
    bpy.context.scene.frame_set(frame)
    bpy.context.view_layer.update()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evrig = rig.evaluated_get(depsgraph)
    bone_rows = []
    for bone in evrig.pose.bones:
        loc, quat, scale = bone.matrix_basis.decompose()
        bone_rows.append({"bone": bone.name, "loc": list(loc), "scale": list(scale),
                          "basis": [[float(bone.matrix_basis[r][c]) for c in range(4)] for r in range(4)]})
    mesh_rows = []
    for obj in meshes:
        ev = obj.evaluated_get(depsgraph)
        mesh = ev.to_mesh()
        pts = [ev.matrix_world @ v.co for v in mesh.vertices]
        ev.to_mesh_clear()
        mesh_rows.append({"name":obj.name,"world": [[float(obj.matrix_world[r][c]) for c in range(4)] for r in range(4)],
                          "local_scale":list(obj.scale),"verts":len(pts),
                          "max_radius":max((p.length for p in pts),default=0),
                          "bounds_min":[min((p[i] for p in pts),default=0) for i in range(3)],
                          "bounds_max":[max((p[i] for p in pts),default=0) for i in range(3)]})
    rows.append({"frame":frame,"bones":bone_rows,"meshes":mesh_rows})
action = rig.animation_data.action
fcurves = [fc for layer in action.layers for strip in layer.strips for bag in strip.channelbags for fc in bag.fcurves]
scale_curves = [fc.data_path for fc in fcurves if ".scale" in fc.data_path]
report = {"file":bpy.data.filepath,"rig_scale":list(rig.scale),"rig_world_scale":list(rig.matrix_world.to_scale()),
          "rig_parent":rig.parent.name if rig.parent else None,"action":action.name,"action_curve_count":len(fcurves),
          "scale_curve_paths":scale_curves,"samples":rows}
print("MG1_SEMANTIC_DIAG="+json.dumps(report))
