"""Standardized, root-normalized diagnostic renders and geometry export.

Run with Blender 5.2 against one disposable preview file. It does not save the
loaded file or alter its animation data. Meshes are evaluated at requested
frames, transformed into a canonical character frame, then rendered as static
geometry through identical cameras/light settings.
"""
import argparse
import gzip
import json
import math
import os
import sys

import bpy
from mathutils import Matrix, Vector
from mathutils.kdtree import KDTree

HERE = os.path.dirname(os.path.abspath(__file__))
PROFILE_PATH = os.path.join(HERE, "profiles", "luigi_hybrid_waluigi.json")


def arguments():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--clip", required=True)
    p.add_argument("--candidate", choices=("reference", "hybrid"), required=True)
    p.add_argument("--output", required=True, help="gzip JSON vertex and measurement bundle")
    p.add_argument("--render-dir", required=True)
    p.add_argument("--frames", type=float, nargs="+", required=True)
    p.add_argument("--neutral", action="store_true")
    return p.parse_args(argv)


def find_subject():
    meshes = [o for o in bpy.data.objects if o.type == 'MESH' and
              not o.hide_render and not o.hide_viewport and not o.hide_get()]
    rigs = []
    for mesh in meshes:
        for mod in mesh.modifiers:
            if mod.type == 'ARMATURE' and mod.object and mod.object not in rigs:
                rigs.append(mod.object)
    if len(rigs) != 1:
        raise RuntimeError(f"expected one visible mesh-bound armature, got {[x.name for x in rigs]}")
    rig = rigs[0]
    meshes = sorted([m for m in meshes if any(x.type == 'ARMATURE' and x.object == rig for x in m.modifiers)],
                    key=lambda x: x.name)
    if len(meshes) != 2:
        raise RuntimeError(f"expected two visible bound meshes, got {[x.name for x in meshes]}")
    return rig, meshes


def canonical_transform(rig):
    # Use the same rest anatomy and original scene-facing convention for every
    # input. Local Waluigi up comes from pelvis->head; its front is the source
    # camera's -world-Y direction expressed in the rig rest frame.
    pelvis = rig.data.bones.get('bone_19')
    head = rig.data.bones.get('bone_53')
    if not pelvis or not head:
        raise RuntimeError("Waluigi rest landmarks bone_19/bone_53 missing")
    up = (head.head_local - pelvis.head_local).normalized()
    front = rig.matrix_world.to_3x3().inverted_safe() @ Vector((0.0, -1.0, 0.0))
    front -= up * front.dot(up)
    front.normalize()
    right = up.cross(front).normalized()
    # Output basis: +X character-right, -Y character-facing, +Z character-up.
    # Rows form a proper rotation and make the front view look from -Y.
    return Matrix((right, -front, up))


def weighted_indices(mesh, groups, threshold=0.02):
    group_index = {g.name: g.index for g in mesh.vertex_groups}
    indices = {}
    for group in groups:
        gi = group_index.get(group)
        if gi is None:
            indices[group] = []
            continue
        ids = []
        for v in mesh.data.vertices:
            if any(x.group == gi and x.weight >= threshold for x in v.groups):
                ids.append(v.index)
        indices[group] = ids
    return indices


def percentile(values, q):
    if not values:
        return None
    vals = sorted(values)
    return float(vals[min(len(vals)-1, int(math.ceil(q * len(vals))-1))])


def seam_p95(points_by_group, pair):
    a, b = points_by_group.get(pair[0], []), points_by_group.get(pair[1], [])
    if not a or not b:
        return None
    tree = KDTree(len(b))
    for i, p in enumerate(b):
        tree.insert(p, i)
    tree.balance()
    return percentile([tree.find(p)[2] for p in a], 0.95)


