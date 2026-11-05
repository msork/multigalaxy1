"""Read-only V5 root-cause investigation for literal Luigi->Waluigi V4 clips.

Loads a V4 preview, makes temporary source/target rig+mesh duplicates, compares
the original source action with V3/V4 pose actions, and measures independent
10-degree control perturbations on evaluated weighted vertices. No file is
saved and no original scene datablock is edited.
"""
import argparse
import json
import math
import os
import sys
from collections import defaultdict

import bpy
from mathutils import Matrix, Quaternion, Vector
from mathutils.kdtree import KDTree

HERE=os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:sys.path.insert(0,HERE)
from literal_retarget_math import local_frame_conversion
from prototype_direct_luigi_walk_literal import (bag_for, curve_path_bone, duplicate_rig,
    evaluate_curves, pose_matrices_from_basis, set_armature_pose_matrix)
from diagnose_v4_anatomical_trajectories import anatomy, body_frame

MAPPING={
 'ShoulderL':['bone_22'],'ArmL1':['bone_23','bone_24','bone_25'],
 'ArmL2':['bone_26','bone_27','bone_29'],'HandL':['bone_30'],
 'ShoulderR':['bone_57'],'ArmR1':['bone_58','bone_59','bone_60'],
 'ArmR2':['bone_61','bone_62','bone_64'],'HandR':['bone_65']}
CHAIN={'left':['bone_22','bone_23','bone_24','bone_25','bone_26','bone_27','bone_29','bone_30'],
       'right':['bone_57','bone_58','bone_59','bone_60','bone_61','bone_62','bone_64','bone_65']}
GROUPS={'left_upper':['bone_22','bone_23','bone_24','bone_25'],
        'left_forearm':['bone_26','bone_27','bone_29'],
        'left_glove':['bone_30']+[f'bone_{i}' for i in range(31,52)],
        'right_upper':['bone_57','bone_58','bone_59','bone_60'],
        'right_forearm':['bone_61','bone_62','bone_64'],
        'right_glove':['bone_65']+[f'bone_{i}' for i in range(66,87)]}
SEAMS={'left_elbow':['bone_25','bone_29'],'left_forearm_wrist':['bone_29','bone_27'],
       'left_forearm_glove':['bone_29','bone_30'],'right_elbow':['bone_60','bone_64'],
       'right_forearm_wrist':['bone_64','bone_62'],'right_forearm_glove':['bone_64','bone_65']}
FRAMES={'run':[5.,11.,14.,24.,32.,40.,46.],
        'jump':[9.,13.,14.,15.,24.],
        'swim':[5.,11.,12.,13.,14.]}
SOURCE_ACTION={'run':'luigi_armature_luigirun','jump':'luigi_armature_luigijump',
               'swim':'luigi_armature_swimbreast'}


def cli():
    a=sys.argv; a=a[a.index('--')+1:] if '--' in a else []
    p=argparse.ArgumentParser();p.add_argument('--clip',choices=FRAMES,required=True)
    p.add_argument('--output',required=True);p.add_argument('--step-deg',type=float,default=10.)
    return p.parse_args(a)


def action_curves(action,object_name):
    slot=next((s for s in action.slots if s.identifier==('OB'+object_name)),None)
    if slot is None:
        if len(action.slots)!=1:raise RuntimeError(f'ambiguous slot for {action.name}')
        slot=action.slots[0]
    return slot,list(bag_for(action,slot).fcurves)


def duplicate_mesh_to_rig(source,rig,name):
    obj=source.copy();obj.data=source.data.copy();obj.name=name;obj.data.name=name+'_Data'
    bpy.context.scene.collection.objects.link(obj)
    obj.animation_data_clear() if obj.animation_data else None
    obj.matrix_world=source.matrix_world.copy()
    for mod in obj.modifiers:
        if mod.type=='ARMATURE':mod.object=rig
    return obj


