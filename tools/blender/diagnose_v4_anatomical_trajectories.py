"""Compare Luigi and V3 Waluigi arm trajectories in geometry-derived body axes."""
import argparse,json,math,os,sys
import bpy
from mathutils import Matrix,Vector
HERE=os.path.dirname(os.path.abspath(__file__));sys.path.insert(0,HERE)
from prototype_direct_luigi_walk_literal import bag_for,duplicate_rig,evaluate_curves,pose_matrices_from_basis,slot_for

def args_cli():
 a=sys.argv; a=a[a.index('--')+1:] if '--' in a else []
 p=argparse.ArgumentParser();p.add_argument('--source-action',required=True);p.add_argument('--frames',nargs='+',type=float,required=True);p.add_argument('--output',required=True);return p.parse_args(a)

def v3(m): return m.to_3x3()
def unit(v):
 if v.length<1e-9: raise RuntimeError('zero anatomical axis')
 return v.normalized()
def body_frame(rig,poses):
 bones=rig.data.bones
 torso='Spine1' if bones.get('Spine1') else 'bone_20'
 left='ShoulderL' if bones.get('ShoulderL') else 'bone_22';right='ShoulderR' if bones.get('ShoulderR') else 'bone_57'
 objrot=rig.matrix_world.to_3x3().to_quaternion().to_matrix(); rest=bones[torso]
 transform_point=lambda p: rig.matrix_world@Vector(p)
 up0=unit(objrot@(rest.tail_local-rest.head_local))
 # The preview's audited front camera looks from world -Y. Project that
 # geometric facing direction onto the torso plane; this resolves the sign
 # ambiguity that a shoulder-only cross product leaves between imported rigs.
 fwd0=Vector((0.0,-1.0,0.0));fwd0=(fwd0-up0*fwd0.dot(up0)).normalized();lat0=unit(up0.cross(fwd0))
 restframe=Matrix(((lat0.x,up0.x,fwd0.x),(lat0.y,up0.y,fwd0.y),(lat0.z,up0.z,fwd0.z)))
 restrot=objrot@v3(rest.matrix_local);poserot=objrot@v3(poses[torso])
 delta=poserot@restrot.inverted()
 current=delta@restframe
 origin=rig.matrix_world@poses[torso].translation
 return current,origin,transform_point
def anatomy(rig,poses,side):
 if side=='left' and rig.data.bones.get('ShoulderL'):
  shoulder='ShoulderL';upper='ArmL1';fore='ArmL2';hand='HandL'; elbow_names=('ArmL2',); wrist_names=('HandL',)
  upper_start=('ShoulderL','head'); elbow=('ArmL2','head'); wrist=('HandL','head')
  forepose='ArmL2';handpose='HandL'
  target=False
 else:
  left=side=='left'; shoulder='bone_22' if left else 'bone_57';upper='bone_23' if left else 'bone_58';fore='bone_27' if left else 'bone_62';hand='bone_30' if left else 'bone_65'
  # Average the two native deformation branches at elbow and wrist landmarks.
  elbow_names=('bone_26','bone_29') if left else ('bone_61','bone_64');wrist_names=('bone_27','bone_30') if left else ('bone_62','bone_65')
  upper_start=(shoulder,'head');elbow=(elbow_names[0],'head');wrist=(hand,'head');forepose=fore;handpose=hand;target=True
 def point(spec): return poses[spec[0]].translation if spec[1]=='head' else poses[spec[0]].translation
 sh=point(upper_start)
 if target:
  el=sum((poses[n].translation for n in elbow_names),Vector())/len(elbow_names)
  wr=sum((poses[n].translation for n in wrist_names),Vector())/len(wrist_names)
 else:
  el=point(elbow);wr=point(wrist)
 frame,origin,worldpoint=body_frame(rig,poses)
 coords=lambda p: frame.transposed()@(worldpoint(p)-origin)
 up=unit(el-sh);low=unit(wr-el)
 bend=math.acos(max(-1,min(1,up.dot(low))))
 anatomical_rot=frame.transposed()@v3(poses[forepose]);hand_rot=frame.transposed()@v3(poses[handpose])
 rel=anatomical_rot.inverted()@hand_rot
 relq=rel.to_quaternion().normalized();axis,angle=relq.to_axis_angle()
 return {'shoulder':list(coords(sh)),'elbow':list(coords(el)),'wrist':list(coords(wr)),
         'upper_direction':list(frame.transposed()@unit(el-sh)),'forearm_direction':list(frame.transposed()@unit(wr-el)),
         'elbow_flex_rad':bend,'forearm_hand_relative_axis':list(axis),'forearm_hand_relative_angle_rad':float(angle),
         'torso_frame_columns':[list(frame.col[i]) for i in range(3)]}

