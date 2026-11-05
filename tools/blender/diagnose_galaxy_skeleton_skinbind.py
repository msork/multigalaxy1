"""Read-only diagnostics for a saved Galaxy-skeleton/Sluggers-mesh preview."""
import bpy
import json
import math
import sys
from mathutils import Matrix


def materr(a, b):
    return max(abs(float(a[r][c])-float(b[r][c])) for r in range(4) for c in range(4))


def verts(obj):
    dg = bpy.context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(dg)
    me = ev.to_mesh()
    try:
        return [ev.matrix_world @ v.co for v in me.vertices]
    finally:
        ev.to_mesh_clear()


def main():
    rig = bpy.data.objects["GalaxySkinBind_Luigi_Rig"]
    candidate_meshes = [o for o in bpy.data.objects if o.name.startswith("GalaxySkinBind_LiteralWalk_ARMTEST")]
    reference = [bpy.data.objects.get(o.name.replace("GalaxySkinBind_", "")) for o in candidate_meshes]
    rig.animation_data.action = None
    for pb in rig.pose.bones:
        pb.location = (0.0, 0.0, 0.0)
        pb.rotation_euler = (0.0, 0.0, 0.0)
        pb.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
        pb.scale = (1.0, 1.0, 1.0)
        pb.matrix_basis = Matrix.Identity(4)
    bpy.context.scene.frame_set(0)
    bpy.context.view_layer.update()
    report = {"rig_world": [list(r) for r in rig.matrix_world], "pose_rest_error": {}}
    for pb in rig.pose.bones:
        report["pose_rest_error"][pb.name] = materr(pb.matrix, pb.bone.matrix_local)
    report["mesh_rows"] = []
    for cand, ref in zip(candidate_meshes, reference):
        row = {"candidate": cand.name, "reference": ref.name if ref else None,
               "cand_world": [list(r) for r in cand.matrix_world],
               "ref_world": [list(r) for r in ref.matrix_world] if ref else None,
               "armature_modifier": [m.object.name if m.object else None for m in cand.modifiers if m.type == 'ARMATURE']}
        if ref:
            a = verts(cand)
            b = [ref.matrix_world @ v.co for v in ref.data.vertices]
            dg = bpy.context.evaluated_depsgraph_get()
            ev = cand.evaluated_get(dg); me = ev.to_mesh()
            row["candidate_raw_vertex0"] = list(cand.data.vertices[0].co)
            row["candidate_evaluated_local_vertex0"] = list(me.vertices[0].co)
            local_distances = [(me.vertices[i].co - cand.data.vertices[i].co).length
                               for i in range(min(len(me.vertices), len(cand.data.vertices)))]
            row["max_local_delta_from_raw"] = max(local_distances, default=0.0)
            row["rms_local_delta_from_raw"] = ((sum(x*x for x in local_distances) /
                                                 max(len(local_distances), 1)) ** 0.5)
            ev.to_mesh_clear()
            row["reference_raw_vertex0"] = list(ref.data.vertices[0].co)
            distances = [(x-y).length for x, y in zip(a, b)]
            row.update({"count": len(a), "max_vertex_delta": max(distances, default=0.0),
                        "rms_vertex_delta": math.sqrt(sum(x*x for x in distances)/max(len(distances),1)),
                        "worst_index": max(range(len(distances)), key=distances.__getitem__) if distances else None})
        report["mesh_rows"].append(row)
    out = sys.argv[sys.argv.index("--") + 1]
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print("SKIN_BIND_DIAGNOSTIC", json.dumps({"max_pose_rest_error": max(report["pose_rest_error"].values(), default=0), "mesh_rows": report["mesh_rows"]}, sort_keys=True))


if __name__ == "__main__":
    main()
