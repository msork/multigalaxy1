"""Literal, rest-calibrated Luigi walk transfer onto disposable Waluigi.

The source action is evaluated from its original Blender 5 layered channelbag.
No IK, trajectory reconstruction, learned pose model, or contact adjustment is
used. Source local basis rotations are converted through per-bone rest-frame
quaternions, then baked on a duplicate Waluigi rig. Sampled baking retains all
source key times and evaluates the original curves every quarter frame.
"""
import argparse
import json
import math
import os
import sys

import bpy
from mathutils import Matrix, Quaternion, Vector

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from convert_native_owner_actions import duplicate_rig
from literal_retarget_math import local_frame_conversion, map_armature_pose_rotation
from test_semantic_preview_noop import copy_action_slot, duplicate_mesh


# Source semantic joints to Waluigi deform outputs. Multi-output mappings drive
# both overlapping arm branches from the same source joint while calibrating
# each target bone's own rest frame independently.
BONE_OUTPUTS = {
    "JointRoot": ("bone_6",),
    "Hip": ("bone_19",),
    "Spine1": ("bone_20",),
    "Spine2": ("bone_21",),
    "Head": ("bone_53",),
    "LegL1": ("bone_7",), "LegL2": ("bone_8",), "FootL": ("bone_9",),
    "LegR1": ("bone_13",), "LegR2": ("bone_14",), "FootR": ("bone_15",),
    "ShoulderL": ("bone_22",),
    "ArmL1": ("bone_23", "bone_24", "bone_25"),
    "ArmL2": ("bone_26", "bone_27", "bone_29"),
    "HandL": ("bone_30",),
    "ShoulderR": ("bone_57",),
    "ArmR1": ("bone_58", "bone_59", "bone_60"),
    "ArmR2": ("bone_61", "bone_62", "bone_64"),
    "HandR": ("bone_65",),
}

MAPPED_TARGETS = tuple(dict.fromkeys(
    target for outputs in BONE_OUTPUTS.values() for target in outputs))
FINGER_BONES = tuple([f"bone_{n}" for n in range(31, 52)] +
                     [f"bone_{n}" for n in range(66, 87)])


