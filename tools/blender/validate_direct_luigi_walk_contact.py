"""Reopen validation for the fitted walk at integer and quarter frames."""
import argparse,json,math,os,sys
import bpy
from mathutils import Vector
HERE=os.path.dirname(os.path.abspath(__file__));sys.path.insert(0,HERE)
import prototype_direct_luigi_walk_contact as direct

def parse():
 argv=sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else []
 p=argparse.ArgumentParser();p.add_argument('--report',required=True);p.add_argument('--output',required=True);return p.parse_args(argv)
def main():
 a=parse(); expected=json.load(open(a.report,encoding='utf-8'))
 visible=[o for o in bpy.data.objects if o.visible_get(view_layer=bpy.context.view_layer)]
 rig=next((o for o in visible if o.type=='ARMATURE' and o.animation_data and
           o.animation_data.action and o.animation_data.action.name==expected['output_action']),None)
 if rig is None: raise RuntimeError('preview rig missing')
 meshes=[o for o in visible if o.type=='MESH']
 visible_rigs=[o.name for o in visible if o.type=='ARMATURE'];visible_meshes=[o.name for o in visible if o.type=='MESH']
 if visible_rigs != [rig.name] or len(visible_meshes)!=2:raise RuntimeError(f'visible body count mismatch: {visible_rigs}, {visible_meshes}')
 for obj in meshes:
  mods=[m.object.name for m in obj.modifiers if m.type=='ARMATURE' and m.object]
  if mods != [rig.name]:raise RuntimeError(f'bad armature binding {obj.name}: {mods}')
 if rig.animation_data is None or rig.animation_data.action is None or rig.animation_data.action.name!=expected['output_action']:raise RuntimeError('wrong action assigned')
 constraints=[(pb.name,c.name) for pb in rig.pose.bones for c in pb.constraints]
 if constraints:raise RuntimeError(f'runtime constraints remain {constraints}')
 scale=list(rig.matrix_basis.to_scale())
 if max(abs(v-43.437) for v in scale)>0.01:raise RuntimeError(f'object scale changed: {scale}')
 scene=bpy.context.scene;pose_max=0.;worst=None;foot_surface_max=0.;fist_max=0.;checks=[]
 frames=sorted((float(k) for k in expected['expected_pose_matrices']),key=float)
 for frame in frames:
  scene.frame_set(int(frame),subframe=frame-int(frame));bpy.context.view_layer.update();dg=bpy.context.evaluated_depsgraph_get();ev=rig.evaluated_get(dg)
  rows=expected['expected_pose_matrices'][str(frame)];err=0.;bone_worst=None
  for name,m in rows.items():
   current=ev.pose.bones[name].matrix
   e=max(abs(float(current[r][c])-float(m[r][c])) for r in range(4) for c in range(4))
   if e>err:err=e;bone_worst=name
   if not all(math.isfinite(float(x)) for row in current for x in row):raise RuntimeError(f'nonfinite pose {frame} {name}')
  if err>pose_max:pose_max=err;worst={'frame':frame,'bone':bone_worst}
  for side,groups in [('left',('bone_9','bone_10','bone_11')),('right',('bone_15','bone_16','bone_17'))]:
   points=[]
   for obj in meshes:
    weights=direct.mesh_group_weights(obj);verts=direct.evaluated_world_vertices(obj)
    points.extend(p for p,row in zip(verts,weights) if any(row.get(g,0.)>.01 for g in groups))
   if points:
    got_min=min(p.z for p in points);cent=sum(points,Vector())/len(points)
    wanted=expected['foot_surface_samples'][side][int(round(frame*4))]
    foot_surface_max=max(foot_surface_max,abs(got_min-wanted['min_z_world']), (cent-Vector(wanted['centroid_world'])).length)
  for side,palm,tips in [('left','bone_30',('bone_33','bone_37','bone_41','bone_45','bone_49')),('right','bone_65',('bone_68','bone_72','bone_76','bone_80','bone_84'))]:
   centers=direct.weighted_mesh_group_centroids(meshes,(palm,*tips));pc=centers[palm]
   score=sum((centers[n]-pc).length for n in tips)/len(tips)
   wanted=expected['native_fist_geometry'][side]['fist_range_world']
   fist_max=max(fist_max,max(abs(score-wanted[0]),abs(score-wanted[1])))
  if abs(frame-round(frame))<1e-6 or abs(frame*2-round(frame*2))<1e-6:
   checks.append({'frame':frame,'pose_error':err})
 material_names={o.name:[m.name if m else None for m in o.data.materials] for o in meshes}
 texture_status={}
 for obj in meshes:
  for mat in obj.data.materials:
   if mat and mat.use_nodes and mat.node_tree:
    for node in mat.node_tree.nodes:
     if node.type=='TEX_IMAGE' and node.image:
      image=node.image
      texture_status[image.name]={'size':list(image.size),'packed':image.packed_file is not None}
 textures_valid=all(min(row['size'])>0 and row['packed'] for row in texture_status.values())
 report={'schema':'mg1.direct-walk-contact-reopen.v1','blend':bpy.data.filepath,'rig':rig.name,'action':rig.animation_data.action.name,'visible_armatures':visible_rigs,'visible_meshes':visible_meshes,'mesh_bindings':{o.name:[m.object.name for m in o.modifiers if m.type=='ARMATURE' and m.object] for o in meshes},'materials':material_names,'textures':texture_status,'object_scale':scale,'constraint_count':len(constraints),'frames_checked':len(frames),'max_pose_matrix_error':pose_max,'worst_pose':worst,'max_weighted_foot_surface_error_world':foot_surface_max,'max_closed_fist_metric_error_world':fist_max,'sample_frames':checks,'passed':pose_max<1e-4 and foot_surface_max<1e-3 and fist_max<0.02 and textures_valid and not constraints and len(visible_meshes)==2}
 os.makedirs(os.path.dirname(os.path.abspath(a.output)),exist_ok=True);json.dump(report,open(a.output,'w',encoding='utf-8'),indent=2,allow_nan=False);print(json.dumps(report))
 if not report['passed']:raise RuntimeError('reopened v4 walk failed')
if __name__=='__main__':main()
