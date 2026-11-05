"""Validate V3 saved/reopened action against its V2 input at quarter frames."""
import argparse
import hashlib
import json
import math
import os

import bpy


def args_cli():
    argv=__import__("sys").argv
    argv=argv[argv.index("--")+1:] if "--" in argv else []
    p=argparse.ArgumentParser(); p.add_argument("--report",required=True); p.add_argument("--output",required=True)
    p.add_argument("--target-rig",default="LiteralWalk_Waluigi_Target"); p.add_argument("--mesh-prefix",default="LiteralWalk_ARMTEST_")
    return p.parse_args(argv)


def digest(value):
    return hashlib.sha256(json.dumps(value,separators=(",",":"),allow_nan=False).encode()).hexdigest()


def basis(rig):
    return {pb.name:[[round(float(x),7) for x in row] for row in pb.matrix_basis] for pb in rig.pose.bones}


def verts(meshes):
    dg=bpy.context.evaluated_depsgraph_get(); out={}
    for obj in meshes:
        ev=obj.evaluated_get(dg); me=ev.to_mesh()
        try: out[obj.name]=[[round(float(c),6) for c in ev.matrix_world@v.co] for v in me.vertices]
        finally: ev.to_mesh_clear()
    return out


def pose_hash(rig):
    return digest({"basis":basis(rig),"location":[round(float(x),7) for x in rig.location],
                   "scale":[round(float(x),7) for x in rig.scale]})


def sample(rig,action,slot,meshes,frames):
    rig.animation_data.action=action; rig.animation_data.action_slot=slot
    out={}
    for f in frames:
        n=math.floor(f); bpy.context.scene.frame_set(n,subframe=f-n); bpy.context.view_layer.update()
        out[f]={"basis":basis(rig),"location":[float(x) for x in rig.location],"pose_hash":pose_hash(rig),"mesh":verts(meshes)}
    return out


def rotation_from_rows(rows):
    from mathutils import Matrix
    return Matrix([rows[r][:3] for r in range(3)]).to_quaternion().normalized()


def main():
    args=args_cli(); expected=json.load(open(args.report,encoding="utf-8"))
    rig=bpy.data.objects.get(args.target_rig)
    meshes=sorted([o for o in bpy.data.objects if o.type=="MESH" and o.name.startswith(args.mesh_prefix)
        and any(m.type=="ARMATURE" and m.object==rig for m in o.modifiers)],key=lambda o:o.name)
    if not rig or len(meshes)!=2 or not rig.animation_data: raise RuntimeError("preview rig/meshes/action missing")
    v3=rig.animation_data.action; v3slot=rig.animation_data.action_slot
    v2=bpy.data.actions.get(expected["input_action"])
    if not v2: raise RuntimeError("preserved V2 action datablock is unavailable in saved V3 file")
    v2slot=next((s for s in v2.slots if s.identifier==v3slot.identifier),None)
    if v2slot is None:
        if len(v2.slots)!=1: raise RuntimeError("V2 action slot is ambiguous")
        v2slot=v2.slots[0]
    frames=sorted(float(k) for k in expected["expected_sample_hashes"])
    v2samples=sample(rig,v2,v2slot,meshes,frames)
    v3samples=sample(rig,v3,v3slot,meshes,frames)
    pose_errors=[]; mesh_errors=[]; per_bone={}; max_root=0.0; corrected=set(expected["bone_corrections"])
    correction_angle_series={name:[] for name in corrected}
    for f in frames:
        key=str(f); exp=expected["expected_sample_hashes"][key]; row=v3samples[f]
        if row["pose_hash"]!=exp["pose"]: pose_errors.append(key)
        for name,expected_hash in exp["mesh"].items():
            got=digest(row["mesh"][name])
            if got!=expected_hash: mesh_errors.append({"frame":key,"mesh":name})
        max_root=max(max_root,max(abs(a-b) for a,b in zip(v2samples[f]["location"],v3samples[f]["location"])))
        for name,old in v2samples[f]["basis"].items():
            new=v3samples[f]["basis"][name]
            delta=max(abs(old[r][c]-new[r][c]) for r in range(4) for c in range(4))
            if name not in corrected and delta>1e-6: per_bone.setdefault("uncorrected_mismatches",[]).append({"frame":key,"bone":name,"delta":delta})
            if name in corrected:
                q0=rotation_from_rows(old); q1=rotation_from_rows(new)
                correction_angle_series[name].append(q0.rotation_difference(q1).angle)
    smoothness={}
    for bone,series in correction_angle_series.items():
        adjacent=[abs(b-a) for a,b in zip(series,series[1:])]
        smoothness[bone]={"max_v2_to_v3_rotation_delta_rad":max(series,default=0.0),
                          "max_quarter_sample_correction_delta_step_rad":max(adjacent,default=0.0)}
    constraints=len(rig.constraints)+sum(len(pb.constraints) for pb in rig.pose.bones)
    scale=[float(x) for x in rig.scale]
    visible=[o.name for o in bpy.context.scene.objects if not o.hide_viewport and not o.hide_get() and
             (o.type=="ARMATURE" or (o.type=="MESH" and o.name.startswith(args.mesh_prefix)))]
    wanted={rig.name,*[m.name for m in meshes]}
    result={"schema":"mg1.literal-retarget-v3-reopen-validation.v1","blend":bpy.data.filepath,
        "quarter_frame_count":len(frames),"pose_hash_mismatches":pose_errors,"mesh_vertex_hash_mismatches":mesh_errors,
        "uncorrected_basis_mismatches":per_bone.get("uncorrected_mismatches",[]),"max_root_translation_delta":max_root,
        "correction_rotation_smoothness":smoothness,"runtime_constraints":constraints,"scale":scale,
        "visible_character_objects":visible,"exactly_one_character_visible":set(visible)==wanted,
        "all_checks_pass":not pose_errors and not mesh_errors and not per_bone.get("uncorrected_mismatches") and
            max_root<1e-7 and constraints==0 and max(abs(x-43.437) for x in scale)<.02 and set(visible)==wanted}
    os.makedirs(os.path.dirname(os.path.abspath(args.output)),exist_ok=True)
    with open(args.output,"w",encoding="utf-8") as f: json.dump(result,f,indent=2,allow_nan=False); f.write("\n")
    print("LITERAL_V3_REOPEN",json.dumps({k:result[k] for k in ("quarter_frame_count","pose_hash_mismatches","mesh_vertex_hash_mismatches","max_root_translation_delta","runtime_constraints","exactly_one_character_visible","all_checks_pass")}))
    if not result["all_checks_pass"]: raise RuntimeError("V3 reopen validation failed")


if __name__=="__main__": main()
