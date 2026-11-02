"""Read-only source/v4/baseline leg trajectory audit at quarter frames."""
import argparse,json,math,os,sys
import bpy
from mathutils import Matrix,Vector
HERE=os.path.dirname(os.path.abspath(__file__));sys.path.insert(0,HERE)
import prototype_direct_luigi_walk_contact as engine
from convert_native_owner_actions import duplicate_rig

def args_parse():
 av=sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else []
 p=argparse.ArgumentParser();p.add_argument('--source-rig',default='luigi.001_armature');p.add_argument('--source-action',default='luigi.001_armature_walk');p.add_argument('--target-action',default='Waluigi_DirectGalaxyWalk_v4_ContactNarrowFists');p.add_argument('--baseline-rig',default='Waluigi_Arm_Clean_Backup');p.add_argument('--baseline-action',default='Waluigi_GalaxyWalk_Legs_Baked');p.add_argument('--output',required=True);return p.parse_args(av)

def get_curves(rig,action):
 slot=engine.action_slot(action,rig.animation_data.action_slot.identifier if rig.animation_data and rig.animation_data.action_slot else None)
 return engine.curves_for_slot(action,slot)

def point(m):return Vector(m.translation) if hasattr(m,'translation') else Vector(m)
def rest_axis_map(src,srcroot,tgt,tgtroot):
 so=engine.object_basis(src).to_3x3().to_quaternion().to_matrix()
 to=engine.object_basis(tgt).to_3x3().to_quaternion().to_matrix()
 sr=src.data.bones[srcroot].matrix_local.to_3x3().to_quaternion().to_matrix()
 tr=tgt.data.bones[tgtroot].matrix_local.to_3x3().to_quaternion().to_matrix()
 return tr.inverted_safe() @ to.inverted_safe() @ so @ sr

def pose_local(root_m,p):return root_m.to_3x3().inverted_safe() @ (p-root_m.translation)
def knee_metrics(hip,knee,ankle):
 upper=knee-hip;lower=ankle-knee
 flex=math.acos(max(-1,min(1,upper.normalized().dot(lower.normalized()))))
 axis=(ankle-hip).normalized();pole=knee-(hip+axis*(knee-hip).dot(axis))
 return flex,pole
def anatomical_frame(forward, up):
 # Build a right-handed character frame from actual rest landmarks, never from
 # assumptions about Blender's X/Y/Z convention.
 f=forward.copy();u=up.copy()
 f.normalize();u.normalize();r=f.cross(u).normalized();u=r.cross(f).normalized()
 return Matrix(((r.x,u.x,f.x),(r.y,u.y,f.y),(r.z,u.z,f.z)))

