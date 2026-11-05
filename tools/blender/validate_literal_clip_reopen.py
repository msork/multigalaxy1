"""Reopen validation for clip-corrected disposable previews."""
import argparse
import hashlib
import json
import math
import os

import bpy


def parse_args():
    argv = __import__("sys").argv
    argv = argv[argv.index("--") + 1:] if "--" in argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--report", required=True, help="pre-save correction validation report")
    p.add_argument("--target-rig", default="LiteralWalk_Waluigi_Target")
    p.add_argument("--mesh-prefix", default="LiteralWalk_ARMTEST_")
    p.add_argument("--output", required=True)
    return p.parse_args(argv)


def digest(rows):
    return hashlib.sha256(json.dumps(rows, separators=(",", ":"),
        allow_nan=False).encode("utf-8")).hexdigest()


def pose_hash(rig):
    basis = {pb.name: [[round(float(v), 6) for v in row] for row in pb.matrix_basis]
             for pb in rig.pose.bones}
    return digest({"basis":basis,
        "location":[round(float(v),6) for v in rig.location],
        "scale":[round(float(v),6) for v in rig.scale]})


def evaluated_meshes(meshes):
    dg=bpy.context.evaluated_depsgraph_get()
    out={}
    for obj in meshes:
        ev=obj.evaluated_get(dg)
        me=ev.to_mesh()
        try:
            out[obj.name]=digest([[round(float(c),6) for c in ev.matrix_world @ v.co]
                                  for v in me.vertices])
        finally:
            ev.to_mesh_clear()
    return out


def main():
    args=parse_args()
    with open(args.report,"r",encoding="utf-8") as f: expected=json.load(f)
    rig=bpy.data.objects.get(args.target_rig)
    meshes=sorted([o for o in bpy.data.objects if o.type=="MESH" and
        o.name.startswith(args.mesh_prefix) and any(m.type=="ARMATURE" and m.object==rig
                                                     for m in o.modifiers)],key=lambda o:o.name)
    if not rig or len(meshes)!=2: raise RuntimeError("reopened preview rig/meshes missing")
    pose_mismatches=[]; mesh_mismatches=[]; worst_count=0
    frame_hashes=expected["expected_pose_hashes_rounded_1e-6"]
    expected_mesh=expected["expected_mesh_vertex_hashes_rounded_1e-6"]
    for key in sorted(frame_hashes,key=lambda x:float(x)):
        frame=float(key); base=math.floor(frame)
        bpy.context.scene.frame_set(base,subframe=frame-base)
        bpy.context.view_layer.update()
        got_pose=pose_hash(rig)
        if got_pose!=frame_hashes[key]: pose_mismatches.append(key)
        got_mesh=evaluated_meshes(meshes)
        if got_mesh!=expected_mesh[key]:
            mesh_mismatches.append({"frame":key,"objects":[name for name in expected_mesh[key]
                if expected_mesh[key][name]!=got_mesh.get(name)]})
    constraints=len(rig.constraints)+sum(len(pb.constraints) for pb in rig.pose.bones)
    armature_scale=[float(x) for x in rig.scale]
    visible=[o.name for o in bpy.context.scene.objects if not o.hide_viewport and
             not o.hide_get() and ((o.type=="ARMATURE") or
             (o.type=="MESH" and o.name.startswith(args.mesh_prefix)))]
    expected_visible={rig.name,*[m.name for m in meshes]}
    report={"schema":"mg1.literal-retarget-clip-reopen-validation.v1",
        "blend":bpy.data.filepath,"source_action":expected["source_action"],
        "target_action":rig.animation_data.action.name if rig.animation_data else None,
        "quarter_frame_count":len(frame_hashes),"pose_hash_mismatches":pose_mismatches,
        "mesh_vertex_hash_mismatches":mesh_mismatches,"runtime_constraints":constraints,
        "armature_scale":armature_scale,"visible_character_objects":visible,
        "exactly_one_character_body_visible":set(visible)==expected_visible,
        "all_checks_pass":not pose_mismatches and not mesh_mismatches and constraints==0 and
            max(abs(v-43.437) for v in armature_scale)<0.02 and set(visible)==expected_visible}
    with open(args.output,"w",encoding="utf-8") as f: json.dump(report,f,indent=2); f.write("\n")
    print("LITERAL_CLIP_REOPEN",json.dumps({k:report[k] for k in (
        "quarter_frame_count","pose_hash_mismatches","mesh_vertex_hash_mismatches",
        "runtime_constraints","exactly_one_character_body_visible","all_checks_pass")}))
    if not report["all_checks_pass"]: raise RuntimeError("saved/reopened clip failed validation")


if __name__=="__main__":main()
