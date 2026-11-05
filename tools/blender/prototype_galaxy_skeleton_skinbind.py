"""Prototype binding Sluggers meshes to a disposable Galaxy skeleton copy.

The original action and mesh datablocks are read-only. A disposable 30-bone
Galaxy rig copy receives a curve-identical action copy. Waluigi vertex-group
weights are consolidated through an explicit profile; mesh vertices and
materials are copied unchanged. Run inside Blender 5.2 background mode.
"""
import argparse
import json
import math
import os
import sys

import bpy
from mathutils import Matrix, Vector
from mathutils.kdtree import KDTree

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def cli():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--profile", default=os.path.join(
        HERE, "profiles", "luigi_skeleton_skinbind_waluigi.json"))
    p.add_argument("--source-rig")
    p.add_argument("--source-action", required=True)
    p.add_argument("--target-rig")
    p.add_argument("--target-mesh", action="append", default=[])
    p.add_argument("--output", required=True)
    p.add_argument("--report", required=True)
    p.add_argument("--renders", required=True)
    p.add_argument("--render-frames", nargs="*", type=int, default=[])
    p.add_argument("--sample-step", type=float, default=0.25)
    return p.parse_args(argv)


def mat_rows(m):
    return [[float(m[r][c]) for c in range(4)] for r in range(4)]


def vec_rows(v):
    return [float(v[i]) for i in range(3)]


def quat_or_matrix_error(a, b):
    return max(abs(float(a[r][c]) - float(b[r][c]))
               for r in range(4) for c in range(4))


def action_curves(action, slot):
    result = []
    for layer in action.layers:
        for strip in layer.strips:
            for bag in strip.channelbags:
                if bag.slot == slot:
                    result.extend(bag.fcurves)
    return result


def choose_slot(action):
    choices = []
    for slot in action.slots:
        curves = action_curves(action, slot)
        if curves and any('pose.bones[' in f.data_path for f in curves):
            choices.append((slot, curves))
    if len(choices) != 1:
        raise RuntimeError(f"{action.name}: expected one bone-animation slot, found "
                           f"{[s.identifier for s, _ in choices]}")
    return choices[0]


def copy_action_for_rig(source_action, source_slot, curves, target):
    out = bpy.data.actions.new("GalaxySkinBind_" + source_action.name)
    out.use_fake_user = True
    slot = out.slots.new('OBJECT', target.name)
    layer = out.layers.new("Curve-identical source animation")
    strip = layer.strips.new(type='KEYFRAME')
    bag = strip.channelbag(slot, ensure=True)
    original_fingerprints, copied_fingerprints = [], []
    for curve in curves:
        kwargs = {"index": curve.array_index}
        if curve.group:
            kwargs["group_name"] = curve.group.name
        dst = bag.fcurves.new(curve.data_path, **kwargs)
        dst.keyframe_points.add(len(curve.keyframe_points))
        for src_point, dst_point in zip(curve.keyframe_points, dst.keyframe_points):
            dst_point.co = src_point.co
            dst_point.interpolation = src_point.interpolation
            dst_point.handle_left_type = src_point.handle_left_type
            dst_point.handle_right_type = src_point.handle_right_type
            dst_point.handle_left = src_point.handle_left
            dst_point.handle_right = src_point.handle_right
            dst_point.type = src_point.type
            dst_point.easing = src_point.easing
            dst_point.amplitude = src_point.amplitude
            dst_point.back = src_point.back
            dst_point.period = src_point.period
        dst.extrapolation = curve.extrapolation
        dst.mute = curve.mute
        dst.lock = curve.lock
        original_fingerprints.append((curve.data_path, curve.array_index,
            tuple((tuple(p.co), p.interpolation, p.handle_left_type,
                   p.handle_right_type, tuple(p.handle_left), tuple(p.handle_right))
                  for p in curve.keyframe_points)))
        copied_fingerprints.append((dst.data_path, dst.array_index,
            tuple((tuple(p.co), p.interpolation, p.handle_left_type,
                   p.handle_right_type, tuple(p.handle_left), tuple(p.handle_right))
                  for p in dst.keyframe_points)))
    if original_fingerprints != copied_fingerprints:
        raise RuntimeError("copied action key data/interpolation did not match source")
    target.animation_data_create()
    target.animation_data.action = out
    target.animation_data.action_slot = slot
    return out, slot