def pose_landmarks(rig, canonical):
    names = ['bone_6','bone_19','bone_20','bone_21','bone_22','bone_23','bone_24','bone_25',
             'bone_26','bone_27','bone_29','bone_30','bone_57','bone_58','bone_59','bone_60',
             'bone_61','bone_62','bone_64','bone_65','bone_53']
    rows = {}
    for name in names:
        pb = rig.pose.bones.get(name)
        if not pb:
            continue
        rows[name] = {
            'head': list(canonical @ pb.head),
            'tail': list(canonical @ pb.tail),
            'rotation': list((canonical.to_4x4() @ pb.matrix.to_3x3().to_4x4() @
                              canonical.to_4x4().inverted()).to_quaternion()),
        }
    for side, hand, forearms in [('left','bone_30',['bone_27','bone_29']),
                                 ('right','bone_65',['bone_62','bone_64'])]:
        qh = rig.pose.bones[hand].matrix.to_quaternion()
        rows['relative_hand_'+side] = {}
        for forearm in forearms:
            qf = rig.pose.bones[forearm].matrix.to_quaternion()
            qrel = qf.inverted() @ qh
            rows['relative_hand_'+side][forearm] = list(qrel)
    return rows


def set_scene_render(scene, cameras, light):
    scene.render.engine = 'BLENDER_EEVEE'
    scene.render.resolution_x = 512
    scene.render.resolution_y = 512
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = 'PNG'
    scene.render.image_settings.color_mode = 'RGBA'
    scene.render.film_transparent = True
    scene.render.image_settings.compression = 15
    scene.render.filepath = '//'
    scene.view_settings.view_transform = 'Standard'
    scene.view_settings.look = 'Medium High Contrast'
    scene.view_settings.exposure = 0.0
    scene.view_settings.gamma = 1.0
    scene.camera = cameras[0][1]
    scene.render.resolution_percentage = 100


def make_render_scene(source_scene):
    scene = bpy.data.scenes.new('HybridLiteralDiagnosticScene')
    scene.render.engine = 'BLENDER_EEVEE'
    scene.render.resolution_x = scene.render.resolution_y = 512
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = 'PNG'
    scene.render.image_settings.color_mode = 'RGBA'
    scene.render.film_transparent = True
    scene.view_settings.view_transform = 'Standard'
    scene.view_settings.look = 'Medium High Contrast'
    scene.render.resolution_percentage = 100
    world = bpy.data.worlds.new('HybridLiteralDiagnosticWorld')
    world.use_nodes = True
    bg = world.node_tree.nodes.get('Background')
    bg.inputs['Color'].default_value = (0.18, 0.18, 0.18, 1.0)
    bg.inputs['Strength'].default_value = 0.0
    scene.world = world
    target = Vector((0.0, 0.0, 1.80))
    views = [('front', Vector((0.0,-6.5,0.0))),
             ('side', Vector((6.5,0.0,0.0))),
             ('three_quarter', Vector((4.7,-6.5,0.0)))]
    cameras = []
    for label, delta in views:
        data = bpy.data.cameras.new('Diagnostic_'+label)
        data.type = 'ORTHO'
        data.ortho_scale = 5.0
        obj = bpy.data.objects.new('Diagnostic_'+label, data)
        scene.collection.objects.link(obj)
        obj.location = target + delta
        # Camera local Y is its image-up axis. Tracking with local Z here rolls
        # the side camera by 90 degrees when its view direction is horizontal.
        obj.rotation_euler = (target-obj.location).to_track_quat('-Z','Y').to_euler()
        cameras.append((label,obj))
    ld = bpy.data.lights.new('Diagnostic_Area','AREA')
    ld.energy = 720.0
    ld.shape = 'DISK'
    ld.size = 5.0
    light = bpy.data.objects.new('Diagnostic_Area',ld)
    scene.collection.objects.link(light)
    light.location = (4.0,-4.5,6.0)
    light.rotation_euler = (target-light.location).to_track_quat('-Z','Y').to_euler()
    return scene, cameras, light


def evaluated_canonical_mesh(source, rig, canonical):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = source.evaluated_get(depsgraph)
    mesh = bpy.data.meshes.new_from_object(evaluated, depsgraph=depsgraph, preserve_all_data_layers=True)
    transform = canonical.to_4x4() @ rig.matrix_world.inverted_safe() @ evaluated.matrix_world
    points = [transform @ v.co for v in mesh.vertices]
    for v, p in zip(mesh.vertices, points):
        v.co = p
    mesh.update()
    mesh.materials.clear()
    for slot in source.material_slots:
        mesh.materials.append(slot.material)
    return mesh, points


