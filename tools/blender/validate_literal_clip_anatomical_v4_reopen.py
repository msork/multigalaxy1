"""Check reopened V4 pose and evaluated geometry against baked sample hashes."""
import argparse, hashlib, json, math, os
import bpy

def digest(value):
    return hashlib.sha256(json.dumps(value,separators=(",",":"),allow_nan=False).encode()).hexdigest()

def cli():
    a=__import__("sys").argv; a=a[a.index("--")+1:] if "--" in a else []
    p=argparse.ArgumentParser(); p.add_argument("--report",required=True); p.add_argument("--output",required=True)
    p.add_argument("--target-rig",default="LiteralWalk_Waluigi_Target"); p.add_argument("--mesh-prefix",default="LiteralWalk_ARMTEST_")
    return p.parse_args(a)

def main():
    a=cli(); expected=json.load(open(a.report,encoding="utf-8")); rig=bpy.data.objects.get(a.target_rig)
    if rig is None or rig.type!="ARMATURE" or rig.animation_data is None or rig.animation_data.action is None: raise RuntimeError("reopened V4 target/action missing")
    action=rig.animation_data.action; slot=rig.animation_data.action_slot
    if action.name!=expected["output_action"]: raise RuntimeError(f"wrong action after reopen: {action.name}")
    meshes=sorted([o for o in bpy.data.objects if o.type=="MESH" and o.name.startswith(a.mesh_prefix) and any(m.type=="ARMATURE" and m.object==rig for m in o.modifiers)],key=lambda o:o.name)
    if len(meshes)!=2: raise RuntimeError(f"expected two bound Waluigi meshes, got {len(meshes)}")
    scene=bpy.context.scene; pose_bad=[];mesh_bad=[];nonfinite=[]
    for key,ex in expected["expected_sample_hashes"].items():
        f=float(key); n=math.floor(f); scene.frame_set(n,subframe=f-n); bpy.context.view_layer.update()
        basis={pb.name:[[round(float(x),7) for x in row] for row in pb.matrix_basis] for pb in rig.pose.bones}
        location=[round(float(x),7) for x in rig.location];scale=[round(float(x),7) for x in rig.scale]
        if digest({"basis":basis,"location":location,"scale":scale})!=ex["pose"]: pose_bad.append(key)
        dg=bpy.context.evaluated_depsgraph_get()
        for obj in meshes:
            ev=obj.evaluated_get(dg);me=ev.to_mesh()
            try:
                pts=[[float(c) for c in ev.matrix_world@v.co] for v in me.vertices]
                if any(not math.isfinite(c) for p in pts for c in p): nonfinite.append({"frame":key,"mesh":obj.name})
                got=digest([[round(c,6) for c in p] for p in pts])
                if got!=ex["mesh"].get(obj.name): mesh_bad.append({"frame":key,"mesh":obj.name})
            finally: ev.to_mesh_clear()
    constraints=len(rig.constraints)+sum(len(pb.constraints) for pb in rig.pose.bones)
    visible=[o.name for o in scene.objects if not o.hide_viewport and not o.hide_get() and (o.type=="ARMATURE" or (o.type=="MESH" and o.name.startswith(a.mesh_prefix)))]
    wanted={rig.name,*[m.name for m in meshes]}
    scale=[float(x) for x in rig.scale]
    result={"schema":"mg1.literal-retarget-anatomical-v4-reopen.v1","blend":bpy.data.filepath,"action":action.name,
        "quarter_sample_count":len(expected["expected_sample_hashes"]),"pose_hash_mismatches":pose_bad,"mesh_hash_mismatches":mesh_bad,
        "nonfinite_mesh_samples":nonfinite,"runtime_constraints":constraints,"scale":scale,"visible_character_objects":visible,
        "exactly_one_visible_character":set(visible)==wanted,
        "all_checks_pass":not pose_bad and not mesh_bad and not nonfinite and constraints==0 and max(abs(x-43.437) for x in scale)<.02 and set(visible)==wanted}
    os.makedirs(os.path.dirname(os.path.abspath(a.output)),exist_ok=True);json.dump(result,open(a.output,"w",encoding="utf-8"),indent=2,allow_nan=False)
    print("LITERAL_V4_REOPEN",json.dumps({k:result[k] for k in ("quarter_sample_count","pose_hash_mismatches","mesh_hash_mismatches","runtime_constraints","exactly_one_visible_character","all_checks_pass")}))
    if not result["all_checks_pass"]: raise RuntimeError("V4 reopened validation failed")

if __name__=="__main__": main()