def fit_similarity(source, target, landmark_pairs):
    """Fit fixed Z-up to Y-up axis conversion, uniform scale, and translation."""
    # source (x, y, z-up) -> Waluigi local (x, z-up, -y-depth)
    rotate = Matrix(((1.0, 0.0, 0.0),
                     (0.0, 0.0, 1.0),
                     (0.0, -1.0, 0.0)))
    source_pts, target_pts = [], []
    for src_name, dst_name in landmark_pairs:
        if src_name not in source.data.bones or dst_name not in target.data.bones:
            continue
        source_pts.append(source.data.bones[src_name].head_local.copy())
        target_pts.append(target.data.bones[dst_name].head_local.copy())
    if len(source_pts) < 4:
        raise RuntimeError("not enough matched rest landmarks")
    xs = sum(source_pts, Vector()) / len(source_pts)
    ys = sum(target_pts, Vector()) / len(target_pts)
    rotated_centered = [rotate @ (x - xs) for x in source_pts]
    target_centered = [y - ys for y in target_pts]
    denominator = sum(v.length_squared for v in rotated_centered)
    scale = sum(a.dot(b) for a, b in zip(rotated_centered, target_centered)) / denominator
    if not math.isfinite(scale) or scale <= 0:
        raise RuntimeError(f"invalid fitted armature scale {scale}")
    translation = ys - scale * (rotate @ xs)
    transform = Matrix.Translation(translation) @ rotate.to_4x4() @ Matrix.Scale(scale, 4)
    residuals = []
    for (src_name, dst_name), src, dst in zip(landmark_pairs, source_pts, target_pts):
        predicted = transform @ src
        residuals.append({"source_bone": src_name, "target_bone": dst_name,
                          "rest_head_error": float((predicted - dst).length),
                          "predicted": vec_rows(predicted), "target": vec_rows(dst)})
    return transform, scale, residuals


def weight_snapshot(mesh):
    index_to_name = {group.index: group.name for group in mesh.vertex_groups}
    return [{index_to_name[a.group]: float(a.weight)
             for a in vertex.groups if a.weight > 0.0 and a.group in index_to_name}
            for vertex in mesh.data.vertices]


def remap_weights(mesh, profile):
    old = weight_snapshot(mesh)
    weight_map = profile["weight_groups"]
    mapped_rows = []
    unmapped_weight = {}
    required_names = set()
    for row in old:
        mapped = {}
        for old_name, weight in row.items():
            new_name = weight_map.get(old_name)
            if new_name is None:
                unmapped_weight[old_name] = unmapped_weight.get(old_name, 0.0) + weight
                continue
            mapped[new_name] = mapped.get(new_name, 0.0) + weight
            required_names.add(new_name)
        total = sum(mapped.values())
        if total > 1.0 + 1.0e-5:
            mapped = {name: value / total for name, value in mapped.items()}
        mapped_rows.append(mapped)
    if unmapped_weight:
        raise RuntimeError(f"weighted source groups lack explicit mappings: {unmapped_weight}")
    for group in list(mesh.vertex_groups):
        mesh.vertex_groups.remove(group)
    groups = {name: mesh.vertex_groups.new(name=name) for name in sorted(required_names)}
    for index, row in enumerate(mapped_rows):
        for name, weight in row.items():
            groups[name].add([index], float(weight), 'REPLACE')
    return {"source_group_count": len(old and {k for row in old for k in row}),
            "mapped_group_count": len(required_names), "unmapped_weight": unmapped_weight,
            "finger_groups_collapsed_to_hand": True,
            "vertex_weight_sum_range": [min(map(sum, (r.values() for r in mapped_rows))),
                                        max(map(sum, (r.values() for r in mapped_rows)))],
            "weight_rows": mapped_rows}


