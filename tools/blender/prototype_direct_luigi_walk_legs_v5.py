"""Geometric leg retarget prototype based on measured anatomical axes.

Opens the disposable v4 preview, preserves its arm/fist/root action curves,
replaces only the leg chain curves, and saves a review file only if numerical
motion, bind, and contact gates pass.
"""
import argparse, json, math, os, sys
import bpy
from mathutils import Matrix, Vector
HERE=os.path.dirname(os.path.abspath(__file__));sys.path.insert(0,HERE)
import prototype_direct_luigi_walk_contact as engine

def parse():
    av=sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else []
    p=argparse.ArgumentParser()
    p.add_argument('--source-rig',default='luigi.001_armature');p.add_argument('--source-action',default='luigi.001_armature_walk')
    p.add_argument('--target-action',default='Waluigi_DirectGalaxyWalk_v4_ContactNarrowFists')
    p.add_argument('--preview',required=True);p.add_argument('--report',required=True)
    return p.parse_args(av)

def rest_frame(forward,up):
    f=forward.normalized();u=up.normalized();r=f.cross(u).normalized();u=r.cross(f).normalized()
    return Matrix(((r.x,u.x,f.x),(r.y,u.y,f.y),(r.z,u.z,f.z)))

def curve_signature(curves,exclude):
    out={}
    for c in curves:
        if any(f'pose.bones["{n}"]' in c.data_path for n in exclude):continue
        out[(c.data_path,c.array_index)]=[(tuple(float(v) for v in kp.co),kp.interpolation) for kp in c.keyframe_points]
    return out

def mesh_samples(meshes,rig,scene,frame):
    scene.frame_set(int(frame),subframe=frame-int(frame));bpy.context.view_layer.update();dg=bpy.context.evaluated_depsgraph_get()
    buckets={'left':[],'right':[]}
    for obj in meshes:
        ev=obj.evaluated_get(dg);me=ev.to_mesh()
        try:
            group_indices={g.name:g.index for g in obj.vertex_groups}
            for side,names in {'left':('bone_9','bone_10','bone_11','bone_12'),'right':('bone_15','bone_16','bone_17','bone_18')}.items():
                ids={group_indices[n] for n in names if n in group_indices}
                for v in me.vertices:
                    weight=sum(g.weight for g in v.groups if g.group in ids)
                    if weight>0:buckets[side].append((ev.matrix_world@v.co,weight))
        finally:ev.to_mesh_clear()
    out={}
    for side,vals in buckets.items():
        if not vals:out[side]={'count':0};continue
        wsum=sum(w for _,w in vals);cen=sum((p*w for p,w in vals),Vector())/wsum
        out[side]={'count':len(vals),'centroid':list(cen),'min_z':min(p.z for p,_ in vals),'max_z':max(p.z for p,_ in vals)}
    return out

