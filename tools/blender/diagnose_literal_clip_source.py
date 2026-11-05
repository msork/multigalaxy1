"""Compare raw Luigi BCK poses with a saved literal-transfer preview.

No saved rig, action, mesh, or source asset is modified. The script evaluates
the source BCK curves on a disposable armature copy, samples the saved target
action/report, and renders each character separately from front and side.
"""
import argparse
import json
import math
import os
import sys

import bpy
from mathutils import Matrix, Vector

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from prototype_direct_luigi_walk_literal import (
    bag_for, curve_path_bone, duplicate_rig, evaluate_curves,
    pose_matrices_from_basis,
)
from test_semantic_preview_noop import duplicate_mesh


def cli():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--source-rig", default="luigi_armature")
    p.add_argument("--source-action", required=True)
    p.add_argument("--target-rig", default="LiteralWalk_Waluigi_Target")
    p.add_argument("--fit-report", required=True)
    p.add_argument("--frames", nargs="+", type=float, required=True)
    p.add_argument("--output-dir", required=True)
    p.add_argument("--output-report", required=True)
    return p.parse_args(argv)


def world_vertices(meshes):
    dg = bpy.context.evaluated_depsgraph_get()
    points = []
    for obj in meshes:
        evaluated = obj.evaluated_get(dg)
        mesh = evaluated.to_mesh()
        try:
            points.extend(evaluated.matrix_world @ v.co for v in mesh.vertices)
        finally:
            evaluated.to_mesh_clear()
    return points


def make_camera(scene, location, center):
    data = bpy.data.cameras.new("ClipCompare_Camera_Data")
    obj = bpy.data.objects.new("ClipCompare_Camera", data)
    scene.collection.objects.link(obj)
    obj.location = location
    obj.rotation_euler = (Vector(center) - Vector(location)).to_track_quat("-Z", "Y").to_euler()
    obj.data.lens = 52
    return obj


def set_pose_from_report(rig, fit, frame):
    row = fit["expected_pose_basis"].get(str(frame))
    if row is None:
        raise RuntimeError(f"target fit report has no exact pose at frame {frame}")
    for name, matrix in row.items():
        rig.pose.bones[name].matrix_basis = Matrix(matrix)
    location = fit.get("expected_object_location", {}).get(str(frame))
    if location is not None:
        rig.location = location


def quat_delta_angle(pose_matrix, rest_matrix):
    qp = pose_matrix.to_3x3().to_quaternion().normalized()
    qr = rest_matrix.to_3x3().to_quaternion().normalized()
    delta = qp @ qr.inverted()
    delta.normalize()
    return 2.0 * math.acos(min(1.0, max(-1.0, abs(float(delta.w)))))


def bone_head(pose_matrices, name):
    return pose_matrices[name].translation.copy()


def setup_render_scene(scene, rig, meshes, label, out_dir, frames, render_pose):
    render_scene = bpy.data.scenes.new("ClipCompare_" + label)
    render_scene.world = scene.world
    render_scene.render.engine = "BLENDER_EEVEE"
    render_scene.render.resolution_x = 800
    render_scene.render.resolution_y = 800
    render_scene.render.resolution_percentage = 100
    render_scene.render.image_settings.file_format = "PNG"
    render_scene.view_settings.view_transform = scene.view_settings.view_transform
    render_scene.view_settings.look = scene.view_settings.look
    render_scene.view_settings.exposure = scene.view_settings.exposure
    render_scene.view_settings.gamma = scene.view_settings.gamma
    render_scene.display.shading.light = "STUDIO"
    render_scene.display.shading.color_type = "MATERIAL"
    render_rig = duplicate_rig(rig, "ClipCompare_" + label + "_Rig")
    for col in list(render_rig.users_collection):
        col.objects.unlink(render_rig)
    render_scene.collection.objects.link(render_rig)
    render_rig.matrix_world = rig.matrix_world.copy()
    render_rig.animation_data_clear()
    for original, clone in zip(rig.pose.bones, render_rig.pose.bones):
        clone.rotation_mode = original.rotation_mode
    render_meshes = [duplicate_mesh(obj, render_rig, render_scene.collection,
                                    "ClipCompare_" + label + "_") for obj in meshes]
    for obj in [render_rig] + render_meshes:
        obj.hide_render = False
        obj.hide_set(False)
    render_scene.frame_start, render_scene.frame_end = int(min(frames)), int(max(frames))
    outputs = []
    key = bpy.data.lights.new("ClipCompare_Key_Data", type="AREA")
    light = bpy.data.objects.new("ClipCompare_Key", key)
    render_scene.collection.objects.link(light)
    for frame in frames:
        render_pose(render_rig, frame)
        render_scene.frame_set(math.floor(frame), subframe=frame-math.floor(frame))
        points = world_vertices(render_meshes)
        lo = Vector((min(p[i] for p in points) for i in range(3)))
        hi = Vector((max(p[i] for p in points) for i in range(3)))
        center = (lo + hi) * 0.5
        span = max((hi-lo).length, 1.0)
        distance = span * 2.35
        cameras = [
            ("front", center + Vector((0, -distance, 0))),
            ("side", center + Vector((distance, 0, 0))),
        ]
        light.location = center + Vector((.35*distance, -.5*distance, .8*distance))
        light.data.energy = 800000
        light.data.shape = "DISK"
        light.data.size = .65 * distance
        light.rotation_euler = (center-light.location).to_track_quat("-Z", "Y").to_euler()
        for view, location in cameras:
            camera = make_camera(render_scene, location, center)
            render_scene.camera = camera
            path = os.path.abspath(os.path.join(out_dir, f"{label}_{frame:g}_{view}.png"))
            render_scene.render.filepath = path
            bpy.ops.render.render(write_still=True, scene=render_scene.name)
            outputs.append(path)
            data = camera.data
            bpy.data.objects.remove(camera, do_unlink=True)
            bpy.data.cameras.remove(data)
    temporary = [light, render_rig] + render_meshes
    bpy.data.scenes.remove(render_scene)
    for obj in temporary:
        data = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if data and data.users == 0:
            if isinstance(data, bpy.types.Mesh):
                bpy.data.meshes.remove(data)
            elif isinstance(data, bpy.types.Armature):
                bpy.data.armatures.remove(data)
            elif isinstance(data, bpy.types.Light):
                bpy.data.lights.remove(data)
    return outputs