def mesh_points(meshes,rig,canonical):
    deps=bpy.context.evaluated_depsgraph_get();out=[]
    inv=rig.matrix_world.inverted_safe()
    for obj in meshes:
        ev=obj.evaluated_get(deps);me=ev.to_mesh()
        try:out.append([canonical@(inv@ev.matrix_world@v.co) for v in me.vertices])
        finally:ev.to_mesh_clear()
    return out


def canonical_matrix(rig):
    up=(rig.data.bones['bone_53'].head_local-rig.data.bones['bone_19'].head_local).normalized()
    front=rig.matrix_world.to_3x3().inverted_safe()@Vector((0,-1,0));front-=up*front.dot(up);front.normalize()
    right=up.cross(front).normalized()
    return Matrix((right,-front,up))


def group_ids(mesh):
    names={g.index:g.name for g in mesh.vertex_groups};rows=defaultdict(list);weights=defaultdict(float)
    for v in mesh.data.vertices:
        for g in v.groups:
            if g.weight>=.02:
                n=names[g.group];rows[n].append(v.index);weights[n]+=float(g.weight)
    return rows,weights


def region_ids(mesh_group_ids,region):
    return sorted({i for meshrows in mesh_group_ids for n in region for i in meshrows.get(n,[])})


def stats_vec(points):
    if not points:return None
    return {'count':len(points),'centroid':[sum(float(p[a]) for p in points)/len(points) for a in range(3)],
            'bounds':[[min(float(p[a]) for p in points),max(float(p[a]) for p in points)] for a in range(3)]}


def weighted_centroids(meshes,points_by_mesh):
    rows={}
    for side in ('left','right'):
        for name in CHAIN[side]:
            allpts=[];ws=[];n=0;weight_sum=0.0
            for mesh,pts in zip(meshes,points_by_mesh):
                gi=mesh.vertex_groups.get(name)
                if not gi:continue
                for v in mesh.data.vertices:
                    for g in v.groups:
                        if g.group==gi.index and g.weight>=.02:
                            allpts.append(pts[v.index]);ws.append(float(g.weight));n+=1;weight_sum+=float(g.weight);break
            if allpts:
                total=sum(ws);centroid=[sum(float(p[a])*w for p,w in zip(allpts,ws))/total for a in range(3)]
                rows[name]={'weighted_vertices':n,'weight_sum':weight_sum,'centroid':centroid,
                    'bounds':[[min(float(p[a]) for p in allpts),max(float(p[a]) for p in allpts)] for a in range(3)]}
            else:rows[name]={'weighted_vertices':0,'weight_sum':0.0,'centroid':None,'bounds':None}
    return rows


def seam_gap(points_by_mesh,group_ids_by_mesh,a,b):
    points_a=[];points_b=[]
    for mi,(pts,groups) in enumerate(zip(points_by_mesh,group_ids_by_mesh)):
        ia=set(groups.get(a,[]));ib=set(groups.get(b,[]));shared=ia&ib
        points_a.extend((mi,i,pts[i]) for i in ia-shared);points_b.extend((mi,i,pts[i]) for i in ib-shared)
    if not points_a or not points_b:return {'p95':None,'count_a':len(points_a),'count_b':len(points_b)}
    tree=KDTree(len(points_b))
    for i,(_,__,p) in enumerate(points_b):tree.insert(p,i)
    tree.balance();d=sorted(tree.find(p)[2] for _,__,p in points_a)
    return {'p95':float(d[min(len(d)-1,math.ceil(.95*len(d))-1)]),'count_a':len(points_a),'count_b':len(points_b)}


def body_axes(rig,pose_mats):
    frame,_,_=body_frame(rig,pose_mats)
    return [frame.col[i].normalized() for i in range(3)]


def change_world_rotation(rig,bone,axis,angle):
    poses=pose_matrices_from_basis(rig);m=poses[bone]
    objrot=rig.matrix_world.to_3x3().to_quaternion().to_matrix()
    current_world=objrot@m.to_3x3();delta=Quaternion(Vector(axis).normalized(),angle).to_matrix()
    desired_arm=objrot.transposed()@delta@current_world
    desired=Matrix.LocRotScale(m.translation,desired_arm.to_quaternion(),m.to_scale())
    set_armature_pose_matrix(rig,bone,desired,poses)
    bpy.context.view_layer.update()


