"""Bake clip-specific V4 corrections from anatomical joint targets, not bone-local amplitude factors."""
import argparse,hashlib,json,math,os,sys
import bpy
from mathutils import Matrix,Quaternion,Vector
HERE=os.path.dirname(os.path.abspath(__file__));sys.path.insert(0,HERE)
from prototype_direct_luigi_walk_literal import bag_for,duplicate_rig,evaluate_curves,pose_matrices_from_basis,set_armature_pose_matrix,setup_render,slot_for
from diagnose_v4_anatomical_trajectories import anatomy,body_frame

def cli():
 a=sys.argv; a=a[a.index('--')+1:] if '--' in a else []
 p=argparse.ArgumentParser();p.add_argument('--clip',required=True);p.add_argument('--profile',default=os.path.join(HERE,'profiles','luigi_clip_anatomical_corrections_v4.json'));p.add_argument('--target-rig',default='LiteralWalk_Waluigi_Target');p.add_argument('--source-rig',default='luigi_armature');p.add_argument('--mesh-prefix',default='LiteralWalk_ARMTEST_');p.add_argument('--output',required=True);p.add_argument('--report',required=True);p.add_argument('--renders',required=True);p.add_argument('--step',type=float,default=.25);return p.parse_args(a)

def digest(value):return hashlib.sha256(json.dumps(value,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def matrix_basis(rig):return {pb.name:pb.matrix_basis.copy() for pb in rig.pose.bones}
def basis_rows(rig):return {pb.name:[[round(float(x),7) for x in row] for row in pb.matrix_basis] for pb in rig.pose.bones}
def action_curves(action,slot,bone):
 path=f'pose.bones["{bone}"].rotation_quaternion';rows={int(fc.array_index):fc for fc in bag_for(action,slot).fcurves if fc.data_path==path}
 if set(rows)!={0,1,2,3}:raise RuntimeError(f'{action.name}: incomplete quaternion curves on {bone}')
 return rows
def window_weight(frame,start,end,fade):
 def ss(x):x=max(0.0,min(1.0,x));return x*x*(3-2*x)
 if start<=frame<=end:return 1.0
 if start-fade<frame<start:return ss((frame-(start-fade))/fade)
 if end<frame<end+fade:return ss(1-(frame-end)/fade)
 return 0.0
def apply_world_delta(rig,bone,pose_mats,axis_world,angle):
 if abs(angle)<1e-9:return pose_mats
 current=pose_mats[bone];objrot=rig.matrix_world.to_3x3().to_quaternion().to_matrix()
 current_world=objrot@current.to_3x3();delta=Quaternion(Vector(axis_world).normalized(),float(angle)).to_matrix()
 desired_arm=objrot.transposed()@delta@current_world
 desired=Matrix.LocRotScale(current.translation,desired_arm.to_quaternion(),current.to_scale())
 set_armature_pose_matrix(rig,bone,desired,pose_mats)
 return pose_matrices_from_basis(rig)
def vec_in_body(frame,vector):return frame.transposed()@Vector(vector)
def world_point(rig,poses,name,endpoint='head'):
 p=poses[name].translation
 return rig.matrix_world@p
def arm_length(rig,side):
 b=rig.data.bones;mw=rig.matrix_world
 if side=='source':return (mw@b['ArmL2'].head_local-mw@b['ShoulderL'].head_local).length+(mw@b['HandL'].head_local-mw@b['ArmL2'].head_local).length
 elbow=(b['bone_26'].head_local+b['bone_29'].head_local)*.5;wrist=(b['bone_27'].tail_local+b['bone_30'].head_local)*.5
 return (mw@elbow-mw@b['bone_22'].head_local).length+(mw@wrist-mw@elbow).length
def normalize_landmarks(row,length):
 for joint in ('shoulder','elbow','wrist'):
  row[joint+'_normalized']=[float(x)/length for x in row[joint]]
 return row
def shoulder_yaw_solution(src_an,tgt_an,max_angle):
 # Fit only torso-horizontal reach; never pull a correctly forward target backward.
 desired={}
 for joint in ('elbow','wrist'):
  sz=src_an[joint+'_normalized'][2]-src_an['shoulder_normalized'][2]
  tz=tgt_an[joint+'_normalized'][2]-tgt_an['shoulder_normalized'][2]
  if sz>tz+0.005:desired[joint]=sz
 if not desired:return 0.0
 # For a left shoulder and torso-up yaw, this grid search finds the smallest
 # signed global rotation that reduces measured backward reach error.
 xs={j:tgt_an[j+'_normalized'][0]-tgt_an['shoulder_normalized'][0] for j in desired}
 zs={j:tgt_an[j+'_normalized'][2]-tgt_an['shoulder_normalized'][2] for j in desired}
 best=(float('inf'),0.0)
 for i in range(161):
  a=-max_angle+2*max_angle*i/160
  c,s=math.cos(a),math.sin(a)
  loss=0.0
  for j,goal in desired.items():
   znew=-s*xs[j]+c*zs[j]
   loss+=(znew-goal)**2
  if loss<best[0]:best=(loss,a)
 return best[1]
def yaw_align_vectors(source_dir,target_dir,max_angle):
 a=Vector(target_dir);b=Vector(source_dir)
 # Project into lateral/forward; keep elevation unchanged.
 ta=math.atan2(a.z,a.x);sa=math.atan2(b.z,b.x)
 # A positive Blender rotation around torso-up decreases atan2(z,x).
 d=(ta-sa+math.pi)%(2*math.pi)-math.pi
 return max(-max_angle,min(max_angle,d))
def best_shoulder_yaw_for_elbow(rig,source_elbow,max_angle,length):
 saved=matrix_basis(rig);poses=pose_matrices_from_basis(rig);frame,_,_=body_frame(rig,poses);axis=frame.col[1].normalized();best=(float('inf'),0.0)
 for i in range(77):
  angle=-max_angle+2*max_angle*i/76
  for name,basis in saved.items():rig.pose.bones[name].matrix_basis=basis
  apply_world_delta(rig,'bone_22',pose_matrices_from_basis(rig),axis,angle)
  candidate=normalize_landmarks(anatomy(rig,pose_matrices_from_basis(rig),'left'),length)
  score=math.dist(source_elbow,candidate['elbow_normalized'])
  if score<best[0]-1e-10 or (abs(score-best[0])<1e-10 and abs(angle)<abs(best[1])):best=(score,angle)
 for name,basis in saved.items():rig.pose.bones[name].matrix_basis=basis
 if abs(best[1])>1e-8:apply_world_delta(rig,'bone_22',pose_matrices_from_basis(rig),axis,best[1])
 return best[1],best[0]
def best_forearm_branch_delta(rig,source_direction,desired_q,max_strength,max_angle):
 saved={name:rig.pose.bones[name].matrix_basis.copy() for name in ('bone_26','bone_29')}
 q,_=cap_quaternion(desired_q,max_angle);base_angle=q.angle*max_strength
 start=pose_matrices_from_basis(rig);frame,_,_=body_frame(rig,start);world_delta=frame@Quaternion(q.axis,base_angle).to_matrix()@frame.transposed();world_q=world_delta.to_quaternion()
 best=(float('inf'),0.0)
 for i in range(41):
  amount=i/40
  for name,basis in saved.items():rig.pose.bones[name].matrix_basis=basis
  poses=pose_matrices_from_basis(rig)
  for bone in ('bone_26','bone_29'):poses=apply_world_delta(rig,bone,poses,world_q.axis,world_q.angle*amount)
  cand=anatomy(rig,pose_matrices_from_basis(rig),'left')
  err=Vector(source_direction).normalized().angle(Vector(cand['forearm_direction']).normalized())
  if err<best[0]-1e-9 or (abs(err-best[0])<1e-9 and amount<best[1]):best=(err,amount)
 for name,basis in saved.items():rig.pose.bones[name].matrix_basis=basis
 poses=pose_matrices_from_basis(rig)
 for bone in ('bone_26','bone_29'):poses=apply_world_delta(rig,bone,poses,world_q.axis,world_q.angle*best[1])
 return best[1],best[0]
def equivalent_quaternion_angle(q):
 q=q.normalized()
 if q.w<0:q.negate()
 return float(q.angle)
def smooth_correction_series(values,weights=3.0,acceleration=15.0):
 import numpy as np
 n=len(values);h=np.eye(n,dtype=float);v=np.asarray(values,dtype=float)
 for i in range(n-1):
  h[i,i]+=weights;h[i+1,i+1]+=weights;h[i,i+1]-=weights;h[i+1,i]-=weights
 for i in range(n-2):
  d=np.zeros(n);d[i:i+3]=(1.0,-2.0,1.0);h+=acceleration*np.outer(d,d)
 return np.linalg.solve(h,v).tolist()
def blend_and_rate_limit_vectors(raw,smooth,max_step=0.12):
 rows=[Vector(a)*.7+Vector(b)*.3 for a,b in zip(raw,smooth)]
 for _ in range(3):
  for i in range(1,len(rows)):
   d=rows[i]-rows[i-1]
   if d.length>max_step:rows[i]=rows[i-1]+d.normalized()*max_step
  for i in range(len(rows)-2,-1,-1):
   d=rows[i]-rows[i+1]
   if d.length>max_step:rows[i]=rows[i+1]+d.normalized()*max_step
 return rows
def cap_quaternion(q,max_angle):
 q.normalize()
 if q.w<0:q.negate()
 angle=q.angle
 return Quaternion((1,0,0,0)).slerp(q,min(1.0,max_angle/max(angle,1e-9))),min(angle,max_angle)
def q_from_matrix(m):return m.to_quaternion().normalized()
def wrist_delta(source_rig,target_rig,source_pose,target_pose,source_frame,target_frame):
 so=source_rig.matrix_world.to_3x3().to_quaternion().to_matrix();to=target_rig.matrix_world.to_3x3().to_quaternion().to_matrix()
 sb=source_frame.transposed();tb=target_frame.transposed()
 def body_pose(objrot,frame,m):return frame.transposed()@objrot@m.to_3x3()
 sfp=body_pose(so,source_frame,source_pose['ArmL2']);shp=body_pose(so,source_frame,source_pose['HandL'])
 sfrest=source_rig.data.bones['ArmL2'].matrix_local.to_3x3();shrest=source_rig.data.bones['HandL'].matrix_local.to_3x3()
 sfr=sb@so@sfrest;shr=sb@so@shrest
 rel_sp=q_from_matrix(sfp).inverted()@q_from_matrix(shp);rel_sr=q_from_matrix(sfr).inverted()@q_from_matrix(shr)
 motion=rel_sp@rel_sr.inverted()
 tfp=body_pose(to,target_frame,target_pose['bone_27']);thp=body_pose(to,target_frame,target_pose['bone_30'])
 tfrest=target_rig.data.bones['bone_27'].matrix_local.to_3x3();threst=target_rig.data.bones['bone_30'].matrix_local.to_3x3()
 tfr=tb@to@tfrest;thr=tb@to@threst
 rel_tr=q_from_matrix(tfr).inverted()@q_from_matrix(thr)
 rel_tp_des=motion@rel_tr
 hand_des=q_from_matrix(tfp)@rel_tp_des
 hand_now=q_from_matrix(thp)
 return hand_des@hand_now.inverted()
def mesh_points(meshes):
 dg=bpy.context.evaluated_depsgraph_get();out={}
 for o in meshes:
  ev=o.evaluated_get(dg);me=ev.to_mesh()
  try:
   pts=[[float(c) for c in ev.matrix_world@v.co] for v in me.vertices]
   if any(not math.isfinite(c) for p in pts for c in p):raise RuntimeError(f'nonfinite mesh vertex: {o.name}')
   out[o.name]=pts
  finally:ev.to_mesh_clear()
 return out
def timing_signature(bag):return sorted((fc.data_path,int(fc.array_index),tuple(round(float(p.co.x),7) for p in fc.keyframe_points),tuple(p.interpolation for p in fc.keyframe_points)) for fc in bag.fcurves)

def main():
 a=cli();srcblend=os.path.abspath(bpy.data.filepath);out=os.path.abspath(a.output)
 if not srcblend or srcblend==out:raise RuntimeError('V4 must write a new disposable blend')
 profile=json.load(open(a.profile,encoding='utf-8'));cfg=profile['clips'].get(a.clip)
 if profile.get('schema')!='mg1.literal-retarget-anatomical-corrections.v4' or not cfg:raise RuntimeError('unsupported or missing V4 clip profile')
 target=bpy.data.objects.get(a.target_rig);source=bpy.data.objects.get(a.source_rig)
 if not target or not source or target.type!='ARMATURE' or source.type!='ARMATURE':raise RuntimeError('source/target rigs missing')
 if max(abs(float(x)-43.437) for x in target.scale)>.02:raise RuntimeError('Waluigi intentional scale signature mismatch')
 if not target.animation_data or not target.animation_data.action:raise RuntimeError('V3 target action missing')
 v3action=target.animation_data.action;v3slot=target.animation_data.action_slot
 if a.clip not in v3action.name:raise RuntimeError(f'V3 action {v3action.name} does not match clip')
 v3action.use_fake_user=True
 v4action=v3action.copy();v4action.name=f'Waluigi_{a.clip}_Anatomical_Surgical_V4';v4action.use_fake_user=True
 v4slot=next((s for s in v4action.slots if s.identifier==v3slot.identifier),None)
 if v4slot is None:
  if len(v4action.slots)!=1:raise RuntimeError('V4 action slot ambiguous')
  v4slot=v4action.slots[0]
 # Source native BCK curves are evaluated manually on a clean copy, retaining
 # each source bone's actual Euler/quaternion mode.
 source_action=bpy.data.actions.get(a.clip)
 if not source_action:raise RuntimeError(f'source action missing: {a.clip}')
 source_slot=next((s for s in source_action.slots if source.animation_data and source.animation_data.action_slot and s.identifier==source.animation_data.action_slot.identifier),None)
 if source_slot is None:
  if len(source_action.slots)!=1:raise RuntimeError('source action slot ambiguous')
  source_slot=source_action.slots[0]
 source_curves=list(bag_for(source_action,source_slot).fcurves)
 source_eval=duplicate_rig(source,'V4_Source_Evaluator')
 for orig,copy in zip(source.pose.bones,source_eval.pose.bones):copy.rotation_mode=orig.rotation_mode
 source_eval.animation_data_clear()
 target_meshes=sorted([o for o in bpy.data.objects if o.type=='MESH' and o.name.startswith(a.mesh_prefix) and any(m.type=='ARMATURE' and m.object==target for m in o.modifiers)],key=lambda o:o.name)
 if len(target_meshes)!=2:raise RuntimeError(f'expected 2 target meshes, got {len(target_meshes)}')
 v3bag=bag_for(v3action,v3slot);v4bag=bag_for(v4action,v4slot);original_timing=timing_signature(v3bag)
 start,end=map(float,v3action.frame_range);count=int(round((end-start)/a.step));frames=[round(start+i*a.step,7) for i in range(count+1)]
 if abs(frames[-1]-end)>1e-5:frames.append(end)
 scene=bpy.context.scene;old_frame=(scene.frame_current,scene.frame_subframe)
 baseline_basis={};baseline_location={};correction_basis={};applied_bones_by_frame={};corrections={};anatomical_before_after={}
 run_raw_yaw={};run_source_an={};run_target_an={};source_an_cache={};target_an_cache={};swim_raw_rotation={}
 active_bones=set()
 if cfg['mode']=='backward_reach_clamp':active_bones={'bone_22'}
 elif cfg['mode']=='wrist_relative_forearm_match':active_bones={'bone_30'}
 elif cfg['mode']=='independent_elbow_and_forearm_targets':active_bones={'bone_22','bone_26','bone_29'}
 else:raise RuntimeError('unknown V4 mode')
 target.animation_data.action=v3action;target.animation_data.action_slot=v3slot
 for frame in frames:
  n=math.floor(frame);scene.frame_set(n,subframe=frame-n);bpy.context.view_layer.update()
  baseline_basis[frame]=matrix_basis(target);baseline_location[frame]=[float(x) for x in target.location]
  evaluate_curves(source_eval,source_curves,frame);source_poses=pose_matrices_from_basis(source_eval)
  target_poses=pose_matrices_from_basis(target)
  source_an=anatomy(source_eval,source_poses,'left');target_an=anatomy(target,target_poses,'left')
  source_arm_length=arm_length(source_eval,'source');target_arm_length=arm_length(target,'target')
  normalize_landmarks(source_an,source_arm_length);normalize_landmarks(target_an,target_arm_length)
  source_an_cache[frame]=dict(source_an);target_an_cache[frame]=dict(target_an)
  angle_log=0.0;applied={};wrist_error_before=None
  target.animation_data.action=None
  for name,m in baseline_basis[frame].items():target.pose.bones[name].matrix_basis=m
  target.location=baseline_location[frame];bpy.context.view_layer.update();target_poses=pose_matrices_from_basis(target)
  frame_matrix,_,_=body_frame(target,target_poses);up_axis=frame_matrix.col[1].normalized()
  if cfg['mode']=='backward_reach_clamp':
   yaw=shoulder_yaw_solution(source_an,target_an,float(cfg['max_shoulder_yaw_rad']))
   envelope=max((window_weight(frame,lo,hi,fade) for lo,hi,fade in cfg['windows']),default=0.0)
   yaw*=envelope
   run_raw_yaw[frame]=yaw;run_source_an[frame]=dict(source_an);run_target_an[frame]=dict(target_an)
   if abs(yaw)>1e-7:
    target_poses=apply_world_delta(target,'bone_22',target_poses,up_axis,yaw);applied={'bone_22':yaw};angle_log=abs(yaw)
  elif cfg['mode']=='wrist_relative_forearm_match':
   delta=wrist_delta(source_eval,target,source_poses,target_poses,body_frame(source_eval,source_poses)[0],frame_matrix)
   wrist_error_before=equivalent_quaternion_angle(delta)
   capped,delta_angle=cap_quaternion(delta,float(cfg['max_wrist_delta_rad']))
   weighted=[]
   for lo,hi,fade,factor in cfg['windows']:
    weight=window_weight(frame,lo,hi,fade)
    if weight>0:weighted.append((float(factor),weight))
   strength=(sum(f*w for f,w in weighted)/sum(w for _,w in weighted))*max((w for _,w in weighted),default=0.0) if weighted else 0.0
   q=Quaternion((1,0,0,0)).slerp(capped,strength)
   if q.angle>1e-7:
    objrot=target.matrix_world.to_3x3().to_quaternion().to_matrix();world_delta=frame_matrix@q.to_matrix()@frame_matrix.transposed()
    world_q=world_delta.to_quaternion()
    target_poses=apply_world_delta(target,'bone_30',target_poses,world_q.axis,world_q.angle)
    applied={'bone_30':float(q.angle)};angle_log=float(q.angle)
  else:
   center,fade,elbow_factor=cfg['elbow_frame'];w=float(elbow_factor)*window_weight(frame,float(center),float(center),float(fade))
   if w>0:
    yaw,_=best_shoulder_yaw_for_elbow(target,source_an['elbow_normalized'],float(cfg['max_shoulder_yaw_rad'])*w,target_arm_length)
    if abs(yaw)>1e-7:target_poses=pose_matrices_from_basis(target);applied['bone_22']=yaw;angle_log=max(angle_log,abs(yaw))
   lo,hi,fade,factor=cfg['forearm_window'];w=factor*window_weight(frame,lo,hi,fade)
   if w>0:
    sv=Vector(source_an['forearm_direction']).normalized();tv=Vector(target_an['forearm_direction']).normalized()
    q=tv.rotation_difference(sv);amount,_=best_forearm_branch_delta(target,source_an['forearm_direction'],q,w,float(cfg['max_forearm_delta_rad']))
    if amount>1e-7:
     target_poses=pose_matrices_from_basis(target);capped_q,_=cap_quaternion(q,float(cfg['max_forearm_delta_rad']));ang=capped_q.angle*w*amount
     body_delta=frame_matrix@Quaternion(capped_q.axis,capped_q.angle*w).to_matrix()@frame_matrix.transposed();world_q=body_delta.to_quaternion()
     swim_raw_rotation[frame]=[float(c*world_q.angle*amount) for c in world_q.axis]
     applied.update({'bone_26':ang,'bone_29':ang});angle_log=max(angle_log,abs(ang))
  correction_basis[frame]={name:target.pose.bones[name].matrix_basis.copy() for name in applied}
  applied_bones_by_frame[frame]=set(applied)
  if applied:
   corrections[str(frame)]={'applied_rotation_rad':applied,'max_delta_rad':angle_log}
   after_poses=pose_matrices_from_basis(target);after_an=normalize_landmarks(anatomy(target,after_poses,'left'),target_arm_length)
   position_errors={}
   for joint in ('elbow','wrist'):
    position_errors[joint]={'v3':math.dist(source_an[joint+'_normalized'],target_an[joint+'_normalized']),
                            'v4':math.dist(source_an[joint+'_normalized'],after_an[joint+'_normalized'])}
   direction_errors={}
   for direction in ('upper_direction','forearm_direction'):
    sv=Vector(source_an[direction]).normalized();tv=Vector(target_an[direction]).normalized();av=Vector(after_an[direction]).normalized()
    direction_errors[direction]={'v3_rad':sv.angle(tv),'v4_rad':sv.angle(av)}
   wrist_error_after=None
   if cfg['mode']=='wrist_relative_forearm_match':
    wrist_error_after=equivalent_quaternion_angle(wrist_delta(source_eval,target,source_poses,after_poses,body_frame(source_eval,source_poses)[0],frame_matrix))
   anatomical_before_after[str(frame)]={'source_elbow_normalized':source_an['elbow_normalized'],'source_wrist_normalized':source_an['wrist_normalized'],
     'v3_elbow_normalized':target_an['elbow_normalized'],'v3_wrist_normalized':target_an['wrist_normalized'],
     'v4_elbow_normalized':after_an['elbow_normalized'],'v4_wrist_normalized':after_an['wrist_normalized'],
     'source_forearm_direction':source_an['forearm_direction'],'v3_forearm_direction':target_an['forearm_direction'],'v4_forearm_direction':after_an['forearm_direction'],
     'source_elbow_flex_rad':source_an['elbow_flex_rad'],'v3_elbow_flex_rad':target_an['elbow_flex_rad'],'v4_elbow_flex_rad':after_an['elbow_flex_rad'],
     'normalized_landmark_error':position_errors,'direction_error_rad':direction_errors,
     'wrist_orientation_residual_rad':{'v3':wrist_error_before,'v4':wrist_error_after} if wrist_error_before is not None else None}
  target.animation_data.action=v3action;target.animation_data.action_slot=v3slot
 if cfg['mode']=='backward_reach_clamp':
  ordered=sorted(frames);smoothed=smooth_correction_series([run_raw_yaw[f] for f in ordered]);corrections.clear();anatomical_before_after.clear()
  for f,yaw in zip(ordered,smoothed):
   envelope=max((window_weight(f,lo,hi,fade) for lo,hi,fade in cfg['windows']),default=0.0)
   if envelope<=0:yaw=0.0
   yaw=max(-float(cfg['max_shoulder_yaw_rad']),min(float(cfg['max_shoulder_yaw_rad']),yaw))
   for name,m in baseline_basis[f].items():target.pose.bones[name].matrix_basis=m
   target.location=baseline_location[f];target.animation_data.action=None;bpy.context.view_layer.update();poses=pose_matrices_from_basis(target)
   frame_matrix,_,_=body_frame(target,poses);applied={}
   if abs(yaw)>1e-7:
    poses=apply_world_delta(target,'bone_22',poses,frame_matrix.col[1].normalized(),yaw);applied={'bone_22':yaw}
   correction_basis[f]={name:target.pose.bones[name].matrix_basis.copy() for name in applied};applied_bones_by_frame[f]=set(applied)
   if applied:
    corrections[str(f)]={'applied_rotation_rad':applied,'max_delta_rad':abs(yaw)}
    sa=run_source_an[f];ta=run_target_an[f];after=normalize_landmarks(anatomy(target,pose_matrices_from_basis(target),'left'),arm_length(target,'target'))
    position_errors={j:{'v3':math.dist(sa[j+'_normalized'],ta[j+'_normalized']),'v4':math.dist(sa[j+'_normalized'],after[j+'_normalized'])} for j in ('elbow','wrist')}
    direction_errors={}
    for direction in ('upper_direction','forearm_direction'):
     direction_errors[direction]={'v3_rad':Vector(sa[direction]).angle(Vector(ta[direction])),'v4_rad':Vector(sa[direction]).angle(Vector(after[direction]))}
    anatomical_before_after[str(f)]={'source_elbow_normalized':sa['elbow_normalized'],'source_wrist_normalized':sa['wrist_normalized'],
     'v3_elbow_normalized':ta['elbow_normalized'],'v3_wrist_normalized':ta['wrist_normalized'],'v4_elbow_normalized':after['elbow_normalized'],'v4_wrist_normalized':after['wrist_normalized'],
     'source_forearm_direction':sa['forearm_direction'],'v3_forearm_direction':ta['forearm_direction'],'v4_forearm_direction':after['forearm_direction'],
     'source_elbow_flex_rad':sa['elbow_flex_rad'],'v3_elbow_flex_rad':ta['elbow_flex_rad'],'v4_elbow_flex_rad':after['elbow_flex_rad'],
     'normalized_landmark_error':position_errors,'direction_error_rad':direction_errors,'wrist_orientation_residual_rad':None}
  target.animation_data.action=v3action;target.animation_data.action_slot=v3slot
 elif cfg['mode']=='independent_elbow_and_forearm_targets':
  ordered=sorted(frames);raw_vectors=[Vector(swim_raw_rotation.get(f,[0.0,0.0,0.0])) for f in ordered]
  raw_magnitudes=[v.length for v in raw_vectors];smooth_magnitudes=smooth_correction_series(raw_magnitudes)
  window=cfg['forearm_window'];magnitudes=[.7*x+.3*y for x,y in zip(raw_magnitudes,smooth_magnitudes)]
  for k,f in enumerate(ordered):
   if window_weight(f,float(window[0]),float(window[1]),float(window[2]))<=0:magnitudes[k]=0.0
  for _ in range(3):
   for k in range(1,len(magnitudes)):magnitudes[k]=min(magnitudes[k-1]+.12,max(magnitudes[k-1]-.12,magnitudes[k]))
   for k in range(len(magnitudes)-2,-1,-1):magnitudes[k]=min(magnitudes[k+1]+.12,max(magnitudes[k+1]-.12,magnitudes[k]))
  axes=[]
  for k,v in enumerate(raw_vectors):
   if v.length>1e-8:axes.append(v.normalized());continue
   nearest=min((j for j in range(len(raw_vectors)) if raw_vectors[j].length>1e-8),key=lambda j:abs(j-k),default=None)
   axes.append(raw_vectors[nearest].normalized() if nearest is not None else Vector((0,0,0)))
  final_vectors=[axes[k]*magnitudes[k] for k in range(len(ordered))]
  initial={f:dict(corrections.get(str(f),{}).get('applied_rotation_rad',{})) for f in ordered};corrections.clear();anatomical_before_after.clear()
  for k,f in enumerate(ordered):
   for name,m in baseline_basis[f].items():target.pose.bones[name].matrix_basis=m
   target.location=baseline_location[f];target.animation_data.action=None;bpy.context.view_layer.update();poses=pose_matrices_from_basis(target)
   frame_matrix,_,_=body_frame(target,poses);applied={};shoulder=initial[f].get('bone_22',0.0)
   if abs(shoulder)>1e-7:
    poses=apply_world_delta(target,'bone_22',poses,frame_matrix.col[1].normalized(),shoulder);applied['bone_22']=shoulder
   vec=final_vectors[k]
   angle=vec.length
   if angle>1e-7:
    for bone in ('bone_26','bone_29'):poses=apply_world_delta(target,bone,poses,vec.normalized(),angle)
    applied.update({'bone_26':angle,'bone_29':angle})
   correction_basis[f]={name:target.pose.bones[name].matrix_basis.copy() for name in applied};applied_bones_by_frame[f]=set(applied)
   if applied:
    corrections[str(f)]={'applied_rotation_rad':applied,'max_delta_rad':max(abs(x) for x in applied.values())}
    sa=source_an_cache[f];ta=target_an_cache[f];after=normalize_landmarks(anatomy(target,pose_matrices_from_basis(target),'left'),arm_length(target,'target'))
    pe={j:{'v3':math.dist(sa[j+'_normalized'],ta[j+'_normalized']),'v4':math.dist(sa[j+'_normalized'],after[j+'_normalized'])} for j in ('elbow','wrist')}
    de={name:{'v3_rad':Vector(sa[name]).angle(Vector(ta[name])),'v4_rad':Vector(sa[name]).angle(Vector(after[name]))} for name in ('upper_direction','forearm_direction')}
    anatomical_before_after[str(f)]={'source_elbow_normalized':sa['elbow_normalized'],'source_wrist_normalized':sa['wrist_normalized'],
     'v3_elbow_normalized':ta['elbow_normalized'],'v3_wrist_normalized':ta['wrist_normalized'],'v4_elbow_normalized':after['elbow_normalized'],'v4_wrist_normalized':after['wrist_normalized'],
     'source_forearm_direction':sa['forearm_direction'],'v3_forearm_direction':ta['forearm_direction'],'v4_forearm_direction':after['forearm_direction'],
     'source_elbow_flex_rad':sa['elbow_flex_rad'],'v3_elbow_flex_rad':ta['elbow_flex_rad'],'v4_elbow_flex_rad':after['elbow_flex_rad'],
     'normalized_landmark_error':pe,'direction_error_rad':de,'wrist_orientation_residual_rad':None}
  target.animation_data.action=v3action;target.animation_data.action_slot=v3slot
 # Write only rotations for explicitly affected control/deformation roots.
 previous_quat={}
 for bone in active_bones:
  curves=action_curves(v4action,v4slot,bone)
  for frame in frames:
   q=correction_basis[frame].get(bone)
   if q is None:continue
   quat=q.to_quaternion().normalized()
   # Keep sign aligned with original samples for shortest interpolation.
   old=Quaternion(tuple(float(curves[i].evaluate(frame)) for i in range(4))).normalized()
   if quat.dot(old)<0:quat.negate()
   points=[]
   for i,fc in curves.items():
    point=next((p for p in fc.keyframe_points if abs(float(p.co.x)-frame)<1e-5),None)
    if point is None:raise RuntimeError(f'missing quarter-frame key: {bone}@{frame}')
    point.co.y=float(quat[i]);points.append(point)
   for fc in curves.values():fc.update()
 timing_ok=timing_signature(v4bag)==original_timing
 if not timing_ok:raise RuntimeError('V4 changed original key times or interpolation')
 target.animation_data.action=v4action;target.animation_data.action_slot=v4slot
 meshes=target_meshes
 expected={};max_vertex_delta=0.0;max_step=0.0;prev=None;max_noncorrected=0.0;max_root=0.0
 max_active_outside_window=0.0
 for f in frames:
  n=math.floor(f);scene.frame_set(n,subframe=f-n);bpy.context.view_layer.update()
  b=basis_rows(target);allmesh=mesh_points(meshes);loc=[float(x) for x in target.location]
  for name,old in baseline_basis[f].items():
   if name not in active_bones:max_noncorrected=max(max_noncorrected,max(abs(float(target.pose.bones[name].matrix_basis[r][c])-float(old[r][c])) for r in range(4) for c in range(4)))
   elif name not in applied_bones_by_frame[f]:max_active_outside_window=max(max_active_outside_window,max(abs(float(target.pose.bones[name].matrix_basis[r][c])-float(old[r][c])) for r in range(4) for c in range(4)))
  max_root=max(max_root,max(abs(x-y) for x,y in zip(loc,baseline_location[f])))
  current={}
  for obj in meshes:current[obj.name]=allmesh[obj.name]
  if prev is not None:max_step=max(max_step,max(math.dist(x,y) for obj in meshes for x,y in zip(current[obj.name],prev[obj.name])))
  prev=current
  expected[str(f)]={'pose':digest({'basis':b,'location':[round(x,7) for x in loc],'scale':[round(float(x),7) for x in target.scale]}),
                    'mesh':{name:digest([[round(c,6) for c in p] for p in pts]) for name,pts in allmesh.items()}}
 if max_noncorrected>5e-6 or max_active_outside_window>5e-6 or max_root>1e-7:raise RuntimeError(f'unaffected channels/root changed: {max_noncorrected}, {max_active_outside_window}, {max_root}')
 clip_frames={'luigi_armature_luigirun':[0,5,8,12,14,24,30,36,44,46,61],
              'luigi_armature_luigijump':[0,9,12,14,15,20,24],
              'luigi_armature_swimbreast':[0,5,10,11,12,13,14,20,59]}
 preview=[f for f in clip_frames[a.clip] if start<=f<=end];poses={};roots={}
 for f in preview:
  n=math.floor(f);scene.frame_set(n,subframe=f-n);bpy.context.view_layer.update();poses[f]={pb.name:[list(row) for row in pb.matrix_basis] for pb in target.pose.bones};roots[float(f)]=[float(x) for x in target.location]
 for obj in bpy.data.objects:
  if obj.type in {'CAMERA','LIGHT'}:continue
  visible=obj==target or obj in meshes;obj.hide_render=not visible;obj.hide_viewport=not visible;obj.hide_set(not visible)
 renders,render_fail=setup_render(scene,target,meshes,os.path.abspath(a.renders),poses,roots)
 scene.frame_start=math.floor(start);scene.frame_end=math.ceil(end);scene.frame_set(math.floor(old_frame[0]),subframe=old_frame[1])
 bpy.data.objects.remove(source_eval,do_unlink=True)
 os.makedirs(os.path.dirname(out),exist_ok=True);bpy.ops.wm.save_as_mainfile(filepath=out)
 constraints=len(target.constraints)+sum(len(pb.constraints) for pb in target.pose.bones)
 correction_steps={}
 for bone in sorted(active_bones):
  values=[corrections.get(str(f),{}).get('applied_rotation_rad',{}).get(bone,0.0) for f in frames]
  correction_steps[bone]=max((abs(y-x) for x,y in zip(values,values[1:])),default=0.0)
 report={'schema':'mg1.literal-retarget-anatomical-v4-validation.v1','input_v3_blend':srcblend,'output_blend':out,'source_action':a.clip,
  'input_action':v3action.name,'output_action':v4action.name,'profile':os.path.abspath(a.profile),'correction_mode':cfg['mode'],
  'changed_bones':sorted(active_bones),'correction_samples':corrections,'anatomical_before_after':anatomical_before_after,
  'max_correction_delta_per_quarter_sample_rad':correction_steps,
  'correction_series_regularization':({'velocity_weight':3.0,'acceleration_weight':15.0,'raw_magnitude_mix':0.7,'max_rotation_step_rad':0.12,'axis_policy':'preserve per-frame anatomical target axis'} if cfg['mode']=='independent_elbow_and_forearm_targets' else {'velocity_weight':3.0,'acceleration_weight':15.0} if cfg['mode']=='backward_reach_clamp' else None),
  'sample_count':len(frames),'sample_step':a.step,'frame_range':[start,end],'key_times_interpolation_unchanged':timing_ok,
  'max_unchanged_basis_error':max_noncorrected,'max_active_basis_error_outside_correction_windows':max_active_outside_window,'max_root_translation_error':max_root,'max_adjacent_quarter_mesh_vertex_step':max_step,
  'runtime_constraints':constraints,'scale':[float(x) for x in target.scale],'expected_sample_hashes':expected,'render_paths':renders,'render_failures':render_fail}
 os.makedirs(os.path.dirname(os.path.abspath(a.report)),exist_ok=True);json.dump(report,open(a.report,'w',encoding='utf-8'),indent=2,allow_nan=False)
 if render_fail or constraints or max(abs(x-43.437) for x in report['scale'])>.02:raise RuntimeError('V4 preview validation failed')
 print('LITERAL_V4_PREVIEW',json.dumps({'clip':a.clip,'samples':len(frames),'changed_bones':sorted(active_bones),'preview':out}))
if __name__=='__main__':main()