def main():
 a=args_parse();scene=bpy.context.scene
 src=bpy.data.objects.get(a.source_rig);srcact=bpy.data.actions.get(a.source_action)
 targets=[o for o in bpy.data.objects if o.type=='ARMATURE' and o.animation_data and o.animation_data.action and o.animation_data.action.name==a.target_action]
 base=bpy.data.objects.get(a.baseline_rig);baseact=bpy.data.actions.get(a.baseline_action)
 if not src or not srcact or len(targets)!=1 or not base or not baseact:raise RuntimeError('source, v4 target, or clean approved baseline missing')
 tgt=targets[0];sc=duplicate_rig(src,'LegAudit_Luigi');sc.matrix_basis=engine.object_basis(src);sc.animation_data_clear()
 bc=duplicate_rig(base,'LegAudit_ApprovedBaseline');bc.matrix_basis=engine.object_basis(base);bc.animation_data_clear()
 scurves=get_curves(src,srcact);tcurves=get_curves(tgt,tgt.animation_data.action);bcurves=get_curves(base,baseact)
 side_names={'left':('LegL1','LegL2','FootL','bone_7','bone_8','bone_9','bone_12'), 'right':('LegR1','LegR2','FootR','bone_13','bone_14','bone_15','bone_18')}
 src_frame=anatomical_frame(sc.data.bones['FootL'].tail_local-sc.data.bones['FootL'].head_local,
                            sc.data.bones['Spine1'].head_local-sc.data.bones['Hip'].head_local)
 tgt_frame=anatomical_frame(tgt.data.bones['bone_12'].tail_local-tgt.data.bones['bone_12'].head_local,
                            tgt.data.bones['bone_6'].head_local-tgt.data.bones['bone_6'].tail_local)
 # Correct source armature coordinates into target armature coordinates by
 # matching measured forward/up/lateral anatomical axes.
 anatomical_map=tgt_frame @ src_frame.transposed()
 root_map={s:anatomical_map for s in side_names}
 src_leg_len={};tgt_leg_len={}
 for side,(th,knee,foot,upper,lower,tfoot,toe) in side_names.items():
  src_leg_len[side]=((sc.data.bones[side_names[side][1]].head_local-sc.data.bones[side_names[side][0]].head_local).length+
                     (sc.data.bones[side_names[side][2]].head_local-sc.data.bones[side_names[side][1]].head_local).length)
  tgt_leg_len[side]=tgt.data.bones[upper].length+tgt.data.bones[lower].length
 data={'frames':{},'rest':{},'axis_map':{},'scale':{'source_leg_length':src_leg_len,'target_leg_length_armature_units':tgt_leg_len,'effective_local_ratio':{s:tgt_leg_len[s]/src_leg_len[s] for s in side_names}}}
 data['object_transforms']={}
 for label,obj in (('source',src),('target',tgt)):
  data['object_transforms'][label]={'matrix_basis':[[float(engine.object_basis(obj)[r][c]) for c in range(4)] for r in range(4)],
   'matrix_world':[[float(obj.matrix_world[r][c]) for c in range(4)] for r in range(4)],
   'basis_scale':list(engine.object_basis(obj).to_scale()),'basis_rotation_quaternion_wxyz':list(engine.object_basis(obj).to_quaternion())}
 for side in side_names:
  data['axis_map'][side]=[[float(root_map[side][r][c]) for c in range(3)] for r in range(3)]
  src_names=('Hip',side_names[side][0],side_names[side][1],side_names[side][2])
  dst_names=('bone_6',side_names[side][3],side_names[side][4],side_names[side][5],side_names[side][6])
  def bone_info(rig,name):
   b=rig.data.bones[name]
   return {'parent':b.parent.name if b.parent else None,'children':[c.name for c in b.children],
    'head':list(b.head_local),'tail':list(b.tail_local),'length':b.length,
    'matrix':[[float(b.matrix_local[r][c]) for c in range(4)] for r in range(4)]}
  data['rest'][side]={'source_hip_to_knee':(sc.data.bones[side_names[side][0]].head_local-sc.data.bones['Hip'].head_local).length,'source_knee_to_ankle':(sc.data.bones[side_names[side][1]].head_local-sc.data.bones[side_names[side][2]].head_local).length,'target_upper':tgt.data.bones[side_names[side][3]].length,'target_lower':tgt.data.bones[side_names[side][4]].length,'source_object_scale':list(engine.object_basis(src).to_scale()),'target_object_scale':list(engine.object_basis(tgt).to_scale()),'source_bones':{n:bone_info(sc,n) for n in src_names},'target_bones':{n:bone_info(tgt,n) for n in dst_names}}
 rows=[];source_root0=None;target_root0=None;base_root0=None;source_ankle0={};target_ankle0={}
 old=(scene.frame_current,scene.frame_subframe)
 try:
  for i in range(241):
   f=i*.25;scene.frame_set(int(f),subframe=f-int(f))
   engine.evaluate_source_curves(sc,scurves,f);sp=engine.pose_matrices_from_basis(sc)
   engine.evaluate_source_curves(tgt,tcurves,f);tp=engine.pose_matrices_from_basis(tgt)
   engine.evaluate_source_curves(bc,bcurves,f);bp=engine.pose_matrices_from_basis(bc)
   if source_root0 is None:source_root0=sp['Hip'].translation.copy();target_root0=tp['bone_6'].translation.copy();base_root0=bp['bone_6'].translation.copy()
   row={'frame':f}
   row['root_motion']={'source_pelvis_up':float((src_frame.transposed()@(sp['Hip'].translation-source_root0)).y/src_leg_len['left']),
    'v4_root_up':float((tgt_frame.transposed()@(tp['bone_6'].translation-target_root0)).y/tgt_leg_len['left']),
    'baseline_root_up':float((tgt_frame.transposed()@(bp['bone_6'].translation-base_root0)).y/tgt_leg_len['left'])}
   for side,(sth,sk,sfoot,th,tk,tfoot,ttoe) in side_names.items():
    if side not in source_ankle0:source_ankle0[side]=point(sp[sfoot]).copy();target_ankle0[side]=point(tp[tfoot]).copy()
    sm=root_map[side]; sr=sp['Hip']
    source_hip=point(sp[sth]);
    sv={'hip':Vector(),'knee':src_frame.transposed() @ (point(sp[sk])-source_hip),'ankle':src_frame.transposed() @ (point(sp[sfoot])-source_hip),'toe':src_frame.transposed() @ ((sp[sfoot] @ Vector((0,sc.data.bones[sfoot].length,0)))-source_hip)}
    # All source points are mapped into the target root anatomical frame,
    # preserving sign while normalizing each rig by its own rest leg length.
    sv={n:sm@v/src_leg_len[side] for n,v in sv.items()}
    tr=tp['bone_6']; tv={'hip':Vector(), 'knee':tgt_frame.transposed() @ (point(tp[tk])-point(tp[th]))/tgt_leg_len[side], 'ankle':tgt_frame.transposed() @ (point(tp[tfoot])-point(tp[th]))/tgt_leg_len[side], 'toe':tgt_frame.transposed() @ ((tp[ttoe] @ Vector((0,tgt.data.bones[ttoe].length,0)))-point(tp[th]))/tgt_leg_len[side]}
    br=bp['bone_6']; bv={'hip':Vector(), 'knee':tgt_frame.transposed() @ (point(bp[tk])-point(bp[th]))/tgt_leg_len[side], 'ankle':tgt_frame.transposed() @ (point(bp[tfoot])-point(bp[th]))/tgt_leg_len[side], 'toe':tgt_frame.transposed() @ ((bp[ttoe] @ Vector((0,base.data.bones[ttoe].length,0)))-point(bp[th]))/tgt_leg_len[side]}
    sf,spole=knee_metrics(sv['hip'],sv['knee'],sv['ankle']);tf,tpole=knee_metrics(tv['hip'],tv['knee'],tv['ankle']);bf,bpole=knee_metrics(bv['hip'],bv['knee'],bv['ankle'])
    # sv has already been mapped into the target anatomical frame. Applying
    # sm again here would flip/rotate the source pole a second time.
    target_source_pole_dot=tpole.normalized().dot(spole.normalized()) if spole.length>1e-8 and tpole.length>1e-8 else 0.
    source_abs=src_frame.transposed()@(point(sp[sfoot])-source_ankle0[side])/src_leg_len[side]
    target_abs=tgt_frame.transposed()@(point(tp[tfoot])-target_ankle0[side])/tgt_leg_len[side]
    row[side]={'source':{k:list(v) for k,v in sv.items()},'v4':{k:list(v) for k,v in tv.items()},'approved_baseline':{k:list(v) for k,v in bv.items()},'v4_local_bone_scales':[list(tgt.pose.bones[th].matrix_basis.to_scale()),list(tgt.pose.bones[tk].matrix_basis.to_scale())],'baseline_local_bone_scales':[list(bc.pose.bones[th].matrix_basis.to_scale()),list(bc.pose.bones[tk].matrix_basis.to_scale())],'source_ankle_absolute_delta':list(source_abs),'v4_ankle_absolute_delta':list(target_abs),'source_knee_flex_rad':sf,'v4_knee_flex_rad':tf,'baseline_knee_flex_rad':bf,'knee_pole_alignment_dot':target_source_pole_dot,'source_segment_lengths_normalized':[(sv['knee']-sv['hip']).length,(sv['ankle']-sv['knee']).length],'v4_segment_lengths_normalized':[(tv['knee']-tv['hip']).length,(tv['ankle']-tv['knee']).length],'source_toe_vector':list(sv['toe']-sv['ankle']),'v4_toe_vector':list(tv['toe']-tv['ankle']),'baseline_toe_vector':list(bv['toe']-bv['ankle'])}
   rows.append(row);data['frames'][str(f)]=row
 finally:
  scene.frame_set(old[0],subframe=old[1]);bpy.context.view_layer.update()
  for rig in (sc,bc):
   d=rig.data;bpy.data.objects.remove(rig,do_unlink=True)
   if d.users==0:bpy.data.armatures.remove(d)
 # Summaries for signs, phase, length errors, and explicit side view axes.
 summary={}
 for side in side_names:
  r=[x[side] for x in rows]
  def rng(k):
   vals=[x[k] for x in r];return [min(vals),max(vals)]
  summary[side]={'source_knee_flex_range_rad':rng('source_knee_flex_rad'),'v4_knee_flex_range_rad':rng('v4_knee_flex_rad'),'baseline_knee_flex_range_rad':rng('baseline_knee_flex_rad'),'knee_pole_alignment_dot_range':rng('knee_pole_alignment_dot'),'source_toe_forward_component_range': [min(x['source_toe_vector'][2] for x in r),max(x['source_toe_vector'][2] for x in r)],'v4_toe_forward_component_range':[min(x['v4_toe_vector'][2] for x in r),max(x['v4_toe_vector'][2] for x in r)],'source_ankle_forward_range':[min(x['source']['ankle'][2] for x in r),max(x['source']['ankle'][2] for x in r)],'v4_ankle_forward_range':[min(x['v4']['ankle'][2] for x in r),max(x['v4']['ankle'][2] for x in r)],'source_ankle_up_range':[min(x['source']['ankle'][1] for x in r),max(x['source']['ankle'][1] for x in r)],'v4_ankle_up_range':[min(x['v4']['ankle'][1] for x in r),max(x['v4']['ankle'][1] for x in r)],'mean_v4_vs_source_ankle_distance_norm':math.sqrt(sum((Vector(x['v4']['ankle'])-Vector(x['source']['ankle'])).length_squared for x in r)/len(r)),'mean_v4_vs_baseline_ankle_distance_norm':math.sqrt(sum((Vector(x['v4']['ankle'])-Vector(x['approved_baseline']['ankle'])).length_squared for x in r)/len(r))}
 data['summary']=summary
 data['side_view_axes']={'forward':'rest landmark derived forward','up':'rest landmark derived up','source_rest_frame':[[float(src_frame[r][c]) for c in range(3)] for r in range(3)],'target_rest_frame':[[float(tgt_frame[r][c]) for c in range(3)] for r in range(3)],'anatomical_source_to_target_armature_map':[[float(anatomical_map[r][c]) for c in range(3)] for r in range(3)]}
 os.makedirs(os.path.dirname(os.path.abspath(a.output)),exist_ok=True);json.dump(data,open(a.output,'w',encoding='utf-8'),indent=2,allow_nan=False)
 print(json.dumps({'output':os.path.abspath(a.output),'scale':data['scale'],'summary':summary,'axis_map':data['axis_map']}))
if __name__=='__main__':main()