def main():
 a=args_cli(); scene=bpy.context.scene;src=bpy.data.objects.get('luigi_armature');dst=bpy.data.objects.get('LiteralWalk_Waluigi_Target');act=bpy.data.actions.get(a.source_action)
 if not src or not dst or not act: raise RuntimeError('source rig/action or V3 target missing')
 slot=next((s for s in act.slots if src.animation_data and src.animation_data.action_slot and s.identifier==src.animation_data.action_slot.identifier),None)
 if slot is None:
  if len(act.slots)!=1: raise RuntimeError('source action slot ambiguous')
  slot=act.slots[0]
 curves=list(bag_for(act,slot).fcurves);clone=duplicate_rig(src,'V4_Source_Evaluator');clone.animation_data_clear()
 for original,copy in zip(src.pose.bones,clone.pose.bones):copy.rotation_mode=original.rotation_mode
 old=(scene.frame_current,scene.frame_subframe);out={'source_action':act.name,'target_action':dst.animation_data.action.name,'coordinate_space':'torso-following rest-derived axes: lateral, up, forward; limb positions normalized by own rest upper+lower length','frames':{}}
 for f in a.frames:
  n=math.floor(f);scene.frame_set(n,subframe=f-n);evaluate_curves(clone,curves,f);sm=pose_matrices_from_basis(clone)
  tm=pose_matrices_from_basis(dst)
  sr=anatomy(clone,sm,'left');tr=anatomy(dst,tm,'left')
  src_len=(clone.matrix_world@clone.data.bones['ArmL2'].head_local-clone.matrix_world@clone.data.bones['ShoulderL'].head_local).length+(clone.matrix_world@clone.data.bones['HandL'].head_local-clone.matrix_world@clone.data.bones['ArmL2'].head_local).length
  drest=dst.data.bones
  de=(drest['bone_26'].head_local+drest['bone_29'].head_local)*0.5
  dw=(drest['bone_27'].tail_local+drest['bone_30'].head_local)*0.5
  tgt_len=(dst.matrix_world@de-dst.matrix_world@drest['bone_22'].head_local).length+(dst.matrix_world@dw-dst.matrix_world@de).length
  for row,L in ((sr,src_len),(tr,tgt_len)):
   for k in ('shoulder','elbow','wrist'): row[k+'_normalized']=[x/L for x in row[k]]
  out['frames'][str(f)]={'source':sr,'target_v3':tr,'normalized_error':{
   k:math.dist(sr[k+'_normalized'],tr[k+'_normalized']) for k in ('elbow','wrist')},
   'elbow_flex_error_rad':tr['elbow_flex_rad']-sr['elbow_flex_rad']}
 data=clone.data;bpy.data.objects.remove(clone,do_unlink=True)
 if data.users==0:bpy.data.armatures.remove(data)
 scene.frame_set(old[0],subframe=old[1])
 os.makedirs(os.path.dirname(os.path.abspath(a.output)),exist_ok=True);json.dump(out,open(a.output,'w',encoding='utf-8'),indent=2,allow_nan=False)
 print('V4_ANATOMICAL_DIAGNOSTIC',json.dumps({'frames':len(a.frames),'output':a.output}))
if __name__=='__main__':main()
