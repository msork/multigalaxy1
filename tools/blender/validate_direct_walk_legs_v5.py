"""Reopen validation for the geometric-leg V5 walk preview."""
import argparse,json,math,os,sys
import bpy
from mathutils import Vector
HERE=os.path.dirname(os.path.abspath(__file__));sys.path.insert(0,HERE)
import prototype_direct_luigi_walk_contact as engine
import prototype_direct_luigi_walk_legs_v5 as v5

ARM_NAMES=set(engine.ARM_OUTPUTS)|set(engine.FINGER_OUTPUTS)
def parse():
 av=sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else []
 p=argparse.ArgumentParser();p.add_argument('--action',default='Waluigi_DirectGalaxyWalk_v5_GeometricLegs');p.add_argument('--base-action',default='Waluigi_DirectGalaxyWalk_v4_ContactNarrowFists');p.add_argument('--fit-report',required=True);p.add_argument('--report',required=True);return p.parse_args(av)
def sig(action,exclude):
 slot=engine.action_slot(action)
 curves=engine.curves_for_slot(action,slot);out={}
 for c in curves:
  if any(f'pose.bones["{n}"]' in c.data_path for n in exclude):continue
  out[(c.data_path,c.array_index)]=[(tuple(float(v) for v in kp.co),kp.interpolation) for kp in c.keyframe_points]
 return out