def rotation_angle(a,b):
    qa=a.copy();qb=b.copy();qa.normalize();qb.normalize()
    return math.degrees(2*math.acos(max(-1.,min(1.,abs(float(qa.dot(qb)))))))


def vertex_change(base,new,groups,region_bones):
    rows=[]
    for mi,(aa,bb) in enumerate(zip(base,new)):
        ids=set(i for n in region_bones for i in groups[mi].get(n,[]))
        if ids:rows.extend((aa[i]-bb[i]).length for i in ids)
    if not rows:return {'count':0,'rms':0.0,'max':0.0}
    return {'count':len(rows),'rms':math.sqrt(sum(x*x for x in rows)/len(rows)),'max':max(rows)}


def basis_snapshot(rig):return {pb.name:pb.matrix_basis.copy() for pb in rig.pose.bones}
def restore_basis(rig,row):
    for n,m in row.items():rig.pose.bones[n].matrix_basis=m
    bpy.context.view_layer.update()


def target_region_motion(source_an,target_an,side):
    # Anatomy vectors are shoulder-relative and normalized by each rig's rest
    # arm length. Directions are [lateral, up, forward] in each torso frame.
    return {key:{'source':source_an[key+'_normalized'],'target':target_an[key+'_normalized'],
        'delta':[target_an[key+'_normalized'][i]-source_an[key+'_normalized'][i] for i in range(3)],
        'distance':math.dist(source_an[key+'_normalized'],target_an[key+'_normalized'])}
        for key in ('elbow','wrist')}


def measured_anatomy(rig,poses,side,target):
    left=side=='left'
    frame,origin,worldpoint=body_frame(rig,poses)
    if target:
        shoulder='bone_22' if left else 'bone_57'
        elbows=('bone_26','bone_29') if left else ('bone_61','bone_64')
        wrists=('bone_27','bone_30') if left else ('bone_62','bone_65')
        foreposes=['bone_27','bone_29'] if left else ['bone_62','bone_64'];handpose='bone_30' if left else 'bone_65'
        sh=poses[shoulder].translation
        el=(poses[elbows[0]].translation+poses[elbows[1]].translation)*.5
        wr=(poses[wrists[0]].translation+poses[wrists[1]].translation)*.5
    else:
        shoulder='ShoulderL' if left else 'ShoulderR'
        fore='ArmL2' if left else 'ArmR2';handpose='HandL' if left else 'HandR';foreposes=[fore]
        sh=poses[shoulder].translation;el=poses[fore].translation;wr=poses[handpose].translation
    coords=lambda p:frame.transposed()@(worldpoint(p)-origin)
    upper=(el-sh).normalized();lower=(wr-el).normalized()
    flex=math.acos(max(-1.,min(1.,upper.dot(lower))))
    hand_rot=frame.transposed()@poses[handpose].to_3x3()
    branch_relative={}
    for branch_index,forepose in enumerate(foreposes):
        fore_rot=frame.transposed()@poses[forepose].to_3x3()
        rel=fore_rot.inverted_safe()@hand_rot;q=rel.to_quaternion().normalized();axis,angle=q.to_axis_angle()
        branch_relative['main' if branch_index==0 else 'overlap']={'axis':list(axis),'angle_rad':float(angle)}
    preferred=branch_relative['main']
    return {'shoulder':list(coords(sh)),'elbow':list(coords(el)),'wrist':list(coords(wr)),
      'upper_direction':list(frame.transposed()@upper),'forearm_direction':list(frame.transposed()@lower),
      'elbow_flex_rad':float(flex),'forearm_hand_relative_axis':preferred['axis'],
      'forearm_hand_relative_angle_rad':preferred['angle_rad'],'forearm_hand_relative_by_branch':branch_relative,
      'torso_frame_columns':[list(frame.col[i]) for i in range(3)]}