def duplicate_mesh(source, rig, collection, group_profile):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    source_world = source.evaluated_get(depsgraph).matrix_world.copy()
    duplicate = source.copy()
    duplicate.data = source.data.copy()
    duplicate.name = "GalaxySkinBind_" + source.name
    duplicate.data.name = duplicate.name + "_Data"
    collection.objects.link(duplicate)
    # Keep the authored mesh world transform independent of root animation;
    # the Armature modifier alone binds it to the candidate Galaxy skeleton.
    duplicate.parent = None
    duplicate.matrix_parent_inverse = Matrix.Identity(4)
    duplicate.matrix_world = source_world
    for modifier in duplicate.modifiers:
        if modifier.type == 'ARMATURE':
            modifier.object = rig
            modifier.use_vertex_groups = True
            modifier.use_bone_envelopes = False
    remap_report = remap_weights(duplicate, group_profile)
    duplicate.hide_viewport = False
    duplicate.hide_render = False
    duplicate.hide_set(False)
    return duplicate, remap_report


def evaluated_vertices(obj):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        return [evaluated.matrix_world @ vertex.co for vertex in mesh.vertices]
    finally:
        evaluated.to_mesh_clear()


def percent(values, q):
    if not values:
        return 0.0
    rows = sorted(float(value) for value in values)
    return rows[min(int(round(q * (len(rows) - 1))), len(rows) - 1)]


def dominant_points(vertices, weights, group):
    return [point for point, row in zip(vertices, weights)
            if row and max(row, key=row.get) == group]


def nearest_surface_p95(points_a, points_b):
    if not points_a or not points_b:
        return None
    tree = KDTree(len(points_b))
    for index, point in enumerate(points_b):
        tree.insert(point, index)
    tree.balance()
    distances = sorted(tree.find(point)[2] for point in points_a)
    return float(distances[min(int(round(0.95 * (len(distances)-1))), len(distances)-1)])


SEAM_GROUPS = {
    "left_shoulder": ("bone_23", "bone_25"),
    "left_elbow": ("bone_25", "bone_29"),
    "left_wrist_forearm": ("bone_29", "bone_27"),
    "left_wrist_glove": ("bone_27", "bone_30"),
    "right_shoulder": ("bone_58", "bone_60"),
    "right_elbow": ("bone_60", "bone_64"),
    "right_wrist_forearm": ("bone_64", "bone_62"),
    "right_wrist_glove": ("bone_62", "bone_65"),
}


def set_frame(scene, frame):
    integer = math.floor(frame)
    scene.frame_set(integer, subframe=frame - integer)
    bpy.context.view_layer.update()


def evaluate_action_copy_error(source_curves, copied_curves, first, last, step):
    source_by_key = {(c.data_path, c.array_index): c for c in source_curves}
    copied_by_key = {(c.data_path, c.array_index): c for c in copied_curves}
    if source_by_key.keys() != copied_by_key.keys():
        return float("inf")
    maximum = 0.0
    for key, src in source_by_key.items():
        dst = copied_by_key[key]
        frame = first
        while frame <= last + 1.0e-6:
            maximum = max(maximum, abs(float(src.evaluate(frame)) - float(dst.evaluate(frame))))
            frame += step
    return maximum