def main():
    args = cli()
    os.makedirs(args.output_dir, exist_ok=True)
    with open(args.fit_report, "r", encoding="utf-8") as f:
        fit = json.load(f)
    scene = bpy.context.scene
    source = bpy.data.objects.get(args.source_rig)
    target = bpy.data.objects.get(args.target_rig)
    action = bpy.data.actions.get(args.source_action)
    if not source or not target or not action:
        raise RuntimeError("source rig/action or target preview rig missing")
    source_slot = next((slot for slot in action.slots
                        if slot.identifier == (source.animation_data.action_slot.identifier
                            if source.animation_data and source.animation_data.action_slot else None)), None)
    if source_slot is None:
        if len(action.slots) != 1:
            raise RuntimeError("source BCK has an ambiguous action slot")
        source_slot = action.slots[0]
    source_curves = list(bag_for(action, source_slot).fcurves)
    source_meshes = [obj for obj in bpy.data.objects if obj.type == "MESH" and
                     any(mod.type == "ARMATURE" and mod.object == source for mod in obj.modifiers)]
    target_meshes = [obj for obj in bpy.data.objects if obj.type == "MESH" and
                     any(mod.type == "ARMATURE" and mod.object == target for mod in obj.modifiers)]
    if not source_meshes or not target_meshes:
        raise RuntimeError("source or target skinned mesh objects not found")
    source_eval = duplicate_rig(source, "ClipCompare_Source_Evaluator")
    for original, clone in zip(source.pose.bones, source_eval.pose.bones):
        clone.rotation_mode = original.rotation_mode

    def pose_source(rig, frame):
        evaluate_curves(rig, source_curves, frame)

    def pose_target(rig, frame):
        set_pose_from_report(rig, fit, frame)

    source_paths = setup_render_scene(scene, source, source_meshes, "Luigi", args.output_dir,
                                      args.frames, pose_source)
    target_paths = setup_render_scene(scene, target, target_meshes, "Waluigi", args.output_dir,
                                      args.frames, pose_target)
    geometry = {"frame": {}, "source_action": action.name,
                "source_loop_type": getattr(action, "bck_loop_type", None),
                "source_frame_range": [float(v) for v in action.frame_range],
                "target_action": target.animation_data.action.name,
                "renders": source_paths + target_paths}
    source_mats = {}
    target_mats = {}
    for frame in args.frames:
        scene.frame_set(math.floor(frame), subframe=frame-math.floor(frame))
        pose_source(source_eval, frame)
        source_mats[frame] = pose_matrices_from_basis(source_eval)
        set_pose_from_report(target, fit, frame)
        target_mats[frame] = pose_matrices_from_basis(target)
        semantic = {}
        pairs = [("shoulder_l", "ShoulderL", "bone_22"),
                 ("upper_arm_l", "ArmL1", "bone_23"),
                 ("forearm_l", "ArmL2", "bone_26"),
                 ("hand_l", "HandL", "bone_30"),
                 ("shoulder_r", "ShoulderR", "bone_57"),
                 ("upper_arm_r", "ArmR1", "bone_58"),
                 ("forearm_r", "ArmR2", "bone_61"),
                 ("hand_r", "HandR", "bone_65"),
                 ("leg_l1", "LegL1", "bone_7"),
                 ("leg_l2", "LegL2", "bone_8"),
                 ("leg_r1", "LegR1", "bone_13"),
                 ("leg_r2", "LegR2", "bone_14")]
        for label, src_name, dst_name in pairs:
            if src_name not in source_mats[frame] or dst_name not in target_mats[frame]:
                continue
            src_angle = quat_delta_angle(source_mats[frame][src_name],
                                         source.data.bones[src_name].matrix_local)
            dst_angle = quat_delta_angle(target_mats[frame][dst_name],
                                         target.data.bones[dst_name].matrix_local)
            semantic[label] = {"source_rest_relative_angle_rad": src_angle,
                               "target_rest_relative_angle_rad": dst_angle}
        geometry["frame"][str(frame)] = semantic
    source_data = source_eval.data
    bpy.data.objects.remove(source_eval, do_unlink=True)
    if source_data.users == 0:
        bpy.data.armatures.remove(source_data)
    with open(args.output_report, "w", encoding="utf-8") as f:
        json.dump(geometry, f, indent=2)
    print("CLIP_SOURCE_COMPARISON", json.dumps({
        "action": action.name, "frame_range": geometry["source_frame_range"],
        "renders": len(geometry["renders"]), "frames": args.frames}, sort_keys=True))


if __name__ == "__main__":
    main()