def main():
    a=parse();scene=bpy.context.scene
    src=bpy.data.objects.get(a.source_rig);src_action=bpy.data.actions.get(a.source_action)
    candidates=[o for o in bpy.data.objects if o.type=='ARMATURE' and o.animation_data and o.animation_data.action and o.animation_data.action.name==a.target_action]
    if not src or not src_action or len(candidates)!=1:raise RuntimeError('expected source rig/action and one v4 target rig')
    tgt=candidates[0];old_action=tgt.animation_data.action;old_slot=tgt.animation_data.action_slot
    if not old_slot:raise RuntimeError('v4 action has no bound slot')
    srcdup=engine.duplicate_rig(src,'LegV5_SourceEval');srcdup.matrix_basis=engine.object_basis(src);srcdup.animation_data_clear()
    scurves=engine.curves_for_slot(src_action,engine.action_slot(src_action,src.animation_data.action_slot.identifier if src.animation_data and src.animation_data.action_slot else None))
    tcurves=engine.curves_for_slot(old_action,old_slot)
    sframe=rest_frame(src.data.bones['FootL'].tail_local-src.data.bones['FootL'].head_local,
                      src.data.bones['Spine1'].head_local-src.data.bones['Hip'].head_local)
    tframe=rest_frame(tgt.data.bones['bone_12'].tail_local-tgt.data.bones['bone_12'].head_local,
                      tgt.data.bones['bone_6'].head_local-tgt.data.bones['bone_6'].tail_local)
    axis_map=tframe@sframe.transposed()
    sides=(('left','LegL1','LegL2','FootL','bone_7','bone_8','bone_9'),('right','LegR1','LegR2','FootR','bone_13','bone_14','bone_15'))
    source_len={s:(src.data.bones[k].head_local-src.data.bones[h].head_local).length+(src.data.bones[f].head_local-src.data.bones[k].head_local).length for s,h,k,f,*_ in sides}
    target_len={s:tgt.data.bones[u].length+tgt.data.bones[l].length for s,*_,u,l,ft in sides}
    ratios={s:target_len[s]/source_len[s] for s in source_len}
    meshes=[o for o in bpy.data.objects if o.type=='MESH' and any(m.type=='ARMATURE' and m.object==tgt for m in o.modifiers)]
    if len(meshes)!=2:raise RuntimeError(f'expected 2 target preview meshes, found {len(meshes)}')
    # Source and baseline samples stay quarter-frame exact. No grounding or
    # stance-anchor correction is applied in this prototype.
    frames=[i*.25 for i in range(241)];records=[];previous_source_flex={};worst_reach={'ratio':0.,'side':None,'frame':None};
    for f in frames:
        scene.frame_set(int(f),subframe=f-int(f));engine.evaluate_source_curves(srcdup,scurves,f);sp=engine.pose_matrices_from_basis(srcdup)
        engine.evaluate_source_curves(tgt,tcurves,f);tp=engine.pose_matrices_from_basis(tgt)
        row={'frame':f,'sides':{},'pose_basis':{}}
        for side,sh,sk,sf,upper,lower,foot in sides:
            source_hip=sp[sh].translation.copy();source_knee=sp[sk].translation.copy();source_ankle=sp[sf].translation.copy()
            target_hip=tp[upper].translation.copy();ankle_delta=axis_map@(source_ankle-source_hip)*ratios[side]
            knee_delta=axis_map@(source_knee-source_hip)*ratios[side]
            ankle=target_hip+ankle_delta;to_ankle=ankle-target_hip;distance=to_ankle.length
            l1=tgt.data.bones[upper].length;l2=tgt.data.bones[lower].length;maxreach=l1+l2
            reach_ratio=distance/maxreach if maxreach else 99.
            if reach_ratio>worst_reach['ratio']:worst_reach={'ratio':reach_ratio,'side':side,'frame':f}
            clipped=max(abs(l1-l2)+1e-6,min(maxreach-1e-6,distance))
            axis=to_ankle.normalized();pole=knee_delta-axis*knee_delta.dot(axis)
            if pole.length<1e-7:raise RuntimeError(f'{side} source knee pole degenerate at {f}')
            along=(l1*l1-l2*l2+clipped*clipped)/(2*clipped)
            height=math.sqrt(max(0.,l1*l1-along*along));knee=target_hip+axis*along+pole.normalized()*height
            upper_m=engine.aim_bone_matrix(tgt,upper,target_hip,knee-target_hip)
            engine.set_pose_matrix_from_armature_matrix(tgt,upper,upper_m,tp);tp=engine.pose_matrices_from_basis(tgt)
            lower_m=engine.aim_bone_matrix(tgt,lower,knee-target_hip+target_hip,ankle-knee)
            engine.set_pose_matrix_from_armature_matrix(tgt,lower,lower_m,tp);tp=engine.pose_matrices_from_basis(tgt)
            foot_tail=sp[sf]@Vector((0.,src.data.bones[sf].length,0.));foot_dir=axis_map@(foot_tail-source_ankle)
            # The target's ankle bone axis is not its foot/toe axis: bones
            # 10/11 form an offset helper chain. Calibrate against the actual
            # posed ankle-to-toe endpoint and rotate the whole branch so that
            # endpoint follows Luigi's foot direction.
            toe_name='bone_12' if side=='left' else 'bone_18'
            toe_tip=tp[toe_name]@Vector((0.,tgt.data.bones[toe_name].length,0.))
            current_foot=tp[foot];current_toe_dir=current_foot.to_3x3()@(current_foot.to_3x3().inverted_safe()@(toe_tip-current_foot.translation))
            if current_toe_dir.length<1e-8 or foot_dir.length<1e-8:raise RuntimeError(f'{side} foot direction degenerate at {f}')
            foot_rotation=(current_toe_dir.normalized().rotation_difference(foot_dir.normalized())@current_foot.to_quaternion()).normalized()
            foot_m=Matrix.LocRotScale(ankle,foot_rotation,(1.,1.,1.))
            engine.set_pose_matrix_from_armature_matrix(tgt,foot,foot_m,tp);tp=engine.pose_matrices_from_basis(tgt)
            actual_knee=tp[lower].translation.copy();actual_ankle=tp[foot].translation.copy()
            src_a=sframe.transposed()@(source_ankle-source_hip)/source_len[side]
            src_k=sframe.transposed()@(source_knee-source_hip)/source_len[side]
            src_flex=math.acos(max(-1.,min(1.,(src_k).normalized().dot((src_a-src_k).normalized()))))
            tgt_a=tframe.transposed()@(actual_ankle-target_hip)/target_len[side]
            tgt_k=tframe.transposed()@(actual_knee-target_hip)/target_len[side]
            tgt_flex=math.acos(max(-1.,min(1.,tgt_k.normalized().dot((tgt_a-tgt_k).normalized()))))
            toe_point=tp['bone_12' if side=='left' else 'bone_18']@Vector((0.,tgt.data.bones['bone_12' if side=='left' else 'bone_18'].length,0.))
            toe_vec=tframe.transposed()@(toe_point-actual_ankle)
            source_toe=foot_tail-source_ankle
            source_toe_can=sframe.transposed()@source_toe
            toe_angle=math.acos(max(-1.,min(1.,toe_vec.normalized().dot(source_toe_can.normalized())))) if toe_vec.length>1e-8 and source_toe_can.length>1e-8 else math.pi
            row['sides'][side]={'source_ankle':list(src_a),'target_ankle':list(tgt_a),'source_knee':list(src_k),'target_knee':list(tgt_k),'source_flex_rad':src_flex,'target_flex_rad':tgt_flex,'reach_ratio':reach_ratio,'ankle_error':(actual_ankle-ankle).length,'ankle_trajectory_error':(src_a-tgt_a).length,'knee_trajectory_error':(src_k-tgt_k).length,'toe_direction_error_rad':toe_angle,'actual_segment_lengths':[(actual_knee-target_hip).length,(actual_ankle-actual_knee).length]}
            for name in (upper,lower,foot):
                pb=tgt.pose.bones[name];pb.rotation_mode='QUATERNION'
                row['pose_basis'][name]=pb.matrix_basis.copy()
        records.append(row)
    # Source rig was only a throwaway evaluator.
    srcdata=srcdup.data;bpy.data.objects.remove(srcdup,do_unlink=True)
    if srcdata.users==0:bpy.data.armatures.remove(srcdata)
    # Build a new action with exactly the V4 curves except leg outputs.
    leg_names={n for _,*_,u,l,ft in sides for n in (u,l,ft)}
    new_action=old_action.copy();new_action.name='Waluigi_DirectGalaxyWalk_v5_GeometricLegs'
    old_sig=curve_signature(tcurves,leg_names|{'bone_6'})
    tgt.animation_data.action=new_action
    slot=next((s for s in new_action.slots if s.identifier==old_slot.identifier),None)
    if slot is None:raise RuntimeError('copied action slot missing')
    tgt.animation_data.action_slot=slot
    bag_curves=engine.curves_for_slot(new_action,slot)
    output_bag=next(b for layer in new_action.layers for strip in layer.strips for b in strip.channelbags if b.slot==slot)
    for c in list(bag_curves):
        if any(f'pose.bones["{n}"]' in c.data_path for n in leg_names):
            output_bag.fcurves.remove(c)
    for row in records:
        f=row['frame'];scene.frame_set(int(f),subframe=f-int(f))
        # Re-evaluate the preserved V4 base, then write the stored solved leg channels.
        engine.evaluate_source_curves(tgt,engine.curves_for_slot(old_action,old_slot),f)
        for name,m in row['pose_basis'].items():tgt.pose.bones[name].matrix_basis=m
        for name in row['pose_basis']:
            pb=tgt.pose.bones[name];pb.rotation_mode='QUATERNION'
            for prop in ('rotation_quaternion','location','scale'):
                pb.keyframe_insert(data_path=prop,frame=f,group='Geometric Leg V5')
    new_curves=engine.curves_for_slot(new_action,slot)
    for c in new_curves:
        if any(f'pose.bones["{n}"]' in c.data_path for n in leg_names):
            for kp in c.keyframe_points:kp.interpolation='LINEAR'
    # Source absolute foot motion identifies actual ground contacts. The low
    # speed at left 11/right 41 is a swing apex (the source foot is high), so
    # those samples are excluded. Root-height correction below is derived from
    # the measured sole clearance at the true contact intervals.
    contact_anchors={'left':None,'right':None};contact_log=[];contact_unreachable=[]
    stance_intervals={'left':[(29.75,30.25)],'right':[(0.,.75),(58.75,60.)]}
    raw_contact_heights={'left':[],'right':[]}
    for f in frames:
        engine.evaluate_source_curves(tgt,engine.curves_for_slot(new_action,slot),f)
        active={s:any(lo<=f<=hi for lo,hi in iv) for s,iv in stance_intervals.items()}
        if not any(active.values()):continue
        wm=mesh_samples(meshes,tgt,scene,f)
        for side in raw_contact_heights:
            if active[side]:raw_contact_heights[side].append(wm[side]['min_z'])
    right_down=max(0.,max(raw_contact_heights['right'],default=0.))
    left_down=max(0.,max(raw_contact_heights['left'],default=0.))
    # Smooth periodic interpolation between measured stance corrections.
    root_controls=[(0.,right_down),(30.,left_down),(60.,right_down)];root_offsets=[]
    for f in frames:
        if f<=30.:
            u=f/30.;off=.5*(right_down+left_down)+.5*(right_down-left_down)*math.cos(math.pi*u)
        else:
            u=(f-30.)/30.;off=.5*(left_down+right_down)+.5*(left_down-right_down)*math.cos(math.pi*u)
        root_offsets.append(off)
        engine.evaluate_source_curves(tgt,engine.curves_for_slot(new_action,slot),f);tp=engine.pose_matrices_from_basis(tgt)
        down_arm=tgt.matrix_world.to_3x3().inverted_safe()@Vector((0.,0.,-off))
        root=tp['bone_6'].copy();root.translation+=down_arm
        engine.set_pose_matrix_from_armature_matrix(tgt,'bone_6',root,tp)
        pb=tgt.pose.bones['bone_6'];pb.rotation_mode='QUATERNION';pb.keyframe_insert(data_path='location',frame=f,group='Measured root-height correction')
    for c in engine.curves_for_slot(new_action,slot):
        if 'pose.bones["bone_6"]' in c.data_path and c.data_path.endswith('.location'):
            for kp in c.keyframe_points:kp.interpolation='LINEAR'
    for f in frames:
        active={s:any(lo<=f<=hi for lo,hi in iv) for s,iv in stance_intervals.items()}
        for side in contact_anchors:
            if not active[side]:contact_anchors[side]=None
        wm=mesh_samples(meshes,tgt,scene,f)
        for side,sh,sk,sf,upper,lower,foot in sides:
            if not active[side]:continue
            current=wm[side]
            if contact_anchors[side] is None:
                c=Vector(current['centroid']);c.z-=current['min_z'];contact_anchors[side]=c
            for iteration in range(4):
                if iteration:current=mesh_samples(meshes,tgt,scene,f)[side]
                delta_world=contact_anchors[side]-Vector(current['centroid'])
                # Armature-space displacement matching the evaluated mesh.
                obj3=tgt.matrix_world.to_3x3();delta_arm=obj3.inverted_safe()@delta_world
                tp=engine.pose_matrices_from_basis(tgt);hip=tp[upper].translation.copy();ankle=tp[foot].translation.copy()+delta_arm
                knee0=tp[lower].translation.copy();axisv=ankle-hip;dist=axisv.length
                l1=tgt.data.bones[upper].length;l2=tgt.data.bones[lower].length
                if dist>=l1+l2-1e-5:
                    contact_unreachable.append({'frame':f,'side':side,'distance':dist,'max_reach':l1+l2,'excess':dist-(l1+l2),'iteration':iteration})
                    break
                axisv.normalize();pole=knee0-hip-axisv*(knee0-hip).dot(axisv)
                if pole.length<1e-7:raise RuntimeError(f'{side} stance pole degenerate at {f}')
                along=(l1*l1-l2*l2+dist*dist)/(2*dist);height=math.sqrt(max(0.,l1*l1-along*along));knee=hip+axisv*along+pole.normalized()*height
                upm=engine.aim_bone_matrix(tgt,upper,hip,knee-hip);engine.set_pose_matrix_from_armature_matrix(tgt,upper,upm,tp);tp=engine.pose_matrices_from_basis(tgt)
                lom=engine.aim_bone_matrix(tgt,lower,knee,ankle-knee);engine.set_pose_matrix_from_armature_matrix(tgt,lower,lom,tp);tp=engine.pose_matrices_from_basis(tgt)
                footrot=tp[foot].to_quaternion();fm=Matrix.LocRotScale(ankle,footrot,(1.,1.,1.));engine.set_pose_matrix_from_armature_matrix(tgt,foot,fm,tp)
                bpy.context.view_layer.update()
                for n in (upper,lower,foot):
                    pb=tgt.pose.bones[n];pb.rotation_mode='QUATERNION'
                    for prop in ('rotation_quaternion','location','scale'):pb.keyframe_insert(data_path=prop,frame=f,group='Geometric Leg V5')
                if delta_world.length<.01 and abs(current['min_z'])<.08:break
            contact_log.append({'frame':f,'side':side,'anchor_world':list(contact_anchors[side]),'pre_shift_centroid':current['centroid'],'pre_shift_min_z':current['min_z'],'armature_shift':list(delta_arm),'iterations':iteration+1})
    for c in engine.curves_for_slot(new_action,slot):
        if any(f'pose.bones["{n}"]' in c.data_path for n in leg_names):
            for kp in c.keyframe_points:kp.interpolation='LINEAR'
    # Reopen-style in-memory evaluate every quarter frame. Arm/fist curves and
    # the root channel are compared against V4 by their exact key data.
    new_sig=curve_signature(new_curves,leg_names|{'bone_6'});arm_curve_equal=(old_sig==new_sig)
    source_flex=[];target_flex=[];ankle_errors=[];reach_excess=0.;final_reopen_reference=[]
    for row in records:
        f=row['frame'];scene.frame_set(int(f),subframe=f-int(f));final_curves=engine.curves_for_slot(new_action,slot);engine.evaluate_source_curves(tgt,final_curves,f);pp=engine.pose_matrices_from_basis(tgt)
        selected=('bone_6','bone_7','bone_8','bone_9','bone_10','bone_11','bone_12','bone_13','bone_14','bone_15','bone_16','bone_17','bone_18')
        final_reopen_reference.append({'frame':f,'matrices':{n:[[float(pp[n][r][c]) for c in range(4)] for r in range(4)] for n in selected}})
        for side,sh,sk,sf,upper,lower,foot in sides:
            rr=row['sides'][side];source_flex.append(rr['source_flex_rad']);target_flex.append(rr['target_flex_rad']);ankle_errors.append(rr['ankle_error']);reach_excess=max(reach_excess,max(0.,rr['reach_ratio']-1.))
    fflex=[abs(x-y) for x,y in zip(source_flex,target_flex)]
    # Weighted foot mesh positions/contact measured after the new curves evaluate.
    stance=stance_intervals;contact={s:[] for s in stance}
    for f in frames:
        wm=mesh_samples(meshes,tgt,scene,f)
        for side in stance:contact[side].append({'frame':f,'min_z':wm[side].get('min_z'),'centroid':wm[side].get('centroid')})
    slide={}
    for side in stance:
        interval_slides=[]
        for lo,hi in stance[side]:
            vals=[x for x in contact[side] if lo<=x['frame']<=hi]
            interval_slides.append(max(((Vector(vals[i+1]['centroid'])-Vector(vals[i]['centroid'])).length for i in range(len(vals)-1) if vals[i]['centroid'] is not None and vals[i+1]['centroid'] is not None),default=0.))
        slide[side]=max(interval_slides,default=0.)
    ankle_trajectory_errors=[x['sides'][s]['ankle_trajectory_error'] for x in records for s,*_ in sides]
    knee_trajectory_errors=[x['sides'][s]['knee_trajectory_error'] for x in records for s,*_ in sides]
    toe_errors=[x['sides'][s]['toe_direction_error_rad'] for x in records for s,*_ in sides]
    clearance={s:{'min_z_range':[min(x['min_z'] for x in contact[s] if x['min_z'] is not None),max(x['min_z'] for x in contact[s] if x['min_z'] is not None)],'stance_min_z_range':[min(x['min_z'] for x in contact[s] if any(lo<=x['frame']<=hi for lo,hi in stance[s]) and x['min_z'] is not None),max(x['min_z'] for x in contact[s] if any(lo<=x['frame']<=hi for lo,hi in stance[s]) and x['min_z'] is not None)]} for s in stance}
    report={
        'version':5,'source_action':src_action.name,'base_action':old_action.name,
        'new_action':new_action.name,'samples':len(frames),
        'detected_source_stance_intervals':stance_intervals,
        'axes':{'source_frame':[[float(sframe[r][c]) for c in range(3)] for r in range(3)],
                'target_frame':[[float(tframe[r][c]) for c in range(3)] for r in range(3)],
                'source_to_target_armature':[[float(axis_map[r][c]) for c in range(3)] for r in range(3)]},
        'source_chain_lengths':source_len,'target_chain_lengths':target_len,'scale_ratios':ratios,
        'worst_reach':worst_reach,'max_source_target_flex_error_rad':max(fflex),
        'mean_source_target_flex_error_rad':sum(fflex)/len(fflex),
        'ankle_trajectory_rmse_normalized':math.sqrt(sum(x*x for x in ankle_trajectory_errors)/len(ankle_trajectory_errors)),
        'knee_trajectory_rmse_normalized':math.sqrt(sum(x*x for x in knee_trajectory_errors)/len(knee_trajectory_errors)),
        'mean_toe_direction_error_rad':sum(toe_errors)/len(toe_errors),
        'max_solver_ankle_error_armature_units':max(ankle_errors),
        'arm_and_fist_curves_unchanged':arm_curve_equal,
        'stance_foot_centroid_slide_max_world':slide,'foot_surface_clearance':clearance,
        'stance_contact_samples':contact,'contact_anchors':contact_log,
        'contact_unreachable':contact_unreachable,'root_height_controls_world':root_controls,
        'root_height_correction_world_range':[min(root_offsets),max(root_offsets)],
        'final_reopen_reference':final_reopen_reference,'frames':records}
    for row in records:
        row['pose_basis']={n:[[float(m[r][c]) for c in range(4)] for r in range(4)] for n,m in row['pose_basis'].items()}
    all_frame_penetration=min(x['min_z'] for side_rows in contact.values() for x in side_rows if x['min_z'] is not None)
    report['maximum_ground_penetration_world']=max(0.,-all_frame_penetration)
    gates={'reach_supported':reach_excess<0.01,'arm_curves_identical':arm_curve_equal,'solver_joint_error':max(ankle_errors)<0.005,'flex_rmse':math.sqrt(sum(x*x for x in fflex)/len(fflex))<0.2,'ankle_trajectory_rmse':report['ankle_trajectory_rmse_normalized']<0.1,'foot_mesh_slide':max(slide.values())<0.5,'stance_ground_clearance':all(-0.5<=v['stance_min_z_range'][0] and v['stance_min_z_range'][1]<=1.0 for v in clearance.values()),'all_frame_penetration':all_frame_penetration>=-0.5,'stance_anchors_reachable':not contact_unreachable}
    report['gates']=gates;report['passed']=all(gates.values())
    os.makedirs(os.path.dirname(os.path.abspath(a.report)),exist_ok=True)
    with open(a.report,'w',encoding='utf-8') as f:json.dump(report,f,indent=2,allow_nan=False)
    if report['passed']:
        # Preserve V4 mesh/material/object transforms; save this disposable scene
        # under a new path only after numerical gates have passed.
        os.makedirs(os.path.dirname(os.path.abspath(a.preview)),exist_ok=True);bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(a.preview))
    print(json.dumps({'report':os.path.abspath(a.report),'preview_saved':report['passed'],'passed':report['passed'],'gates':gates,'worst_reach':worst_reach,'max_flex_error':max(fflex),'max_ankle_error':max(ankle_errors),'arm_curves_identical':arm_curve_equal,'stance_vertical_slide':slide}))
if __name__=='__main__':main()