def render_previews(scene, rig, meshes, frames, render_dir):
    if not frames:
        frames = [int(scene.frame_start), int((scene.frame_start + scene.frame_end) / 2),
                  int(scene.frame_end)]
    frames = sorted(set(frames))
    poses = {}
    old = (scene.frame_current, scene.frame_subframe)
    for frame in frames:
        set_frame(scene, frame)
        poses[frame] = {pb.name: mat_rows(pb.matrix_basis) for pb in rig.pose.bones}
    set_frame(scene, old[0] + old[1])
    os.makedirs(render_dir, exist_ok=True)
    render_scene = bpy.data.scenes.new("GalaxySkinBind_RenderScene")
    render_scene.world = scene.world
    render_scene.render.engine = "BLENDER_EEVEE"
    render_scene.render.resolution_x = 960
    render_scene.render.resolution_y = 960
    render_scene.render.resolution_percentage = 100
    render_scene.render.image_settings.file_format = "PNG"
    render_scene.view_settings.view_transform = scene.view_settings.view_transform
    render_scene.view_settings.look = scene.view_settings.look
    render_scene.view_settings.exposure = scene.view_settings.exposure
    render_scene.view_settings.gamma = scene.view_settings.gamma
    render_rig = rig.copy()
    render_rig.data = rig.data.copy()
    render_rig.name = "GalaxySkinBind_Render_Rig"
    render_rig.data.name = render_rig.name + "_Data"
    render_rig.animation_data_clear()
    render_rig.parent = None
    render_rig.matrix_world = rig.matrix_world.copy()
    render_scene.collection.objects.link(render_rig)
    render_meshes = []
    for source_mesh in meshes:
        mesh = source_mesh.copy()
        mesh.data = source_mesh.data.copy()
        mesh.name = "GalaxySkinBind_Render_" + source_mesh.name
        mesh.data.name = mesh.name + "_Data"
        mesh.parent = None
        mesh.matrix_world = source_mesh.matrix_world.copy()
        render_scene.collection.objects.link(mesh)
        for modifier in mesh.modifiers:
            if modifier.type == 'ARMATURE':
                modifier.object = render_rig
        mesh.hide_render = False
        mesh.hide_set(False)
        render_meshes.append(mesh)
    points = [point for mesh in meshes for point in evaluated_vertices(mesh)]
    lower = Vector((min(p[i] for p in points) for i in range(3)))
    upper = Vector((max(p[i] for p in points) for i in range(3)))
    center = (lower + upper) * 0.5
    span = max((upper-lower).length, 1.0)
    distance = span * 2.2
    cameras = []
    for label, location in (("front", center + Vector((0.0, -distance, 0.0))),
                            ("side", center + Vector((distance, 0.0, 0.0)))):
        data = bpy.data.cameras.new("GalaxySkinBind_" + label + "_CameraData")
        camera = bpy.data.objects.new("GalaxySkinBind_" + label + "_Camera", data)
        render_scene.collection.objects.link(camera)
        camera.location = location
        camera.rotation_euler = (center-location).to_track_quat("-Z", "Z").to_euler()
        camera.data.type = "ORTHO"
        camera.data.ortho_scale = max((upper-lower).length, 1.0) * 1.2
        cameras.append((label, camera))
    light_data = bpy.data.lights.new("GalaxySkinBind_KeyData", type="AREA")
    light = bpy.data.objects.new("GalaxySkinBind_Key", light_data)
    render_scene.collection.objects.link(light)
    light.location = center + Vector((0.35*distance, -0.5*distance, 0.8*distance))
    light.data.energy = 800000.0
    light.data.size = 0.65*distance
    light.rotation_euler = (center-light.location).to_track_quat("-Z", "Z").to_euler()
    outputs, failures = [], []
    for frame in frames:
        for name, rows in poses[frame].items():
            render_rig.pose.bones[name].matrix_basis = Matrix(rows)
        render_scene.frame_set(frame)
        bpy.context.view_layer.update()
        for view_name, camera in cameras:
            render_scene.camera = camera
            path = os.path.abspath(os.path.join(render_dir, f"frame_{frame:02d}_{view_name}.png"))
            render_scene.render.filepath = path[:-4]
            status = bpy.ops.render.render(write_still=True, scene=render_scene.name)
            if status == {"FINISHED"} and os.path.isfile(path):
                outputs.append(path)
            else:
                failures.append({"frame": frame, "view": view_name, "status": list(status)})
    for obj in [light] + [camera for _label, camera in cameras] + [render_rig] + render_meshes:
        data = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if data and data.users == 0:
            if isinstance(data, bpy.types.Mesh):
                bpy.data.meshes.remove(data)
            elif isinstance(data, bpy.types.Armature):
                bpy.data.armatures.remove(data)
            elif isinstance(data, bpy.types.Camera):
                bpy.data.cameras.remove(data)
            elif isinstance(data, bpy.types.Light):
                bpy.data.lights.remove(data)
    bpy.data.scenes.remove(render_scene)
    return outputs, failures