def main():
    a=cli();clip=a.clip;scene=bpy.context.scene
    target=next((o for o in bpy.data.objects if o.type=='ARMATURE' and o.name=='LiteralWalk_Waluigi_Target'),None)
    meshes=sorted([o for o in bpy.data.objects if o.type=='MESH' and o.visible_get() and
                   any(m.type=='ARMATURE' and m.object==target for m in o.modifiers)],key=lambda o:o.name)
    source=next((o for o in bpy.data.objects if o.type=='ARMATURE' and o.name=='luigi_armature'),None)
    if not target or len(meshes)!=2 or not source:raise RuntimeError('expected V4 source/target and two bound meshes')
    source_action=bpy.data.actions.get(SOURCE_ACTION[clip]);v4_action=target.animation_data.action
    v3_action=next((x for x in bpy.data.actions if 'Surgical_V3' in x.name and clip in x.name),None)
    if not source_action or not v4_action or not v3_action:raise RuntimeError('required source/V3/V4 action missing')
    source_slot,source_curves=action_curves(source_action,source.name)
    v4_slot,v4_curves=action_curves(v4_action,target.name)
    v3_slot,v3_curves=action_curves(v3_action,target.name)
    source_eval=duplicate_rig(source,'V5_Source_Diagnostic');source_eval.animation_data_clear()
    for orig,copy in zip(source.pose.bones,source_eval.pose.bones):copy.rotation_mode=orig.rotation_mode
    target_eval=duplicate_rig(target,'V5_Target_Diagnostic');target_eval.animation_data_clear()
    target_meshes=[duplicate_mesh_to_rig(m,target_eval,'V5_'+m.name) for m in meshes]
    canonical=canonical_matrix(target_eval)
    group_rows_by_mesh=[group_ids(m)[0] for m in target_meshes]
    weight_info={}
    for side in ('left','right'):
        for bone in CHAIN[side]:
            counts=[];sums=[]
            for m in target_meshes:
                gi=m.vertex_groups.get(bone);count=0;ws=0.
                if gi:
                    for v in m.data.vertices:
                        for g in v.groups:
                            if g.group==gi.index and g.weight>=.02:count+=1;ws+=float(g.weight);break
                counts.append(count);sums.append(ws)
            weight_info[bone]={'weighted_vertex_count_by_mesh':counts,'weighted_vertex_count_total':sum(counts),
                               'weight_sum_by_mesh':sums,'weight_sum_total':sum(sums),'zero_weight':sum(counts)==0}
    rest_rows={}
    conversion_rows={}
    for src,names in MAPPING.items():
        sb=source_eval.data.bones[src];sparent=sb.parent
        smat=sparent.matrix_local.inverted_safe()@sb.matrix_local if sparent else sb.matrix_local
        conversion_rows[src]={}
        for name in names:
            tb=target_eval.data.bones[name];tparent=tb.parent
            tmat=tparent.matrix_local.inverted_safe()@tb.matrix_local if tparent else tb.matrix_local
            conv=local_frame_conversion(source_eval.matrix_world,sb.matrix_local,target_eval.matrix_world,tb.matrix_local)
            conversion_rows[src][name]={'quaternion_wxyz':list(conv),'angle_deg':math.degrees(conv.angle)}
            rest_rows[name]={'source':src,'parent':tb.parent.name if tb.parent else None,
                'children':[c.name for c in tb.children],'deform':bool(tb.use_deform),'length':float(tb.length),
                'head':list(tb.head_local),'tail':list(tb.tail_local),'roll_quaternion_wxyz':list(tb.matrix_local.to_quaternion()),
                'parent_space_rest_matrix':[list(x) for x in tmat],
                'parent_space_rest_quaternion_wxyz':list(tmat.to_quaternion()),
                'source_parent':sparent.name if sparent else None,'source_parent_space_rest_matrix':[list(x) for x in smat]}
    action_stats={}
    for act,slot,curves in [('source',source_slot,source_curves),('target_v3',v3_slot,v3_curves),('target_v4',v4_slot,v4_curves)]:
        by=defaultdict(list)
        for fc in curves:
            name=curve_path_bone(fc.data_path)
            if name:by[name].append(fc)
        action_stats[act]={}
        names=set(MAPPING.keys()) if act=='source' else set(n for ns in MAPPING.values() for n in ns)
        for name in sorted(names):
            curves0=by.get(name,[]);vals=[float(p.co.y) for fc in curves0 for p in fc.keyframe_points]
            action_stats[act][name]={'curve_count':len(curves0),'key_count':sum(len(fc.keyframe_points) for fc in curves0),
                'value_min':min(vals) if vals else None,'value_max':max(vals) if vals else None}
    v4_report_path=os.path.join(HERE,'reports',f'luigi_armature_{SOURCE_ACTION[clip]}_waluigi_anatomical_v4.json')
    v4_summary=json.load(open(v4_report_path,encoding='utf-8')) if os.path.isfile(v4_report_path) else {}
    out={'schema':'mg1.literal-v5-root-cause-diagnostics.v1','clip':clip,'input_blend':bpy.data.filepath,
        'source_action':source_action.name,'target_v3_action':v3_action.name,'target_v4_action':v4_action.name,
        'scope':'read-only diagnostic; animation is evaluated on disposable duplicate rigs; no save performed',
        'armature_object_transforms':{'source':{'matrix':[list(x) for x in source_eval.matrix_world]},
            'target':{'matrix':[list(x) for x in target_eval.matrix_world],'scale':list(target_eval.scale)},
            'target_scale_preserved':max(abs(float(x)-43.437) for x in target_eval.scale)<.02},
        'mapping':MAPPING,'action_channels':action_stats,'target_rest_and_parent_basis':rest_rows,
        'rest_frame_conversion':conversion_rows,'weights':weight_info,
        'v4_correction_record':{'changed_bones':v4_summary.get('changed_bones'),
          'correction_samples':v4_summary.get('correction_samples'),
          'max_correction_delta_per_quarter_sample_rad':v4_summary.get('max_correction_delta_per_quarter_sample_rad')},
        'samples':{},'perturbation_step_deg':a.step_deg,'perturbations':{}}
    frames=FRAMES[clip]
    for frame in frames:
        whole=math.floor(frame);scene.frame_set(whole,subframe=frame-whole)
        evaluate_curves(source_eval,source_curves,frame);sp=pose_matrices_from_basis(source_eval)
        evaluate_curves(target_eval,v4_curves,frame);tp=pose_matrices_from_basis(target_eval)
        source_an={s:measured_anatomy(source_eval,sp,s,False) for s in ('left','right')}
        target_an={s:measured_anatomy(target_eval,tp,s,True) for s in ('left','right')}
        source_length={}
        for side,upper,fore,hand in [('left','ArmL1','ArmL2','HandL'),('right','ArmR1','ArmR2','HandR')]:
            b=source_eval.data.bones;mw=source_eval.matrix_world
            source_length[side]=(mw@b[fore].head_local-mw@b[('ShoulderL' if side=='left' else 'ShoulderR')].head_local).length+(mw@b[hand].head_local-mw@b[fore].head_local).length
        target_length={}
        for side,shoulder,elbow_a,elbow_b,wrist_a,wrist_b in [('left','bone_22','bone_26','bone_29','bone_27','bone_30'),('right','bone_57','bone_61','bone_64','bone_62','bone_65')]:
            b=target_eval.data.bones;mw=target_eval.matrix_world
            ep=(b[elbow_a].head_local+b[elbow_b].head_local)*.5;wp=(b[wrist_a].head_local+b[wrist_b].head_local)*.5
            target_length[side]=(mw@ep-mw@b[shoulder].head_local).length+(mw@wp-mw@ep).length
        for side in ('left','right'):
            for key in ('elbow','wrist'):
                source_an[side][key+'_normalized']=[float(x)/source_length[side] for x in source_an[side][key]]
                target_an[side][key+'_normalized']=[float(x)/target_length[side] for x in target_an[side][key]]
        pts=mesh_points(target_meshes,target_eval,canonical)
        out['samples'][str(frame)]={'source_anatomy':source_an,'target_v4_anatomy':target_an,
            'normalized_landmark_errors':{side:target_region_motion(source_an[side],target_an[side],side) for side in ('left','right')},
            'rest_arm_length':{'source':source_length,'target':target_length},
            'target_weighted_vertex_centroids':weighted_centroids(target_meshes,pts),
            'distinct_seam_gaps':{n:seam_gap(pts,group_rows_by_mesh,*pair) for n,pair in SEAMS.items()},
            'source_vs_target_elbow_flex_rad':{s:target_an[s]['elbow_flex_rad']-source_an[s]['elbow_flex_rad'] for s in ('left','right')},
            'left_right_weighted_centroid_mirror_error':None}
        basebasis=basis_snapshot(target_eval);basepts=pts
        axes=body_axes(target_eval,pose_matrices_from_basis(target_eval))
        control_bones=CHAIN['left']+CHAIN['right']+['bone_20','bone_21']
        per_frame={}
        for bone in control_bones:
            if bone not in target_eval.pose.bones:continue
            per_frame[bone]={}
            for axis_i,axis in enumerate(axes):
                per_frame[bone][['lateral','up','forward'][axis_i]]={}
                for sign in (-1,1):
                    restore_basis(target_eval,basebasis)
                    change_world_rotation(target_eval,bone,axis,math.radians(a.step_deg)*sign)
                    npts=mesh_points(target_meshes,target_eval,canonical)
                    region_motion={name:vertex_change(basepts,npts,group_rows_by_mesh,g) for name,g in GROUPS.items()}
                    for s in ('left','right'):
                        relevant=[region_motion[s+'_upper']['rms'],region_motion[s+'_forearm']['rms'],region_motion[s+'_glove']['rms']]
                        region_motion[s+'_arm_summary']={'rms_all_arm_regions':math.sqrt(sum(x*x for x in relevant)/3),
                            'max_region_displacement':max(region_motion[s+'_upper']['max'],region_motion[s+'_forearm']['max'],region_motion[s+'_glove']['max'])}
                    pert_an=measured_anatomy(target_eval,pose_matrices_from_basis(target_eval),'left',True)
                    base_an=target_an['left'];directional={}
                    target_len=target_length['left']
                    for joint in ('elbow','wrist'):
                        source_norm=source_an['left'][joint+'_normalized'];base_norm=base_an[joint+'_normalized']
                        perturb_norm=[float(x)/target_len for x in pert_an[joint]]
                        before=math.dist(source_norm,base_norm);after=math.dist(source_norm,perturb_norm)
                        directional[joint]={'source_error_before':before,'source_error_after':after,
                            'error_reduction':before-after,'normalized_endpoint_delta':[
                                perturb_norm[i]-base_norm[i] for i in range(3)]}
                    def relq(row):
                        v=row['axis'];ang=row['angle_rad']
                        return Quaternion(Vector(v),float(ang))
                    hand_orientation={}
                    for branch in set(source_an['left']['forearm_hand_relative_by_branch']) & set(pert_an['forearm_hand_relative_by_branch']):
                        qs=relq(source_an['left']['forearm_hand_relative_by_branch'][branch])
                        qb=relq(base_an['forearm_hand_relative_by_branch'][branch])
                        qp=relq(pert_an['forearm_hand_relative_by_branch'][branch])
                        before=rotation_angle(qs,qb);after=rotation_angle(qs,qp)
                        hand_orientation[branch]={'error_before_deg':before,'error_after_deg':after,'error_reduction_deg':before-after}
                    endpoint_delta={key:[pert_an[key][i]-base_an[key][i] for i in range(3)] for key in ('elbow','wrist')}
                    per_frame[bone][['lateral','up','forward'][axis_i]][str(sign)]={'region_vertex_displacement':region_motion,
                        'left_landmark_delta_armature_units':endpoint_delta,'source_target_error_reduction':directional,
                        'source_target_hand_orientation_error_reduction':hand_orientation}
        restore_basis(target_eval,basebasis)
        out['perturbations'][str(frame)]=per_frame
        # Measure actual V3->V4 surface change on the same disposable duplicate.
        evaluate_curves(target_eval,v3_curves,frame);v3pts=mesh_points(target_meshes,target_eval,canonical)
        evaluate_curves(target_eval,v4_curves,frame);v4pts=mesh_points(target_meshes,target_eval,canonical)
        v3v4={}
        for region,bones in GROUPS.items():
            vals=[]
            for mi,(aa,bb) in enumerate(zip(v3pts,v4pts)):
                ids=set(i for n in bones for i in group_rows_by_mesh[mi].get(n,[]))
                vals.extend((aa[i]-bb[i]).length for i in ids)
            v3v4[region]={'count':len(vals),'rms':math.sqrt(sum(x*x for x in vals)/len(vals)) if vals else 0.,'max':max(vals,default=0.)}
        out['samples'][str(frame)]['v3_to_v4_evaluated_vertex_change']=v3v4
        restore_basis(target_eval,basebasis)
    # Bidirectional group overlap and arm geometry provide a direct asymmetry audit.
    out['left_right_rest_comparison']={}
    for l,r in zip(CHAIN['left'],CHAIN['right']):
        lb=target_eval.data.bones[l];rb=target_eval.data.bones[r]
        out['left_right_rest_comparison'][l+'|'+r]={'length_delta':float(lb.length-rb.length),
          'parent_left':lb.parent.name if lb.parent else None,'parent_right':rb.parent.name if rb.parent else None,
          'rest_rotation_angle_deg':rotation_angle(lb.matrix_local.to_quaternion(),rb.matrix_local.to_quaternion()),
          'weighted_count_delta':weight_info[l]['weighted_vertex_count_total']-weight_info[r]['weighted_vertex_count_total'],
          'weight_sum_delta':weight_info[l]['weight_sum_total']-weight_info[r]['weight_sum_total']}
    # Report vertices influenced by both sides; this catches group asymmetry and
    # overlap that endpoint-only diagnostics miss.
    out['vertex_group_overlap']={}
    for a0,b0 in [('bone_23','bone_25'),('bone_26','bone_29'),('bone_27','bone_30'),
                  ('bone_58','bone_60'),('bone_61','bone_64'),('bone_62','bone_65')]:
        out['vertex_group_overlap'][a0+'|'+b0]={'common_vertex_ids_total':sum(len(set(g.get(a0,[]))&set(g.get(b0,[]))) for g in group_rows_by_mesh),
            'group_a_vertices':sum(len(g.get(a0,[])) for g in group_rows_by_mesh),'group_b_vertices':sum(len(g.get(b0,[])) for g in group_rows_by_mesh)}
    # Remove all temporary objects/data. Nothing is saved.
    temp_mesh_data=[x.data for x in target_meshes];temp_rig_data=[target_eval.data,source_eval.data]
    for obj in target_meshes+[target_eval,source_eval]:bpy.data.objects.remove(obj,do_unlink=True)
    for data in temp_mesh_data:
        if data.users==0:bpy.data.meshes.remove(data)
    for data in temp_rig_data:
        if data.users==0:bpy.data.armatures.remove(data)
    os.makedirs(os.path.dirname(os.path.abspath(a.output)),exist_ok=True)
    with open(a.output,'w',encoding='utf-8') as f:json.dump(out,f,indent=2,allow_nan=False)
    print(json.dumps({'output':a.output,'clip':clip,'frames':len(frames),'perturbations_per_frame':len(control_bones)*3*2,
                      'zero_weight_controls':[n for n,r in weight_info.items() if r['zero_weight']]}))


if __name__=='__main__':main()