def args_from_cli():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-rig", default="luigi.001_armature")
    parser.add_argument("--source-action", default="luigi.001_armature_walk")
    parser.add_argument("--target-rig", default="Waluigi_Arm_Test")
    parser.add_argument("--baseline-action", default="Waluigi_GalaxyWalk_Legs_Baked")
    parser.add_argument("--mesh-prefix", default="ARMTEST_")
    parser.add_argument("--start", type=float, default=0.0)
    parser.add_argument("--end", type=float, default=60.0)
    parser.add_argument("--step", type=float, default=0.25)
    parser.add_argument("--preview", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--renders", required=True)
    return parser.parse_args(argv)


def bag_for(action, slot):
    found = [bag for layer in action.layers for strip in layer.strips
             for bag in strip.channelbags if bag.slot == slot]
    if len(found) != 1:
        raise RuntimeError(f"{action.name}: expected one channelbag for {slot.identifier}, got {len(found)}")
    return found[0]


def slot_for(action, identifier=None):
    slots = [slot for slot in action.slots
             if identifier is None or slot.identifier == identifier]
    if len(slots) != 1:
        raise RuntimeError(f"{action.name}: ambiguous/missing slot {identifier}: {len(slots)}")
    return slots[0]


def curve_path_bone(path):
    prefix = 'pose.bones["'
    if prefix not in path:
        return None
    return path.split(prefix, 1)[1].split('"', 1)[0]


def evaluate_curves(rig, curves, frame):
    # Recreate a clean pose basis before directly evaluating the selected
    # original channelbag. This avoids stale action-owner binding state.
    for pb in rig.pose.bones:
        pb.location = (0.0, 0.0, 0.0)
        pb.rotation_euler = (0.0, 0.0, 0.0)
        pb.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
        pb.scale = (1.0, 1.0, 1.0)
    values = {}
    for curve in curves:
        bone_name = curve_path_bone(curve.data_path)
        if bone_name is None:
            continue
        prop = curve.data_path.split("].", 1)[1]
        if prop not in {"location", "rotation_euler", "rotation_quaternion", "scale"}:
            continue
        size = 4 if prop == "rotation_quaternion" else 3
        components = values.setdefault((bone_name, prop), [None] * size)
        components[curve.array_index] = float(curve.evaluate(frame))
    for (bone_name, prop), components in values.items():
        pb = rig.pose.bones.get(bone_name)
        if pb is None:
            raise RuntimeError(f"curve targets missing bone {bone_name}")
        current = list(getattr(pb, prop))
        for axis, value in enumerate(components):
            if value is not None:
                current[axis] = value
        setattr(pb, prop, current)
    bpy.context.view_layer.update()


def quat_rotation(matrix):
    return matrix.to_3x3().to_quaternion().normalized()


def pose_matrices_from_basis(rig):
    """Compose armature-space pose matrices without relying on stale depsgraph data."""
    result = {}
    pending = set(rig.data.bones.keys())
    while pending:
        progressed = False
        for name in tuple(pending):
            bone = rig.data.bones[name]
            if bone.parent and bone.parent.name in pending:
                continue
            rest = bone.matrix_local.copy()
            basis = rig.pose.bones[name].matrix_basis.copy()
            if bone.parent:
                rest_local = bone.parent.matrix_local.inverted_safe() @ rest
                result[name] = result[bone.parent.name] @ rest_local @ basis
            else:
                result[name] = rest @ basis
            pending.remove(name)
            progressed = True
        if not progressed:
            raise RuntimeError("armature hierarchy has a cycle or missing parent")
    return result


def set_armature_pose_matrix(rig, bone_name, desired, pose_matrices):
    bone = rig.data.bones[bone_name]
    parent = bone.parent
    if parent:
        parent_pose = pose_matrices[parent.name]
        rest_local = parent.matrix_local.inverted_safe() @ bone.matrix_local
        basis = rest_local.inverted_safe() @ parent_pose.inverted_safe() @ desired
    else:
        basis = bone.matrix_local.inverted_safe() @ desired
    rig.pose.bones[bone_name].matrix_basis = basis


def rest_frame_conversion(source, source_bone, target, target_bone):
    """Return the constant source-bone-local -> target-bone-local rotation."""
    return local_frame_conversion(source.matrix_basis,
        source.data.bones[source_bone].matrix_local, target.matrix_basis,
        target.data.bones[target_bone].matrix_local)


def bone_length_ratio(source, source_name, target, target_name,
                      source_height_ratio):
    if source_name == "JointRoot":
        return source_height_ratio
    sl = float(source.data.bones[source_name].length)
    tl = float(target.data.bones[target_name].length)
    return tl / sl if sl > 1.0e-8 else source_height_ratio


def remove_bone_curves(action, slot, targets):
    bag = bag_for(action, slot)
    paths = {f'pose.bones["{name}"]' for name in targets}
    for curve in list(bag.fcurves):
        if any(path in curve.data_path for path in paths):
            bag.fcurves.remove(curve)
    return bag


def set_key(pb, frame, group):
    pb.keyframe_insert("rotation_quaternion", frame=frame, group=group)
    pb.keyframe_insert("location", frame=frame, group=group)
    pb.keyframe_insert("scale", frame=frame, group=group)


def matrix_error(a, b):
    return max(abs(float(a[r][c]) - float(b[r][c]))
               for r in range(4) for c in range(4))


def matrix_rows(matrix):
    return [[float(matrix[r][c]) for c in range(4)] for r in range(4)]


def mesh_vertices(meshes):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    result = []
    for obj in meshes:
        evaluated = obj.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh()
        try:
            result.append([evaluated.matrix_world @ vertex.co for vertex in mesh.vertices])
        finally:
            evaluated.to_mesh_clear()
    return result


def max_vertex_error(a, b):
    if len(a) != len(b):
        return float("inf")
    worst = 0.0
    for aa, bb in zip(a, b):
        if len(aa) != len(bb):
            return float("inf")
        worst = max(worst, max(((x - y).length for x, y in zip(aa, bb)), default=0.0))
    return worst


def make_camera(scene, name, location, target, lens=52.0):
    data = bpy.data.cameras.new(name + "_Data")
    camera = bpy.data.objects.new(name, data)
    scene.collection.objects.link(camera)
    camera.location = location
    camera.rotation_euler = (Vector(target) - Vector(location)).to_track_quat("-Z", "Y").to_euler()
    camera.data.lens = lens
    return camera


def setup_render(scene, rig, meshes, render_dir, pose_samples, root_location_samples):
    os.makedirs(render_dir, exist_ok=True)
    render_scene = bpy.data.scenes.new("LiteralWalk_Render_Scene")
    render_scene.world = scene.world
    render_scene.render.engine = "BLENDER_EEVEE"
    render_scene.render.resolution_x = 960
    render_scene.render.resolution_y = 960
    render_scene.render.resolution_percentage = 100
    render_scene.render.image_settings.file_format = "PNG"
    render_scene.render.film_transparent = False
    render_rig = duplicate_rig(rig, "LiteralWalk_RenderOnly_Rig")
    for col in list(render_rig.users_collection):
        col.objects.unlink(render_rig)
    render_scene.collection.objects.link(render_rig)
    render_rig.matrix_basis = rig.matrix_basis.copy()
    render_rig.animation_data_clear()
    render_meshes = [duplicate_mesh(obj, render_rig, render_scene.collection,
                                    "LiteralWalk_RenderOnly_") for obj in meshes]
    for obj in [render_rig] + render_meshes:
        obj.hide_render = False
        obj.hide_set(False)
    render_scene.frame_start = 0
    render_scene.frame_end = 60
    render_scene.view_settings.view_transform = scene.view_settings.view_transform
    render_scene.view_settings.look = scene.view_settings.look
    render_scene.view_settings.exposure = scene.view_settings.exposure
    render_scene.view_settings.gamma = scene.view_settings.gamma
    render_scene.display.shading.light = "STUDIO"
    render_scene.display.shading.color_type = "MATERIAL"
    # Frame actual skinned world-space bounds. The authored 43.437 scale is
    # intentional and must be included in camera distance, never applied.
    points = [point for body in mesh_vertices(meshes) for point in body]
    if not points:
        raise RuntimeError("cannot frame an empty preview mesh")
    lower = Vector((min(p[i] for p in points) for i in range(3)))
    upper = Vector((max(p[i] for p in points) for i in range(3)))
    center = (lower + upper) * 0.5
    span = max((upper - lower).length, 1.0)
    distance = span * 2.2
    front = make_camera(render_scene, "LiteralWalk_Front_Camera", center + Vector((0.0, -distance, 0.0)), center)
    side = make_camera(render_scene, "LiteralWalk_Side_Camera", center + Vector((distance, 0.0, 0.0)), center)
    light_data = bpy.data.lights.new("LiteralWalk_Key_Data", type="AREA")
    light = bpy.data.objects.new("LiteralWalk_Key", light_data)
    render_scene.collection.objects.link(light)
    light.location = center + Vector((0.35 * distance, -0.5 * distance, 0.8 * distance))
    light.data.energy = 800000
    light.data.shape = "DISK"
    light.data.size = 0.65 * distance
    light.rotation_euler = (center - light.location).to_track_quat("-Z", "Y").to_euler()
    outputs = []
    failures = []
    for frame in (0, 15, 30, 45, 60):
        sample = pose_samples[frame]
        for name, matrix_rows_value in sample.items():
            render_rig.pose.bones[name].matrix_basis = Matrix(matrix_rows_value)
        if float(frame) in root_location_samples:
            render_rig.location = root_location_samples[float(frame)]
        for obj in render_meshes:
            obj.hide_render = False
            obj.hide_set(False)
        render_scene.frame_set(frame)
        for view_name, camera in (("front", front), ("side", side)):
            render_scene.camera = camera
            output_path = os.path.abspath(os.path.join(
                render_dir, f"frame_{frame:02d}_{view_name}"))
            render_scene.render.filepath = output_path
            render_status = bpy.ops.render.render(write_still=True, scene=render_scene.name)
            output_file = output_path + ".png"
            if render_status != {"FINISHED"} or not os.path.isfile(output_file):
                failures.append({"frame": frame, "view": view_name,
                                 "status": list(render_status), "output": output_file})
                continue
            outputs.append(output_file)
    temporary_objects = [front, side, light, render_rig] + render_meshes
    bpy.data.scenes.remove(render_scene)
    for obj in temporary_objects:
        datablock = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if datablock and datablock.users == 0:
            if isinstance(datablock, bpy.types.Mesh):
                bpy.data.meshes.remove(datablock)
            elif isinstance(datablock, bpy.types.Armature):
                bpy.data.armatures.remove(datablock)
            elif isinstance(datablock, bpy.types.Camera):
                bpy.data.cameras.remove(datablock)
            elif isinstance(datablock, bpy.types.Light):
                bpy.data.lights.remove(datablock)
    return outputs, failures


def main():
    args = args_from_cli()
    input_blend = bpy.data.filepath
    if bpy.data.filepath and os.path.abspath(args.preview) == os.path.abspath(bpy.data.filepath):
        raise RuntimeError("refusing to overwrite the input blend")
    scene = bpy.context.scene
    source = bpy.data.objects.get(args.source_rig)
    target_base = bpy.data.objects.get(args.target_rig)
    source_action = bpy.data.actions.get(args.source_action)
    baseline_action = bpy.data.actions.get(args.baseline_action)
    if not all((source, target_base, source_action, baseline_action)):
        raise RuntimeError("required source rig/action or Waluigi baseline is missing")
    if len(target_base.data.bones) != 92 or abs(target_base.matrix_basis.to_scale().x - 43.437) > 0.02:
        raise RuntimeError("Waluigi skeleton or intentional 43.437 scale signature mismatch")
    source_slot = slot_for(source_action,
        source.animation_data.action_slot.identifier if source.animation_data else None)
    baseline_slot = slot_for(baseline_action,
        target_base.animation_data.action_slot.identifier
        if target_base.animation_data and target_base.animation_data.action == baseline_action else None)
    source_curves = list(bag_for(source_action, source_slot).fcurves)
    baseline_curves = list(bag_for(baseline_action, baseline_slot).fcurves)
    keyed_source = {curve_path_bone(curve.data_path) for curve in source_curves
                    if curve_path_bone(curve.data_path) and len(curve.keyframe_points) > 1}
    missing = sorted(keyed_source - set(BONE_OUTPUTS))
    if missing:
        raise RuntimeError(f"animated source bones lack explicit target mapping: {missing}")
    for source_name, targets in BONE_OUTPUTS.items():
        if source_name not in source.data.bones:
            raise RuntimeError(f"mapped source bone missing: {source_name}")
        for target_name in targets:
            if target_name not in target_base.data.bones:
                raise RuntimeError(f"mapped Waluigi bone missing: {target_name}")

    source_meshes = [obj for obj in bpy.data.objects if obj.type == "MESH" and
        obj.name.startswith(args.mesh_prefix) and "ArmRetarget" not in obj.name and
        any(mod.type == "ARMATURE" and mod.object in
            (target_base, bpy.data.objects.get("Waluigi_Arm_Test")) for mod in obj.modifiers)]
    if len(source_meshes) != 2:
        raise RuntimeError(f"expected both original Waluigi preview meshes, found {len(source_meshes)}")

    source_height = (max(float(b.tail_local.z) for b in source.data.bones) -
                     min(float(b.head_local.z) for b in source.data.bones))
    target_height = (max(float(b.head_local.y) for b in target_base.data.bones) -
                     min(float(b.tail_local.y) for b in target_base.data.bones))
    root_translation_ratio = target_height / source_height
    scene.collection.children.link(bpy.data.collections.new("LiteralLuigiWalk_WaluigiPreview"))
    collection = bpy.data.collections["LiteralLuigiWalk_WaluigiPreview"]
    target = duplicate_rig(target_base, "LiteralWalk_Waluigi_Target")
    target.matrix_basis = target_base.matrix_basis.copy()
    target.data.name = target.name + "_Data"
    if target.parent is not None:
        raise RuntimeError("literal root-motion relocation requires an unparented target armature")
    target_object_location = target.location.copy()
    for col in list(target.users_collection):
        col.objects.unlink(target)
    collection.objects.link(target)
    meshes = [duplicate_mesh(obj, target, collection, "LiteralWalk_") for obj in source_meshes]
    # Remove any copied source pose constraints from the target duplicate. This
    # prototype bakes curves and deliberately has no runtime constraint layer.
    for pb in target.pose.bones:
        for constraint in list(pb.constraints):
            pb.constraints.remove(constraint)
    source_eval = duplicate_rig(source, "LiteralWalk_Luigi_SourceEvaluator")
    source_eval.matrix_basis = source.matrix_basis.copy()
    for original, clone in zip(source.pose.bones, source_eval.pose.bones):
        clone.rotation_mode = original.rotation_mode
    source_eval.animation_data_clear()

    new_action = copy_action_slot(baseline_action, baseline_slot, target,
                                  "Waluigi_LuigiWalk_LiteralRestBasis_v1")
    target_slot = target.animation_data.action_slot
    output_bag = remove_bone_curves(new_action, target_slot,
                                    set(MAPPED_TARGETS) | set(FINGER_BONES))
    fist_path = os.path.join(HERE, "profiles", "waluigi_v5_closed_fist.json")
    with open(fist_path, "r", encoding="utf-8") as stream:
        fist_reference = json.load(stream)["quaternion_wxyz_by_bone"]
    original_range = (args.start, args.end)
    if args.step <= 0 or args.end <= args.start:
        raise ValueError("invalid frame range or bake step")
    sample_count = int(round((args.end - args.start) / args.step))
    frames = [args.start + i * args.step for i in range(sample_count + 1)]
    source_bag = bag_for(source_action, source_slot)
    original_key_times = sorted({float(point.co.x) for curve in source_bag.fcurves
                                 for point in curve.keyframe_points
                                 if args.start <= point.co.x <= args.end})
    if not original_key_times:
        raise RuntimeError("source action has no keys in requested walk interval")
    for time in original_key_times:
        if min(abs(frame - time) for frame in frames) > 1.0e-5:
            frames.append(time)
    frames = sorted(set(round(frame, 6) for frame in frames))

    conversions = {}
    ratios = {}
    for source_name, targets in BONE_OUTPUTS.items():
        if source_name not in keyed_source:
            continue
        conversions[source_name] = {}
        ratios[source_name] = {}
        for target_name in targets:
            conversions[source_name][target_name] = rest_frame_conversion(
                source, source_name, target_base, target_name)
            ratios[source_name][target_name] = bone_length_ratio(
                source, source_name, target_base, target_name, root_translation_ratio)

    intended = {}
    render_pose_samples = {}
    root_location_samples = {}
    root_translation_reference = None
    root_translation_world_pose_error = 0.0
    per_bone_source_error = {target_name: 0.0 for target_name in MAPPED_TARGETS}
    finger_change = 0.0
    for frame in frames:
        scene.frame_set(math.floor(frame), subframe=frame - math.floor(frame))
        target.location = target_object_location
        bpy.context.view_layer.update()
        evaluate_curves(source_eval, source_curves, frame)
        source_pose_matrices = pose_matrices_from_basis(source_eval)
        src_basis = {name: source_eval.pose.bones[name].matrix_basis.copy()
                     for name in keyed_source}
        evaluate_curves(target, baseline_curves, frame)
        base_pose = {name: target.pose.bones[name].matrix_basis.copy()
                     for name in target.data.bones.keys()}
        target_pose_matrices = pose_matrices_from_basis(target)
        for source_name, targets in BONE_OUTPUTS.items():
            if source_name not in keyed_source:
                continue
            source_pb = source_eval.pose.bones[source_name]
            source_basis = source_pb.matrix_basis.copy()
            for target_name in targets:
                conversion = conversions[source_name][target_name]
                target_pb = target.pose.bones[target_name]
                target_pb.rotation_mode = "QUATERNION"
                desired_rotation = map_armature_pose_rotation(
                    source_pose_matrices[source_name], source.data.bones[source_name].matrix_local,
                    source.matrix_basis, target_base.data.bones[target_name].matrix_local,
                    target_base.matrix_basis)
                current = target_pose_matrices[target_name]
                desired = Matrix.LocRotScale(
                    current.translation, desired_rotation, current.to_scale())
                set_armature_pose_matrix(target, target_name, desired, target_pose_matrices)
                target_pose_matrices = pose_matrices_from_basis(target)
                basis_rotation = target.pose.bones[target_name].matrix_basis.to_quaternion()
                mapped_location = (conversion @ source_basis.translation *
                                   ratios[source_name][target_name])
                target_pb.matrix_basis = Matrix.LocRotScale(mapped_location,
                    basis_rotation, source_basis.to_scale())
                target_pose_matrices = pose_matrices_from_basis(target)
                set_key(target_pb, frame, "Literal Galaxy Walk Retarget")
                expected = target_pb.matrix_basis.copy()
                intended.setdefault(frame, {})[target_name] = expected
                per_bone_source_error[target_name] = max(
                    per_bone_source_error[target_name],
                    abs(1.0 - math.sqrt(sum(float(v) ** 2
                                             for v in target_pb.rotation_quaternion))))
        # JointRoot translation belongs to the character root, not the interior
        # pelvis connector bone_6. Keep all descendant bone world transforms
        # unchanged by moving only that translation component to the armature
        # object's location. This prevents the pelvis subtree sliding under
        # torso vertices weighted above bone_6.
        root_pb = target.pose.bones["bone_6"]
        root_local = root_pb.matrix_basis.copy()
        if root_translation_reference is None:
            root_translation_reference = root_local.translation.copy()
        old_pose_matrices = pose_matrices_from_basis(target)
        old_world_pose = {name: target.matrix_world @ matrix
                          for name, matrix in old_pose_matrices.items()}
        root_bone = target.data.bones["bone_6"]
        root_parent = root_bone.parent
        parent_pose = old_pose_matrices[root_parent.name] if root_parent else Matrix.Identity(4)
        rest_local = (root_parent.matrix_local.inverted_safe() @ root_bone.matrix_local
                      if root_parent else root_bone.matrix_local.copy())
        local_displacement = parent_pose.to_3x3() @ rest_local.to_3x3() @ (
            root_local.translation - root_translation_reference)
        world_displacement = target.matrix_world.to_3x3() @ local_displacement
        target.location = target_object_location + world_displacement
        root_local.translation = root_translation_reference
        root_pb.matrix_basis = root_local
        root_pb.keyframe_insert("location", frame=frame, group="Literal Galaxy Root Translation")
        target.keyframe_insert("location", frame=frame, group="Literal Galaxy Root Translation")
        root_location_samples[float(frame)] = [float(x) for x in target.location]
        intended.setdefault(frame, {})["bone_6"] = root_pb.matrix_basis.copy()
        target_pose_matrices = pose_matrices_from_basis(target)
        bpy.context.view_layer.update()
        for name, old_world in old_world_pose.items():
            if name == "bone_6" or name.startswith("bone_"):
                # Only validate the subtree whose local root displacement was
                # redistributed. Other root branches intentionally receive the
                # now-correct global motion.
                data_bone = target.data.bones[name]
                ancestor = data_bone.parent
                descendant_of_root = False
                while ancestor:
                    if ancestor.name == "bone_6":
                        descendant_of_root = True
                        break
                    ancestor = ancestor.parent
                if name == "bone_6" or descendant_of_root:
                    new_world = target.matrix_world @ target_pose_matrices[name]
                    root_translation_world_pose_error = max(
                        root_translation_world_pose_error, matrix_error(old_world, new_world))
        # The user-approved Waluigi fists are a fixed pose exception: Galaxy
        # has no finger articulation in this walk, and Waluigi must keep fists.
        reflection = Matrix.Diagonal(Vector((-1.0, 1.0, 1.0)))
        for left_index in range(31, 52):
            left_name, right_name = f"bone_{left_index}", f"bone_{left_index + 35}"
            left_q = Quaternion(fist_reference[left_name])
            left_bone, right_bone = target_base.data.bones[left_name], target_base.data.bones[right_name]
            left_rest_local = (left_bone.parent.matrix_local.inverted_safe() @ left_bone.matrix_local
                               if left_bone.parent else left_bone.matrix_local)
            right_rest_local = (right_bone.parent.matrix_local.inverted_safe() @ right_bone.matrix_local
                                if right_bone.parent else right_bone.matrix_local)
            left_delta = (left_rest_local.to_3x3() @ left_q.to_matrix() @
                          left_rest_local.to_3x3().inverted_safe())
            mirrored_delta = reflection @ left_delta @ reflection
            right_q = (right_rest_local.to_3x3().inverted_safe() @ mirrored_delta @
                       right_rest_local.to_3x3()).to_quaternion()
            for name, q in ((left_name, left_q), (right_name, right_q)):
                pb = target.pose.bones[name]
                before = base_pose[name]
                pb.rotation_mode = "QUATERNION"
                pb.matrix_basis = Matrix.LocRotScale(before.translation, q, before.to_scale())
                pb.keyframe_insert("rotation_quaternion", frame=frame, group="Validated Closed Fists")
                pb.keyframe_insert("location", frame=frame, group="Validated Closed Fists")
                pb.keyframe_insert("scale", frame=frame, group="Validated Closed Fists")
                intended.setdefault(frame, {})[name] = pb.matrix_basis.copy()
        for name in FINGER_BONES:
            if name in base_pose:
                finger_change = max(finger_change,
                    matrix_error(base_pose[name], target.pose.bones[name].matrix_basis))
        if abs(frame - round(frame)) < 1e-6 and int(round(frame)) in (0, 15, 30, 45, 60):
            render_pose_samples[int(round(frame))] = {
                name: matrix_rows(target.pose.bones[name].matrix_basis)
                for name in target.data.bones.keys()}
    # The mapped channels are baked at source-evaluated times. Preserve source
    # integer timing exactly and use linear interpolation between 0.25 samples.
    for curve in output_bag.fcurves:
        if any(f'pose.bones["{name}"]' in curve.data_path for name in MAPPED_TARGETS):
            for point in curve.keyframe_points:
                point.interpolation = "LINEAR"
            curve.update()
        if curve.data_path == "location":
            for point in curve.keyframe_points:
                point.interpolation = "LINEAR"
            curve.update()

    # Validate the saved action in-memory at all baked samples, mesh geometry,
    # bone scale, root object scale, and retained closed-fist basis.
    replay_error = 0.0
    for frame in frames:
        scene.frame_set(math.floor(frame), subframe=frame - math.floor(frame))
        bpy.context.view_layer.update()
        for name, wanted in intended[frame].items():
            replay_error = max(replay_error,
                matrix_error(target.pose.bones[name].matrix_basis, wanted))
    scale_error = max(abs(float(x) - float(y)) for x, y in zip(
        target.matrix_basis.to_scale(), target_base.matrix_basis.to_scale()))
    runtime_constraints = sum(len(pb.constraints) for pb in target.pose.bones)
    # Compare candidate and baseline weighted mesh coordinates at the original
    # walk sample points; this is reported, not hidden by bone endpoint metrics.
    mesh_deltas = []
    for frame in (0, 15, 30, 45, 60):
        scene.frame_set(frame)
        bpy.context.view_layer.update()
        candidate_vertices = mesh_vertices(meshes)
        evaluate_curves(target, baseline_curves, frame)
        bpy.context.view_layer.update()
        baseline_vertices = mesh_vertices(meshes)
        mesh_deltas.append({"frame": frame,
            "max_world_vertex_delta_from_baseline": max_vertex_error(candidate_vertices, baseline_vertices)})

    # Restore candidate at the opening frame and keep only its character meshes
    # visible. Source and all baseline references remain in the hidden scene.
    visible = set(meshes) | {target}
    for obj in bpy.data.objects:
        show = obj in visible
        obj.hide_render = not show
        obj.hide_viewport = not show
        obj.hide_set(not show)
    scene.frame_set(0)
    bpy.context.view_layer.update()
    source_eval_data = source_eval.data
    bpy.data.objects.remove(source_eval, do_unlink=True)
    if source_eval_data.users == 0:
        bpy.data.armatures.remove(source_eval_data)
    render_paths, render_failures = setup_render(
        scene, target, meshes, args.renders, render_pose_samples, root_location_samples)
    scene.frame_set(0)
    bpy.context.view_layer.update()
    os.makedirs(os.path.dirname(os.path.abspath(args.preview)), exist_ok=True)
    scene.render.filepath = "//"
    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(args.preview))

    report = {
        "schema": "mg1.literal-galaxy-walk-retarget.v1",
        "input_blend": input_blend,
        "source_rig": source.name,
        "source_action": source_action.name,
        "source_slot": source_slot.identifier,
        "source_keyed_bones": sorted(keyed_source),
        "mapping": {key: list(value) for key, value in BONE_OUTPUTS.items()},
        "rest_frame_conversion": {s: {t: list(q) for t, q in rows.items()}
                                   for s, rows in conversions.items()},
        "translation_scale": {s: rows for s, rows in ratios.items()},
        "source_original_key_times": original_key_times,
        "baked_samples": len(frames),
        "bake_step": args.step,
        "source_height_to_target_height_ratio": root_translation_ratio,
        "root_translation_rebased_to_object_location": True,
        "root_translation_world_pose_preservation_error": root_translation_world_pose_error,
        "expected_object_location": {str(frame): location
                                     for frame, location in root_location_samples.items()},
        "target_rig": target.name,
        "target_action": new_action.name,
        "target_meshes": [obj.name for obj in meshes],
        "intentional_target_object_scale": list(target.matrix_basis.to_scale()),
        "scale_error": scale_error,
        "closed_fist_local_basis_delta": finger_change,
        "closed_fist_reference": fist_path,
        "in_memory_bake_sample_matrix_error": replay_error,
        "max_source_rotation_normalization_error": max(per_bone_source_error.values(), default=0.0),
        "runtime_constraint_count": runtime_constraints,
        "baseline_mesh_comparison": mesh_deltas,
        "render_files": render_paths,
        "render_failures": render_failures,
        "expected_pose_basis": {str(frame): {
            name: matrix_rows(matrix) for name, matrix in poses.items()}
            for frame, poses in intended.items()},
        "front_side_review_required": True,
        "human_visual_approval": False,
        "limitations": [
            "Quaternion rest-basis conversion is nonlinear, so target curves are baked from the original source curve evaluation at quarter-frame samples; source original key times are all retained.",
            "Unmapped Waluigi helper bones retain the validated baseline; finger bones use the validated closed-fist profile.",
            "Automated front/side renders are evidence for review and do not constitute human visual approval."
        ]
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.report)), exist_ok=True)
    with open(args.report, "w", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
    # Remove source evaluator after rendering; it is never visible or saved in
    # the preview because it was excluded above.
    print("LITERAL_WALK_REPORT", json.dumps({k: report[k] for k in (
        "source_keyed_bones", "baked_samples", "scale_error",
        "closed_fist_local_basis_delta", "in_memory_bake_sample_matrix_error",
        "runtime_constraint_count", "target_action", "render_files")}, sort_keys=True))


if __name__ == "__main__":
    main()