def main():
    args = cli()
    input_blend = bpy.data.filepath
    scene = bpy.context.scene
    scene.frame_set(0)
    bpy.context.view_layer.update()
    with open(args.profile, "r", encoding="utf-8") as stream:
        profile = json.load(stream)
    source_name = args.source_rig or profile["source_rig"]
    target_name = args.target_rig or profile["target_rig"]
    source = bpy.data.objects.get(source_name)
    target = bpy.data.objects.get(target_name)
    source_action = bpy.data.actions.get(args.source_action)
    if source is None or source.type != 'ARMATURE':
        raise RuntimeError(f"source Galaxy rig missing: {source_name}")
    if target is None or target.type != 'ARMATURE':
        raise RuntimeError(f"target Sluggers rig missing: {target_name}")
    if source_action is None:
        raise RuntimeError(f"source action missing: {args.source_action}")
    source_slot, source_curves = choose_slot(source_action)
    for curve in source_curves:
        if 'pose.bones["' in curve.data_path:
            bone_name = curve.data_path.split('pose.bones["', 1)[1].split('"', 1)[0]
            if source.data.bones.get(bone_name) is None:
                raise RuntimeError(f"source action targets absent bone {bone_name}")

    target_mesh_names = args.target_mesh or profile["target_meshes"]
    target_meshes = [bpy.data.objects.get(name) for name in target_mesh_names]
    if any(obj is None or obj.type != 'MESH' for obj in target_meshes):
        raise RuntimeError(f"one or more target meshes are missing: {target_mesh_names}")
    for mesh in target_meshes:
        if not any(mod.type == 'ARMATURE' and mod.object == target for mod in mesh.modifiers):
            raise RuntimeError(f"{mesh.name} is not bound to {target.name}")

    bpy.context.view_layer.update()
    rest_transform, fit_scale, landmark_residuals = fit_similarity(
        source, target, profile["landmarks"])
    depsgraph = bpy.context.evaluated_depsgraph_get()
    target_world = target.evaluated_get(depsgraph).matrix_world.copy()
    candidate = source.copy()
    candidate.data = source.data.copy()
    candidate.name = "GalaxySkinBind_Luigi_Rig"
    candidate.data.name = candidate.name + "_Data"
    for collection in list(candidate.users_collection):
        collection.objects.unlink(candidate)
    bpy.context.scene.collection.objects.link(candidate)
    candidate.parent = None
    candidate.matrix_parent_inverse = Matrix.Identity(4)
    candidate.animation_data_clear()
    candidate.delta_location = (0.0, 0.0, 0.0)
    candidate.delta_rotation_euler = (0.0, 0.0, 0.0)
    candidate.delta_scale = (1.0, 1.0, 1.0)
    for pose_bone in candidate.pose.bones:
        pose_bone.location = (0.0, 0.0, 0.0)
        pose_bone.rotation_euler = (0.0, 0.0, 0.0)
        pose_bone.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
        pose_bone.scale = (1.0, 1.0, 1.0)
        pose_bone.matrix_basis = Matrix.Identity(4)
    candidate.matrix_world = target_world @ rest_transform
    action_copy, copied_slot = copy_action_for_rig(source_action, source_slot,
                                                   source_curves, candidate)
    candidate_meshes, weight_reports = [], {}
    for mesh in target_meshes:
        duplicate, weight_report = duplicate_mesh(
            mesh, candidate, bpy.context.scene.collection, profile)
        candidate_meshes.append(duplicate)
        weight_reports[mesh.name] = weight_report
    bpy.context.view_layer.update()

    # Rest-pose check: with no action contribution the untouched copied mesh
    # must remain at the source world vertices after rebinding.
    candidate.animation_data.action = None
    for pose_bone in candidate.pose.bones:
        pose_bone.location = (0.0, 0.0, 0.0)
        pose_bone.rotation_euler = (0.0, 0.0, 0.0)
        pose_bone.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
        pose_bone.scale = (1.0, 1.0, 1.0)
        pose_bone.matrix_basis = Matrix.Identity(4)
    set_frame(bpy.context.scene, 0.0)
    rest_errors = []
    rest_mesh_rows = {}
    for original, duplicate in zip(target_meshes, candidate_meshes):
        depsgraph = bpy.context.evaluated_depsgraph_get()
        original_eval = original.evaluated_get(depsgraph)
        duplicate_eval = duplicate.evaluated_get(depsgraph)
        before = [original_eval.matrix_world @ v.co for v in original.data.vertices]
        after = evaluated_vertices(duplicate)
        local_evaluated = duplicate_eval.to_mesh()
        local_errors = [(local_evaluated.vertices[i].co - duplicate.data.vertices[i].co).length
                        for i in range(min(len(local_evaluated.vertices), len(duplicate.data.vertices)))]
        duplicate_eval.to_mesh_clear()
        mesh_errors = [(a - b).length for a, b in zip(before, after)]
        rest_errors.extend(mesh_errors)
        rest_mesh_rows[original.name] = {
            "source_world_matrix": mat_rows(original_eval.matrix_world),
            "candidate_world_matrix": mat_rows(duplicate_eval.matrix_world),
            "max_vertex_error": max(mesh_errors, default=0.0),
            "max_local_deform_from_rest": max(local_errors, default=0.0),
        }
        if len(before) != len(after):
            raise RuntimeError(f"rest mesh vertex count changed for {original.name}")
    max_rest_error = max(rest_errors, default=0.0)
    candidate.animation_data.action = action_copy
    candidate.animation_data.action_slot = copied_slot
    bpy.context.view_layer.update()

    first, last = map(float, source_action.frame_range)
    step = float(args.sample_step)
    frames = []
    frame = first
    while frame <= last + 1.0e-6:
        frames.append(round(frame, 6))
        frame += step
    if not frames or frames[-1] < last - 1.0e-6:
        frames.append(last)
    candidate_action_curves = action_curves(action_copy, copied_slot)
    curve_error = evaluate_action_copy_error(source_curves, candidate_action_curves,
                                             first, last, min(0.25, step))
    region_names = {
        "left_upper_arm": {"bone_22", "bone_23", "bone_24", "bone_25"},
        "left_forearm": {"bone_26", "bone_27", "bone_29"},
        "left_hand_fist": {f"bone_{n}" for n in range(30, 52)},
        "right_upper_arm": {"bone_57", "bone_58", "bone_59", "bone_60"},
        "right_forearm": {"bone_61", "bone_62", "bone_64"},
        "right_hand_fist": {f"bone_{n}" for n in range(65, 87)},
    }
    region_indices = {}
    original_weight_rows = {}
    for name, mesh in zip(target_mesh_names, target_meshes):
        original_weights = weight_snapshot(mesh)
        original_weight_rows[name] = original_weights
        region_indices[name] = {region: [i for i, row in enumerate(original_weights)
                                          if any(row.get(group, 0.0) > 0.02
                                                 for group in groups)]
                                for region, groups in region_names.items()}

    reference_meshes = [next((obj for obj in bpy.data.objects
                              if obj.type == 'MESH' and obj.name == mesh.name.replace(
                                  "LiteralWalk_", "LiteralWalk_")), None)
                        for mesh in target_meshes]
    # The named target meshes themselves are the converted V2 reference. Keep
    # their original evaluated geometry available while candidates are hidden.
    if reference_meshes != target_meshes:
        reference_meshes = target_meshes
    sample_report = {}
    worst = {"all": {"distance": -1.0}, "left_arm": {"distance": -1.0},
             "right_arm": {"distance": -1.0}}
    all_errors = []
    for frame in frames:
        set_frame(bpy.context.scene, frame)
        per_mesh = {}
        for original, duplicate in zip(target_meshes, candidate_meshes):
            # Original target mesh is the converted V2 reference.
            reference = evaluated_vertices(original)
            candidate_vertices = evaluated_vertices(duplicate)
            if len(reference) != len(candidate_vertices):
                raise RuntimeError(f"vertex count mismatch at {frame} for {original.name}")
            distances = [(a - b).length for a, b in zip(candidate_vertices, reference)]
            deltas = [a - b for a, b in zip(candidate_vertices, reference)]
            mean_delta = sum(deltas, Vector()) / max(len(deltas), 1)
            shape_distances = [(delta - mean_delta).length for delta in deltas]
            all_errors.extend(distances)
            metrics = {"vertex_count": len(distances), "max": max(distances, default=0.0),
                       "rms": math.sqrt(sum(v*v for v in distances) / max(len(distances), 1)),
                       "p95": percent(distances, 0.95),
                       "mean_translation_delta_world": vec_rows(mean_delta),
                       "mean_translation_delta_length": float(mean_delta.length),
                       "shape_only_max": max(shape_distances, default=0.0),
                       "shape_only_rms": math.sqrt(sum(v*v for v in shape_distances) /
                                                    max(len(shape_distances), 1)),
                       "shape_only_p95": percent(shape_distances, 0.95)}
            metrics["seams"] = {}
            original_weights = original_weight_rows[original.name]
            for seam_name, (group_a, group_b) in SEAM_GROUPS.items():
                ref_a = dominant_points(reference, original_weights, group_a)
                ref_b = dominant_points(reference, original_weights, group_b)
                cand_a = dominant_points(candidate_vertices, original_weights, group_a)
                cand_b = dominant_points(candidate_vertices, original_weights, group_b)
                metrics["seams"][seam_name] = {
                    "groups": [group_a, group_b],
                    "candidate_nearest_surface_p95": nearest_surface_p95(cand_a, cand_b),
                    "v2_nearest_surface_p95": nearest_surface_p95(ref_a, ref_b),
                    "candidate_region_counts": [len(cand_a), len(cand_b)],
                }
            for region, indices in region_indices[original.name].items():
                region_values = [distances[i] for i in indices]
                region_deltas = [deltas[i] for i in indices]
                region_mean_delta = sum(region_deltas, Vector()) / max(len(region_deltas), 1)
                region_shape = [(delta-region_mean_delta).length for delta in region_deltas]
                metrics[region] = {"count": len(region_values),
                                   "max": max(region_values, default=0.0),
                                   "rms": math.sqrt(sum(v*v for v in region_values) /
                                                    max(len(region_values), 1)),
                                   "p95": percent(region_values, 0.95),
                                   "mean_translation_delta_world": vec_rows(region_mean_delta),
                                   "shape_only_max": max(region_shape, default=0.0),
                                   "shape_only_rms": math.sqrt(sum(v*v for v in region_shape) /
                                                                max(len(region_shape), 1)),
                                   "shape_only_p95": percent(region_shape, 0.95)}
                side = "left_arm" if region.startswith("left_") else (
                    "right_arm" if region.startswith("right_") else None)
                if side and region_values:
                    value = max(region_values)
                    if value > worst[side]["distance"]:
                        worst[side] = {"distance": value, "frame": frame,
                                       "mesh": original.name, "region": region}
            if metrics["max"] > worst["all"]["distance"]:
                worst["all"] = {"distance": metrics["max"], "frame": frame,
                                "mesh": original.name}
            per_mesh[original.name] = metrics
        sample_report[str(frame)] = per_mesh

    all_errors.sort()
    max_error = max(all_errors, default=0.0)
    seam_values = {name: [] for name in SEAM_GROUPS}
    for per_mesh in sample_report.values():
        for mesh_metrics in per_mesh.values():
            for seam_name, seam_metric in mesh_metrics.get("seams", {}).items():
                candidate_p95 = seam_metric["candidate_nearest_surface_p95"]
                v2_p95 = seam_metric["v2_nearest_surface_p95"]
                if candidate_p95 is not None and v2_p95 is not None:
                    seam_values[seam_name].append({
                        "candidate_p95": candidate_p95, "v2_p95": v2_p95,
                        "absolute_change": abs(candidate_p95-v2_p95)})
    summary = {"max": max_error,
               "rms": math.sqrt(sum(v*v for v in all_errors) / max(len(all_errors), 1)),
               "p95": percent(all_errors, 0.95), "worst": worst}
    summary["seam_nearest_surface_p95"] = {
        name: {"samples": len(rows),
               "candidate_max_p95": max((r["candidate_p95"] for r in rows), default=None),
               "v2_max_p95": max((r["v2_p95"] for r in rows), default=None),
               "max_absolute_p95_change": max((r["absolute_change"] for r in rows), default=None)}
        for name, rows in seam_values.items()}

    # Render immutable pose snapshots before hiding the source/reference scene.
    preview_frames = args.render_frames or sorted(set(
        [int(round(first)), int(round(first + (last-first)*0.25)),
         int(round((first+last)*0.5)), int(round(first + (last-first)*0.75)),
         int(round(last))]))
    render_files, render_failures = render_previews(
        bpy.context.scene, candidate, candidate_meshes, preview_frames, args.renders)

    visible = {candidate, *candidate_meshes}
    for obj in bpy.data.objects:
        show = obj in visible
        obj.hide_render = not show
        obj.hide_viewport = not show
        obj.hide_set(not show)
    candidate.hide_viewport = False
    candidate.hide_render = False
    candidate.hide_set(False)
    for mesh in candidate_meshes:
        mesh.hide_viewport = False
        mesh.hide_render = False
        mesh.hide_set(False)
    bpy.context.scene.frame_start = math.floor(first)
    bpy.context.scene.frame_end = math.ceil(last)
    set_frame(bpy.context.scene, first)
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(args.output))

    report = {
        "schema": "mg1.galaxy-skeleton-mesh-bind-prototype.v1",
        "input_blend": input_blend,
        "source_rig": source.name,
        "source_action": source_action.name,
        "source_slot": source_slot.identifier,
        "action_curve_count": len(source_curves),
        "action_curve_sample_error": curve_error,
        "frame_range": [first, last], "sample_step": step,
        "samples_checked": len(frames), "source_meshes": target_mesh_names,
        "candidate_rig": candidate.name,
        "candidate_action": action_copy.name,
        "candidate_action_slot": copied_slot.identifier,
        "target_original_object_scale": list(target.matrix_basis.to_scale()),
        "candidate_fit_object_scale": list(candidate.matrix_basis.to_scale()),
        "fitted_uniform_rest_scale": fit_scale,
        "fitted_rest_transform": mat_rows(rest_transform),
        "rest_landmark_errors": landmark_residuals,
        "rest_landmark_rms": math.sqrt(sum(r["rest_head_error"]**2
                                            for r in landmark_residuals) /
                                          max(len(landmark_residuals), 1)),
        "rest_rebind_max_world_vertex_error": max_rest_error,
        "rest_rebind_mesh_diagnostics": rest_mesh_rows,
        "weight_remapping": {k: {key: value for key, value in v.items()
                                  if key != "weight_rows"}
                             for k, v in weight_reports.items()},
        "converted_v2_mesh_difference": summary,
        "sample_metrics": sample_report,
        "preview": os.path.abspath(args.output),
        "render_files": render_files,
        "render_failures": render_failures,
        "human_visual_approval": False,
        "limitations": [
            "The fitted transform is one uniform similarity, so it cannot reproduce nonuniform limb, torso, or hand proportions.",
            "Waluigi's overlapping arm and finger groups are consolidated onto the 30-bone Galaxy controls; this removes independent Sluggers branch and finger deformation.",
            "Rest landmark residuals and evaluated mesh differences are diagnostics; they do not establish acceptable visual deformation.",
            "A successful Waluigi prototype does not establish Mario-to-Wario compatibility or full-bank scalability."
        ]
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.report)), exist_ok=True)
    with open(args.report, "w", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
    print("GALAXY_SKIN_BIND", json.dumps({k: report[k] for k in (
        "source_action", "samples_checked", "fitted_uniform_rest_scale",
        "rest_landmark_rms", "rest_rebind_max_world_vertex_error",
        "converted_v2_mesh_difference", "render_files", "render_failures")}, sort_keys=True))


if __name__ == "__main__":
    main()