def render_pose(label, frame, rig, meshes, canonical, render_scene, cameras, light, out_dir):
    static_objects, mesh_rows, points_by_group = [], [], {}
    profile = json.load(open(PROFILE_PATH,encoding='utf-8'))
    groups = sorted({g for pair in profile['seams'].values() for g in pair} |
                    {g for values in profile['regions'].values() for g in values})
    group_indices = {}
    for i, src in enumerate(meshes):
        mesh, points = evaluated_canonical_mesh(src, rig, canonical)
        obj = bpy.data.objects.new('DiagnosticMesh_'+str(i),mesh)
        render_scene.collection.objects.link(obj)
        obj.matrix_world = Matrix.Identity(4)
        static_objects.append(obj)
        mesh_rows.append({'name':src.name,'points':[list(p) for p in points]})
        inds = weighted_indices(src, groups)
        group_indices[src.name] = inds
        for group, ids in inds.items():
            points_by_group.setdefault(group,[]).extend(points[j] for j in ids if j < len(points))
    root = {"frame":frame,"meshes":mesh_rows,
            "bones":pose_landmarks(rig,canonical),
            "seam_p95":{k:seam_p95(points_by_group,pair) for k,pair in profile['seams'].items()}}
    outputs=[]
    for view,camera in cameras:
        render_scene.camera=camera
        fname=f"{label}_{str(frame).replace('.','_')}_{view}.png"
        path=os.path.abspath(os.path.join(out_dir,fname))
        render_scene.render.filepath=path
        bpy.ops.render.render(write_still=True,scene=render_scene.name)
        outputs.append({'view':view,'path':path})
    for obj in static_objects:
        data=obj.data
        bpy.data.objects.remove(obj,do_unlink=True)
        bpy.data.meshes.remove(data)
    return root,group_indices,outputs


def main():
    args=arguments()
    rig,meshes=find_subject()
    scene=bpy.context.scene
    source_scene=scene
    canonical=canonical_transform(rig)
    render_scene,cameras,light=make_render_scene(source_scene)
    os.makedirs(args.render_dir,exist_ok=True)
    frames=[float(x) for x in args.frames]
    data={'schema':'mg1.hybrid-literal-diagnostic.v1','clip':args.clip,'candidate':args.candidate,
          'blend':bpy.data.filepath,'rig':rig.name,'meshes':[m.name for m in meshes],
          'frame_range':[scene.frame_start,scene.frame_end],'canonical_matrix':[list(r) for r in canonical],
          'camera':{'views':['front','side','three_quarter'],'ortho_scale':5.0,'resolution':[512,512],
                    'up':'canonical +Z','facing':'canonical -Y','lighting':'fixed area light 720W, size 5'},
          'samples':{},'render_files':[],'group_indices':{}}
    saved_action=rig.animation_data.action if rig.animation_data else None
    saved_slot=rig.animation_data.action_slot if rig.animation_data else None
    if args.neutral:
        if rig.animation_data:
            rig.animation_data.action=None
        for pb in rig.pose.bones:
            pb.matrix_basis=Matrix.Identity(4)
        scene.frame_set(scene.frame_start)
        bpy.context.view_layer.update()
        sample,indices,rendered=render_pose('neutral','rest',rig,meshes,canonical,render_scene,cameras,light,args.render_dir)
        data['samples']['rest']=sample
        data['group_indices']=indices
        data['render_files'].extend(rendered)
        if rig.animation_data:
            rig.animation_data.action=saved_action
            if saved_action and saved_slot:
                rig.animation_data.action_slot=saved_slot
    for frame in frames:
        whole=math.floor(frame)
        scene.frame_set(whole,subframe=frame-whole)
        bpy.context.view_layer.update()
        sample,indices,rendered=render_pose('frame',frame,rig,meshes,canonical,render_scene,cameras,light,args.render_dir)
        data['samples'][str(frame)]=sample
        if not data['group_indices']:
            data['group_indices']=indices
        data['render_files'].extend(rendered)
    with gzip.open(args.output,'wt',encoding='utf-8',compresslevel=6) as f:
        json.dump(data,f,separators=(',',':'))
    print(json.dumps({'output':args.output,'render_dir':args.render_dir,'frames':len(frames),
                      'render_files':len(data['render_files']),'rig':rig.name,'meshes':data['meshes']}))


if __name__=='__main__':
    main()