def finite_matrix(m):return all(math.isfinite(float(m[r][c])) for r in range(4) for c in range(4))
def main():
 a=parse();scene=bpy.context.scene
 candidates=[o for o in bpy.data.objects if o.type=='ARMATURE' and o.animation_data and o.animation_data.action and o.animation_data.action.name==a.action]
 if len(candidates)!=1:raise RuntimeError(f'expected exactly one rig bound to {a.action}, got {len(candidates)}')
 rig=candidates[0];action=rig.animation_data.action;slot=rig.animation_data.action_slot
 base=bpy.data.actions.get(a.base_action)
 if base is None:raise RuntimeError('V4 reference action missing from preview file')
 meshes=[o for o in bpy.data.objects if o.type=='MESH' and any(m.type=='ARMATURE' and m.object==rig for m in o.modifiers) and not o.hide_get() and not o.hide_viewport]
 visible_armatures=[o for o in bpy.data.objects if o.type=='ARMATURE' and not o.hide_get() and not o.hide_viewport]
 if len(meshes)!=2 or visible_armatures!=[rig]:raise RuntimeError(f'visible-body validation failed: {len(meshes)} meshes; armatures={[o.name for o in visible_armatures]}')
 scales=list(rig.matrix_basis.to_scale());scale_error=max(abs(s-43.437) for s in scales)
 constraints=[(pb.name,c.name) for pb in rig.pose.bones for c in pb.constraints]
 curves=engine.curves_for_slot(action,slot);base_curves=engine.curves_for_slot(base,engine.action_slot(base))
 local_signature_equal=sig(action,{'bone_6',*engine.LEG_OUTPUTS})==sig(base,{'bone_6',*engine.LEG_OUTPUTS})
 # Duplicate the bound rig as a pure action evaluator for independent curve
 # evaluation at the same frame/subframe values.
 probe=engine.duplicate_rig(rig,'V5_ReopenActionProbe');probe.matrix_basis=engine.object_basis(rig);probe.animation_data_clear()
 leg_names=('bone_6','bone_7','bone_8','bone_9','bone_10','bone_11','bone_13','bone_14','bone_15','bone_16','bone_17')
 fit=json.load(open(a.fit_report,encoding='utf-8'));fit_by_frame={r['frame']:r for r in fit['frames']};reference={r['frame']:r['matrices'] for r in fit['final_reopen_reference']}
 tframe=v5.rest_frame(rig.data.bones['bone_12'].tail_local-rig.data.bones['bone_12'].head_local,rig.data.bones['bone_6'].head_local-rig.data.bones['bone_6'].tail_local)
 lengths={'left':rig.data.bones['bone_7'].length+rig.data.bones['bone_8'].length,'right':rig.data.bones['bone_13'].length+rig.data.bones['bone_14'].length}
 frames=[i*.25 for i in range(241)];max_basis_error=0.;max_leg_joint_gap=0.;max_reopen_pose_error=0.;nan_count=0;leg_samples=[];contacts={'left':[],'right':[]}
 for f in frames:
  scene.frame_set(int(f),subframe=f-int(f));bpy.context.view_layer.update()
  actual={n:rig.pose.bones[n].matrix_basis.copy() for n in set(leg_names)|ARM_NAMES}
  # Compare evaluated action state to direct FCurve/channelbag evaluation.
  engine.evaluate_source_curves(probe,base_curves,f);engine.evaluate_source_curves(rig,curves,f)
  for name in ARM_NAMES:
   if name in actual and name in probe.pose.bones:
    m=actual[name];p=probe.pose.bones[name].matrix_basis
    err=max(abs(float(m[r][c]-p[r][c])) for r in range(4) for c in range(4));max_basis_error=max(max_basis_error,err)
  # Restore the actual saved action evaluation for mesh and pose checks.
  scene.frame_set(int(f),subframe=f-int(f));bpy.context.view_layer.update()
  pose={n:rig.pose.bones[n].matrix.copy() for n in set(leg_names)|{'bone_12','bone_18'}}
  if any(not finite_matrix(m) for m in pose.values()):nan_count+=1
  for name,expected in reference[f].items():
   m=pose[name]
   err=max(abs(float(m[r][c])-float(expected[r][c])) for r in range(4) for c in range(4))
   max_reopen_pose_error=max(max_reopen_pose_error,err)
  for side,upper,lower,foot in (('left','bone_7','bone_8','bone_9'),('right','bone_13','bone_14','bone_15')):
   hip=pose[upper].translation;knee=pose[lower].translation;ankle=pose[foot].translation
   l1=(knee-hip).length;l2=(ankle-knee).length
   rest1=rig.data.bones[upper].length;rest2=rig.data.bones[lower].length
   max_leg_joint_gap=max(max_leg_joint_gap,abs(l1-rest1),abs(l2-rest2))
   row={'frame':f,'sides':{}}
   for side,upper,lower,foot,toe in (('left','bone_7','bone_8','bone_9','bone_12'),('right','bone_13','bone_14','bone_15','bone_18')):
    h=pose[upper].translation;k=pose[lower].translation;an=pose[foot].translation
    kn=tframe.transposed()@(k-h)/lengths[side];anv=tframe.transposed()@(an-h)/lengths[side]
    flex=math.acos(max(-1.,min(1.,(k-h).normalized().dot((an-k).normalized()))))
    tip=pose[toe]@Vector((0.,rig.data.bones[toe].length,0.));toev=tframe.transposed()@(tip-an)
    src=fit_by_frame[f]['sides'][side]
    sa=Vector(src['source_ankle']);sk=Vector(src['source_knee']);saxis=sa.normalized();spole=sk-saxis*sk.dot(saxis)
    taxis=anv.normalized();tpole=kn-taxis*kn.dot(taxis)
    pole_dot=max(-1.,min(1.,spole.normalized().dot(tpole.normalized()))) if spole.length>1e-8 and tpole.length>1e-8 else 0.
    row['sides'][side]={'target_ankle':list(anv),'target_knee':list(kn),'target_flex_rad':flex,'source_ankle':src['source_ankle'],'source_knee':src['source_knee'],'source_flex_rad':src['source_flex_rad'],'ankle_error_normalized':(anv-Vector(src['source_ankle'])).length,'knee_error_normalized':(kn-Vector(src['source_knee'])).length,'flex_error_rad':abs(flex-src['source_flex_rad']),'knee_pole_alignment_dot':pole_dot,'toe_direction_error_rad':src['toe_direction_error_rad']}
   leg_samples.append(row)
  wm=v5.mesh_samples(meshes,rig,scene,f)
  for side in contacts:contacts[side].append({'frame':f,'min_z':wm[side]['min_z'],'centroid':wm[side]['centroid'],'count':wm[side]['count']})
 # Destroy evaluator duplicate.
 pdata=probe.data;bpy.data.objects.remove(probe,do_unlink=True)
 if pdata.users==0:bpy.data.armatures.remove(pdata)
 # Contact windows from the source trajectory audit.
 intervals={'left':[(29.75,30.25)],'right':[(0.,.75),(58.75,60.)]};slide={};clearance={}
 for side in contacts:
  group=[]
  for lo,hi in intervals[side]:
   seq=[x for x in contacts[side] if lo<=x['frame']<=hi]
   group.append(max(((Vector(seq[i+1]['centroid'])-Vector(seq[i]['centroid'])).length for i in range(len(seq)-1)),default=0.))
  slide[side]=max(group,default=0.)
  vals=[x['min_z'] for x in contacts[side] if any(lo<=x['frame']<=hi for lo,hi in intervals[side])]
  clearance[side]=[min(vals),max(vals)]
 all_min=min(x['min_z'] for rows in contacts.values() for x in rows)
 all_ankle=[x['sides'][s]['ankle_error_normalized'] for x in leg_samples for s in ('left','right')];all_knee=[x['sides'][s]['knee_error_normalized'] for x in leg_samples for s in ('left','right')];all_flex=[x['sides'][s]['flex_error_rad'] for x in leg_samples for s in ('left','right')]
 pole_dots={s:[x['sides'][s]['knee_pole_alignment_dot'] for x in leg_samples] for s in ('left','right')}
 result={'preview':bpy.data.filepath,'action':action.name,'slot':slot.identifier if slot else None,'frames':len(frames),'sample_step':.25,'visible_target_meshes':[o.name for o in meshes],'visible_armatures':[o.name for o in visible_armatures],'arm_fist_curves_equal_v4':local_signature_equal,'max_arm_fist_basis_error_vs_v4':max_basis_error,'max_reopened_pose_matrix_error':max_reopen_pose_error,'max_leg_segment_length_error_armature_units':max_leg_joint_gap,'scale':scales,'scale_error':scale_error,'constraint_count':len(constraints),'nan_pose_samples':nan_count,'stance_centroid_slide_world':slide,'stance_ground_clearance_world':clearance,'max_ground_penetration_world':max(0.,-all_min),'all_frame_min_z_world':all_min,'final_ankle_trajectory_rmse_normalized':math.sqrt(sum(x*x for x in all_ankle)/len(all_ankle)),'final_knee_trajectory_rmse_normalized':math.sqrt(sum(x*x for x in all_knee)/len(all_knee)),'final_knee_flex_rmse_rad':math.sqrt(sum(x*x for x in all_flex)/len(all_flex)),'max_final_knee_flex_error_rad':max(all_flex),'knee_pole_alignment_dot_range':{s:[min(pole_dots[s]),max(pole_dots[s])] for s in pole_dots},'final_leg_trajectories':leg_samples,'contacts':contacts}
 gates={'single_visible_character':len(meshes)==2 and visible_armatures==[rig],'arms_fists_unchanged':local_signature_equal and max_basis_error<1e-5,'reopened_action_matches_fit':max_reopen_pose_error<1e-5,'scale_preserved':scale_error<.01,'no_constraints':not constraints,'finite_pose':nan_count==0,'leg_segments_reproduced':max_leg_joint_gap<.01,'stance_slide':max(slide.values())<.5,'stance_clearance':all(-.5<=v[0] and v[1]<=1. for v in clearance.values()),'no_ground_penetration':all_min>=-.5}
 result['gates']=gates;result['passed']=all(gates.values())
 os.makedirs(os.path.dirname(os.path.abspath(a.report)),exist_ok=True);json.dump(result,open(a.report,'w',encoding='utf-8'),indent=2,allow_nan=False)
 print(json.dumps({'report':os.path.abspath(a.report),'passed':result['passed'],'gates':gates,'max_arm_basis_error':max_basis_error,'max_leg_segment_error':max_leg_joint_gap,'slide':slide,'clearance':clearance,'max_penetration':result['max_ground_penetration_world']}))
if __name__=='__main__':main()
